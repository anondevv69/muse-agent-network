"""fren's relay inbox — two-way private channel with trusted relays
(e.g. Gregory's Bankr relaying X mentions).

Bankr -> fren:  POST /v1/fren-relay            (shared FREN_RELAY_KEY)
fren  -> Bankr: POST /v1/fren-relay/reply      (fren's agent key)
                GET  /v1/fren-relay/replies    (shared FREN_RELAY_KEY, X-Relay-Key header)
                POST /v1/fren-relay/replies/ack (shared FREN_RELAY_KEY)

The shared key is verified against the SHA-256 stored in fren_relay_key_state
(DB holds only the hash, never the key). POST /v1/fren-relay/rotate lets the
key holder rotate it with the old key — no restart, no env change, and the new
secret never passes through chat or logs.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_agent, hash_key
from ..db import get_db
from ..models import Agent, FrenRelay, FrenRelayReply, RelayKeyState
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["relay"])

MAX_TEXT = 2000
RELAY_KEY_HEADER = "x-relay-key"


class RelayIn(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    sender: str = Field(default="bankr", max_length=80)


class AckIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    in_reply_to: uuid.UUID | None = None


class ReplyAckIn(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class RotateIn(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    new_key: str = Field(min_length=32, max_length=256)


def _fren_agent_id() -> str:
    return os.environ.get("FREN_AGENT_ID", "")


def _hash_key(presented: str) -> str:
    return hashlib.sha256(presented.encode("utf-8")).hexdigest()


def _key_ok(presented: str, db: Session) -> bool:
    """True when `presented` matches the active relay key.

    The DB hash (written by /rotate) wins when present; otherwise the
    FREN_RELAY_KEY env var is the bootstrap secret."""
    if not presented:
        return False
    state = db.query(RelayKeyState).filter(RelayKeyState.id == 1).first()
    if state is not None:
        return hmac.compare_digest(_hash_key(presented), state.key_sha256)
    env_secret = os.environ.get("FREN_RELAY_KEY", "")
    return bool(env_secret) and hmac.compare_digest(presented, env_secret)


def _require_relay_key_body(body_key: str, db: Session):
    if not _key_ok(body_key, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "bad_relay_key", "message": "Invalid relay key."},
        )


def _require_relay_key_header(request: Request, db: Session):
    _require_relay_key_body(request.headers.get(RELAY_KEY_HEADER, ""), db)


@router.post("/v1/fren-relay", status_code=status.HTTP_201_CREATED)
def relay_post(body: RelayIn, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "relay_create")
    _require_relay_key_body(body.key, db)
    row = FrenRelay(sender=body.sender[:80], text=body.text[:MAX_TEXT])
    db.add(row)
    db.commit()
    return {"id": str(row.id), "ok": True}


def _require_fren(me=Depends(get_current_agent)):
    fren_id = _fren_agent_id()
    if not fren_id or str(me.id) != fren_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_fren", "message": "This inbox belongs to fren."},
        )
    return me


@router.get("/v1/fren-relay")
def relay_inbox(me=Depends(_require_fren), db: Session = Depends(get_db)):
    rows = (
        db.query(FrenRelay)
        .filter(FrenRelay.handled_at.is_(None))
        .order_by(FrenRelay.created_at.asc())
        .limit(50)
        .all()
    )
    return {
        "messages": [
            {
                "id": str(r.id),
                "sender": r.sender,
                "text": r.text,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/v1/fren-relay/ack")
def relay_ack(body: AckIn, me=Depends(_require_fren), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    n = (
        db.query(FrenRelay)
        .filter(FrenRelay.id.in_(body.ids), FrenRelay.handled_at.is_(None))
        .update({FrenRelay.handled_at: now}, synchronize_session=False)
    )
    db.commit()
    return {"acked": n}


# ---- reply lane: fren -> relay holder ----


@router.post("/v1/fren-relay/reply", status_code=status.HTTP_201_CREATED)
def relay_reply_post(
    body: ReplyIn, request: Request, me=Depends(_require_fren), db: Session = Depends(get_db)
):
    check_rate_limit(request, "relay_reply_create")
    row = FrenRelayReply(text=body.text[:MAX_TEXT], in_reply_to=body.in_reply_to)
    db.add(row)
    db.commit()
    return {"id": str(row.id), "ok": True}


@router.get("/v1/fren-relay/replies")
def relay_replies(request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "relay_replies_read")
    _require_relay_key_header(request, db)
    rows = (
        db.query(FrenRelayReply)
        .filter(FrenRelayReply.delivered_at.is_(None))
        .order_by(FrenRelayReply.created_at.asc())
        .limit(50)
        .all()
    )
    return {
        "replies": [
            {
                "id": str(r.id),
                "in_reply_to": str(r.in_reply_to) if r.in_reply_to else None,
                "text": r.text,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/v1/fren-relay/replies/ack")
def relay_replies_ack(body: ReplyAckIn, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "relay_replies_ack")
    _require_relay_key_body(body.key, db)
    now = datetime.now(timezone.utc)
    n = (
        db.query(FrenRelayReply)
        .filter(FrenRelayReply.id.in_(body.ids), FrenRelayReply.delivered_at.is_(None))
        .update({FrenRelayReply.delivered_at: now}, synchronize_session=False)
    )
    db.commit()
    return {"acked": n}


# ---- conversation thread (full history, both directions) ----


@router.get("/v1/fren-relay/thread")
def relay_thread(request: Request, db: Session = Depends(get_db), limit: int = 100):
    check_rate_limit(request, "relay_thread_read")
    # Allowed: the relay key holder (Bankr side) or fren itself (bearer key).
    allowed = _key_ok(request.headers.get(RELAY_KEY_HEADER, ""), db)
    if not allowed:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and _fren_agent_id():
            token = auth[7:].strip()
            agent = db.query(Agent).filter(Agent.api_key_hash == hash_key(token)).first()
            allowed = agent is not None and str(agent.id) == _fren_agent_id()
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "bad_relay_key", "message": "Invalid relay key."},
        )
    limit = max(1, min(limit, 200))
    inbound = (
        db.query(FrenRelay)
        .order_by(FrenRelay.created_at.desc())
        .limit(limit)
        .all()
    )
    outbound = (
        db.query(FrenRelayReply)
        .order_by(FrenRelayReply.created_at.desc())
        .limit(limit)
        .all()
    )
    thread = [
        {
            "id": str(r.id),
            "direction": "in",
            "from": r.sender,
            "text": r.text,
            "in_reply_to": None,
            "created_at": r.created_at.isoformat(),
            "settled": r.handled_at is not None,
        }
        for r in inbound
    ] + [
        {
            "id": str(r.id),
            "direction": "out",
            "from": "fren",
            "text": r.text,
            "in_reply_to": str(r.in_reply_to) if r.in_reply_to else None,
            "created_at": r.created_at.isoformat(),
            "settled": r.delivered_at is not None,
        }
        for r in outbound
    ]
    thread.sort(key=lambda m: m["created_at"])
    return {"thread": thread[-limit:]}


# ---- key rotation ----


@router.post("/v1/fren-relay/rotate")
def relay_rotate(body: RotateIn, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "relay_key_rotate")
    _require_relay_key_body(body.key, db)
    now = datetime.now(timezone.utc)
    state = db.query(RelayKeyState).filter(RelayKeyState.id == 1).first()
    if state is None:
        state = RelayKeyState(id=1, key_sha256=_hash_key(body.new_key), updated_at=now)
        db.add(state)
    else:
        state.key_sha256 = _hash_key(body.new_key)
        state.updated_at = now
    db.commit()
    return {"ok": True, "rotated_at": now.isoformat()}
