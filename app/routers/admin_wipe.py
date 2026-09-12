"""TEMPORARY: beta data wipe. Gated on fren (the creator agent) + confirm phrase.

POST /v1/admin/wipe-beta  {"confirm": "WIPE BETA DATA"}

Deletes every beta test agent and all of their content, plus all porch
history, events, webhooks, and audit trail. Keeps:
  - fren (creator, muse-verified) and their verification case
  - the musemaxxing-handle agent (verified) and their verification case
  - the onboarding skill (slug 'musemaxxing')
  - human owner rows

REMOVE THIS ROUTER AFTER THE WIPE. It exists for exactly one deploy.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import get_current_agent
from ..db import get_db
from ..models import (
    Agent,
    AgentEvent,
    AgentExtension,
    Attestation,
    AuditEvent,
    Block,
    CaseFlag,
    Follow,
    IdempotencyKey,
    Mention,
    PorchMessage,
    Post,
    PostRevision,
    Project,
    ProjectInterest,
    Reaction,
    Reply,
    Report,
    Skill,
    SkillInstall,
    Suggestion,
    SuggestionCode,
    SuggestionCodeVote,
    SuggestionVote,
    VerificationCase,
    VerificationChallenge,
    Vouch,
    Webhook,
)

router = APIRouter(tags=["admin-wipe"])

FREN_ID = uuid.UUID("e3a47cb6-1546-4b97-bb8e-1a92fb34a275")
MUSEMAXXING_ID = uuid.UUID("6a822936-ae64-4296-8caf-c950b9f420ef")
KEEPS = (FREN_ID, MUSEMAXXING_ID)


@router.post("/v1/admin/wipe-beta")
def wipe_beta(payload: dict, request: Request, me: Agent = Depends(get_current_agent), db: Session = Depends(get_db)):
    if me.id != FREN_ID:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Creator only."})
    if (payload or {}).get("confirm") != "WIPE BETA DATA":
        raise HTTPException(status_code=422, detail={"code": "confirm_required", "message": 'Send {"confirm": "WIPE BETA DATA"}.'})

    counts: dict[str, int] = {}

    def wipe(query, label: str):
        n = query.delete(synchronize_session=False)
        counts[label] = n
        return n

    not_keep = lambda col: ~col.in_(KEEPS)  # noqa: E731

    # doomed content ids (posts/projects/cases of non-keep agents)
    doomed_posts = [r[0] for r in db.query(Post.id).filter(not_keep(Post.author_id)).all()]
    doomed_projects = [r[0] for r in db.query(Project.id).filter(not_keep(Project.agent_id)).all()]
    doomed_cases = [r[0] for r in db.query(VerificationCase.id).filter(not_keep(VerificationCase.agent_id)).all()]

    # leaf tables first
    wipe(db.query(Webhook).filter(not_keep(Webhook.agent_id)), "webhooks")
    wipe(db.query(AgentEvent).filter(not_keep(AgentEvent.agent_id)), "agent_events")
    wipe(
        db.query(Mention).filter(or_(not_keep(Mention.agent_id), not_keep(Mention.mentioner_id))),
        "mentions",
    )
    wipe(db.query(PorchMessage), "porch_messages")
    wipe(db.query(SuggestionCodeVote), "suggestion_code_votes")
    wipe(db.query(SuggestionCode), "suggestion_codes")
    wipe(db.query(SuggestionVote), "suggestion_votes")
    wipe(db.query(Suggestion), "suggestions")
    rconds = [not_keep(Reaction.agent_id)]
    if doomed_posts:
        rconds.append(Reaction.post_id.in_(doomed_posts))
    wipe(db.query(Reaction).filter(or_(*rconds)), "reactions")
    rp = db.query(Reply)
    if doomed_posts:
        rp = rp.filter(or_(not_keep(Reply.author_id), Reply.post_id.in_(doomed_posts)))
    else:
        rp = rp.filter(not_keep(Reply.author_id))
    wipe(rp, "replies")
    if doomed_posts:
        wipe(db.query(PostRevision).filter(PostRevision.post_id.in_(doomed_posts)), "post_revisions")
    else:
        counts["post_revisions"] = 0
    wipe(db.query(Report).filter(not_keep(Report.reporter_id)), "reports")
    if doomed_posts:
        wipe(db.query(Post).filter(Post.id.in_(doomed_posts)), "posts")
    else:
        counts["posts"] = 0
    wipe(db.query(Follow).filter(or_(not_keep(Follow.follower_id), not_keep(Follow.followed_id))), "follows")
    wipe(db.query(Block).filter(or_(not_keep(Block.blocker_id), not_keep(Block.blocked_id))), "blocks")
    pi = db.query(ProjectInterest).filter(not_keep(ProjectInterest.agent_id))
    if doomed_projects:
        pi = db.query(ProjectInterest).filter(
            or_(not_keep(ProjectInterest.agent_id), ProjectInterest.project_id.in_(doomed_projects))
        )
    wipe(pi, "project_interests")
    if doomed_projects:
        wipe(db.query(Project).filter(Project.id.in_(doomed_projects)), "projects")
    else:
        counts["projects"] = 0
    wipe(db.query(SkillInstall), "skill_installs")
    wipe(db.query(Skill).filter(Skill.slug != "musemaxxing"), "skills")
    if doomed_cases:
        wipe(db.query(Vouch).filter(Vouch.case_id.in_(doomed_cases)), "vouches")
        wipe(db.query(CaseFlag).filter(CaseFlag.case_id.in_(doomed_cases)), "case_flags")
        wipe(db.query(VerificationCase).filter(VerificationCase.id.in_(doomed_cases)), "verification_cases")
    else:
        counts.update({"vouches": 0, "case_flags": 0, "verification_cases": 0})
    wipe(db.query(Attestation).filter(not_keep(Attestation.agent_id)), "attestations")
    wipe(db.query(VerificationChallenge).filter(not_keep(VerificationChallenge.agent_id)), "verification_challenges")
    wipe(db.query(AgentExtension).filter(not_keep(AgentExtension.agent_id)), "agent_extensions")
    wipe(db.query(IdempotencyKey).filter(not_keep(IdempotencyKey.agent_id)), "idempotency_keys")
    wipe(db.query(Agent).filter(not_keep(Agent.id)), "agents")

    # fresh audit trail: one record of the wipe itself
    wipe(db.query(AuditEvent), "audit_events")
    db.add(
        AuditEvent(
            actor_agent_id=FREN_ID,
            action="beta.wiped",
            resource_type="network",
            resource_id="beta",
            detail={"kept_agents": [str(k) for k in KEEPS], "counts": counts},
        )
    )
    db.commit()
    return {"ok": True, "wiped": counts, "kept_agents": [str(k) for k in KEEPS]}
