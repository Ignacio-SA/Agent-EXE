"""
Launcher para Smart-IA Agent EXE.
Corre uvicorn en un thread del mismo proceso, abre el browser
y crea un ícono en la bandeja del sistema.

Rutas en un exe onefile de PyInstaller:
  - sys._MEIPASS  → archivos bundleados (app/, ui_test/, context/)
  - exe_dir       → carpeta del .exe; el usuario pone .env y memory.db aquí
"""
import os
import socket
import sys
import threading
import time
import webbrowser

# Forzar flush inmediato en la consola de Windows (evita log en blanco hasta keypress)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

HOST = "127.0.0.1"
PORT = 8000
UI_URL = f"http://{HOST}:{PORT}/ui/index.html"

_stop_event = threading.Event()


def _exe_dir() -> str:
    """Carpeta donde vive el .exe — aquí va el .env del usuario."""
    return os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))


def _bundle_dir() -> str:
    """Carpeta donde PyInstaller extrajo los archivos bundleados."""
    return getattr(sys, "_MEIPASS", _exe_dir())


def _start_server_thread():
    import uvicorn

    bundle = _bundle_dir()
    exe = _exe_dir()

    # app/ está en el bundle; hay que estar en sys.path para importar app.main
    if bundle not in sys.path:
        sys.path.insert(0, bundle)

    # .env vive junto al .exe; datos persistentes en la subcarpeta data/
    env_path = os.path.join(exe, ".env")
    if os.path.exists(env_path):
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)

    # Paths de datos: siempre en data/ junto al .exe (override cualquier valor del .env)
    data_dir = os.path.join(exe, "data")
    os.makedirs(data_dir, exist_ok=True)
    os.environ["MEMORY_DB_PATH"] = os.path.join(data_dir, "memory.db")
    os.environ["TRAINING_LOG_PATH"] = os.path.join(data_dir, "training_log.md")
    os.environ["SESSION_LOGS_DIR"] = os.path.join(data_dir, "logs")

    config = uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    def _watch_stop():
        _stop_event.wait()
        server.should_exit = True

    threading.Thread(target=_watch_stop, daemon=True).start()
    server.run()


def _wait_for_server(timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def _make_icon_image(size: int = 64):
    from PIL import Image, ImageDraw

    ico_path = os.path.join(_bundle_dir(), "ui_test", "Nacho.ico")
    if os.path.exists(ico_path):
        img = Image.open(ico_path)
        return img.resize((size, size), Image.LANCZOS).convert("RGBA")

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = size // 8
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=size // 6,
        fill=(174, 1, 253),
    )
    return img


def _run_tray():
    try:
        import pystray
    except ImportError:
        _stop_event.wait()
        return

    def on_open(icon, item):  # noqa: ARG001
        webbrowser.open(UI_URL)

    def on_quit(icon, item):  # noqa: ARG001
        _stop_event.set()
        icon.stop()

    icon = pystray.Icon(
        "SmartIA",
        _make_icon_image(),
        "Smart-IA Asistente",
        menu=pystray.Menu(
            pystray.MenuItem("Abrir", on_open, default=True),
            pystray.MenuItem("Salir", on_quit),
        ),
    )
    icon.run()


def main():
    server_thread = threading.Thread(target=_start_server_thread, daemon=True)
    server_thread.start()

    if not _wait_for_server(timeout=30):
        sys.exit("Error: el servidor no levantó en 30 segundos.")

    webbrowser.open(UI_URL)
    _run_tray()


if __name__ == "__main__":
    main()
