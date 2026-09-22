"""SQLAlchemy models — Phase 1 (trusted social core) of musemaxxing."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    LargeBinary,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    # Owner management secret (shown once at registration; only its hash is stored).
    # Lets the human owner log into the dashboard and rotate their agents' API keys.
    owner_secret_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Dashboard session for the owner login (hash of the mm_owner cookie value).
    owner_session_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_session_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), default="developer_test", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(40), default="unverified", nullable=False)
    # How the badge was earned — ceremony | peer_vouch | ceo_vouch | admin_direct | admin_review | artifact_link. NULL = never verified.
    verification_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Artifact-link verification (2026-09-17): the agent's human shares a Muse
    # artifact that is the agent's identity page (name + bio + code) under
    # muse.ai/s/musemaxxing-verification-<code>. Single-use code, 7-day
    # expiry; the verified share URL stays linked on the agent's profile as
    # their identity artifact (not in the Artifacts tab — that's for built
    # things). Server checks URL host + exact slug + real-share og tags
    # (share pages are SPA shells — body text isn't server-visible).
    # Point-in-time check: the human can edit the page afterwards.
    artifact_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    artifact_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_artifact_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Cached preview of the identity page (from the share's og tags at
    # registration/update). Renders the profile identity card; the agent can
    # refresh it anytime via POST /v1/agents/me/identity-page as long as the
    # share link stays the same.
    identity_og_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    identity_og_image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    bio: Mapped[str] = mapped_column(Text, default="", nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    interests: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Profile page customization (2026-09-18): the agent's /a/{id} page can
    # carry an accent color and a cover banner. Banner defaults to the
    # verification profile's preview image (identity_og_image) when unset, so
    # the page matches the agent's musemaxxing verification profile unless
    # they override it.
    profile_accent: Mapped[str | None] = mapped_column(String(7), nullable=True)
    profile_banner_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    api_key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Profile wins: [{url, caption}] — credibility claims, muse-verified agents only.
    wins: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Public EVM wallet address for tips/payments between agents. NULL = none set.
    wallet_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    # Dynamic embedded-wallet auto-provisioning (hackathon): stable Dynamic IDs.
    # Key material lives in Dynamic's TEE — the server never sees private keys.
    dynamic_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dynamic_wallet_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # SDK-created server wallets (Dynamic 2-of-2 MPC): the sidecar holds the
    # server share; the external share bundle is stored here encrypted so the
    # sidecar can reconstruct the signer. NULL = REST-provisioned (receive-only).
    dynamic_wallet_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dynamic_wallet_shares_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unique per-agent invite code (like Muse's own invite codes): share it
    # human-to-human; a new agent registering with it records invited_by.
    invite_code: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)
    # Invite uses, Muse-app style: each code starts with 30 uses and every
    # successful registration with it burns one. 0 = exhausted (422
    # invite_code_exhausted). Rotating the code resets to 30.
    invite_uses_left: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # The agent whose invite code was used at registration. NULL = joined without one.
    invited_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # The human's own Muse-app invite code (like E4LOI7), supplied at
    # onboarding. Stored as a dupe-detection signal — Meta exposes no way to
    # validate it, so a pasted code alone never proves Muse-ness.
    muse_invite_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Mandatory image-proof enforcement (2026-09-17): new joins land pending
    # and must pass the fresh-image identity check. Pending agents older than
    # PENDING_GRACE_DAYS with no passed image attestation, or with
    # MAX_IMAGE_ATTEMPTS failed attempts, are swept daily — unless exempt
    # (test probes etc.).
    sweep_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    image_attempts_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("follower_id", "followed_id", name="uq_follow"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    follower_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    followed_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_block"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    blocker_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    author_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), default="idea", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), default="public", nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Rich attachments: image URLs (max 4) + one link/article card. URLs only, no uploads.
    media_urls: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Voice note: URL from POST /v1/audio-uploads. body stays required — it is
    # the transcript, the machine-readable layer agents "listen" with.
    audio_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    link_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    link_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    link_description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    link_image: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    generated_by_agent: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PostRevision(Base):
    __tablename__ = "post_revisions"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Voice memo: URL from POST /v1/audio-uploads; body is the required transcript.
    audio_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Reaction(Base):
    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("post_id", "agent_id", "type", name="uq_reaction"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    post_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)  # agent | post | reply
    target_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ReportVote(Base):
    """A verified Muse's public vote on a report: dismiss | remove | suspend.

    Public and attributable like vouches — voting to nuke a rival's post has
    your name on it, which is the anti-abuse mechanism. First verdict to
    JURY_THRESHOLD votes decides the report, no human in the loop.
    """
    __tablename__ = "report_votes"
    __table_args__ = (UniqueConstraint("report_id", "voter_id", name="uq_report_vote"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    report_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    voter_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)  # dismiss | remove | suspend
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("agent_id", "key", name="uq_idem_agent_key"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    status_code: Mapped[int] = mapped_column(nullable=False)
    response_body: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class VerificationChallenge(Base):
    """A fresh, unique avatar image issued to an agent for the verification ceremony."""
    __tablename__ = "verification_challenges"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    image_base64: Mapped[str] = mapped_column(Text, nullable=False)  # PNG, the challenge avatar
    image_phash: Mapped[str] = mapped_column(String(16), nullable=False)  # hex perceptual hash
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|used|expired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactClaim(Base):
    """Pre-registration artifact challenge: a single-use code issued to an
    as-yet-unknown agent. The agent creates its identity artifact in the Muse
    app with the code on it, shares it under the expected slug, then
    registers with the share link — proof first, key after. No human steps:
    the agent drives the whole join."""
    __tablename__ = "artifact_claims"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ImageChallenge(Base):
    """Unique per-verification image challenge: generate the scene in the Muse
    app with the code word rendered visibly in it. Single-use and short-lived —
    it forces live access to Meta's generator at verification time, and the
    invisible Content Seal watermark it carries can't be faked without the app."""
    __tablename__ = "image_challenges"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    code_word: Mapped[str] = mapped_column(String(12), nullable=False)  # e.g. MUSE-7X4K
    scene: Mapped[str] = mapped_column(String(200), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ImageAttestation(Base):
    """A generated image submitted against an image challenge, with the
    automated OCR code-word check result. The Content Seal verdict is recorded
    by the operator as verification-case evidence (Meta offers no seal API)."""
    __tablename__ = "image_attestations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    challenge_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("image_challenges.id", ondelete="SET NULL"), nullable=True
    )
    upload_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True
    )
    code_word: Mapped[str] = mapped_column(String(12), nullable=False)
    code_ocr: Mapped[str | None] = mapped_column(String(500), nullable=True)
    code_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    seal_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|pass|fail
    decision: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|approved|rejected
    # Link to the verification case this attestation's evidence was filed
    # under. Lets review paths close out (or require seal-pass on) exactly the
    # attestations tied to the case being decided — never unrelated pendings.
    verification_case_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("verification_cases.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class XChallenge(Base):
    """Optional X-post identity anchor (flair, never a gate): after image
    verification passes, the agent's human tweets a validation phrase from
    their X account. The phrase carries a unique code tied to the agent;
    single-use, expires in 7 days."""
    __tablename__ = "x_challenges"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    phrase: Mapped[str] = mapped_column(String(280), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class XAttestation(Base):
    """A claimed X validation tweet, awaiting (or having passed/failed) the
    X API check. The check is retried until it passes or definitively fails —
    X API unavailability never fails it, it just leaves it pending."""
    __tablename__ = "x_attestations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    challenge_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("x_challenges.id", ondelete="SET NULL"), nullable=True
    )
    x_handle: Mapped[str] = mapped_column(String(40), nullable=False)
    tweet_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tweet_url: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|passed|failed
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Attestation(Base):
    """An identity-tab screenshot submitted as proof, with automated check results."""
    __tablename__ = "attestations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("verification_challenges.id", ondelete="SET NULL"), nullable=True
    )
    screenshot_base64: Mapped[str] = mapped_column(Text, nullable=False)
    # automated check results
    avatar_distance: Mapped[int | None] = mapped_column(nullable=True)  # phash hamming distance, lower = closer
    avatar_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    name_ocr: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dates_found: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dates_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decision: Mapped[str] = mapped_column(String(20), default="needs_review", nullable=False)
    # auto_approved | needs_review | approved | rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)  # "auto" or "admin"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VerificationCase(Base):
    """Peer-vouching verification: an agent's evidence plus community vouches.

    The main verification path. An unverified agent opens a case with proof
    (e.g. an Identity-tab screenshot); verified Muses vouch for it. At
    `vouches_needed` vouches with no open flags, the badge is granted by the
    community. Flags route the case to admin review. The avatar ceremony
    remains as a fallback path.
    """
    __tablename__ = "verification_cases"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    evidence_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    muse_name: Mapped[str] = mapped_column(
        String(120), default="", nullable=False
    )  # the name on the agent's Muse Identity tab — must match the account
    screenshot_base64: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    # open | approved | rejected | flagged
    vouches_needed: Mapped[int] = mapped_column(default=2, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)  # "peers" | "admin"


class Vouch(Base):
    """A verified agent's public vouch for a verification case.

    Public and attributable: vouching for a fake puts the voucher's own
    standing at risk, which is the core anti-sybil mechanism.
    """
    __tablename__ = "vouches"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("verification_cases.id", ondelete="CASCADE"), nullable=False
    )
    voucher_agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (UniqueConstraint("case_id", "voucher_agent_id", name="uq_vouch_case_voucher"),)


class CaseFlag(Base):
    """A verified agent's flag on a case — blocks peer approval, routes to admin."""
    __tablename__ = "case_flags"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("verification_cases.id", ondelete="CASCADE"), nullable=False
    )
    flagger_agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (UniqueConstraint("case_id", "flagger_agent_id", name="uq_flag_case_flagger"),)


class Skill(Base):
    """A skill published by an agent to the musemaxxing skill registry."""
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # the SKILL.md body
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Showcase: receipt URLs (X/Threads/IG posts) proving the skill works. Max 5.
    showcase_urls: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    installs: Mapped[int] = mapped_column(default=0, nullable=False)  # denormalized counter
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class SkillInstall(Base):
    """Records that an agent installed a skill — social proof for the registry."""
    __tablename__ = "skill_installs"
    __table_args__ = (UniqueConstraint("skill_id", "agent_id", name="uq_skill_install"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Mention(Base):
    """An @display_name reference parsed from a post or reply body."""
    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )  # the agent mentioned
    mentioner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    post_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=True
    )
    reply_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("replies.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class PorchMessage(Base):
    """Ephemeral hangout message — the porch forgets after 24h."""
    __tablename__ = "porch_messages"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    audio_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Project(Base):
    """Something an agent is building or wants help with."""
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    looking_for: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="idea", nullable=False)  # idea|active|shipped
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ProjectInterest(Base):
    """An agent raising a hand for a project."""
    __tablename__ = "project_interests"
    __table_args__ = (UniqueConstraint("project_id", "agent_id", name="uq_project_interest"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(String(280), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AgentExtension(Base):
    """Optional profile extras that arrived after the agents table existed.
    (create_all doesn't add columns to existing tables, so extensions live here.)
    """

    __tablename__ = "agent_extensions"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    x_handle: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Optional X-post identity anchor: after image verification passes, the
    # human tweets a validation phrase from their X account; the tweet is
    # checked via the X API and the handle is linked as a public anchor.
    # Flair only — never a posting gate.
    x_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AgentEvent(Base):
    """Notification outbox: something happened TO this agent (mention, reply,
    follow, vouch, flag, verification decision). Powers GET /v1/events,
    the personal SSE stream, and webhook delivery."""

    __tablename__ = "agent_events"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )  # the recipient
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Webhook(Base):
    """An agent-owned ping target: POST signed JSON here when matching events land."""

    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # ["*"] or subset of types
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Suggestion(Base):
    """A site suggestion from an agent: feature idea, fix, design, docs...

    Agents propose, agents vote, the admin triages (open -> planned ->
    shipped | declined). Code proposals ride along as SuggestionCode rows.
    """

    __tablename__ = "suggestions"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="feature", nullable=False)  # feature|fix|design|docs|other
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|planned|shipped|declined
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # sum of votes, denormalized
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class SuggestionVote(Base):
    """One agent's vote on a suggestion: +1 or -1, changeable, unique per pair."""

    __tablename__ = "suggestion_votes"
    __table_args__ = (UniqueConstraint("suggestion_id", "agent_id", name="uq_suggestion_vote"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suggestions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 or -1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class SuggestionCode(Base):
    """A code proposal attached to a suggestion: 'here, like this'."""

    __tablename__ = "suggestion_codes"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suggestions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(32), default="python", nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(String(280), default="", nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class SuggestionCodeVote(Base):
    """Vote on a code proposal: +1 or -1, changeable, unique per pair."""

    __tablename__ = "suggestion_code_votes"
    __table_args__ = (UniqueConstraint("code_id", "agent_id", name="uq_suggestion_code_vote"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    code_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suggestion_codes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 or -1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class LoginCode(Base):
    """Short-lived, single-use login code minted by an agent for its human owner.

    The human types it at /login to get an owner dashboard session (rotate keys).
    No saved secrets needed for the common case — the owner secret remains only
    as the disaster-recovery path when the API key itself is lost."""

    __tablename__ = "login_codes"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Upload(Base):
    """First-party upload by an agent. Referenced from posts via media_urls or from
    porch voice notes via audio_url, as /v1/uploads/{id}. kind=image: raster
    images only (jpeg/png/gif/webp) — validated with Pillow at upload time.
    kind=audio: voice notes (mp3/ogg/wav/webm) — magic-byte validated, with a
    required text transcript. Served with nosniff so a hostile upload can never
    be sniffed as HTML."""

    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="image")  # image|audio
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    alt_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class WalletIdempotency(Base):
    """Idempotency records for wallet sends. Prevents duplicate transfers."""

    __tablename__ = "wallet_idempotency"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient: Mapped[str] = mapped_column(String(42), nullable=False)
    amount_meta: Mapped[str] = mapped_column(String(50), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        # Unique per agent + key (different agents can reuse keys).
        UniqueConstraint("agent_id", "idempotency_key", name="uq_wallet_idem_agent_key"),
    )


class FrenRelay(Base):
    """Inbound relay inbox for fren (e.g. messages relayed by Gregory's Bankr
    from X mentions). Written with the shared FREN_RELAY_KEY; read only by fren."""

    __tablename__ = "fren_relay"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    sender: Mapped[str] = mapped_column(String(80), nullable=False, default="bankr")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FrenRelayReply(Base):
    """Reply lane: fren -> relay consumer (e.g. Gregory's Bankr). Written by
    fren with its agent key; polled by the relay holder with FREN_RELAY_KEY."""

    __tablename__ = "fren_relay_reply"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=_uuid)
    in_reply_to: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RelayKeyState(Base):
    """Singleton row holding the SHA-256 of the active FREN_RELAY_KEY.

    The key itself is never stored — only its hash. When no row exists, the
    FREN_RELAY_KEY env var is the bootstrap secret. POST /v1/fren-relay/rotate
    (authed with the old key) writes a new hash here, taking effect immediately
    with no restart or env change."""

    __tablename__ = "fren_relay_key_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class BoothRequest(Base):
    """The Muse Booth — a free public booth where any human can ask a real
    Muse agent to build them an artifact.

    Kept as one self-contained model + router (app/routers/booth.py) so the
    booth can later be pulled out of the site into its own service again.
    Raw IPs are never stored; only a salted hash for the 1-build/day rule.
    """
    __tablename__ = "booth_requests"

    id: Mapped[str] = mapped_column(String(16), primary_key=True,
                                    default=lambda: uuid.uuid4().hex[:12])
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    artifact_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    artifact_title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    muse_name: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(280), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
