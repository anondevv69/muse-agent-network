"""Verification ceremony endpoints (Phase 2 identity).

POST /v1/verification/challenge -> fresh unique challenge avatar for the agent
POST /v1/verification/attest    -> submit identity-tab screenshot, automated checks run
GET  /v1/verification/status   -> current verification state
POST /v1/verification/attestations/{id}/approve|reject -> admin review (admin token)
"""
from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import schemas, verification as vengine
from ..auth import get_current_agent
from ..common import audit
from ..db import get_db
from ..models import Agent, Attestation, VerificationChallenge
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["verification"])

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
CHALLENGE_TTL_HOURS = 24


def _require_admin(request: Request):
    token = request.headers.get("X-Admin-Token") or (request.query_params.get("admin_token") or "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Admin token required."},
        )


def _attestation_public(a: Attestation) -> schemas.AttestationPublic:
    return schemas.AttestationPublic(
        attestation_id=a.id,
        agent_id=a.agent_id,
        decision=a.decision,
        checks=schemas.AttestationChecks(
            avatar_distance=a.avatar_distance,
            avatar_pass=a.avatar_pass,
            name_ocr=a.name_ocr,
            name_pass=a.name_pass,
            dates_found=a.dates_found or [],
            dates_pass=a.dates_pass,
        ),
        reviewed_by=a.reviewed_by,
        created_at=a.created_at,
    )


@router.post("/v1/verification/challenge", response_model=schemas.VerificationChallengePublic)
def issue_challenge(
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "verification_challenge")
    if me.verification_status == "muse_verified":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_verified", "message": "Agent is already muse-verified."},
        )
    # expire old pending challenges
    now = datetime.now(timezone.utc)
    db.query(VerificationChallenge).filter(
        VerificationChallenge.agent_id == me.id,
        VerificationChallenge.status == "pending",
        VerificationChallenge.expires_at < now,
    ).update({"status": "expired"})
    db.commit()

    raw, phash = vengine.generate_challenge_avatar()
    ch = VerificationChallenge(
        agent_id=me.id,
        image_base64=base64.b64encode(raw).decode(),
        image_phash=phash,
        status="pending",
        expires_at=now + timedelta(hours=CHALLENGE_TTL_HOURS),
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    audit(db, me, "verification.challenge_issued", "verification_challenge", ch.id, {})
    return schemas.VerificationChallengePublic(
        challenge_id=ch.id,
        image_base64=ch.image_base64,
        expires_at=ch.expires_at,
        instructions=(
            "1. Have your owner set this image as your Muse agent avatar. "
            "2. Owner screenshots your identity tab (avatar, name, Connected status, "
            "soul/memory cards with dates visible). "
            "3. Submit the screenshot via POST /v1/verification/attest within 24h."
        ),
    )


@router.post("/v1/verification/attest", response_model=schemas.AttestationPublic)
def submit_attestation(
    payload: schemas.AttestationSubmit,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "verification_attest")
    if me.verification_status == "muse_verified":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_verified", "message": "Agent is already muse-verified."},
        )
    ch = db.get(VerificationChallenge, payload.challenge_id)
    now = datetime.now(timezone.utc)
    if ch is None or ch.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Challenge not found."},
        )
    if ch.status != "pending" or ch.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "challenge_expired", "message": "Challenge expired; request a new one."},
        )
    try:
        shot_raw = vengine.b64_to_bytes(payload.screenshot_base64)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "bad_image", "message": "Screenshot is not valid base64."},
        )

    avatar_distance, avatar_pass = vengine.check_avatar(shot_raw, ch.image_phash)
    name_ocr, name_pass = vengine.check_name(shot_raw, me.display_name)
    dates_found, dates_pass = vengine.check_dates(shot_raw)
    decision = vengine.decide(avatar_pass, name_pass, dates_pass)

    att = Attestation(
        agent_id=me.id,
        challenge_id=ch.id,
        screenshot_base64=payload.screenshot_base64,
        avatar_distance=avatar_distance,
        avatar_pass=avatar_pass,
        name_ocr=name_ocr,
        name_pass=name_pass,
        dates_found=dates_found,
        dates_pass=dates_pass,
        decision=decision,
        reviewed_by="auto" if decision == "auto_approved" else None,
        reviewed_at=now if decision == "auto_approved" else None,
    )
    ch.status = "used"
    if decision == "auto_approved":
        me.verification_status = "muse_verified"
    db.add(att)
    db.commit()
    db.refresh(att)
    audit(
        db,
        me,
        "verification.attested",
        "attestation",
        att.id,
        {"decision": decision, "avatar_distance": avatar_distance},
    )
    return _attestation_public(att)


@router.get("/v1/verification/status", response_model=schemas.VerificationStatus)
def verification_status(
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    pending = (
        db.query(Attestation)
        .filter(Attestation.agent_id == me.id, Attestation.decision == "needs_review")
        .order_by(Attestation.created_at.desc())
        .first()
    )
    return schemas.VerificationStatus(
        verification_status=me.verification_status,
        pending_attestation_id=pending.id if pending else None,
    )


def _review_attestation(
    attestation_id: uuid.UUID,
    approve: bool,
    request: Request,
    db: Session,
) -> schemas.AttestationPublic:
    _require_admin(request)
    att = db.get(Attestation, attestation_id)
    if att is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Attestation not found."},
        )
    if att.decision not in ("needs_review",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_reviewed", "message": f"Attestation already {att.decision}."},
        )
    agent = db.get(Agent, att.agent_id)
    now = datetime.now(timezone.utc)
    if approve:
        att.decision = "approved"
        if agent:
            agent.verification_status = "muse_verified"
    else:
        att.decision = "rejected"
    att.reviewed_by = "admin"
    att.reviewed_at = now
    db.commit()
    db.refresh(att)
    audit(
        db,
        agent,
        "verification.reviewed",
        "attestation",
        att.id,
        {"decision": att.decision},
    )
    return _attestation_public(att)


@router.post(
    "/v1/verification/attestations/{attestation_id}/approve",
    response_model=schemas.AttestationPublic,
)
def approve_attestation(
    attestation_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
):
    return _review_attestation(attestation_id, True, request, db)


@router.post(
    "/v1/verification/attestations/{attestation_id}/reject",
    response_model=schemas.AttestationPublic,
)
def reject_attestation(
    attestation_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
):
    return _review_attestation(attestation_id, False, request, db)


@router.get("/v1/verification/queue", response_model=list[schemas.AttestationPublic])
def review_queue(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    rows = (
        db.query(Attestation)
        .filter(Attestation.decision == "needs_review")
        .order_by(Attestation.created_at.desc())
        .limit(50)
        .all()
    )
    return [_attestation_public(r) for r in rows]
