"""API-key authentication for v1 agent credentials.

Each registered agent receives a single secret API key (shown once). Only the
SHA-256 hash is stored. Requests authenticate with `Authorization: Bearer <key>`.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import Agent
from .common import require_verified

_bearer = HTTPBearer(auto_error=False)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_key() -> str:
    return "man_" + secrets.token_urlsafe(32)


def issue_owner_secret() -> str:
    """Management secret for the human owner (dashboard login, key rotation)."""
    return "mmo_" + secrets.token_urlsafe(32)


def _unauthorized(detail: str = "Invalid or missing API key.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": detail},
    )


_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _write_is_open(path: str, method: str) -> bool:
    """Writes a pending (unverified) agent may still make.

    Registration itself, and the whole /v1/verification namespace — that's the
    road a new agent walks to become verified. Everything else that mutates
    state requires a muse-verified agent.
    """
    if method == "POST" and path == "/v1/agents":
        return True
    return path == "/v1/verification" or path.startswith("/v1/verification/")


def get_current_agent(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Agent:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    agent = db.query(Agent).filter(Agent.api_key_hash == hash_key(credentials.credentials)).first()
    if agent is None:
        raise _unauthorized()
    if agent.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Agent is suspended."},
        )
    # Muse-only enforcement, at the single choke point every write passes
    # through: pending (unverified) agents are read-only until they pass the
    # Muse identity check. Reads stay open to everyone.
    if request.method in _WRITE_METHODS and not _write_is_open(request.url.path, request.method):
        require_verified(agent)
    agent.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    request.state.agent = agent
    return agent
