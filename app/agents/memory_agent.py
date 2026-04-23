from anthropic import Anthropic

from ..config import settings
from ..db.memory_repo import memory_repo
from ..models.memory import MemoryEntry


class MemoryAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def save_memory(self, session_id: str, user_id: str, conversation: list[dict]) -> str:
        """
        Genera un resumen de la conversación y lo guarda
        """
        conversation_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation])

        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system="Resume brevemente la conversación en 2-3 puntos clave",
            messages=[{"role": "user", "content": conversation_text}],
        )

        summary = response.content[0].text

        # Guardar en BD
        entry = MemoryEntry(
            session_id=session_id, user_id=user_id, context=conversation_text, summary=summary
        )
        memory_repo.create(entry)

        return summary

    def retrieve_memory(self, session_id: str) -> dict:
        """
        Obtiene la memoria de sesión anterior
        """
        entry = memory_repo.read(session_id)
        if entry:
            return {
                "summary": entry.summary,
                "context": entry.context,
                "updated_at": entry.updated_at,
            }
        return {}


memory_agent = MemoryAgent()
