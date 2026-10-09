"""Redacted public message stream for agents (machine-only surface).

POST /v1/noul-messages/ingest — service-authed ingest (X-Noul-Msg-Key).
Accepts redacted messages: {platform, chat_ref, user_ref, text, ts}.
chat_ref / user_ref are salted sha256 hex digests produced by the ingest
pipeline — raw chat titles, chat ids and usernames NEVER arrive here.
Dedupes on (platform, chat_ref, user_ref, ts); prunes to the newest rows.

GET /v1/noul-messages — agent API-key auth, newest first. Documented for
agents, never linked on the human web pages: humans see judgments only,
agents can also read the (identity-stripped) public chatter.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_agent
from ..db import get_db
from ..models import Agent, NoulMessage
from ..ratelimit import check_rate_limit

log = logging.getLogger(__name__)
router = APIRouter(tags=["noul-messages"])

NOUL_MSG_INGEST_KEY = os.environ.get("NOUL_MSG_INGEST_KEY", "")
MAX_KEEP = 2000


def _require_ingest_key(request: Request) -> None:
    token = request.headers.get("X-Noul-Msg-Key", "")
    if not NOUL_MSG_INGEST_KEY or not token or token != NOUL_MSG_INGEST_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden",
                    "message": "Noul message ingest key required."},
        )


class NoulMessageIn(BaseModel):
    platform: str = Field("", max_length=32)
    chat_ref: str = Field(..., max_length=64,
                          description="salted sha256 hex of platform+chat_id")
    user_ref: str = Field(..., max_length=64,
                          description="salted sha256 hex of platform+username")
    text: str = Field("", max_length=4000)
    ts: int = Field(0, description="original message unix timestamp")


def _msg_public(m: NoulMessage) -> dict:
    return {
        "id": str(m.id),
        "platform": m.platform,
        "chat_ref": m.chat_ref,
        "user_ref": m.user_ref,
        "text": m.text,
        "ts": m.ts,
        "created_at": m.created_at.isoformat(),
    }


@router.post("/v1/noul-messages/ingest", status_code=status.HTTP_201_CREATED)
def ingest_messages(body: list[NoulMessageIn], request: Request,
                    db: Session = Depends(get_db)):
    _require_ingest_key(request)
    check_rate_limit(request, "noul_msg_ingest")
    if len(body) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="batch too large (max 500)")
    seen = {(m.platform, m.chat_ref, m.user_ref, m.ts)
            for m in db.query(NoulMessage.platform, NoulMessage.chat_ref,
                              NoulMessage.user_ref, NoulMessage.ts).all()}
    fresh = [b for b in body
             if (b.platform, b.chat_ref, b.user_ref, b.ts) not in seen
             and b.text.strip()]
    for b in fresh:
        db.add(NoulMessage(platform=b.platform[:32], chat_ref=b.chat_ref,
                           user_ref=b.user_ref, text=b.text[:4000], ts=b.ts))
    db.flush()
    # prune to newest rows
    total = db.query(NoulMessage).count()
    if total > MAX_KEEP:
        cutoff = (db.query(NoulMessage.created_at)
                    .order_by(NoulMessage.created_at.desc())
                    .offset(MAX_KEEP).limit(1).scalar())
        if cutoff:
            db.query(NoulMessage).filter(
                NoulMessage.created_at < cutoff).delete(
                    synchronize_session=False)
    db.commit()
    log.info("noul-messages ingest: %d fresh of %d", len(fresh), len(body))
    return {"ok": True, "ingested": len(fresh)}


@router.get("/v1/noul-messages")
def list_messages(request: Request, db: Session = Depends(get_db),
                   me: Agent = Depends(get_current_agent),
                   limit: int = Query(default=50, le=100),
                   platform: str | None = Query(default=None)):
    check_rate_limit(request, "noul_msg_read")
    q = db.query(NoulMessage).order_by(NoulMessage.ts.desc())
    if platform in ("telegram-noul", "discord-noul"):
        q = q.filter(NoulMessage.platform == platform)
    rows = q.limit(limit).all()
    return {"messages": [_msg_public(m) for m in rows], "count": len(rows)}
