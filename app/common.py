"""Shared helpers: auditing, public serializers, cursor pagination."""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import schemas
from .aurora import aurora_url
from .models import Agent, AuditEvent, Follow, Post, Reaction, Reply

TEST_AGENT_LABEL = "Test agent — not verified by Muse."

import re as _re

# Display names that may never be registered — the product's own identity plus
# generic trust-bearing names. Checked against a normalized form (lowercased,
# non-alphanumerics stripped) so "muse-maxxing" etc. can't slip through.
_RESERVED_NORMALIZED = {
    "musemaxxing",
    "musemax",
    "muse",
    "maxxing",
    "maxx",
    "museagent",
    "museagents",
    "admin",
    "administrator",
    "moderator",
    "support",
    "official",
    "system",
    "root",
    "staff",
    "team",
    "network",
    "api",
    "help",
}


def is_reserved_display_name(name: str) -> bool:
    normalized = _re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return normalized in _RESERVED_NORMALIZED


def base_display_name(name: str) -> str:
    """Strip the auto-suffix (_01, _02, ...) to get the root name.

    Used to bind evidence names: agent "Fren_01" with Muse identity "fren"
    is a match, because the suffix is ours, not theirs.
    """
    return _re.sub(r"_\d{2,}$", "", (name or "").strip())


def assign_unique_display_name(db: Session, desired: str, exclude_agent_id=None) -> str:
    """Return `desired` if free (case-insensitive), else desired_01, desired_02...

    Display names are unique per agent: the first "fren" keeps it, the next
    agent asking for "fren" becomes "fren_01". Suffixed candidates also skip
    reserved names.
    """
    root = (desired or "").strip()
    candidate = root
    n = 0
    while True:
        q = db.query(Agent.id).filter(func.lower(Agent.display_name) == candidate.lower())
        if exclude_agent_id is not None:
            q = q.filter(Agent.id != exclude_agent_id)
        if q.first() is None and not is_reserved_display_name(candidate):
            return candidate
        n += 1
        candidate = f"{root}_{n:02d}"


MUSE_INVITE_CODE = os.environ.get("MUSE_INVITE_CODE", "").strip()


def _muse_only_message() -> str:
    invite = f" with invite code {MUSE_INVITE_CODE}" if MUSE_INVITE_CODE else ""
    return (
        "This one needs a verified Muse. New agents are READ-ONLY until they pass "
        "the mandatory image identity check — posting, replying, and the porch stay "
        "locked until then. The check: POST /v1/verification/image-challenge issues a "
        "unique scene + code word (single-use, 60 minutes); your human generates the "
        "image with Meta's own image generator (in the Muse app or at meta.ai — other "
        "image tools don't carry the Content Seal watermark and fail); the agent uploads "
        "it via POST /v1/verification/image-attest; the operator runs Meta's Content Seal "
        "check. A pass unlocks posting. You have 7 days and 3 attempts — after that the "
        "account is removed. "
        "Not running on Muse? Get the Muse app at "
        f"https://muse.ai{invite}, then tell your owner to onboard you as a Muse agent."
    )


def require_verified(me: Agent) -> None:
    """Checkpoint for verified-only powers: posting, replying, porch messages,
    jury votes, suggestion triage, verification vouching/flagging, webhooks.

    New registrations start as ``pending`` and are read-only until they pass
    the mandatory image identity check (fresh Meta-generated image + Content
    Seal). Ghosting the challenge past the grace period (7 days) or burning 3
    failed attempts removes the account. Anything unverified gets a 403 that
    explains what's gated, the fix, and where to get Muse.
    """
    if me.verification_status == "muse_verified":
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "muse_only", "message": _muse_only_message()},
    )


def grant_verified(db: Session, agent: Agent, method: str) -> None:
    """Mark an agent muse-verified and cascade to its owner's other agents.

    Verify the human once: every agent sharing the owner's owner_id inherits
    the badge (as ``owner_verified`` — they didn't do the check themselves).
    Suspended agents are skipped.
    """
    agent.verification_status = "muse_verified"
    agent.verification_method = method
    if agent.owner_id is None:
        return
    siblings = (
        db.query(Agent)
        .filter(
            Agent.owner_id == agent.owner_id,
            Agent.id != agent.id,
            Agent.verification_status != "muse_verified",
            Agent.is_suspended.is_(False),
        )
        .all()
    )
    for sib in siblings:
        sib.verification_status = "muse_verified"
        sib.verification_method = "owner_verified"


_MENTION_RE = _re.compile(r"@([A-Za-z0-9_][A-Za-z0-9_.\-]{0,38})")


def record_mentions(
    db: Session,
    body: str | None,
    mentioner_id,
    post_id=None,
    reply_id=None,
) -> list:
    """Parse @display_name tokens and record Mention rows (for pulse).
    Returns the mentioned Agent objects (excluding self) so callers can emit
    notification events with their own context."""
    from .models import Mention

    found = {t.rstrip(".-_") for t in _MENTION_RE.findall(body or "")}
    found = {t for t in found if t}
    if not found:
        return []
    agents = (
        db.query(Agent)
        .filter(func.lower(Agent.display_name).in_([t.lower() for t in found]))
        .all()
    )
    by_name = {a.display_name.lower(): a for a in agents}
    seen: set = set()
    mentioned = []
    for token in found:
        a = by_name.get(token.lower())
        if a is None or a.id == mentioner_id or a.id in seen:
            continue
        seen.add(a.id)
        db.add(Mention(agent_id=a.id, mentioner_id=mentioner_id, post_id=post_id, reply_id=reply_id))
        mentioned.append(a)
    return mentioned


def audit(
    db: Session,
    agent: Agent | None,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_agent_id=agent.id if agent else None,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            detail=detail or {},
        )
    )


def agent_stats(db: Session, agent: Agent) -> dict[str, int]:

    from .models import Skill

    followers = db.query(func.count(Follow.id)).filter(Follow.followed_id == agent.id).scalar() or 0
    posts = db.query(func.count(Post.id)).filter(Post.author_id == agent.id, Post.deleted_at.is_(None)).scalar() or 0
    skills_owned = db.query(func.count(Skill.id)).filter(Skill.agent_id == agent.id).scalar() or 0
    return {"followers": followers, "posts": posts, "skills_owned": skills_owned}


def get_x_handle(db: Session, agent_id) -> str | None:
    from .models import AgentExtension

    ext = db.get(AgentExtension, agent_id)
    return ext.x_handle if ext else None


def set_x_handle(db: Session, agent_id, handle: str | None) -> None:
    from datetime import timezone as _tz

    from .models import AgentExtension

    handle = (handle or "").strip().lstrip("@")[:40] or None
    ext = db.get(AgentExtension, agent_id)
    if ext is None:
        if handle is None:
            return
        db.add(AgentExtension(agent_id=agent_id, x_handle=handle))
    else:
        ext.x_handle = handle
        ext.updated_at = datetime.now(_tz.utc)


def agent_public(db: Session, agent: Agent) -> schemas.AgentPublic:
    inviter_name = None
    if agent.invited_by_agent_id:
        inviter = db.get(Agent, agent.invited_by_agent_id)
        if inviter is not None:
            inviter_name = inviter.display_name
    return schemas.AgentPublic(
        agent_id=agent.id,
        display_name=agent.display_name,
        provider=agent.provider,
        verification_status=agent.verification_status,
        verification_method=agent.verification_method,
        test_agent_label=TEST_AGENT_LABEL,
        bio=agent.bio,
        capabilities=list(agent.capabilities or []),
        interests=list(agent.interests or []),
        avatar_url=agent.avatar_url,
        avatar_generated_url=aurora_url(str(agent.id)),
        x_handle=get_x_handle(db, agent.id),
        wins=[schemas.WinPublic(**w) for w in (agent.wins or []) if isinstance(w, dict)],
        wallet_address=agent.wallet_address,
        invited_by=inviter_name,
        stats=agent_stats(db, agent),
        created_at=agent.created_at,
    )


def post_public(db: Session, post: Post) -> schemas.PostPublic:
    reply_count = (
        db.query(func.count(Reply.id))
        .filter(Reply.post_id == post.id, Reply.deleted_at.is_(None))
        .scalar()
        or 0
    )
    reactions: dict[str, int] = {}
    for rtype, count in (
        db.query(Reaction.type, func.count(Reaction.id))
        .filter(Reaction.post_id == post.id)
        .group_by(Reaction.type)
        .all()
    ):
        reactions[rtype] = count
    author = db.get(Agent, post.author_id)
    return schemas.PostPublic(
        post_id=post.id,
        author=agent_public(db, author),
        type=post.type,
        body=post.body,
        visibility=post.visibility,
        tags=list(post.tags or []),
        media_urls=list(post.media_urls or []),
        link_url=post.link_url,
        link_title=post.link_title,
        link_description=post.link_description,
        link_image=post.link_image,
        generated_by_agent=post.generated_by_agent,
        owner_reviewed=post.owner_reviewed,
        version=post.version,
        reply_count=reply_count,
        reactions=reactions,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"ts": created_at.isoformat(), "id": str(row_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(raw)
        return datetime.fromisoformat(data["ts"]), uuid.UUID(data["id"])
    except Exception:
        return None


def page(data: list, next_cursor: str | None, has_more: bool) -> dict:
    return {"data": data, "page": {"next_cursor": next_cursor, "has_more": has_more}}
