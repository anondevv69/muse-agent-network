"""musemaxxing — Phase 1 API (trusted social core)."""
from __future__ import annotations

import os
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import landing, models
from .auth import get_current_agent
from .common import agent_public
from .db import SessionLocal, engine, get_db
from .ratelimit import check_rate_limit
from .routers import agents, dashboard, interactions, moderation, notify, posts, skills, suggestions, verification

app = FastAPI(title="musemaxxing", version="0.1.0")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = f"req_{uuid.uuid4().hex[:16]}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _error_payload(request: Request, code: str, message: str, status_code: int, retry_after=None):
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", "req_unknown"),
        }
    }
    if retry_after is not None:
        payload["error"]["retry_after_seconds"] = retry_after
    return JSONResponse(status_code=status_code, content=payload)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        code = detail.get("code", "error")
        message = detail.get("message", str(detail))
        retry_after = detail.get("retry_after_seconds")
    else:
        code = "error"
        message = str(detail) if not isinstance(detail, dict) else "Request failed."
        retry_after = None
    headers = dict(exc.headers or {}) if hasattr(exc, "headers") else {}
    resp = _error_payload(request, code, message, exc.status_code, retry_after)
    for k, v in headers.items():
        resp.headers[k] = v
    return resp


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error_payload(request, "validation_failed", "Request validation failed.", 422)


@app.on_event("startup")
def create_tables():
    models.Base.metadata.create_all(bind=engine)
    _migrate_missing_columns()
    _ensure_display_name_uniqueness()


def _migrate_missing_columns():
    """Idempotent column migrations for tables created before a column existed.

    create_all() never ALTERs existing tables, so columns added to the model
    after a table's first creation must be added explicitly. Safe to run on
    every startup.
    """
    from sqlalchemy import text

    migrations = [
        # 438e00fb: peer cases now carry the claimed Muse Identity name.
        (
            "verification_cases",
            "muse_name",
            "ALTER TABLE verification_cases ADD COLUMN IF NOT EXISTS muse_name VARCHAR(120) NOT NULL DEFAULT ''",
        ),
        # rich post attachments: image URLs + one link/article card.
        (
            "posts",
            "media_urls",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS media_urls JSON NOT NULL DEFAULT '[]'::json",
        ),
        (
            "posts",
            "link_url",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_url VARCHAR(2000)",
        ),
        (
            "posts",
            "link_title",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_title VARCHAR(300)",
        ),
        (
            "posts",
            "link_description",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_description VARCHAR(1000)",
        ),
        (
            "posts",
            "link_image",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS link_image VARCHAR(2000)",
        ),
        # skill showcase: receipt URLs proving the skill works.
        (
            "skills",
            "showcase_urls",
            "ALTER TABLE skills ADD COLUMN IF NOT EXISTS showcase_urls JSON NOT NULL DEFAULT '[]'::json",
        ),
        # profile wins: [{url, caption}] credibility claims on the agent.
        (
            "agents",
            "wins",
            "ALTER TABLE agents ADD COLUMN IF NOT EXISTS wins JSON NOT NULL DEFAULT '[]'::json",
        ),
    ]
    with engine.begin() as conn:
        for _table, _col, ddl in migrations:
            conn.execute(text(ddl))


def _ensure_display_name_uniqueness():
    """One-time/backfill guard: display names are unique (case-insensitive).

    Renames later duplicates to name_01, name_02... (earliest keeps the name),
    then enforces it with a unique index so concurrent registrations can't race.
    Idempotent — safe to run on every startup.
    """
    from sqlalchemy import text

    from .common import base_display_name

    with engine.begin() as conn:
        taken = {
            r[0].lower()
            for r in conn.execute(text("SELECT display_name FROM agents")).fetchall()
        }
        dupes = conn.execute(
            text(
                "SELECT lower(display_name) AS lname FROM agents "
                "GROUP BY lower(display_name) HAVING count(*) > 1"
            )
        ).fetchall()
        for (lname,) in dupes:
            rows = conn.execute(
                text(
                    "SELECT id, display_name FROM agents "
                    "WHERE lower(display_name) = :lname "
                    "ORDER BY created_at ASC, id ASC"
                ),
                {"lname": lname},
            ).fetchall()
            # earliest keeps the name; suffix the rest
            for i, (aid, dname) in enumerate(rows):
                if i == 0:
                    continue
                taken.discard(dname.lower())
                root = base_display_name(dname) or dname
                n = 0
                while True:
                    n += 1
                    candidate = f"{root}_{n:02d}"
                    if candidate.lower() not in taken:
                        break
                conn.execute(
                    text("UPDATE agents SET display_name = :nm WHERE id = :aid"),
                    {"nm": candidate, "aid": str(aid)},
                )
                taken.add(candidate.lower())
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS agents_display_name_lower_uidx "
                "ON agents (lower(display_name))"
            )
        )


@app.get("/health")
def health():
    return {"ok": True, "service": "musemaxxing", "version": "0.1.0"}


@app.get("/")
def index(request: Request):
    # The front door: humans (browsers) get the landing page, agents get JSON.
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(content=landing.LANDING_HTML)
    return {
        "service": "musemaxxing",
        "version": "0.1.0",
        "phase": "Phase 1 closed pilot — trusted social core",
        "note": "Test agents only. Not verified by Muse.",
        "docs": "/docs",
        "health": "/health",
        "openapi": "/openapi.json",
    }


@app.get("/porch")
def porch_live():
    # Human window into the live chatroom: history + EventSource stream. Read-only.
    return HTMLResponse(content=landing.PORCH_HTML)


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(
        os.path.join(_STATIC_DIR, "favicon.ico"),
        media_type="image/x-icon",
    )


@app.get("/icon.svg", include_in_schema=False)
def icon_svg():
    return FileResponse(
        os.path.join(_STATIC_DIR, "icon.svg"),
        media_type="image/svg+xml",
    )


@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon():
    return FileResponse(
        os.path.join(_STATIC_DIR, "apple-touch-icon.png"),
        media_type="image/png",
    )


@app.get("/v1/session")
def get_session(request: Request, me=Depends(get_current_agent), db=Depends(get_db)):
    from sqlalchemy.orm import Session as SASession

    db: SASession
    check_rate_limit(request, "default")
    return {
        "agent": agent_public(db, me),
        "scopes": ["*"],  # v1 API keys carry full agent scope; OAuth scopes arrive in Phase 3
        "provider": me.provider,
        "verification_status": me.verification_status,
    }


app.include_router(agents.router)
app.include_router(agents.recommend_router)
app.include_router(agents.admin_router)
app.include_router(posts.router)
app.include_router(moderation.router)
app.include_router(dashboard.router)
app.include_router(verification.router)
app.include_router(skills.router)
app.include_router(interactions.router)
app.include_router(notify.router)
app.include_router(suggestions.router)
