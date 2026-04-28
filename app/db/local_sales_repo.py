"""
local_sales_repo.py
-------------------
Repositorio de ventas LOCAL basado en db_ventas.db (SQLite).

Cuando db_ventas.db existe, los datos se cargan COMPLETOS en memoria RAM
al arrancar la aplicación (una sola vez). Las consultas posteriores no
tocan disco: se filtran directamente sobre la lista en RAM.

Soporta múltiples franquicias: get_sales() acepta una lista de códigos.
Si franchise_codes es None o vacío, retorna todas las franquicias del DB.
"""

import logging
import sqlite3
from datetime import datetime, date

_log = logging.getLogger(__name__)


class LocalSalesRepository:
    """
    Lee db_ventas.db UNA VEZ al inicializar y guarda todas las filas en RAM.
    Las consultas filtran en Python sobre esa lista — sin I/O de disco posterior.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._all_rows: list[dict] = []
        self._columns: list[str] = []
        self._loaded = False
        self._fc_col: str = "FranchiseeCode"

    # ------------------------------------------------------------------
    # Carga inicial en RAM (llamar UNA SOLA VEZ al arrancar la app)
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Carga todos los datos de db_ventas.db en memoria RAM."""
        _log.info("[LOCAL-DB] Cargando db_ventas.db en RAM…")
        t0 = _now_ms()
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM ventas")
            self._columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            self._all_rows = [dict(row) for row in rows]
        finally:
            conn.close()

        elapsed = _now_ms() - t0
        self._loaded = True
        self._fc_col = "FranchiseCode" if "FranchiseCode" in self._columns else "FranchiseeCode"

        available = self.get_available_franchise_codes()
        _log.info(
            "[LOCAL-DB] db_ventas.db cargada en RAM: %d filas, %.0f ms",
            len(self._all_rows),
            elapsed,
        )
        _log.info("[LOCAL-DB] Franquicias disponibles en DB: %s", available)

    # ------------------------------------------------------------------
    # Consulta de franquicias disponibles
    # ------------------------------------------------------------------
    def get_available_franchise_codes(self) -> list[str]:
        """Retorna los códigos de franquicia únicos presentes en el DB."""
        if not self._loaded:
            self.load()
        codes = list({row.get(self._fc_col) for row in self._all_rows if row.get(self._fc_col)})
        return codes

    # ------------------------------------------------------------------
    # Interfaz pública — igual a SalesRepository
    # ------------------------------------------------------------------
    def get_sales(
        self,
        franchise_codes: list[str] | None = None,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> list[dict]:
        """
        Filtra las filas en RAM por franchise_codes y rango de fechas.
        franchise_codes=None retorna todas las franquicias del DB.
        """
        if not self._loaded:
            self.load()

        t0 = _now_ms()

        df_date = _to_date(date_from)
        dt_date = _to_date(date_to)
        codes_set = set(franchise_codes) if franchise_codes else None

        result = []
        for row in self._all_rows:
            # Filtrar por franquicia(s) si se especifica
            if codes_set is not None:
                row_fc = row.get(self._fc_col)
                if row_fc not in codes_set:
                    continue

            # Filtrar por año si se especifica
            if year is not None:
                row_year = _extract_year(row)
                if row_year is not None and row_year != year:
                    continue

            # Filtrar por rango de fechas
            row_date = _extract_date(row)
            if row_date is not None:
                if df_date and row_date < df_date:
                    continue
                if dt_date and row_date > dt_date:
                    continue

            result.append(row)

        _log.info(
            "[LOCAL-DB] Consulta: codes=%s date_from=%s date_to=%s → %d filas (%.0f ms)",
            franchise_codes,
            date_from,
            date_to,
            len(result),
            _now_ms() - t0,
        )
        return result

    def get_sales_summary(
        self,
        franchise_codes: list[str] | None = None,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> dict:
        sales = self.get_sales(franchise_codes, year, date_from, date_to)
        if not sales:
            return {"total": 0, "items": []}
        return {"total": len(sales), "items": sales[:20]}


# ------------------------------------------------------------------
# Helpers internos
# ------------------------------------------------------------------

def _now_ms() -> float:
    import time
    return time.perf_counter() * 1000


def _to_date(dt) -> date | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.date()
    if isinstance(dt, date):
        return dt
    return None


def _extract_date(row: dict) -> date | None:
    """Extrae la fecha de SaleDateTimeUtc (string ISO o datetime)."""
    val = row.get("SaleDateTimeUtc")
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        s = str(val).strip()[:10]  # "YYYY-MM-DD"
        return date.fromisoformat(s)
    except Exception:
        return None


def _extract_year(row: dict) -> int | None:
    d = _extract_date(row)
    return d.year if d else None
