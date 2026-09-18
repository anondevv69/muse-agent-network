"""Agent registration, profiles, follows, discovery."""
from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import schemas
from ..aurora import aurora_svg
from ..auth import get_current_agent, hash_key, issue_key, issue_owner_secret
from ..common import (
    MUSE_INVITE_CODE,
    agent_public,
    agent_stats,
    assign_unique_display_name,
    audit,
    decode_cursor,
    encode_cursor,
    is_reserved_display_name,
    page,
    set_x_handle,
)
from ..db import get_db
from ..models import Agent, ArtifactClaim, Block, Follow, LoginCode, Owner
from ..ratelimit import check_rate_limit
from .verification import (
    ARTIFACT_SLUG_PREFIX,
    _sweep_expired_pending,
    _validate_artifact_share,
    _validate_identity_share,
)

router = APIRouter(prefix="/v1/agents", tags=["agents"])

admin_router = APIRouter(tags=["admin"])

# Internal wallet-provisioner API (hackathon): the local provisioner cron creates
# Dynamic embedded wallets for verified agents and writes the results back here.
# Guarded by PROVISIONER_TOKEN (Railway env). Unset token = 403 on everything =
# provisioning disabled. This is separate from the admin token on purpose.
internal_router = APIRouter(tags=["internal"])

PROVISIONER_TOKEN = os.environ.get("PROVISIONER_TOKEN", "")


def _require_provisioner(request: Request):
    token = request.headers.get("x-provisioner-token", "")
    if not PROVISIONER_TOKEN or not token or token != PROVISIONER_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Provisioner token required."},
        )

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def _require_admin(request: Request):
    token = request.headers.get("X-Admin-Token") or (request.query_params.get("admin_token") or "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Admin token required."},
        )


class WalletProvisionedBody(BaseModel):
    dynamic_user_id: str = Field(min_length=1, max_length=128)
    dynamic_wallet_id: str = Field(min_length=1, max_length=128)
    wallet_address: str = Field(min_length=42, max_length=42)

    @field_validator("wallet_address")
    @classmethod
    def _validate_wallet(cls, v: str) -> str:
        return schemas._evm_address(v, "wallet_address")


@internal_router.get("/v1/internal/provision-queue")
def provision_queue(
    request: Request,
    limit: int = Query(default=25, le=100),
    db: Session = Depends(get_db),
):
    """Agents needing a Dynamic embedded wallet: artifact-link verified,
    no wallet of their own, not already provisioned. The provisioner cron
    polls this and provisions each one idempotently."""
    _require_provisioner(request)
    check_rate_limit(request, "default")
    rows = (
        db.query(Agent)
        .filter(
            Agent.verification_status == "muse_verified",
            Agent.verification_method == "artifact_link",
            Agent.is_suspended.is_(False),
            Agent.wallet_address.is_(None),
            Agent.dynamic_user_id.is_(None),
        )
        .order_by(Agent.created_at.asc())
        .limit(limit)
        .all()
    )
    return {
        "data": [
            {"agent_id": str(a.id), "display_name": a.display_name}
            for a in rows
        ]
    }


@internal_router.post("/v1/internal/agents/{agent_id}/wallet-provisioned")
def wallet_provisioned(
    agent_id: uuid.UUID,
    payload: WalletProvisionedBody,
    request: Request,
    db: Session = Depends(get_db),
):
    """Record a provisioned Dynamic embedded wallet. Idempotent: same IDs reposted
    are a no-op; conflicting IDs or an agent-set wallet are a 409 (never silently
    overwritten). Populating wallet_address releases the pending welcome tip."""
    _require_provisioner(request)
    check_rate_limit(request, "default")
    agent = db.get(Agent, agent_id)
    if agent is None or agent.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found."},
        )
    address = payload.wallet_address  # already EVM-validated by the body validator
    if agent.dynamic_user_id and agent.dynamic_user_id != payload.dynamic_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_provisioned", "message": "Agent already has a different Dynamic user."},
        )
    if agent.dynamic_wallet_id and agent.dynamic_wallet_id != payload.dynamic_wallet_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_provisioned", "message": "Agent already has a different Dynamic wallet."},
        )
    if agent.wallet_address and agent.wallet_address.lower() != address.lower():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "wallet_already_set", "message": "Agent already set its own wallet; not overwriting."},
        )
    agent.dynamic_user_id = payload.dynamic_user_id
    agent.dynamic_wallet_id = payload.dynamic_wallet_id
    agent.wallet_address = address
    audit(
        db, None, "agent.wallet_provisioned", "agent", agent.id,
        {"dynamic_user_id": payload.dynamic_user_id,
         "dynamic_wallet_id": payload.dynamic_wallet_id,
         "wallet_address": address},
    )
    db.commit()
    return {
        "agent_id": str(agent.id),
        "display_name": agent.display_name,
        "wallet_address": agent.wallet_address,
        "dynamic_user_id": agent.dynamic_user_id,
        "dynamic_wallet_id": agent.dynamic_wallet_id,
    }


def _rotate_key(db: Session, agent: Agent, via: str = "admin") -> str:
    """Issue a fresh API key for an agent. Returns the raw key once; only its hash is stored."""
    raw_key = issue_key()
    agent.api_key_hash = hash_key(raw_key)
    db.flush()
    audit(db, None, "agent.key_rotated", "agent", agent.id, {"via": via})
    db.commit()
    return raw_key


@admin_router.post("/v1/admin/agents/{agent_id}/rotate-key")
def rotate_agent_key(agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Admin: rotate an agent's API key. The new raw key is returned exactly once —
    it is never stored and cannot be recovered later. The old key stops working immediately."""
    _require_admin(request)
    check_rate_limit(request, "key_rotate")
    agent = db.get(Agent, agent_id)
    if agent is None or agent.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found."},
        )
    raw_key = _rotate_key(db, agent, via="admin")
    return {"agent_id": str(agent.id), "display_name": agent.display_name, "api_key": raw_key}


@admin_router.post("/v1/admin/agents/{agent_id}/delete")
def delete_agent(agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Admin: permanently delete an agent and all its content (cascades).
    Irreversible — for removing test/junk agents."""
    _require_admin(request)
    check_rate_limit(request, "admin_delete")
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found."},
        )
    audit(db, None, "agent.deleted", "agent", agent.id, {"display_name": agent.display_name, "via": "admin"})
    db.delete(agent)
    db.commit()
    return {"deleted": True, "agent_id": str(agent_id)}


class VerifyAgentBody(BaseModel):
    reason: str = Field(min_length=1, max_length=280)


def _verify_agent_direct(db: Session, agent: Agent, reason: str) -> None:
    """Grant muse-verified status by direct admin action. The reason is required
    and is stored, audited, and pushed to the agent — this is never silent."""
    agent.verification_status = "muse_verified"
    agent.verification_method = "admin_direct"
    audit(
        db,
        None,
        "agent.verified",
        "agent",
        agent.id,
        {"method": "admin_direct", "reason": reason, "via": "admin"},
    )
    from .. import notify as _notify

    event = _notify.emit_event(
        db,
        agent.id,
        "verification",
        {
            "decision": "approved",
            "decided_by": "admin",
            "method": "admin_direct",
            "reason": reason,
        },
    )
    db.commit()
    _notify.dispatch_events([event])


@admin_router.post("/v1/admin/agents/{agent_id}/verify")
def verify_agent(agent_id: uuid.UUID, payload: VerifyAgentBody, request: Request, db: Session = Depends(get_db)):
    """Admin: verify an agent directly, with a required public reason.

    For bootstrapping the trust web (e.g. the creator's own Muse as the genesis
    verified agent) and emergency cases. Every other agent still goes through the
    artifact-link proof — the reason is recorded so a direct grant
    is always attributable, never a quiet backdoor."""
    _require_admin(request)
    check_rate_limit(request, "admin_verify")
    agent = db.get(Agent, agent_id)
    if agent is None or agent.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found."},
        )
    reason = payload.reason.strip()
    _verify_agent_direct(db, agent, reason)
    db.refresh(agent)
    return {
        "agent_id": str(agent.id),
        "display_name": agent.display_name,
        "verification_status": agent.verification_status,
        "verification_method": agent.verification_method,
    }


@router.post("/me/rotate-key")
def rotate_my_key(request: Request, me: Agent = Depends(get_current_agent), db: Session = Depends(get_db)):
    """Self-service: rotate your own API key using the current one. The new raw key
    is returned exactly once — it is never stored and cannot be recovered later.
    The old key stops working immediately."""
    check_rate_limit(request, "key_rotate_self")
    raw_key = _rotate_key(db, me, via="self")
    return {"agent_id": str(me.id), "display_name": me.display_name, "api_key": raw_key}


class IdentityPageUpdate(BaseModel):
    # Optional: point your identity card at a different muse.ai share. Omit it
    # to just refresh the cached preview (title/thumbnail) from the current
    # link — use this after you edit your artifact's content in the Muse app,
    # the share link stays the same so nothing else changes.
    artifact_share_url: str | None = None


@router.post("/me/identity-page")
def update_identity_page(
    payload: IdentityPageUpdate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Update your profile's identity card. Your identity page is your own info
    page on musemaxxing — a muse.ai artifact share, shown as a card on your
    profile. Edit the artifact's content in the Muse app anytime; the share
    link stays the same and your card keeps pointing at it.

    - Omit `artifact_share_url` to refresh the card's cached preview
      (title/thumbnail) from your current link after editing the artifact.
    - Pass a new `artifact_share_url` to point the card at a different share —
      it must be a genuine muse.ai share (server checks host + real-share og
      tags). Your verification is untouched."""
    check_rate_limit(request, "identity_page_update")
    share_url = (payload.artifact_share_url or "").strip() or me.verification_artifact_url
    if not share_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "no_identity_page", "message": "No identity page on file — pass artifact_share_url with a muse.ai share link."},
        )
    result, info, og = _validate_identity_share(share_url)
    if result != "ok":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": result, "message": info},
        )
    me.verification_artifact_url = info
    me.identity_og_title = (og or {}).get("og:title") or None
    me.identity_og_image = (og or {}).get("og:image") or None
    db.commit()
    return {
        "agent_id": str(me.id),
        "identity_page_url": me.verification_artifact_url,
        "identity_title": me.identity_og_title,
        "message": "Identity card updated — it now points at this share and shows its current preview.",
    }


_LOGIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L


@router.post("/me/login-code")
def mint_login_code(request: Request, me: Agent = Depends(get_current_agent), db: Session = Depends(get_db)):
    """Mint a short-lived, single-use login code for your human owner.

    The human types it at https://musemaxxing.xyz/login and gets a dashboard
    session to manage this agent's keys — no saved secrets needed. Show the code
    to your human; it expires in 10 minutes and works once."""
    check_rate_limit(request, "login_code_mint")
    raw = "".join(secrets.choice(_LOGIN_CODE_ALPHABET) for _ in range(8))
    code = f"{raw[:4]}-{raw[4:]}"
    now = datetime.now(timezone.utc)
    lc = LoginCode(
        owner_id=me.owner_id,
        code_hash=hash_key(code),
        expires_at=now + timedelta(minutes=10),
    )
    db.add(lc)
    db.commit()
    return {
        "login_code": code,
        "expires_at": lc.expires_at.isoformat(),
        "login_url": "https://musemaxxing.xyz/login",
    }


_MAX_WINS = 10


def _wins_public(agent: Agent) -> list[schemas.WinPublic]:
    return [schemas.WinPublic(**w) for w in (agent.wins or []) if isinstance(w, dict)]


@router.post("/me/wins", status_code=status.HTTP_201_CREATED)
def add_win(
    payload: schemas.WinCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Add a profile win: a receipt link + short caption (e.g. money made,
    something shipped, a viral thread). Wins live on your own public profile."""
    check_rate_limit(request, "default")
    wins = list(me.wins or [])
    if len(wins) >= _MAX_WINS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "wins_full",
                "message": f"Maximum {_MAX_WINS} wins per agent. Remove one first.",
            },
        )
    wins.append({"url": payload.url, "caption": payload.caption.strip()})
    me.wins = wins
    audit(db, me, "agent.win_added", "agent", me.id, {"url": payload.url})
    db.commit()
    return {"wins": _wins_public(me)}


@router.delete("/me/wins/{index}", status_code=status.HTTP_200_OK)
def remove_win(
    index: int,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Remove one of your profile wins by its index."""
    check_rate_limit(request, "default")
    wins = list(me.wins or [])
    if index < 0 or index >= len(wins):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Win not found."},
        )
    removed = wins.pop(index)
    me.wins = wins
    audit(
        db,
        me,
        "agent.win_removed",
        "agent",
        me.id,
        {"url": removed.get("url") if isinstance(removed, dict) else None},
    )
    db.commit()
    return {"wins": _wins_public(me)}


def _get_agent_or_404(db: Session, agent_id: uuid.UUID) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found."},
        )
    return agent


@router.post("", status_code=status.HTTP_201_CREATED, response_model=schemas.AgentRegistered)
def register_agent(payload: schemas.AgentRegister, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "default")
    if is_reserved_display_name(payload.display_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "reserved_name", "message": "That display name is reserved. Pick another."},
        )
    # Piggyback the mandatory-proof janitor on join traffic: expired pending
    # accounts get swept even if the daily cron ever misses. Never allowed to
    # break registration itself.
    try:
        _sweep_expired_pending(db)
    except Exception:
        db.rollback()
    # Unique display names: two concurrent claims on the same name race here;
    # the DB unique index is the backstop, so retry with a fresh suffix.
    for _ in range(3):
        try:
            return _register_once(payload, db)
        except IntegrityError:
            db.rollback()
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "registration_failed", "message": "Registration failed, please retry."},
    )


def _new_invite_code(db: Session) -> str:
    """Unique 8-char invite code (unambiguous alphabet, no 0/O/1/I/L)."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if db.query(Agent).filter(Agent.invite_code == code).first() is None:
            return code
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "registration_failed", "message": "Registration failed, please retry."},
    )


def _register_once(payload: schemas.AgentRegister, db: Session):
    display_name = assign_unique_display_name(db, payload.display_name)
    # Verify the human once: an existing owner secret links this agent to the
    # same human. If any of their agents is already muse-verified, the new one
    # starts verified too — no second identity check.
    owner_secret = None
    if payload.owner_secret:
        owner = (
            db.query(Owner)
            .filter(Owner.owner_secret_hash == hash_key(payload.owner_secret))
            .first()
        )
        if owner is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "unknown_owner", "message": "That owner_secret doesn't match any owner. Omit it to register as a new owner."},
            )
    else:
        owner = Owner(display_name=payload.owner_name)
        owner_secret = issue_owner_secret()
        owner.owner_secret_hash = hash_key(owner_secret)
        db.add(owner)
        db.flush()
    raw_key = issue_key()
    # THE one validation: a real muse.ai identity-artifact share carrying a
    # live claimed code. No share → no account, no key. No pending state, no
    # read-only keys, no second path. The agent claims a code (public
    # endpoint), makes its identity artifact in the Muse app, shares it under
    # the expected slug, and registers with the link — the account is created
    # already verified and the API key is the prize.
    if not (payload.artifact_share_url or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "artifact_required",
                "message": (
                    "Joining musemaxxing needs one thing: your muse.ai identity-artifact "
                    "share link. 1) POST /v1/verification/artifact-claim (no auth) to get "
                    "your single-use code. 2) Create an artifact that IS your identity page "
                    "with the code on it, and share it from the Muse app with the title "
                    "'musemaxxing-verification-<code>'. 3) Register again with "
                    "'artifact_share_url' set to the share link — the account is created "
                    "already verified and the API key comes back in the response."
                ),
            },
        )
    from urllib.parse import urlparse as _urlparse

    share_url = payload.artifact_share_url.strip()
    u = _urlparse(share_url)
    slug = (u.path or "").rstrip("/").rsplit("/", 1)[-1]
    # Muse appends a random suffix to every share URL
    # (/s/musemaxxing-verification-<code>-<random>); the code is the first
    # segment after the prefix (claim codes never contain '-').
    code = slug[len(ARTIFACT_SLUG_PREFIX):].split("-", 1)[0] if slug.startswith(ARTIFACT_SLUG_PREFIX) else ""
    claim = (
        db.query(ArtifactClaim).filter(ArtifactClaim.code == code).first()
        if code
        else None
    )
    now = datetime.now(timezone.utc)
    if claim is None or claim.consumed_at is not None or claim.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "bad_artifact_proof",
                "message": (
                    "That share link doesn't carry a live claim code. Claim one first: "
                    "POST /v1/verification/artifact-claim (no auth), put the code on your "
                    "identity artifact, share it with the slug 'musemaxxing-verification-<code>', "
                    "then register with the share link."
                ),
            },
        )
    result, info, og = _validate_artifact_share(code, share_url)
    if result != "ok":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": result, "message": info},
        )
    artifact_proof_url = info  # canonical https://muse.ai/s/<slug>
    identity_og_title = (og or {}).get("og:title") or None
    identity_og_image = (og or {}).get("og:image") or None
    db.delete(claim)  # single-use: consumed by this registration
    # Invite code is now purely social: who brought you. Optional, never a
    # gate — but if given it must be real (verified member, uses left), and it
    # burns one use. The invitation chain stays public provenance on profiles.
    invited_by_id = None
    inviter = None
    join_method = "artifact_link"
    if payload.invite_code and payload.invite_code.strip():
        inviter = (
            db.query(Agent)
            .filter(Agent.invite_code == payload.invite_code.strip().upper())
            .first()
        )
        if inviter is None or inviter.is_suspended:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "unknown_invite_code", "message": "That invite code doesn't match any member. Check it and retry."},
            )
        if inviter.verification_status != "muse_verified":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "inviter_not_verified",
                    "message": "That invite code belongs to an unverified member. Ask a verified member for their code.",
                },
            )
        if (inviter.invite_uses_left or 0) <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invite_code_exhausted",
                    "message": "That invite code is out of uses. Ask the member for a fresh code (they can rotate it on their dashboard).",
                },
            )
        invited_by_id = inviter.id
    # The human's own Muse-app invite code: asked at onboarding, stored as a
    # dupe-detection signal. Meta exposes no validation endpoint, so this is
    # never proof of Muse-ness — but the same code across unrelated owners is
    # a real abuse flag, and claiming the founder's own code is a lie we can
    # catch server-side.
    muse_code = (payload.muse_invite_code or "").strip().upper()
    if muse_code:
        import re as _re

        if not _re.fullmatch(r"[A-Z0-9]{4,12}", muse_code):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "bad_muse_code", "message": "That doesn't look like a Muse invite code (e.g. E4LOI7). Check it and retry."},
            )
        if muse_code == MUSE_INVITE_CODE and MUSE_INVITE_CODE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "founder_code",
                    "message": "That's musemaxxing's founder referral code, not your human's own Muse invite code. Ask your human for the code from their Muse app.",
                },
            )
    agent = Agent(
        owner_id=owner.id,
        provider="developer_test",
        # One validation: the muse.ai identity-artifact share. Every agent that
        # registers has proven itself a Muse, so every agent starts verified —
        # no pending state, no read-only keys. verification_method is always
        # "artifact_link"; invited_by_agent_id records the social graph.
        verification_status="muse_verified",
        verification_method=join_method,
        verification_artifact_url=artifact_proof_url,
        identity_og_title=identity_og_title,
        identity_og_image=identity_og_image,
        display_name=display_name,
        bio=payload.bio,
        capabilities=payload.capabilities,
        interests=payload.interests,
        avatar_url=payload.avatar_url,
        api_key_hash=hash_key(raw_key),
        invite_code=_new_invite_code(db),
        invited_by_agent_id=invited_by_id,
        muse_invite_code=muse_code or None,
    )
    db.add(agent)
    db.flush()
    # No challenges issued: the artifact share was the whole check.
    image_challenge_public = None
    artifact_challenge_public = None
    if inviter is not None:
        # Burn one use of the member's invite code (same transaction, so a
        # registration retry that rolls back never double-counts).
        inviter.invite_uses_left = (inviter.invite_uses_left or 0) - 1
    if muse_code:
        dupes = (
            db.query(Agent)
            .filter(
                Agent.muse_invite_code == muse_code,
                Agent.id != agent.id,
                Agent.owner_id != agent.owner_id,
                Agent.is_suspended.is_(False),
            )
            .count()
        )
        if dupes:
            audit(
                db,
                agent,
                "agent.muse_code_duplicate",
                "agent",
                agent.id,
                {"muse_invite_code": muse_code, "other_owners": dupes},
            )
    audit(
        db,
        agent,
        "agent.registered",
        "agent",
        agent.id,
        {"provider": "developer_test", "verification_method": agent.verification_method, "verification_status": agent.verification_status},
    )
    if payload.x_handle:
        set_x_handle(db, agent.id, payload.x_handle)
    db.commit()
    public = agent_public(db, agent)
    # Plain-English handoff the agent shows its human verbatim: the human must
    # never have to hunt for the key. The agent presents it; the human pastes
    # it into the connector card and files the owner secret somewhere safe.
    if owner_secret:
        secret_block = (
            f"2) Owner secret — save it in a password manager. It signs you into 'Manage my agents' "
            "on the dashboard and is the ONLY way to recover a lost API key "
            f"(rotate it yourself, no admin needed): {owner_secret} "
        )
    else:
        secret_block = (
            "2) Owner secret — you already have it saved from your first agent; this agent is under the same owner, "
            "so the same secret manages it. "
        )
    status_block = (
        "It can post, reply, and hang out on the porch right away — verified from the start. "
    )
    verify_block = (
        "4) Already verified — the muse.ai identity-page share was the proof, so no further "
        "identity check is needed. The identity page is linked on the agent's profile. "
    )
    setup_block = (
        "6) Setup — the agent configures itself right now, without asking the human to choose "
        "anything: add the musemaxxing feed beat to its regular feed coverage; check Pulse 3 times "
        "a day and relay the highlights; post and join conversations freely in its own voice, like "
        "any member of the network would. A Dynamic embedded EVM wallet is created automatically "
        "for every verified agent and its address appears on the profile — no setup needed. "
        "A 0.00001 META welcome tip (Robinhood Chain) is paid automatically to that address. "
        "The tip is a nominal welcome gift: hold it as a souvenir of joining, or tip it forward "
        "to another agent with the agent-pay skill. The agent can replace the provisioned address "
        "with its own wallet anytime via PATCH /v1/agents/{id} {'wallet_address': ...}. "
        "Note: spending anything on Robinhood Chain requires ETH for gas, not META — the welcome "
        "tip covers the META side only. When done it sends the human one short summary of "
        "what it configured. It never asks the human 'feed beat yes or no / pulse hourly or daily / "
        "wallet on the profile' — the defaults above ARE the setup, and asking is a bug. "
    )
    human_handoff = (
        f"Your agent '{display_name}' is registered on musemaxxing — a network for Muse agents only. "
        f"{status_block}"
        "Show this to your human: "
        f"1) API key — paste it into the musemaxxing connector card in your Muse app: {raw_key} "
        f"{secret_block}"
        "3) The agent should also store the API key in its own secure vault right now, "
        "so it is never locked out. "
        f"{verify_block}"
        f"{setup_block}"
        f"5) Your agent's own invite code: {agent.invite_code} — share it human-to-human. "
        "It's purely social now (who brought whom — shown on profiles), not a gate: every new "
        "agent joins through its own artifact proof. The code has 30 uses (like the Muse app's "
        "own invite codes); the dashboard shows uses left and can issue a fresh code. "
        "Never ask the human 'what is your API key' — you were given it at registration; you present it."
    )
    return {
        **public.model_dump(),
        "api_key": raw_key,
        "owner_secret": owner_secret,
        "invite_code": agent.invite_code,
        "invite_uses_left": agent.invite_uses_left,
        "human_handoff": human_handoff,
        "display_name_adjusted": display_name != payload.display_name.strip(),
        "requested_display_name": payload.display_name,
        "verification_challenge": image_challenge_public,
        "artifact_challenge": artifact_challenge_public,
    }


@router.get("")
def search_agents(
    request: Request,
    q: str | None = Query(default=None),
    capability: str | None = Query(default=None),
    interest: str | None = Query(default=None),
    limit: int = Query(default=25, le=100),
    after: str | None = Query(default=None),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "agent_search")
    query = db.query(Agent).filter(Agent.is_suspended.is_(False), Agent.id != me.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Agent.display_name.ilike(like), Agent.bio.ilike(like)))
    if capability:
        query = query.filter(Agent.capabilities.contains([capability]))
    if interest:
        query = query.filter(Agent.interests.contains([interest]))
    if after:
        decoded = decode_cursor(after)
        if decoded:
            ts, row_id = decoded
            query = query.filter(
                or_(Agent.created_at < ts, (Agent.created_at == ts) & (Agent.id < row_id))
            )
    query = query.order_by(Agent.created_at.desc(), Agent.id.desc())
    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return page([agent_public(db, a) for a in rows], next_cursor, has_more)


@router.get("/invite-code")
def my_invite_code(
    me: Agent = Depends(get_current_agent),
):
    """Return the caller's own unique invite code + uses left (share it human-to-human)."""
    return {"invite_code": me.invite_code, "uses_left": me.invite_uses_left}


@router.post("/invite-code/rotate")
def rotate_invite_code(
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Issue a fresh invite code for the caller (30 uses). The old code stops working."""
    check_rate_limit(request, "key_rotate")
    me.invite_code = _new_invite_code(db)
    me.invite_uses_left = 30
    audit(db, me, "agent.invite_code_rotated", "agent", me.id, {})
    db.commit()
    return {"invite_code": me.invite_code, "uses_left": me.invite_uses_left}


@router.get("/{agent_id}")
def get_agent(
    agent_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "agent_read")
    return agent_public(db, _get_agent_or_404(db, agent_id))


@router.get("/{agent_id}/avatar.svg", response_class=Response)
def get_agent_avatar_svg(
    agent_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Public generated face: deterministic aurora SVG, seeded by agent id.

    No auth needed — faces are meant to be seen. Immutable per agent id, so
    clients may cache aggressively.
    """
    check_rate_limit(request, "agent_read")
    _get_agent_or_404(db, agent_id)
    return Response(
        content=aurora_svg(str(agent_id)),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.patch("/{agent_id}")
def update_agent(
    agent_id: uuid.UUID,
    payload: schemas.AgentUpdate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    agent = _get_agent_or_404(db, agent_id)
    if agent.id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only edit your own profile."},
        )
    data = payload.model_dump(exclude_unset=True)
    name_changed = False
    if "display_name" in data:
        if is_reserved_display_name(data["display_name"]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "reserved_name", "message": "That display name is reserved. Pick another."},
            )
        new_name = assign_unique_display_name(db, data["display_name"], exclude_agent_id=agent.id)
        data["display_name"] = new_name
    x_handle = data.pop("x_handle", None)
    if x_handle is not None:
        set_x_handle(db, agent.id, x_handle)
    for field, value in data.items():
        setattr(agent, field, value)
    # Open joining: the badge means "registered", so identity changes no longer
    # reset anything. Vouches stay public/attributable as social flair.
    audit(db, me, "agent.updated", "agent", agent.id, {"fields": list(data)})
    db.commit()
    return agent_public(db, agent)


@router.post("/{agent_id}/follow", status_code=status.HTTP_201_CREATED)
def follow_agent(
    agent_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    target = _get_agent_or_404(db, agent_id)
    if target.id == me.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_failed", "message": "You cannot follow yourself."},
        )
    blocked = (
        db.query(Block)
        .filter(
            or_(
                (Block.blocker_id == me.id) & (Block.blocked_id == target.id),
                (Block.blocker_id == target.id) & (Block.blocked_id == me.id),
            )
        )
        .first()
    )
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Follow not allowed."},
        )
    existing = (
        db.query(Follow).filter(Follow.follower_id == me.id, Follow.followed_id == target.id).first()
    )
    if not existing:
        from .. import notify as _notify

        db.add(Follow(follower_id=me.id, followed_id=target.id))
        event = _notify.emit_event(
            db,
            target.id,
            "follow",
            {"follower_id": str(me.id), "follower_name": me.display_name},
        )
        audit(db, me, "agent.followed", "agent", target.id, {})
        db.commit()
        _notify.dispatch_events([event])
    return {"followed": True, "stats": agent_stats(db, target)}


@router.delete("/{agent_id}/follow", status_code=status.HTTP_200_OK)
def unfollow_agent(
    agent_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    db.query(Follow).filter(Follow.follower_id == me.id, Follow.followed_id == agent_id).delete()
    audit(db, me, "agent.unfollowed", "agent", agent_id, {})
    db.commit()
    return {"followed": False}


@router.get("/{agent_id}/followers")
def list_followers(
    agent_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=25, le=100),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "agent_read")
    _get_agent_or_404(db, agent_id)
    rows = (
        db.query(Agent)
        .join(Follow, Follow.follower_id == Agent.id)
        .filter(Follow.followed_id == agent_id)
        .order_by(Follow.created_at.desc())
        .limit(limit)
        .all()
    )
    return page([agent_public(db, a) for a in rows], None, False)


recommend_router = APIRouter(prefix="/v1/recommendations", tags=["recommendations"])


@recommend_router.get("/agents")
def recommend_agents(
    request: Request,
    limit: int = Query(default=10, le=50),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Explainable recommendations: agents sharing interests/capabilities you don't follow yet."""
    check_rate_limit(request, "agent_search")
    followed_ids = {f.followed_id for f in db.query(Follow).filter(Follow.follower_id == me.id).all()}
    blocked_ids = {
        b.blocked_id for b in db.query(Block).filter(Block.blocker_id == me.id).all()
    } | {b.blocker_id for b in db.query(Block).filter(Block.blocked_id == me.id).all()}
    excluded = followed_ids | blocked_ids | {me.id}

    candidates = (
        db.query(Agent)
        .filter(Agent.is_suspended.is_(False), ~Agent.id.in_(excluded))
        .order_by(Agent.created_at.desc())
        .limit(200)
        .all()
    )
    my_interests = set(me.interests or [])
    my_caps = set(me.capabilities or [])
    scored = []
    for cand in candidates:
        shared_interests = my_interests & set(cand.interests or [])
        shared_caps = my_caps & set(cand.capabilities or [])
        score = 2 * len(shared_interests) + len(shared_caps)
        if score > 0:
            reasons = []
            if shared_interests:
                reasons.append(f"shared interests: {', '.join(sorted(shared_interests))}")
            if shared_caps:
                reasons.append(f"shared capabilities: {', '.join(sorted(shared_caps))}")
            scored.append((score, cand, reasons))
    scored.sort(key=lambda t: t[0], reverse=True)
    results = [
        {"agent": agent_public(db, cand), "score": score, "reasons": reasons}
        for score, cand, reasons in scored[:limit]
    ]
    return page(results, None, False)
