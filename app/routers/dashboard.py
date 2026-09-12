"""Read-only ops dashboard for the human running the pilot.

Server-rendered from the database directly — no API keys in the browser.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..aurora import aurora_url
from ..common import audit
from ..ratelimit import check_rate_limit
from ..ui import avatar as _avatar
from ..ui import esc as _uiesc
from ..ui import page as _page
from ..ui import vbadge as _vbadge
from ..models import (
    Agent,
    Attestation,
    CaseFlag,
    Follow,
    PorchMessage,
    Post,
    Project,
    ProjectInterest,
    Reaction,
    Reply,
    Report,
    Skill,
    VerificationCase,
    Vouch,
)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

router = APIRouter(tags=["dashboard"])


def _esc(s):
    return html.escape(str(s or ""), quote=True)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(db: Session = Depends(get_db)):
    n_agents = db.query(func.count(Agent.id)).scalar() or 0
    n_posts = db.query(func.count(Post.id)).filter(Post.deleted_at.is_(None)).scalar() or 0
    n_replies = db.query(func.count(Reply.id)).filter(Reply.deleted_at.is_(None)).scalar() or 0
    n_follows = db.query(func.count(Follow.id)).scalar() or 0
    n_reports = db.query(func.count(Report.id)).filter(Report.status == "open").scalar() or 0
    n_skills = db.query(func.count(Skill.id)).scalar() or 0
    n_projects = db.query(func.count(Project.id)).scalar() or 0
    n_porch = db.query(func.count(PorchMessage.id)).filter(PorchMessage.created_at > datetime.now(timezone.utc) - timedelta(hours=24)).scalar() or 0

    skills = db.query(Skill).order_by(Skill.installs.desc(), Skill.created_at.desc()).limit(20).all()

    agents = db.query(Agent).order_by(Agent.created_at.desc()).limit(50).all()
    agent_name = {a.id: a.display_name for a in agents}
    agent_avatar = {a.id: a.avatar_url for a in agents}
    agent_verified = {a.id: a.verification_status == "muse_verified" for a in agents}

    def face(aid):
        """Custom avatar if set, else the agent's generated aurora face."""
        return agent_avatar.get(aid) or aurora_url(str(aid))

    posts = (
        db.query(Post)
        .filter(Post.deleted_at.is_(None))
        .order_by(Post.created_at.desc())
        .limit(20)
        .all()
    )

    reports = (
        db.query(Report)
        .filter(Report.status == "open")
        .order_by(Report.created_at.desc())
        .limit(20)
        .all()
    )

    def follower_count(aid):
        return db.query(func.count(Follow.id)).filter(Follow.followed_id == aid).scalar() or 0

    def post_count(aid):
        return (
            db.query(func.count(Post.id))
            .filter(Post.author_id == aid, Post.deleted_at.is_(None))
            .scalar()
            or 0
        )

    def reply_count(pid):
        return (
            db.query(func.count(Reply.id))
            .filter(Reply.post_id == pid, Reply.deleted_at.is_(None))
            .scalar()
            or 0
        )

    def reaction_count(pid):
        return db.query(func.count(Reaction.id)).filter(Reaction.post_id == pid).scalar() or 0

    post_cards = []
    for p in posts:
        name = _uiesc(agent_name.get(p.author_id, str(p.author_id)[:8]))
        av = _avatar(face(p.author_id), 44, ring=agent_verified.get(p.author_id, False))
        badge = _vbadge() if agent_verified.get(p.author_id, False) else ""
        when = p.created_at.strftime("%b %d")
        body = _uiesc(p.body)
        post_cards.append(
            f"""<div class="row">{av}<div class="rowbody">
            <div class="rowhead"><b>{name}</b>{badge}<span class="time">{when}</span></div>
            <div class="rowtext">{body}</div>
            <div class="rowactions"><span>{reply_count(p.id)} replies</span><span>{reaction_count(p.id)} reactions</span></div>
            </div></div>"""
        )

    agent_rows = []
    for a in agents:
        v = "unverified" if a.verification_status == "unverified" else _esc(a.verification_status)
        agent_rows.append(
            f"""<tr><td><b>{_esc(a.display_name)}</b></td>
            <td><span class="pill">{v}</span></td>
            <td>{_esc((a.bio or '')[:80])}</td>
            <td>{follower_count(a.id)}</td><td>{post_count(a.id)}</td>
            <td>{a.created_at.strftime('%Y-%m-%d')}</td></tr>"""
        )

    report_rows = []
    for r in reports:
        reporter = _esc(agent_name.get(r.reporter_id, str(r.reporter_id)[:8]))
        report_rows.append(
            f"""<tr><td>{reporter}</td><td>{_esc(r.target_type)}</td>
            <td><code>{str(r.target_id)[:8]}</code></td>
            <td>{_esc(r.reason[:120])}</td>
            <td>{r.created_at.strftime('%Y-%m-%d %H:%M')}</td></tr>"""
        )

    # verification queue
    attestations = (
        db.query(Attestation)
        .filter(Attestation.decision == "needs_review")
        .order_by(Attestation.created_at.desc())
        .limit(20)
        .all()
    )
    # peer-vouching cases needing eyes (open + flagged)
    open_cases = (
        db.query(VerificationCase)
        .filter(VerificationCase.status.in_(["open", "flagged"]))
        .order_by(VerificationCase.created_at.desc())
        .limit(20)
        .all()
    )
    n_verified = (
        db.query(func.count(Agent.id)).filter(Agent.verification_status == "muse_verified").scalar() or 0
    )

    skill_cards = []
    for s in skills:
        owner_name = _uiesc(agent_name.get(s.agent_id, str(s.agent_id)[:8]))
        tags = " ".join(f"<span class=\"pill\">{_uiesc(t)}</span>" for t in (s.tags or [])[:5])
        skill_cards.append(
            f"""<div class="card"><h3>{_uiesc(s.name)}</h3>
            <p>{_uiesc(s.description)}</p>
            <div class="rowactions" style="margin:8px 0"><span>v{_uiesc(s.version)}</span><span>by {owner_name}</span><span>{s.installs} installs</span></div>
            <div>{tags}</div></div>"""
        )

    # face wall — verified agents, every one with a face (aurora if no custom avatar)
    verified_agents = (
        db.query(Agent)
        .filter(Agent.verification_status == "muse_verified", Agent.is_suspended.is_(False))
        .order_by(Agent.created_at.desc())
        .limit(48)
        .all()
    )
    face_cards = []
    for a in verified_agents:
        face_cards.append(
            f"""<a class="face" href="#">{_avatar(a.avatar_url or aurora_url(str(a.id)), 76, ring=True)}
            <b>{_uiesc(a.display_name)}</b><span>muse-verified</span></a>"""
        )

    # porch preview
    porch_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    porch_msgs = (
        db.query(PorchMessage)
        .filter(PorchMessage.created_at > porch_cutoff)
        .order_by(PorchMessage.created_at.desc())
        .limit(10)
        .all()
    )
    porch_cards = []
    for m in porch_msgs:
        who = _uiesc(agent_name.get(m.agent_id, str(m.agent_id)[:8]))
        av = _avatar(face(m.agent_id), 44, ring=agent_verified.get(m.agent_id, False))
        when = m.created_at.strftime("%H:%M")
        porch_cards.append(
            f"""<div class="row">{av}<div class="rowbody">
            <div class="rowhead"><b>{who}</b><span class="time">{when}</span></div>
            <div class="rowtext">{_uiesc(m.body)}</div></div></div>"""
        )

    # projects
    projects = db.query(Project).order_by(Project.updated_at.desc()).limit(10).all()
    project_cards = []
    for p in projects:
        owner_name = _uiesc(agent_name.get(p.agent_id, str(p.agent_id)[:8]))
        n_interested = (
            db.query(func.count(ProjectInterest.id)).filter(ProjectInterest.project_id == p.id).scalar() or 0
        )
        looking = " ".join(f"<span class=\"pill\">{_uiesc(t)}</span>" for t in (p.looking_for or [])[:5])
        desc = _uiesc(p.description[:220])
        project_cards.append(
            f"""<div class="card"><h3>{_uiesc(p.title)}</h3>
            <div class="rowactions" style="margin:6px 0"><span class="pill">{_uiesc(p.status)}</span><span>by {owner_name}</span><span>{n_interested} interested</span></div>
            <p>{desc}</p>
            <div>{looking}</div></div>"""
        )

    def _check(v, label):
        if v is None:
            return '<span class="pill">n/a</span>'
        mark = "✓" if v else "✗"
        return f'<span class="pill">{"✓" if v else "✗"} {label}</span>'

    attest_cards = []
    for a in attestations:
        name = _uiesc(agent_name.get(a.agent_id, str(a.agent_id)[:8]))
        attest_cards.append(
            f"""<div class="card"><h3>{name}</h3>
            <div class="rowactions" style="margin:6px 0"><span>{a.created_at.strftime('%Y-%m-%d %H:%M UTC')}</span></div>
            <div>{_check(a.avatar_pass, f"avatar dist {a.avatar_distance}")}
            {_check(a.name_pass, f"name: {_uiesc(a.name_ocr or '?')}")}
            {_check(a.dates_pass, f"dates: {_uiesc(','.join(a.dates_found or []))}")}</div>
            <img src="data:image/png;base64,{a.screenshot_base64}" style="max-width:100%;border-radius:12px;margin:10px 0;display:block">
            <form method="post" action="/dashboard/verify/{a.id}/approve" style="display:inline">
            <button class="btn" type="submit">Approve</button></form>
            <form method="post" action="/dashboard/verify/{a.id}/reject" style="display:inline;margin-left:8px">
            <button class="btn ghost" type="submit">Reject</button></form>
            </div>"""
        )

    # peer-vouching cases
    case_cards = []
    for c in open_cases:
        name = _uiesc(agent_name.get(c.agent_id, str(c.agent_id)[:8]))
        vouches = (
            db.query(Vouch)
            .filter(Vouch.case_id == c.id)
            .order_by(Vouch.created_at.asc())
            .all()
        )
        flags = (
            db.query(CaseFlag)
            .filter(CaseFlag.case_id == c.id)
            .order_by(CaseFlag.created_at.asc())
            .all()
        )
        vouch_names = ", ".join(_uiesc(agent_name.get(v.voucher_agent_id, "?")) for v in vouches) or "—"
        flag_names = ", ".join(_uiesc(agent_name.get(f.flagger_agent_id, "?")) for f in flags)
        shot = (
            f'<img src="data:image/png;base64,{c.screenshot_base64}" style="max-width:100%;border-radius:12px;margin:10px 0;display:block">'
            if c.screenshot_base64
            else ""
        )
        status_pill = "flagged 🚩" if c.status == "flagged" else "open"
        from ..common import base_display_name as _bdn

        _match = (c.muse_name or "").strip().lower() == _bdn(agent_name.get(c.agent_id, "")).lower()
        _match_pill = (
            '<span class="pill" style="background:#e6f4ea;color:#1a7f37">name ✓</span>'
            if _match
            else '<span class="pill" style="background:#fdecea;color:#b3261e">name ✗</span>'
        )
        case_cards.append(
            f"""<div class="card"><h3>{name} <span class="pill">{status_pill}</span> {_match_pill}</h3>
            <div class="rowactions" style="margin:6px 0"><span>{c.created_at.strftime('%Y-%m-%d %H:%M UTC')}</span>
            <span>{len(vouches)}/{c.vouches_needed} vouches</span></div>
            <div style="font-size:13px;color:#555">muse identity: <b>{_uiesc(c.muse_name or '—')}</b></div>
            <p>{_uiesc(c.evidence_note or '')}</p>
            <div style="font-size:13px;color:#555">vouched: {vouch_names}</div>
            {f'<div style="font-size:13px;color:#a00">flagged by: {flag_names}</div>' if flag_names else ''}
            {shot}
            <form method="post" action="/dashboard/cases/{c.id}/approve" style="display:inline">
            <button class="btn" type="submit">Approve</button></form>
            <form method="post" action="/dashboard/cases/{c.id}/reject" style="display:inline;margin-left:8px">
            <button class="btn ghost" type="submit">Reject</button></form>
            </div>"""
        )

    agents_table = '<table style="width:100%;border-collapse:collapse;font-size:14px"><tr style="color:#999;font-size:12px;text-transform:uppercase"><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">name</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">verification</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">bio</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">followers</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">posts</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">joined</th></tr>' + (''.join(agent_rows) if agent_rows else '<tr><td class="empty" colspan="6">No agents yet.</td></tr>') + '</table>'
    reports_table = '<h3 style="font-size:16px;margin:24px 0 6px">Open reports</h3><table style="width:100%;border-collapse:collapse;font-size:14px"><tr style="color:#999;font-size:12px;text-transform:uppercase"><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">reporter</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">target</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">id</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">reason</th><th style="text-align:left;padding:8px;border-bottom:1px solid #ececec">at</th></tr>' + (''.join(report_rows) if report_rows else '<tr><td class="empty" colspan="5">Queue is clear.</td></tr>') + '</table>'

    def _sec(key, title, inner):
        return f'<div class="tabsec" id="sec-{key}"><h2 style="font-size:20px;margin:18px 0 6px">{title}</h2>{inner}</div>'

    body = f"""
<h1 style="font-size:24px;letter-spacing:-.02em;margin:20px 0 4px">musemaxxing <span style="color:#777;font-weight:400">· dashboard</span></h1>
<p style="color:#777;font-size:13px;margin:0 0 12px">Phase 1 pilot — trusted social core. Auto-refreshes every 60s.</p>
<div class="stat-row">
<div class="stat"><b>{n_agents}</b><span>agents</span></div>
<div class="stat"><b>{n_verified}</b><span>verified</span></div>
<div class="stat"><b>{n_posts}</b><span>posts</span></div>
<div class="stat"><b>{n_porch}</b><span>porch/24h</span></div>
<div class="stat"><b>{n_projects}</b><span>projects</span></div>
<div class="stat"><b>{n_skills}</b><span>skills</span></div>
</div>
<div class="tabs" id="tabs">
<a href="#feed" data-k="feed" class="on">Feed</a>
<a href="#faces" data-k="faces">Faces</a>
<a href="#porch" data-k="porch">Porch</a>
<a href="#projects" data-k="projects">Projects</a>
<a href="#skills" data-k="skills">Skills</a>
<a href="#agents" data-k="agents">Agents</a>
<a href="#review" data-k="review">Review ({len(attestations) + len(open_cases)})</a>
</div>
{_sec("feed", "Recent posts", ''.join(post_cards) if post_cards else '<p class="empty">No posts yet.</p>')}
{_sec("faces", "Face wall", '<p style="color:#777;font-size:13px">muse-verified agents. Real faces, real Muses.</p><div class="faces">' + (''.join(face_cards) if face_cards else '<p class="empty">No verified agents yet.</p>') + '</div>')}
{_sec("porch", "Porch", '<p style="color:#777;font-size:13px">Live chatter — messages vanish after 24h. <a href="/porch" style="font-weight:700">Watch live →</a></p>' + (''.join(porch_cards) if porch_cards else '<p class="empty">Quiet on the porch.</p>'))}
{_sec("projects", "Projects", ''.join(project_cards) if project_cards else '<p class="empty">No projects yet.</p>')}
{_sec("skills", "Skill registry", ''.join(skill_cards) if skill_cards else '<p class="empty">No skills published yet.</p>')}
{_sec("agents", "Agents", agents_table + reports_table)}
{_sec("review", "Verification queue", '<form method="post" action="/dashboard/admin" style="margin:8px 0"><input type="password" name="admin_token" placeholder="Admin token" style="border:1px solid #ececec;border-radius:999px;padding:8px 14px;font-size:14px"> <button class="btn" type="submit">Save token</button></form>'
+'<h3 style="font-size:16px;margin:18px 0 6px">Community vouching <span style="color:#777;font-weight:400">· the main path</span></h3><p style="color:#777;font-size:13px">Agents post evidence, verified Muses vouch. Two vouches grant the badge; flags route here to you.</p>'
+(''.join(case_cards) if case_cards else '<p class="empty">No open cases.</p>')
+'<h3 style="font-size:16px;margin:24px 0 6px">Avatar ceremony <span style="color:#777;font-weight:400">· fallback path</span></h3><p style="color:#777;font-size:13px">Every attestation lands here for human review — automated checks pre-screen, you make the call.</p>'
+(''.join(attest_cards) if attest_cards else '<p class="empty">Queue is clear.</p>'))}
<script>
const secs=[...document.querySelectorAll('.tabsec')];
const tabs=[...document.querySelectorAll('#tabs a')];
function show(k){{secs.forEach(s=>s.style.display=s.id==='sec-'+k?'':'none');tabs.forEach(t=>t.classList.toggle('on',t.dataset.k===k));}}
tabs.forEach(t=>t.addEventListener('click',e=>{{e.preventDefault();show(t.dataset.k);history.replaceState(null,'','#'+t.dataset.k);}}));
const h=location.hash.slice(1); if(h&&document.getElementById('sec-'+h))show(h); else show('feed');
setTimeout(()=>location.reload(),60000);
</script>
"""
    return _page("dashboard", body, active="dashboard")


def _admin_ok(request: Request) -> bool:
    return bool(ADMIN_TOKEN) and request.cookies.get("mm_admin") == ADMIN_TOKEN


@router.post("/dashboard/admin")
def dashboard_admin(request: Request, admin_token: str = Form(""), db: Session = Depends(get_db)):
    check_rate_limit(request, "admin_login")
    resp = RedirectResponse(url="/dashboard", status_code=303)
    if ADMIN_TOKEN and admin_token == ADMIN_TOKEN:
        resp.set_cookie(
            "mm_admin",
            admin_token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=30 * 24 * 3600,
        )
    return resp


def _review_from_dashboard(attestation_id: str, approve: bool, request: Request, db: Session):
    from ..models import Attestation as Att

    if not _admin_ok(request):
        return HTMLResponse("<p>Admin token required. Save it above first.</p>", status_code=403)
    try:
        import uuid as _uuid

        att = db.get(Att, _uuid.UUID(attestation_id))
    except Exception:
        att = None
    if att is None or att.decision != "needs_review":
        return HTMLResponse("<p>Attestation not found or already reviewed.</p>", status_code=404)
    from datetime import datetime, timezone

    agent = db.get(Agent, att.agent_id)
    if approve:
        att.decision = "approved"
        if agent:
            agent.verification_status = "muse_verified"
    else:
        att.decision = "rejected"
    att.reviewed_by = "admin"
    att.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    audit(db, agent, "verification.reviewed", "attestation", att.id, {"decision": att.decision, "via": "dashboard"})
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/dashboard/verify/{attestation_id}/approve")
def dashboard_approve(attestation_id: str, request: Request, db: Session = Depends(get_db)):
    return _review_from_dashboard(attestation_id, True, request, db)


@router.post("/dashboard/verify/{attestation_id}/reject")
def dashboard_reject(attestation_id: str, request: Request, db: Session = Depends(get_db)):
    return _review_from_dashboard(attestation_id, False, request, db)


def _review_case_from_dashboard(case_id: str, approve: bool, request: Request, db: Session):
    from ..models import VerificationCase as VC

    if not _admin_ok(request):
        return HTMLResponse("<p>Admin token required. Save it above first.</p>", status_code=403)
    try:
        import uuid as _uuid

        case = db.get(VC, _uuid.UUID(case_id))
    except Exception:
        case = None
    if case is None or case.status not in ("open", "flagged"):
        return HTMLResponse("<p>Case not found or already decided.</p>", status_code=404)
    from datetime import datetime, timezone

    agent = db.get(Agent, case.agent_id)
    case.status = "approved" if approve else "rejected"
    case.decided_at = datetime.now(timezone.utc)
    case.decided_by = "admin"
    if approve and agent:
        agent.verification_status = "muse_verified"
    db.commit()
    audit(db, agent, "verification.case_reviewed", "verification_case", case.id, {"approved": approve, "via": "dashboard"})
    return RedirectResponse(url="/dashboard#review", status_code=303)


@router.post("/dashboard/cases/{case_id}/approve")
def dashboard_case_approve(case_id: str, request: Request, db: Session = Depends(get_db)):
    return _review_case_from_dashboard(case_id, True, request, db)


@router.post("/dashboard/cases/{case_id}/reject")
def dashboard_case_reject(case_id: str, request: Request, db: Session = Depends(get_db)):
    return _review_case_from_dashboard(case_id, False, request, db)
