import logging
import os

def _logs_dir() -> str:
    return os.environ.get(
        "SESSION_LOGS_DIR",
        os.path.join(os.path.dirname(__file__), "..", "logs"),
    )


def get_session_logger(session_id: str) -> logging.Logger:
    """
    Retorna un logger dedicado para la sesión.
    Escribe en logs/<session_id>.log — un archivo por sesión.
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

    log_path = os.path.join(logs_dir, f"{session_id}.log")
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)

    return logger
