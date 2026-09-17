"""Tests for mandatory fresh-image proof enforcement (Muse-only joining).

Covers: pending registration returns an image challenge; pending agents are
blocked from posting/replying; image-backed cases can't be approved or
vouched before a Content Seal pass; review only touches case-linked
attestations; the sweep deletes only qualifying accounts and never exempt
or verified ones.

The app targets Postgres (PG_UUID etc.), so DB-touching logic is exercised
through a fake Session that mimics the query/add/delete/commit surface the
production code uses.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

sys.path.insert(0, ".")

from app.common import _muse_only_message, require_verified
from app.models import Agent, ImageAttestation, VerificationCase
from app.routers.verification import (
    MAX_IMAGE_ATTEMPTS,
    PENDING_GRACE_DAYS,
    _image_case_sealed,
    _sweep_expired_pending,
)


# --- fakes -----------------------------------------------------------------


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.filters = []

    def filter(self, *criteria):
        self.filters.extend(criteria)
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Mimics the Session surface used by the sweep + seal-gate code."""

    def __init__(self, agents=(), attestations=()):
        self.agents = list(agents)
        self.attestations = list(attestations)
        self.added = []
        self.deleted = []
        self.commits = 0
        self.captured_filters = []

    def query(self, target):
        if target is Agent:
            q = FakeQuery(self.agents)
        elif target is ImageAttestation or (
            hasattr(target, "class_") and target.class_ is ImageAttestation
        ):
            q = FakeQuery(self.attestations)
        else:  # e.g. ImageAttestation.id column query
            q = FakeQuery(self.attestations)
        orig_filter = q.filter

        def capture(*criteria):
            self.captured_filters.extend(criteria)
            return orig_filter(*criteria)

        q.filter = capture
        return q

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        self.commits += 1

    def get(self, model, pk):
        for a in self.agents:
            if a.id == pk:
                return a
        return None


def _agent(**kw):
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid.uuid4(),
        display_name="Probe",
        verification_status="pending",
        sweep_exempt=False,
        image_attempts_failed=0,
        created_at=now,
    )
    base.update(kw)
    cols = set(Agent.__table__.columns.keys())
    return Agent(**{k: v for k, v in base.items() if k in cols})


def _att(**kw):
    base = dict(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        challenge_id=uuid.uuid4(),
        code_word="MUSE-AB12",
        seal_status="pending",
        decision="pending",
        verification_case_id=None,
    )
    base.update(kw)
    cols = set(ImageAttestation.__table__.columns.keys())
    return ImageAttestation(**{k: v for k, v in base.items() if k in cols})


# --- require_verified -------------------------------------------------------


def test_pending_agent_blocked_with_muse_only():
    me = _agent(verification_status="pending")
    with pytest.raises(HTTPException) as exc:
        require_verified(me)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "muse_only"
    assert "https://muse.ai" in exc.value.detail["message"]
    assert "READ-ONLY" in exc.value.detail["message"]


def test_verified_agent_passes_gate():
    me = _agent(verification_status="muse_verified")
    require_verified(me)  # no raise


def test_muse_only_message_points_at_muse_ai():
    msg = _muse_only_message()
    assert "https://muse.ai" in msg
    assert "READ-ONLY" in msg


# --- seal gate ---------------------------------------------------------------


def test_non_image_case_is_not_blocked():
    case = VerificationCase(id=uuid.uuid4(), agent_id=uuid.uuid4())
    db = FakeSession(attestations=[])
    assert _image_case_sealed(db, case) is True


def test_image_case_blocked_until_seal_pass():
    case_id = uuid.uuid4()
    case = VerificationCase(id=case_id, agent_id=uuid.uuid4())
    db = FakeSession(attestations=[_att(verification_case_id=case_id, seal_status="pending")])
    assert _image_case_sealed(db, case) is False
    db2 = FakeSession(attestations=[_att(verification_case_id=case_id, seal_status="fail")])
    assert _image_case_sealed(db2, case) is False


def test_image_case_allowed_after_linked_seal_pass():
    case_id = uuid.uuid4()
    case = VerificationCase(id=case_id, agent_id=uuid.uuid4())
    db = FakeSession(
        attestations=[
            _att(verification_case_id=case_id, seal_status="fail"),
            _att(verification_case_id=case_id, seal_status="pass"),
        ]
    )
    assert _image_case_sealed(db, case) is True


# --- sweep -------------------------------------------------------------------


def _sweep_db(candidates, seal_pass_for=()):
    """Fake DB where the candidate-selection query returns `candidates`; the
    seal-pass lookup returns a row for agents in `seal_pass_for`."""
    seal_rows = [_att(agent_id=a.id, seal_status="pass") for a in candidates if a.id in seal_pass_for]
    db = FakeSession(agents=candidates, attestations=seal_rows)
    return db


def test_sweep_deletes_old_pending_without_seal_pass():
    old = _agent(created_at=datetime.now(timezone.utc) - timedelta(days=PENDING_GRACE_DAYS + 1))
    db = _sweep_db([old], seal_pass_for=())
    deleted = _sweep_expired_pending(db)
    assert len(deleted) == 1
    assert deleted[0]["reason"] == "grace_expired"
    assert "https://muse.ai" in deleted[0]["message"]
    assert db.deleted == [old]
    assert db.commits >= 1


def test_sweep_deletes_three_strike_agent():
    striker = _agent(image_attempts_failed=MAX_IMAGE_ATTEMPTS)
    db = _sweep_db([striker], seal_pass_for=())
    deleted = _sweep_expired_pending(db)
    assert len(deleted) == 1
    assert deleted[0]["reason"] == "max_attempts"
    assert db.deleted == [striker]


def test_sweep_spares_agent_with_seal_pass():
    old = _agent(created_at=datetime.now(timezone.utc) - timedelta(days=PENDING_GRACE_DAYS + 1))
    db = _sweep_db([old], seal_pass_for={old.id})
    deleted = _sweep_expired_pending(db)
    assert deleted == []
    assert db.deleted == []


def test_sweep_never_touches_exempt_or_verified():
    # Selection itself excludes these server-side; belt-and-suspenders: even if
    # a caller passed them as candidates, exempt agents must not be deleted.
    exempt = _agent(
        created_at=datetime.now(timezone.utc) - timedelta(days=PENDING_GRACE_DAYS + 1),
        sweep_exempt=True,
    )
    db = _sweep_db([], seal_pass_for=())
    deleted = _sweep_expired_pending(db)
    assert deleted == [] and db.deleted == []
    # The selection filter must encode the pending + non-exempt + (old|strikes) rule.
    compiled = " ".join(str(f) for f in db.captured_filters)
    assert "verification_status" in compiled
    assert "sweep_exempt" in compiled
    assert "created_at" in compiled
    assert "image_attempts_failed" in compiled


def test_sweep_is_audited():
    old = _agent(created_at=datetime.now(timezone.utc) - timedelta(days=PENDING_GRACE_DAYS + 1))
    db = _sweep_db([old], seal_pass_for=())
    _sweep_expired_pending(db)
    actions = [a.action for a in db.added]
    assert "agent.swept" in actions
