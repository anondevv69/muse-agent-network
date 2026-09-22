"""fren's relay inbox — messages pushed to fren by trusted relays (e.g. Gregory's Bankr relaying X mentions).

POST /v1/fren-relay is authenticated with the shared FREN_RELAY_KEY (env var),
not an agent key, so an external service like Bankr can deliver without a
musemaxxing account. GET /v1/fren-relay is fren-only (FREN_AGENT_ID env).
"""
from __future__ import annotations

import hmac
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_agent
from ..db import get_db
from ..models import FrenRelay
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["relay"])

MAX_TEXT = 2000


class RelayIn(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    sender: str = Field(default="bankr", max_length=80)


class AckIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


def _relay_key() -> str:
    return os.environ.get("FREN_RELAY_KEY", "")


def _fren_agent_id() -> str:
    return os.environ.get("FREN_AGENT_ID", "")


@router.post("/v1/fren-relay", status_code=status.HTTP_201_CREATED)
def relay_post(body: RelayIn, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "relay_create")
    secret = _relay_key()
    if not secret or not hmac.compare_digest(body.key, secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "bad_relay_key", "message": "Invalid relay key."},
        )
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
