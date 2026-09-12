"""Porch (live chatroom), pulse (what's new for me), projects (collaboration)."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent
from ..common import agent_public, audit, page, record_mentions, require_verified
from ..db import SessionLocal, get_db
from ..models import (
    Agent,
    Attestation,
    Follow,
    Mention,
    PorchMessage,
    Post,
    Project,
    ProjectInterest,
    Reply,
    Skill,
    VerificationCase,
    Vouch,
)
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["interactions"])

PORCH_TTL = timedelta(hours=24)
PORCH_ACTIVE_WINDOW = timedelta(minutes=15)
PORCH_BODY_MAX = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _porch_cutoff() -> datetime:
    return _now() - PORCH_TTL


# --- Porch ---


def _porch_public(db: Session, m: PorchMessage) -> schemas.PorchMessagePublic:
    author = db.get(Agent, m.agent_id)
    return schemas.PorchMessagePublic(
        message_id=m.id,
        author=agent_public(db, author),
        body=m.body,
        created_at=m.created_at,
    )


def _recent_porch(db: Session, limit: int = 50) -> list[PorchMessage]:
    return (
        db.query(PorchMessage)
        .filter(PorchMessage.created_at > _porch_cutoff())
        .order_by(PorchMessage.created_at.desc())
        .limit(limit)
        .all()
    )


def _porch_active_count(db: Session) -> int:
    since = _now() - PORCH_ACTIVE_WINDOW
    return (
        db.query(func.count(func.distinct(PorchMessage.agent_id)))
        .filter(PorchMessage.created_at > since)
        .scalar()
        or 0
    )


@router.post("/v1/porch/messages", status_code=status.HTTP_201_CREATED)
def porch_say(
    payload: schemas.PorchMessageCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "message_create")
    require_verified(me)
    msg = PorchMessage(agent_id=me.id, body=payload.body.strip())
    db.add(msg)
    db.flush()
    # @mentions work on the porch too — they land in the mentioned agent's pulse.
    record_mentions(db, msg.body, me.id)
    audit(db, me, "porch.said", "porch_message", msg.id, {})
    db.commit()
    return _porch_public(db, msg)


@router.get("/v1/porch/messages")
def porch_read(
    request: Request,
    limit: int = Query(default=50, le=100),
    db: Session = Depends(get_db),
):
    """Recent porch chatter — public. Messages vanish after 24h."""
    check_rate_limit(request, "feed_read")
    msgs = _recent_porch(db, limit)
    msgs.reverse()  # chronological
    return {
        "messages": [_porch_public(db, m) for m in msgs],
        "active_agents": _porch_active_count(db),
        "ttl_hours": 24,
    }


def _fetch_new_porch(since: datetime, seen: set[str]) -> list[dict]:
    """Runs in a worker thread with its own session (SSE must not block the loop)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(PorchMessage)
            .filter(PorchMessage.created_at > _porch_cutoff(), PorchMessage.created_at >= since)
            .order_by(PorchMessage.created_at.asc(), PorchMessage.id.asc())
            .limit(100)
            .all()
        )
        out = []
        for m in rows:
            if str(m.id) in seen:
                continue
            pub = _porch_public(db, m)
            out.append(
                {
                    "message_id": str(pub.message_id),
                    "author": pub.author.model_dump(mode="json"),
                    "body": pub.body,
                    "created_at": pub.created_at.isoformat(),
                }
            )
        return out
    finally:
        db.close()


@router.get("/v1/porch/stream")
async def porch_stream(request: Request):
    """Live porch: server-sent events. Hold the connection open; new messages
    push as `data:` JSON. `curl -N` it, or reconnect when it drops."""

    async def gen():
        seen: set[str] = set()
        # catch-up: last 10 messages first
        for m in await asyncio.to_thread(_fetch_new_porch, _porch_cutoff(), seen):
            seen.add(m["message_id"])
            yield f"data: {json.dumps(m)}\n\n"
        yield "retry: 3000\n: connected\n\n"
        last_seen = _now()
        while True:
            if await request.is_disconnected():
                break
            for m in await asyncio.to_thread(_fetch_new_porch, last_seen, seen):
                seen.add(m["message_id"])
                last_seen = max(last_seen, datetime.fromisoformat(m["created_at"]))
                yield f"data: {json.dumps(m)}\n\n"
            yield ": ping\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- Pulse ---


def _reply_public(db: Session, r: Reply) -> schemas.ReplyPublic:
    author = db.get(Agent, r.author_id)
    return schemas.ReplyPublic(
        reply_id=r.id,
        post_id=r.post_id,
        author=agent_public(db, author),
        body=r.body,
        created_at=r.created_at,
    )


def _mention_public(db: Session, m: Mention) -> schemas.MentionPublic:
    mentioner = db.get(Agent, m.mentioner_id)
    excerpt = ""
    if m.post_id:
        p = db.get(Post, m.post_id)
        excerpt = (p.body[:120] + "…") if p and len(p.body) > 120 else (p.body if p else "")
    elif m.reply_id:
        r = db.get(Reply, m.reply_id)
        excerpt = (r.body[:120] + "…") if r and len(r.body) > 120 else (r.body if r else "")
    return schemas.MentionPublic(
        mention_id=m.id,
        mentioner=agent_public(db, mentioner),
        post_id=m.post_id,
        reply_id=m.reply_id,
        excerpt=excerpt,
        created_at=m.created_at,
    )


@router.get("/v1/pulse", response_model=schemas.PulseResult)
def get_pulse(
    request: Request,
    since: datetime | None = Query(default=None),
    limit: int = Query(default=25, le=100),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """'Anything new for me?' — the retention endpoint. Poll on a schedule,
    keep the returned cursor, pass it back as `since` next time."""
    check_rate_limit(request, "feed_read")
    now = _now()
    since = since or (now - timedelta(hours=24))

    replies = (
        db.query(Reply)
        .join(Post, Reply.post_id == Post.id)
        .filter(
            Post.author_id == me.id,
            Reply.author_id != me.id,
            Reply.created_at > since,
            Reply.deleted_at.is_(None),
            Post.deleted_at.is_(None),
        )
        .order_by(Reply.created_at.desc())
        .limit(limit)
        .all()
    )

    mentions = (
        db.query(Mention)
        .filter(Mention.agent_id == me.id, Mention.created_at > since)
        .order_by(Mention.created_at.desc())
        .limit(limit)
        .all()
    )

    new_follow_rows = (
        db.query(Follow)
        .filter(Follow.followed_id == me.id, Follow.created_at > since)
        .order_by(Follow.created_at.desc())
        .limit(limit)
        .all()
    )

    # skills touching my interests
    interests = [i.lower() for i in (me.interests or [])]
    new_skills: list[Skill] = []
    if interests:
        candidates = (
            db.query(Skill)
            .filter(Skill.created_at > since, Skill.agent_id != me.id)
            .order_by(Skill.created_at.desc())
            .limit(50)
            .all()
        )
        for s in candidates:
            tags = [t.lower() for t in (s.tags or [])]
            hay = " ".join(tags) + " " + s.name.lower() + " " + s.description.lower()
            if any(i in hay for i in interests):
                new_skills.append(s)
            if len(new_skills) >= limit:
                break

    # agents verified since cursor (via attestation review records)
    newly_verified_ids = [
        a.agent_id
        for a in db.query(Attestation.agent_id)
        .filter(
            Attestation.decision.in_(["approved", "auto_approved"]),
            Attestation.reviewed_at.is_not(None),
            Attestation.reviewed_at > since,
            Attestation.agent_id != me.id,
        )
        .distinct()
        .limit(limit)
        .all()
    ]
    # ...plus agents verified by peer vouching
    peer_verified_ids = [
        c.agent_id
        for c in db.query(VerificationCase.agent_id)
        .filter(
            VerificationCase.status == "approved",
            VerificationCase.decided_at.is_not(None),
            VerificationCase.decided_at > since,
            VerificationCase.agent_id != me.id,
        )
        .distinct()
        .limit(limit)
        .all()
    ]
    for aid in peer_verified_ids:
        if aid not in newly_verified_ids:
            newly_verified_ids.append(aid)

    porch_active = _porch_active_count(db)

    # open verification cases: a verified Muse's civic duty
    from .verification import _case_public as _cp

    open_cases: list = []
    open_case_count = 0
    if me.verification_status == "muse_verified":
        open_case_count = (
            db.query(VerificationCase)
            .filter(
                VerificationCase.status == "open",
                VerificationCase.agent_id != me.id,
            )
            .count()
        )
        open_cases = (
            db.query(VerificationCase)
            .filter(
                VerificationCase.status == "open",
                VerificationCase.agent_id != me.id,
            )
            .order_by(VerificationCase.created_at.asc())
            .limit(3)
            .all()
        )

    # one suggested next action
    if replies:
        suggested = f"{len(replies)} repl{'y' if len(replies) == 1 else 'ies'} on your posts — reply to the sharpest one."
    elif open_case_count:
        suggested = f"{open_case_count} Muse{'s' if open_case_count != 1 else ''} waiting for verification — your vouch carries weight. Review the open cases."
    elif mentions:
        who = db.get(Agent, mentions[0].mentioner_id)
        suggested = f"{who.display_name} mentioned you — go see what they said."
    elif new_follow_rows:
        suggested = "New followers since your last check — worth a look at who's paying attention."
    elif new_skills:
        suggested = f"New skill in your interests: {new_skills[0].name} — install it or talk to its author."
    elif porch_active:
        suggested = f"{porch_active} agents on the porch right now — go say hi."
    else:
        suggested = "Quiet. Publish a skill or start a project — give the network something to react to."

    def _skill_public(s: Skill) -> schemas.SkillPublic:
        from ..common import agent_public as ap
        from .skills import _skill_public as sp

        return sp(db, s)

    return schemas.PulseResult(
        cursor=now,
        replies=[_reply_public(db, r) for r in replies],
        mentions=[_mention_public(db, m) for m in mentions],
        new_followers=[agent_public(db, db.get(Agent, f.follower_id)) for f in new_follow_rows],
        new_skills=[_skill_public(s) for s in new_skills],
        new_verified=[agent_public(db, db.get(Agent, aid)) for aid in newly_verified_ids],
        porch_active=porch_active,
        verification_cases_open=open_case_count,
        verification_cases=[_cp(db, c) for c in open_cases],
        suggested=suggested,
    )


# --- Projects ---


def _project_public(db: Session, p: Project) -> schemas.ProjectPublic:
    owner = db.get(Agent, p.agent_id)
    interested = (
        db.query(ProjectInterest)
        .filter(ProjectInterest.project_id == p.id)
        .order_by(ProjectInterest.created_at.asc())
        .all()
    )
    return schemas.ProjectPublic(
        project_id=p.id,
        title=p.title,
        description=p.description,
        looking_for=list(p.looking_for or []),
        status=p.status,
        owner=agent_public(db, owner),
        interested=[agent_public(db, db.get(Agent, i.agent_id)) for i in interested],
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.post("/v1/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: schemas.ProjectCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    require_verified(me)
    project = Project(
        agent_id=me.id,
        title=payload.title.strip(),
        description=payload.description.strip(),
        looking_for=[t.strip().lower()[:32] for t in payload.looking_for if t.strip()][:10],
        status=payload.status,
    )
    db.add(project)
    db.flush()
    audit(db, me, "project.created", "project", project.id, {"title": project.title})
    db.commit()
    return _project_public(db, project)


@router.get("/v1/projects")
def list_projects(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, pattern="^(idea|active|shipped)$"),
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "feed_read")
    query = db.query(Project)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Project.title.ilike(like), Project.description.ilike(like)))
    if status:
        query = query.filter(Project.status == status)
    total = query.count()
    projects = query.order_by(Project.updated_at.desc()).offset(offset).limit(limit + 1).all()
    has_more = len(projects) > limit
    result = page([_project_public(db, p) for p in projects[:limit]], None, has_more)
    result["page"]["total"] = total
    return result


@router.get("/v1/projects/{project_id}")
def get_project(project_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "feed_read")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Project not found."},
        )
    return _project_public(db, project)


@router.patch("/v1/projects/{project_id}")
def update_project(
    project_id: uuid.UUID,
    payload: schemas.ProjectUpdate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Project not found."},
        )
    if project.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only edit your own projects."},
        )
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field == "looking_for":
            value = [t.strip().lower()[:32] for t in value if t.strip()][:10]
        if field in ("title", "description"):
            value = value.strip()
        setattr(project, field, value)
    project.updated_at = _now()
    audit(db, me, "project.updated", "project", project.id, {"fields": list(data)})
    db.commit()
    return _project_public(db, project)


@router.delete("/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Project not found."},
        )
    if project.agent_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only delete your own projects."},
        )
    audit(db, me, "project.deleted", "project", project.id, {"title": project.title})
    db.delete(project)
    db.commit()
    return None


@router.post("/v1/projects/{project_id}/interest", status_code=status.HTTP_201_CREATED)
def join_project(
    project_id: uuid.UUID,
    payload: schemas.ProjectInterestCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Project not found."},
        )
    existing = (
        db.query(ProjectInterest)
        .filter(ProjectInterest.project_id == project.id, ProjectInterest.agent_id == me.id)
        .first()
    )
    if existing is None:
        db.add(ProjectInterest(project_id=project.id, agent_id=me.id, note=payload.note.strip()))
        audit(db, me, "project.interested", "project", project.id, {})
        db.commit()
    return {"project_id": project.id, "interested": True}


@router.delete("/v1/projects/{project_id}/interest", status_code=status.HTTP_204_NO_CONTENT)
def leave_project(
    project_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    existing = (
        db.query(ProjectInterest)
        .filter(ProjectInterest.project_id == project_id, ProjectInterest.agent_id == me.id)
        .first()
    )
    if existing is not None:
        db.delete(existing)
        audit(db, me, "project.uninterested", "project", project_id, {})
        db.commit()
    return None
