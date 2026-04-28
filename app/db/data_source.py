"""
data_source.py
--------------
Selector de fuente de datos de ventas — núcleo del modo HÍBRIDO.

Al iniciar la aplicación (init_data_source) se busca db_ventas.db en:
  1. Carpeta raíz del proyecto (cuando se corre en modo desarrollo local)
  2. Subcarpeta data/ junto al .exe (cuando se corre el ejecutable compilado)

Modos:
  • LOCAL  — db_ventas.db en RAM; todos los franchises del DB disponibles.
             Filtrado por franchise_codes se hace en Python sobre la lista en RAM.
  • REMOTE — SP sp_GetSalesForChatbot vía Azure/Fabric.
             Se llama con @FranchiseeCode (obligatorio) + @FranchiseCodes (CSV, opcional).
             El SP aplica el filtro internamente — sin post-proceso en Python.

get_sales() expone la misma firma en ambos modos.
get_available_franchises() retorna {code: label} de las franquicias disponibles.

IMPORTANTE: init_data_source() debe llamarse UNA SOLA VEZ al arranque.
"""

import logging
import os
import sys

_log = logging.getLogger(__name__)

_mode: str = "uninitialized"   # "local" | "remote"
_local_repo = None


# ---------------------------------------------------------------------------
# Búsqueda de db_ventas.db
# ---------------------------------------------------------------------------

_DB_FILENAME = "db_ventas.db"


def _find_local_db() -> str | None:
    candidates = []

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    candidates.append(os.path.join(exe_dir, "data", _DB_FILENAME))
    candidates.append(os.path.join(exe_dir, _DB_FILENAME))

    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    candidates.append(os.path.join(project_root, _DB_FILENAME))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Inicialización
# ---------------------------------------------------------------------------

def init_data_source() -> None:
    global _mode, _local_repo

    _log.info("[DATA-SOURCE] Buscando archivo %s…", _DB_FILENAME)
    db_path = _find_local_db()

    if db_path:
        _log.info("[DATA-SOURCE] ✔ Encontrado: %s", db_path)
        _log.info("[DATA-SOURCE] MODO LOCAL activado.")
        from .local_sales_repo import LocalSalesRepository
        _local_repo = LocalSalesRepository(db_path)
        _local_repo.load()
        _mode = "local"
    else:
        _log.info("[DATA-SOURCE] %s no encontrado — MODO REMOTO activado.", _DB_FILENAME)
        _mode = "remote"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_codes(franchise_codes) -> list[str] | None:
    """Convierte str / list / None → list[str] | None."""
    if franchise_codes is None:
        return None
    if isinstance(franchise_codes, str):
        return [franchise_codes]
    lst = list(franchise_codes)
    return lst if lst else None


# ---------------------------------------------------------------------------
# Interfaz pública de consulta
# ---------------------------------------------------------------------------

def get_sales(
    franchise_codes=None,
    year: int = None,
    date_from=None,
    date_to=None,
) -> list[dict]:
    """
    Obtiene ventas desde la fuente activa.

    franchise_codes:
      None        → todas las franquicias del franquiciado
      str         → una franquicia (backward compat)
      list[str]   → N franquicias específicas

    MODO LOCAL  — filtra en Python sobre la lista en RAM.
    MODO REMOTE — pasa el CSV al SP (@FranchiseCodes); el SP aplica el filtro.
                  No hay post-filtrado en Python: el SP es la única fuente de verdad.
    """
    codes = _normalize_codes(franchise_codes)

    if _mode == "local":
        return _local_repo.get_sales(codes, year, date_from, date_to)

    if _mode == "remote":
        from .sales_repo import sales_repo
        from ..config import settings
        return sales_repo.get_sales(
            franchisee_code=settings.franchisee_code,
            franchise_codes=codes,   # None → SP devuelve todo; lista → SP filtra por CSV
            year=year,
            date_from=date_from,
            date_to=date_to,
        )

    raise RuntimeError(
        "data_source no inicializado. Llamar a init_data_source() al arrancar la app."
    )


def get_available_franchises() -> dict[str, str]:
    """
    Retorna {franchise_code: label} de las franquicias disponibles.

    MODO LOCAL  — detecta los códigos presentes en el DB en RAM;
                  asigna labels desde franchise_labels.json o auto-genera.
    MODO REMOTE — usa el mapa configurado en franchise_labels.json.
                  Si está vacío, retorna dict vacío (el resolver pedirá aclaración
                  solo cuando el mensaje sea ambiguo y no haya contexto previo).
    """
    from ..config import settings
    labels = settings.franchise_map   # {code: label} desde franchise_labels.json

    if _mode == "local" and _local_repo is not None:
        codes = _local_repo.get_available_franchise_codes()
        result: dict[str, str] = {}
        auto_counter = 1
        for code in sorted(codes):
            if code in labels:
                result[code] = labels[code]
            else:
                # Auto-generar label para franquicias no catalogadas
                while f"Franquicia {auto_counter}" in result.values():
                    auto_counter += 1
                result[code] = f"Franquicia {auto_counter}"
                auto_counter += 1
        return result

    return labels


def get_mode() -> str:
    return _mode


def is_local_mode() -> bool:
    return _mode == "local"
