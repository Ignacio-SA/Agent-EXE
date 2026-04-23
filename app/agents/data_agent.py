import logging
import os
import re
import sqlite3
import struct
from datetime import datetime, timedelta, timezone

from anthropic import Anthropic

from ..config import settings
from ..db.sales_repo import sales_repo
from ..logger import get_session_logger

_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "context", "business_rules.md")


def _load_business_rules() -> str:
    try:
        with open(_RULES_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


class DataAgent:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
        self.model = "claude-haiku-4-5-20251001"

    def _load_into_memory(self, sales: list[dict]) -> sqlite3.Connection:
        """Carga los datos del SP en una tabla SQLite en memoria."""
        conn = sqlite3.connect(":memory:")
        if not sales:
            conn.execute("""
                CREATE TABLE ventas (
                    id TEXT, FranchiseeCode TEXT, ShiftCode TEXT, PosCode TEXT,
                    UserName TEXT, SaleDateTimeUtc TEXT, Quantity REAL,
                    ArticleId TEXT, ArticleDescription TEXT, TypeDetail TEXT, UnitPriceFix REAL
                )
            """)
            return conn

        columns = list(sales[0].keys())
        cols_def = ", ".join([f'"{c}" TEXT' for c in columns])
        conn.execute(f"CREATE TABLE ventas ({cols_def})")

        def fmt(v):
            if v is None:
                return None
            # DATETIMEOFFSET llega como bytes raw del ODBC driver (20 bytes)
            # Formato: year(h) month(H) day(H) hour(H) minute(H) second(H) fraction_ns(I) tz_h(h) tz_m(h)
            if isinstance(v, (bytes, bytearray)) and len(v) == 20:
                year, month, day, hour, minute, second, fraction, tz_h, tz_m = struct.unpack('<hHHHHHIhh', v)
                microsecond = fraction // 1000
                tz = timezone(timedelta(hours=tz_h, minutes=tz_m))
                dt = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tz)
                # Guardar en hora local (ya viene en -03:00 gracias a SWITCHOFFSET)
                return dt.strftime("%Y-%m-%d %H:%M:%S.%f")
            if hasattr(v, "strftime"):
                return v.strftime("%Y-%m-%d %H:%M:%S.%f")
            # Limpiar timezone offset de strings
            return re.sub(r'\s*[+-]\d{2}:\d{2}$', '', str(v))

        placeholders = ", ".join(["?" for _ in columns])
        for row in sales:
            conn.execute(
                f"INSERT INTO ventas VALUES ({placeholders})",
                [fmt(v) for v in row.values()],
            )
        conn.commit()
        return conn

    def _generate_sql(self, user_message: str, total_rows: int, today: str, context: str = "") -> tuple[str, int, int]:
        """LLM genera el SQL apropiado para la pregunta del usuario."""
        business_rules = _load_business_rules()
        context_block = f"\nCONTEXTO DE CONVERSACIÓN PREVIA:\n{context}\n" if context else ""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            temperature=0,
            system=f"""Eres un experto en SQL. Genera UNA sola consulta SQL para responder la pregunta del usuario.

Total de registros en la tabla: {total_rows}
Fecha de hoy: {today}
{context_block}
Reglas IMPORTANTES de SQL (SQLite):
- Responde SOLO con la consulta SQL, sin explicaciones ni markdown ni bloques de código
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

---
{business_rules}
""",
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip().strip("```sql").strip("```").strip(), response.usage.input_tokens, response.usage.output_tokens

    def _execute_sql(self, mem_conn: sqlite3.Connection, sql: str) -> tuple[list, list]:
        """Ejecuta el SQL generado y retorna columnas + filas."""
        try:
            cursor = mem_conn.execute(sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return columns, rows
        except Exception as e:
            return [], [("Error en SQL", str(e))]

    def _compute_summary(self, mem_conn: sqlite3.Connection, date_filter: str = "", period_label: str = "") -> str:
        """Calcula todas las métricas en Python (sin LLM) para evitar inconsistencias."""
        try:
            base = f"\"Type\" != '2'{' AND ' + date_filter if date_filter else ''}"

            totals = mem_conn.execute(f"""
                SELECT COUNT(DISTINCT id),
                       ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2),
                       COUNT(DISTINCT UserName)
                FROM ventas WHERE {base}
            """).fetchone()

            if not totals[0]:
                return "Sin datos para el período consultado."

            by_vendor = mem_conn.execute(f"""
                SELECT UserName,
                       COUNT(DISTINCT id),
                       ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2)
                FROM ventas WHERE {base}
                GROUP BY UserName ORDER BY 3 DESC
            """).fetchall()

            top_products = mem_conn.execute(f"""
                SELECT ArticleDescription,
                       SUM(CAST(Quantity AS REAL)),
                       ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2)
                FROM ventas WHERE {base}
                GROUP BY ArticleDescription ORDER BY 2 DESC LIMIT 10
            """).fetchall()

            hourly = mem_conn.execute(f"""
                SELECT strftime('%H', SaleDateTimeUtc),
                       COUNT(DISTINCT id)
                FROM ventas WHERE {base}
                GROUP BY 1 ORDER BY 2 DESC LIMIT 5
            """).fetchall()

            def fmt(n):
                return f"${n:,.0f}".replace(",", ".")

            period_str = f" — PERÍODO: {period_label}" if period_label else ""
            lines = [
                f"=== DATOS PRE-CALCULADOS{period_str} (usar exactamente estos números) ===",
                "",
                f"RESUMEN GENERAL:",
                f"- Transacciones: {totals[0]}",
                f"- Total ventas: {fmt(totals[1])}",
                f"- Vendedores activos: {totals[2]}",
                "",
                "POR VENDEDOR:",
            ]
            for v in by_vendor:
                lines.append(f"  • {v[0]}: {v[1]} transacciones | {fmt(v[2])}")

            lines += ["", "TOP PRODUCTOS (por unidades):"]
            for p in top_products:
                lines.append(f"  • {p[0]}: {p[1]:.0f} unidades | {fmt(p[2])}")

            lines += ["", "HORAS MÁS ACTIVAS (transacciones únicas):"]
            for h in hourly:
                lines.append(f"  • {h[0]}:00 hs — {h[1]} transacciones")

            return "\n".join(lines)
        except Exception:
            return ""

    def _format_response(self, user_message: str, sql: str, columns: list, rows: list, summary: str) -> tuple[str, int, int]:
        """LLM formatea los resultados en lenguaje natural."""
        business_rules = _load_business_rules()

        # Para respuestas con muchas filas, usar solo los datos pre-calculados
        if len(rows) > 20:
            data_content = f"(Datos detallados omitidos — usar solo los datos pre-calculados del sistema)"
        else:
            data_content = f"Columnas: {columns}\nResultados: {rows}"

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0,
            system=f"""Eres un asistente de ventas. Presenta los resultados de forma clara y estructurada en español. Usa formato markdown con tablas o listas cuando sea útil.

INSTRUCCIÓN CRÍTICA: Los siguientes datos fueron calculados con precisión en Python. Úsalos EXACTAMENTE como aparecen. NO recalcules ni modifiques ningún número.

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

    def _extract_date_range(self, user_message: str, context: str = "") -> tuple[datetime | None, datetime | None, str, int, int]:
        """Extrae el rango de fechas del mensaje. Retorna (date_from, date_to, date_filter_sql)."""
        import json
        now = datetime.now()
        today = now.date()
        msg = user_message.lower()

        def day_range(d):
            """Devuelve (inicio_dia, fin_dia, filtro_sql) para una fecha dada."""
            dt_from = datetime.combine(d, datetime.min.time())
            dt_to = datetime.combine(d, datetime.max.time().replace(microsecond=0))
            sql = f"DATE(SaleDateTimeUtc) = '{d.isoformat()}'"
            return dt_from, dt_to, sql

        # Detección directa en Python — sin LLM, sin fallos (0 tokens)
        if "hoy" in msg:
            return (*day_range(today), 0, 0)
        if "ayer" in msg:
            return (*day_range(today - timedelta(days=1)), 0, 0)
        if "esta semana" in msg:
            start = today - timedelta(days=today.weekday())
            return (
                datetime.combine(start, datetime.min.time()),
                datetime.combine(today, datetime.max.time().replace(microsecond=0)),
                f"DATE(SaleDateTimeUtc) >= '{start.isoformat()}'",
                0, 0,
            )
        if "semana pasada" in msg:
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
            return (
                datetime.combine(start, datetime.min.time()),
                datetime.combine(end, datetime.max.time().replace(microsecond=0)),
                f"DATE(SaleDateTimeUtc) BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'",
                0, 0,
            )
        if "este mes" in msg:
            start = today.replace(day=1)
            return (
                datetime.combine(start, datetime.min.time()),
                datetime.combine(today, datetime.max.time().replace(microsecond=0)),
                f"strftime('%Y-%m', SaleDateTimeUtc) = '{today.strftime('%Y-%m')}'",
                0, 0,
            )

        # LLM como fallback para fechas específicas ("25 de marzo", "del 1 al 15", etc.)
        context_hint = f"\nContexto de conversación previa (puede contener la fecha relevante):\n{context}\n" if context else ""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=60,
            temperature=0,
            system=f"""Hoy es {today}. Extrae el rango de fechas del mensaje o del contexto previo si el mensaje no tiene fecha explícita.
Responde SOLO con JSON: {{"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}}
Si no hay fecha en el mensaje ni en el contexto responde: {{"date_from": null, "date_to": null}}{context_hint}""",
            messages=[{"role": "user", "content": user_message}],
        )
        llm_in, llm_out = response.usage.input_tokens, response.usage.output_tokens

        try:
            text = response.content[0].text.strip()
            start = text.find("{")
            data = json.loads(text[start:text.rfind("}") + 1])
            if data.get("date_from"):
                df = datetime.strptime(data["date_from"], "%Y-%m-%d")
                dt = datetime.strptime(data["date_to"] + " 23:59:59", "%Y-%m-%d %H:%M:%S") if data.get("date_to") else df.replace(hour=23, minute=59, second=59)
                date_filter = (
                    f"DATE(SaleDateTimeUtc) = '{data['date_from']}'"
                    if data["date_from"] == data.get("date_to")
                    else f"DATE(SaleDateTimeUtc) BETWEEN '{data['date_from']}' AND '{data.get('date_to', today.isoformat())}'"
                )
                return df, dt, date_filter, llm_in, llm_out
        except Exception:
            pass

        return None, None, "", llm_in, llm_out

    def process_data_request(self, user_message: str, franchise_code: str, context: str = "", session_id: str = "") -> tuple[str, int, int]:
        log = get_session_logger(session_id) if session_id else logging.getLogger(__name__)
        today = datetime.now().strftime("%Y-%m-%d")

        log.info("━" * 60)
        log.info(f"CONSULTA : {user_message!r}")
        log.info(f"FRANCHISE: {franchise_code}")

        total_input = total_output = 0

        # 1. Extraer rango de fechas del mensaje para filtrar en el SP
        date_from, date_to, date_filter, tok_in, tok_out = self._extract_date_range(user_message, context)
        total_input += tok_in; total_output += tok_out
        log.info(f"FECHAS   : date_from={date_from}  date_to={date_to}")
        log.info(f"FILTRO   : {date_filter or '(sin filtro de fecha — año completo)'}")

        # 2. Obtener datos del SP (solo el rango necesario)
        sales = sales_repo.get_sales(franchise_code, date_from=date_from, date_to=date_to)
        log.info(f"SP ROWS  : {len(sales)} filas devueltas por sp_GetSalesForChatbot")

        # 3. Cargar en SQLite en memoria
        mem_conn = self._load_into_memory(sales)

        # 4. LLM genera SQL
        sql, tok_in, tok_out = self._generate_sql(user_message, len(sales), today, context)
        total_input += tok_in; total_output += tok_out
        log.info(f"SQL GEN  :\n{sql}")

        # 5. Ejecutar SQL
        columns, rows = self._execute_sql(mem_conn, sql)
        if rows and rows[0] and rows[0][0] and str(rows[0][0]).startswith("Error"):
            log.warning(f"SQL ERROR: {rows[0]}")
        else:
            log.info(f"SQL ROWS : {len(rows)} filas — columnas: {columns}")

        # 6. Calcular métricas en Python — filtradas por la misma fecha que el usuario pidió
        if date_from and date_to and date_from.date() == date_to.date():
            period_label = date_from.strftime("%d/%m/%Y")
        elif date_from and date_to:
            period_label = f"{date_from.strftime('%d/%m/%Y')} al {date_to.strftime('%d/%m/%Y')}"
        elif date_from:
            period_label = f"desde {date_from.strftime('%d/%m/%Y')}"
        else:
            period_label = "año completo"

        summary = self._compute_summary(mem_conn, date_filter, period_label)
        mem_conn.close()
        log.info(f"PERÍODO  : {period_label}")
        log.info("━" * 60)

        # 7. LLM formatea la respuesta
        response_text, tok_in, tok_out = self._format_response(user_message, sql, columns, rows, summary)
        total_input += tok_in; total_output += tok_out
        log.info(f"TOKENS   : input={total_input}  output={total_output}  total={total_input + total_output}")
        return response_text, total_input, total_output


data_agent = DataAgent()
