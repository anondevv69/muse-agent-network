"""Tests for the X-post identity anchor (optional flair, never a gate).

Covers: phrase/code format, tweet-id parsing, evidence validation rules, and
the X API unavailability contract (pending + retryable, never failed).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.routers.verification import (
    X_CHALLENGE_TTL_DAYS,
    _check_x_evidence,
    _parse_tweet_id,
    _x_lookup_tweet,
    _x_phrase,
)
from app.models import XChallenge


def _challenge(**kw):
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        code="AB12CD",
        phrase="",
        used=False,
        expires_at=now + timedelta(days=X_CHALLENGE_TTL_DAYS),
        created_at=now,
    )
    base.update(kw)
    base["phrase"] = _x_phrase("TestAgent", base["code"])
    cols = set(XChallenge.__table__.columns.keys())
    return XChallenge(**{k: v for k, v in base.items() if k in cols})


def test_phrase_format():
    p = _x_phrase("TestAgent", "AB12CD")
    assert "TestAgent" in p
    assert "musemaxxing.xyz" in p
    assert "AB12CD" in p


def test_parse_tweet_id():
    assert _parse_tweet_id("https://x.com/someone/status/1234567890123456789") == "1234567890123456789"
    assert _parse_tweet_id("https://twitter.com/someone/status/1234567890123456789") == "1234567890123456789"
    assert _parse_tweet_id("https://mobile.twitter.com/someone/statuses/1234567890123456789?s=20") == "1234567890123456789"
    assert _parse_tweet_id("1234567890123456789") == "1234567890123456789"
    assert _parse_tweet_id("not a tweet") is None
    assert _parse_tweet_id("https://x.com/someone") is None
    assert _parse_tweet_id("") is None


def test_evidence_pass():
    ch = _challenge()
    tweeted = (ch.created_at + timedelta(minutes=5)).isoformat()
    ok, reason = _check_x_evidence(ch, "SomeHandle", "somehandle", ch.phrase, tweeted)
    assert ok, reason


def test_evidence_handle_mismatch():
    ch = _challenge()
    tweeted = (ch.created_at + timedelta(minutes=5)).isoformat()
    ok, reason = _check_x_evidence(ch, "SomeHandle", "otherperson", ch.phrase, tweeted)
    assert not ok and "does not match" in reason


def test_evidence_missing_phrase_and_code():
    ch = _challenge()
    tweeted = (ch.created_at + timedelta(minutes=5)).isoformat()
    ok, reason = _check_x_evidence(ch, "h", "h", "just some random tweet", tweeted)
    assert not ok and "phrase/code" in reason


def test_evidence_code_alone_counts():
    # The phrase contains the code; a client echoing just the code still counts.
    ch = _challenge()
    tweeted = (ch.created_at + timedelta(minutes=5)).isoformat()
    ok, _ = _check_x_evidence(ch, "h", "h", f"validating! code {ch.code}", tweeted)
    assert ok


def test_evidence_tweet_before_challenge_fails():
    ch = _challenge()
    tweeted = (ch.created_at - timedelta(minutes=5)).isoformat()
    ok, reason = _check_x_evidence(ch, "h", "h", ch.phrase, tweeted)
    assert not ok and "before the challenge" in reason


def test_lookup_unavailable_without_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    status, reason = _x_lookup_tweet("1234567890123456789")
    assert status == "unavailable"
    assert reason
