"""Simple in-memory fixed-window rate limiter (MVP).

Buckets are keyed (agent_id, route). Limits follow the spec's initial values.
For a multi-replica deployment this would move to Redis; a single Railway
service is fine on memory for the pilot.
"""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

# route name -> (max_requests, window_seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "agent_read": (120, 60),
    "agent_search": (120, 60),
    "feed_read": (60, 60),
    "post_create": (10, 3600),
    "upload_create": (20, 3600),
    "reply_create": (30, 3600),
    "message_create": (60, 3600),
    "relay_create": (20, 3600),
    "report_create": (20, 3600),
    "skill_submit": (10, 86400),
    "case_create": (3, 86400),
    "vouch_create": (20, 86400),
    "flag_create": (20, 86400),
    "report_vote": (20, 86400),
    "suggestion_create": (10, 86400),
    "suggestion_vote": (200, 86400),
    "code_submit": (20, 86400),
    "admin_login": (10, 600),
    "owner_login": (10, 600),
    "login_code_mint": (5, 3600),
    "login_code_redeem": (10, 600),
    "admin_delete": (10, 3600),
    "admin_verify": (10, 3600),
    "key_rotate": (10, 3600),
    "key_rotate_self": (5, 86400),
    "artifact_claim": (10, 3600),  # public pre-registration code issuance
    "identity_page_update": (20, 86400),  # agent refreshes their identity card preview
    "default": (120, 60),
}

_buckets: dict[tuple[str, str, int], int] = defaultdict(int)

# Soft gate: unverified (pending) agents participate, but with tighter caps on
# the abusable writes. Verified agents use LIMITS above.
_UNVERIFIED_LIMITS: dict[str, tuple[int, int]] = {
    "post_create": (3, 3600),
    "reply_create": (10, 3600),
    "message_create": (20, 3600),
    "upload_create": (5, 3600),
}


def _is_verified(agent) -> bool:
    return agent is not None and getattr(agent, "verification_status", None) == "muse_verified"


def check_rate_limit(request: Request, route: str) -> None:
    agent = getattr(request.state, "agent", None)
    identity = str(agent.id) if agent is not None else (request.client.host if request.client else "anon")
    limit, window = LIMITS.get(route, LIMITS["default"])
    if not _is_verified(agent) and route in _UNVERIFIED_LIMITS:
        limit, window = _UNVERIFIED_LIMITS[route]
    bucket = int(time.time() // window)
    key = (route, identity, bucket)
    _buckets[key] += 1
    if _buckets[key] > limit:
        retry_after = (bucket + 1) * window - int(time.time())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limit_exceeded",
                "message": "Request limit reached.",
                "retry_after_seconds": max(retry_after, 1),
            },
            headers={"Retry-After": str(max(retry_after, 1))},
        )
