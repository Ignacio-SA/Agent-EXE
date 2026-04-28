"""
date_resolver.py
----------------
Extrae el rango de fechas de un mensaje del usuario.
Persiste el rango resuelto en SessionContext para que los follow-ups
hereden el período sin buscar fechas en texto del historial.

resolve() retorna:
  (date_from, date_to, date_filter, tok_in, tok_out, clarification)
"""

import json
import re
from datetime import datetime, timedelta

from anthropic import Anthropic

from ..config import settings
from .session_context import session_context

_FOLLOWUP_STARTS = (
    "y ", "y como", "y cuál", "y cuanto", "y qué", "y que",
    "también", "ademas", "además", "ahora", "en ese", "del mismo",
    "de ese", "ese día", "esa fecha", "en facturación", "por monto",
    "desglosá", "desglose", "mostrame", "muéstrame", "detallame",
    "y el", "y la", "y los", "y las",
)


class DateResolver:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def resolve(
        self,
        user_message: str,
        context: str = "",
        session_id: str = "",
    ) -> tuple[datetime | None, datetime | None, str, int, int, str]:
        """
        Extrae el rango de fechas del mensaje.
        Si hay una fecha guardada en la sesión y el mensaje es un follow-up, la reutiliza.
        Guarda el rango resuelto en session_context para el próximo turno.
        """
        now = datetime.now()
        today = now.date()
        msg = user_message.lower()

        def day_range(d):
            return (
                datetime.combine(d, datetime.min.time()),
                datetime.combine(d, datetime.max.time().replace(microsecond=0)),
                f"DATE(SaleDateTimeUtc) = '{d.isoformat()}'",
            )

        # ── Detección directa en Python (0 tokens) ───────────────────────
        if "hoy" in msg:
            return self._save(*day_range(today), 0, 0, "", session_id)
        if "ayer" in msg:
            return self._save(*day_range(today - timedelta(days=1)), 0, 0, "", session_id)
        if "esta semana" in msg:
            start = today - timedelta(days=today.weekday())
            return self._save(
                datetime.combine(start, datetime.min.time()),
                datetime.combine(today, datetime.max.time().replace(microsecond=0)),
                f"DATE(SaleDateTimeUtc) >= '{start.isoformat()}'",
                0, 0, "", session_id,
            )
        if "semana pasada" in msg:
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
            return self._save(
                datetime.combine(start, datetime.min.time()),
                datetime.combine(end, datetime.max.time().replace(microsecond=0)),
                f"DATE(SaleDateTimeUtc) BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'",
                0, 0, "", session_id,
            )
        if "este mes" in msg:
            start = today.replace(day=1)
            return self._save(
                datetime.combine(start, datetime.min.time()),
                datetime.combine(today, datetime.max.time().replace(microsecond=0)),
                f"strftime('%Y-%m', SaleDateTimeUtc) = '{today.strftime('%Y-%m')}'",
                0, 0, "", session_id,
            )

        # "2026 hasta ahora/hoy" → desde el 1° de enero del año hasta hoy
        _year_now = re.search(r'\b(20\d{2})\b', msg)
        if _year_now and any(k in msg for k in ("hasta ahora", "hasta hoy", "al día de hoy", "al dia de hoy")):
            yr = int(_year_now.group(1))
            start = datetime(yr, 1, 1)
            return self._save(
                start,
                datetime.combine(today, datetime.max.time().replace(microsecond=0)),
                f"DATE(SaleDateTimeUtc) BETWEEN '{yr}-01-01' AND '{today.isoformat()}'",
                0, 0, "", session_id,
            )

        # ── Follow-up: usar fecha de sesión guardada ──────────────────────
        # Detectar patrón de fecha real (dd/mm, dd-mm, yyyy-mm-dd)
        # \d{2}[/-]\d{2} requiere 2 dígitos → no dispara con fracciones como "1/4"
        _has_date_pattern = bool(re.search(
            r'\b\d{2}[/\-]\d{2}(?:[/\-]\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b', msg
        ))
        _no_date_keywords = not (
            any(k in msg for k in [
                "hoy", "ayer", "semana", "mes", "año", "enero", "febrero",
                "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre",
                "al ", "del ", "desde", "hasta",
            ])
            or _has_date_pattern
        )
        _is_followup = _no_date_keywords and (
            len(user_message.strip()) < 70
            or any(msg.strip().startswith(s) for s in _FOLLOWUP_STARTS)
        )

        # Si el contexto muestra que el bot acaba de pedir aclaración de franquicia,
        # el usuario está respondiendo a eso — la fecha debe venir del contexto (LLM),
        # no del período de sesión que puede ser de una consulta anterior.
        # Usamos el texto exacto del mensaje de aclaración (no frases genéricas)
        # para evitar falsos positivos con resúmenes de memoria que mencionen "franquicia".
        _context_has_franchise_clarification = context and any(
            m in context.lower() for m in [
                "¿para cuál franquicia necesitás",
                "para cuál franquicia necesitás",
                "para cual franquicia necesitas",
                "necesitás los datos? (",
                "necesitas los datos? (",
            ]
        )

        if _is_followup and session_id and not _context_has_franchise_clarification:
            saved = session_context.get_date(session_id)
            if saved:
                return saved[0], saved[1], saved[2], 0, 0, ""

        # ── LLM fallback para fechas específicas ─────────────────────────
        context_hint = (
            f"\n\nCONTEXTO DE CONVERSACIÓN PREVIA (leer para inferir período):\n{context}\n"
            if context else ""
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=80,
            temperature=0,
            system=f"""Hoy es {today}. Extrae el rango de fechas para responder la consulta del usuario.

REGLA OBLIGATORIA N°1: Si el mensaje actual NO menciona ninguna fecha ni período (es decir, es una continuación o follow-up), DEBES extraer la fecha del CONTEXTO DE CONVERSACIÓN PREVIA. Jamás retornes null cuando el contexto contiene fechas.

REGLA OBLIGATORIA N°2: Si el mensaje menciona un mes sin año (ej: "en marzo") y no es claro si es {today.year} o {today.year - 1}, devuelve:
{{"date_from": null, "date_to": null, "clarification": "¿A qué año te referís, {today.year - 1} o {today.year}?"}}

REGLA N°3: Si el mensaje dice "YYYY hasta ahora", "YYYY hasta hoy", "YYYY al día de hoy" o similar, interpreta como date_from=YYYY-01-01 y date_to={today}.
REGLA N°4: Si el mensaje menciona solo un año (ej: "en 2025", "el 2026"), interpreta como el año completo: date_from=YYYY-01-01 y date_to=YYYY-12-31.

Solo retorna null sin clarification si NO hay absolutamente ninguna fecha ni en el mensaje ni en el contexto.
En cualquier otro caso devuelve SIEMPRE: {{"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}}{context_hint}""",
            messages=[{"role": "user", "content": user_message}],
        )
        llm_in, llm_out = response.usage.input_tokens, response.usage.output_tokens

        try:
            text = response.content[0].text.strip()
            start = text.find("{")
            data = json.loads(text[start:text.rfind("}") + 1])
            if data.get("clarification"):
                return None, None, "", llm_in, llm_out, data["clarification"]
            if data.get("date_from"):
                df = datetime.strptime(data["date_from"], "%Y-%m-%d")
                dt = (
                    datetime.strptime(data["date_to"] + " 23:59:59", "%Y-%m-%d %H:%M:%S")
                    if data.get("date_to")
                    else df.replace(hour=23, minute=59, second=59)
                )
                date_filter = (
                    f"DATE(SaleDateTimeUtc) = '{data['date_from']}'"
                    if data["date_from"] == data.get("date_to")
                    else f"DATE(SaleDateTimeUtc) BETWEEN '{data['date_from']}' AND '{data.get('date_to', today.isoformat())}'"
                )
                return self._save(df, dt, date_filter, llm_in, llm_out, "", session_id)
        except Exception:
            pass

        return None, None, "", llm_in, llm_out, ""

    def _save(
        self,
        date_from: datetime | None,
        date_to: datetime | None,
        date_filter: str,
        tok_in: int,
        tok_out: int,
        clarification: str,
        session_id: str,
    ) -> tuple:
        if date_from and date_to and session_id:
            session_context.set_date(session_id, date_from, date_to, date_filter)
        return date_from, date_to, date_filter, tok_in, tok_out, clarification


date_resolver = DateResolver()
