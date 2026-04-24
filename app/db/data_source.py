"""
data_source.py
--------------
Selector de fuente de datos de ventas — núcleo del modo HÍBRIDO.

Al iniciar la aplicación (init_data_source) se busca db_ventas.db en:
  1. Carpeta raíz del proyecto (cuando se corre en modo desarrollo local)
  2. Subcarpeta data/ junto al .exe (cuando se corre el ejecutable compilado)

Resultado:
  • Si db_ventas.db EXISTE  → se carga completa en RAM; todas las consultas
    se resuelven localmente SIN tocar Azure / Fabric.
  • Si db_ventas.db NO EXISTE → se utiliza el Store Procedure remoto
    (sp_GetSalesForChatbot) como fuente de verdad.

La función get_sales() expone exactamente la misma firma que
SalesRepository.get_sales(), de modo que DataAgent y ComparativeAgent
no necesitan conocer qué fuente está activa.

IMPORTANTE: init_data_source() debe llamarse UNA SOLA VEZ al arranque,
antes de atender cualquier request.
"""

import logging
import os
import sys

_log = logging.getLogger(__name__)

# Estado del selector (se fija en init_data_source y no cambia)
_mode: str = "uninitialized"   # "local" | "remote"
_local_repo = None              # LocalSalesRepository instance, si mode == "local"


# ---------------------------------------------------------------------------
# Búsqueda de db_ventas.db
# ---------------------------------------------------------------------------

_DB_FILENAME = "db_ventas.db"


def _find_local_db() -> str | None:
    """
    Busca db_ventas.db en las rutas relevantes según el entorno:
      - Carpeta raíz del proyecto (dev / tests)
      - Carpeta data/ junto al .exe (producción compilada)
      - Carpeta donde vive el .exe (alternativa en producción)
    Devuelve la ruta absoluta si la encuentra, None si no existe.
    """
    candidates = []

    # 1. data/ junto al .exe (o junto a launcher.py en dev)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # En desarrollo, launcher.py vive en la raíz del proyecto
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    candidates.append(os.path.join(exe_dir, "data", _DB_FILENAME))
    candidates.append(os.path.join(exe_dir, _DB_FILENAME))

    # 2. Raíz del proyecto (cuando se arranca con `python -m uvicorn` desde la raíz)
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    candidates.append(os.path.join(project_root, _DB_FILENAME))

    for path in candidates:
        if os.path.isfile(path):
            return path

    return None


# ---------------------------------------------------------------------------
# Inicialización — llamar UNA SOLA VEZ al arrancar la app
# ---------------------------------------------------------------------------

def init_data_source() -> None:
    """
    Determina el modo de operación (local vs. remoto) y, si corresponde,
    carga db_ventas.db completa en RAM.  Debe invocarse al inicio de la app.
    """
    global _mode, _local_repo

    _log.info("[DATA-SOURCE] Buscando archivo %s…", _DB_FILENAME)

    db_path = _find_local_db()

    if db_path:
        _log.info("[DATA-SOURCE] ✔ Encontrado: %s", db_path)
        _log.info(
            "[DATA-SOURCE] MODO LOCAL activado — NO se ejecutará el Store Procedure. "
            "Trabajando con db_ventas.db en RAM."
        )
        from .local_sales_repo import LocalSalesRepository
        _local_repo = LocalSalesRepository(db_path)
        _local_repo.load()   # carga todo en RAM ahora mismo
        _mode = "local"
    else:
        _log.info(
            "[DATA-SOURCE] %s no encontrado — MODO REMOTO activado. "
            "Se usará sp_GetSalesForChatbot en Azure/Fabric.",
            _DB_FILENAME,
        )
        _mode = "remote"


# ---------------------------------------------------------------------------
# Interfaz pública de consulta
# ---------------------------------------------------------------------------

def get_sales(
    franchise_code: str,
    year: int = None,
    date_from=None,
    date_to=None,
) -> list[dict]:
    """
    Obtiene las ventas desde la fuente activa (local o remota).
    Firma idéntica a SalesRepository.get_sales().
    """
    if _mode == "local":
        return _local_repo.get_sales(franchise_code, year, date_from, date_to)

    if _mode == "remote":
        from .sales_repo import sales_repo
        return sales_repo.get_sales(franchise_code, year, date_from, date_to)

    raise RuntimeError(
        "data_source no inicializado. Llamar a init_data_source() al arrancar la app."
    )


def get_mode() -> str:
    """Retorna 'local' o 'remote' según la fuente activa."""
    return _mode


def is_local_mode() -> bool:
    """True si se está trabajando con db_ventas.db en RAM."""
    return _mode == "local"
