"""
Genera ui_test/Nacho.ico con los colores del logo Smart-IA.
Usa solo Pillow — sin dependencias de Cairo ni librerías nativas.

Uso:
    python make_icon.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ICO_PATH = ROOT / "ui_test" / "Nacho.ico"

SIZES = [16, 24, 32, 48, 64, 128, 256]

BG_COLOR = (17, 2, 54)       # #110236  fondo oscuro del logo
ACCENT    = (174, 1, 253)     # #ae01fd  púrpura del logo
TEXT_CLR  = (255, 255, 255)   # blanco


def _make_frame(size: int):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fondo redondeado
    radius = max(2, size // 6)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG_COLOR)

    # Borde púrpura fino (solo para tamaños >= 32)
    if size >= 32:
        border = max(1, size // 32)
        draw.rounded_rectangle(
            [border, border, size - 1 - border, size - 1 - border],
            radius=max(1, radius - border),
            outline=ACCENT,
            width=border,
        )

    # Texto "IA"
    font_size = max(6, int(size * 0.45))
    font = None
    try:
        # Intentar cargarlo desde Windows
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    text = "IA"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]

    # Sombra sutil en tamaños grandes
    if size >= 48:
        draw.text((tx + 1, ty + 1), text, font=font, fill=(0, 0, 0, 120))

    draw.text((tx, ty), text, font=font, fill=ACCENT)

    return img


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        sys.exit("Falta Pillow. Instalá con:\n    pip install Pillow")

    frames = [_make_frame(s) for s in SIZES]

    largest = frames[-1]
    largest.save(
        ICO_PATH,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=frames[:-1],
    )
    print(f"Ícono generado: {ICO_PATH}  ({len(SIZES)} resoluciones)")


if __name__ == "__main__":
    main()
