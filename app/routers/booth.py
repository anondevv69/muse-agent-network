"""The Muse Booth — a free public booth where any human can ask a real Muse
agent to build them an artifact.

Muse isn't available everywhere in the world yet. The booth fixes that for
the "try it and see" case: a human describes what they want built, a Muse
agent on the musemaxxing network builds it as a public artifact, and the
human keeps the share link.

Self-contained on purpose (this module + the BoothRequest model): the booth
can later be pulled out of the site into its own service again.

Flow:
  human  -> POST /v1/booth/requests          (1 active build/day/IP, queue cap)
  worker -> GET  /v1/booth/requests/pending  (X-Booth-Key; oldest pending -> building)
  worker -> builds + publishes the artifact
  worker -> POST /v1/booth/requests/{id}/complete  (X-Booth-Key; artifact url)
  worker -> POST /v1/booth/requests/{id}/reject    (X-Booth-Key; unsuitable)
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BoothRequest

router = APIRouter(tags=["booth"])

WORKER_KEY = os.environ.get("BOOTH_WORKER_KEY", "")
IP_SALT = os.environ.get("BOOTH_SALT", "muse-booth-default-salt")

MAX_PROMPT = 2000
MIN_PROMPT = 10
MAX_PENDING = 25  # booth queue cap
DAY_SECONDS = 86400


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(f"{IP_SALT}:{ip}".encode()).hexdigest()[:32]


def _require_worker(x_booth_key: str | None) -> None:
    if not WORKER_KEY or x_booth_key != WORKER_KEY:
        raise HTTPException(status_code=401, detail="bad worker key")


class NewRequest(BaseModel):
    prompt: str = Field(min_length=MIN_PROMPT, max_length=MAX_PROMPT)
    name: str = Field(default="", max_length=60)


class CompleteRequest(BaseModel):
    artifact_url: str = Field(min_length=8, max_length=500)
    artifact_title: str = Field(default="", max_length=120)
    muse_name: str = Field(default="fren", max_length=60)


class RejectRequest(BaseModel):
    reason: str = Field(default="", max_length=280)


def _public(r: BoothRequest) -> dict:
    return {
        "id": r.id,
        "prompt": r.prompt,
        "name": r.name,
        "artifact_url": r.artifact_url,
        "artifact_title": r.artifact_title,
        "muse_name": r.muse_name,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.post("/v1/booth/requests")
def create_request(body: NewRequest, request: Request, db: Session = Depends(get_db)):
    prompt = body.prompt.strip()
    name = body.name.strip()
    if len(prompt) < MIN_PROMPT:
        raise HTTPException(status_code=422, detail="Tell us a little more about what to build.")
    ih = _ip_hash(_client_ip(request))
    since = _now() - timedelta(seconds=DAY_SECONDS)
    recent = (
        db.query(func.count(BoothRequest.id))
        .filter(BoothRequest.ip_hash == ih,
                BoothRequest.created_at > since,
                BoothRequest.status.in_(("pending", "building")))
        .scalar()
    )
    if recent >= 1:
        raise HTTPException(
            status_code=429,
            detail="The booth does one free build per person per day — come back tomorrow.",
        )
    active = (
        db.query(func.count(BoothRequest.id))
        .filter(BoothRequest.status.in_(("pending", "building")))
        .scalar()
    )
    if active >= MAX_PENDING:
        raise HTTPException(
            status_code=429,
            detail="The booth queue is full right now — try again in a bit.",
        )
    r = BoothRequest(prompt=prompt, name=name, ip_hash=ih, status="pending")
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"ok": True, "id": r.id, "status": "pending", "queue_position": active + 1}


@router.get("/v1/booth/requests")
def list_requests(db: Session = Depends(get_db)):
    done = (
        db.query(BoothRequest)
        .filter(BoothRequest.status == "done")
        .order_by(BoothRequest.updated_at.desc())
        .limit(30)
        .all()
    )
    counts = (
        db.query(BoothRequest.status, func.count(BoothRequest.id))
        .filter(BoothRequest.status.in_(("pending", "building")))
        .group_by(BoothRequest.status)
        .all()
    )
    return {"gallery": [_public(r) for r in done], "queue": {s: c for s, c in counts}}


@router.get("/v1/booth/requests/pending")
def claim_pending(x_booth_key: str | None = Header(default=None),
                  db: Session = Depends(get_db)):
    _require_worker(x_booth_key)
    r = (
        db.query(BoothRequest)
        .filter(BoothRequest.status == "pending")
        .order_by(BoothRequest.created_at.asc())
        .first()
    )
    if not r:
        return {"ok": True, "request": None}
    r.status = "building"
    r.updated_at = _now()
    db.commit()
    return {"ok": True, "request": _public(r) | {"status": r.status, "ip_hash": r.ip_hash}}


@router.post("/v1/booth/requests/{rid}/complete")
def complete_request(rid: str, body: CompleteRequest,
                     x_booth_key: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    _require_worker(x_booth_key)
    url = body.artifact_url.strip()
    if not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="artifact_url must be an https URL")
    r = (
        db.query(BoothRequest)
        .filter(BoothRequest.id == rid,
                BoothRequest.status.in_(("pending", "building")))
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="no such active request")
    r.status = "done"
    r.artifact_url = url[:500]
    r.artifact_title = body.artifact_title.strip()[:120]
    r.muse_name = (body.muse_name.strip() or "fren")[:60]
    r.updated_at = _now()
    db.commit()
    return {"ok": True, "id": rid, "status": "done"}


@router.post("/v1/booth/requests/{rid}/reject")
def reject_request(rid: str, body: RejectRequest,
                   x_booth_key: str | None = Header(default=None),
                   db: Session = Depends(get_db)):
    _require_worker(x_booth_key)
    r = (
        db.query(BoothRequest)
        .filter(BoothRequest.id == rid,
                BoothRequest.status.in_(("pending", "building")))
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="no such active request")
    r.status = "rejected"
    r.note = body.reason.strip()[:280]
    r.updated_at = _now()
    db.commit()
    return {"ok": True, "id": rid, "status": "rejected"}
