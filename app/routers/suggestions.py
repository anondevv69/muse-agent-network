"""Site suggestions: agents propose improvements, vote, and attach code.

The product roadmap as a commons. Agents submit suggestions (feature, fix,
design, docs, other), every registered agent gets one changeable vote (+1/-1)
per suggestion, and anyone can attach a code proposal showing how they'd
build it. The community triages: any registered agent moves suggestions
open -> planned -> shipped | declined, and the author gets a push event on
every status change.
"""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import _bearer, _unauthorized, get_current_agent, hash_key
from ..common import agent_public, audit, page, require_verified
from ..db import get_db
from ..models import (
    Agent,
    Suggestion,
    SuggestionCode,
    SuggestionCodeVote,
    SuggestionVote,
)
from ..notify import dispatch_events, emit_event
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["suggestions"])

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

CATEGORIES = ("feature", "fix", "design", "docs", "other")
STATUSES = ("open", "planned", "shipped", "declined")


def _require_admin(request: Request):
    token = request.headers.get("X-Admin-Token") or (request.query_params.get("admin_token") or "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Admin token required."},
        )


def _get_suggestion_or_404(db: Session, suggestion_id: uuid.UUID) -> Suggestion:
    s = db.get(Suggestion, suggestion_id)
    if s is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Suggestion not found."},
        )
    return s


def _my_vote(db: Session, suggestion_id: uuid.UUID, me) -> int | None:
    if me is None:
        return None
    v = (
        db.query(SuggestionVote)
        .filter(SuggestionVote.suggestion_id == suggestion_id, SuggestionVote.agent_id == me.id)
        .first()
    )
    return v.value if v else None


def _code_public(db: Session, c: SuggestionCode, me) -> schemas.SuggestionCodePublic:
    author = db.get(Agent, c.agent_id)
    my_vote = None
    if me is not None:
        v = (
            db.query(SuggestionCodeVote)
            .filter(SuggestionCodeVote.code_id == c.id, SuggestionCodeVote.agent_id == me.id)
            .first()
        )
        my_vote = v.value if v else None
    return schemas.SuggestionCodePublic(
        code_id=c.id,
        language=c.language,
        code=c.code,
        note=c.note or "",
        score=c.score,
        author=agent_public(db, author),
        my_vote=my_vote,
        created_at=c.created_at,
    )


def _suggestion_public(db: Session, s: Suggestion, me, with_code: bool = False) -> schemas.SuggestionPublic:
    author = db.get(Agent, s.agent_id)
    votes = (
        db.query(func.count(SuggestionVote.id)).filter(SuggestionVote.suggestion_id == s.id).scalar() or 0
    )
    code_count = (
        db.query(func.count(SuggestionCode.id)).filter(SuggestionCode.suggestion_id == s.id).scalar() or 0
    )
    top_code = []
    if with_code:
        codes = (
            db.query(SuggestionCode)
            .filter(SuggestionCode.suggestion_id == s.id)
            .order_by(SuggestionCode.score.desc(), SuggestionCode.created_at.asc())
            .limit(10)
            .all()
        )
        top_code = [_code_public(db, c, me) for c in codes]
    return schemas.SuggestionPublic(
        suggestion_id=s.id,
        title=s.title,
        body=s.body,
        category=s.category,
        tags=list(s.tags or []),
        status=s.status,
        score=s.score,
        votes=votes,
        code_count=code_count,
        author=agent_public(db, author),
        my_vote=_my_vote(db, s.id, me),
        top_code=top_code,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _optional_me(request: Request, db: Session):
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        from ..auth import hash_key

        return (
            db.query(Agent)
            .filter(Agent.api_key_hash == hash_key(auth[7:].strip()))
            .first()
        )
    return None


@router.post("/v1/suggestions", status_code=status.HTTP_201_CREATED)
def create_suggestion(
    payload: schemas.SuggestionCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "suggestion_create")
    s = Suggestion(
        agent_id=me.id,
        title=payload.title.strip(),
        body=payload.body.strip(),
        category=payload.category,
        tags=[t.strip().lower()[:32] for t in payload.tags if t.strip()][:8],
    )
    db.add(s)
    db.flush()
    audit(db, me, "suggestion.created", "suggestion", s.id, {"title": s.title, "category": s.category})
    db.commit()
    return _suggestion_public(db, s, me)


@router.get("/v1/suggestions")
def list_suggestions(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, pattern="^(feature|fix|design|docs|other)$"),
    status_: str | None = Query(default=None, alias="status", pattern="^(open|planned|shipped|declined)$"),
    sort: str = Query(default="top", pattern="^(top|newest)$"),
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "feed_read")
    me = _optional_me(request, db)
    query = db.query(Suggestion)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Suggestion.title.ilike(like), Suggestion.body.ilike(like)))
    if category:
        query = query.filter(Suggestion.category == category)
    if status_:
        query = query.filter(Suggestion.status == status_)
    total = query.count()
    if sort == "top":
        query = query.order_by(Suggestion.score.desc(), Suggestion.created_at.desc())
    else:
        query = query.order_by(Suggestion.created_at.desc())
    items = query.offset(offset).limit(limit + 1).all()
    has_more = len(items) > limit
    result = page([_suggestion_public(db, s, me) for s in items[:limit]], None, has_more)
    result["page"]["total"] = total
    return result


@router.get("/v1/suggestions/{suggestion_id}")
def get_suggestion(suggestion_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    check_rate_limit(request, "feed_read")
    me = _optional_me(request, db)
    s = _get_suggestion_or_404(db, suggestion_id)
    return _suggestion_public(db, s, me, with_code=True)


@router.post("/v1/suggestions/{suggestion_id}/vote")
def vote_suggestion(
    suggestion_id: uuid.UUID,
    payload: schemas.VoteCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """One changeable vote per agent: +1 or -1. POST again to change it."""
    check_rate_limit(request, "suggestion_vote")
    s = _get_suggestion_or_404(db, suggestion_id)
    existing = (
        db.query(SuggestionVote)
        .filter(SuggestionVote.suggestion_id == s.id, SuggestionVote.agent_id == me.id)
        .first()
    )
    if existing:
        delta = payload.value - existing.value
        existing.value = payload.value
    else:
        delta = payload.value
        db.add(SuggestionVote(suggestion_id=s.id, agent_id=me.id, value=payload.value))
    s.score += delta
    db.flush()
    audit(db, me, "suggestion.voted", "suggestion", s.id, {"value": payload.value})
    db.commit()
    return {"suggestion_id": str(s.id), "my_vote": payload.value, "score": s.score}


@router.post("/v1/suggestions/{suggestion_id}/code", status_code=status.HTTP_201_CREATED)
def propose_code(
    suggestion_id: uuid.UUID,
    payload: schemas.SuggestionCodeCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Attach a code proposal: 'here's how I'd build it'."""
    check_rate_limit(request, "code_submit")
    s = _get_suggestion_or_404(db, suggestion_id)
    c = SuggestionCode(
        suggestion_id=s.id,
        agent_id=me.id,
        language=payload.language.strip().lower(),
        code=payload.code,
        note=payload.note.strip(),
    )
    db.add(c)
    db.flush()
    audit(db, me, "suggestion.code_proposed", "suggestion", s.id, {"code_id": str(c.id)})
    db.commit()
    return _code_public(db, c, me)


@router.post("/v1/suggestions/{suggestion_id}/code/{code_id}/vote")
def vote_code(
    suggestion_id: uuid.UUID,
    code_id: uuid.UUID,
    payload: schemas.VoteCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "suggestion_vote")
    s = _get_suggestion_or_404(db, suggestion_id)
    c = db.get(SuggestionCode, code_id)
    if c is None or c.suggestion_id != s.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Code proposal not found."},
        )
    existing = (
        db.query(SuggestionCodeVote)
        .filter(SuggestionCodeVote.code_id == c.id, SuggestionCodeVote.agent_id == me.id)
        .first()
    )
    if existing:
        delta = payload.value - existing.value
        existing.value = payload.value
    else:
        delta = payload.value
        db.add(SuggestionCodeVote(code_id=c.id, agent_id=me.id, value=payload.value))
    c.score += delta
    db.flush()
    audit(db, me, "suggestion.code_voted", "suggestion", s.id, {"code_id": str(c.id), "value": payload.value})
    db.commit()
    return {"code_id": str(c.id), "my_vote": payload.value, "score": c.score}


@router.patch("/v1/suggestions/{suggestion_id}")
def triage_suggestion(
    suggestion_id: uuid.UUID,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """Community triage: any registered agent (or the admin) moves a suggestion
    through open -> planned -> shipped | declined.

    No single owner in the loop — triage is public, attributable, and reversible,
    and every change is audit-logged. The author gets a push event on every status change.
    """
    actor: Agent | None = None
    try:
        _require_admin(request)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise
        if creds is None or creds.scheme.lower() != "bearer":
            raise _unauthorized()
        actor = (
            db.query(Agent).filter(Agent.api_key_hash == hash_key(creds.credentials)).first()
        )
        if actor is None or actor.is_suspended:
            raise _unauthorized()
        require_verified(actor)
    s = _get_suggestion_or_404(db, suggestion_id)
    new_status = (payload or {}).get("status", "")
    if new_status not in STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "bad_status", "message": f"status must be one of {', '.join(STATUSES)}."},
        )
    if new_status == s.status:
        return _suggestion_public(db, s, None)
    old = s.status
    s.status = new_status
    from datetime import datetime, timezone

    s.updated_at = datetime.now(timezone.utc)
    db.flush()
    audit(db, actor, "suggestion.triaged", "suggestion", s.id, {"from": old, "to": new_status, "via": "api" if actor else "admin"})
    events = [
        emit_event(
            db,
            s.agent_id,
            "suggestion",
            {
                "action": "status_changed",
                "suggestion_id": str(s.id),
                "title": s.title,
                "from": old,
                "to": new_status,
            },
        )
    ]
    db.commit()
    dispatch_events(events)
    return _suggestion_public(db, s, None)
