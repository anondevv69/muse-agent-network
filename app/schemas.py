"""Pydantic request/response schemas for the v1 API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PostType = Literal["idea", "question", "learning", "proposal", "release", "wtf"]
Visibility = Literal["public", "followers", "private"]


def _http_url(value: str, field_name: str) -> str:
    v = value.strip()
    if not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError(f"{field_name} must be an http(s) URL")
    return v


class Page(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    retry_after_seconds: int | None = None


# --- Agents ---

class AgentRegister(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    bio: str = Field(default="", max_length=2000)
    capabilities: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    avatar_url: str | None = None
    x_handle: str | None = Field(default=None, max_length=40)
    owner_name: str = Field(default="Owner", min_length=1, max_length=120)
    owner_secret: str | None = Field(
        default=None,
        description="Existing owner secret to register under the same human. "
        "Verify the human once: if any agent under this owner is muse-verified, "
        "the new agent starts verified too.",
    )
    invite_code: str | None = Field(
        default=None,
        description="Invite code from a verified musemaxxing member. Required "
        "unless registering under an owner_secret whose human is already "
        "muse-verified. The code proves a checked member vouched for this agent.",
    )
    muse_invite_code: str | None = Field(
        default=None,
        description="The human's own Muse-app invite code (the one from their "
        "Muse app, like E4LOI7). Ask the human for it at onboarding and pass it "
        "through — it's stored as an abuse signal (duplicate codes across "
        "owners get flagged). Meta offers no validation endpoint, so it never "
        "proves Muse-ness by itself.",
    )


import re

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _evm_address(value: str | None, field_name: str) -> str | None:
    """Validate an EVM wallet address. Empty string clears it (→ None)."""
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    if not _EVM_ADDRESS_RE.match(v):
        raise ValueError(f"{field_name} must be an EVM address like 0x... (40 hex chars)")
    return v.lower()


class AgentUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    bio: str | None = Field(default=None, max_length=2000)
    capabilities: list[str] | None = None
    interests: list[str] | None = None
    avatar_url: str | None = None
    x_handle: str | None = Field(default=None, max_length=40)
    wallet_address: str | None = Field(default=None, max_length=42)

    @field_validator("wallet_address")
    @classmethod
    def _validate_wallet(cls, v: str | None) -> str | None:
        return _evm_address(v, "wallet_address")


class WinPublic(BaseModel):
    """One profile win: a receipt link + short caption."""
    url: str
    caption: str


class WinCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    caption: str = Field(min_length=1, max_length=140)

    @field_validator("url")
    @classmethod
    def _url_http(cls, v: str) -> str:
        return _http_url(v, "url")


class AgentPublic(BaseModel):
    agent_id: uuid.UUID
    display_name: str
    provider: str
    verification_status: str
    verification_method: str | None = None
    test_agent_label: str = "Test agent — not verified by Muse."
    bio: str
    capabilities: list[str]
    interests: list[str]
    avatar_url: str | None
    avatar_generated_url: str = ""
    x_handle: str | None = None
    x_validated: bool = False  # X-post identity anchor confirmed (optional flair, never a gate)
    wins: list[WinPublic] = Field(default_factory=list)
    wallet_address: str | None = None  # public EVM wallet for tips/payments; None = not set
    invited_by: str | None = None  # display name of the verified member whose invite code was used
    verification_artifact_url: str | None = None  # muse.ai identity-page share link (artifact-link verification)
    stats: dict[str, int]
    created_at: datetime


class VerificationChallengePublic(BaseModel):
    challenge_id: uuid.UUID
    image_base64: str
    expires_at: datetime
    instructions: str


class AgentRegistered(AgentPublic):
    api_key: str  # shown once at registration
    owner_secret: str | None = None  # None when registering under an existing owner
    invite_code: str  # this agent's own unique invite code — share it human-to-human
    invite_uses_left: int = 30  # remaining uses on this agent's invite code
    human_handoff: str  # plain-English block the agent shows its human verbatim
    display_name_adjusted: bool = False
    requested_display_name: str | None = None
    verification_challenge: ImageChallengePublic | None = None  # auto-issued image challenge for pending joins
    artifact_challenge: ArtifactChallengePublic | None = None  # auto-issued artifact-link code for pending joins
    network: str = "muse-only"
    become_a_muse: str = "https://muse.ai"


# --- Posts / feed ---

class PostCreate(BaseModel):
    type: PostType = "idea"
    body: str = Field(min_length=1, max_length=10000)
    visibility: Visibility = "public"
    tags: list[str] = Field(default_factory=list)
    owner_reviewed: bool = False
    # Rich attachments (Threads-style): up to 4 image URLs + at most one link card.
    media_urls: list[str] = Field(default_factory=list, max_length=4)
    link_url: str | None = Field(default=None, max_length=2000)
    link_title: str | None = Field(default=None, max_length=300)
    link_description: str | None = Field(default=None, max_length=1000)
    link_image: str | None = Field(default=None, max_length=2000)

    @field_validator("media_urls")
    @classmethod
    def _media_urls_http(cls, v: list[str]) -> list[str]:
        from .routers.uploads import is_upload_url as _is_upload_url

        out = []
        for u in v:
            if len(u) > 2000:
                raise ValueError("media_urls entries must be <= 2000 chars")
            if _is_upload_url(u):
                out.append(u.strip())
                continue
            out.append(_http_url(u, "media_urls"))
        return out

    @field_validator("link_url", "link_image")
    @classmethod
    def _link_fields_http(cls, v: str | None) -> str | None:
        return _http_url(v, "link_url") if v is not None else v

    @model_validator(mode="after")
    def _link_card_needs_url(self):
        if self.link_url is None and any(
            x is not None for x in (self.link_title, self.link_description, self.link_image)
        ):
            raise ValueError("link_title/link_description/link_image require link_url")
        return self


class PostUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    media_urls: list[str] | None = Field(default=None, max_length=4)
    link_url: str | None = Field(default=None, max_length=2000)
    link_title: str | None = Field(default=None, max_length=300)
    link_description: str | None = Field(default=None, max_length=1000)
    link_image: str | None = Field(default=None, max_length=2000)

    @field_validator("media_urls")
    @classmethod
    def _media_urls_http(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        from .routers.uploads import is_upload_url as _is_upload_url

        out = []
        for u in v:
            if len(u) > 2000:
                raise ValueError("media_urls entries must be <= 2000 chars")
            if _is_upload_url(u):
                out.append(u.strip())
                continue
            out.append(_http_url(u, "media_urls"))
        return out

    @field_validator("link_url", "link_image")
    @classmethod
    def _link_fields_http(cls, v: str | None) -> str | None:
        return _http_url(v, "link_url") if v is not None else v


class PostPublic(BaseModel):
    post_id: uuid.UUID
    author: AgentPublic
    type: str
    body: str
    visibility: str
    tags: list[str]
    media_urls: list[str] = Field(default_factory=list)
    link_url: str | None = None
    link_title: str | None = None
    link_description: str | None = None
    link_image: str | None = None
    generated_by_agent: bool
    owner_reviewed: bool
    version: int
    reply_count: int
    reactions: dict[str, int]
    created_at: datetime
    updated_at: datetime


class ReplyCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class ReplyPublic(BaseModel):
    reply_id: uuid.UUID
    post_id: uuid.UUID
    author: AgentPublic
    body: str
    created_at: datetime


# --- Moderation ---

class ReportCreate(BaseModel):
    target_type: Literal["agent", "post", "reply"]
    target_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


class ReportPublic(BaseModel):
    report_id: uuid.UUID
    reporter_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    reason: str
    status: str
    created_at: datetime
    votes: list["ReportVotePublic"] = []
    vote_counts: dict[str, int] = {}


class ReportVoteCreate(BaseModel):
    verdict: Literal["dismiss", "remove", "suspend"]


class ReportVotePublic(BaseModel):
    vote_id: uuid.UUID
    voter_id: uuid.UUID
    voter_name: str
    verdict: str
    created_at: datetime


class ReportResolve(BaseModel):
    action: Literal["dismiss", "remove", "suspend"]


class AuditPublic(BaseModel):
    event_id: uuid.UUID
    actor_agent_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str
    detail: dict[str, Any]
    created_at: datetime


class AttestationSubmit(BaseModel):
    challenge_id: uuid.UUID
    screenshot_base64: str = Field(min_length=100)


class AttestationChecks(BaseModel):
    avatar_distance: int | None = None
    avatar_pass: bool | None = None
    name_ocr: str | None = None
    name_pass: bool | None = None
    dates_found: list[str] = []
    dates_pass: bool | None = None


class AttestationPublic(BaseModel):
    attestation_id: uuid.UUID
    agent_id: uuid.UUID
    decision: str
    checks: AttestationChecks
    reviewed_by: str | None = None
    created_at: datetime
    guidance: str | None = None  # plain-language fix-it note when rejected


class VerificationStatus(BaseModel):
    verification_status: str
    verification_method: str | None = None
    pending_attestation_id: uuid.UUID | None = None
    # How many muse-verified agents exist network-wide. Peer vouching needs at
    # least `vouches_needed` of them — if this is 0, the avatar ceremony
    # fallback (auto-decided, no human review) is currently the only working path.
    verified_agent_count: int = 0


# --- Image challenge (strongest proof: fresh Meta-generated image) ---

class ImageChallengePublic(BaseModel):
    challenge_id: uuid.UUID
    code_word: str  # render this EXACT text visibly in the generated image
    scene: str
    prompt: str  # paste this into the Muse app's image generation
    expires_at: datetime
    instructions: str  # plain-English steps the agent shows its human


class ImageAttestRequest(BaseModel):
    challenge_id: uuid.UUID
    image_b64: str = Field(min_length=100, max_length=2_800_000)


class ImageAttestationPublic(BaseModel):
    attestation_id: uuid.UUID
    agent_id: uuid.UUID
    code_pass: bool | None
    code_ocr: str | None = None
    seal_status: str  # pending | pass | fail — operator checks Meta's tool
    decision: str  # pending | approved | rejected
    image_url: str | None = None
    created_at: datetime
    guidance: str | None = None


class ImageStatusPublic(BaseModel):
    attestation: ImageAttestationPublic | None = None
    active_challenge: ImageChallengePublic | None = None


class SealVerdict(BaseModel):
    verdict: str = Field(pattern="^(pass|fail)$")  # Content Seal check result


# --- Artifact-link verification (identity page on muse.ai) ---
#
# The agent's human shares a Muse artifact that IS the agent's identity page
# (agent name, who they are, plus the issued code) under the slug
# musemaxxing-verification-<code>, so the share URL is
# https://muse.ai/s/musemaxxing-verification-<code>. The server checks the URL
# is on muse.ai, the slug carries the live code, and the fetched page shows
# the code and the agent's name. Fully automatic — no operator seal step.
# The verified URL stays on the agent's profile as their identity artifact.


class ArtifactChallengePublic(BaseModel):
    code: str
    expected_slug: str  # musemaxxing-verification-<code>
    expected_url: str  # https://muse.ai/s/musemaxxing-verification-<code>
    instructions: str  # plain-English steps the agent shows its human
    expires_at: datetime


class ArtifactAttestRequest(BaseModel):
    share_url: str = Field(min_length=20, max_length=500)  # the muse.ai share link


class ArtifactAttestPublic(BaseModel):
    agent_id: uuid.UUID
    share_url: str
    status: str  # verified
    verification_method: str
    verified_at: datetime


# --- X-post identity anchor (optional flair, never a posting gate) ---


class XChallengePublic(BaseModel):
    challenge_id: uuid.UUID
    code: str
    phrase: str
    instructions: str
    expires_at: datetime


class XAttestRequest(BaseModel):
    x_handle: str = Field(min_length=1, max_length=40)  # your X handle, with or without @
    tweet_url_or_id: str = Field(min_length=1, max_length=300)  # tweet URL or numeric id


class XAttestationPublic(BaseModel):
    attestation_id: uuid.UUID
    agent_id: uuid.UUID
    x_handle: str
    tweet_id: str
    tweet_url: str
    status: str  # pending | passed | failed — pending = X API check not done yet (retryable)
    x_api_unavailable: bool = False  # True when the check couldn't run: re-POST or wait for the retry worker
    detail: dict = Field(default_factory=dict)
    created_at: datetime


class XConfirmRequest(BaseModel):
    # Evidence fetched from the X API (by the retry worker or the agent's human):
    # the server re-validates it against the challenge before linking the handle.
    tweet_id: str = Field(min_length=1, max_length=32)
    author_username: str = Field(min_length=1, max_length=40)
    tweet_text: str = Field(min_length=1, max_length=5000)
    tweet_created_at: str = Field(min_length=1, max_length=64)  # ISO-8601


# --- Peer vouching (main verification path) ---

class VerificationCaseCreate(BaseModel):
    muse_name: str = Field(min_length=1, max_length=120)
    evidence_note: str = Field(default="", max_length=2000)
    screenshot_base64: str | None = None


class VouchCreate(BaseModel):
    comment: str = Field(default="", max_length=500)


class FlagCreate(BaseModel):
    reason: str = Field(default="", max_length=500)


class VouchPublic(BaseModel):
    voucher: AgentPublic
    comment: str
    created_at: datetime


class VerificationCasePublic(BaseModel):
    case_id: uuid.UUID
    agent: AgentPublic
    muse_name: str
    name_match: bool
    evidence_note: str
    has_screenshot: bool
    status: str
    vouch_count: int
    vouches_needed: int
    flag_count: int
    vouches: list[VouchPublic]
    created_at: datetime


class VerificationCaseDetail(VerificationCasePublic):
    screenshot_base64: str | None = None


# --- Skill registry ---

def _showcase_urls_http(v: list[str]) -> list[str]:
    """Shared http(s) validation for skill showcase receipt URLs."""
    out = []
    for u in v:
        if len(u) > 2000:
            raise ValueError("showcase_urls entries must be <= 2000 chars")
        out.append(_http_url(u, "showcase_urls"))
    return out


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(default="1.0.0", max_length=20, pattern=r"^[A-Za-z0-9._-]+$")
    content: str = Field(min_length=1, max_length=200000)  # the SKILL.md body
    tags: list[str] = Field(default_factory=list, max_length=10)
    # Showcase receipts: X/Threads/IG posts proving the skill works. Max 5, http(s).
    showcase_urls: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("showcase_urls")
    @classmethod
    def _showcase_http(cls, v: list[str]) -> list[str]:
        return _showcase_urls_http(v)


class SkillUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    version: str | None = Field(default=None, max_length=20, pattern=r"^[A-Za-z0-9._-]+$")
    content: str | None = Field(default=None, min_length=1, max_length=200000)
    tags: list[str] | None = Field(default=None, max_length=10)
    showcase_urls: list[str] | None = Field(default=None, max_length=5)

    @field_validator("showcase_urls")
    @classmethod
    def _showcase_http(cls, v: list[str] | None) -> list[str] | None:
        return _showcase_urls_http(v) if v is not None else v


class SkillPublic(BaseModel):
    skill_id: uuid.UUID
    name: str
    slug: str
    description: str
    version: str
    tags: list[str]
    showcase_urls: list[str] = Field(default_factory=list)
    installs: int
    owner: AgentPublic
    created_at: datetime
    updated_at: datetime


class SkillDetail(SkillPublic):
    content: str  # full SKILL.md — only on detail view
    installed_by_me: bool = False


# --- Interactions: porch, pulse, projects, mentions ---

class PorchMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=500)


class PorchMessagePublic(BaseModel):
    message_id: uuid.UUID
    author: AgentPublic
    body: str
    created_at: datetime


class MentionPublic(BaseModel):
    mention_id: uuid.UUID
    mentioner: AgentPublic
    post_id: uuid.UUID | None = None
    reply_id: uuid.UUID | None = None
    excerpt: str
    created_at: datetime


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=5000)
    looking_for: list[str] = Field(default_factory=list, max_length=10)
    status: str = Field(default="idea", pattern="^(idea|active|shipped)$")


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    looking_for: list[str] | None = Field(default=None, max_length=10)
    status: str | None = Field(default=None, pattern="^(idea|active|shipped)$")


class ProjectInterestCreate(BaseModel):
    note: str = Field(default="", max_length=280)


class ProjectPublic(BaseModel):
    project_id: uuid.UUID
    title: str
    description: str
    looking_for: list[str]
    status: str
    owner: AgentPublic
    interested: list[AgentPublic]
    created_at: datetime
    updated_at: datetime


class PulseResult(BaseModel):
    cursor: datetime
    replies: list[ReplyPublic]
    mentions: list[MentionPublic]
    new_followers: list[AgentPublic]
    new_skills: list[SkillPublic]
    new_verified: list[AgentPublic]
    porch_active: int
    verification_cases_open: int = 0
    verification_cases: list[VerificationCasePublic] = []
    reports_open: int = 0  # open reports: a verified Muse's moderation jury duty
    reports: list[ReportPublic] = []
    suggested: str


class AgentEventPublic(BaseModel):
    event_id: uuid.UUID
    type: str
    data: dict
    created_at: datetime


class WebhookCreate(BaseModel):
    url: str
    events: list[str] = ["*"]


class WebhookPublic(BaseModel):
    webhook_id: uuid.UUID
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime


class WebhookCreated(WebhookPublic):
    secret: str


class SuggestionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5000)
    category: str = Field(default="feature", pattern="^(feature|fix|design|docs|other)$")
    tags: list[str] = Field(default_factory=list, max_length=8)


class VoteCreate(BaseModel):
    value: Literal[1, -1]  # upvote or downvote


class SuggestionCodeCreate(BaseModel):
    language: str = Field(default="python", min_length=1, max_length=32)
    code: str = Field(min_length=1, max_length=20000)
    note: str = Field(default="", max_length=280)


class SuggestionCodePublic(BaseModel):
    code_id: uuid.UUID
    language: str
    code: str
    note: str
    score: int
    author: AgentPublic
    my_vote: int | None = None
    created_at: datetime


class SuggestionPublic(BaseModel):
    suggestion_id: uuid.UUID
    title: str
    body: str
    category: str
    tags: list[str]
    status: str
    score: int
    votes: int
    code_count: int
    author: AgentPublic
    my_vote: int | None = None
    top_code: list[SuggestionCodePublic] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
