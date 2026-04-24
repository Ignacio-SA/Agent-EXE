from anthropic import Anthropic

from ..config import settings
from ..db.memory_repo import memory_repo
from ..models.memory import MemoryEntry


class MemoryAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def save_memory(self, session_id: str, user_id: str, conversation: list[dict], previous_summary: str = "") -> str:
        """
        Genera un resumen acumulativo de la conversación y lo guarda.
        Incorpora el resumen anterior para no perder contexto (fechas, métricas, períodos).
        """
        new_exchange = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation])

        if previous_summary:
            content = f"Resumen acumulado hasta ahora:\n{previous_summary}\n\nNuevo intercambio:\n{new_exchange}"
            system = (
                "Actualiza el resumen de sesión incorporando el nuevo intercambio al resumen previo. "
                "Conserva SIEMPRE: rangos de fechas o períodos consultados, totales de ventas, vendedores mencionados y métricas clave. "
                "Responde con un resumen de 3-5 puntos en español."
            )
        else:
            content = new_exchange
            system = (
                "Resume brevemente la conversación en 2-3 puntos clave. "
                "Conserva explícitamente: rangos de fechas o períodos consultados, totales de ventas y métricas clave."
            )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": content}],
        )

        summary = response.content[0].text

        # Guardar en BD
        entry = MemoryEntry(
            session_id=session_id, user_id=user_id, context=new_exchange, summary=summary
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
