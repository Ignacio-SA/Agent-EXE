import json
import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv(override=True)

_LABELS_PATH = os.path.join(os.path.dirname(__file__), "..", "context", "franchise_labels.json")

# Cache del JSON — se recarga solo si el archivo cambia (mtime)
_labels_cache: dict[str, str] = {}
_labels_mtime: float = 0.0


def _load_franchise_labels() -> dict[str, str]:
    """
    Lee context/franchise_labels.json y lo cachea en RAM.
    Se recarga automáticamente si el archivo fue modificado.
    Permite actualizar labels sin reiniciar la app.
    """
    global _labels_cache, _labels_mtime
    try:
        mtime = os.path.getmtime(_LABELS_PATH)
        if _labels_cache and mtime == _labels_mtime:
            return _labels_cache
        with open(_LABELS_PATH, encoding="utf-8") as f:
            _labels_cache = json.load(f)
            _labels_mtime = mtime
        return _labels_cache
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Database
    db_server: str = os.getenv("DB_SERVER", "localhost")
    db_name: str = os.getenv("DB_NAME", "chatbot_db")
    db_user: str = os.getenv("DB_USER", "")
    db_password: str = os.getenv("DB_PASSWORD", "")
    # Azure AD auth: "sql" | "activedirectoryinteractive" | "activedirectoryintegrated"
    db_auth_mode: str = os.getenv("DB_AUTH_MODE", "sql")

    # Código del franquiciado (dueño) — se pasa al SP como @FranchiseeCode.
    # El SP devuelve todas sus franquicias si @FranchiseCodes es NULL.
    # Backward compat: si FRANCHISEE_CODE no está, usa FRANCHISE_CODE.
    franchisee_code: str = os.getenv(
        "FRANCHISEE_CODE",
        os.getenv("FRANCHISE_CODE", ""),
    )

    # Alias de backward compat (usado en memory_repo y otros lugares)
    @property
    def franchise_code(self) -> str:
        return self.franchisee_code

    # Memoria local (SQLite)
    memory_db_path: str = os.getenv("MEMORY_DB_PATH", "./memory.db")

    # API Settings
    api_rate_limit: int = int(os.getenv("API_RATE_LIMIT", "100"))
    api_timeout: int = int(os.getenv("API_TIMEOUT", "30"))
    fastapi_env: str = os.getenv("FASTAPI_ENV", "development")
    fastapi_debug: bool = os.getenv("FASTAPI_DEBUG", "false").lower() == "true"

    @property
    def franchise_map(self) -> dict[str, str]:
        """
        Mapa {franchise_code: label} desde context/franchise_labels.json.
        Se recarga automáticamente si el archivo cambia (sin reiniciar).
        """
        return _load_franchise_labels()

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
