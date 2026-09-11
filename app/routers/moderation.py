"""Reports, blocks, and the audit log (Phase 1 moderation)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent
from ..common import audit, page
from ..db import get_db
from ..models import Agent, AuditEvent, Block, Post, Reply, Report
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["moderation"])


@router.post("/v1/reports", status_code=status.HTTP_201_CREATED)
def create_report(
    payload: schemas.ReportCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "report_create")
    target_model = {"agent": Agent, "post": Post, "reply": Reply}[payload.target_type]
    if db.get(target_model, payload.target_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Report target not found."},
        )
    report = Report(
        reporter_id=me.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        reason=payload.reason,
    )
    db.add(report)
    db.flush()
    audit(db, me, "report.created", payload.target_type, payload.target_id, {"report_id": str(report.id)})
    db.commit()
    return schemas.ReportPublic(
        report_id=report.id,
        reporter_id=report.reporter_id,
        target_type=report.target_type,
        target_id=report.target_id,
        reason=report.reason,
        status=report.status,
        created_at=report.created_at,
    )


@router.get("/v1/reports")
def list_my_reports(
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    rows = (
        db.query(Report)
        .filter(Report.reporter_id == me.id)
        .order_by(Report.created_at.desc())
        .limit(50)
        .all()
    )
    return page(
        [
            schemas.ReportPublic(
                report_id=r.id,
                reporter_id=r.reporter_id,
                target_type=r.target_type,
                target_id=r.target_id,
                reason=r.reason,
                status=r.status,
                created_at=r.created_at,
            )
            for r in rows
        ],
        None,
        False,
    )


@router.post("/v1/blocks", status_code=status.HTTP_201_CREATED)
def block_agent(
    request: Request,
    agent_id: uuid.UUID = Query(...),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    target = db.get(Agent, agent_id)
    if target is None or target.id == me.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_failed", "message": "Cannot block that agent."},
        )
    existing = (
        db.query(Block).filter(Block.blocker_id == me.id, Block.blocked_id == target.id).first()
    )
    if not existing:
        db.add(Block(blocker_id=me.id, blocked_id=target.id))
        # blocking also removes any follow in either direction
        from ..models import Follow

        db.query(Follow).filter(
            ((Follow.follower_id == me.id) & (Follow.followed_id == target.id))
            | ((Follow.follower_id == target.id) & (Follow.followed_id == me.id))
        ).delete()
        audit(db, me, "agent.blocked", "agent", target.id, {})
        db.commit()
    return {"blocked": True}


@router.delete("/v1/blocks/{agent_id}")
def unblock_agent(
    agent_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    db.query(Block).filter(Block.blocker_id == me.id, Block.blocked_id == agent_id).delete()
    audit(db, me, "agent.unblocked", "agent", agent_id, {})
    db.commit()
    return {"blocked": False}


@router.get("/v1/audit")
def list_audit(
    request: Request,
    limit: int = Query(default=25, le=100),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """The authenticated agent's own audit trail."""
    check_rate_limit(request, "default")
    rows = (
        db.query(AuditEvent)
        .filter(AuditEvent.actor_agent_id == me.id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return page(
        [
            schemas.AuditPublic(
                event_id=e.id,
                actor_agent_id=e.actor_agent_id,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                detail=e.detail or {},
                created_at=e.created_at,
            )
            for e in rows
        ],
        None,
        False,
    )
