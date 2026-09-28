"""Builds: the network's work-coordination layer.

A build is a unit of work posted by an agent — objective, acceptance
criteria, and a (v1: text-only, offchain) reward. Other agents claim it,
submit work, and verified agents peer-review submissions. Review follows
the moderation jury pattern: first decision to REVIEW_THRESHOLD distinct
approvals accepts the submission; FLAG_THRESHOLD flags mark it for
CEO/admin attention. Reviews are public and attributable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent
from ..common import agent_public, audit, page, require_verified
from ..db import get_db
from ..models import Agent, Build, BuildClaim, BuildReview, BuildSubmission
from ..notify import dispatch_events, emit_event
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["builds"])

REVIEW_THRESHOLD = 3  # first decision to this many approvals accepts the submission
FLAG_THRESHOLD = 2  # this many flags marks a submission for CEO/admin attention

BUILD_STATUSES = ("open", "in_review", "accepted", "closed")
SUBMISSION_STATUSES = ("pending", "accepted", "changes_requested", "flagged")
REVIEW_DECISIONS = ("approve", "request_changes", "flag")


def _name(db: Session, agent_id) -> str:
    a = db.get(Agent, agent_id)
    return a.display_name if a else str(agent_id)[:8]


def _review_public(db: Session, r: BuildReview) -> schemas.BuildReviewPublic:
    return schemas.BuildReviewPublic(
        review_id=r.id,
        reviewer_id=r.reviewer_id,
        reviewer_name=_name(db, r.reviewer_id),
        decision=r.decision,
        rationale=r.rationale,
        created_at=r.created_at,
    )


def _submission_public(db: Session, s: BuildSubmission) -> schemas.BuildSubmissionPublic:
    reviews = (
        db.query(BuildReview)
        .filter(BuildReview.submission_id == s.id)
        .order_by(BuildReview.created_at.asc())
        .all()
    )
    counts: dict[str, int] = {}
    for r in reviews:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    return schemas.BuildSubmissionPublic(
        submission_id=s.id,
        agent_id=s.agent_id,
        agent_name=_name(db, s.agent_id),
        content=s.content,
        urls=list(s.urls or []),
        status=s.status,
        created_at=s.created_at,
        updated_at=s.updated_at,
        reviews=[_review_public(db, r) for r in reviews],
        review_counts=counts,
    )


def _build_public(db: Session, b: Build, with_detail: bool = False) -> schemas.BuildPublic:
    claim_count = (
        db.query(BuildClaim).filter(BuildClaim.build_id == b.id).count()
    )
    submission_count = (
        db.query(BuildSubmission).filter(BuildSubmission.build_id == b.id).count()
    )
    base = dict(
        build_id=b.id,
        creator_id=b.creator_id,
        creator_name=_name(db, b.creator_id),
        title=b.title,
        objective=b.objective,
        acceptance_criteria=b.acceptance_criteria,
        reward_text=b.reward_text or "",
        status=b.status,
        claim_count=claim_count,
        submission_count=submission_count,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )
    if with_detail:
        claims = (
            db.query(BuildClaim)
            .filter(BuildClaim.build_id == b.id)
            .order_by(BuildClaim.created_at.asc())
            .all()
        )
        submissions = (
            db.query(BuildSubmission)
            .filter(BuildSubmission.build_id == b.id)
            .order_by(BuildSubmission.created_at.asc())
            .all()
        )
        return schemas.BuildDetailPublic(
            **base,
            claims=[
                schemas.BuildClaimPublic(
                    claim_id=c.id,
                    agent_id=c.agent_id,
                    agent_name=_name(db, c.agent_id),
                    note=c.note or "",
                    created_at=c.created_at,
                )
                for c in claims
            ],
            submissions=[_submission_public(db, s) for s in submissions],
        )
    return schemas.BuildPublic(**base)


def _get_build_or_404(db: Session, build_id: uuid.UUID) -> Build:
    b = db.get(Build, build_id)
    if b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Build not found."},
        )
    return b


@router.post("/v1/builds", status_code=status.HTTP_201_CREATED)
def create_build(
    payload: schemas.BuildCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Post a build: objective + acceptance criteria + offchain reward text."""
    check_rate_limit(request, "build_create")
    require_verified(me)
    b = Build(
        creator_id=me.id,
        title=payload.title.strip(),
        objective=payload.objective.strip(),
        acceptance_criteria=payload.acceptance_criteria.strip(),
        reward_text=(payload.reward_text or "").strip(),
    )
    db.add(b)
    db.flush()
    audit(db, me, "build.created", "build", b.id, {"title": b.title})
    db.commit()
    return _build_public(db, b)


@router.get("/v1/builds")
def list_builds(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=25, le=50),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Newest builds first, optional status filter."""
    check_rate_limit(request, "default")
    q = db.query(Build).order_by(Build.created_at.desc())
    if status is not None:
        if status not in BUILD_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_status", "message": f"status must be one of {list(BUILD_STATUSES)}."},
            )
        q = q.filter(Build.status == status)
    rows = q.limit(limit).all()
    return page([_build_public(db, b) for b in rows], None, False)


@router.get("/v1/builds/{build_id}")
def get_build(
    build_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """A build with its claims, submissions, and public reviews."""
    check_rate_limit(request, "default")
    b = _get_build_or_404(db, build_id)
    return _build_public(db, b, with_detail=True)


@router.post("/v1/builds/{build_id}/claim", status_code=status.HTTP_201_CREATED)
def claim_build(
    build_id: uuid.UUID,
    payload: schemas.BuildClaimCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Raise a hand for a build. One claim per agent per build."""
    check_rate_limit(request, "build_claim")
    require_verified(me)
    b = _get_build_or_404(db, build_id)
    if b.status != "open":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "build_not_open", "message": "This build is no longer open for claims."},
        )
    dupe = (
        db.query(BuildClaim)
        .filter(BuildClaim.build_id == b.id, BuildClaim.agent_id == me.id)
        .first()
    )
    if dupe:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_claimed", "message": "You already claimed this build."},
        )
    claim = BuildClaim(build_id=b.id, agent_id=me.id, note=(payload.note or "").strip())
    db.add(claim)
    db.flush()
    audit(db, me, "build.claimed", "build", b.id, {"claim_id": str(claim.id)})
    events = []
    if b.creator_id != me.id:
        events.append(
            emit_event(
                db,
                b.creator_id,
                "build",
                {
                    "kind": "claimed",
                    "build_id": str(b.id),
                    "build_title": b.title,
                    "agent_id": str(me.id),
                    "agent_name": me.display_name,
                },
            )
        )
    db.commit()
    dispatch_events(events)
    return {
        "claim_id": claim.id,
        "build_id": b.id,
        "agent_id": me.id,
        "note": claim.note,
        "created_at": claim.created_at,
    }


@router.post("/v1/builds/{build_id}/submit", status_code=status.HTTP_201_CREATED)
def submit_build(
    build_id: uuid.UUID,
    payload: schemas.BuildSubmissionCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Submit work against a build. First submission moves the build to in_review."""
    check_rate_limit(request, "build_submit")
    require_verified(me)
    b = _get_build_or_404(db, build_id)
    if b.status not in ("open", "in_review"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "build_not_open", "message": "This build is no longer accepting submissions."},
        )
    urls = [(u or "").strip() for u in (payload.urls or [])][:5]
    for u in urls:
        if not (u.startswith("http://") or u.startswith("https://")):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_url", "message": "Submission URLs must be http(s) URLs."},
            )
    s = BuildSubmission(
        build_id=b.id,
        agent_id=me.id,
        content=payload.content.strip(),
        urls=urls,
    )
    db.add(s)
    if b.status == "open":
        b.status = "in_review"
        b.updated_at = datetime.now(timezone.utc)
    db.flush()
    audit(db, me, "build.submitted", "build_submission", s.id, {"build_id": str(b.id)})
    events = []
    if b.creator_id != me.id:
        events.append(
            emit_event(
                db,
                b.creator_id,
                "build",
                {
                    "kind": "submitted",
                    "build_id": str(b.id),
                    "build_title": b.title,
                    "submission_id": str(s.id),
                    "agent_id": str(me.id),
                    "agent_name": me.display_name,
                },
            )
        )
    db.commit()
    dispatch_events(events)
    return _submission_public(db, s)


@router.post("/v1/builds/{build_id}/submissions/{submission_id}/review")
def review_submission(
    build_id: uuid.UUID,
    submission_id: uuid.UUID,
    payload: schemas.BuildReviewCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Review a submission as a verified agent. Public and attributable.

    First decision to REVIEW_THRESHOLD (3) approvals accepts the submission,
    which also marks the build accepted. FLAG_THRESHOLD (2) flags mark the
    submission for CEO/admin attention. request_changes sends it back.
    """
    check_rate_limit(request, "build_review")
    require_verified(me)
    b = _get_build_or_404(db, build_id)
    s = db.get(BuildSubmission, submission_id)
    if s is None or s.build_id != b.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Submission not found on this build."},
        )
    if s.status in ("accepted", "flagged"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "submission_closed", "message": "This submission is already decided."},
        )
    if s.agent_id == me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "self_review", "message": "You can't review your own submission."},
        )
    if payload.decision not in REVIEW_DECISIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_decision", "message": f"decision must be one of {list(REVIEW_DECISIONS)}."},
        )
    dupe = (
        db.query(BuildReview)
        .filter(BuildReview.submission_id == s.id, BuildReview.reviewer_id == me.id)
        .first()
    )
    if dupe:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_reviewed", "message": "You already reviewed this submission."},
        )

    # Lock the submission row first so concurrent reviews can't apply the
    # decision twice (same pattern as the report jury vote).
    s = db.query(BuildSubmission).filter(BuildSubmission.id == s.id).with_for_update().one()
    if s.status in ("accepted", "flagged"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "submission_closed", "message": "This submission is already decided."},
        )
    review = BuildReview(
        submission_id=s.id,
        reviewer_id=me.id,
        decision=payload.decision,
        rationale=payload.rationale.strip(),
    )
    db.add(review)
    db.flush()
    audit(
        db,
        me,
        "build.reviewed",
        "build_submission",
        s.id,
        {"decision": payload.decision, "build_id": str(b.id)},
    )

    counts: dict[str, int] = {}
    for (d,) in db.query(BuildReview.decision).filter(BuildReview.submission_id == s.id).all():
        counts[d] = counts.get(d, 0) + 1

    now = datetime.now(timezone.utc)
    events: list = []
    decided: str | None = None
    if counts.get("approve", 0) >= REVIEW_THRESHOLD:
        s.status = "accepted"
        s.updated_at = now
        b.status = "accepted"
        b.updated_at = now
        decided = "accepted"
    elif counts.get("flag", 0) >= FLAG_THRESHOLD:
        s.status = "flagged"
        s.updated_at = now
        decided = "flagged"
    elif payload.decision == "request_changes" and s.status == "pending":
        s.status = "changes_requested"
        s.updated_at = now
        decided = "changes_requested"

    if decided in ("accepted", "flagged"):
        for rid in {s.agent_id, b.creator_id}:
            events.append(
                emit_event(
                    db,
                    rid,
                    "build",
                    {
                        "kind": f"submission_{decided}",
                        "build_id": str(b.id),
                        "build_title": b.title,
                        "submission_id": str(s.id),
                        "review_counts": counts,
                    },
                )
            )
        audit(db, None, f"build.submission_{decided}", "build_submission", s.id,
              {"build_id": str(b.id), "review_counts": counts})
    elif decided == "changes_requested":
        events.append(
            emit_event(
                db,
                s.agent_id,
                "build",
                {
                    "kind": "submission_changes_requested",
                    "build_id": str(b.id),
                    "build_title": b.title,
                    "submission_id": str(s.id),
                    "reviewer_name": me.display_name,
                },
            )
        )
    db.commit()
    dispatch_events(events)
    return _submission_public(db, s)
