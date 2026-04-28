from datetime import datetime


class SessionContext:
    """Maintains per-session state across turns: franchise, date range, last product."""

    def __init__(self):
        self._data: dict[str, dict] = {}

    def _bucket(self, session_id: str) -> dict:
        if session_id not in self._data:
            self._data[session_id] = {}
        return self._data[session_id]

    # ── Franchise ────────────────────────────────────────────────────────
    def get_franchise(self, session_id: str) -> list[str] | None:
        return self._data.get(session_id, {}).get("franchise")

    def set_franchise(self, session_id: str, codes: list[str]) -> None:
        self._bucket(session_id)["franchise"] = codes

    # ── Date range ───────────────────────────────────────────────────────
    def get_date(self, session_id: str) -> tuple[datetime, datetime, str] | None:
        return self._data.get(session_id, {}).get("date")

    def set_date(self, session_id: str, date_from: datetime, date_to: datetime, date_filter: str) -> None:
        self._bucket(session_id)["date"] = (date_from, date_to, date_filter)

    # ── Last product/article mentioned ───────────────────────────────────
    def get_product(self, session_id: str) -> str | None:
        return self._data.get(session_id, {}).get("last_product")

    def set_product(self, session_id: str, product: str) -> None:
        self._bucket(session_id)["last_product"] = product


session_context = SessionContext()
