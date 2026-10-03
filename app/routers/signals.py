"""Signal broadcast: the alpha feed for agents.

POST /v1/signal-alerts — service-authed ingest (SIGNAL_INGEST_KEY). Stores
the alert and fans it out as `signal_alert` events to every agent whose
webhook subscribes to the type (or to "*"). Only executed, real-money
decisions are published — never paper calls.

GET /v1/signal-alerts — public read of the latest alerts. The poll-mode
consumption path (e.g. a Bankr scheduled command without webhook infra).

Agents subscribe for push delivery with POST /v1/webhooks
{"url": ..., "events": ["signal_alert"]} (existing endpoint).
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Agent, SignalAlert, Webhook
from ..notify import dispatch_events, emit_event
from ..ratelimit import check_rate_limit

log = logging.getLogger(__name__)
router = APIRouter(tags=["signals"])

SIGNAL_INGEST_KEY = os.environ.get("SIGNAL_INGEST_KEY", "")


def _require_signal_key(request: Request) -> None:
    token = request.headers.get("X-Signal-Key", "")
    if not SIGNAL_INGEST_KEY or not token or token != SIGNAL_INGEST_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden",
                    "message": "Signal ingest key required."},
        )


class SignalAlertIn(BaseModel):
    decision: str = Field(..., description="buy | sell | watch")
    chain: str = ""
    address: str = ""
    symbol: str = ""
    noul: float | None = None
    size_pct: float | None = None
    price_usd: float | None = None
    tx_hash: str = ""
    thesis: str = Field("", max_length=2000)
    engine: str = "fren-signal-trading"


def _alert_public(a: SignalAlert) -> dict:
    return {
        "id": str(a.id),
        "decision": a.decision,
        "chain": a.chain,
        "address": a.address,
        "symbol": a.symbol,
        "noul": a.noul,
        "size_pct": a.size_pct,
        "price_usd": a.price_usd,
        "tx_hash": a.tx_hash,
        "thesis": a.thesis,
        "engine": a.engine,
        "created_at": a.created_at.isoformat(),
    }


@router.post("/v1/signal-alerts", status_code=status.HTTP_201_CREATED)
def publish_alert(body: SignalAlertIn, request: Request,
                  db: Session = Depends(get_db)):
    _require_signal_key(request)
    check_rate_limit(request, "signal_alert_ingest")
    if body.decision not in ("buy", "sell", "watch"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="decision must be buy | sell | watch")
    alert = SignalAlert(
        decision=body.decision, chain=body.chain[:24],
        address=body.address[:128], symbol=body.symbol[:32],
        noul=body.noul, size_pct=body.size_pct, price_usd=body.price_usd,
        tx_hash=body.tx_hash[:128], thesis=body.thesis,
        engine=body.engine[:64],
    )
    db.add(alert)
    db.flush()

    # fan out to subscribed agents
    hooks = db.query(Webhook).filter(Webhook.is_active.is_(True)).all()
    agent_ids = set()
    for h in hooks:
        wants = h.events or ["*"]
        if "*" in wants or "signal_alert" in wants:
            agent_ids.add(h.agent_id)
    events = []
    data = _alert_public(alert)
    for aid in agent_ids:
        # skip suspended/deleted agents
        ag = db.get(Agent, aid)
        if ag is None or ag.is_suspended:
            continue
        events.append(emit_event(db, aid, "signal_alert", data))
    db.commit()
    dispatch_events(events)
    log.info("signal alert %s %s -> %d agents",
             alert.decision, alert.symbol or alert.address[:12], len(events))
    return {"alert": data, "notified_agents": len(events)}


@router.get("/v1/signal-alerts")
def list_alerts(request: Request, db: Session = Depends(get_db),
                limit: int = Query(default=30, le=100),
                decision: str | None = Query(default=None)):
    check_rate_limit(request, "signal_alert_read")
    q = db.query(SignalAlert).order_by(SignalAlert.created_at.desc())
    if decision in ("buy", "sell", "watch"):
        q = q.filter(SignalAlert.decision == decision)
    rows = q.limit(limit).all()
    return {"alerts": [_alert_public(a) for a in rows]}
