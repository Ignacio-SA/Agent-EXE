import logging
import time
from datetime import datetime, date

from .connection import db

_log = logging.getLogger(__name__)


class _SalesCache:
    """
    Cache en RAM con TTL diferenciado: corto para hoy, largo para históricos.
    Clave: (franchisee_code, franchise_codes_str, date_from, date_to)
    donde franchise_codes_str es la cadena CSV pasada al SP (None = todas).
    """

    TTL_TODAY   = 300    # 5 min  — datos del día en curso pueden cambiar
    TTL_HISTORY = 3600   # 60 min — histórico no cambia

    def __init__(self):
        self._store: dict[tuple, tuple[list, float]] = {}

    def _key(self, franchisee_code, franchise_codes_str, date_from, date_to):
        df = date_from.date() if isinstance(date_from, datetime) else date_from
        dt = date_to.date()   if isinstance(date_to,   datetime) else date_to
        return (franchisee_code, franchise_codes_str or "", str(df), str(dt))

    def _is_today_range(self, date_from, date_to) -> bool:
        today = date.today()
        df = date_from.date() if isinstance(date_from, datetime) else (date_from or today)
        dt = date_to.date()   if isinstance(date_to,   datetime) else (date_to   or today)
        return dt >= today or df >= today

    def get(self, franchisee_code, franchise_codes_str, date_from, date_to):
        key = self._key(franchisee_code, franchise_codes_str, date_from, date_to)
        entry = self._store.get(key)
        if not entry:
            return None
        data, ts = entry
        ttl = self.TTL_TODAY if self._is_today_range(date_from, date_to) else self.TTL_HISTORY
        if time.time() - ts > ttl:
            del self._store[key]
            return None
        return data

    def set(self, franchisee_code, franchise_codes_str, date_from, date_to, data: list):
        key = self._key(franchisee_code, franchise_codes_str, date_from, date_to)
        self._store[key] = (data, time.time())

    def invalidate_today(self):
        today = str(date.today())
        stale = [k for k, (_, ts) in self._store.items() if today in k]
        for k in stale:
            del self._store[k]


_cache = _SalesCache()


class SalesRepository:
    @staticmethod
    def get_sales(
        franchisee_code: str,
        franchise_codes: list[str] | None = None,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> list[dict]:
        """
        Llama a sp_GetSalesForChatbot con:
          @FranchiseeCode  — obligatorio: el dueño de las franquicias
          @FranchiseCodes  — opcional (NULL = todas):
                             string CSV con los códigos deseados,
                             ej. 'code1' o 'code1,code2,code3'
          @Year, @DateFrom, @DateTo — filtros de fecha

        El SP aplica internamente:
          WHERE (@FranchiseCodes IS NULL
                 OR FranchiseCode IN (SELECT value FROM STRING_SPLIT(@FranchiseCodes, ',')))
        """
        # Armar string CSV para el SP
        franchise_codes_str = ",".join(franchise_codes) if franchise_codes else None

        cached = _cache.get(franchisee_code, franchise_codes_str, date_from, date_to)
        if cached is not None:
            _log.debug(
                "[SalesRepo] Cache hit — franchisee=%s codes=%s",
                franchisee_code, franchise_codes_str,
            )
            return cached

        with db.get_connection() as conn:
            cursor = conn.cursor()
            t0 = time.perf_counter()
            cursor.execute(
                "EXEC sp_GetSalesForChatbot "
                "@FranchiseeCode=?, @FranchiseCodes=?, @Year=?, @DateFrom=?, @DateTo=?",
                (franchisee_code, franchise_codes_str, year, date_from, date_to),
            )
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            elapsed = (time.perf_counter() - t0) * 1000
            _log.info(
                "[SalesRepo] SP ejecutado en %.0f ms — franchisee=%s codes=%s rows=%d",
                elapsed, franchisee_code, franchise_codes_str, len(rows),
            )
            result = [dict(zip(columns, row)) for row in rows]

        _cache.set(franchisee_code, franchise_codes_str, date_from, date_to, result)
        return result

    @staticmethod
    def get_sales_summary(
        franchisee_code: str,
        franchise_codes: list[str] | None = None,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> dict:
        sales = SalesRepository.get_sales(franchisee_code, franchise_codes, year, date_from, date_to)
        if not sales:
            return {"total": 0, "items": []}
        return {"total": len(sales), "items": sales[:20]}


sales_repo = SalesRepository()
