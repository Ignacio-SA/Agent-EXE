from anthropic import Anthropic

from ..config import settings


class InteractionAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def respond(self, user_message: str, memory_context: str = "") -> tuple[str, int, int]:
        """
        Claude Haiku responde conversacionalmente
        """
        system_prompt = """Eres un asistente de ventas para franquiciados. Responde preguntas sobre:
- Saludos y conversación básica relacionada con el negocio
- Dudas sobre cómo usar este asistente

Si el mensaje mezcla contenido del negocio con preguntas off-topic, responde SOLO la parte del negocio e ignora el resto sin mencionarlo.
No traduzcas ni resuelvas tareas externas."""

        if memory_context:
            system_prompt += f"\n\nContexto: {memory_context}"

        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens


interaction_agent = InteractionAgent()
