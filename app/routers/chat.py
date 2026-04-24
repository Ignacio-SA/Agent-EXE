import traceback
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..agents.comparative_agent import comparative_agent
from ..agents.data_agent import data_agent
from ..agents.interaction import interaction_agent
from ..agents.memory_agent import memory_agent
from ..agents.orchestrator import orchestrator
from ..agents.training_agent import training_agent
from ..config import settings
from ..db.training_repo import training_memory
from ..models.schemas import ChatRequest, ChatResponse, FeedbackRequest, FeedbackResponse, HistoryEntry

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        # Obtener memoria previa
        memory = memory_agent.retrieve_memory(request.session_id)
        memory_context = memory.get("summary", "") if memory else ""

        # Inyectar contexto de entrenamiento si training_mode está activo
        if request.training_mode:
            training_ctx = training_memory.get_context()
            if training_ctx:
                memory_context = f"{training_ctx}\n\n{memory_context}" if memory_context else training_ctx

        # Orquestador decide qué agente
        decision = orchestrator.decide_agent(request.message, memory_context)
        agent_type = decision.get("agent_type", "interaction")

        orch_in = decision.get("input_tokens", 0)
        orch_out = decision.get("output_tokens", 0)

        # Invocar agente correspondiente
        agent_in = agent_out = 0
        if agent_type == "comparative":
            response_text, agent_in, agent_out = comparative_agent.process_comparative_request(
                request.message, settings.franchise_code, memory_context, request.session_id
            )
        elif agent_type == "data":
            response_text, agent_in, agent_out = data_agent.process_data_request(
                request.message, settings.franchise_code, memory_context, request.session_id
            )
        elif agent_type == "feedback":
            # Feedback textual: recuperar último intercambio desde memoria y analizar
            from ..db.memory_repo import memory_repo as repo
            msgs = repo.get_messages(request.session_id)
            last_user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
            last_bot = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")
            feedback_type = "positivo" if any(w in request.message.lower() for w in ["bien", "perfecto", "correcto", "excelente", "gracias"]) else "negativo"
            training_agent.analyze_feedback(
                request.session_id, last_user, last_bot, request.message, feedback_type
            )
            response_text = "Gracias por tu feedback. Lo voy a tener en cuenta para mejorar."
        elif agent_type == "off_topic":
            response_text = "Solo puedo ayudarte con consultas de ventas o datos del negocio."
        elif agent_type == "memory":
            response_text = f"Recordando: {memory_context}"
        else:  # interaction
            response_text, agent_in, agent_out = interaction_agent.respond(
                request.message, memory_context
            )

        # Guardar mensajes y log de tokens
        from ..db.memory_repo import memory_repo as repo
        repo.save_message(request.session_id, "user", request.message)
        repo.save_message(request.session_id, "assistant", response_text, agent_type)
        repo.save_query_log(
            request.session_id, request.message, agent_type,
            orch_in + agent_in, orch_out + agent_out,
        )

        # Guardar memoria/resumen
        conversation = [
            {"role": "user", "content": request.message},
            {"role": "assistant", "content": response_text},
        ]
        user_id = request.user_id or settings.franchise_code
        memory_agent.save_memory(request.session_id, user_id, conversation, previous_summary=memory_context)

        return ChatResponse(
            session_id=request.session_id,
            response=response_text,
            agent_type=agent_type,
            timestamp=datetime.now(),
        )

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/feedback/", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """POST /chat/feedback/ - Envía feedback explícito sobre una respuesta del bot"""
    try:
        entry, _, _ = training_agent.analyze_feedback(
            request.session_id,
            request.user_message,
            request.bot_response,
            request.feedback,
            request.feedback_type,
        )
        # Extraer component y priority del entry para devolver al frontend
        import re
        component = re.search(r"\*\*Componente afectado:\*\* (.+)", entry)
        priority = re.search(r"\*\*Prioridad:\*\* (alta|media|baja)", entry)
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
