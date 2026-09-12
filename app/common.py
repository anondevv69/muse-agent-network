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
