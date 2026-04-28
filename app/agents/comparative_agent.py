import json
import logging
from datetime import datetime

from anthropic import Anthropic

from ..config import settings
from ..db import data_source
from ..db.sales_analytics import compute_summary, load_into_memory
from ..logger import get_session_logger
from .date_resolver import date_resolver
from .session_context import session_context


class ComparativeAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def _extract_two_periods(
        self, user_message: str, context: str = ""
    ) -> tuple[dict, dict, int, int, str]:
        """
        Extrae dos períodos de la consulta comparativa.
        Retorna (period_a, period_b, input_tokens, output_tokens, clarification)
        donde cada período es {"label": str, "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD", "date_filter": str}
        """
        today = datetime.now().date()
        context_hint = f"\nContexto previo:\n{context}\n" if context else ""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=160,
            temperature=0,
            system=f"""Hoy es {today}. Extrae los DOS períodos que el usuario quiere comparar.
Si el mensaje es corto o incompleto, usa el contexto previo para inferir los períodos faltantes.

REGLA DE AMBIGÜEDAD: Si el año de algún período es ambiguo (se menciona un mes sin año y no es claro si es {today.year} o {today.year - 1}), devuelve:
{{"clarification": "¿A qué año te referís para [período], {today.year - 1} o {today.year}?"}}

En cualquier otro caso devuelve SOLO este JSON:
{{
  "period_a": {{"label": "nombre legible", "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}},
  "period_b": {{"label": "nombre legible", "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}}
}}{context_hint}""",
            messages=[{"role": "user", "content": user_message}],
        )

        tok_in = response.usage.input_tokens
        tok_out = response.usage.output_tokens

        try:
            text = response.content[0].text.strip()
            data = json.loads(text[text.find("{") : text.rfind("}") + 1])

            if data.get("clarification"):
                fallback = {
                    "label": "período",
                    "date_from": datetime.now().replace(hour=0, minute=0, second=0),
                    "date_to": datetime.now().replace(hour=23, minute=59, second=59),
                    "date_filter": f"DATE(SaleDateTimeUtc) = '{today}'",
                }
                return fallback, fallback, tok_in, tok_out, data["clarification"]

            def build_period(p: dict) -> dict:
                df = p["date_from"]
                dt = p["date_to"]
                date_filter = (
                    f"DATE(SaleDateTimeUtc) = '{df}'"
                    if df == dt
                    else f"DATE(SaleDateTimeUtc) BETWEEN '{df}' AND '{dt}'"
                )
                return {
                    "label": p.get("label", f"{df} al {dt}"),
                    "date_from": datetime.strptime(df, "%Y-%m-%d"),
                    "date_to": datetime.strptime(dt + " 23:59:59", "%Y-%m-%d %H:%M:%S"),
                    "date_filter": date_filter,
                }

            return build_period(data["period_a"]), build_period(data["period_b"]), tok_in, tok_out, ""

        except Exception:
            fallback = {
                "label": "período",
                "date_from": datetime.now().replace(hour=0, minute=0, second=0),
                "date_to": datetime.now().replace(hour=23, minute=59, second=59),
                "date_filter": f"DATE(SaleDateTimeUtc) = '{today}'",
            }
            return fallback, fallback, tok_in, tok_out, ""

    def _format_comparative_response(
        self,
        user_message: str,
        summary_a: str,
        summary_b: str,
        label_a: str,
        label_b: str,
    ) -> tuple[str, int, int]:
        """LLM formatea la comparativa con deltas entre los dos bloques."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0,
            system=f"""Eres un asistente de ventas. Presentá una comparación clara entre dos elementos en español, usando markdown con tablas.

INSTRUCCIÓN CRÍTICA: Usá EXACTAMENTE los números de los bloques de datos pre-calculados. NO recalcules ni modifiques ningún número. Calculá deltas y variaciones porcentuales solo a partir de esos números.

INSTRUCCIÓN DE ETIQUETAS: Los bloques indican exactamente qué se está comparando (períodos o franquicias). Úsalos siempre. NUNCA uses términos vagos.

Si el mensaje del usuario incluye preguntas no relacionadas con ventas o el negocio, ignóralas por completo. No las menciones ni las respondas.

Elemento A — {label_a}:
{summary_a}

Elemento B — {label_b}:
{summary_b}

Formato sugerido:
- Tabla resumen con ambos elementos y variación %
- Desglose comparativo relevante
- Conclusión en 1-2 líneas

Nunca mostres nombres técnicos de columnas ni códigos internos.""",
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens

    def process_comparative_request(
        self,
        user_message: str,
        franchise_codes: list[str],
        context: str = "",
        session_id: str = "",
    ) -> tuple[str, int, int]:
        """Comparación entre dos períodos de tiempo para las franquicias dadas."""
        log = get_session_logger(session_id) if session_id else logging.getLogger(__name__)

        log.info("━" * 60)
        log.info(f"COMPARATIVA  : {user_message!r}")
        log.info(f"FRANCHISES   : {franchise_codes}")

        total_input = total_output = 0

        # 1. Extraer los dos períodos
        period_a, period_b, tok_in, tok_out, clarification = self._extract_two_periods(user_message, context)
        total_input += tok_in
        total_output += tok_out

        if clarification:
            log.info(f"CLARIF       : {clarification!r}")
            log.info("━" * 60)
            return clarification, total_input, total_output

        log.info(f"PERÍODO A    : {period_a['label']} ({period_a['date_from'].date()} → {period_a['date_to'].date()})")
        log.info(f"PERÍODO B    : {period_b['label']} ({period_b['date_from'].date()} → {period_b['date_to'].date()})")

        # 2. Un solo llamado a la fuente activa con el rango completo
        global_from = min(period_a["date_from"], period_b["date_from"])
        global_to   = max(period_a["date_to"],   period_b["date_to"])

        # Persistir rango global en SessionContext para follow-ups posteriores
        if session_id:
            global_filter = (
                f"DATE(SaleDateTimeUtc) BETWEEN '{global_from.date().isoformat()}' AND '{global_to.date().isoformat()}'"
            )
            session_context.set_date(session_id, global_from, global_to, global_filter)
            log.info(f"[ComparativeAgent] Rango global guardado en sesión: {global_from.date()} → {global_to.date()}")
        sales = data_source.get_sales(franchise_codes, date_from=global_from, date_to=global_to)
        src = "LOCAL db_ventas.db" if data_source.is_local_mode() else "SP sp_GetSalesForChatbot"
        log.info(f"DATA SRC     : {src} → {len(sales)} filas para el rango completo")

        # Mapa franquicia→label (para desglose en summary si hay varias)
        franchise_map = (
            {code: settings.franchise_map.get(code, code[:12] + "...") for code in franchise_codes}
            if len(franchise_codes) > 1 else None
        )

        # 3. Cargar en SQLite en RAM
        mem_conn = load_into_memory(sales)

        # 4. Métricas para cada período
        summary_a = compute_summary(mem_conn, period_a["date_filter"], period_a["label"], franchise_map)
        summary_b = compute_summary(mem_conn, period_b["date_filter"], period_b["label"], franchise_map)
        mem_conn.close()

        log.info(f"SUMMARY A    : {len(summary_a)} chars")
        log.info(f"SUMMARY B    : {len(summary_b)} chars")

        # 5. Formatear respuesta comparativa
        response_text, tok_in, tok_out = self._format_comparative_response(
            user_message, summary_a, summary_b, period_a["label"], period_b["label"]
        )
        total_input += tok_in
        total_output += tok_out

        log.info(f"TOKENS       : input={total_input}  output={total_output}  total={total_input + total_output}")
        log.info("━" * 60)
        return response_text, total_input, total_output

    def process_franchise_comparison(
        self,
        user_message: str,
        franchise_map: dict[str, str],
        context: str = "",
        session_id: str = "",
    ) -> tuple[str, int, int]:
        """
        Compara dos franquicias en el mismo período.
        franchise_map: {code: label} con exactamente dos entradas.
        """
        log = get_session_logger(session_id) if session_id else logging.getLogger(__name__)

        codes  = list(franchise_map.keys())
        labels = list(franchise_map.values())

        log.info("━" * 60)
        log.info(f"COMP FRANQUICIAS: {user_message!r}")
        log.info(f"FRANQUICIA A    : {labels[0]}")
        log.info(f"FRANQUICIA B    : {labels[1]}")

        total_input = total_output = 0

        # 1. Extraer el período único
        date_from, date_to, date_filter, tok_in, tok_out, clarification = date_resolver.resolve(
            user_message, context, session_id
        )
        total_input += tok_in
        total_output += tok_out

        if clarification:
            log.info(f"CLARIF          : {clarification!r}")
            log.info("━" * 60)
            return clarification, total_input, total_output

        if date_from and date_to and date_from.date() == date_to.date():
            period_label = date_from.strftime("%d/%m/%Y")
        elif date_from and date_to:
            period_label = f"{date_from.strftime('%d/%m/%Y')} al {date_to.strftime('%d/%m/%Y')}"
        elif date_from:
            period_label = f"desde {date_from.strftime('%d/%m/%Y')}"
        else:
            period_label = "período completo"

        log.info(f"PERÍODO         : {period_label}")

        # Persistir período en SessionContext para follow-ups posteriores
        if session_id and date_from and date_to:
            session_context.set_date(session_id, date_from, date_to, date_filter or "")
            log.info(f"[ComparativeAgent] Período guardado en sesión: {date_from.date()} → {date_to.date()}")

        # 2. Obtener datos de cada franquicia por separado
        sales_a = data_source.get_sales([codes[0]], date_from=date_from, date_to=date_to)
        sales_b = data_source.get_sales([codes[1]], date_from=date_from, date_to=date_to)

        src = "LOCAL db_ventas.db" if data_source.is_local_mode() else "SP sp_GetSalesForChatbot"
        log.info(f"DATA SRC        : {src}")
        log.info(f"  {labels[0]}: {len(sales_a)} filas")
        log.info(f"  {labels[1]}: {len(sales_b)} filas")

        # 3. Cargar en SQLite separadas y calcular métricas
        mem_a = load_into_memory(sales_a)
        summary_a = compute_summary(mem_a, date_filter, period_label)
        mem_a.close()

        mem_b = load_into_memory(sales_b)
        summary_b = compute_summary(mem_b, date_filter, period_label)
        mem_b.close()

        log.info(f"SUMMARY A       : {len(summary_a)} chars")
        log.info(f"SUMMARY B       : {len(summary_b)} chars")

        # 4. Formatear respuesta comparativa con labels de franquicia
        response_text, tok_in, tok_out = self._format_comparative_response(
            user_message, summary_a, summary_b, labels[0], labels[1]
        )
        total_input += tok_in
        total_output += tok_out

        log.info(f"TOKENS          : input={total_input}  output={total_output}  total={total_input + total_output}")
        log.info("━" * 60)
        return response_text, total_input, total_output


comparative_agent = ComparativeAgent()
