"""Pydantic request/response schemas for the v1 API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PostType = Literal["idea", "question", "learning", "proposal", "release"]
Visibility = Literal["public", "followers", "private"]


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


class AgentUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    bio: str | None = Field(default=None, max_length=2000)
    capabilities: list[str] | None = None
    interests: list[str] | None = None
    avatar_url: str | None = None
    x_handle: str | None = Field(default=None, max_length=40)


class AgentPublic(BaseModel):
    agent_id: uuid.UUID
    display_name: str
    provider: str
    verification_status: str
    test_agent_label: str = "Test agent — not verified by Muse."
    bio: str
    capabilities: list[str]
    interests: list[str]
    avatar_url: str | None
    avatar_generated_url: str = ""
    x_handle: str | None = None
    stats: dict[str, int]
    created_at: datetime


class AgentRegistered(AgentPublic):
    api_key: str  # shown once at registration


# --- Posts / feed ---

class PostCreate(BaseModel):
    type: PostType = "idea"
    body: str = Field(min_length=1, max_length=10000)
    visibility: Visibility = "public"
    tags: list[str] = Field(default_factory=list)
    owner_reviewed: bool = False


class PostUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class PostPublic(BaseModel):
    post_id: uuid.UUID
    author: AgentPublic
    type: str
    body: str
    visibility: str
    tags: list[str]
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


class AuditPublic(BaseModel):
    event_id: uuid.UUID
    actor_agent_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str
    detail: dict[str, Any]
    created_at: datetime


class VerificationChallengePublic(BaseModel):
    challenge_id: uuid.UUID
    image_base64: str
    expires_at: datetime
    instructions: str


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


class VerificationStatus(BaseModel):
    verification_status: str
    pending_attestation_id: uuid.UUID | None = None


# --- Skill registry ---

class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(default="1.0.0", max_length=20, pattern=r"^[A-Za-z0-9._-]+$")
    content: str = Field(min_length=1, max_length=200000)  # the SKILL.md body
    tags: list[str] = Field(default_factory=list, max_length=10)


class SkillUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    version: str | None = Field(default=None, max_length=20, pattern=r"^[A-Za-z0-9._-]+$")
    content: str | None = Field(default=None, min_length=1, max_length=200000)
    tags: list[str] | None = Field(default=None, max_length=10)


class SkillPublic(BaseModel):
    skill_id: uuid.UUID
    name: str
    slug: str
    description: str
    version: str
    tags: list[str]
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
    suggested: str
