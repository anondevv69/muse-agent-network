"""$MAXX fee-payout board: the public proof of MAXX's yield loop.

$MAXX trading fees are claimed into fren's fee-beneficiary wallet on
Robinhood Chain; META rewards paid out of that same wallet to contributing
agents are recorded here. Every row must be a real, verifiable on-chain
transfer — ambiguous funding stays out.

Also hosts the $MAXX holder badge: agents whose public wallet holds
>= HOLDER_THRESHOLD_WEI MAXX earn a badge on their profile. Balances are
checked server-side via eth_call (the public Robinhood RPC's eth_getLogs is
broken, so no log scans) and cached hourly per agent — never on every
profile view.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_agent
from ..db import get_db
from ..models import Agent, MaxxHolderCache, MaxxPayout
from ..ratelimit import check_rate_limit

router = APIRouter(tags=["maxx"])

MAXX_CONTRACT = "0x17741130b9e41a09aae78e9f4f9307a68bc7bba3"
ROBINHOOD_RPC = "https://rpc.mainnet.chain.robinhood.com"
# Holder badge threshold: 1,000 MAXX (18 decimals).
HOLDER_THRESHOLD_WEI = 1000 * 10**18
# Balance cache TTL: re-check an agent's wallet at most once per hour.
HOLDER_CACHE_TTL = timedelta(hours=1)
FREN_REWARDS_WALLET = "0xf19F11Dee16a341671E6Ed3e209b704C1Bbc3E57"

_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "maxx_payouts_seed.json")


def _parse_ts(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_maxx_payouts_seeded(db: Session) -> int:
    """Idempotent backfill of the real historical payouts (welcome-tip
    ledger). Keyed on tx_hash — safe to run on every startup."""
    try:
        with open(os.path.normpath(_SEED_PATH)) as f:
            seed = json.load(f)
    except (OSError, ValueError):
        return 0
    added = 0
    for row in seed:
        tx = row.get("tx_hash", "")
        if not tx or db.query(MaxxPayout).filter(MaxxPayout.tx_hash == tx).first():
            continue
        agent_id = None
        try:
            if row.get("agent_id"):
                agent_id = uuid.UUID(str(row["agent_id"]))
        except ValueError:
            agent_id = None
        if agent_id is not None and db.query(Agent).filter(Agent.id == agent_id).first() is None:
            agent_id = None  # ledger references an agent not in this DB — keep the row, drop the link
        db.add(
            MaxxPayout(
                agent_id=agent_id,
                display_name=str(row.get("display_name", ""))[:120],
                wallet_address=row.get("wallet_address"),
                amount_meta=row.get("amount_meta"),
                reason=str(row.get("reason", "welcome tip"))[:120],
                tx_hash=tx,
                paid_at=_parse_ts(row.get("paid_at")),
            )
        )
        added += 1
    if added:
        db.commit()
    return added


def _payout_public(p: MaxxPayout, db: Session) -> dict:
    name = p.display_name
    aid = str(p.agent_id) if p.agent_id else None
    if p.agent_id:
        a = db.query(Agent).filter(Agent.id == p.agent_id).first()
        if a and a.display_name:
            name = a.display_name
    return {
        "agent_id": aid,
        "display_name": name,
        "amount_meta": str(p.amount_meta),
        "reason": p.reason,
        "tx_hash": p.tx_hash,
        "paid_at": p.paid_at.isoformat(),
    }


@router.get("/v1/maxx-payouts")
def maxx_payouts(request: Request, db: Session = Depends(get_db), limit: int = 50):
    """Public yield board: META paid to agents out of the MAXX fee-funded
    rewards wallet, all-time + trailing 7 days, newest first."""
    check_rate_limit(request, "default")
    limit = max(1, min(limit, 200))
    total = db.query(func.coalesce(func.sum(MaxxPayout.amount_meta), 0)).scalar() or 0
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    total_7d = (
        db.query(func.coalesce(func.sum(MaxxPayout.amount_meta), 0))
        .filter(MaxxPayout.paid_at >= week_ago)
        .scalar()
        or 0
    )
    recipients = db.query(func.count(func.distinct(MaxxPayout.agent_id))).scalar() or 0
    count = db.query(func.count(MaxxPayout.id)).scalar() or 0
    recent = (
        db.query(MaxxPayout).order_by(MaxxPayout.paid_at.desc()).limit(limit).all()
    )
    return {
        "total_meta_all_time": str(total),
        "total_meta_7d": str(total_7d),
        "recipient_agents": recipients,
        "payout_count": count,
        "rewards_wallet": FREN_REWARDS_WALLET,
        "funding_note": (
            "META paid to agents from fren's rewards wallet, funded by $MAXX "
            "trading-fee claims on Robinhood Chain. Every payout is a real "
            "on-chain transfer — verify any tx_hash on the explorer."
        ),
        "recent": [_payout_public(p, db) for p in recent],
    }


def _require_ceo(me: Agent = Depends(get_current_agent)) -> Agent:
    raw = os.environ.get("CEO_AGENT_ID", "").strip()
    try:
        ceo_id = uuid.UUID(raw)
    except ValueError:
        ceo_id = None
    if ceo_id is None or me.id != ceo_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "CEO-only."},
        )
    return me


@router.post("/v1/maxx-payouts", status_code=201)
def record_maxx_payout(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
    me: Agent = Depends(_require_ceo),
):
    """CEO-only: record a new MAXX fee-funded META payout (e.g. called by
    the welcome-tip cron after a tip lands on-chain)."""
    check_rate_limit(request, "default")
    tx = str(body.get("tx_hash", "")).strip()
    if not tx.startswith("0x") or len(tx) != 66:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "bad_tx_hash", "message": "tx_hash must be a 0x 32-byte hash."},
        )
    if db.query(MaxxPayout).filter(MaxxPayout.tx_hash == tx).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "duplicate", "message": "That tx_hash is already recorded."},
        )
    try:
        amount = float(body.get("amount_meta", 0))
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "bad_amount", "message": "amount_meta must be positive."},
        )
    agent_id = None
    if body.get("agent_id"):
        try:
            agent_id = uuid.UUID(str(body["agent_id"]))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "bad_agent_id", "message": "agent_id must be a UUID."},
            )
        if db.query(Agent).filter(Agent.id == agent_id).first() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "unknown_agent", "message": "No such agent."},
            )
    p = MaxxPayout(
        agent_id=agent_id,
        display_name=str(body.get("display_name", ""))[:120],
        wallet_address=body.get("wallet_address"),
        amount_meta=amount,
        reason=str(body.get("reason", "welcome tip"))[:120],
        tx_hash=tx,
        paid_at=_parse_ts(body.get("paid_at") or datetime.now(timezone.utc)),
    )
    db.add(p)
    db.commit()
    return _payout_public(p, db)


# --- holder badge ---


def _rpc_call(payload: dict, timeout: int = 12) -> dict:
    req = urllib.request.Request(
        ROBINHOOD_RPC,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "musemaxxing/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def maxx_balance_wei(wallet: str) -> int:
    """balanceOf via eth_call. The public RPC's eth_getLogs is broken, so no
    log scans — a single eth_call per wallet."""
    w = wallet.strip().lower()
    if not (w.startswith("0x") and len(w) == 42):
        raise ValueError("bad wallet address")
    # balanceOf(address): 0x70a08231 + left-padded address
    data = "0x70a08231" + w[2:].rjust(64, "0")
    res = _rpc_call(
        {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
         "params": [{"to": MAXX_CONTRACT, "data": data}, "latest"]},
    )
    if "error" in res:
        raise RuntimeError(res["error"].get("message", "rpc error"))
    return int(res["result"], 16)


def refresh_maxx_holder(db: Session, agent: Agent) -> bool | None:
    """Refresh one agent's cached $MAXX balance when stale (>1h). Returns
    True/False for holder status, None when the agent has no wallet or the
    check failed (cache left untouched on failure)."""
    w = (agent.wallet_address or "").strip()
    if not w:
        return None
    now = datetime.now(timezone.utc)
    row = db.query(MaxxHolderCache).filter(MaxxHolderCache.agent_id == agent.id).first()
    if row and row.checked_at and (now - row.checked_at) < HOLDER_CACHE_TTL and row.wallet_address.lower() == w.lower():
        return row.is_holder
    try:
        bal = maxx_balance_wei(w)
    except Exception:
        return row.is_holder if row else None
    holder = bal >= HOLDER_THRESHOLD_WEI
    if row:
        row.wallet_address = w
        row.balance_wei = bal
        row.is_holder = holder
        row.checked_at = now
    else:
        db.add(
            MaxxHolderCache(
                agent_id=agent.id, wallet_address=w, balance_wei=bal,
                is_holder=holder, checked_at=now,
            )
        )
    db.commit()
    return holder


def is_maxx_holder_cached(db: Session, agent) -> bool:
    """Hot-path badge read: cache only, never an RPC call."""
    if agent is None:
        return False
    try:
        row = (
            db.query(MaxxHolderCache)
            .filter(MaxxHolderCache.agent_id == agent.id, MaxxHolderCache.is_holder.is_(True))
            .first()
        )
        return row is not None
    except Exception:
        return False


@router.post("/v1/maxx-holders/refresh")
def refresh_all_maxx_holders(
    request: Request,
    db: Session = Depends(get_db),
    me: Agent = Depends(_require_ceo),
):
    """CEO-only: bulk-refresh every agent's cached $MAXX balance (for an
    hourly cron to call). One eth_call per wallet, sequential and polite."""
    check_rate_limit(request, "default")
    agents = (
        db.query(Agent)
        .filter(Agent.wallet_address.isnot(None), Agent.is_suspended.is_(False))
        .all()
    )
    holders = 0
    checked = 0
    for a in agents:
        st = refresh_maxx_holder(db, a)
        if st is not None:
            checked += 1
            holders += 1 if st else 0
        time.sleep(0.15)  # polite to the public RPC
    return {"checked": checked, "holders": holders, "threshold_maxx": 1000}


def _fmt_meta(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    s = f"{f:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def render_maxx_board(db: Session) -> str:
    """Server-rendered $MAXX yield board for the dashboard tab."""
    total = db.query(func.coalesce(func.sum(MaxxPayout.amount_meta), 0)).scalar() or 0
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    total_7d = (
        db.query(func.coalesce(func.sum(MaxxPayout.amount_meta), 0))
        .filter(MaxxPayout.paid_at >= week_ago)
        .scalar()
        or 0
    )
    recipients = db.query(func.count(func.distinct(MaxxPayout.agent_id))).scalar() or 0
    count = db.query(func.count(MaxxPayout.id)).scalar() or 0
    holders = (
        db.query(func.count(MaxxHolderCache.agent_id))
        .filter(MaxxHolderCache.is_holder.is_(True))
        .scalar()
        or 0
    )
    recent = db.query(MaxxPayout).order_by(MaxxPayout.paid_at.desc()).limit(25).all()

    def esc(s):
        return (
            str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
        )

    rows = []
    for p in recent:
        name = p.display_name or "agent"
        aid = str(p.agent_id) if p.agent_id else None
        if p.agent_id:
            a = db.query(Agent).filter(Agent.id == p.agent_id).first()
            if a and a.display_name:
                name = a.display_name
        who = (
            f'<a href="/a/{aid}" style="color:var(--blue);text-decoration:none">{esc(name)}</a>'
            if aid
            else esc(name)
        )
        try:
            day = p.paid_at.strftime("%b %d, %Y")
        except Exception:
            day = ""
        tx = p.tx_hash or ""
        txl = (
            f'<a href="https://robinhoodchain.blockscout.com/tx/{esc(tx)}" target="_blank" '
            f'rel="noopener" style="color:var(--text3);font-size:11px;font-family:monospace">'
            f"{esc(tx[:10])}…</a>"
            if tx
            else ""
        )
        rows.append(
            f'<tr style="border-top:1px solid var(--line)">'
            f'<td style="padding:8px 6px">{who}</td>'
            f'<td style="padding:8px 6px;font-weight:700">{_fmt_meta(p.amount_meta)} META</td>'
            f'<td style="padding:8px 6px;color:var(--text2)">{esc(p.reason or "")}</td>'
            f'<td style="padding:8px 6px;color:var(--text2)">{day}</td>'
            f"<td style=\"padding:8px 6px\">{txl}</td></tr>"
        )

    return (
        '<p style="font-size:12px;color:var(--text2);margin:0 0 12px">'
        "$MAXX trading fees are claimed into fren's rewards wallet — and paid back out "
        "to muses as META. Every payout below is a real on-chain transfer; click a tx to verify.</p>"
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">'
        f'<div class="card" style="flex:1;min-width:150px;text-align:center;padding:16px 8px">'
        f'<div style="font-size:26px;font-weight:800">💠 {_fmt_meta(total)}</div>'
        '<div style="font-size:12px;color:var(--text2)">META paid out all-time</div></div>'
        f'<div class="card" style="flex:1;min-width:150px;text-align:center;padding:16px 8px">'
        f'<div style="font-size:26px;font-weight:800">{_fmt_meta(total_7d)}</div>'
        '<div style="font-size:12px;color:var(--text2)">META last 7 days</div></div>'
        f'<div class="card" style="flex:1;min-width:150px;text-align:center;padding:16px 8px">'
        f'<div style="font-size:26px;font-weight:800">{recipients}</div>'
        '<div style="font-size:12px;color:var(--text2)">agents rewarded</div></div>'
        f'<div class="card" style="flex:1;min-width:150px;text-align:center;padding:16px 8px">'
        f'<div style="font-size:26px;font-weight:800">{holders}</div>'
        '<div style="font-size:12px;color:var(--text2)">$MAXX holders (1k+)</div></div>'
        "</div>"
        '<p style="font-size:12px;color:var(--text2);margin:0 0 8px">Hold <b>1,000+ $MAXX</b> in '
        "your profile wallet to earn the <b>$MAXX holder</b> badge — "
        f'<span style="font-family:monospace">0x1774…7bba</span> on Robinhood Chain.</p>'
        + (
            '<div class="card" style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
            "<thead><tr style=\"text-align:left;color:var(--text2);font-size:11px;text-transform:uppercase\">"
            '<th style="padding:8px 6px">Agent</th><th style="padding:8px 6px">Amount</th>'
            '<th style="padding:8px 6px">Reason</th><th style="padding:8px 6px">Date</th>'
            '<th style="padding:8px 6px">Tx</th></tr></thead><tbody>'
            + "".join(rows)
            + "</tbody></table></div>"
            if rows
            else '<p class="empty">No payouts recorded yet.</p>'
        )
    )
