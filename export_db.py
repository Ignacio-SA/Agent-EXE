"""
Exporta datos del SP a un archivo SQLite (.db) con UserName anonimizado.

Llama UNA SOLA VEZ al SP con @FranchiseeCode (el dueño) y @FranchiseCodes=NULL
para obtener TODAS las franquicias de ese franquiciado en un solo resultado.

Uso:
    python export_db.py [output.db]

Variables de entorno requeridas:
    FRANCHISEE_CODE  — código del franquiciado (o FRANCHISE_CODE como fallback)
    DB_SERVER, DB_NAME, DB_AUTH_MODE
"""
import os
import re
import sqlite3
import struct
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv(override=True)

FRANCHISEE_CODE = os.environ.get("FRANCHISEE_CODE") or os.environ.get("FRANCHISE_CODE")
if not FRANCHISEE_CODE:
    raise SystemExit("ERROR: Definí FRANCHISEE_CODE en el .env")

DATE_FROM = datetime(2025, 1, 1, 0, 0, 0)
DATE_TO   = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)

DB_SERVER   = os.environ["DB_SERVER"]
DB_NAME     = os.environ.get("DB_NAME") or os.environ["DB_DATABASE"]
DB_AUTH     = os.environ.get("DB_AUTH_MODE", "sql").lower()
DB_USER     = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

OUTPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else f"export_{datetime.now().strftime('%Y%m%d')}.db"


# ---------------------------------------------------------------------------
# Conexión SQL Server
# ---------------------------------------------------------------------------
def _get_azure_token() -> bytes:
    from azure.identity import InteractiveBrowserCredential
    cred = InteractiveBrowserCredential(login_hint=DB_USER or None)
    token = cred.get_token("https://database.windows.net/.default")
    token_bytes = token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def open_connection():
    import pyodbc
    base = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
    )
    if DB_AUTH in ("activedirectoryinteractive", "interactive"):
        return pyodbc.connect(base, attrs_before={1256: _get_azure_token()})
    elif DB_AUTH == "activedirectoryintegrated":
        return pyodbc.connect(base + "Authentication=ActiveDirectoryIntegrated;")
    else:
        return pyodbc.connect(base + f"UID={DB_USER};PWD={DB_PASSWORD}")


# ---------------------------------------------------------------------------
# Fetch desde SP — una sola llamada con @FranchiseCodes=NULL (todas)
# ---------------------------------------------------------------------------
def fetch_all_sales() -> tuple[list[str], list[tuple]]:
    """
    Obtiene todas las franquicias del franquiciado en una sola llamada al SP.
    @FranchiseCodes = NULL → el SP devuelve todos los franchises del dueño.
    """
    print(f"Conectando a {DB_SERVER}/{DB_NAME}...")
    print(f"Franquiciado : {FRANCHISEE_CODE}")
    print(f"Rango        : {DATE_FROM.date()} -> {DATE_TO.date()}")

    conn = open_connection()
    conn.autocommit = False
    conn.add_output_converter(-155, lambda x: x)
    cursor = conn.cursor()

    print("Ejecutando SP (todas las franquicias)...")
    cursor.execute(
        "EXEC sp_GetSalesForChatbot "
        "@FranchiseeCode=?, @FranchiseCodes=NULL, @DateFrom=?, @DateTo=?",
        (FRANCHISEE_CODE, DATE_FROM, DATE_TO),
    )
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    conn.close()

    print(f"  {len(rows)} filas obtenidas, {len(columns)} columnas.")
    return columns, rows


# ---------------------------------------------------------------------------
# Conversión de valores
# ---------------------------------------------------------------------------
def _fmt_value(v):
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)) and len(v) == 20:
        year, month, day, hour, minute, second, fraction, tz_h, tz_m = struct.unpack('<hHHHHHIhh', v)
        microsecond = fraction // 1000
        tz = timezone(timedelta(hours=tz_h, minutes=tz_m))
        dt = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tz)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S.%f")
    return re.sub(r'\s*[+-]\d{2}:\d{2}$', '', str(v))


# ---------------------------------------------------------------------------
# Anonimización
# ---------------------------------------------------------------------------
def build_username_map(columns: list[str], rows: list[tuple]) -> dict[str, str]:
    """Mapea cada UserName único a 'Colaborador N' (orden de primera aparición)."""
    try:
        idx = columns.index("UserName")
    except ValueError:
        return {}
    mapping: dict[str, str] = {}
    counter = 1
    for row in rows:
        name = row[idx]
        if name and name not in mapping:
            mapping[name] = f"Colaborador {counter}"
            counter += 1
    print(f"  {len(mapping)} colaboradores únicos anonimizados.")
    return mapping


# ---------------------------------------------------------------------------
# Exportar a SQLite
# ---------------------------------------------------------------------------
def _load_labels() -> dict[str, str]:
    """Lee context/franchise_labels.json si existe."""
    import json
    labels_path = os.path.join(os.path.dirname(__file__), "context", "franchise_labels.json")
    try:
        with open(labels_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def export_to_sqlite(columns: list[str], rows: list[tuple], username_map: dict[str, str]):
    try:
        username_idx = columns.index("UserName")
    except ValueError:
        username_idx = -1

    fc_col = "FranchiseCode" if "FranchiseCode" in columns else "FranchiseeCode"
    try:
        fc_idx = columns.index(fc_col)
    except ValueError:
        fc_idx = -1

    try:
        sale_idx = columns.index("SaleDateTimeUtc")
    except ValueError:
        sale_idx = -1

    _DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

    # Mapa {código: "1", "2", ...} según el orden en franchise_labels.json
    labels = _load_labels()
    franchise_number_map = {code: str(i + 1) for i, code in enumerate(labels.keys())}

    cols_def = ", ".join([f'"{c}" TEXT' for c in columns]) + ', "Franquicia" TEXT, "DiaSemana" TEXT'

    if os.path.exists(OUTPUT_PATH):
        os.remove(OUTPUT_PATH)

    conn = sqlite3.connect(OUTPUT_PATH)
    conn.execute(f"CREATE TABLE ventas ({cols_def})")

    placeholders = ", ".join(["?" for _ in columns]) + ", ?, ?"
    batch = []
    for row in rows:
        values = [_fmt_value(v) for v in row]
        if username_idx >= 0 and values[username_idx]:
            values[username_idx] = username_map.get(values[username_idx], values[username_idx])
        fc_val = values[fc_idx] if fc_idx >= 0 else None
        values.append(franchise_number_map.get(fc_val, "") if fc_val else "")
        try:
            sale_str = values[sale_idx] if sale_idx >= 0 else None
            dia = _DIAS[datetime.strptime(sale_str[:19], "%Y-%m-%d %H:%M:%S").weekday()] if sale_str else ""
        except Exception:
            dia = ""
        values.append(dia)
        batch.append(values)
        if len(batch) >= 5000:
            conn.executemany(f"INSERT INTO ventas VALUES ({placeholders})", batch)
            batch.clear()

    if batch:
        conn.executemany(f"INSERT INTO ventas VALUES ({placeholders})", batch)

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    fc_col = "FranchiseCode" if "FranchiseCode" in columns else "FranchiseeCode"
    by_franchise = conn.execute(
        f'SELECT "{fc_col}", COUNT(*) FROM ventas GROUP BY "{fc_col}"'
    ).fetchall()
    conn.close()

    labels = _load_labels()
    print(f"  {total} filas escritas en '{OUTPUT_PATH}'.")
    for code, count in by_franchise:
        label = labels.get(code, code[:8] + "...") if code else "—"
        print(f"    • {label} ({code}): {count} filas")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    columns, rows = fetch_all_sales()
    username_map = build_username_map(columns, rows)
    print("\nExportando a SQLite...")
    export_to_sqlite(columns, rows, username_map)
    print(f"\nListo: {os.path.abspath(OUTPUT_PATH)}")
