import time
from datetime import datetime, date

from .connection import db


class _SalesCache:
    """Cache en RAM con TTL diferenciado: corto para hoy, largo para históricos."""

    TTL_TODAY    = 300    # 5 min  — datos del día en curso pueden cambiar
    TTL_HISTORY  = 3600   # 60 min — histórico no cambia

    def __init__(self):
        self._store: dict[tuple, tuple[list, float]] = {}

    def _key(self, franchise_code, date_from, date_to):
        df = date_from.date() if isinstance(date_from, datetime) else date_from
        dt = date_to.date()   if isinstance(date_to,   datetime) else date_to
        return (franchise_code, str(df), str(dt))

    def _is_today_range(self, date_from, date_to) -> bool:
        today = date.today()
        df = date_from.date() if isinstance(date_from, datetime) else (date_from or today)
        dt = date_to.date()   if isinstance(date_to,   datetime) else (date_to   or today)
        return dt >= today or df >= today

    def get(self, franchise_code, date_from, date_to):
        key = self._key(franchise_code, date_from, date_to)
        entry = self._store.get(key)
        if not entry:
            return None
        data, ts = entry
        ttl = self.TTL_TODAY if self._is_today_range(date_from, date_to) else self.TTL_HISTORY
        if time.time() - ts > ttl:
            del self._store[key]
            return None
        return data

    def set(self, franchise_code, date_from, date_to, data: list):
        key = self._key(franchise_code, date_from, date_to)
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
        franchise_code: str,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> list[dict]:
        cached = _cache.get(franchise_code, date_from, date_to)
        if cached is not None:
            return cached

        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "EXEC sp_GetSalesForChatbot @FranchiseCode=?, @Year=?, @DateFrom=?, @DateTo=?",
                (franchise_code, year, date_from, date_to),
            )
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = [dict(zip(columns, row)) for row in rows]

        _cache.set(franchise_code, date_from, date_to, result)
        return result

    @staticmethod
    def get_sales_summary(
        franchise_code: str,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> dict:
        sales = SalesRepository.get_sales(franchise_code, year, date_from, date_to)
        if not sales:
            return {"total": 0, "items": []}
        return {
            "total": len(sales),
            "items": sales[:20],
        }


sales_repo = SalesRepository()
