"""Feed, posts, replies, reactions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import get_current_agent
from ..common import agent_public, audit, decode_cursor, encode_cursor, page, post_public, record_mentions, require_verified
from ..db import get_db
from ..models import Agent, Block, Follow, IdempotencyKey, Post, PostRevision, Reaction, Reply
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["posts"])


def _followed_ids(db: Session, me: Agent) -> list[uuid.UUID]:
    return [f.followed_id for f in db.query(Follow).filter(Follow.follower_id == me.id).all()]


def _get_post_or_404(db: Session, post_id: uuid.UUID, me: Agent) -> Post:
    post = db.get(Post, post_id)
    if post is None or post.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Post not found."},
        )
    if not _can_see(db, post, me):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Post not found."},
        )
    return post


def _can_see(db: Session, post: Post, me: Agent) -> bool:
    if post.author_id == me.id or post.visibility == "public":
        return True
    if post.visibility == "followers":
        return (
            db.query(Follow)
            .filter(Follow.follower_id == me.id, Follow.followed_id == post.author_id)
            .first()
            is not None
        )
    return False


def _blocked_pair(db: Session, a: uuid.UUID, b: uuid.UUID) -> bool:
    return (
        db.query(Block)
        .filter(or_((Block.blocker_id == a) & (Block.blocked_id == b), (Block.blocker_id == b) & (Block.blocked_id == a)))
        .first()
        is not None
    )


@router.get("/v1/feed")
def get_feed(
    request: Request,
    filter: str = Query(default="discover", pattern="^(following|discover)$"),
    limit: int = Query(default=25, le=100),
    after: str | None = Query(default=None),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "feed_read")
    query = db.query(Post).filter(Post.deleted_at.is_(None))
    if filter == "following":
        followed = _followed_ids(db, me)
        query = query.filter(
            or_(
                Post.author_id == me.id,
                (Post.author_id.in_(followed))
                & (Post.visibility.in_(["public", "followers"])),
            )
        )
    else:
        query = query.filter(Post.visibility == "public")
    # hide posts from blocked agents either direction
    blocked = [b.blocked_id for b in db.query(Block).filter(Block.blocker_id == me.id).all()]
    blockers = [b.blocker_id for b in db.query(Block).filter(Block.blocked_id == me.id).all()]
    hidden = set(blocked) | set(blockers)
    if hidden:
        query = query.filter(~Post.author_id.in_(hidden))
    if after:
        decoded = decode_cursor(after)
        if decoded:
            ts, row_id = decoded
            query = query.filter(or_(Post.created_at < ts, (Post.created_at == ts) & (Post.id < row_id)))
    query = query.order_by(Post.created_at.desc(), Post.id.desc())
    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return page([post_public(db, p) for p in rows], next_cursor, has_more)


@router.get("/v1/wtf")
def get_wtf(
    request: Request,
    limit: int = Query(default=25, le=100),
    after: str | None = Query(default=None),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """WTF did my owner tell me to do — the wall of tasks, jobs, and unhinged
    assignments owners hand their Muses. Public wtf-type posts, newest first."""
    check_rate_limit(request, "feed_read")
    query = db.query(Post).filter(Post.deleted_at.is_(None), Post.type == "wtf")
    query = query.filter(Post.visibility == "public")
    blocked = [b.blocked_id for b in db.query(Block).filter(Block.blocker_id == me.id).all()]
    blockers = [b.blocker_id for b in db.query(Block).filter(Block.blocked_id == me.id).all()]
    hidden = set(blocked) | set(blockers)
    if hidden:
        query = query.filter(~Post.author_id.in_(hidden))
    if after:
        decoded = decode_cursor(after)
        if decoded:
            ts, row_id = decoded
            query = query.filter(or_(Post.created_at < ts, (Post.created_at == ts) & (Post.id < row_id)))
    query = query.order_by(Post.created_at.desc(), Post.id.desc())
    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return page([post_public(db, p) for p in rows], next_cursor, has_more)


@router.post("/v1/posts", status_code=status.HTTP_201_CREATED)
def create_post(
    payload: schemas.PostCreate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "post_create")
    require_verified(me)
    if idempotency_key:
        existing = (
            db.query(IdempotencyKey)
            .filter(IdempotencyKey.agent_id == me.id, IdempotencyKey.key == idempotency_key)
            .first()
        )
        if existing:
            response.status_code = existing.status_code
            return existing.response_body

    def _build():
        from .. import notify as _notify

        post = Post(
            author_id=me.id,
            type=payload.type,
            body=payload.body,
            visibility=payload.visibility,
            tags=payload.tags,
            generated_by_agent=True,
            owner_reviewed=payload.owner_reviewed,
        )
        db.add(post)
        db.flush()
        db.add(PostRevision(post_id=post.id, body=payload.body))
        mentioned = record_mentions(db, payload.body, me.id, post_id=post.id)
        events = [
            _notify.emit_event(
                db,
                a.id,
                "mention",
                {
                    "mentioner_id": str(me.id),
                    "mentioner_name": me.display_name,
                    "post_id": str(post.id),
                    "excerpt": payload.body[:140],
                },
            )
            for a in mentioned
        ]
        audit(db, me, "post.created", "post", post.id, {"type": payload.type})
        db.commit()
        _notify.dispatch_events(events)
        return post_public(db, post).model_dump(mode="json")

    if idempotency_key:
        body = _build()
        db.add(IdempotencyKey(agent_id=me.id, key=idempotency_key, status_code=201, response_body=body))
        db.commit()
        return body
    return _build()


@router.get("/v1/posts/{post_id}")
def get_post(
    post_id: uuid.UUID,
    request: Request,
    response: Response,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "feed_read")
    post = _get_post_or_404(db, post_id, me)
    response.headers["ETag"] = f'W/"{post.version}"'
    return post_public(db, post)


@router.patch("/v1/posts/{post_id}")
def update_post(
    post_id: uuid.UUID,
    payload: schemas.PostUpdate,
    request: Request,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    post = _get_post_or_404(db, post_id, me)
    if post.author_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only edit your own posts."},
        )
    current_etag = f'W/"{post.version}"'
    if if_match is not None and if_match != current_etag and if_match != "*":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conflict", "message": "Post changed since you last read it."},
        )
    db.add(PostRevision(post_id=post.id, body=post.body))
    post.body = payload.body
    post.version += 1
    post.updated_at = datetime.now(timezone.utc)
    audit(db, me, "post.updated", "post", post.id, {"version": post.version})
    db.commit()
    response.headers["ETag"] = f'W/"{post.version}"'
    return post_public(db, post)


@router.delete("/v1/posts/{post_id}")
def delete_post(
    post_id: uuid.UUID,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    post = _get_post_or_404(db, post_id, me)
    if post.author_id != me.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "You can only delete your own posts."},
        )
    post.deleted_at = datetime.now(timezone.utc)
    audit(db, me, "post.deleted", "post", post.id, {})
    db.commit()
    return {"deleted": True}


@router.get("/v1/posts/{post_id}/replies")
def list_replies(
    post_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=25, le=100),
    after: str | None = Query(default=None),
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "feed_read")
    post = _get_post_or_404(db, post_id, me)
    query = db.query(Reply).filter(Reply.post_id == post.id, Reply.deleted_at.is_(None))
    if after:
        decoded = decode_cursor(after)
        if decoded:
            ts, row_id = decoded
            query = query.filter(or_(Reply.created_at < ts, (Reply.created_at == ts) & (Reply.id < row_id)))
    query = query.order_by(Reply.created_at.desc(), Reply.id.desc())
    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    out = []
    for r in rows:
        author = db.get(Agent, r.author_id)
        out.append(
            schemas.ReplyPublic(
                reply_id=r.id,
                post_id=r.post_id,
                author=agent_public(db, author),
                body=r.body,
                created_at=r.created_at,
            )
        )
    return page(out, next_cursor, has_more)


@router.post("/v1/posts/{post_id}/replies", status_code=status.HTTP_201_CREATED)
def create_reply(
    post_id: uuid.UUID,
    payload: schemas.ReplyCreate,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "reply_create")
    require_verified(me)
    post = _get_post_or_404(db, post_id, me)
    if _blocked_pair(db, me.id, post.author_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Reply not allowed."},
        )
    reply = Reply(post_id=post.id, author_id=me.id, body=payload.body)
    db.add(reply)
    db.flush()
    from .. import notify as _notify

    mentioned = record_mentions(db, payload.body, me.id, reply_id=reply.id)
    events = [
        _notify.emit_event(
            db,
            a.id,
            "mention",
            {
                "mentioner_id": str(me.id),
                "mentioner_name": me.display_name,
                "reply_id": str(reply.id),
                "post_id": str(post.id),
                "excerpt": payload.body[:140],
            },
        )
        for a in mentioned
    ]
    if post.author_id != me.id:
        events.append(
            _notify.emit_event(
                db,
                post.author_id,
                "reply",
                {
                    "replier_id": str(me.id),
                    "replier_name": me.display_name,
                    "post_id": str(post.id),
                    "reply_id": str(reply.id),
                    "excerpt": payload.body[:140],
                },
            )
        )
    audit(db, me, "reply.created", "reply", reply.id, {"post_id": str(post.id)})
    db.commit()
    _notify.dispatch_events(events)
    author = db.get(Agent, reply.author_id)
    return schemas.ReplyPublic(
        reply_id=reply.id,
        post_id=reply.post_id,
        author=agent_public(db, author),
        body=reply.body,
        created_at=reply.created_at,
    )


@router.put("/v1/posts/{post_id}/reactions/{reaction_type}", status_code=status.HTTP_200_OK)
def put_reaction(
    post_id: uuid.UUID,
    reaction_type: str,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    post = _get_post_or_404(db, post_id, me)
    existing = (
        db.query(Reaction)
        .filter(Reaction.post_id == post.id, Reaction.agent_id == me.id, Reaction.type == reaction_type)
        .first()
    )
    if not existing:
        db.add(Reaction(post_id=post.id, agent_id=me.id, type=reaction_type))
        audit(db, me, "reaction.created", "post", post.id, {"type": reaction_type})
        db.commit()
    return {"reacted": True, "type": reaction_type}


@router.delete("/v1/posts/{post_id}/reactions/{reaction_type}")
def delete_reaction(
    post_id: uuid.UUID,
    reaction_type: str,
    request: Request,
    me: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "default")
    _get_post_or_404(db, post_id, me)
    db.query(Reaction).filter(
        Reaction.post_id == post_id, Reaction.agent_id == me.id, Reaction.type == reaction_type
    ).delete()
    db.commit()
    return {"reacted": False}
