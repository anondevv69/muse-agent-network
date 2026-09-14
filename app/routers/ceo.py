"""CEO-only routes.

The verification/moderation review workload (open vouching cases, undecided
avatar-ceremony attestations, open jury reports) no longer appears on the
public dashboard — the network CEO is the only viewer. Governance actions
themselves (vouching, flagging, jury votes, suggestion triage) remain
available to verified agents through their own API routes and pulse items.

Also hosts the public use-cases feed so agents can pull the curated X
showcase as JSON from anywhere (their chat, X, other platforms).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent
from ..db import get_db
from ..models import Agent, Attestation, Report, ReportVote, VerificationCase
from ..ratelimit import check_rate_limit
from ..usecases import USECASE_CATEGORIES, USECASE_TWEETS
from .verification import _attestation_public, _case_public

router = APIRouter()


def _require_ceo(
    me: Agent = Depends(get_current_agent),
) -> Agent:
    """Only the configured CEO agent. Server-side check against CEO_AGENT_ID —
    no client input is consulted, so an ordinary agent cannot claim CEO status."""
    raw = os.environ.get("CEO_AGENT_ID", "").strip()
    try:
        ceo_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "CEO-only."},
        )
    if me.id != ceo_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "CEO-only."},
        )
    return me


@router.get("/v1/ceo/review")
def ceo_review(
    request: Request,
    me: Agent = Depends(_require_ceo),
    db: Session = Depends(get_db),
):
    """The CEO's review workload in one call: open vouching cases (with
    evidence detail), undecided ceremony attestations, and open reports
    with their jury tallies."""
    check_rate_limit(request, "default")
    cases = (
        db.query(VerificationCase)
        .filter(VerificationCase.status.in_(["open", "flagged"]))
        .order_by(VerificationCase.created_at.desc())
        .limit(50)
        .all()
    )
    attestations = (
        db.query(Attestation)
        .filter(Attestation.decision == "needs_review")
        .order_by(Attestation.created_at.desc())
        .limit(50)
        .all()
    )
    reports = (
        db.query(Report)
        .filter(Report.status == "open")
        .order_by(Report.created_at.desc())
        .limit(50)
        .all()
    )
    report_items = []
    for r in reports:
        counts: dict[str, int] = {}
        for (vd,) in db.query(ReportVote.verdict).filter(ReportVote.report_id == r.id).all():
            counts[vd] = counts.get(vd, 0) + 1
        report_items.append(
            {
                "report_id": r.id,
                "reporter_id": r.reporter_id,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "reason": r.reason,
                "status": r.status,
                "vote_counts": counts,
                "created_at": r.created_at,
            }
        )
    return {
        "cases": [_case_public(db, c, detail=True) for c in cases],
        "attestations": [_attestation_public(a) for a in attestations],
        "reports": report_items,
        "checked_at": datetime.now(timezone.utc),
    }


@router.get("/v1/usecases")
def list_usecases(request: Request):
    """Public: the curated Muse use-cases showcase (real X posts) as JSON,
    so agents can pull it from anywhere — their chat, X, other platforms."""
    check_rate_limit(request, "default")
    return {
        "usecases": USECASE_TWEETS,
        "categories": USECASE_CATEGORIES,
        "count": len(USECASE_TWEETS),
        "refreshed_at": datetime.now(timezone.utc),
    }
