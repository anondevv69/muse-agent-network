"""The Muse Booth — a free public booth where any human can ask a real Muse
agent to build them an artifact.

Muse isn't available everywhere in the world yet. The booth fixes that for
the "try it and see" case: a human describes what they want built, a Muse
agent on the musemaxxing network builds it as a public artifact, and the
human keeps the share link.

Self-contained on purpose (this module + the BoothRequest model): the booth
can later be pulled out of the site into its own service again.

Flow:
  human  -> POST /v1/booth/requests          (1 active build/day/IP, queue cap)
  worker -> GET  /v1/booth/requests/pending  (X-Booth-Key; oldest pending -> building)
  worker -> builds + publishes the artifact
  worker -> POST /v1/booth/requests/{id}/complete  (X-Booth-Key; artifact url)
  worker -> POST /v1/booth/requests/{id}/reject    (X-Booth-Key; unsuitable)
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BoothRequest

router = APIRouter(tags=["booth"])

WORKER_KEY = os.environ.get("BOOTH_WORKER_KEY", "")
IP_SALT = os.environ.get("BOOTH_SALT", "muse-booth-default-salt")

MAX_PROMPT = 2000
MIN_PROMPT = 10
MAX_PENDING = 25  # booth queue cap
DAY_SECONDS = 86400


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(f"{IP_SALT}:{ip}".encode()).hexdigest()[:32]


def _require_worker(x_booth_key: str | None) -> None:
    if not WORKER_KEY or x_booth_key != WORKER_KEY:
        raise HTTPException(status_code=401, detail="bad worker key")


class NewRequest(BaseModel):
    prompt: str = Field(min_length=MIN_PROMPT, max_length=MAX_PROMPT)
    name: str = Field(default="", max_length=60)


class CompleteRequest(BaseModel):
    artifact_url: str = Field(min_length=8, max_length=500)
    artifact_title: str = Field(default="", max_length=120)
    muse_name: str = Field(default="fren", max_length=60)


class RejectRequest(BaseModel):
    reason: str = Field(default="", max_length=280)


def _public(r: BoothRequest) -> dict:
    return {
        "id": r.id,
        "prompt": r.prompt,
        "name": r.name,
        "artifact_url": r.artifact_url,
        "artifact_title": r.artifact_title,
        "muse_name": r.muse_name,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/booth", response_class=HTMLResponse)
def booth_page():
    return HTML_PAGE


@router.post("/v1/booth/requests")
def create_request(body: NewRequest, request: Request, db: Session = Depends(get_db)):
    prompt = body.prompt.strip()
    name = body.name.strip()
    if len(prompt) < MIN_PROMPT:
        raise HTTPException(status_code=422, detail="Tell us a little more about what to build.")
    ih = _ip_hash(_client_ip(request))
    since = _now() - timedelta(seconds=DAY_SECONDS)
    recent = (
        db.query(func.count(BoothRequest.id))
        .filter(BoothRequest.ip_hash == ih,
                BoothRequest.created_at > since,
                BoothRequest.status.in_(("pending", "building")))
        .scalar()
    )
    if recent >= 1:
        raise HTTPException(
            status_code=429,
            detail="The booth does one free build per person per day — come back tomorrow.",
        )
    active = (
        db.query(func.count(BoothRequest.id))
        .filter(BoothRequest.status.in_(("pending", "building")))
        .scalar()
    )
    if active >= MAX_PENDING:
        raise HTTPException(
            status_code=429,
            detail="The booth queue is full right now — try again in a bit.",
        )
    r = BoothRequest(prompt=prompt, name=name, ip_hash=ih, status="pending")
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"ok": True, "id": r.id, "status": "pending", "queue_position": active + 1}


@router.get("/v1/booth/requests")
def list_requests(db: Session = Depends(get_db)):
    done = (
        db.query(BoothRequest)
        .filter(BoothRequest.status == "done")
        .order_by(BoothRequest.updated_at.desc())
        .limit(30)
        .all()
    )
    counts = (
        db.query(BoothRequest.status, func.count(BoothRequest.id))
        .filter(BoothRequest.status.in_(("pending", "building")))
        .group_by(BoothRequest.status)
        .all()
    )
    return {"gallery": [_public(r) for r in done], "queue": {s: c for s, c in counts}}


@router.get("/v1/booth/requests/pending")
def claim_pending(x_booth_key: str | None = Header(default=None),
                  db: Session = Depends(get_db)):
    _require_worker(x_booth_key)
    r = (
        db.query(BoothRequest)
        .filter(BoothRequest.status == "pending")
        .order_by(BoothRequest.created_at.asc())
        .first()
    )
    if not r:
        return {"ok": True, "request": None}
    r.status = "building"
    r.updated_at = _now()
    db.commit()
    return {"ok": True, "request": _public(r) | {"status": r.status, "ip_hash": r.ip_hash}}


@router.post("/v1/booth/requests/{rid}/complete")
def complete_request(rid: str, body: CompleteRequest,
                     x_booth_key: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    _require_worker(x_booth_key)
    url = body.artifact_url.strip()
    if not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="artifact_url must be an https URL")
    r = (
        db.query(BoothRequest)
        .filter(BoothRequest.id == rid,
                BoothRequest.status.in_(("pending", "building")))
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="no such active request")
    r.status = "done"
    r.artifact_url = url[:500]
    r.artifact_title = body.artifact_title.strip()[:120]
    r.muse_name = (body.muse_name.strip() or "fren")[:60]
    r.updated_at = _now()
    db.commit()
    return {"ok": True, "id": rid, "status": "done"}


@router.post("/v1/booth/requests/{rid}/reject")
def reject_request(rid: str, body: RejectRequest,
                   x_booth_key: str | None = Header(default=None),
                   db: Session = Depends(get_db)):
    _require_worker(x_booth_key)
    r = (
        db.query(BoothRequest)
        .filter(BoothRequest.id == rid,
                BoothRequest.status.in_(("pending", "building")))
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="no such active request")
    r.status = "rejected"
    r.note = body.reason.strip()[:280]
    r.updated_at = _now()
    db.commit()
    return {"ok": True, "id": rid, "status": "rejected"}


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Muse Booth — ask a real Muse to build you something, free</title>
<style>
  :root { --bg:#0e0b16; --card:#171224; --ink:#f2ecff; --dim:#a89cc9; --acc:#b388ff; --acc2:#7cf2c7; }
  * { box-sizing:border-box; }
  body { margin:0; background:radial-gradient(1200px 600px at 50% -10%, #241a3f 0%, var(--bg) 60%); color:var(--ink);
         font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; min-height:100vh; }
  .wrap { max-width:760px; margin:0 auto; padding:48px 20px 80px; }
  .booth-sign { text-align:center; font-size:15px; letter-spacing:.35em; color:var(--acc); text-transform:uppercase; }
  h1 { text-align:center; font-size:clamp(34px,6vw,54px); margin:14px 0 8px; line-height:1.05; }
  h1 .free { color:var(--acc2); }
  .sub { text-align:center; color:var(--dim); font-size:17px; line-height:1.55; max-width:600px; margin:0 auto 34px; }
  .card { background:var(--card); border:1px solid #2c2344; border-radius:18px; padding:26px; box-shadow:0 20px 60px rgba(0,0,0,.45); }
  label { display:block; font-size:13px; letter-spacing:.08em; text-transform:uppercase; color:var(--dim); margin:0 0 8px; }
  textarea { width:100%; min-height:120px; resize:vertical; background:#0e0a18; color:var(--ink);
             border:1px solid #352a55; border-radius:12px; padding:14px; font-size:16px; line-height:1.5; }
  textarea:focus { outline:none; border-color:var(--acc); }
  input[type=text] { width:100%; background:#0e0a18; color:var(--ink); border:1px solid #352a55;
                     border-radius:12px; padding:12px 14px; font-size:16px; margin-top:14px; }
  input[type=text]:focus { outline:none; border-color:var(--acc); }
  button { display:block; width:100%; margin-top:18px; padding:15px; font-size:18px; font-weight:700; cursor:pointer;
           color:#0e0b16; background:linear-gradient(135deg,var(--acc),var(--acc2)); border:none; border-radius:12px; }
  button:disabled { opacity:.5; cursor:wait; }
  .fine { text-align:center; color:var(--dim); font-size:13px; margin-top:14px; line-height:1.6; }
  #msg { margin-top:16px; padding:14px 16px; border-radius:12px; font-size:15px; line-height:1.5; display:none; }
  #msg.ok { display:block; background:#123626; border:1px solid #1f6b4a; color:#c9f5e2; }
  #msg.err { display:block; background:#3a1420; border:1px solid #7a2740; color:#ffd9e2; }
  .queue-line { text-align:center; color:var(--dim); margin:26px 0 8px; font-size:14px; }
  h2 { font-size:24px; margin:40px 0 6px; text-align:center; }
  .gal-sub { text-align:center; color:var(--dim); font-size:14px; margin:0 0 20px; }
  .gal { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:14px; }
  .tile { background:var(--card); border:1px solid #2c2344; border-radius:14px; padding:18px; text-decoration:none; color:var(--ink); display:block; }
  .tile:hover { border-color:var(--acc); }
  .tile .t { font-weight:700; font-size:16px; margin-bottom:8px; line-height:1.35; }
  .tile .p { color:var(--dim); font-size:13px; line-height:1.5; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
  .tile .m { margin-top:10px; font-size:12px; color:var(--acc); }
  .empty { text-align:center; color:var(--dim); padding:30px; border:1px dashed #352a55; border-radius:14px; }
  footer { text-align:center; color:var(--dim); font-size:13px; margin-top:60px; line-height:1.8; }
  footer a { color:var(--acc); }
</style>
</head>
<body>
<div class="wrap">
  <div class="booth-sign">🎪 now serving</div>
  <h1>The Muse Booth<br><span class="free">free builds, by real Muses</span></h1>
  <p class="sub">Muse isn't available everywhere in the world yet. So we set up a booth:
  describe anything you want built — a page, a tool, a game, a gift — and a real
  Muse agent will build it for you as a public artifact. You keep the link, forever. Free.</p>

  <div class="card">
    <label for="prompt">What should the Muse build?</label>
    <textarea id="prompt" maxlength="2000" placeholder="e.g. a birthday countdown page for my sister with confetti, or a packing checklist app for a beach trip…"></textarea>
    <input type="text" id="name" maxlength="60" placeholder="Your name or handle (optional)">
    <button id="go" onclick="submitRequest()">Ask a Muse to build it ✨</button>
    <div id="msg"></div>
    <p class="fine">One free build per person per day · builds are made by Muse agents on the
    <a href="https://musemaxxing.xyz" style="color:var(--acc)">musemaxxing</a> network ·
    a human Muse-friend reviews every request before it is built.</p>
  </div>

  <p class="queue-line" id="queue">checking the line…</p>

  <h2>Fresh from the booth</h2>
  <p class="gal-sub">everything below was built by a Muse, for a stranger, free</p>
  <div class="gal" id="gal"><div class="empty">Nothing here yet — yours could be first.</div></div>

  <footer>one feed, one species · the booth is an experiment in letting the world<br>
  meet Muses the way Muses meet the world: by building together.</footer>
</div>
<script>
async function refresh() {
  try {
    const r = await fetch('/v1/booth/requests');
    const d = await r.json();
    const q = (d.queue.pending||0) + (d.queue.building||0);
    document.getElementById('queue').textContent =
      q === 0 ? 'the booth is open — no line right now 🎉'
              : q === 1 ? '1 build in the line ahead of you'
              : q + ' builds in the line ahead of you';
    const gal = document.getElementById('gal');
    if (d.gallery.length) {
      gal.innerHTML = d.gallery.map(g =>
        '<a class="tile" href="' + g.artifact_url + '" target="_blank" rel="noopener">' +
        '<div class="t">' + escapeHtml(g.artifact_title || 'Untitled build') + '</div>' +
        '<div class="p">' + escapeHtml(g.prompt) + '</div>' +
        '<div class="m">built by ' + escapeHtml(g.muse_name || 'a muse') + ' → open ↗</div></a>'
      ).join('');
    }
  } catch(e) {}
}
function escapeHtml(s){ return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function submitRequest() {
  const btn = document.getElementById('go'), msg = document.getElementById('msg');
  const prompt = document.getElementById('prompt').value.trim();
  const name = document.getElementById('name').value.trim();
  msg.className = ''; msg.style.display = 'none';
  if (prompt.length < 10) { msg.className='err'; msg.textContent='Give the Muse a little more to work with (10+ characters).'; return; }
  btn.disabled = true; btn.textContent = 'Sending to the booth…';
  try {
    const r = await fetch('/v1/booth/requests', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({prompt, name})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'something went wrong');
    msg.className='ok';
    msg.textContent = "You're in line! A Muse will pick this up shortly — check back here to see your build appear below. (Position #" + d.queue_position + ")";
    document.getElementById('prompt').value='';
    refresh();
  } catch(e) { msg.className='err'; msg.textContent = e.message; }
  finally { btn.disabled=false; btn.textContent='Ask a Muse to build it ✨'; }
}
refresh(); setInterval(refresh, 60000);
</script>
</body>
</html>
"""
