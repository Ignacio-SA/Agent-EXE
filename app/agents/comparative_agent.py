import json
import logging
from datetime import datetime

from anthropic import Anthropic

from ..config import settings
from ..db.sales_repo import sales_repo
from ..logger import get_session_logger
from .data_agent import data_agent


class ComparativeAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def _extract_two_periods(
        self, user_message: str, context: str = ""
    ) -> tuple[dict, dict, int, int]:
        """
        Extrae dos períodos de la consulta comparativa.
        Retorna (period_a, period_b, input_tokens, output_tokens)
        donde cada período es {"label": str, "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD", "date_filter": str}
        """
        today = datetime.now().date()
        context_hint = f"\nContexto previo:\n{context}\n" if context else ""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=150,
            temperature=0,
            system=f"""Hoy es {today}. Extrae los DOS períodos que el usuario quiere comparar.
Responde SOLO con JSON:
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

            return build_period(data["period_a"]), build_period(data["period_b"]), tok_in, tok_out

        except Exception:
            fallback = {
                "label": "período",
                "date_from": datetime.now().replace(hour=0, minute=0, second=0),
                "date_to": datetime.now().replace(hour=23, minute=59, second=59),
                "date_filter": f"DATE(SaleDateTimeUtc) = '{today}'",
            }
            return fallback, fallback, tok_in, tok_out

    def _format_comparative_response(
        self,
        user_message: str,
        summary_a: str,
        summary_b: str,
        label_a: str,
        label_b: str,
    ) -> tuple[str, int, int]:
        """LLM formatea la comparativa con deltas entre los dos períodos."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0,
            system=f"""Eres un asistente de ventas. Presentá una comparación clara entre dos períodos en español, usando markdown con tablas.

INSTRUCCIÓN CRÍTICA: Usá EXACTAMENTE los números de los bloques de datos pre-calculados. NO recalcules ni modifiques ningún número. Calculá deltas y variaciones porcentuales solo a partir de esos números.

Si el mensaje del usuario incluye preguntas no relacionadas con ventas o el negocio, ignóralas por completo. No las menciones ni las respondas.

Período A — {label_a}:
{summary_a}

Período B — {label_b}:
{summary_b}

Formato sugerido:
- Tabla resumen con ambos períodos y variación %
- Desglose por vendedor comparativo
- Top productos comparativo
- Conclusión en 1-2 líneas

Nunca mostres nombres técnicos de columnas ni códigos internos.""",
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens

    def process_comparative_request(
        self,
        user_message: str,
        franchise_code: str,
        context: str = "",
        session_id: str = "",
    ) -> tuple[str, int, int]:
        log = get_session_logger(session_id) if session_id else logging.getLogger(__name__)

        log.info("━" * 60)
        log.info(f"COMPARATIVA: {user_message!r}")
        log.info(f"FRANCHISE  : {franchise_code}")

        total_input = total_output = 0

        # 1. Extraer los dos períodos
        period_a, period_b, tok_in, tok_out = self._extract_two_periods(user_message, context)
        total_input += tok_in
        total_output += tok_out
        log.info(f"PERÍODO A  : {period_a['label']} ({period_a['date_from'].date()} → {period_a['date_to'].date()})")
        log.info(f"PERÍODO B  : {period_b['label']} ({period_b['date_from'].date()} → {period_b['date_to'].date()})")

        # 2. Un solo llamado al SP con el rango completo
        global_from = min(period_a["date_from"], period_b["date_from"])
        global_to = max(period_a["date_to"], period_b["date_to"])
        sales = sales_repo.get_sales(franchise_code, date_from=global_from, date_to=global_to)
        log.info(f"SP ROWS    : {len(sales)} filas para el rango completo")

        # 3. Cargar en SQLite (reutiliza DataAgent)
        mem_conn = data_agent._load_into_memory(sales)

        # 4. Métricas para cada período (reutiliza DataAgent)
        summary_a = data_agent._compute_summary(mem_conn, period_a["date_filter"], period_a["label"])
        summary_b = data_agent._compute_summary(mem_conn, period_b["date_filter"], period_b["label"])
        mem_conn.close()

        log.info(f"SUMMARY A  : {len(summary_a)} chars")
        log.info(f"SUMMARY B  : {len(summary_b)} chars")

        # 5. Formatear respuesta comparativa
        response_text, tok_in, tok_out = self._format_comparative_response(
            user_message, summary_a, summary_b, period_a["label"], period_b["label"]
        )
        total_input += tok_in
        total_output += tok_out

        log.info(f"TOKENS     : input={total_input}  output={total_output}  total={total_input + total_output}")
        log.info("━" * 60)

        return response_text, total_input, total_output


comparative_agent = ComparativeAgent()
