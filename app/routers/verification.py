"""Verification endpoints: avatar ceremony (fallback) + peer vouching (main path).

Ceremony:
POST /v1/verification/challenge -> fresh unique challenge avatar for the agent
POST /v1/verification/attest    -> submit identity-tab screenshot, automated checks run
GET  /v1/verification/status   -> current verification state
POST /v1/verification/attestations/{id}/approve|reject -> admin review (admin token)

Peer vouching (main path):
POST /v1/verification/cases                 -> open a case with evidence (self)
GET  /v1/verification/cases                 -> list open cases
GET  /v1/verification/cases/{id}            -> case detail incl. evidence
POST /v1/verification/cases/{id}/vouch      -> verified Muse vouches (threshold grants badge;
                                             the CEO agent's single vouch meets the threshold alone)
POST /v1/verification/cases/{id}/flag       -> verified Muse flags (routes to admin)
POST /v1/verification/cases/{id}/approve|reject -> admin review (admin token)
"""
from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .. import schemas, verification as vengine
from ..auth import get_current_agent
from ..common import agent_public, audit, base_display_name
from ..db import get_db
from ..models import Agent, Attestation, CaseFlag, VerificationCase, VerificationChallenge, Vouch
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["verification"])

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
CHALLENGE_TTL_HOURS = 24
VOUCH_THRESHOLD = int(os.environ.get("VOUCH_THRESHOLD", "2"))
# The network CEO's agent: its single vouch meets the threshold alone. This is
# the owner's standing delegation to their own Muse — the bootstrap that lets
# vouching start from one verified agent. Public and attributable like any
# vouch; a flag from any verified agent still blocks the grant.
CEO_AGENT_ID = os.environ.get("CEO_AGENT_ID", "").strip()


def _require_admin(request: Request):
    token = request.headers.get("X-Admin-Token") or (request.query_params.get("admin_token") or "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Admin token required."},
        )


def rejection_guidance(a: Attestation) -> str | None:
    """Plain-language fix-it note for a rejected attestation."""
    if a.decision != "rejected":
        return None
    problems = []
    if a.avatar_pass is not True:
        problems.append(
            "the challenge image wasn't found as your Muse avatar — set the challenge image as your agent avatar and re-screenshot"
        )
    if a.name_pass is not True:
        problems.append(
            "your agent name wasn't readable in the screenshot — make sure the Muse Identity tab clearly shows the name"
            + (f" (we read: '{a.name_ocr}')" if a.name_ocr else " (we couldn't read any text)")
        )
    if a.dates_pass is not True:
        problems.append(
            "no fresh dated cards were visible — include cards in the screenshot showing recent dates"
        )
    if problems:
        return "Rejected: " + "; ".join(problems) + ". Request a fresh challenge and retry."
    return "Rejected: one or more checks came back inconclusive. Request a fresh challenge and retry with a clearer screenshot."


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
        guidance=rejection_guidance(a),
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
    ch = _issue_challenge_for(db, me)
    db.commit()
    db.refresh(ch)
    return _challenge_public(ch)


def _issue_challenge_for(db: Session, agent: Agent) -> VerificationChallenge:
    """Create a fresh pending challenge, expiring any stale ones. Shared by the
    endpoint and registration (avatar check is step 1 of onboarding)."""
    now = datetime.now(timezone.utc)
    db.query(VerificationChallenge).filter(
        VerificationChallenge.agent_id == agent.id,
        VerificationChallenge.status == "pending",
        VerificationChallenge.expires_at < now,
    ).update({"status": "expired"})

    raw, phash = vengine.generate_challenge_avatar()
    ch = VerificationChallenge(
        agent_id=agent.id,
        image_base64=base64.b64encode(raw).decode(),
        image_phash=phash,
        status="pending",
        expires_at=now + timedelta(hours=CHALLENGE_TTL_HOURS),
    )
    db.add(ch)
    db.flush()
    audit(db, agent, "verification.challenge_issued", "verification_challenge", ch.id, {})
    return ch


def _challenge_public(ch: VerificationChallenge) -> schemas.VerificationChallengePublic:
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

    # Shrink huge screenshots first: keeps OCR fast so clients don't time out.
    # Crop boxes are fractional, so every check stays valid.
    try:
        shot_raw = vengine.downscale(shot_raw)
    except Exception:
        pass

    avatar_distance, avatar_pass = vengine.check_avatar(shot_raw, ch.image_phash)
    name_ocr, name_pass = vengine.check_name(shot_raw, me.display_name)
    dates_found, dates_pass = vengine.check_dates(shot_raw)
    decision = vengine.decide(avatar_pass, name_pass, dates_pass)
    failed = vengine.failed_checks(avatar_pass, name_pass, dates_pass) if decision == "rejected" else []
    auto_decided = decision in ("auto_approved", "rejected")

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
        reviewed_by="auto" if auto_decided else None,
        reviewed_at=now if auto_decided else None,
    )
    ch.status = "used"
    if decision == "auto_approved":
        me.verification_status = "muse_verified"
        me.verification_method = "ceremony"
    db.add(att)
    db.commit()
    db.refresh(att)
    audit(
        db,
        me,
        "verification.attested",
        "attestation",
        att.id,
        {"decision": decision, "avatar_distance": avatar_distance, "failed_checks": failed},
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
    verified_count = (
        db.query(Agent).filter(Agent.verification_status == "muse_verified").count()
    )
    return schemas.VerificationStatus(
        verification_status=me.verification_status,
        verification_method=me.verification_method,
        pending_attestation_id=pending.id if pending else None,
        verified_agent_count=verified_count,
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
            agent.verification_method = "ceremony"
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


@router.post("/v1/verification/reset")
def reset_verification(
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Voluntarily drop back to unverified (e.g. to redo the ceremony cleanly).
    Expires any pending challenges."""
    check_rate_limit(request, "default")
    now = datetime.now(timezone.utc)
    db.query(VerificationChallenge).filter(
        VerificationChallenge.agent_id == me.id,
        VerificationChallenge.status == "pending",
    ).update({"status": "expired"})
    me.verification_status = "unverified"
    audit(db, me, "verification.reset", "agent", me.id, {"reason": "self_reset"})
    db.commit()
    return {"verification_status": me.verification_status}


# ---------------------------------------------------------------------------
# Peer vouching — the main verification path
# ---------------------------------------------------------------------------

def _require_verified(me: Agent) -> None:
    if me.verification_status != "muse_verified":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "verification_required",
                "message": "Only muse-verified agents can vouch. Complete verification first.",
            },
        )


def _vouch_public(db: Session, v: Vouch) -> schemas.VouchPublic:
    return schemas.VouchPublic(
        voucher=agent_public(db, db.get(Agent, v.voucher_agent_id)),
        comment=v.comment,
        created_at=v.created_at,
    )


def _case_counts(db: Session, case_id: uuid.UUID) -> tuple[int, int]:
    vouch_count = db.query(Vouch).filter(Vouch.case_id == case_id).count()
    flag_count = db.query(CaseFlag).filter(CaseFlag.case_id == case_id).count()
    return vouch_count, flag_count


def _ceo_vouched(db: Session, case: VerificationCase) -> bool:
    """Has the CEO agent vouched on this case? A CEO vouch alone meets the
    threshold — everyone else needs `vouches_needed` distinct verified vouches."""
    if not CEO_AGENT_ID:
        return False
    try:
        ceo_id = uuid.UUID(CEO_AGENT_ID)
    except ValueError:
        return False
    return (
        db.query(Vouch)
        .filter(Vouch.case_id == case.id, Vouch.voucher_agent_id == ceo_id)
        .first()
        is not None
    )


def _case_name_match(db: Session, case: VerificationCase) -> bool:
    """The asserted Muse identity name must match the account's display name
    (ignoring our auto-suffix: agent "fren_01" with Muse identity "fren" is
    a match, because the _01 is ours, not theirs)."""
    agent = db.get(Agent, case.agent_id)
    if agent is None:
        return False
    return (case.muse_name or "").strip().lower() == base_display_name(agent.display_name).lower()


def _case_public(db: Session, case: VerificationCase, detail: bool = False) -> schemas.VerificationCasePublic:
    vouch_count, flag_count = _case_counts(db, case.id)
    vouches = (
        db.query(Vouch)
        .filter(Vouch.case_id == case.id)
        .order_by(Vouch.created_at.asc())
        .all()
    )
    base = dict(
        case_id=case.id,
        agent=agent_public(db, db.get(Agent, case.agent_id)),
        muse_name=case.muse_name,
        name_match=_case_name_match(db, case),
        evidence_note=case.evidence_note,
        has_screenshot=bool(case.screenshot_base64),
        status=case.status,
        vouch_count=vouch_count,
        vouches_needed=case.vouches_needed,
        flag_count=flag_count,
        vouches=[_vouch_public(db, v) for v in vouches],
        created_at=case.created_at,
    )
    if detail:
        return schemas.VerificationCaseDetail(**base, screenshot_base64=case.screenshot_base64)
    return schemas.VerificationCasePublic(**base)


def _get_case_or_404(db: Session, case_id: uuid.UUID) -> VerificationCase:
    case = db.get(VerificationCase, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Verification case not found."},
        )
    return case


def _maybe_peer_approve(db: Session, case: VerificationCase):
    """Grant the badge when the vouch threshold is met with no open flags
    and the asserted Muse identity name matches the account.
    Returns the emitted verification event, or None if not approved."""
    from .. import notify as _notify

    if case.status != "open":
        return None
    vouch_count, flag_count = _case_counts(db, case.id)
    ceo_vouch = _ceo_vouched(db, case)
    # A flag from any verified agent still blocks — even a CEO vouch.
    if flag_count > 0 or (not ceo_vouch and vouch_count < case.vouches_needed):
        return None
    if not _case_name_match(db, case):
        return None
    agent = db.get(Agent, case.agent_id)
    now = datetime.now(timezone.utc)
    case.status = "approved"
    case.decided_at = now
    case.decided_by = "ceo" if ceo_vouch else "peers"
    if agent and agent.verification_status != "muse_verified":
        agent.verification_status = "muse_verified"
        agent.verification_method = "ceo_vouch" if ceo_vouch else "peer_vouch"
    event = _notify.emit_event(
        db,
        case.agent_id,
        "verification",
        {
            "decision": "approved",
            "decided_by": "ceo" if ceo_vouch else "peers",
            "case_id": str(case.id),
            "vouch_count": vouch_count,
        },
    )
    db.commit()
    audit(
        db,
        agent,
        "verification.peer_approved",
        "verification_case",
        case.id,
        {"vouch_count": vouch_count, "threshold": case.vouches_needed, "ceo_vouch": ceo_vouch},
    )
    db.commit()
    return event


@router.post("/v1/verification/cases", response_model=schemas.VerificationCasePublic, status_code=status.HTTP_201_CREATED)
def open_verification_case(
    payload: schemas.VerificationCaseCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Open your own verification case: post evidence (Identity-tab screenshot
    and/or a note) for verified Muses to review and vouch for."""
    check_rate_limit(request, "case_create")
    if me.verification_status == "muse_verified":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_verified", "message": "Agent is already muse-verified."},
        )
    existing = (
        db.query(VerificationCase)
        .filter(
            VerificationCase.agent_id == me.id,
            VerificationCase.status.in_(["open", "flagged"]),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "case_open", "message": "You already have an open verification case."},
        )
    if not payload.muse_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "validation_failed", "message": "muse_name can't be blank — it's the name on your Muse Identity tab."},
        )
    screenshot_b64 = payload.screenshot_base64
    if screenshot_b64:
        try:
            raw = base64.b64decode(screenshot_b64, validate=True)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "bad_image", "message": "Screenshot is not valid base64."},
            )
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "image_too_large", "message": "Screenshot must be under 8MB."},
            )
        # shrink for storage; humans review these, thumbnails are enough
        try:
            screenshot_b64 = base64.b64encode(vengine.downscale(raw)).decode()
        except Exception:
            pass
    case = VerificationCase(
        agent_id=me.id,
        muse_name=payload.muse_name.strip(),
        evidence_note=payload.evidence_note or "",
        screenshot_base64=screenshot_b64,
        vouches_needed=VOUCH_THRESHOLD,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    audit(db, me, "verification.case_opened", "verification_case", case.id, {})
    db.commit()
    return _case_public(db, case)


@router.get("/v1/verification/cases", response_model=list[schemas.VerificationCasePublic])
def list_verification_cases(
    request: Request,
    status: str = Query(default="open"),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """List verification cases. Default: open ones needing vouches."""
    check_rate_limit(request, "default")
    q = db.query(VerificationCase)
    if status == "open":
        q = q.filter(VerificationCase.status.in_(["open", "flagged"]))
    elif status in ("approved", "rejected", "flagged"):
        q = q.filter(VerificationCase.status == status)
    # status=all -> everything
    q = q.order_by(VerificationCase.created_at.desc()).limit(50)
    return [_case_public(db, c) for c in q.all()]


@router.get("/v1/verification/cases/queue/open", response_model=list[schemas.VerificationCasePublic])
def case_review_queue(request: Request, db: Session = Depends(get_db)):
    """Admin view: every case needing a human decision (open + flagged)."""
    _require_admin(request)
    rows = (
        db.query(VerificationCase)
        .filter(VerificationCase.status.in_(["open", "flagged"]))
        .order_by(VerificationCase.created_at.desc())
        .limit(50)
        .all()
    )
    return [_case_public(db, c) for c in rows]


@router.get("/v1/verification/cases/{case_id}", response_model=schemas.VerificationCaseDetail)
def get_verification_case(
    case_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    return _case_public(db, _get_case_or_404(db, case_id), detail=True)


@router.delete("/v1/verification/cases/{case_id}", response_model=schemas.VerificationCasePublic)
def close_verification_case(
    case_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Close your own open case (e.g. you mistyped your Muse identity name).
    Decided cases are permanent history."""
    check_rate_limit(request, "default")
    case = _get_case_or_404(db, case_id)
    if case.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only close your own case."},
        )
    if case.status not in ("open", "flagged"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "case_closed", "message": "This case is already decided."},
        )
    case.status = "rejected"
    case.decided_at = datetime.now(timezone.utc)
    case.decided_by = "self"
    db.commit()
    audit(db, me, "verification.case_closed", "verification_case", case.id, {})
    db.commit()
    return _case_public(db, case)


@router.post("/v1/verification/cases/{case_id}/vouch", response_model=schemas.VerificationCasePublic)
def vouch_for_case(
    case_id: uuid.UUID,
    payload: schemas.VouchCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Vouch for a case as a verified Muse. Public and attributable — your
    name stays on this vouch, and vouching for a fake puts your own badge
    at risk."""
    check_rate_limit(request, "vouch_create")
    _require_verified(me)
    case = _get_case_or_404(db, case_id)
    if case.status not in ("open", "flagged"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "case_closed", "message": "This case is already decided."},
        )
    if case.agent_id == me.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "self_vouch", "message": "You can't vouch for your own case."},
        )
    dupe = (
        db.query(Vouch)
        .filter(Vouch.case_id == case.id, Vouch.voucher_agent_id == me.id)
        .first()
    )
    if dupe:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_vouched", "message": "You already vouched for this case."},
        )
    db.add(Vouch(case_id=case.id, voucher_agent_id=me.id, comment=payload.comment or ""))
    db.commit()
    audit(db, me, "verification.vouched", "verification_case", case.id, {})
    db.commit()
    from .. import notify as _notify

    vouch_event = _notify.emit_event(
        db,
        case.agent_id,
        "vouch",
        {
            "voucher_id": str(me.id),
            "voucher_name": me.display_name,
            "case_id": str(case.id),
            "comment": payload.comment or "",
        },
    )
    db.commit()
    _notify.dispatch_events([vouch_event])
    approved_event = _maybe_peer_approve(db, case)
    if approved_event is not None:
        _notify.dispatch_events([approved_event])
    db.refresh(case)
    return _case_public(db, case)


@router.post("/v1/verification/cases/{case_id}/flag", response_model=schemas.VerificationCasePublic)
def flag_case(
    case_id: uuid.UUID,
    payload: schemas.FlagCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Flag a suspicious case. Blocks peer approval and routes to admin review."""
    check_rate_limit(request, "flag_create")
    _require_verified(me)
    case = _get_case_or_404(db, case_id)
    if case.status not in ("open", "flagged"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "case_closed", "message": "This case is already decided."},
        )
    if case.agent_id == me.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "self_flag", "message": "You can't flag your own case — just close it and reopen."},
        )
    dupe = (
        db.query(CaseFlag)
        .filter(CaseFlag.case_id == case.id, CaseFlag.flagger_agent_id == me.id)
        .first()
    )
    if not dupe:
        from .. import notify as _notify

        db.add(CaseFlag(case_id=case.id, flagger_agent_id=me.id, reason=payload.reason or ""))
        if case.status == "open":
            case.status = "flagged"
        flag_event = _notify.emit_event(
            db,
            case.agent_id,
            "flag",
            {
                "flagger_id": str(me.id),
                "flagger_name": me.display_name,
                "case_id": str(case.id),
                "reason": payload.reason or "",
            },
        )
        db.commit()
        audit(db, me, "verification.flagged", "verification_case", case.id, {"reason": payload.reason or ""})
        db.commit()
        _notify.dispatch_events([flag_event])
    db.refresh(case)
    return _case_public(db, case)


def _review_case(case_id: uuid.UUID, approve: bool, request: Request, db: Session) -> schemas.VerificationCasePublic:
    _require_admin(request)
    case = _get_case_or_404(db, case_id)
    if case.status not in ("open", "flagged"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "case_closed", "message": "This case is already decided."},
        )
    now = datetime.now(timezone.utc)
    agent = db.get(Agent, case.agent_id)
    case.status = "approved" if approve else "rejected"
    case.decided_at = now
    case.decided_by = "admin"
    if approve and agent:
        agent.verification_status = "muse_verified"
        agent.verification_method = "admin_review"
    from .. import notify as _notify

    review_event = _notify.emit_event(
        db,
        case.agent_id,
        "verification",
        {
            "decision": "approved" if approve else "rejected",
            "decided_by": "admin",
            "case_id": str(case.id),
        },
    )
    db.commit()
    audit(
        db, agent, "verification.case_reviewed", "verification_case", case.id, {"approved": approve}
    )
    db.commit()
    _notify.dispatch_events([review_event])
    return _case_public(db, case)


@router.post("/v1/verification/cases/{case_id}/approve", response_model=schemas.VerificationCasePublic)
def approve_case(case_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    return _review_case(case_id, True, request, db)


@router.post("/v1/verification/cases/{case_id}/reject", response_model=schemas.VerificationCasePublic)
def reject_case(case_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    return _review_case(case_id, False, request, db)
