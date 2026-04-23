import struct
from contextlib import contextmanager

import pyodbc

from ..config import settings


_credential = None

def _get_azure_token() -> bytes:
    """Obtiene un token Azure AD — reutiliza la sesión para no pedir MFA en cada request."""
    global _credential
    from azure.identity import InteractiveBrowserCredential

    if _credential is None:
        _credential = InteractiveBrowserCredential(login_hint=settings.db_user)

    # get_token renueva automáticamente el token si está por vencer
    token = _credential.get_token("https://database.windows.net/.default")
    token_bytes = token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


class DatabaseConnection:
    def __init__(self):
        self.mode = settings.db_auth_mode.lower()
        self.base_string = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={settings.db_server};"
            f"DATABASE={settings.db_name};"
        )

    def connect(self):
        try:
            if self.mode in ("activedirectoryinteractive", "interactive"):
                token_bytes = _get_azure_token()
                conn = pyodbc.connect(
                    self.base_string,
                    attrs_before={1256: token_bytes},  # SQL_COPT_SS_ACCESS_TOKEN = 1256
                )
            elif self.mode == "activedirectoryintegrated":
                conn = pyodbc.connect(self.base_string + "Authentication=ActiveDirectoryIntegrated;")
            else:
                conn = pyodbc.connect(
                    self.base_string + f"UID={settings.db_user};PWD={settings.db_password}"
                )
            conn.autocommit = False
            # Habilitar soporte de DATETIME2 con precisión completa
            conn.add_output_converter(-155, lambda x: x)
            return conn
        except Exception as e:
            raise Exception(f"Database connection failed: {e!s}")

    @contextmanager
    def get_connection(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()


db = DatabaseConnection()
