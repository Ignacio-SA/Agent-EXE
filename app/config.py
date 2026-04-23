import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


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

    # Memoria local (SQLite) — ruta al archivo .db
    memory_db_path: str = os.getenv("MEMORY_DB_PATH", "./memory.db")

    # API Settings
    api_rate_limit: int = int(os.getenv("API_RATE_LIMIT", "100"))
    api_timeout: int = int(os.getenv("API_TIMEOUT", "30"))
    fastapi_env: str = os.getenv("FASTAPI_ENV", "development")
    fastapi_debug: bool = os.getenv("FASTAPI_DEBUG", "false").lower() == "true"

    class Config:
        env_file = ".env"


settings = Settings()
