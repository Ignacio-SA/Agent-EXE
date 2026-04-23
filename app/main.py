import json as _json
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db.memory_repo import init_memory_db
from .routers import chat, debug


class UTF8JSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return _json.dumps(content, ensure_ascii=False).encode("utf-8")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

# Validar variables críticas al iniciar
if not settings.anthropic_api_key:
    raise RuntimeError("ANTHROPIC_API_KEY no está configurada en el archivo .env")

if settings.db_auth_mode == "sql" and not settings.db_password:
    raise RuntimeError("DB_PASSWORD no está configurada en el archivo .env")

app = FastAPI(
    title="Chatbot Multi-Agente",
    description="API de chatbot con múltiples agentes especializados",
    version="1.0.0",
    default_response_class=UTF8JSONResponse,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializar SQLite para memoria local
init_memory_db()

# Routers
app.include_router(chat.router)
app.include_router(debug.router)


# Health check
@app.get("/health")
async def health_check():
    return {"status": "ok", "environment": settings.fastapi_env}


# Servir UI de prueba
ui_path = os.path.join(os.path.dirname(__file__), "..", "ui_test")
if os.path.exists(ui_path):
    app.mount("/ui", StaticFiles(directory=ui_path), name="ui")


@app.get("/")
async def root():
    return {"message": "Chatbot Multi-Agente API", "docs": "/docs", "ui": "/ui/index.html"}
