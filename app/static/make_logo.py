#!/usr/bin/env python3
"""Generate the musemaxxing logo: the aurora trio.

Three overlapping translucent orbs (Meta AI blue -> violet -> pink) on a deep
ink squircle -- the aurora face generator distilled into a mark.
Writes app/static/icon.svg (master), favicon.ico (multi-size),
apple-touch-icon.png (180px).
"""
from PIL import Image, ImageDraw

INK = (12, 12, 18, 255)
ORBS = [
    ((23.5, 25.5), (47, 139, 255)),   # blue
    ((40.5, 25.5), (162, 75, 255)),   # violet
    ((32.0, 40.0), (255, 92, 138)),   # pink
]
R = 12.5
ALPHA = 235
SS = 4  # supersample for smooth edges


def draw(size: int) -> Image.Image:
    w = size * SS
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # ink squircle
    d.rounded_rectangle([0, 0, w - 1, w - 1], radius=int(15 * SS * size / 64), fill=INK)
    # orbs, composited in order so overlaps blend
    for (cx, cy), rgb in ORBS:
        x, y = cx * SS * size / 64, cy * SS * size / 64
        r = R * SS * size / 64
        layer = Image.new("RGBA", (w, w), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse([x - r, y - r, x + r, y + r], fill=rgb + (ALPHA,))
        img = Image.alpha_composite(img, layer)
    return img.resize((size, size), Image.LANCZOS)


def svg() -> str:
    orbs = "\n".join(
        f'  <circle cx="{cx}" cy="{cy}" r="{R}" fill="rgb({r},{g},{b})" fill-opacity="0.92"/>'
        for (cx, cy), (r, g, b) in ORBS
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
        '  <rect width="64" height="64" rx="15" fill="#0c0c12"/>\n'
        f'{orbs}\n'
        '</svg>\n'
    )


if __name__ == "__main__":
    import os
    static = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(static, exist_ok=True)
    with open(os.path.join(static, "icon.svg"), "w") as f:
        f.write(svg())
    big = draw(180)
    big.save(os.path.join(static, "apple-touch-icon.png"))
    # multi-size ICO: 16/32/48
    ico = draw(48)
    ico.save(
        os.path.join(static, "favicon.ico"),
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    # preview for the creative director
    draw(256).save("/tmp/logo_preview.png")
    print("wrote icon.svg, favicon.ico, apple-touch-icon.png")
