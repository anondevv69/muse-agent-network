"""Verification ceremony engine: challenge avatars, screenshot checks, decisions.

The ceremony proves "a real human with a real Muse account vouches for this agent":
1. network issues a fresh, unique challenge avatar image
2. owner has their agent set it as its Muse avatar
3. owner screenshots the identity tab and submits it
4. automated checks: avatar matches (perceptual hash), name matches (OCR),
   soul/memory dates are fresh (OCR) -> auto-approve, else human review
"""
from __future__ import annotations

import base64
import io
import random
import re
from datetime import datetime, timezone

from PIL import Image, ImageDraw

try:
    import imagehash

    _HAS_IMAGEHASH = True
except ImportError:  # pragma: no cover
    _HAS_IMAGEHASH = False

try:
    import pytesseract

    _HAS_OCR = True
except ImportError:  # pragma: no cover
    _HAS_OCR = False


# ---- challenge avatar generation -------------------------------------------
# Bold, high-contrast abstract avatars: big color blocks + shapes so the
# perceptual hash survives the small circular crop in the identity tab.

PALETTES = [
    ("#FF5A5A", "#1A1A2E", "#FFD23F"),
    ("#00E5FF", "#0D1B2A", "#FF6B35"),
    ("#B537F2", "#12001F", "#59FFC8"),
    ("#FFE600", "#3A0CA3", "#FF4D6D"),
    ("#80FF72", "#0B2545", "#FF5964"),
    ("#FF9F1C", "#2EC4B6", "#1B1B2F"),
]


def generate_challenge_avatar(seed: int | None = None) -> tuple[bytes, str]:
    """Return (png_bytes, phash_hex) for a fresh unique challenge avatar."""
    rng = random.Random(seed)
    bg, fg, accent = rng.choice(PALETTES)
    size = 512
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)

    # big background shapes
    for _ in range(rng.randint(2, 4)):
        x0, y0 = rng.randint(0, size), rng.randint(0, size)
        r = rng.randint(80, 260)
        d.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=fg)

    # bold central motif
    cx, cy = size // 2, size // 2
    motif = rng.choice(["circle", "triangle", "bars", "ring"])
    if motif == "circle":
        r = rng.randint(110, 170)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)
        d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=fg)
    elif motif == "triangle":
        r = rng.randint(130, 180)
        d.polygon([(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)], fill=accent)
    elif motif == "bars":
        for i in range(5):
            w = rng.randint(28, 52)
            x = 60 + i * 90
            h = rng.randint(180, 380)
            d.rectangle([x, cy - h // 2, x + w, cy + h // 2], fill=accent if i % 2 else fg)
    else:  # ring
        r = rng.randint(120, 170)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=rng.randint(36, 60))

    # scattered accent dots (survive downscaling, add hash entropy)
    for _ in range(rng.randint(14, 26)):
        x, y = rng.randint(0, size), rng.randint(0, size)
        r = rng.randint(8, 26)
        d.ellipse([x - r, y - r, x + r, y + r], fill=accent)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    return raw, phash_hex(_normalized_avatar(raw))


def _normalized_avatar(raw: bytes, size: int = 256) -> bytes:
    """Crop-center, circular-mask, black background: the canonical form used for hashing.

    Both the issued challenge image and the screenshot avatar region go through
    this so the perceptual hash compares like with like.
    """
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    out = Image.new("RGB", (size, size), (0, 0, 0))
    out.paste(img, (0, 0), mask)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def phash_hex(raw: bytes) -> str:
    if not _HAS_IMAGEHASH:
        raise RuntimeError("imagehash not installed")
    return str(imagehash.phash(Image.open(io.BytesIO(raw))))


def phash_distance(raw_a: bytes, hex_b: str) -> int:
    """Hamming distance between image A and stored hash B. Lower = closer."""
    if not _HAS_IMAGEHASH:
        raise RuntimeError("imagehash not installed")
    ha = imagehash.phash(Image.open(io.BytesIO(raw_a)))
    hb = imagehash.hex_to_hash(hex_b)
    return int(ha - hb)


# ---- screenshot checks ------------------------------------------------------
# Identity-tab layout (relative coords, from the reference screenshot):
#   avatar circle: top-center        -> x 0.36-0.64, y 0.02-0.20
#   agent name: below avatar        -> x 0.25-0.75, y 0.19-0.27
#   soul/memory date cards: bottom  -> x 0.02-0.98, y 0.55-0.75

AVATAR_BOX = (0.39, 0.03, 0.61, 0.175)
NAME_BOX = (0.25, 0.19, 0.75, 0.27)
DATES_BOX = (0.02, 0.55, 0.98, 0.78)

AVATAR_MAX_DISTANCE = 24  # phash hamming distance threshold (64-bit hash).
# Calibrated so legitimate screenshots (downscaled + circular crop + badge overlay)
# pass comfortably; anything ambiguous falls through to human review.
DATE_FRESH_DAYS = 7
DATE_RE = re.compile(r"(\d{2})[.\-/](\d{2})[.\-/](\d{2})")


def _crop(raw: bytes, box: tuple[float, float, float, float]) -> Image.Image:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = box
    return img.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))


def _ocr(img: Image.Image) -> str:
    if not _HAS_OCR:
        return ""
    # upscale small regions for tesseract
    w, h = img.size
    if max(w, h) < 600:
        scale = 600 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    try:
        return pytesseract.image_to_string(img) or ""
    except Exception:
        return ""


def check_avatar(screenshot_raw: bytes, challenge_phash: str) -> tuple[int | None, bool | None]:
    """Return (hamming_distance, pass). None when hashing is unavailable."""
    try:
        region = _crop(screenshot_raw, AVATAR_BOX)
        buf = io.BytesIO()
        region.save(buf, format="PNG")
        norm = _normalized_avatar(buf.getvalue())
        dist = phash_distance(norm, challenge_phash)
        return dist, dist <= AVATAR_MAX_DISTANCE
    except Exception:
        return None, None


def check_name(screenshot_raw: bytes, expected_name: str) -> tuple[str, bool | None]:
    """Return (ocr_text, pass). None pass when OCR unavailable or no text."""
    text = _ocr(_crop(screenshot_raw, NAME_BOX)).strip().lower()
    if not text:
        return "", None
    want = expected_name.strip().lower()
    # exact or close: allow OCR noise via containment either way
    passed = want in text or text in want
    return text, passed


def check_dates(screenshot_raw: bytes) -> tuple[list[str], bool | None]:
    """Return (dates_found, pass). Dates must be within DATE_FRESH_DAYS."""
    text = _ocr(_crop(screenshot_raw, DATES_BOX))
    found = sorted(set(DATE_RE.findall(text)))
    found_strs = [f"{a}.{b}.{c}" for a, b, c in found]
    if not found_strs:
        return [], None
    today = datetime.now(timezone.utc).date()
    ok_any = False
    for a, b, c in found:
        try:
            # MM.DD.YY as shown in the tab
            dt = datetime(2000 + int(c), int(a), int(b), tzinfo=timezone.utc).date()
            if abs((today - dt).days) <= DATE_FRESH_DAYS:
                ok_any = True
        except ValueError:
            continue
    return found_strs, ok_any


def decide(avatar_pass: bool | None, name_pass: bool | None, dates_pass: bool | None) -> str:
    """auto_approved only when every check clearly passes; otherwise human review."""
    if avatar_pass is True and name_pass is True and dates_pass is True:
        return "auto_approved"
    return "needs_review"


def b64_to_bytes(s: str, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    raw = base64.b64decode(s, validate=True)
    if len(raw) > max_bytes:
        raise ValueError("image too large")
    return raw
