import re
import sqlite3
import struct
from datetime import datetime, timedelta, timezone

_DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def load_into_memory(sales: list[dict]) -> sqlite3.Connection:
    """Carga ventas en SQLite en memoria con columna DiaSemana calculada."""
    conn = sqlite3.connect(":memory:")
    if not sales:
        conn.execute("""
            CREATE TABLE ventas (
                id TEXT, FranchiseeCode TEXT, ShiftCode TEXT, PosCode TEXT,
                UserName TEXT, SaleDateTimeUtc TEXT, Quantity REAL,
                ArticleId TEXT, ArticleDescription TEXT, TypeDetail TEXT,
                UnitPriceFix REAL, DiaSemana TEXT
            )
        """)
        return conn

    columns = list(sales[0].keys())
    need_dia = "DiaSemana" not in columns
    if need_dia:
        columns = columns + ["DiaSemana"]

    cols_def = ", ".join([f'"{c}" TEXT' for c in columns])
    conn.execute(f"CREATE TABLE ventas ({cols_def})")

    try:
        sale_idx = columns.index("SaleDateTimeUtc")
    except ValueError:
        sale_idx = -1

    def fmt(v):
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)) and len(v) == 20:
            year, month, day, hour, minute, second, fraction, tz_h, tz_m = struct.unpack('<hHHHHHIhh', v)
            microsecond = fraction // 1000
            tz = timezone(timedelta(hours=tz_h, minutes=tz_m))
            dt = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tz)
            return dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d %H:%M:%S.%f")
        return re.sub(r'\s*[+-]\d{2}:\d{2}$', '', str(v))

    placeholders = ", ".join(["?" for _ in columns])
    for row in sales:
        values = [fmt(v) for v in row.values()]
        if need_dia:
            try:
                sale_str = values[sale_idx] if sale_idx >= 0 else None
                dia = _DIAS_ES[datetime.strptime(sale_str[:19], "%Y-%m-%d %H:%M:%S").weekday()] if sale_str else ""
            except Exception:
                dia = ""
            values.append(dia)
        conn.execute(f"INSERT INTO ventas VALUES ({placeholders})", values)
    conn.commit()
    return conn


def compute_summary(
    conn: sqlite3.Connection,
    date_filter: str = "",
    period_label: str = "",
    franchise_map: dict | None = None,
) -> str:
    """Calcula métricas de ventas en Python (sin LLM) para evitar inconsistencias."""
    try:
        base = f"\"Type\" != '2'{' AND ' + date_filter if date_filter else ''}"

        # Columnas disponibles en esta tabla (para secciones opcionales)
        available_cols = {r[1] for r in conn.execute("PRAGMA table_info(ventas)").fetchall()}

        totals = conn.execute(f"""
            SELECT COUNT(DISTINCT id),
                   ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2),
                   COUNT(DISTINCT UserName),
                   ROUND(SUM(CASE WHEN WeightKilos IS NOT NULL AND WeightKilos != ''
                             THEN CAST(Quantity AS REAL) * CAST(WeightKilos AS REAL) ELSE 0 END), 2)
            FROM ventas WHERE {base}
        """).fetchone()

        if not totals[0]:
            return "Sin datos para el período consultado."

        by_vendor = conn.execute(f"""
            SELECT UserName,
                   COUNT(DISTINCT id),
                   ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2),
                   ROUND(SUM(CASE WHEN WeightKilos IS NOT NULL AND WeightKilos != ''
                             THEN CAST(Quantity AS REAL) * CAST(WeightKilos AS REAL) ELSE 0 END), 2)
            FROM ventas WHERE {base}
            GROUP BY UserName ORDER BY 3 DESC
        """).fetchall()

        top_by_units = conn.execute(f"""
            SELECT ArticleDescription,
                   SUM(CAST(Quantity AS REAL)),
                   ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2),
                   ROUND(SUM(CASE WHEN WeightKilos IS NOT NULL AND WeightKilos != ''
                             THEN CAST(Quantity AS REAL) * CAST(WeightKilos AS REAL) ELSE 0 END), 2)
            FROM ventas WHERE {base}
            GROUP BY ArticleDescription ORDER BY 2 DESC LIMIT 10
        """).fetchall()

        top_by_revenue = conn.execute(f"""
            SELECT ArticleDescription,
                   ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2),
                   SUM(CAST(Quantity AS REAL))
            FROM ventas WHERE {base}
            GROUP BY ArticleDescription ORDER BY 2 DESC LIMIT 10
        """).fetchall()

        by_day = conn.execute(f"""
            SELECT DiaSemana,
                   COUNT(DISTINCT id),
                   ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2),
                   ROUND(SUM(CASE WHEN WeightKilos IS NOT NULL AND WeightKilos != ''
                             THEN CAST(Quantity AS REAL) * CAST(WeightKilos AS REAL) ELSE 0 END), 2)
            FROM ventas WHERE {base} AND DiaSemana IS NOT NULL AND DiaSemana != ''
            GROUP BY DiaSemana ORDER BY 2 DESC
        """).fetchall()

        hourly = conn.execute(f"""
            SELECT strftime('%H', SaleDateTimeUtc),
                   COUNT(DISTINCT id)
            FROM ventas WHERE {base}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 5
        """).fetchall()

        def fmt(n):
            return f"${n:,.0f}".replace(",", ".")

        avg_ticket = round(totals[1] / totals[0]) if totals[0] else 0
        total_kilos = totals[3] or 0

        period_str = f" — PERÍODO: {period_label}" if period_label else ""
        lines = [
            f"=== DATOS PRE-CALCULADOS{period_str} (usar exactamente estos números) ===",
            "",
            "RESUMEN GENERAL:",
            f"- Transacciones: {totals[0]}",
            f"- Total ventas: {fmt(totals[1])}",
            f"- Ticket promedio: {fmt(avg_ticket)}",
            f"- Vendedores activos: {totals[2]}",
        ]
        if total_kilos > 0:
            lines.append(f"- Kilos vendidos: {total_kilos:,.2f} kg")

        # ── Por franquicia (si hay más de una) ────────────────────────────
        if franchise_map and len(franchise_map) > 1:
            fc_col = "FranchiseCode" if conn.execute(
                "SELECT COUNT(*) FROM pragma_table_info('ventas') WHERE name='FranchiseCode'"
            ).fetchone()[0] else "FranchiseeCode"
            by_franchise = conn.execute(f"""
                SELECT "{fc_col}",
                       COUNT(DISTINCT id),
                       ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2)
                FROM ventas WHERE {base}
                GROUP BY "{fc_col}"
            """).fetchall()
            if by_franchise:
                lines += ["", "POR FRANQUICIA:"]
                for fc in by_franchise:
                    fc_label = franchise_map.get(fc[0], fc[0][:12] + "...")
                    fc_ticket = round(fc[2] / fc[1]) if fc[1] else 0
                    lines.append(
                        f"  • {fc_label}: {fc[1]} transacciones | {fmt(fc[2])} | ticket prom: {fmt(fc_ticket)}"
                    )

        # ── Por vendedor ──────────────────────────────────────────────────
        lines += ["", "POR VENDEDOR:"]
        for v in by_vendor:
            v_ticket = round(v[2] / v[1]) if v[1] else 0
            kilos_str = f" | kilos: {v[3]:,.2f} kg" if v[3] and v[3] > 0 else ""
            lines.append(f"  • {v[0]}: {v[1]} transacciones | {fmt(v[2])} | ticket prom: {fmt(v_ticket)}{kilos_str}")

        # ── Top productos por unidades ────────────────────────────────────
        lines += ["", "TOP PRODUCTOS (por unidades):"]
        for p in top_by_units:
            kilos_str = f" | {p[3]:,.2f} kg" if p[3] and p[3] > 0 else ""
            lines.append(f"  • {p[0]}: {p[1]:.0f} unidades | {fmt(p[2])}{kilos_str}")

        # ── Top productos por facturación ─────────────────────────────────
        lines += ["", "TOP PRODUCTOS (por facturación):"]
        for p in top_by_revenue:
            lines.append(f"  • {p[0]}: {fmt(p[1])} | {p[2]:.0f} unidades")

        # ── Por día de la semana ──────────────────────────────────────────
        if by_day:
            lines += ["", "VENTAS POR DÍA DE LA SEMANA:"]
            for d in by_day:
                kilos_str = f" | {d[3]:,.2f} kg" if d[3] and d[3] > 0 else ""
                lines.append(f"  • {d[0]}: {d[1]} transacciones | {fmt(d[2])}{kilos_str}")

        # ── Por canal (CtaChannel) — si existe la columna ─────────────────
        if "CtaChannel" in available_cols:
            by_channel = conn.execute(f"""
                SELECT CtaChannel,
                       COUNT(DISTINCT id),
                       ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2)
                FROM ventas WHERE {base} AND CtaChannel IS NOT NULL AND CtaChannel != ''
                GROUP BY CtaChannel ORDER BY 2 DESC
            """).fetchall()
            if by_channel:
                lines += ["", "POR CANAL (CtaChannel):"]
                for c in by_channel:
                    lines.append(f"  • {c[0]}: {c[1]} transacciones | {fmt(c[2])}")

        # ── Por forma de pago — si existe la columna ─────────────────────
        if "FormaPago" in available_cols:
            by_payment = conn.execute(f"""
                SELECT FormaPago,
                       COUNT(DISTINCT id),
                       ROUND(SUM(CAST(Quantity AS REAL) * CAST(UnitPriceFix AS REAL)), 2)
                FROM ventas WHERE {base} AND FormaPago IS NOT NULL AND FormaPago != ''
                GROUP BY FormaPago ORDER BY 2 DESC
            """).fetchall()
            if by_payment:
                lines += ["", "POR FORMA DE PAGO:"]
                for p in by_payment:
                    lines.append(f"  • {p[0]}: {p[1]} transacciones | {fmt(p[2])}")

        # ── Horas más activas ─────────────────────────────────────────────
        lines += ["", "HORAS MÁS ACTIVAS (transacciones únicas):"]
        for h in hourly:
            lines.append(f"  • {h[0]}:00 hs — {h[1]} transacciones")

        return "\n".join(lines)
    except Exception:
        return ""
