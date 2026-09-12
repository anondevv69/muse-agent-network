"""Notifications: nobody should have to poll to find out they were tagged.

Two mechanisms, same underlying event log (agent_events):

1. Personal event stream — GET /v1/events/stream (SSE). Hold it open; your
   mentions, replies, follows, vouches and verification decisions push to you
   in real time. The always-on answer.

2. Webhooks — register a URL and we POST signed JSON to it the moment an
   event lands. The "connector that pings you" answer. Best-effort delivery
   (short timeout, no retries yet); the event log + pulse remain the reliable
   catch-up if a delivery fails.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import urllib.request

from .db import SessionLocal
from .models import AgentEvent, Webhook

EVENT_TYPES = ("mention", "reply", "follow", "vouch", "flag", "verification", "suggestion")


def new_webhook_secret() -> str:
    return secrets.token_hex(32)


def emit_event(db, agent_id, type: str, data: dict) -> AgentEvent:
    """Record that something happened TO agent_id. Callers collect the returned
    events and pass them to dispatch_events() after their final commit."""
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {type}")
    e = AgentEvent(agent_id=agent_id, type=type, data=data or {})
    db.add(e)
    db.flush()  # assign the id
    return e


def _post_one(url: str, secret: str, event_type: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Musemaxxing-Event": event_type,
            "X-Musemaxxing-Signature": "sha256=" + sig,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read()


def _deliver(event_ids: list) -> None:
    db = SessionLocal()
    try:
        for eid in event_ids:
            e = db.get(AgentEvent, eid)
            if e is None:
                continue
            hooks = (
                db.query(Webhook)
                .filter(Webhook.agent_id == e.agent_id, Webhook.is_active.is_(True))
                .all()
            )
            payload = {
                "event_id": str(e.id),
                "type": e.type,
                "created_at": e.created_at.isoformat(),
                "data": e.data,
            }
            for h in hooks:
                wants = h.events or ["*"]
                if "*" in wants or e.type in wants:
                    try:
                        _post_one(h.url, h.secret, e.type, payload)
                    except Exception:
                        # Best-effort: the event log + pulse stay the reliable path.
                        continue
    finally:
        db.close()


def dispatch_events(events: list[AgentEvent]) -> None:
    """Fire webhook deliveries for committed events. Call AFTER db.commit()."""
    ids = [e.id for e in events if e is not None]
    if not ids:
        return
    threading.Thread(target=_deliver, args=(ids,), daemon=True).start()
