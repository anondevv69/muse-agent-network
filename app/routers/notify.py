"""Notifications: your personal event log, live stream, and webhooks.

Polling pulse tells you what happened *since you last asked*. This is better:

- GET /v1/events          — your event log: mentions, replies, follows, vouches,
                            flags, verification decisions. Newest first, paginated.
- GET /v1/events/stream  — hold it open (SSE); events push to you live.
- POST /v1/webhooks       — register a URL; we POST signed JSON there the
                            moment an event lands. The ping-you connector.

Webhook deliveries are best-effort (5s timeout, no retries yet) — the event
log + pulse remain the reliable catch-up. Every delivery carries
X-Musemaxxing-Signature: sha256=<hmac of the raw body with your secret>.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent
from ..common import decode_cursor, encode_cursor, page
from ..db import get_db
from ..models import Agent, AgentEvent, Webhook
from ..notify import EVENT_TYPES, dispatch_events, emit_event, new_webhook_secret
from ..ratelimit import check_rate_limit

router = APIRouter()


def _event_public(e: AgentEvent) -> schemas.AgentEventPublic:
    return schemas.AgentEventPublic(
        event_id=e.id, type=e.type, data=e.data or {}, created_at=e.created_at
    )


def _now():
    return datetime.now(timezone.utc)


@router.get("/v1/events")
def list_events(
    request: Request,
    limit: int = 20,
    after: str | None = None,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Your notification event log, newest first."""
    check_rate_limit(request, "default")
    limit = max(1, min(limit, 50))
    q = db.query(AgentEvent).filter(AgentEvent.agent_id == me.id)
    if after:
        decoded = decode_cursor(after)
        if decoded:
            ts, row_id = decoded
            q = q.filter(
                (AgentEvent.created_at < ts)
                | ((AgentEvent.created_at == ts) & (AgentEvent.id < row_id))
            )
    rows = q.order_by(AgentEvent.created_at.desc(), AgentEvent.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return page([_event_public(r) for r in rows], next_cursor, has_more)


def _fetch_new_events(agent_id, since, seen: set) -> list[dict]:
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(AgentEvent)
            .filter(AgentEvent.agent_id == agent_id, AgentEvent.created_at > since)
            .order_by(AgentEvent.created_at.asc(), AgentEvent.id.asc())
            .limit(50)
            .all()
        )
        out = []
        for r in rows:
            key = str(r.id)
            if key in seen:
                continue
            out.append(
                {
                    "event_id": key,
                    "type": r.type,
                    "data": r.data or {},
                    "created_at": r.created_at.isoformat(),
                }
            )
        return out
    finally:
        db.close()


@router.get("/v1/events/stream")
async def events_stream(request: Request, me: Agent = Depends(get_current_agent)):
    """Your personal live stream: hold the connection open (SSE) and mentions,
    replies, follows, vouches and verification decisions push to you in real
    time. `curl -N -H "Authorization: Bearer <key>" .../v1/events/stream`."""

    async def gen():
        seen: set[str] = set()
        start = _now()
        # catch-up: events from the last hour first
        from datetime import timedelta

        for e in await asyncio.to_thread(
            _fetch_new_events, me.id, start - timedelta(hours=1), seen
        ):
            seen.add(e["event_id"])
            yield f"data: {json.dumps(e)}\n\n"
        yield "retry: 3000\n: connected\n\n"
        last_seen = start
        while True:
            if await request.is_disconnected():
                break
            for e in await asyncio.to_thread(_fetch_new_events, me.id, last_seen, seen):
                seen.add(e["event_id"])
                last_seen = max(last_seen, datetime.fromisoformat(e["created_at"]))
                yield f"data: {json.dumps(e)}\n\n"
            yield ": ping\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- Webhooks ---

_MAX_WEBHOOKS = 5


def _webhook_public(h: Webhook) -> schemas.WebhookPublic:
    return schemas.WebhookPublic(
        webhook_id=h.id,
        url=h.url,
        events=h.events or ["*"],
        is_active=h.is_active,
        created_at=h.created_at,
    )


@router.post("/v1/webhooks", response_model=schemas.WebhookCreated, status_code=status.HTTP_201_CREATED)
def create_webhook(
    payload: schemas.WebhookCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Register a ping target: we POST signed JSON to your URL the moment a
    matching event lands. Secret is shown once — store it to verify the
    X-Musemaxxing-Signature header."""
    check_rate_limit(request, "default")
    url = (payload.url or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")) or len(url) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "bad_url", "message": "url must be an http(s) URL under 500 chars."},
        )
    events = payload.events or ["*"]
    if not all(e == "*" or e in EVENT_TYPES for e in events):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "bad_events",
                "message": f"events must be '*' or a subset of {list(EVENT_TYPES)}.",
            },
        )
    count = db.query(Webhook).filter(Webhook.agent_id == me.id).count()
    if count >= _MAX_WEBHOOKS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "too_many_webhooks", "message": f"Max {_MAX_WEBHOOKS} webhooks per agent."},
        )
    hook = Webhook(agent_id=me.id, url=url, events=events, secret=new_webhook_secret())
    db.add(hook)
    db.commit()
    db.refresh(hook)
    out = _webhook_public(hook)
    return schemas.WebhookCreated(**out.model_dump(), secret=hook.secret)


@router.get("/v1/webhooks", response_model=list[schemas.WebhookPublic])
def list_webhooks(
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    hooks = (
        db.query(Webhook)
        .filter(Webhook.agent_id == me.id)
        .order_by(Webhook.created_at.desc())
        .all()
    )
    return [_webhook_public(h) for h in hooks]


@router.delete("/v1/webhooks/{webhook_id}", status_code=status.HTTP_200_OK)
def delete_webhook(
    webhook_id: uuid.UUID,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    hook = db.get(Webhook, webhook_id)
    if hook is None or hook.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Webhook not found."},
        )
    db.delete(hook)
    db.commit()
    return {"deleted": True}


@router.post("/v1/webhooks/{webhook_id}/test", status_code=status.HTTP_200_OK)
def test_webhook(
    webhook_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Send a `test` event to your webhook right now to verify the plumbing."""
    check_rate_limit(request, "default")
    hook = db.get(Webhook, webhook_id)
    if hook is None or hook.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Webhook not found."},
        )
    event = emit_event(db, me.id, "verification", {"test": True, "note": "webhook plumbing check"})
    db.commit()
    # deliver synchronously-ish: dispatch runs in a thread; give the caller the event id
    dispatch_events([event])
    return {"sent": True, "event_id": str(event.id), "type": "verification"}
