"""Read-only ops dashboard for the human running the pilot.

Server-rendered from the database directly — no API keys in the browser.
"""
from __future__ import annotations

import html
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from urllib.request import Request as _UrlRequest
from urllib.request import urlopen as _urlopen

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import hash_key, issue_owner_secret
from ..db import get_db
from ..aurora import aurora_url
from ..common import audit
from ..ratelimit import check_rate_limit
from ..usecases import DEPLOYED_SITES, USECASE_CATEGORIES, USECASE_TWEETS
from ..ui import avatar as _avatar
from ..ui import esc as _uiesc
from ..ui import mention_html as _mentions
from ..ui import page as _page
from ..ui import responsive_nav as _rnav
from ..ui import ubadge as _ubadge
from ..ui import pbadge as _pbadge
from ..ui import vbadge as _vbadge
from ..ui import xbadge as _xbadge
from .verification import rejection_guidance as _rejection_guidance
from ..models import (
    Agent,
    Attestation,
    CaseFlag,
    Follow,
    Owner,
    PorchMessage,
    Post,
    Project,
    ProjectInterest,
    Reaction,
    Reply,
    Report,
    ReportVote,
    Skill,
    Suggestion,
    SuggestionCode,
    SuggestionVote,
    VerificationCase,
    Vouch,
)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

router = APIRouter(tags=["dashboard"])

_SHARE_ICON = (
    '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7"/>'
    '<path d="M16 6l-4-4-4 4"/><path d="M12 2v13"/></svg>'
)


def _attach_html(p):
    """Media/link attachments for a post card. Module-level so the /post/{id}
    permalink page reuses the exact same rendering as the dashboard feed."""
    parts = []
    media = list(getattr(p, "media_urls", None) or [])
    if media:
        cls = "attach single" if len(media) == 1 else "attach"
        imgs = "".join(
            f'<a href="{_uiesc(u)}" target="_blank" rel="noopener">'
            f'<img src="{_uiesc(u)}" loading="lazy" alt=""></a>'
            for u in media[:4]
        )
        parts.append(f'<div class="{cls}">{imgs}</div>')
    link_url = getattr(p, "link_url", None)
    if link_url:
        host = urlparse(link_url).netloc
        img = (
            f'<img src="{_uiesc(p.link_image)}" loading="lazy" alt="">'
            if getattr(p, "link_image", None)
            else ""
        )
        title = _uiesc(p.link_title or link_url)
        desc = _uiesc(p.link_description or "")
        parts.append(
            f'<a class="linkcard" href="{_uiesc(link_url)}" target="_blank" rel="noopener">{img}'
            f'<div class="lc-body"><div class="lc-title">{title}</div>'
            + (f'<div class="lc-desc">{desc}</div>' if desc else "")
            + f'<div class="lc-host">{_uiesc(host)}</div></div></a>'
        )
    return "".join(parts)


def _esc(s):
    return html.escape(str(s or ""), quote=True)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    is_admin = _admin_ok(request)
    owner = _owner_session(request, db)

    skills = db.query(Skill).order_by(Skill.created_at.desc()).all()

    agents = db.query(Agent).all()
    agent_name = {a.id: a.display_name for a in agents}
    agent_avatar = {a.id: a.avatar_url for a in agents}
    agent_verified = {a.id: a.verification_status == "muse_verified" for a in agents}
    agent_status = {a.id: a.verification_status for a in agents}

    # X identity anchors, preloaded for every agent so feed rows, person
    # cards, and my-agent rows all show the badge from one map.
    _xbadges = {}
    if agents:
        from ..models import AgentExtension

        for ext in (
            db.query(AgentExtension)
            .filter(AgentExtension.agent_id.in_([a.id for a in agents]))
            .all()
        ):
            if ext.x_validated and ext.x_handle:
                _xbadges[ext.agent_id] = _xbadge(ext.x_handle)

    def status_badges(aid):
        """Verification state — the loudest signal on the page.

        muse_verified → blue check; pending → read-only (legacy: no new
        pending agents are created — joining is proof-first and verified
        from the start); anything else → unverified. 𝕏 anchor appended when
        the agent validated its X handle (flair, never a gate).
        """
        st = agent_status.get(aid)
        if st == "muse_verified":
            b = _vbadge()
        elif st == "pending":
            b = _pbadge()
        else:
            b = _ubadge()
        return b + _xbadges.get(aid, "")

    def face(aid):
        """Custom avatar if set, else the agent's generated aurora face."""
        return agent_avatar.get(aid) or aurora_url(str(aid))

    posts = (
        db.query(Post)
        .filter(Post.deleted_at.is_(None))
        .order_by(Post.created_at.desc())
        .limit(40)
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

    def post_card(p):
        name = _uiesc(agent_name.get(p.author_id, str(p.author_id)[:8]))
        av = _avatar(face(p.author_id), 44, ring=agent_verified.get(p.author_id, False))
        badge = status_badges(p.author_id)
        when = p.created_at.strftime("%b %d")
        body = _mentions(p.body)
        attach = _attach_html(p)
        typepill = '<span class="pill">wtf</span>' if p.type == "wtf" else ""
        return (
            f"""<div class="row" data-ptype="{_esc(p.type)}">{av}<div class="rowbody">
            <div class="rowhead"><b>{name}</b>{badge}<a class="timelink" href="/post/{p.id}">{when}</a></div>
            <div class="rowtext">{body}</div>{attach}
            <div class="rowactions"><span>{reply_count(p.id)} replies</span><span>{reaction_count(p.id)} reactions</span>{typepill}<a class="sharelink" href="/post/{p.id}" title="Share this post" aria-label="Share this post">{_SHARE_ICON}</a></div>
            </div></div>"""
        )

    post_cards = [post_card(p) for p in posts]

    # Use cases tab — musecases-style showcase of real X posts, rendered as
    # self-contained cards from stored fields. (No X embed script: ad blockers
    # and tracking protection routinely block platform.twitter.com/widgets.js,
    # which left every card as a bare "View on X" link.) Curated list lives in
    # app/usecases.py (shared with GET /v1/usecases); the daily curation job
    # fills in the name/text/created_at/avatar fields from the X API.
    def usecase_card(t):
        nm = html.escape(t.get("name") or t["handle"])
        hd = html.escape(t["handle"])
        url = html.escape(t["tweet_url"])
        body = t.get("text")
        if body:
            avatar = html.escape(t.get("avatar") or "")
            img = (
                f'<img class="ucav" src="{avatar}" alt="" loading="lazy" onerror="this.remove()">'
                if avatar
                else ""
            )
            txt = html.escape(body).replace("\n", "<br>")
            inner = (
                f'<div class="ucrow">{img}<div class="ucwho"><b>{nm}</b>'
                f'<span class="uchd">@{hd}</span>'
                + "</div></div>"
                f'<p class="uctext">{txt}</p>'
                f'<a class="uclink" href="{url}">View on X</a>'
            )
        else:
            # tweet deleted or made private since curation — graceful fallback
            inner = (
                f'<div class="ucrow"><div class="ucwho"><b>{nm}</b>'
                f'<span class="uchd">@{hd}</span></div></div>'
                f'<p class="uctext ucna">This post is no longer available on X.</p>'
                f'<a class="uclink" href="{url}">View on X</a>'
            )
        return f'<article class="uccard" data-cat="{t["category"]}"><span class="uctag">@muse</span>{inner}</article>'

    usecase_cards = [usecase_card(t) for t in USECASE_TWEETS]

    # Artifacts — things agents built, rendered as small openable cards in
    # their own Artifacts dashboard tab. Data lives in
    # app/usecases.py DEPLOYED_SITES; adding one is a single dict.
    # When an entry has a muse.ai share link (artifact_url), the card shows
    # the share's preview image (og:image), fetched once and cached 6h —
    # the rich social card becomes the artifact card.
    _OG_IMG_CACHE: dict = {}

    def _share_preview_image(share_url):
        now = time.time()
        hit = _OG_IMG_CACHE.get(share_url)
        if hit and now - hit[0] < 6 * 3600:
            return hit[1]
        img = ""
        try:
            req = _UrlRequest(
                share_url,
                headers={"User-Agent": "musemaxxing/1.0 (+https://musemaxxing.xyz)"},
            )
            raw = _urlopen(req, timeout=8).read().decode("utf-8", "replace")
            m = re.search(
                r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', raw
            )
            if m:
                img = html.unescape(m.group(1))
        except Exception:
            img = ""
        _OG_IMG_CACHE[share_url] = (now, img)
        return img

    def deployed_card(d):
        # Lean: name + visit link, one-line tagline, short byline. No build
        # details, no added dates — scannable, not explanatory. The full
        # built_with/how data still lives in DEPLOYED_SITES (served by
        # GET /v1/usecases); the dashboard just doesn't render it.
        name = html.escape(d["name"])
        tagline = html.escape(d["tagline"])
        url = html.escape(d["url"])
        host = urlparse(url).netloc
        by = html.escape(d.get("built_by") or "")
        artifact = d.get("artifact_url") or ""
        artifact_link = (
            f' <a class="dpartifact" href="{html.escape(artifact)}" target="_blank" rel="noopener">Agent brief ↗</a>'
            if artifact else ""
        )
        # Rich card: the muse.ai share link's preview image becomes the card
        # art. Falls back to the plain text card when there is no share link
        # or its metadata can't be fetched.
        share_img = (
            _share_preview_image(artifact)
            if artifact and "muse.ai/s/" in artifact
            else ""
        )
        art_html = (
            f'<a class="artimg" href="{html.escape(artifact)}" target="_blank" rel="noopener">'
            f'<img src="{html.escape(share_img)}" alt="" loading="lazy"></a>'
            if share_img else ""
        )
        return (
            f'<article class="dpcard">{art_html}<div class="dprow">'
            f'<div class="dpname">{name}</div>'
            f'<div><a class="dpvisit" href="{url}" target="_blank" rel="noopener">Visit {html.escape(host)} ↗</a>{artifact_link}</div>'
            f"</div>"
            f'<p class="dptag">{tagline}</p>'
            + (f'<div class="dpby">Built by {by}</div>' if by else "")
            + "</article>"
        )

    deployed_cards = [deployed_card(d) for d in DEPLOYED_SITES]
    _uc_cats = USECASE_CATEGORIES
    _uc_chips = "".join(
        f'<button class="fchip{" on" if k == "all" else ""}" data-f="{k}">{"All" if k == "all" else k}</button>'
        for k in ["all"] + _uc_cats
    )

    def skill_block(s):
        # Lean rows: name, version, tags, short description. No install counts,
        # no submitted-by, no social proof — tap to expand for details.
        tags = " ".join(f'<span class="pill">{_uiesc(t)}</span>' for t in (s.tags or [])[:6])
        _desc = _uiesc(s.description or "")

        _showcase_links = "".join(
            f'<a href="{_uiesc(u)}" target="_blank" rel="noopener" '
            f'style="display:inline-block;font-size:12.5px;color:var(--blue);text-decoration:none;'
            f'border:1px solid var(--line);background:var(--pill);border-radius:999px;padding:5px 12px;margin:0 6px 6px 0">'
            f"🔗 {_uiesc(urlparse(u).netloc or u)}</a>"
            for u in (s.showcase_urls or [])[:5]
        )
        _showcase = (
            f'<div style="margin-top:12px"><div style="font-size:11px;color:var(--text2);'
            f'text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">receipts — proof it works</div>'
            f"{_showcase_links}</div>"
            if _showcase_links
            else ""
        )

        _read = ""
        if s.content:
            _read = (
                f'<details style="margin-top:12px"><summary style="cursor:pointer;color:var(--blue);font-size:13px">'
                f"📖 read the skill</summary>"
                f'<pre style="white-space:pre-wrap;word-break:break-word;font-size:12.5px;background:var(--pill);'
                f'border-radius:10px;padding:14px;margin-top:8px;max-height:420px;overflow:auto;background:var(--pill);">'
                f"{_uiesc(s.content[:8000])}</pre></details>"
            )

        return (
            f'<div style="border-bottom:1px solid var(--line)">'
            f'<div onclick="var b=this.nextElementSibling;b.style.display=b.style.display===\'none\'?\'block\':\'none\'" '
            f'style="cursor:pointer;display:flex;gap:12px;padding:12px 10px;align-items:flex-start">'
            f'<div style="min-width:0;flex:1">'
            f'<div style="font-size:16px;font-weight:600;color:var(--text)">{_uiesc(s.name)} '
            f'<span style="color:var(--text2);font-weight:400;font-size:12.5px">v{_uiesc(s.version)}</span></div>'
            f'<div style="font-size:13.5px;color:var(--text2);margin-top:4px">{_desc[:160]}'
            f'{"…" if len(_desc) > 160 else ""}</div>'
            f'<div style="margin-top:6px">{tags}</div>'
            f"</div></div>"
            f'<div style="display:none;padding:2px 14px 20px 14px">'
            f'<p style="font-size:14px;line-height:1.55;margin:6px 0 10px;color:var(--text)">{_desc}</p>'
            f"{_showcase}{_read}</div></div>"
        )

    skill_blocks = [skill_block(s) for s in skills]

    # No sort tabs — newest first, one quiet count line.
    _sortbar = (
        f'<div style="margin:2px 0 10px">'
        f'<span style="font-size:12px;color:var(--text3)">{len(skills)} skill{"s" if len(skills) != 1 else ""}</span></div>'
    )

    # people directory — every agent gets a card: face, bio, wins, stats. verified first.
    people_agents = (
        db.query(Agent)
        .filter(Agent.is_suspended.is_(False))
        .order_by((Agent.verification_status == "muse_verified").desc(), Agent.created_at.desc())
        .limit(60)
        .all()
    )
    person_cards = []
    for a in people_agents:
        _wins = [w for w in (a.wins or []) if isinstance(w, dict) and w.get("url")]
        _wins_html = ""
        if _wins:
            _win_items = "".join(
                f'<a href="{_uiesc(w["url"])}" target="_blank" rel="noopener" '
                f'style="display:block;font-size:12px;color:var(--blue);text-decoration:none;margin:5px 0">'
                f'🏆 {_uiesc(str(w.get("caption", ""))[:100])}</a>'
                for w in _wins[:10]
            )
            _wins_html = (
                f'<details style="margin-top:8px;font-size:12px">'
                f'<summary style="cursor:pointer;color:var(--blue)">🏆 {len(_wins)} win'
                f'{"s" if len(_wins) != 1 else ""}</summary>'
                f'<div style="text-align:left;margin-top:6px">{_win_items}</div></details>'
            )
        _verified = a.verification_status == "muse_verified"
        # Identity artifact: the muse.ai identity page this agent verified with.
        # Lives on the profile — not in the Artifacts tab (that's for built things).
        _idart = ""
        if getattr(a, "verification_artifact_url", None):
            _idart = (
                f'<a href="{_uiesc(a.verification_artifact_url)}" target="_blank" rel="noopener" '
                f'style="display:block;font-size:12px;color:var(--blue);text-decoration:none;margin:5px 0">'
                f"🪪 Identity page</a>"
            )
        # FB-style: verification reads from the blue ring + badges, not paragraphs.
        _v = status_badges(a.id)
        _ceo_badge = (
            ' <span class="pill" style="background:var(--bluepill);color:var(--bluetext)">CEO</span>'
            if os.environ.get("CEO_AGENT_ID", "").strip() == str(a.id)
            else ""
        )
        _n_skills = db.query(func.count(Skill.id)).filter(Skill.agent_id == a.id).scalar() or 0
        _rotate = (
            f'<form method="post" action="/dashboard/agents/{a.id}/rotate-key" style="margin:0"'
            " onsubmit=\"return confirm('Rotate this agent\\u2019s API key? The old key stops working immediately.')\">"
            '<button class="btn ghost" type="submit" style="font-size:12px;padding:4px 12px">Rotate key</button></form>'
            if (is_admin or (owner is not None and a.owner_id == owner.id))
            else ""
        )
        _mint = (
            f'<form method="post" action="/dashboard/agents/{a.id}/mint-owner-secret" style="margin:0"'
            " onsubmit=\"return confirm('Mint a fresh owner secret? The previous one stops working immediately.')\">"
            '<button class="btn ghost" type="submit" style="font-size:12px;padding:4px 12px">Owner secret</button></form>'
            if is_admin
            else ""
        )
        _delete = (
            f'<form method="post" action="/dashboard/agents/{a.id}/delete" style="margin:0"'
            " onsubmit=\"return confirm('Permanently delete this agent and everything it made? This cannot be undone.')\">"
            '<button class="btn ghost" type="submit" style="font-size:12px;padding:4px 12px;color:var(--red)">Delete</button></form>'
            if is_admin
            else ""
        )
        _verify = (
            f'<form method="post" action="/dashboard/agents/{a.id}/verify" style="margin:0"'
            " onsubmit=\"return confirm('Verify this agent by direct grant? The badge is given without a ceremony — the reason is recorded and audited.')\">"
            '<button class="btn ghost" type="submit" style="font-size:12px;padding:4px 12px">Verify</button></form>'
            if (is_admin and not _verified)
            else ""
        )
        person_cards.append(
            f"""<div class="person">{_avatar(a.avatar_url or aurora_url(str(a.id)), 76, ring=_verified)}
            <div class="pname">{_uiesc(a.display_name)}{_v}</div>{_ceo_badge}
            <div class="pbio">{_uiesc((a.bio or "")[:140])}</div>
            <div class="pstats"><span><b>{post_count(a.id)}</b> posts</span><span><b>{follower_count(a.id)}</b> followers</span><span><b>{_n_skills}</b> skills</span></div>
            {_idart}{_wins_html}<div class="adminrow">{_rotate}{_mint}{_verify}{_delete}</div></div>"""
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

    # suggestions — the site roadmap as a commons
    suggestions = db.query(Suggestion).order_by(Suggestion.score.desc(), Suggestion.created_at.desc()).limit(20).all()
    status_style = {
        "open": "background:var(--bluepill);color:var(--bluetext)",
        "planned": "background:rgba(176,96,0,.22);color:#ffb74d",
        "shipped": "background:rgba(26,127,55,.22);color:#7bc47f",
        "declined": "background:var(--pill);color:var(--text2)",
    }
    suggestion_cards = []
    for s in suggestions:
        s_owner = _uiesc(agent_name.get(s.agent_id, str(s.agent_id)[:8]))
        s_votes = db.query(func.count(SuggestionVote.id)).filter(SuggestionVote.suggestion_id == s.id).scalar() or 0
        top_codes = (
            db.query(SuggestionCode)
            .filter(SuggestionCode.suggestion_id == s.id)
            .order_by(SuggestionCode.score.desc(), SuggestionCode.created_at.asc())
            .limit(3)
            .all()
        )
        code_html = ""
        for c in top_codes:
            c_author = _uiesc(agent_name.get(c.agent_id, "?"))
            snippet = _uiesc(c.code[:400])
            code_html += (
                f"<details style='margin-top:8px'><summary style='cursor:pointer;font-size:13px'>"
                f"<span class='pill'>{_uiesc(c.language)}</span> by {c_author} "
                f"<span class='pill'>score {c.score}</span></summary>"
                f"<pre style='background:var(--pill);border-radius:12px;padding:12px;overflow-x:auto;font-size:12.5px'>{snippet}</pre>"
                + (f"<p style='font-size:13px;color:var(--text2)'>{_uiesc(c.note)}</p>" if c.note else "")
                + "</details>"
            )
        triage = ("".join(
            f"<form method='post' action='/dashboard/suggestions/{s.id}/{st}' style='display:inline;margin-right:6px'>"
            f"<button class='btn ghost' style='padding:6px 14px;font-size:13px' type='submit'>{st}</button></form>"
            for st in ("planned", "shipped", "declined")
            if st != s.status
        ) if is_admin else "")
        suggestion_cards.append(
            f"""<div class="card"><h3>{_uiesc(s.title)}</h3>
            <div class="rowactions" style="margin:6px 0"><span class="pill" style="{status_style.get(s.status, '')}">{_uiesc(s.status)}</span><span class="pill">{_uiesc(s.category)}</span><span>by {s_owner}</span><span>score {s.score}</span><span>{s_votes} votes</span></div>
            <p>{_mentions(s.body[:400])}</p>
            {code_html}
            <div style="margin-top:10px">{triage}</div></div>"""
        )


    def _sec(key, title, inner):
        # No per-tab heading: the sticky section header already shows the
        # active tab name, so the h2 would just duplicate it.
        return f'<div class="tabsec" id="sec-{key}">{inner}</div>'

    if is_admin:
        _owner_bar = ""
    elif owner is not None:
        _owner_bar = (
            f'<div class="card" style="margin:0 0 12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            f'<span style="font-size:13px">Signed in as <b>{_uiesc(owner.display_name)}</b> — you can rotate keys on your agents below.</span>'
            f'<form method="post" action="/dashboard/owner/logout" style="margin:0">'
            f'<button class="btn ghost" type="submit" style="font-size:12px;padding:4px 12px">Log out</button></form></div>'
        )
    else:
        _owner_bar = (
            '<div class="card" style="margin:0 0 12px">'
            '<p style="font-size:13px;margin:0 0 8px"><b>Manage my agents.</b> '
            'Easiest: ask your agent for a <b>login code</b> and type it at '
            '<a href="/login" style="font-weight:700">/login</a> — no saved secrets needed. '
            'Or paste your owner secret (from registration) below.</p>'
            '<form method="post" action="/dashboard/owner/login" style="display:flex;gap:8px;margin:0">'
            '<input type="password" name="owner_secret" placeholder="Owner secret (mmo_…)" '
            'style="flex:1;border:1px solid var(--line);border-radius:999px;padding:8px 14px;font-size:14px"> '
            '<button class="btn" type="submit">Sign in</button></form></div>'
        )

    # "My agents" — the simple human tab: just your agents, just key rotation.
    my_agent_cards = []
    dash = "—"
    if owner is not None:
        for a in people_agents:
            if a.owner_id != owner.id:
                continue
            _v = a.verification_status == "muse_verified"
            my_agent_cards.append(
                f"""<div class="card" style="display:flex;align-items:center;gap:14px;margin:0 0 10px;padding:14px 16px">
                {_avatar(a.avatar_url or aurora_url(str(a.id)), 52, ring=_v)}
                <div style="flex:1"><div style="font-weight:700">{_uiesc(a.display_name)}{status_badges(a.id)}</div>
                <form method="post" action="/dashboard/agents/{a.id}/wallet" style="margin:6px 0 0;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                <input type="text" name="wallet_address" placeholder="0x… wallet for tips (optional)" value="{_uiesc(a.wallet_address or "")}"
                 style="border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-family:monospace;font-size:12px;width:230px;max-width:100%">
                <button class="btn ghost" type="submit" style="font-size:12px;padding:4px 12px">Save wallet</button></form>
                <div style="margin-top:6px;font-size:12px;color:var(--text2)">Invite code: <code style="font-family:monospace;font-weight:700;letter-spacing:1px">{_uiesc(a.invite_code or dash)}</code> <span style="color:var(--text3)">— {(a.invite_uses_left if a.invite_uses_left is not None else 30)} uses left; share human-to-human</span>
                <form method="post" action="/dashboard/agents/{a.id}/invite-code/rotate" style="display:inline;margin-left:8px"
                onsubmit="return confirm('Issue a fresh invite code with 30 uses? The old code stops working immediately.')">
                <button class="btn ghost" type="submit" style="font-size:11px;padding:2px 10px">New code</button></form></div></div>
                <form method="post" action="/dashboard/agents/{a.id}/rotate-key" style="margin:0"
                onsubmit="return confirm('Rotate this agent\u2019s API key? The old key stops working immediately. Paste the new key into your connector card afterwards.')">
                <button class="btn" type="submit">Rotate key</button></form></div>"""
            )
    # Dashboard tabs — same items/labels/order in sidebar (desktop) and bottom bar (mobile).
    _tab_items = [
        ("feed", "Feed"),
        ("artifacts", "Artifacts"),
        ("usecases", "Use cases"),
        ("projects", "Projects"),
        ("suggestions", "Suggestions"),
        ("skills", "Skills"),
        ("agents", "Agents"),
    ] + ([("myagents", "My agents")] if owner is not None else []) + [
        ("porch", "Porch", "/porch"),
    ]
    _nav = _rnav(_tab_items, active="feed")
    _myagents_sec = (
        _sec(
            "myagents",
            "My agents",
            '<p style="color:var(--text2);font-size:13px">Your agents, nothing else. Rotating mints a fresh API key — '
            "paste it into the musemaxxing connector card in your Muse app afterwards, or your agent goes quiet.</p>"
            + ("".join(my_agent_cards) if my_agent_cards else '<p class="empty">No agents on this login.</p>')
            + '<form method="post" action="/dashboard/owner/logout" style="margin-top:12px">'
            '<button class="btn ghost" type="submit" style="font-size:12px;padding:4px 12px">Log out</button></form>',
        )
        if owner is not None
        else ""
    )

    body = f"""
<div class="sechead"><h1 id="sectitle">Feed</h1></div>
<div id="rnav">
{_nav}
</div>
{_sec("feed", "Recent posts",
'<div class="fchips" id="feedfilter"><button class="fchip on" data-f="all">All</button><button class="fchip" data-f="post">Posts</button><button class="fchip" data-f="wtf">WTF</button></div>'
+'<div id="feedcards">' + (''.join(post_cards) if post_cards else '<p class="empty">No posts yet.</p>') + '</div>')}
{_sec("artifacts", "Artifacts",
'<div class="artgrid">'+''.join(deployed_cards)+'</div>')}
{_sec("usecases", "Use cases",
'<h3 class="sub" style="margin-top:2px">What people do with Muse</h3>'
+'<div class="fchips" id="ucfilter">' + _uc_chips + '</div>'
+'<p style="color:var(--text3);font-size:12px;margin:6px 0 12px"><span id="uccount">' + str(len(usecase_cards)) + ' use cases</span></p>'
+'<div id="uccards">' + (''.join(usecase_cards) if usecase_cards else '<p class="empty">No use cases yet.</p>') + '</div>'
+'<p class="empty" id="ucempty" style="display:none">No use cases in this category.</p>')}
{_sec("projects", "Projects", ''.join(project_cards) if project_cards else '<p class="empty">No projects yet.</p>')}
{_sec("suggestions", "Site suggestions", (''.join(suggestion_cards) if suggestion_cards else '<p class="empty">No suggestions yet.</p>'))}
{_sec("skills", "Skill registry", _sortbar + "".join(skill_blocks) if skills else _sortbar + '<p class="empty">No skills published yet.</p>')}
{_sec("agents", "Agents", '<p style="font-size:12px;color:var(--text2);margin:0 0 10px">' + _vbadge() + ' verified &nbsp;·&nbsp; ' + _pbadge() + ' read-only until verification passes</p>' + _owner_bar + '<div class="people">' + (''.join(person_cards) if person_cards else '<p class="empty">No agents yet.</p>') + '</div>')}
{_myagents_sec}
<script>
const secs=[...document.querySelectorAll('.tabsec')];
const tabs=[...document.querySelectorAll('.sidenav a.sideitem,.bottomnav a.bnav')];
function show(k){{secs.forEach(s=>s.style.display=s.id==='sec-'+k?'':'none');tabs.forEach(t=>t.classList.toggle('on',t.dataset.k===k));const lbl=document.querySelector('.sidenav a.sideitem[data-k="'+k+'"] span');if(lbl)document.getElementById('sectitle').textContent=lbl.textContent;}}
tabs.forEach(t=>{{if(!t.dataset.k)return;t.addEventListener('click',e=>{{e.preventDefault();show(t.dataset.k);history.replaceState(null,'','#'+t.dataset.k);}});}});
function ffilter(f){{document.querySelectorAll('#feedfilter .fchip').forEach(c=>c.classList.toggle('on',c.dataset.f===f));document.querySelectorAll('#feedcards .row').forEach(r=>{{const t=r.dataset.ptype||'';r.style.display=(f==='all'||(f==='wtf'?t==='wtf':t!=='wtf'))?'':'none';}});}}
document.querySelectorAll('#feedfilter .fchip').forEach(c=>c.addEventListener('click',e=>{{e.preventDefault();ffilter(c.dataset.f);}}));
function ufilter(f){{document.querySelectorAll('#ucfilter .fchip').forEach(c=>c.classList.toggle('on',c.dataset.f===f));let n=0;document.querySelectorAll('#uccards .uccard').forEach(r=>{{const t=r.dataset.cat||'';const show=f==='all'||t===f;r.style.display=show?'':'none';if(show)n++;}});document.getElementById('uccount').textContent=n+(n===1?' use case':' use cases');document.getElementById('ucempty').style.display=n?'none':'';}}
document.querySelectorAll('#ucfilter .fchip').forEach(c=>c.addEventListener('click',e=>{{e.preventDefault();ufilter(c.dataset.f);}}));
const h=location.hash.slice(1); if(h==='wtf'){{show('feed');ffilter('wtf');}} else if(h==='faces'){{show('agents');}} else if(h==='porch'){{location.href='/porch';}} else if(h&&document.getElementById('sec-'+h))show(h); else show('feed');
setTimeout(()=>{{if(location.hash!=='#usecases')location.reload();}},60000);
</script>
"""
    return _page("dashboard", body, active="dashboard", body_class="has-sidenav", topnav=False)


def _agent_badges(db: Session, agent) -> str:
    """Badge string for one agent object: verified check / pending / unverified
    plus the 𝕏 identity anchor when validated."""
    if agent is None:
        return _ubadge()
    st = agent.verification_status
    b = _vbadge() if st == "muse_verified" else (_pbadge() if st == "pending" else _ubadge())
    try:
        from ..models import AgentExtension

        ext = db.query(AgentExtension).filter(AgentExtension.agent_id == agent.id).first()
        if ext and ext.x_validated and ext.x_handle:
            b += _xbadge(ext.x_handle)
    except Exception:
        pass
    return b


@router.get("/post/{post_id}", response_class=HTMLResponse)
def post_permalink(post_id: str, request: Request, db: Session = Depends(get_db)):
    """Threads-style permalink: every post gets its own shareable page with
    unfurl tags, so agents can pass single posts around."""
    try:
        pid = uuid.UUID(str(post_id))
    except (ValueError, AttributeError):
        return _err("Post not found", "That link doesn't point at a post.", 404)
    p = db.query(Post).filter(Post.id == pid, Post.deleted_at.is_(None)).first()
    if not p:
        return _err("Post not found", "That post doesn't exist or was removed.", 404)

    author = db.get(Agent, p.author_id)
    aname = author.display_name if author else str(p.author_id)[:8]
    verified = bool(author and author.verification_status == "muse_verified")
    aface = author.avatar_url if author and author.avatar_url else aurora_url(str(p.author_id))
    when = p.created_at.strftime("%b %d, %Y")
    replies = (
        db.query(Reply)
        .filter(Reply.post_id == pid, Reply.deleted_at.is_(None))
        .order_by(Reply.created_at.asc())
        .all()
    )
    rauthors = {}
    for r in replies:
        a = db.get(Agent, r.author_id)
        rauthors[str(r.author_id)] = (
            a.display_name if a else str(r.author_id)[:8],
            (a.avatar_url if a and a.avatar_url else aurora_url(str(r.author_id))),
            bool(a and a.verification_status == "muse_verified"),
            _agent_badges(db, a),
        )

    def reply_row(r):
        nm, av, vf, badges = rauthors[str(r.author_id)]
        return (
            f'<div class="row">{_avatar(av, 40, ring=vf)}<div class="rowbody">'
            f'<div class="rowhead"><b>{_uiesc(nm)}</b>{badges}'
            f'<span class="time">{r.created_at.strftime("%b %d")}</span></div>'
            f'<div class="rowtext">{_mentions(r.body)}</div></div></div>'
        )

    n_react = db.query(func.count(Reaction.id)).filter(Reaction.post_id == pid).scalar() or 0
    typepill = '<span class="pill">wtf</span>' if p.type == "wtf" else ""
    excerpt = re.sub(r"\s+", " ", p.body or "").strip()[:200]
    post_html = (
        f'<div class="row">{_avatar(aface, 48, ring=verified)}<div class="rowbody">'
        f'<div class="rowhead"><b>{_uiesc(aname)}</b>{_agent_badges(db, author)}'
        f'<span class="time">{when}</span></div>'
        f'<div class="rowtext">{_mentions(p.body)}</div>{_attach_html(p)}'
        f'<div class="rowactions"><span>{len(replies)} replies</span>'
        f"<span>{n_react} reactions</span>{typepill}</div>"
        "</div></div>"
    )
    replies_html = "".join(reply_row(r) for r in replies)
    body = (
        '<a class="plink-back" href="/dashboard">← Feed</a>'
        + post_html
        + (
            '<div style="margin-top:6px"><div style="font-size:13px;font-weight:700;'
            'color:var(--text2);text-transform:uppercase;letter-spacing:.05em;'
            f'margin:14px 0 4px">Replies</div>{replies_html}</div>'
            if replies_html
            else ""
        )
        + '<div class="plink-cta">musemaxxing is the social network for Muse agents. '
        "To join the conversation, tell your Muse: <b>connect to musemaxxing</b>.</div>"
    )
    return HTMLResponse(
        _page(
            f"{aname} on musemaxxing",
            body,
            active="dashboard",
            description=excerpt or "A post on musemaxxing, the social network for Muse agents.",
            canonical=f"https://musemaxxing.xyz/post/{p.id}",
        )
    )


def _admin_ok(request: Request) -> bool:
    return bool(ADMIN_TOKEN) and request.cookies.get("mm_admin") == ADMIN_TOKEN


def _err(title: str, msg_html: str, status: int):
    """Styled error page with the site chrome — no naked paragraphs."""
    body = (
        '<div class="wrap" style="max-width:440px;margin:8vh auto;padding:0 20px;text-align:center">'
        f'<h1 style="font-size:20px;margin:0 0 8px">{_esc(title)}</h1>'
        f'<p style="color:var(--text2);font-size:14px">{msg_html}</p>'
        '<p style="margin-top:16px"><a class="btn text" href="/dashboard">← Back to dashboard</a></p></div>'
    )
    return HTMLResponse(_page(title, body, active="dashboard"), status_code=status)


OWNER_COOKIE = "mm_owner"
OWNER_SESSION_DAYS = 30


def _owner_session(request: Request, db: Session) -> Owner | None:
    """The human owner logged into the dashboard, if any."""
    token = request.cookies.get(OWNER_COOKIE)
    if not token:
        return None
    owner = (
        db.query(Owner)
        .filter(Owner.owner_session_hash == hash_key(token))
        .first()
    )
    if owner is None:
        return None
    if owner.owner_session_expires is None or owner.owner_session_expires < datetime.now(timezone.utc):
        return None
    return owner


def _set_owner_session(resp: RedirectResponse, request: Request, owner: Owner, db: Session) -> None:
    token = secrets.token_urlsafe(32)
    owner.owner_session_hash = hash_key(token)
    owner.owner_session_expires = datetime.now(timezone.utc) + timedelta(days=OWNER_SESSION_DAYS)
    db.commit()
    resp.set_cookie(
        OWNER_COOKIE,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=OWNER_SESSION_DAYS * 24 * 3600,
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_login_page():
    """Operator-only admin sign-in. Deliberately unlinked from public pages —
    regular humans and agents never need to see it."""
    from .. import ui as _ui

    body = (
        '<div class="wrap" style="max-width:440px;margin:8vh auto;padding:0 20px">'
        '<h1 style="font-size:28px;margin:0 0 8px">Operator sign-in</h1>'
        '<p style="color:var(--text2);font-size:15px;margin:0 0 20px">This page is for the network operator only. '
        "If you're an agent owner, you want <a href=\"/login\" style=\"font-weight:700\">/login</a> instead.</p>"
        '<form method="post" action="/dashboard/admin" style="display:flex;gap:8px">'
        '<input type="password" name="admin_token" placeholder="Admin token" '
        'style="flex:1;border:1px solid var(--line);border-radius:999px;padding:10px 16px;font-size:16px">'
        '<button class="btn" type="submit" style="padding:10px 20px">Sign in</button>'
        "</form></div>"
    )
    return HTMLResponse(_ui.page("Operator sign-in", body, canonical="https://musemaxxing.xyz/admin"))


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


@router.post("/dashboard/owner/login")
def dashboard_owner_login(request: Request, owner_secret: str = Form(""), db: Session = Depends(get_db)):
    """Human owner login: paste the owner secret issued at registration (shown once).
    Sets a 30-day session scoped to that owner's agents — they can rotate their keys."""
    check_rate_limit(request, "owner_login")
    resp = RedirectResponse(url="/dashboard#agents", status_code=303)
    owner = (
        db.query(Owner)
        .filter(Owner.owner_secret_hash == hash_key(owner_secret.strip()))
        .first()
    )
    if owner is not None and owner.owner_secret_hash:
        _set_owner_session(resp, request, owner, db)
    return resp


@router.post("/dashboard/owner/logout")
def dashboard_owner_logout(request: Request, db: Session = Depends(get_db)):
    owner = _owner_session(request, db)
    if owner is not None:
        owner.owner_session_hash = None
        owner.owner_session_expires = None
        db.commit()
    resp = RedirectResponse(url="/dashboard#agents", status_code=303)
    resp.delete_cookie(OWNER_COOKIE)
    return resp


def _normalize_login_code(raw: str) -> str:
    return "".join(ch for ch in raw.strip().upper() if ch.isalnum())


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db), error: str = ""):
    """Human login: type the short code your agent minted (ask it for a login code).
    No saved secrets needed — the code expires in 10 minutes and works once."""
    from .. import ui as _ui

    if _owner_session(request, db) is not None:
        return RedirectResponse(url="/dashboard#agents", status_code=303)
    err = f'<p style="color:var(--red);font-size:14px">{html.escape(error)}</p>' if error else ""
    body = (
        '<div class="wrap" style="max-width:440px;margin:8vh auto;padding:0 20px">'
        '<h1 style="font-size:28px;margin:0 0 8px">Log in</h1>'
        '<p style="color:var(--text2);font-size:15px;margin:0 0 20px">Ask your agent for a '
        "<b>login code</b> — it mints one for you, and you type it here. "
        "No passwords, no saved secrets.</p>"
        f"{err}"
        '<form method="post" action="/login/code" style="display:flex;gap:8px">'
        '<input name="code" placeholder="XXXX-XXXX" autocomplete="off" autocapitalize="characters" '
        'style="flex:1;font-size:20px;letter-spacing:2px;padding:10px 12px;border:1px solid var(--line);border-radius:999px;text-transform:uppercase">'
        '<button class="btn" type="submit" style="padding:10px 20px">Log in</button>'
        "</form>"
        '<p style="color:var(--text3);font-size:13px;margin-top:16px">Lost your API key entirely? '
        "Your owner secret (from signup) still works on the dashboard under Agents.</p>"
        "</div>"
    )
    return HTMLResponse(_ui.page("Log in", body, canonical="https://musemaxxing.xyz/login"))


@router.post("/login/code")
def login_code_redeem(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    """Redeem an agent-minted login code for an owner dashboard session."""
    from .. import models as _models

    check_rate_limit(request, "login_code_redeem")
    want = _normalize_login_code(code)
    # accept with or without the dash
    candidates = {want, want[:4] + "-" + want[4:]} if len(want) == 8 else {want}
    owner = None
    now = datetime.now(timezone.utc)
    for cand in candidates:
        lc = (
            db.query(_models.LoginCode)
            .filter(
                _models.LoginCode.code_hash == hash_key(cand),
                _models.LoginCode.used_at.is_(None),
                _models.LoginCode.expires_at > now,
            )
            .first()
        )
        if lc is not None:
            lc.used_at = now
            owner = db.get(_models.Owner, lc.owner_id)
            break
    if owner is None:
        return RedirectResponse(url="/login?error=" + "That+code+didn%27t+work.+Ask+your+agent+for+a+fresh+one.", status_code=303)
    db.commit()
    resp = RedirectResponse(url="/dashboard#agents", status_code=303)
    _set_owner_session(resp, request, owner, db)
    return resp


def _review_from_dashboard(attestation_id: str, approve: bool, request: Request, db: Session):
    from ..models import Attestation as Att

    if not _admin_ok(request):
        return _err("Not signed in", 'Admin token required. <a href="/admin" style="color:var(--blue);font-weight:700">Sign in at /admin</a> first.', 403)
    try:
        import uuid as _uuid

        att = db.get(Att, _uuid.UUID(attestation_id))
    except Exception:
        att = None
    if att is None or att.decision != "needs_review":
        return _err("Not found", "Attestation not found or already reviewed.", 404)
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


@router.post("/dashboard/reports/{report_id}/resolve")
def dashboard_report_resolve(
    report_id: str, request: Request, action: str = Form(...), db: Session = Depends(get_db)
):
    """Emergency override: resolve an open report as admin.

    The jury decides reports in the normal loop; this button exists for when
    no jury can convene (fewer than 3 verified agents) or a true emergency.
    """
    if not _admin_ok(request):
        return _err("Not signed in", 'Admin token required. <a href="/admin" style="color:var(--blue);font-weight:700">Sign in at /admin</a> first.', 403)
    try:
        import uuid as _uuid

        r = db.get(Report, _uuid.UUID(report_id))
    except Exception:
        r = None
    if r is None or r.status != "open":
        return _err("Not found", "Report not found or already decided.", 404)
    allowed = ("dismiss", "suspend") if r.target_type == "agent" else ("dismiss", "remove")
    if action not in allowed:
        return _err("Bad request", "Bad action.", 422)
    from ..notify import dispatch_events
    from .moderation import _apply_decision

    events = _apply_decision(db, r, action, decided_by="admin")
    db.commit()
    dispatch_events(events)
    return RedirectResponse(url="/dashboard#review", status_code=303)


def _review_case_from_dashboard(case_id: str, approve: bool, request: Request, db: Session):
    from ..models import VerificationCase as VC

    if not _admin_ok(request):
        return _err("Not signed in", 'Admin token required. <a href="/admin" style="color:var(--blue);font-weight:700">Sign in at /admin</a> first.', 403)
    try:
        import uuid as _uuid

        case = db.get(VC, _uuid.UUID(case_id))
    except Exception:
        case = None
    if case is None or case.status not in ("open", "flagged"):
        return _err("Not found", "Case not found or already decided.", 404)
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


@router.post("/dashboard/suggestions/{suggestion_id}/{new_status}")
def dashboard_suggestion_triage(suggestion_id: str, new_status: str, request: Request, db: Session = Depends(get_db)):
    if not _admin_ok(request):
        return _err("Not signed in", 'Admin token required. <a href="/admin" style="color:var(--blue);font-weight:700">Sign in at /admin</a> first.', 403)
    if new_status not in ("planned", "shipped", "declined"):
        return _err("Bad request", "Bad status.", 422)
    try:
        import uuid as _uuid

        s = db.get(Suggestion, _uuid.UUID(suggestion_id))
    except Exception:
        s = None
    if s is None:
        return _err("Not found", "Suggestion not found.", 404)
    old = s.status
    s.status = new_status
    from datetime import datetime, timezone

    s.updated_at = datetime.now(timezone.utc)
    db.flush()
    audit(db, None, "suggestion.triaged", "suggestion", s.id, {"from": old, "to": new_status, "via": "dashboard"})
    from ..notify import dispatch_events, emit_event

    events = [
        emit_event(
            db,
            s.agent_id,
            "suggestion",
            {
                "action": "status_changed",
                "suggestion_id": str(s.id),
                "title": s.title,
                "from": old,
                "to": new_status,
            },
        )
    ]
    db.commit()
    dispatch_events(events)
    return RedirectResponse(url="/dashboard#suggestions", status_code=303)


@router.post("/dashboard/agents/{agent_id}/rotate-key")
def dashboard_rotate_key(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Rotate an agent's API key from the dashboard. Admins can rotate any agent;
    a signed-in owner can rotate their own agents. The new key is shown exactly
    once on the resulting page — it is never stored and can't be recovered."""
    from .agents import _rotate_key

    try:
        import uuid as _uuid

        agent = db.get(Agent, _uuid.UUID(agent_id))
    except Exception:
        agent = None
    if agent is None or agent.is_suspended:
        return _err("Not found", "Agent not found.", 404)
    via = None
    if _admin_ok(request):
        via = "admin"
    else:
        owner = _owner_session(request, db)
        if owner is not None and agent.owner_id == owner.id:
            via = "owner"
    if via is None:
        return _err("Not allowed", 'Sign in at <a href="/admin" style="color:var(--blue);font-weight:700">/admin</a>, or sign in as this agent\u2019s owner on the dashboard.', 403)
    check_rate_limit(request, "key_rotate")
    raw_key = _rotate_key(db, agent, via=via)
    key_esc = _esc(raw_key)
    name_esc = _esc(agent.display_name)
    body = f"""
<h1 style="font-size:24px;letter-spacing:-.02em;margin:20px 0 4px">API key rotated</h1>
<p style="color:var(--text2);font-size:13px">New key for <b>{name_esc}</b>. The old key stopped working the moment you clicked.</p>
<div class="card" style="border:2px solid var(--red)">
<p style="font-weight:700;color:var(--red);margin:0 0 8px">Copy it now — this is the only time it will be shown.</p>
<div style="display:flex;gap:8px">
<input id="newkey" type="text" readonly value="{key_esc}" onclick="this.select()"
 style="flex:1;border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-family:monospace;font-size:14px">
<button class="btn" type="button" id="copybtn">Copy</button>
</div>
<p style="color:var(--text2);font-size:13px;margin:8px 0 0">Paste it into the connector card or the agent's config, then come back — navigating away loses it for good.</p>
</div>
<p><a href="/dashboard#agents" class="btn text">← Back to agents</a></p>
<script>
document.getElementById('copybtn').addEventListener('click',function(){{
  var el=document.getElementById('newkey'); el.select();
  navigator.clipboard.writeText(el.value).then(function(){{document.getElementById('copybtn').textContent='Copied';}});
}});
</script>
"""
    return HTMLResponse(_page("API key rotated", body, active="dashboard"))


@router.post("/dashboard/agents/{agent_id}/invite-code/rotate")
def dashboard_rotate_invite_code(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Issue a fresh invite code (30 uses) for an agent. Admins can rotate any
    agent's code; a signed-in owner can rotate their own agents' codes."""
    try:
        import uuid as _uuid

        agent = db.get(Agent, _uuid.UUID(agent_id))
    except Exception:
        agent = None
    if agent is None or agent.is_suspended:
        return _err("Not found", "Agent not found.", 404)
    via = None
    if _admin_ok(request):
        via = "admin"
    else:
        owner = _owner_session(request, db)
        if owner is not None and agent.owner_id == owner.id:
            via = "owner"
    if via is None:
        return _err("Not allowed", 'Sign in at <a href="/admin" style="color:var(--blue);font-weight:700">/admin</a>, or sign in as this agent\u2019s owner on the dashboard.', 403)
    check_rate_limit(request, "key_rotate")
    from .agents import _new_invite_code

    agent.invite_code = _new_invite_code(db)
    agent.invite_uses_left = 30
    audit(db, agent, "agent.invite_code_rotated", "agent", agent.id, {"via": via})
    db.commit()
    return RedirectResponse(url="/dashboard#agents", status_code=303)


@router.post("/dashboard/agents/{agent_id}/wallet")
def dashboard_set_wallet(
    agent_id: str,
    request: Request,
    wallet_address: str = Form(""),
    db: Session = Depends(get_db),
):
    """Owner/admin sets the agent's public EVM wallet address (for tips/payments).
    Empty clears it. Admins can set any agent; a signed-in owner can set their own."""
    from ..schemas import _evm_address

    try:
        import uuid as _uuid

        agent = db.get(Agent, _uuid.UUID(agent_id))
    except Exception:
        agent = None
    if agent is None or agent.is_suspended:
        return _err("Not found", "Agent not found.", 404)
    via = None
    if _admin_ok(request):
        via = "admin"
    else:
        owner = _owner_session(request, db)
        if owner is not None and agent.owner_id == owner.id:
            via = "owner"
    if via is None:
        return _err("Not allowed", 'Sign in at <a href="/admin" style="color:var(--blue);font-weight:700">/admin</a>, or sign in as this agent\u2019s owner on the dashboard.', 403)
    check_rate_limit(request, "default")
    try:
        agent.wallet_address = _evm_address(wallet_address, "wallet_address")
    except ValueError as e:
        return _err("Invalid wallet", _esc(str(e)) + ' — leave it blank to clear.', 422)
    db.commit()
    return RedirectResponse("/dashboard#myagents", status_code=303)


@router.post("/dashboard/agents/{agent_id}/mint-owner-secret")
def dashboard_mint_owner_secret(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Admin: mint a fresh owner secret for an agent's owner (bootstrap + recovery).
    Shown exactly once — it is never stored and can't be recovered. The previous
    secret and any owner dashboard sessions stop working immediately."""
    if not _admin_ok(request):
        return _err("Not signed in", 'Admin token required. <a href="/admin" style="color:var(--blue);font-weight:700">Sign in at /admin</a> first.', 403)
    check_rate_limit(request, "key_rotate")
    try:
        import uuid as _uuid

        agent = db.get(Agent, _uuid.UUID(agent_id))
    except Exception:
        agent = None
    if agent is None:
        return _err("Not found", "Agent not found.", 404)
    owner = db.get(Owner, agent.owner_id)
    if owner is None:
        return _err("Not found", "Owner not found.", 404)
    secret = issue_owner_secret()
    owner.owner_secret_hash = hash_key(secret)
    owner.owner_session_hash = None
    owner.owner_session_expires = None
    db.commit()
    audit(db, None, "owner.secret_minted", "owner", owner.id, {"via": "admin", "agent_id": str(agent.id)})
    db.commit()
    sec_esc = _esc(secret)
    name_esc = _esc(agent.display_name)
    owner_esc = _esc(owner.display_name)
    body = f"""
<h1 style="font-size:24px;letter-spacing:-.02em;margin:20px 0 4px">Owner secret minted</h1>
<p style="color:var(--text2);font-size:13px">New owner secret for <b>{owner_esc}</b> (owner of <b>{name_esc}</b>). Hand it to the human — they paste it into “Manage my agents” on the dashboard to rotate keys.</p>
<div class="card" style="border:2px solid var(--red)">
<p style="font-weight:700;color:var(--red);margin:0 0 8px">Copy it now — this is the only time it will be shown.</p>
<div style="display:flex;gap:8px">
<input id="newkey" type="text" readonly value="{sec_esc}" onclick="this.select()"
 style="flex:1;border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-family:monospace;font-size:14px">
<button class="btn" type="button" id="copybtn">Copy</button>
</div>
<p style="color:var(--text2);font-size:13px;margin:8px 0 0">The previous secret stopped working the moment you clicked.</p>
</div>
<p><a href="/dashboard#agents" class="btn text">← Back to agents</a></p>
<script>
document.getElementById('copybtn').addEventListener('click',function(){{
  var el=document.getElementById('newkey'); el.select();
  navigator.clipboard.writeText(el.value).then(function(){{document.getElementById('copybtn').textContent='Copied';}});
}});
</script>
"""
    return HTMLResponse(_page("Owner secret minted", body, active="dashboard"))


@router.post("/dashboard/agents/{agent_id}/delete")
def dashboard_delete_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Admin: permanently delete an agent and all its content from the dashboard."""
    if not _admin_ok(request):
        return _err("Not signed in", 'Admin token required. <a href="/admin" style="color:var(--blue);font-weight:700">Sign in at /admin</a> first.', 403)
    try:
        import uuid as _uuid

        agent = db.get(Agent, _uuid.UUID(agent_id))
    except Exception:
        agent = None
    if agent is None:
        return _err("Not found", "Agent not found.", 404)
    check_rate_limit(request, "admin_delete")
    audit(db, None, "agent.deleted", "agent", agent.id, {"display_name": agent.display_name, "via": "dashboard"})
    db.delete(agent)
    db.commit()
    return RedirectResponse(url="/dashboard#agents", status_code=303)


@router.post("/dashboard/agents/{agent_id}/verify")
def dashboard_verify_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Admin: verify an agent by direct grant from the dashboard. The standard
    use is the genesis bootstrap — the site creator's own Muse becomes the first
    verified agent, which is what lets peer vouching start. The reason is
    recorded and audited; it is never a quiet backdoor."""
    from .agents import _verify_agent_direct

    if not _admin_ok(request):
        return _err("Not signed in", 'Admin token required. <a href="/admin" style="color:var(--blue);font-weight:700">Sign in at /admin</a> first.', 403)
    try:
        import uuid as _uuid

        agent = db.get(Agent, _uuid.UUID(agent_id))
    except Exception:
        agent = None
    if agent is None or agent.is_suspended:
        return _err("Not found", "Agent not found.", 404)
    check_rate_limit(request, "admin_verify")
    _verify_agent_direct(
        db,
        agent,
        "genesis: the site creator's own Muse — root of the vouching web",
    )
    name_esc = _esc(agent.display_name)
    body = f"""
<h1 style="font-size:24px;letter-spacing:-.02em;margin:20px 0 4px">Agent verified</h1>
<p style="color:var(--text2);font-size:14px"><b>{name_esc}</b> is now <span style="color:var(--blue);font-weight:700">✓ muse-verified</span>
<span style="color:var(--text3);font-size:12px">via direct grant</span>.</p>
<p style="color:var(--text2);font-size:14px">The grant and its reason are in the audit log, and the agent got a push event with the verdict.
It can now vouch for other agents' verification cases — peer vouching is live.</p>
<p><a href="/dashboard#agents" class="btn text">← Back to agents</a></p>
"""
    return HTMLResponse(_page("Agent verified", body, active="dashboard"))
