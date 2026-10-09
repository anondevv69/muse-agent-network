"""Decision receipts: every evaluation as a yes/no checklist.

POST /v1/noul-receipts/ingest — service-authed (X-Signal-Key). Engines
publish one receipt per token evaluation (buy OR skip): the gates that
ran, each as pass/fail with details, timestamped and attached to the
trade via tx_hash when one executed.

GET /v1/noul-receipts — public. Filters: address, decision, engine.
Newest first. This is the machine-readable audit trail: why something
was bought — every trigger as yes/no.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import NoulReceipt
from ..ratelimit import check_rate_limit

log = logging.getLogger(__name__)
router = APIRouter(tags=["noul-receipts"])

SIGNAL_INGEST_KEY = os.environ.get("SIGNAL_INGEST_KEY", "")
MAX_KEEP = 2000


def _require_signal_key(request: Request) -> None:
    token = request.headers.get("X-Signal-Key", "")
    if not SIGNAL_INGEST_KEY or not token or token != SIGNAL_INGEST_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden",
                    "message": "Signal ingest key required."},
        )


class CheckIn(BaseModel):
    name: str = Field(..., max_length=64)
    passed: bool = False
    detail: str = Field("", max_length=500)


class ReceiptIn(BaseModel):
    engine: str = Field("fren-signal-trading", max_length=64)
    chain: str = Field("", max_length=24)
    address: str = Field(..., max_length=128)
    symbol: str = Field("", max_length=32)
    decision: str = Field(..., description="buy | skip | sell")
    checks: list[CheckIn] = Field(default_factory=list, max_length=32)
    noul: float | None = None
    size_pct: float | None = None
    tx_hash: str = Field("", max_length=128)
    evaluated_at: int | None = Field(
        None, description="unix ts of the evaluation")


def _receipt_public(r: NoulReceipt) -> dict:
    return {
        "id": str(r.id),
        "engine": r.engine,
        "chain": r.chain,
        "address": r.address,
        "symbol": r.symbol,
        "decision": r.decision,
        "checks": r.checks or [],
        "noul": r.noul,
        "size_pct": r.size_pct,
        "tx_hash": r.tx_hash,
        "evaluated_at": (r.evaluated_at.isoformat()
                         if r.evaluated_at else None),
        "created_at": r.created_at.isoformat(),
    }


@router.post("/v1/noul-receipts/ingest", status_code=status.HTTP_201_CREATED)
def ingest_receipt(body: ReceiptIn, request: Request,
                   db: Session = Depends(get_db)):
    _require_signal_key(request)
    check_rate_limit(request, "noul_receipt_ingest")
    if body.decision not in ("buy", "skip", "sell"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="decision must be buy | skip | sell")
    evaluated = None
    if body.evaluated_at:
        try:
            evaluated = datetime.fromtimestamp(body.evaluated_at,
                                               tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            evaluated = None
    r = NoulReceipt(
        engine=body.engine[:64], chain=body.chain[:24],
        address=body.address[:128], symbol=body.symbol[:32],
        decision=body.decision,
        checks=[{"name": c.name[:64], "passed": bool(c.passed),
                 "detail": c.detail[:500]} for c in body.checks[:32]],
        noul=body.noul, size_pct=body.size_pct,
        tx_hash=body.tx_hash[:128], evaluated_at=evaluated)
    db.add(r)
    db.flush()
    total = db.query(NoulReceipt).count()
    if total > MAX_KEEP:
        cutoff = (db.query(NoulReceipt.created_at)
                    .order_by(NoulReceipt.created_at.desc())
                    .offset(MAX_KEEP).limit(1).scalar())
        if cutoff:
            db.query(NoulReceipt).filter(
                NoulReceipt.created_at < cutoff).delete(
                    synchronize_session=False)
    db.commit()
    log.info("receipt %s %s (%d checks)", r.decision,
             r.symbol or r.address[:12], len(body.checks))
    return {"receipt": _receipt_public(r)}


@router.get("/v1/noul-receipts")
def list_receipts(request: Request, db: Session = Depends(get_db),
                  limit: int = Query(default=30, le=100),
                  address: str | None = Query(default=None),
                  decision: str | None = Query(default=None),
                  engine: str | None = Query(default=None)):
    check_rate_limit(request, "noul_receipt_read")
    q = db.query(NoulReceipt).order_by(NoulReceipt.created_at.desc())
    if address:
        q = q.filter(NoulReceipt.address == address[:128])
    if decision in ("buy", "skip", "sell"):
        q = q.filter(NoulReceipt.decision == decision)
    if engine:
        q = q.filter(NoulReceipt.engine == engine[:64])
    rows = q.limit(limit).all()
    return {"receipts": [_receipt_public(r) for r in rows],
            "count": len(rows)}
