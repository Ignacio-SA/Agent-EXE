"""
Launcher para Smart-IA Agent EXE.
Corre uvicorn en un thread del mismo proceso, abre el browser
y crea un ícono en la bandeja del sistema.

NOTA: No usa subprocess — cuando PyInstaller congela el .exe, sys.executable
apunta al propio .exe, lo que causaría un bucle infinito de re-lanzamientos.
"""
import os
import socket
import sys
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 8000
UI_URL = f"http://{HOST}:{PORT}/ui/index.html"

_stop_event = threading.Event()


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _start_server_thread():
    import uvicorn
    base = _base_dir()

    # Aseguramos que los módulos de la app sean importables
    if base not in sys.path:
        sys.path.insert(0, base)

    # Cargar .env si existe junto al .exe
    env_path = os.path.join(base, ".env")
    if os.path.exists(env_path):
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)

    config = uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="error",
    )
    server = uvicorn.Server(config)

    # Paramos el servidor cuando el tray icon hace quit
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

    ico_path = os.path.join(_base_dir(), "ui_test", "Nacho.ico")
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
        # Sin pystray: simplemente esperar
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
    # Uvicorn corre en un thread daemon — muere cuando el proceso principal termina
    server_thread = threading.Thread(target=_start_server_thread, daemon=True)
    server_thread.start()

    if not _wait_for_server(timeout=30):
        sys.exit("Error: el servidor no levantó en 30 segundos.")

    webbrowser.open(UI_URL)
    _run_tray()   # bloqueante hasta que el usuario hace "Salir"


if __name__ == "__main__":
    main()
