import os
import re

_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "context", "training_log.md")
_MAX_ENTRIES = 20
_MAX_CONTEXT_CHARS = 2000


class TrainingMemory:
    def __init__(self):
        self._suggestions: list[dict] = []
        self._load_from_disk()

    def _parse_entry(self, text: str) -> dict | None:
        try:
            tipo = re.search(r"Tipo: (positivo|negativo)", text)
            component = re.search(r"\*\*Componente afectado:\*\* (.+)", text)
            suggestion = re.search(
                r"\*\*Sugerencia de cambio:\*\*\s*\n(.+?)(?:\n\n|\*\*Prioridad)", text, re.DOTALL
            )
            priority = re.search(r"\*\*Prioridad:\*\* (alta|media|baja)", text)
            return {
                "type": tipo.group(1) if tipo else "negativo",
                "component": component.group(1).strip() if component else "unknown",
                "suggestion": suggestion.group(1).strip() if suggestion else text[:200],
                "priority": priority.group(1) if priority else "media",
            }
        except Exception:
            return None

    def _load_from_disk(self):
        try:
            if not os.path.exists(_LOG_PATH):
                return
            with open(_LOG_PATH, encoding="utf-8") as f:
                content = f.read()
            raw_entries = content.split("## [")
            entries = []
            for raw in raw_entries[1:]:
                parsed = self._parse_entry("## [" + raw)
                if parsed:
                    entries.append(parsed)
            self._suggestions = entries[-_MAX_ENTRIES:]
        except Exception:
            self._suggestions = []

    def add_suggestion(self, entry_text: str, parsed: dict):
        self._suggestions.append(parsed)
        if len(self._suggestions) > _MAX_ENTRIES:
            self._suggestions = self._suggestions[-_MAX_ENTRIES:]
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("\n" + entry_text + "\n")
        except Exception:
            pass

    def get_context(self) -> str:
        if not self._suggestions:
            return ""
        lines = [
            "=== CONTEXTO DE ENTRENAMIENTO ACTIVO ===",
            "Sugerencias de mejora basadas en feedback previo de usuarios:",
        ]
        total = sum(len(l) for l in lines)
        sorted_entries = sorted(
            self._suggestions,
            key=lambda e: {"alta": 0, "media": 1, "baja": 2}.get(e["priority"], 1),
        )
        for e in sorted_entries:
            if e["priority"] == "baja":
                continue
            icon = "⚠️ CORRECCIÓN" if e["type"] == "negativo" else "✅ PATRÓN EXITOSO"
            line = f"{icon} ({e['component']}): {e['suggestion']}"
            if total + len(line) > _MAX_CONTEXT_CHARS:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines) if len(lines) > 2 else ""


training_memory = TrainingMemory()
