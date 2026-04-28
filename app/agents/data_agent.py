import logging
import os
import re
import sqlite3
from datetime import datetime

from anthropic import Anthropic

from ..config import settings
from ..db import data_source
from ..db.sales_analytics import compute_summary, load_into_memory
from ..logger import get_session_logger
from .date_resolver import date_resolver
from .session_context import session_context

_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "context", "business_rules.md")


def _load_business_rules() -> str:
    try:
        with open(_RULES_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


_BUSINESS_RULES = _load_business_rules()


class DataAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def _generate_sql(self, user_message: str, total_rows: int, today: str, context: str = "") -> tuple[str, int, int]:
        """LLM genera el SQL apropiado para la pregunta del usuario."""
        business_rules = _BUSINESS_RULES
        context_block = f"\nCONTEXTO DE CONVERSACIÓN PREVIA:\n{context}\n" if context else ""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=600,
            temperature=0,
            system=f"""Eres un experto en SQL. Genera UNA sola consulta SQL para responder la pregunta del usuario.

Total de registros en la tabla: {total_rows}
Fecha de hoy: {today}
{context_block}
Reglas IMPORTANTES de SQL (SQLite):
- Responde SOLO con la consulta SQL, sin explicaciones ni markdown ni bloques de código
- NUNCA uses UNION ALL. El sistema ya calcula automáticamente todos los desgloses (vendedores, productos, canales, medios de pago, días). Generá SIEMPRE una consulta simple y directa.
- Para informes completos o detallados, generá una consulta de resumen general simple (COUNT DISTINCT id, SUM ventas, etc.) — el desglose completo ya está pre-calculado.
- Si el mensaje del usuario es muy corto o es una continuación ("en facturación", "por monto", "y en $", "también", etc.), usa el CONTEXTO DE CONVERSACIÓN PREVIA para reconstruir la pregunta completa (misma agrupación, misma fecha, pero con la métrica/dimensión que indica el mensaje).
- Si el mensaje del usuario es una respuesta a una pregunta de aclaración ("ambas", "las dos", "quiero ambas", "la 1", "la primera", "para las dos", etc.), buscá en el CONTEXTO la consulta original del usuario y generá el SQL para responderla. NUNCA devuelvas texto conversacional en lugar de SQL.
- La base de datos es SQLite — usa SOLO funciones SQLite:
  * Para año: strftime('%Y', SaleDateTimeUtc)
  * Para mes: strftime('%m', SaleDateTimeUtc)
  * Para año-mes: strftime('%Y-%m', SaleDateTimeUtc)
  * Para fecha: DATE(SaleDateTimeUtc)
  * NUNCA uses YEAR(), MONTH(), DATEPART() — no existen en SQLite
- Para "hoy" usa: DATE(SaleDateTimeUtc) = '{datetime.now().strftime("%Y-%m-%d")}'
- Para "ayer" usa: DATE(SaleDateTimeUtc) = date('{datetime.now().strftime("%Y-%m-%d")}', '-1 day')
- Para totales usa COUNT o SUM según corresponda
- NO uses LIMIT salvo que el usuario pida explícitamente un "top N" o "los N más..."
- Si la pregunta menciona kilos, peso o distribución por peso/kilos, incluí OBLIGATORIAMENTE SUM(CAST(Quantity AS REAL) * CAST(WeightKilos AS REAL)) AS kilos_total en el SELECT, y filtrá WHERE WeightKilos IS NOT NULL AND WeightKilos != ''. Si usás GROUP BY, kilos_total debe estar en el SELECT.

---
{business_rules}
""",
            messages=[{"role": "user", "content": user_message}],
        )
        sql = response.content[0].text.strip().strip("```sql").strip("```").strip()
        if not sql.upper().startswith("SELECT"):
            sql = "SELECT UserName, COUNT(DISTINCT id) AS transacciones, ROUND(SUM(CAST(Quantity AS REAL)*CAST(UnitPriceFix AS REAL)),2) AS total FROM ventas WHERE \"Type\" != '2' GROUP BY UserName ORDER BY total DESC"
        return sql, response.usage.input_tokens, response.usage.output_tokens

    def _execute_sql(self, mem_conn: sqlite3.Connection, sql: str) -> tuple[list, list]:
        """Ejecuta el SQL generado y retorna columnas + filas."""
        try:
            cursor = mem_conn.execute(sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return columns, rows
        except Exception as e:
            return [], [("Error en SQL", str(e))]

    def _format_response(self, user_message: str, sql: str, columns: list, rows: list, summary: str) -> tuple[str, int, int]:
        """LLM formatea los resultados en lenguaje natural."""
        business_rules = _BUSINESS_RULES

        if len(rows) > 20:
            data_content = "(Datos detallados omitidos — usar solo los datos pre-calculados del sistema)"
        else:
            data_content = f"Columnas: {columns}\nResultados: {rows}"

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0,
            system=f"""Eres un asistente de ventas. Presenta los resultados de forma clara y estructurada en español. Usa formato markdown con tablas o listas cuando sea útil.

INSTRUCCIÓN CRÍTICA: Los siguientes datos fueron calculados con precisión en Python. Úsalos EXACTAMENTE como aparecen. NO recalcules ni modifiques ningún número.

INSTRUCCIÓN DE PERÍODO: Los datos pre-calculados indican el período exacto analizado. Úsalo siempre al describir los resultados. NUNCA uses "período completo", "datos generales" ni términos vagos si el período está definido.

INSTRUCCIÓN DE KILOS/PESO: Si el resultado SQL contiene columnas de kilos o peso (WeightKilos, kilos, peso), esos valores son la fuente autoritativa. Úsalos directamente SIN estimar ni agregar asteriscos. NUNCA digas que "no tenés el peso exacto" si el resultado SQL ya lo incluye.

Si el mensaje del usuario incluye preguntas no relacionadas con ventas o el negocio, ignóralas por completo. No las menciones ni las respondas.

{summary}

Reglas de presentación:
{business_rules}""",
            messages=[{
                "role": "user",
                "content": f"Pregunta: {user_message}\n{data_content}"
            }],
        )
        return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens

    def process_data_request(
        self,
        user_message: str,
        franchise_codes: list[str],
        context: str = "",
        session_id: str = "",
    ) -> tuple[str, int, int]:
        log = get_session_logger(session_id) if session_id else logging.getLogger(__name__)
        today = datetime.now().strftime("%Y-%m-%d")

        log.info("━" * 65)
        log.info(f"[DataAgent] CONSULTA   : {user_message!r}")
        log.info(f"[DataAgent] FRANCHISES : {franchise_codes}")
        if context:
            log.info(f"[DataAgent] CONTEXTO   : {context[:200]}{'…' if len(context) > 200 else ''}")

        total_input = total_output = 0

        franchise_map = (
            {code: settings.franchise_map.get(code, code[:12] + "...") for code in franchise_codes}
            if len(franchise_codes) > 1 else None
        )

        # ── Paso 1: Extraer rango de fechas ───────────────────────────
        log.info("[DataAgent] (Paso 1) Extrayendo rango de fechas del mensaje…")
        date_from, date_to, date_filter, tok_in, tok_out, clarification = date_resolver.resolve(
            user_message, context, session_id
        )
        total_input += tok_in; total_output += tok_out

        if clarification:
            log.info(f"[DataAgent] (Paso 1) Solicitud de aclaración al usuario: {clarification!r}")
            log.info("━" * 65)
            return clarification, total_input, total_output

        log.info(f"[DataAgent] (Paso 1) date_from={date_from}  date_to={date_to}")
        log.info(f"[DataAgent] (Paso 1) Filtro SQL: {date_filter or '(sin filtro — año completo)'}")

        # ── Paso 2: Obtener datos (local o remoto) ────────────────────
        src_label = "LOCAL db_ventas.db" if data_source.is_local_mode() else "SP sp_GetSalesForChatbot @ Azure/Fabric"
        log.info(f"[DataAgent] (Paso 2) Consultando fuente: {src_label}")
        sales = data_source.get_sales(franchise_codes, date_from=date_from, date_to=date_to)
        log.info(f"[DataAgent] (Paso 2) Filas recibidas: {len(sales)}")

        # ── Paso 3: Cargar en SQLite en RAM ───────────────────────────
        log.info("[DataAgent] (Paso 3) Volcando datos en SQLite RAM…")
        mem_conn = load_into_memory(sales)

        if sales:
            cols_preview = list(sales[0].keys())
            log.info(f"[DataAgent] (Paso 3) Columnas: {cols_preview}")
            log.info("[DataAgent] (Paso 3) Preview — primeras 5 filas:")
            for i, row in enumerate(sales[:5], 1):
                preview_fields = {
                    k: str(v)[:40] for k, v in row.items()
                    if k in ("id", "FranchiseeCode", "UserName", "SaleDateTimeUtc", "ArticleDescription", "Quantity", "UnitPriceFix")
                }
                log.info(f"[DataAgent]   fila {i}: {preview_fields}")
        else:
            log.info("[DataAgent] (Paso 3) Sin datos para el período — tabla RAM vacía.")

        # ── Paso 4: LLM genera SQL ────────────────────────────────────
        # Enriquecer contexto con último artículo consultado en la sesión
        last_product = session_context.get_product(session_id) if session_id else None
        enriched_context = (
            f"ÚLTIMO ARTÍCULO/PRODUCTO CONSULTADO: {last_product}\n{context}"
            if last_product else context
        )

        log.info(f"[DataAgent] (Paso 4) Generando SQL con LLM (total_rows={len(sales)})…")
        if last_product:
            log.info(f"[DataAgent] (Paso 4) Último artículo en sesión: {last_product!r}")
        sql, tok_in, tok_out = self._generate_sql(user_message, len(sales), today, enriched_context)
        total_input += tok_in; total_output += tok_out
        log.info(f"[DataAgent] (Paso 4) SQL generado:\n{sql}")

        # Guardar artículo consultado para follow-ups de la sesión
        if session_id:
            m = re.search(
                r'ArticleDescription\s+(?:LIKE\s+["\']%?([^%"\']+)%?["\']|=\s+["\']([^"\']+)["\'])',
                sql, re.IGNORECASE,
            )
            if m:
                session_context.set_product(session_id, (m.group(1) or m.group(2)).strip())

        # ── Paso 5: Ejecutar SQL en RAM ───────────────────────────────
        log.info("[DataAgent] (Paso 5) Ejecutando SQL en SQLite RAM…")
        columns, rows = self._execute_sql(mem_conn, sql)
        if rows and rows[0] and rows[0][0] and str(rows[0][0]).startswith("Error"):
            log.warning(f"[DataAgent] (Paso 5) ✖ SQL ERROR: {rows[0]}")
        else:
            log.info(f"[DataAgent] (Paso 5) Resultado: {len(rows)} filas — columnas: {columns}")
            if rows:
                log.info(f"[DataAgent] (Paso 5) Primeras 3 filas del resultado: {rows[:3]}")

        # ── Cálculo de métricas Python ────────────────────────────────
        if date_from and date_to and date_from.date() == date_to.date():
            period_label = date_from.strftime("%d/%m/%Y")
        elif date_from and date_to:
            period_label = f"{date_from.strftime('%d/%m/%Y')} al {date_to.strftime('%d/%m/%Y')}"
        elif date_from:
            period_label = f"desde {date_from.strftime('%d/%m/%Y')}"
        else:
            period_label = "año completo"

        summary = compute_summary(mem_conn, date_filter, period_label, franchise_map)
        mem_conn.close()
        log.info(f"[DataAgent] Período analizado: {period_label}")

        # ── Paso 6: LLM formatea la respuesta ─────────────────────────
        log.info("[DataAgent] (Paso 6) Formateando respuesta final con LLM…")
        response_text, tok_in, tok_out = self._format_response(user_message, sql, columns, rows, summary)
        total_input += tok_in; total_output += tok_out
        log.info(f"[DataAgent] TOKENS     : input={total_input}  output={total_output}  total={total_input + total_output}")
        log.info("━" * 65)
        return response_text, total_input, total_output


data_agent = DataAgent()
