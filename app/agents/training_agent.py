import json
import logging
import re
from datetime import datetime

from anthropic import Anthropic

from ..config import settings
from ..db.training_repo import training_memory
from ..logger import get_session_logger


class TrainingAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def analyze_feedback(
        self,
        session_id: str,
        user_message: str,
        bot_response: str,
        feedback: str,
        feedback_type: str,
    ) -> tuple[str, int, int]:
        log = get_session_logger(session_id) if session_id else logging.getLogger(__name__)

        log.info("─" * 65)
        log.info(f"[TrainingAgent] (Paso 1) Analizando feedback — tipo: {feedback_type.upper()}")
        log.info(f"[TrainingAgent] (Paso 1) Feedback recibido: {feedback!r}")
        log.info(f"[TrainingAgent] (Paso 1) Mensaje original del usuario: {user_message[:150]!r}")
        log.info(f"[TrainingAgent] (Paso 1) Respuesta del bot que se evalúa: {bot_response[:150]!r}…")

        response = self.client.messages.create(
            model=self.model,
            max_tokens=600,
            temperature=0,
            system="""Analizás ciclos de feedback de un chatbot de ventas para franquiciados.
Tu tarea es identificar la causa raíz y generar una sugerencia concreta de mejora.

Respondé SOLO con JSON:
{
  "component": "data_agent | orchestrator | interaction | business_rules",
  "root_cause": "descripción concisa de la causa raíz (1 oración)",
  "suggestion": "sugerencia concreta de mejora (1-2 oraciones)",
  "priority": "alta | media | baja"
}

Criterios de prioridad:
- alta: datos incorrectos, queries fallidas, respuestas completamente erróneas
- media: imprecisiones, formato inadecuado, contexto incompleto
- baja: estilo, redacción, preferencias menores""",
            messages=[{
                "role": "user",
                "content": (
                    f'Usuario preguntó: "{user_message}"\n\n'
                    f'El bot respondió: "{bot_response[:1000]}"\n\n'
                    f'Feedback del usuario ({feedback_type}): "{feedback}"'
                ),
            }],
        )

        tok_in  = response.usage.input_tokens
        tok_out = response.usage.output_tokens

        try:
            text = response.content[0].text.strip()
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])

            # Paso 2: disección del feedback
            log.info(f"[TrainingAgent] (Paso 2) Clasificación LLM:")
            log.info(f"[TrainingAgent] (Paso 2) Tipo     : {feedback_type.upper()}")
            log.info(f"[TrainingAgent] (Paso 2) Componente afectado: {data.get('component', 'unknown')}")

            # Paso 3: causa raíz
            log.info(f"[TrainingAgent] (Paso 3) Causa raíz identificada: {data.get('root_cause', '—')}")

            # Paso 4: sugerencia
            log.info(f"[TrainingAgent] (Paso 4) Sugerencia de mejora: {data.get('suggestion', '—')}")
            log.info(f"[TrainingAgent] (Paso 4) Prioridad: {data.get('priority', 'media').upper()}")

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry_text = (
                f"## [{now}] Sesión: {session_id} | Tipo: {feedback_type}\n\n"
                f"**Chat analizado:**\n"
                f'- Usuario preguntó: "{user_message}"\n'
                f'- Agente respondió: "{bot_response[:300]}..."\n'
                f'- Feedback recibido: "{feedback}"\n\n'
                f"**Componente afectado:** {data.get('component', 'unknown')}\n\n"
                f"**Causa raíz identificada:**\n{data.get('root_cause', '')}\n\n"
                f"**Sugerencia de cambio:**\n{data.get('suggestion', '')}\n\n"
                f"**Prioridad:** {data.get('priority', 'media')}\n"
                f"---"
            )

            training_memory.add_suggestion(entry_text, {
                "type":       feedback_type,
                "component":  data.get("component", "unknown"),
                "suggestion": data.get("suggestion", ""),
                "priority":   data.get("priority", "media"),
            })

            # Paso 5: confirmación
            log.info(
                f"[TrainingAgent] (Paso 5) ✔ Sugerencia almacenada en RAM y disco — "
                f"componente: {data.get('component')} | prioridad: {data.get('priority')}"
            )
            log.info("─" * 65)
            return entry_text, tok_in, tok_out

        except Exception as e:
            log.warning(f"[TrainingAgent] ✖ Error al parsear respuesta LLM: {e}")
            return "", tok_in, tok_out


training_agent = TrainingAgent()
