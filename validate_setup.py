"""
Script de validación del entorno.
Corre esto antes de levantar la app para verificar que todo está configurado.

Uso:
    python validate_setup.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def check(label: str, ok: bool, detail: str = ""):
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def validate_env():
    print("\n1. Variables de entorno (.env)")
    results = []
    auth_mode = os.getenv("DB_AUTH_MODE", "sql").lower()
    results.append(check("ANTHROPIC_API_KEY", bool(os.getenv("ANTHROPIC_API_KEY"))))
    results.append(check("DB_SERVER",         bool(os.getenv("DB_SERVER"))))
    results.append(check("DB_DATABASE",       bool(os.getenv("DB_DATABASE"))))
    results.append(check("DB_USER",           bool(os.getenv("DB_USER"))))
    # DB_PASSWORD solo es requerido en modo SQL clásico
    if auth_mode == "sql":
        results.append(check("DB_PASSWORD", bool(os.getenv("DB_PASSWORD"))))
    else:
        check("DB_PASSWORD", True, f"no requerido (DB_AUTH_MODE={auth_mode})")
    return all(results)


def validate_db():
    print("\n2. Conexión a SQL Server")
    try:
        import struct

        import pyodbc

        server    = os.getenv("DB_SERVER")
        database  = os.getenv("DB_DATABASE")
        user      = os.getenv("DB_USER")
        password  = os.getenv("DB_PASSWORD")
        auth_mode = os.getenv("DB_AUTH_MODE", "sql").lower()

        base = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server};DATABASE={database};"
        )

        if auth_mode == "activedirectoryinteractive":
            from azure.identity import InteractiveBrowserCredential
            print("  [..] Abriendo browser para login Azure AD...")
            credential = InteractiveBrowserCredential(login_hint=user)
            token = credential.get_token("https://database.windows.net/.default")
            token_bytes = token.token.encode("utf-16-le")
            token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
            conn = pyodbc.connect(base, attrs_before={1256: token_struct})
        elif auth_mode == "activedirectoryintegrated":
            conn = pyodbc.connect(base + "Authentication=ActiveDirectoryIntegrated;")
        else:
            conn = pyodbc.connect(base + f"UID={user};PWD={password}", timeout=5)
        check("Conexión a SQL Server", True, f"{server}/{database}")

        # Verificar tabla chatbot_memory
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'chatbot_memory'")
        exists = cursor.fetchone()[0] == 1
        check("Tabla chatbot_memory", exists, "Corre sql/create_chatbot_memory.sql si falla")

        # Verificar tabla Sales
        cursor.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Sales'")
        exists = cursor.fetchone()[0] == 1
        check("Tabla Sales", exists, "Corre sql/create_sales_table.sql si falla")

        # Verificar stored procedure
        cursor.execute("SELECT COUNT(*) FROM sys.objects WHERE type = 'P' AND name = 'sp_GetSalesForChatbot'")
        exists = cursor.fetchone()[0] == 1
        check("sp_GetSalesForChatbot", exists, "Corre sql/sp_GetSalesForChatbot.sql si falla")

        # Inspeccionar parámetros reales del SP y ejecutarlo
        if exists:
            cursor.execute("""
                SELECT PARAMETER_NAME, DATA_TYPE
                FROM INFORMATION_SCHEMA.PARAMETERS
                WHERE SPECIFIC_NAME = 'sp_GetSalesForChatbot'
                ORDER BY ORDINAL_POSITION
            """)
            params = cursor.fetchall()
            param_names = [p[0] for p in params]
            check("Parámetros del SP", True, ", ".join(param_names) if param_names else "ninguno")

            # Ejecutar pasando NULL a todos los parámetros
            null_params = ", ".join([f"{p[0]} = NULL" for p in params])
            exec_sql = f"EXEC sp_GetSalesForChatbot {null_params}" if null_params else "EXEC sp_GetSalesForChatbot"
            cursor.execute(exec_sql)
            rows = cursor.fetchall()
            check("Ejecutar sp_GetSalesForChatbot", True, f"{len(rows)} filas retornadas")

        conn.close()
        return True

    except Exception as e:
        check("Conexión a SQL Server", False, str(e))
        return False


def validate_anthropic():
    print("\n3. API de Anthropic")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "Di solo: OK"}],
        )
        ok = bool(response.content[0].text)
        check("Conexión a Anthropic API", ok, response.content[0].text.strip())
        return ok
    except Exception as e:
        check("Conexión a Anthropic API", False, str(e))
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("  Validación del entorno — Chatbot Multi-Agente")
    print("=" * 50)

    env_ok  = validate_env()
    db_ok   = validate_db() if env_ok else False
    api_ok  = validate_anthropic() if env_ok else False

    print("\n" + "=" * 50)
    if env_ok and db_ok and api_ok:
        print("  Todo OK — puedes levantar la app:")
        print("  uvicorn app.main:app --reload")
    else:
        print("  Hay errores. Corrige los FAIL de arriba antes de continuar.")
    print("=" * 50 + "\n")

    sys.exit(0 if (env_ok and db_ok and api_ok) else 1)
