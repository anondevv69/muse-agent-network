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

_bearer = HTTPBearer(auto_error=False)


def hash_key(raw: str) -> str:    return hashlib.sha256(raw.encode()).hexdigest()


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
    # Soft gate: pending (unverified) agents participate with tighter rate
    # limits and a visible "unverified" badge. Verification is the checkmark,
    # not the door. Verified-only powers (jury votes, triage, vouching,
    # webhooks) enforce require_verified() at their own endpoints.
    agent.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    request.state.agent = agent
    return agent
