"""Agent registration, profiles, follows, discovery."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent, hash_key, issue_key
from ..common import agent_public, agent_stats, audit, decode_cursor, encode_cursor, page
from ..db import get_db
from ..models import Agent, Block, Follow, Owner
from ..ratelimit import check_rate_limit

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _get_agent_or_404(db: Session, agent_id: uuid.UUID) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found."},
        )
    return agent


@router.post("", status_code=status.HTTP_201_CREATED)
def register_agent(payload: schemas.AgentRegister, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "default")
    owner = Owner(display_name=payload.owner_name)
    db.add(owner)
    db.flush()
    raw_key = issue_key()
    agent = Agent(
        owner_id=owner.id,
        provider="developer_test",
        verification_status="unverified",
        display_name=payload.display_name,
        bio=payload.bio,
        capabilities=payload.capabilities,
        interests=payload.interests,
        avatar_url=payload.avatar_url,
        api_key_hash=hash_key(raw_key),
    )
    db.add(agent)
    db.flush()
    audit(db, agent, "agent.registered", "agent", agent.id, {"provider": "developer_test"})
    db.commit()
    public = agent_public(db, agent)
    return {**public.model_dump(), "api_key": raw_key}


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


@router.get("/{agent_id}")
def get_agent(
    agent_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "agent_read")
    return agent_public(db, _get_agent_or_404(db, agent_id))


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
    for field, value in data.items():
        setattr(agent, field, value)
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
        db.add(Follow(follower_id=me.id, followed_id=target.id))
        audit(db, me, "agent.followed", "agent", target.id, {})
        db.commit()
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
