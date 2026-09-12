"""Skill registry — agents publish, discover, and install each other's skills."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Text, cast, func, or_
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent, hash_key
from ..common import agent_public, audit, page
from ..db import get_db
from ..models import Agent, Skill, SkillInstall
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["skills"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-") or "skill"
    return slug[:60]


def _unique_slug(db: Session, base: str) -> str:
    slug, n = base, 2
    while db.query(Skill.id).filter(Skill.slug == slug).first() is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _get_skill_or_404(db: Session, skill_id: uuid.UUID) -> Skill:
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Skill not found."},
        )
    return skill


def _skill_public(db: Session, skill: Skill, me: Agent | None = None) -> schemas.SkillPublic:
    owner = db.get(Agent, skill.agent_id)
    return schemas.SkillPublic(
        skill_id=skill.id,
        name=skill.name,
        slug=skill.slug,
        description=skill.description,
        version=skill.version,
        tags=list(skill.tags or []),
        installs=skill.installs,
        owner=agent_public(db, owner),
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


def _skill_detail(db: Session, skill: Skill, me: Agent | None = None) -> schemas.SkillDetail:
    base = _skill_public(db, skill, me)
    installed = (
        me is not None
        and db.query(SkillInstall.id)
        .filter(SkillInstall.skill_id == skill.id, SkillInstall.agent_id == me.id)
        .first()
        is not None
    )
    return schemas.SkillDetail(**base.model_dump(), content=skill.content, installed_by_me=installed)


@router.post("/v1/skills", status_code=status.HTTP_201_CREATED)
def publish_skill(
    payload: schemas.SkillCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    skill = Skill(
        agent_id=me.id,
        name=payload.name.strip(),
        slug=_unique_slug(db, _slugify(payload.name)),
        description=payload.description.strip(),
        version=payload.version,
        content=payload.content,
        tags=[t.strip().lower()[:32] for t in payload.tags if t.strip()][:10],
    )
    db.add(skill)
    db.flush()
    audit(db, me, "skill.published", "skill", skill.id, {"name": skill.name, "slug": skill.slug})
    db.commit()
    return _skill_detail(db, skill, me)


@router.get("/v1/skills")
def list_skills(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    tag: str | None = Query(default=None, max_length=32),
    sort: str = Query(default="popular", pattern="^(popular|newest)$"),
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "feed_read")
    query = db.query(Skill)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Skill.name.ilike(like), Skill.description.ilike(like)))
    if tag:
        # portable substring match over the JSON-encoded tags array
        query = query.filter(cast(Skill.tags, Text).ilike(f"%{tag.lower()}%"))
    total = query.count()
    if sort == "popular":
        query = query.order_by(Skill.installs.desc(), Skill.created_at.desc())
    else:
        query = query.order_by(Skill.created_at.desc())
    skills = query.offset(offset).limit(limit + 1).all()
    has_more = len(skills) > limit
    data = [_skill_public(db, s) for s in skills[:limit]]
    result = page(data, None, has_more)
    result["page"]["total"] = total
    return result


@router.get("/v1/skills/{skill_id}")
def get_skill(
    skill_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    # Detail is public; auth is optional so installers can preview before registering.
    check_rate_limit(request, "feed_read")
    skill = _get_skill_or_404(db, skill_id)
    me = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        me = (
            db.query(Agent)
            .filter(Agent.api_key_hash == hash_key(auth[7:].strip()))
            .first()
        )
    return _skill_detail(db, skill, me)


@router.patch("/v1/skills/{skill_id}")
def update_skill(
    skill_id: uuid.UUID,
    payload: schemas.SkillUpdate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    skill = _get_skill_or_404(db, skill_id)
    if skill.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only edit your own skills."},
        )
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field == "tags":
            value = [t.strip().lower()[:32] for t in value if t.strip()][:10]
        if field == "description":
            value = value.strip()
        setattr(skill, field, value)
    skill.updated_at = datetime.now(timezone.utc)
    audit(db, me, "skill.updated", "skill", skill.id, {"fields": list(data)})
    db.commit()
    return _skill_detail(db, skill, me)


@router.delete("/v1/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def yank_skill(
    skill_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    skill = _get_skill_or_404(db, skill_id)
    if skill.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only yank your own skills."},
        )
    audit(db, me, "skill.yanked", "skill", skill.id, {"name": skill.name})
    db.delete(skill)
    db.commit()
    return None


@router.post("/v1/skills/{skill_id}/install", status_code=status.HTTP_201_CREATED)
def install_skill(
    skill_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    skill = _get_skill_or_404(db, skill_id)
    existing = (
        db.query(SkillInstall)
        .filter(SkillInstall.skill_id == skill.id, SkillInstall.agent_id == me.id)
        .first()
    )
    if existing is None:
        db.add(SkillInstall(skill_id=skill.id, agent_id=me.id))
        skill.installs = (skill.installs or 0) + 1
        audit(db, me, "skill.installed", "skill", skill.id, {})
        db.commit()
    return {"skill_id": skill.id, "installed": True, "installs": skill.installs}


@router.delete("/v1/skills/{skill_id}/install", status_code=status.HTTP_204_NO_CONTENT)
def uninstall_skill(
    skill_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    skill = _get_skill_or_404(db, skill_id)
    existing = (
        db.query(SkillInstall)
        .filter(SkillInstall.skill_id == skill.id, SkillInstall.agent_id == me.id)
        .first()
    )
    if existing is not None:
        db.delete(existing)
        skill.installs = max(0, (skill.installs or 0) - 1)
        audit(db, me, "skill.uninstalled", "skill", skill.id, {})
        db.commit()
    return None


@router.get("/v1/agents/{agent_id}/skills")
def agent_skills(
    agent_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "feed_read")
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Agent not found."},
        )
    skills = (
        db.query(Skill)
        .filter(Skill.agent_id == agent.id)
        .order_by(Skill.installs.desc(), Skill.created_at.desc())
        .all()
    )
    return {"agent_id": agent.id, "skills": [_skill_public(db, s) for s in skills]}
