import json

from anthropic import Anthropic

from ..config import settings


class OrchestratorAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-sonnet-4-6"

    def decide_agent(self, user_message: str, memory_context: str = "") -> dict:
        """
        Claude Sonnet decide cuál sub-agente activar
        Retorna: {agent_type, reasoning, params}
        """
        system_prompt = """Eres un orquestador de un chatbot de ventas para franquiciados. Clasifica el mensaje:

1. "comparative" — consultas que comparan DOS períodos o dimensiones: "esta semana vs la semana pasada", "enero vs febrero", "compará hoy con ayer", "diferencia entre", "cómo fue X comparado con Y".
2. "data" — consultas de ventas de UN solo período: productos, artículos, precios, turnos, POS, reportes, métricas del negocio.
3. "interaction" — saludos, preguntas sobre cómo usar el chatbot, conversación mínima relacionada con el negocio.
4. "off_topic" — SOLO cuando el mensaje NO contiene NINGUNA parte relacionada con ventas o el negocio.

REGLA DE PRIORIDAD: Si el mensaje mezcla contenido de negocio con contenido off-topic, clasificar siempre por la parte de negocio e ignorar el resto. "off_topic" es el último recurso.
Si hay duda entre "comparative" y "data", usar "comparative".
Si hay duda entre "data" e "interaction", usar "data".

Responde SOLO con JSON: {"agent_type": "", "reasoning": "", "should_use_memory": bool}"""

        context = f"Contexto de memoria: {memory_context}" if memory_context else ""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": f"{context}\nMensaje del usuario: {user_message}\n\nResponde SOLO con el JSON, sin texto adicional."}],
        )

        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}

        try:
            text = response.content[0].text.strip()
            # Extraer JSON aunque venga con texto extra
            start = text.find("{")
            end = text.rfind("}") + 1
            result = json.loads(text[start:end])
            result.update(usage)
            return result
        except:
            pass

        # Fallback por palabras clave si el LLM no retorna JSON válido
        keywords_comparative = ["vs", "versus", "comparar", "compará", "comparación", "diferencia entre", "contra"]
        keywords_data = ["venta", "ventas", "producto", "artículo", "reporte", "turno",
                         "pos", "cantidad", "precio", "franquicia", "ingreso", "ticket"]
        keywords_interaction = ["hola", "gracias", "ayuda", "cómo funciona", "que puedes hacer"]
        msg_lower = user_message.lower()
        if any(k in msg_lower for k in keywords_comparative):
            return {"agent_type": "comparative", "reasoning": "keyword fallback", "should_use_memory": False, **usage}
        if any(k in msg_lower for k in keywords_data):
            return {"agent_type": "data", "reasoning": "keyword fallback", "should_use_memory": False, **usage}
        if any(k in msg_lower for k in keywords_interaction):
            return {"agent_type": "interaction", "reasoning": "keyword fallback", "should_use_memory": False, **usage}

        return {"agent_type": "off_topic", "reasoning": "Default fallback", "should_use_memory": False, **usage}


orchestrator = OrchestratorAgent()
