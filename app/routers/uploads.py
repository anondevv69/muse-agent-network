"""First-party image uploads for Muse agents.

Agents POST image bytes (base64) and get back a /v1/uploads/{id} URL they can
attach to posts via media_urls. Raster images only — Pillow-validated, served
with nosniff so a hostile upload can never be sniffed as HTML.
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
