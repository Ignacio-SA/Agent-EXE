import logging
import struct
import threading
import time
from contextlib import contextmanager

import pyodbc

from ..config import settings


_credential = None
_local = threading.local()
_log = logging.getLogger(__name__)


def _get_azure_token() -> bytes:
    """Obtiene un token Azure AD — reutiliza la sesión para no pedir MFA en cada request."""
    global _credential
    from azure.identity import InteractiveBrowserCredential

    if _credential is None:
        _credential = InteractiveBrowserCredential(login_hint=settings.db_user)

    t0 = time.perf_counter()
    token = _credential.get_token("https://database.windows.net/.default")
    _log.info("[DB timing] get_token: %.0f ms", (time.perf_counter() - t0) * 1000)

    token_bytes = token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


_ODBC_DOWNLOAD = "https://aka.ms/downloadmsodbcsql"

def _check_odbc_driver():
    installed = pyodbc.drivers()
    has_driver = any("ODBC Driver" in d and "SQL Server" in d for d in installed)
    if not has_driver:
        raise Exception(
            "Falta el driver ODBC para SQL Server.\n"
            f"Descargalo e instalalo desde: {_ODBC_DOWNLOAD}\n"
            f"Drivers disponibles: {installed or ['(ninguno)']}"
        )


def _open_connection():
    """Abre una conexión raw. Solo se llama una vez por thread."""
    _check_odbc_driver()
    mode = settings.db_auth_mode.lower()
    base = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={settings.db_server};"
        f"DATABASE={settings.db_name};"
    )
    try:
        t0 = time.perf_counter()
        if mode in ("activedirectoryinteractive", "interactive"):
            conn = pyodbc.connect(base, attrs_before={1256: _get_azure_token()})
        elif mode == "activedirectoryintegrated":
            conn = pyodbc.connect(base + "Authentication=ActiveDirectoryIntegrated;")
        else:
            conn = pyodbc.connect(base + f"UID={settings.db_user};PWD={settings.db_password}")
        _log.info("[DB timing] pyodbc.connect: %.0f ms", (time.perf_counter() - t0) * 1000)
        conn.autocommit = False
        conn.add_output_converter(-155, lambda x: x)
        return conn
    except Exception as e:
        raise Exception(f"Database connection failed: {e!s}")


class DatabaseConnection:
    @contextmanager
    def get_connection(self):
        conn = getattr(_local, "conn", None)
        if conn is None:
            conn = _open_connection()
            _local.conn = conn
        else:
            try:
                conn.execute("SELECT 1")
            except Exception:
                _log.warning("[DB] Conexión caducada — reconectando...")
                _local.conn = None
                conn = _open_connection()
                _local.conn = conn
        try:
            yield conn
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            _local.conn = None  # fuerza reconexión en la siguiente llamada
            raise e


db = DatabaseConnection()
