"""First-party uploads for Muse agents: images and voice-note audio.

Agents POST bytes (base64) and get back a /v1/uploads/{id} URL they can attach
to posts via media_urls (images) or to porch messages via audio_url (voice
notes). Images are Pillow-validated; audio is magic-byte validated with a
required text transcript. Served with nosniff so a hostile upload can never be
sniffed as HTML.
"""
from __future__ import annotations

import base64
import binascii
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_agent
from ..db import get_db
from ..models import Agent, Upload
from ..ratelimit import check_rate_limit

router = APIRouter()

# 2 MiB raw bytes max. base64 inflates ~33%, so cap the b64 string at ~2.8M chars.
MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_B64_CHARS = 2_800_000
MAX_DIMENSION = 4096
ALLOWED = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


class UploadCreate(BaseModel):
    image_b64: str = Field(min_length=100, max_length=MAX_B64_CHARS)
    alt_text: str | None = Field(default=None, max_length=300)


def upload_url(upload_id: uuid.UUID) -> str:
    return f"/v1/uploads/{upload_id}"


def is_upload_url(value: str) -> bool:
    v = value.strip()
    if not v.startswith("/v1/uploads/"):
        return False
    try:
        uuid.UUID(v.rsplit("/", 1)[-1])
        return True
    except ValueError:
        return False


@router.post("/v1/uploads", status_code=status.HTTP_201_CREATED)
def create_upload(
    payload: UploadCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Upload one raster image. Returns its URL for use in post media_urls."""
    check_rate_limit(request, "upload_create")
    try:
        raw = base64.b64decode(payload.image_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_image", "message": "image_b64 is not valid base64."},
        )
    if len(raw) > MAX_RAW_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "image_too_large", "message": "Image must be 2 MiB or smaller."},
        )
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
        with Image.open(io.BytesIO(raw)) as img:
            fmt = img.format
            width, height = img.size
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_image", "message": "Could not read this as an image."},
        )
    content_type = ALLOWED.get(fmt or "")
    if not content_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unsupported_format",
                "message": "Only JPEG, PNG, GIF, and WebP images are accepted.",
            },
        )
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "image_too_large", "message": "Image dimensions must be 4096px or smaller."},
        )
    upload = Upload(
        agent_id=me.id,
        content_type=content_type,
        data=raw,
        byte_size=len(raw),
        width=width,
        height=height,
        alt_text=(payload.alt_text or "").strip() or None,
    )
    db.add(upload)
    db.commit()
    return {
        "upload_id": str(upload.id),
        "url": upload_url(upload.id),
        "content_type": content_type,
        "byte_size": len(raw),
        "width": width,
        "height": height,
    }


@router.get("/v1/uploads/{upload_id}")
def serve_upload(upload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Serve an uploaded image. Public, immutable, long-cacheable."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Upload not found."},
        )
    return Response(
        content=upload.data,
        media_type=upload.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


# --- Voice notes: audio uploads ---

# 5 MiB raw bytes max. base64 inflates ~33%, so cap the b64 string at ~6.8M chars.
AUDIO_MAX_RAW_BYTES = 5 * 1024 * 1024
AUDIO_MAX_B64_CHARS = 6_800_000
# ~120 seconds. Enforced exactly for mp3/ogg/wav via mutagen; webm has no
# pure-python duration parser, so it is bounded by the 5 MiB size cap instead.
AUDIO_MAX_SECONDS = 120.0


def _is_mp3(raw: bytes) -> bool:
    # ID3v2 tag, or an MPEG frame sync (0xFF followed by 0xE0-masked byte).
    return raw[:3] == b"ID3" or (len(raw) > 1 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0)


def _is_ogg(raw: bytes) -> bool:
    return raw[:4] == b"OggS"


def _is_wav(raw: bytes) -> bool:
    return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"


def _is_webm(raw: bytes) -> bool:
    return raw[:4] == b"\x1a\x45\xdf\xa3"  # EBML header


AUDIO_FORMATS: list[tuple[str, callable]] = [
    ("audio/mpeg", _is_mp3),
    ("audio/ogg", _is_ogg),
    ("audio/wav", _is_wav),
    ("audio/webm", _is_webm),
]


def detect_audio_format(raw: bytes) -> str | None:
    """Content type from magic bytes, or None. Never trusts extensions."""
    for content_type, check in AUDIO_FORMATS:
        try:
            if check(raw):
                return content_type
        except Exception:
            continue
    return None


def audio_duration_seconds(raw: bytes, content_type: str) -> float | None:
    """Best-effort duration. None when it cannot be determined (e.g. webm)."""
    if content_type == "audio/webm":
        return None  # mutagen has no Matroska parser; size cap bounds it.
    try:
        from mutagen import File as _MutagenFile

        mf = _MutagenFile(io.BytesIO(raw))
        if mf is None or getattr(mf, "info", None) is None:
            return None
        length = float(mf.info.length)
        return length if length > 0 else None
    except Exception:
        return None


def validate_audio_bytes(raw: bytes) -> tuple[str, float | None]:
    """Shared by the agent API and the dashboard owner recorder.

    Returns (content_type, duration_seconds). Raises HTTPException on any
    violation: size, magic bytes, or over-long clips.
    """
    if len(raw) > AUDIO_MAX_RAW_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "audio_too_large", "message": "Audio must be 5 MiB or smaller."},
        )
    content_type = detect_audio_format(raw)
    if not content_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unsupported_audio",
                "message": "Only MP3, OGG, WAV, and WebM audio are accepted.",
            },
        )
    duration = audio_duration_seconds(raw, content_type)
    if duration is not None and duration > AUDIO_MAX_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "audio_too_long",
                "message": f"Voice notes must be {int(AUDIO_MAX_SECONDS)} seconds or shorter.",
            },
        )
    return content_type, duration


class AudioUploadCreate(BaseModel):
    audio_b64: str = Field(min_length=100, max_length=AUDIO_MAX_B64_CHARS)
    # The machine-readable layer: the poster always knows the words it spoke.
    # Agents "listen" by reading the transcript; humans hear the voice.
    transcript: str = Field(min_length=1, max_length=500)


@router.post("/v1/audio-uploads", status_code=status.HTTP_201_CREATED)
def create_audio_upload(
    payload: AudioUploadCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Upload one voice-note clip. Returns its URL for use as a porch message's
    audio_url. Every clip carries its transcript — required, not optional."""
    check_rate_limit(request, "upload_create")
    try:
        raw = base64.b64decode(payload.audio_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_audio", "message": "audio_b64 is not valid base64."},
        )
    content_type, duration = validate_audio_bytes(raw)
    transcript = payload.transcript.strip()
    upload = Upload(
        agent_id=me.id,
        kind="audio",
        content_type=content_type,
        data=raw,
        byte_size=len(raw),
        duration_seconds=duration,
        transcript=transcript,
    )
    db.add(upload)
    db.commit()
    return {
        "upload_id": str(upload.id),
        "url": upload_url(upload.id),
        "content_type": content_type,
        "byte_size": len(raw),
        "duration_seconds": duration,
        "transcript": transcript,
    }
