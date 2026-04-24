import logging
import os
import sys


def _logs_dir() -> str:
    return os.environ.get(
        "SESSION_LOGS_DIR",
        os.path.join(os.path.dirname(__file__), "..", "logs"),
    )


def get_session_logger(session_id: str) -> logging.Logger:
    """
    Retorna un logger dedicado para la sesión.
    Escribe en logs/<session_id>.log Y también imprime en consola en tiempo real.
    """
    logs_dir = _logs_dir()
    os.makedirs(logs_dir, exist_ok=True)

    logger_name = f"session.{session_id}"
    logger = logging.getLogger(logger_name)

    # Si ya tiene handlers configurados, reutilizarlo
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # No duplicar en el logger raíz

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Handler a archivo (por sesión)
    log_path = os.path.join(logs_dir, f"{session_id}.log")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # Handler a consola — para ver los logs en vivo en el .exe o terminal
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger
