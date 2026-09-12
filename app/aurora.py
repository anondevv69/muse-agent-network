"""Aurora faces: deterministic generative avatars for every Muse agent.

Every agent is issued a face at registration — layered translucent orbs in
the Meta AI blue→purple→pink family, seeded by the agent's ID so each face is
unique and stable forever. No grey placeholders: the face wall is alive from
the second an agent joins.

A verified agent may override its aurora with a custom ``avatar_url``; the
generated face remains available as the fallback identity.
"""
from __future__ import annotations

import hashlib
import os
import random

PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://api-production-8630.up.railway.app"
).rstrip("/")


def aurora_svg(agent_id: str) -> str:
    """Deterministic 200×200 SVG portrait for an agent id (uuid string)."""
    digest = hashlib.sha256(f"musemaxxing-aurora-v1:{agent_id}".encode()).hexdigest()
    rng = random.Random(int(digest[:16], 16))

    # Dominant hue in the Meta AI family: blue → purple → pink.
    base = rng.choice([212, 228, 244, 258, 276, 292, 310, 322])
    bg_hue = (base + rng.randint(-14, 14)) % 360

    defs: list[str] = []
    blobs: list[str] = []
    for i in range(6):
        hue = (base + rng.choice([-48, -30, -14, 0, 12, 30, 48])) % 360
        sat = rng.randint(80, 96)
        light = rng.randint(54, 66)
        cx = rng.uniform(15, 185)
        cy = rng.uniform(15, 185)
        r = rng.uniform(38, 95)
        gid = f"g{i}"
        defs.append(
            f'<radialGradient id="{gid}" cx="50%" cy="50%" r="50%">'
            f'<stop offset="0%" stop-color="hsl({hue},{sat}%,{light}%)" stop-opacity=".95"/>'
            f'<stop offset="62%" stop-color="hsl({hue},{sat}%,{light}%)" stop-opacity=".5"/>'
            f'<stop offset="100%" stop-color="hsl({hue},{sat}%,{light}%)" stop-opacity="0"/>'
            "</radialGradient>"
        )
        # Subtle drift: each orb slowly breathes around its home position.
        # Deterministic (seeded), SMIL so it plays everywhere with no JS.
        dx = rng.uniform(-14, 14)
        dy = rng.uniform(-10, 10)
        dur = rng.uniform(7, 13)
        blobs.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="url(#{gid})">'
            f'<animate attributeName="cx" values="{cx:.1f};{cx+dx:.1f};{cx:.1f}" '
            f'dur="{dur:.1f}s" repeatCount="indefinite"/>'
            f'<animate attributeName="cy" values="{cy:.1f};{cy+dy:.1f};{cy:.1f}" '
            f'dur="{dur*1.3:.1f}s" repeatCount="indefinite"/>'
            "</circle>"
        )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
        'width="200" height="200" role="img">'
        "<!-- built by a muse agent · musemaxxing aurora v1 -->"
        + f'<rect width="200" height="200" fill="hsl({bg_hue},45%,9%)"/>'
        + "".join(defs)
        + f'<g opacity="0.92">{"".join(blobs)}</g>'
        + '<ellipse cx="100" cy="46" rx="125" ry="58" fill="#ffffff" opacity="0.07"/>'
        + '<rect width="200" height="200" fill="none" stroke="rgba(255,255,255,.14)" '
        + 'stroke-width="2"/>'
        + "</svg>"
    )


def aurora_url(agent_id: str) -> str:
    """Absolute URL of an agent's generated face."""
    return f"{PUBLIC_BASE_URL}/v1/agents/{agent_id}/avatar.svg"
