"""
Convierte un CSV exportado desde SSMS al db_ventas.db que usa el chatbot.
Computa Franquicia y DiaSemana, y anonimiza UserName.

Uso:
    python csv_to_db.py ventas.csv [db_ventas.db]
"""
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime

INPUT_CSV  = sys.argv[1] if len(sys.argv) > 1 else None
OUTPUT_PATH = sys.argv[2] if len(sys.argv) > 2 else "db_ventas.db"

if not INPUT_CSV:
    raise SystemExit("Uso: python csv_to_db.py ventas.csv [db_ventas.db]")
if not os.path.exists(INPUT_CSV):
    raise SystemExit(f"ERROR: No se encuentra '{INPUT_CSV}'")

_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _load_labels() -> dict[str, str]:
    labels_path = os.path.join(os.path.dirname(__file__), "context", "franchise_labels.json")
    try:
        with open(labels_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _dia_semana(sale_str: str) -> str:
    try:
        return _DIAS[datetime.strptime(sale_str[:19], "%Y-%m-%d %H:%M:%S").weekday()]
    except Exception:
        return ""


def _normalize(val: str):
    """Convierte strings vacios y NULLs de SSMS a None."""
    if val in ("NULL", "null", "None", ""):
        return None
    return val


def main():
    labels = _load_labels()
    franchise_number_map = {code: str(i + 1) for i, code in enumerate(labels.keys())}

    # Columnas esperadas del SP (en orden)
    SP_COLUMNS = [
        "id", "FranchiseeCode", "FranchiseCode", "ShiftCode", "PosCode",
        "UserName", "SaleDateTimeUtc", "Quantity", "ArticleId", "ArticleDescription",
        "TypeDetail", "UnitPriceFix", "Type", "CtaChannel", "VtaOperation",
        "Plataforma", "FormaPago", "WeightKilos",
    ]

    print(f"Leyendo '{INPUT_CSV}'...")
    # utf-8-sig maneja el BOM que agrega SSMS al exportar
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="\t,;|")
        except csv.Error:
            dialect = csv.excel  # fallback: coma

        # Detectar si el CSV tiene encabezados comparando la primera fila con las columnas esperadas
        first_fields = sample.split("\n")[0].strip().split(dialect.delimiter)
        first_fields = [f.strip().strip('"') for f in first_fields]
        has_header = first_fields[0].lower() == "id"

        if not has_header:
            print("  [!] CSV sin encabezados — inyectando columnas del SP.")
            reader = csv.DictReader(f, fieldnames=SP_COLUMNS, dialect=dialect)
        else:
            reader = csv.DictReader(f, dialect=dialect)

        rows = list(reader)

    if not rows:
        raise SystemExit("ERROR: El CSV esta vacio.")

    columns = list(rows[0].keys())
    print(f"  {len(rows)} filas, {len(columns)} columnas.")
    print(f"  Columnas: {', '.join(columns)}")

    # Anonimizar UserName
    username_map: dict[str, str] = {}
    counter = 1
    if "UserName" in columns:
        for row in rows:
            name = row.get("UserName", "")
            if name and name not in ("NULL", "null", "None") and name not in username_map:
                username_map[name] = f"Colaborador {counter}"
                counter += 1
    print(f"  {len(username_map)} colaboradores anonimizados.")

    fc_col = "FranchiseCode" if "FranchiseCode" in columns else "FranchiseeCode"

    # Crear SQLite
    if os.path.exists(OUTPUT_PATH):
        os.remove(OUTPUT_PATH)

    cols_def    = ", ".join([f'"{c}" TEXT' for c in columns]) + ', "Franquicia" TEXT, "DiaSemana" TEXT'
    placeholders = ", ".join(["?" for _ in columns]) + ", ?, ?"

    conn = sqlite3.connect(OUTPUT_PATH)
    conn.execute(f"CREATE TABLE ventas ({cols_def})")

    username_idx   = columns.index("UserName")         if "UserName"         in columns else -1
    fc_idx         = columns.index(fc_col)             if fc_col             in columns else -1
    sale_idx       = columns.index("SaleDateTimeUtc")  if "SaleDateTimeUtc"  in columns else -1

    batch = []
    for row in rows:
        values = [_normalize(row.get(c, "")) for c in columns]

        if username_idx >= 0 and values[username_idx]:
            values[username_idx] = username_map.get(values[username_idx], values[username_idx])

        fc_val     = values[fc_idx] if fc_idx >= 0 else None
        franquicia = franchise_number_map.get(fc_val, "") if fc_val else ""
        values.append(franquicia)

        sale_str = values[sale_idx] if sale_idx >= 0 else None
        dia      = _dia_semana(sale_str) if sale_str else ""
        values.append(dia)

        batch.append(values)
        if len(batch) >= 5000:
            conn.executemany(f"INSERT INTO ventas VALUES ({placeholders})", batch)
            batch.clear()

    if batch:
        conn.executemany(f"INSERT INTO ventas VALUES ({placeholders})", batch)

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    by_franchise = conn.execute(
        f'SELECT "{fc_col}", COUNT(*) FROM ventas GROUP BY "{fc_col}"'
    ).fetchall()
    conn.close()

    print(f"\n{total} filas escritas en '{OUTPUT_PATH}'.")
    for code, count in by_franchise:
        label = labels.get(code, (code or "")[:12] + "...") if code else "sin asignar"
        print(f"  - {label}: {count} filas")
    print(f"\nListo: {os.path.abspath(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
