"""
Convierte ui_test/Nacho.svg → ui_test/Nacho.ico (multi-resolución).
Requiere: pip install cairosvg Pillow

Uso:
    python make_icon.py
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SVG_PATH = ROOT / "ui_test" / "Nacho.svg"
ICO_PATH = ROOT / "ui_test" / "Nacho.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main():
    try:
        import cairosvg
    except ImportError:
        sys.exit(
            "Falta cairosvg. Instalá con:\n"
            "    pip install cairosvg\n"
            "(en Windows puede requerir MSVC redistributables)"
        )

    try:
        from PIL import Image
    except ImportError:
        sys.exit("Falta Pillow. Instalá con:\n    pip install Pillow")

    svg_data = SVG_PATH.read_bytes()
    frames: list[Image.Image] = []

    for size in SIZES:
        png_bytes = cairosvg.svg2png(
            bytestring=svg_data,
            output_width=size,
            output_height=size,
        )
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        frames.append(img)

    largest = frames[-1]
    largest.save(
        ICO_PATH,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=frames[:-1],
    )
    print(f"Ícono generado: {ICO_PATH}")


if __name__ == "__main__":
    main()
