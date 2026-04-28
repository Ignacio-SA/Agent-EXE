import logging
import traceback
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..agents.comparative_agent import comparative_agent
from ..agents.data_agent import data_agent
from ..agents.franchise_resolver import franchise_resolver
from ..agents.interaction import interaction_agent
from ..agents.memory_agent import memory_agent
from ..agents.orchestrator import orchestrator
from ..agents.session_context import session_context
from ..agents.training_agent import training_agent
from ..config import settings
from ..db import data_source as _data_source
from ..db.training_repo import training_memory
from ..logger import get_session_logger
from ..models.schemas import ChatRequest, ChatResponse, FeedbackRequest, FeedbackResponse, HistoryEntry

router = APIRouter(prefix="/chat", tags=["chat"])

_log = logging.getLogger(__name__)


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    log = get_session_logger(request.session_id)

    try:
        # ──────────────────────────────────────────────────────────────
        # [Router Global] Nueva petición
        # ──────────────────────────────────────────────────────────────
        log.info("=" * 65)
        log.info(f"[Router Global] ▶ Nueva petición — sesión: {request.session_id}")
        log.info(f"[Router Global] Mensaje del usuario: {request.message!r}")
        if request.training_mode:
            log.info("[Router Global] Modo entrenamiento: ACTIVO")

        # Obtener memoria previa
        memory = memory_agent.retrieve_memory(request.session_id)
        memory_context = memory.get("summary", "") if memory else ""

        if memory_context:
            log.info(f"[MemoryAgent]  Contexto previo recuperado ({len(memory_context)} chars):")
            log.info(f"[MemoryAgent]  └─ {memory_context[:300]}{'…' if len(memory_context) > 300 else ''}")
        else:
            log.info("[MemoryAgent]  Sin historial previo para esta sesión.")

        # Inyectar contexto de entrenamiento si training_mode está activo
        if request.training_mode:
            training_ctx = training_memory.get_context()
            if training_ctx:
                memory_context = f"{training_ctx}\n\n{memory_context}" if memory_context else training_ctx
                log.info(f"[Router Global] Contexto de entrenamiento inyectado ({len(training_ctx)} chars)")

        # ──────────────────────────────────────────────────────────────
        # [Orchestrator] Clasificación del mensaje
        # ──────────────────────────────────────────────────────────────
        log.info("─" * 65)
        log.info("[Orchestrator] Clasificando mensaje…")
        decision = orchestrator.decide_agent(request.message, memory_context)
        agent_type = decision.get("agent_type", "interaction")
        reasoning  = decision.get("reasoning", "—")
        is_fallback = "fallback" in reasoning.lower()

        log.info(f"[Orchestrator] ▶ Agente seleccionado : {agent_type.upper()}")
        log.info(f"[Orchestrator] ▶ Razonamiento del modelo: {reasoning}")
        if is_fallback:
            log.warning("[Orchestrator] ⚠ Clasificación por FALLBACK (LLM no devolvió JSON válido)")
        log.info(f"[Orchestrator] Tokens orquestador — input: {decision.get('input_tokens', 0)}  output: {decision.get('output_tokens', 0)}")

        orch_in  = decision.get("input_tokens", 0)
        orch_out = decision.get("output_tokens", 0)

        # ──────────────────────────────────────────────────────────────
        # [FranchiseResolver] Resolución de franquicia(s)
        # ──────────────────────────────────────────────────────────────
        log.info("─" * 65)
        franchise_map = _data_source.get_available_franchises()
        resolved_codes, fr_clarification, is_franchise_compare = franchise_resolver.resolve(
            request.message, memory_context, franchise_map, agent_type,
            session_franchise=session_context.get_franchise(request.session_id),
        )
        log.info(
            f"[FranchiseResolver] codes={resolved_codes}  clarification={bool(fr_clarification)}  "
            f"franchise_compare={is_franchise_compare}"
        )

        # ──────────────────────────────────────────────────────────────
        # Invocar agente correspondiente
        # ──────────────────────────────────────────────────────────────
        log.info("─" * 65)
        agent_in = agent_out = 0

        # Guardar franquicia resuelta para follow-ups de esta sesión
        if resolved_codes and not fr_clarification:
            session_context.set_franchise(request.session_id, resolved_codes)

        if fr_clarification:
            # Franquicia ambigua — devolver la pregunta de aclaración
            log.info("[FranchiseResolver] Devolviendo aclaración de franquicia al usuario.")
            response_text = fr_clarification

        elif is_franchise_compare:
            # Comparación directa entre franquicias
            log.info("[ComparativeAgent] ▶ Iniciando comparación entre franquicias…")
            response_text, agent_in, agent_out = comparative_agent.process_franchise_comparison(
                request.message, franchise_map, memory_context, request.session_id
            )
            agent_type = "franchise_compare"

        elif agent_type == "comparative":
            franchise_codes = resolved_codes or list(franchise_map.keys())
            log.info(f"[ComparativeAgent] ▶ Iniciando agente comparativo — franchises: {franchise_codes}")
            response_text, agent_in, agent_out = comparative_agent.process_comparative_request(
                request.message, franchise_codes, memory_context, request.session_id
            )

        elif agent_type == "data":
            franchise_codes = resolved_codes or list(franchise_map.keys())
            log.info(f"[DataAgent] ▶ Iniciando agente de datos (Text-to-SQL) — franchises: {franchise_codes}")
            response_text, agent_in, agent_out = data_agent.process_data_request(
                request.message, franchise_codes, memory_context, request.session_id
            )

        elif agent_type == "feedback":
            log.info("[Feedback Global] ▶ Interceptando feedback textual del usuario…")
            from ..db.memory_repo import memory_repo as repo
            msgs = repo.get_messages(request.session_id)
            last_user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
            last_bot  = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")
            feedback_type = "positivo" if any(
                w in request.message.lower() for w in ["bien", "perfecto", "correcto", "excelente", "gracias"]
            ) else "negativo"
            log.info(f"[Feedback Global] Tipo detectado: {feedback_type.upper()}")
            log.info(f"[Feedback Global] Último mensaje usuario: {last_user[:120]!r}")
            log.info(f"[Feedback Global] Última respuesta bot : {last_bot[:120]!r}")
            training_agent.analyze_feedback(
                request.session_id, last_user, last_bot, request.message, feedback_type
            )
            response_text = "Gracias por tu feedback. Lo voy a tener en cuenta para mejorar."

        elif agent_type == "off_topic":
            log.info("[Router Global] Mensaje off-topic — respuesta fija, sin LLM.")
            response_text = "Solo puedo ayudarte con consultas de ventas o datos del negocio."

        elif agent_type == "memory":
            log.info("[MemoryAgent] Devolviendo resumen de memoria como respuesta.")
            response_text = f"Recordando: {memory_context}"

        else:  # interaction
            log.info("[InteractionAgent] ▶ Iniciando agente de interacción…")
            response_text, agent_in, agent_out = interaction_agent.respond(
                request.message, memory_context
            )

        # ──────────────────────────────────────────────────────────────
        # Guardar mensajes y log de tokens
        # ──────────────────────────────────────────────────────────────
        from ..db.memory_repo import memory_repo as repo
        repo.save_message(request.session_id, "user", request.message)
        repo.save_message(request.session_id, "assistant", response_text, agent_type)
        repo.save_query_log(
            request.session_id, request.message, agent_type,
            orch_in + agent_in, orch_out + agent_out,
        )

        # Guardar memoria/resumen
        conversation = [
            {"role": "user",      "content": request.message},
            {"role": "assistant", "content": response_text},
        ]
        user_id = request.user_id or settings.franchise_code
        memory_agent.save_memory(request.session_id, user_id, conversation, previous_summary=memory_context)

        total_tokens = orch_in + agent_in + orch_out + agent_out
        log.info("─" * 65)
        log.info(f"[Router Global] ✔ Respuesta lista — agente: {agent_type}  tokens totales: {total_tokens}")
        log.info("=" * 65)

        return ChatResponse(
            session_id=request.session_id,
            response=response_text,
            agent_type=agent_type,
            timestamp=datetime.now(),
        )

    except Exception as e:
        log.error(f"[Router Global] ✖ ERROR en request: {e}", exc_info=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/feedback/", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """POST /chat/feedback/ - Envía feedback explícito sobre una respuesta del bot"""
    try:
        log = get_session_logger(request.session_id)
        log.info("─" * 65)
        log.info(f"[Feedback Global] ▶ Feedback explícito recibido — tipo: {request.feedback_type.upper()}")
        log.info(f"[Feedback Global] Texto del feedback: {request.feedback!r}")

        entry, _, _ = training_agent.analyze_feedback(
            request.session_id,
            request.user_message,
            request.bot_response,
            request.feedback,
            request.feedback_type,
        )
        import re
        component = re.search(r"\*\*Componente afectado:\*\* (.+)", entry)
        priority  = re.search(r"\*\*Prioridad:\*\* (alta|media|baja)", entry)
        return FeedbackResponse(
            ok=True,
            component=component.group(1).strip() if component else "",
            priority=priority.group(1) if priority else "",
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/")
async def list_sessions():
    try:
        from ..db.memory_repo import memory_repo as repo
        return repo.list_all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    try:
        from ..db.memory_repo import memory_repo as repo
        return repo.get_messages(session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        from ..db.memory_repo import memory_repo as repo
        deleted = repo.delete(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{session_id}", response_model=list[HistoryEntry])
async def get_history(session_id: str):
    try:
        memory = memory_agent.retrieve_memory(session_id)
        if not memory:
            return []
        return [
            HistoryEntry(
                session_id=session_id,
                user_message="[Previous conversation]",
                bot_response=memory.get("summary", ""),
                agent_type="memory",
                timestamp=memory.get("updated_at", datetime.now()),
            )
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
