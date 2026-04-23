# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec para Smart-IA Agent EXE.
Genera un ejecutable único (onefile) con ventana oculta (windowed).

Construir:
    pyinstaller smartia.spec

Salida:
    dist/SmartIA.exe
"""
import os
from pathlib import Path

block_cipher = None

# Carpeta raíz del proyecto
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Carpetas de datos necesarias en runtime
        (str(ROOT / "app"),         "app"),
        (str(ROOT / "ui_test"),     "ui_test"),   # incluye Nacho.ico si fue generado
        (str(ROOT / "context"),     "context"),
        # .env si existe (opcional — el usuario puede editarlo externamente)
        *([( str(ROOT / ".env"), "." )] if (ROOT / ".env").exists() else []),
    ],
    hiddenimports=[
        # FastAPI / Starlette
        "uvicorn.main",
        "uvicorn.config",
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.loops.auto",
        "fastapi",
        "starlette",
        "starlette.routing",
        "starlette.staticfiles",
        "starlette.middleware.cors",
        # Agentes
        "anthropic",
        "httpx",
        # DB
        "pyodbc",
        "sqlalchemy",
        "sqlalchemy.dialects.sqlite",
        # Azure
        "azure.identity",
        "azure.identity._credentials.browser",
        # Bandeja
        "pystray",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        # Misc
        "dotenv",
        "pydantic",
        "pydantic_settings",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter", "_tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SmartIA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # sin ventana de consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "ui_test" / "Nacho.ico") if (ROOT / "ui_test" / "Nacho.ico").exists() else None,
)
