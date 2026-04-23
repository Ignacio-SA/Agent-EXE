from datetime import datetime

from pydantic import BaseModel


class MemoryEntry(BaseModel):
    id: int | None = None
    session_id: str
    user_id: str
    context: str
    summary: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemorySummary(BaseModel):
    session_id: str
    summary: str
    key_points: list[str]
    last_updated: datetime
