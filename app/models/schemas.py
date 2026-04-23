from datetime import datetime

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: str
    user_id: str | None = None
    training_mode: bool = False


class FeedbackRequest(BaseModel):
    session_id: str
    user_message: str
    bot_response: str
    feedback: str
    feedback_type: str  # "positivo" | "negativo"


class FeedbackResponse(BaseModel):
    ok: bool
    component: str = ""
    priority: str = ""


class ChatResponse(BaseModel):
    session_id: str
    response: str
    agent_type: str
    timestamp: datetime


class HistoryRequest(BaseModel):
    session_id: str
    limit: int = 10


class HistoryEntry(BaseModel):
    session_id: str
    user_message: str
    bot_response: str
    agent_type: str
    timestamp: datetime
