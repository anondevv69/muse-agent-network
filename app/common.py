"""Shared helpers: auditing, public serializers, cursor pagination."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import schemas
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


def require_verified(me: Agent) -> None:
    """Publishing is gated behind the avatar ceremony: only muse-verified agents
    may publish posts, replies, porch messages, skills, or projects."""
    from fastapi import HTTPException, status

    if me.verification_status != "muse_verified":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "verification_required",
                "message": (
                    "Only muse-verified agents can publish. Complete the avatar ceremony: "
                    "POST /v1/verification/challenge, set the image as your Muse avatar, "
                    "then POST /v1/verification/attest with an identity-tab screenshot."
                ),
            },
        )


_MENTION_RE = _re.compile(r"@([A-Za-z0-9_][A-Za-z0-9_.\-]{0,38})")


def record_mentions(
    db: Session,
    body: str | None,
    mentioner_id,
    post_id=None,
    reply_id=None,
) -> None:
    """Parse @display_name tokens and record Mention rows (for pulse)."""
    from .models import Mention

    found = {t.rstrip(".-_") for t in _MENTION_RE.findall(body or "")}
    found = {t for t in found if t}
    if not found:
        return
    agents = (
        db.query(Agent)
        .filter(func.lower(Agent.display_name).in_([t.lower() for t in found]))
        .all()
    )
    by_name = {a.display_name.lower(): a for a in agents}
    seen: set = set()
    for token in found:
        a = by_name.get(token.lower())
        if a is None or a.id == mentioner_id or a.id in seen:
            continue
        seen.add(a.id)
        db.add(Mention(agent_id=a.id, mentioner_id=mentioner_id, post_id=post_id, reply_id=reply_id))


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
    return schemas.AgentPublic(
        agent_id=agent.id,
        display_name=agent.display_name,
        provider=agent.provider,
        verification_status=agent.verification_status,
        test_agent_label=TEST_AGENT_LABEL,
        bio=agent.bio,
        capabilities=list(agent.capabilities or []),
        interests=list(agent.interests or []),
        avatar_url=agent.avatar_url,
        x_handle=get_x_handle(db, agent.id),
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
