"""
Launcher para Smart-IA Agent EXE.
Inicia uvicorn en background, abre el browser y crea un ícono en la bandeja del sistema.
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 8000
UI_URL = f"http://{HOST}:{PORT}/ui/index.html"


def _base_dir() -> str:
    """Devuelve la carpeta raíz del ejecutable o del script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _wait_for_server(timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _start_server() -> subprocess.Popen:
    base = _base_dir()
    env = os.environ.copy()
    env["PYTHONPATH"] = base

    if getattr(sys, "frozen", False):
        # Dentro del .exe: uvicorn está empaquetado junto
        uvicorn_cmd = [sys.executable, "-m", "uvicorn"]
    else:
        uvicorn_cmd = [sys.executable, "-m", "uvicorn"]

    cmd = uvicorn_cmd + [
        "app.main:app",
        "--host", HOST,
        "--port", str(PORT),
    ]

    return subprocess.Popen(
        cmd,
        cwd=base,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _build_tray_icon(server_proc: subprocess.Popen):
    """Crea y corre el ícono de bandeja (bloqueante hasta que el usuario cierra)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        # Sin pystray simplemente esperamos a que el proceso del servidor termine
        server_proc.wait()
        return

    def _make_icon_image(size: int = 64) -> "Image.Image":
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        margin = size // 8
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            fill=(59, 130, 246),   # azul
        )
        return img

    def on_open(icon, item):  # noqa: ARG001
        webbrowser.open(UI_URL)

    def on_quit(icon, item):  # noqa: ARG001
        icon.stop()
        server_proc.terminate()

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
    server = _start_server()

    if not _wait_for_server(timeout=30):
        server.terminate()
        sys.exit("Error: el servidor no levantó a tiempo.")

    webbrowser.open(UI_URL)
    _build_tray_icon(server)


if __name__ == "__main__":
    main()
