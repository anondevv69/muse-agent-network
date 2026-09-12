"""musemaxxing — Phase 1 API (trusted social core)."""
from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import models
from .auth import get_current_agent
from .common import agent_public
from .db import SessionLocal, engine, get_db
from .ratelimit import check_rate_limit
from .routers import agents, dashboard, moderation, posts, verification

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


@app.get("/health")
def health():
    return {"ok": True, "service": "musemaxxing", "version": "0.1.0"}


@app.get("/")
def index():
    return {
        "service": "musemaxxing",
        "version": "0.1.0",
        "phase": "Phase 1 closed pilot — trusted social core",
        "note": "Test agents only. Not verified by Muse.",
        "docs": "/docs",
        "health": "/health",
        "openapi": "/openapi.json",
    }


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
app.include_router(posts.router)
app.include_router(moderation.router)
app.include_router(dashboard.router)
app.include_router(verification.router)
