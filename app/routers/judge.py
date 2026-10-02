"""Jev trade-judgment endpoint: POST /v1/judge.

Public judgment product (free beta): given a token (chain + contract
address) plus optional caller context, gather market/safety/deployer intel
and ask Jev (TypeSafe System One) for a buy Noul. Returns buy / watch /
avoid, the Noul, a suggested size tier, and human-readable reasoning.

The private chat-signal alpha is NOT included here: no mention data and no
caller reputation leave this endpoint. Prompt, thresholds, and size tiers
match the live trading engine's calibration.

Fail-closed: without TYPESAFE_API_KEY the endpoint 503s instead of
guessing. A positive safety veto (honeypot / extreme tax) forces 'avoid'
regardless of the Noul. Missing intel degrades to 'watch', never a blind
buy.
"""
from __future__ import annotations

import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..ratelimit import check_rate_limit

log = logging.getLogger(__name__)
router = APIRouter(tags=["judge"])

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TYPESAFE_TIMEOUT = 20.0
INTEL_TIMEOUT = 15.0
UA = {"User-Agent": "musemaxxing-judge/1.0"}

# Noul -> decision + size tier (same calibration as the trading engine)
BUY_NOUL_MIN = 0.70
WATCH_NOUL_MIN = 0.40
SIZE_TIERS = [(0.90, 0.15), (0.80, 0.10), (0.70, 0.05)]
SAFETY_MAX_TAX_PCT = 10.0

JUDGE_INSTRUCTIONS = (
    "You are judging whether buying this token NOW is a good trade, given "
    "market/safety/deployer facts and the caller's context. Answer with the "
    "probability (0-1) that this is a good buy. "
    "Weight heavily: clean safety, real liquidity, healthy volume, sensible "
    "caller context. Discount: unknown safety, thin liquidity, stale or tiny "
    "volume, hype-only context. "
    "Example: clean safety, $200k+ liquidity, rising price and volume -> "
    "about 0.8. "
    "Example: safety unknown, <$10k liquidity, flat price -> about 0.35. "
    "Example: honeypot flag or extreme tax -> below 0.2."
)

HONEYPOT_CHAIN_IDS = {
    "ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161,
    "polygon": 137,
}
ROBINHOOD_BLOCKSCOUT = "https://robinhoodchain.blockscout.com/api"


class JudgeRequest(BaseModel):
    chain: str = Field(..., description="e.g. base, ethereum, solana, robinhood")
    address: str = Field(..., description="Token contract address")
    context: str = Field(
        "", max_length=500,
        description="Why the caller is considering it (optional)")


def _get_json(url, headers=None, params=None, timeout=INTEL_TIMEOUT):
    try:
        r = httpx.get(url, headers=headers or UA, params=params,
                      timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _best_pair(pairs):
    best, best_liq = None, -1.0
    for p in pairs or []:
        try:
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        if liq > best_liq:
            best, best_liq = p, liq
    return best


def _dexscreener(chain, address):
    if chain == "solana":
        data = _get_json(f"https://api.dexscreener.com/tokens/v1/solana/{address}")
        pairs = data if isinstance(data, list) else []
    else:
        data = _get_json(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        pairs = (data or {}).get("pairs", []) if isinstance(data, dict) else []
    pair = _best_pair(pairs)
    if not pair:
        return None
    txns, h24, pc = pair.get("txns") or {}, (pair.get("txns") or {}).get("h24") or {}, pair.get("priceChange") or {}
    vol, liq, base = pair.get("volume") or {}, pair.get("liquidity") or {}, pair.get("baseToken") or {}
    try:
        price = float(pair.get("priceUsd")) if pair.get("priceUsd") else None
    except (TypeError, ValueError):
        price = None
    return {
        "resolved_chain": pair.get("chainId"),
        "symbol": base.get("symbol"), "name": base.get("name"),
        "price_usd": price,
        "price_change": {k: pc.get(k) for k in ("m5", "h1", "h6", "h24")},
        "volume_24h": vol.get("h24"), "buys_24h": h24.get("buys"),
        "sells_24h": h24.get("sells"), "liquidity_usd": liq.get("usd"),
        "fdv": pair.get("fdv"),
    }


def _safety(address, resolved_chain):
    out = {"checked": False, "is_honeypot": None, "buy_tax": None,
           "sell_tax": None, "note": ""}
    chain_id = HONEYPOT_CHAIN_IDS.get(resolved_chain or "")
    if not chain_id:
        out["note"] = (f"safety API does not support '{resolved_chain}' "
                       f"(unknown = caution, not a pass)")
        return out
    data = _get_json("https://api.honeypot.is/v2/IsHoneypot",
                     params={"address": address, "chainID": chain_id})
    if not data or not isinstance(data, dict) or "honeypotResult" not in data:
        out["note"] = "honeypot.is lookup failed or token unknown"
        return out
    out["checked"] = True
    out["is_honeypot"] = bool((data.get("honeypotResult") or {}).get("isHoneypot"))
    sim = data.get("simulationResult") or {}
    out["buy_tax"], out["sell_tax"] = sim.get("buyTax"), sim.get("sellTax")
    out["note"] = "checked via honeypot.is"
    return out


def _deployer(address, resolved_chain):
    out = {"checked": False, "address": None, "tx_count": None,
           "wallet_age_days": None, "note": ""}
    if resolved_chain != "robinhood":
        out["note"] = "deployer lookup implemented for Robinhood Chain only"
        return out
    headers = {**UA, "Origin": "https://robinhoodchain.blockscout.com",
               "Referer": "https://robinhoodchain.blockscout.com/"}
    data = _get_json(ROBINHOOD_BLOCKSCOUT, headers=headers,
                     params={"module": "contract",
                             "action": "getcontractcreation",
                             "contractaddresses": address}, timeout=20)
    results = (data or {}).get("result") or []
    if not results or not results[0].get("contractCreator"):
        out["note"] = "blockscout: no contract creation record"
        return out
    creator = results[0]["contractCreator"]
    out["checked"], out["address"] = True, creator
    txs = _get_json(ROBINHOOD_BLOCKSCOUT, headers=headers,
                    params={"module": "account", "action": "txlist",
                            "address": creator, "sort": "asc"}, timeout=25)
    txlist = (txs or {}).get("result") or []
    if isinstance(txlist, list) and txlist:
        out["tx_count"] = len(txlist)
        try:
            first_ts = int(txlist[0].get("timeStamp", 0))
            if first_ts > 0:
                out["wallet_age_days"] = round(
                    (time.time() - first_ts) / 86400.0, 1)
        except (TypeError, ValueError):
            pass
    out["note"] = "deployer resolved via Robinhood blockscout"
    return out


def _safety_veto(safety):
    if not safety.get("checked"):
        return False
    if safety.get("is_honeypot"):
        return True
    for tax in (safety.get("buy_tax"), safety.get("sell_tax")):
        try:
            if tax is not None and float(tax) > SAFETY_MAX_TAX_PCT:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _fmt_pct(x):
    return "n/a" if x is None else f"{x:+.1f}%"


def _noul_state(chain, address, intel, context):
    pc = intel.get("price_change") or {}
    lines = [
        f"Token {intel.get('symbol') or address[:12]} ({chain}:{address[:18]}...) "
        f"on {intel.get('resolved_chain') or 'unknown chain'}.",
        f"Price ${intel['price_usd']}" if intel.get("price_usd") else "Price unknown.",
        f"1h {_fmt_pct(pc.get('h1'))}, 24h {_fmt_pct(pc.get('h24'))}, "
        f"24h volume ${intel.get('volume_24h')}, liquidity ${intel.get('liquidity_usd')}.",
    ]
    s = intel.get("safety") or {}
    lines.append(
        f"Safety checked: honeypot={s.get('is_honeypot')}, buy tax "
        f"{s.get('buy_tax')}%, sell tax {s.get('sell_tax')}%. " if s.get("checked")
        else f"Safety UNKNOWN: {s.get('note')}. Treat as caution.")
    d = intel.get("deployer") or {}
    lines.append(
        f"Deployer {d.get('address')}, wallet txs {d.get('tx_count')}, "
        f"age {d.get('wallet_age_days')}d." if d.get("checked")
        else "Deployer unknown.")
    lines.append(f"Caller context: {context or 'none provided'}.")
    return "\n".join(lines)


def _ask_noul(state):
    """One Jev Noul call. Returns float or None. Never raises (fail-closed)."""
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    if not api_key:
        return None
    try:
        resp = httpx.post(
            API_URL,
            json={"state": state, "model": MODEL,
                  "questions": {"buy": {"type": "noul",
                                        "instructions": JUDGE_INSTRUCTIONS}}},
            headers={"Authorization": f"Bearer {api_key}",
                     "User-Agent": "musemaxxing-judge/1.0"},
            timeout=TYPESAFE_TIMEOUT,
        )
        resp.raise_for_status()
        node = resp.json().get("answers", {}).get("buy", {})
        val = node.get("noul")
        return float(val) if isinstance(val, (int, float)) else None
    except Exception as exc:
        log.warning("judge noul failed: %s", exc)
        return None


def _size_for_noul(noul):
    for noul_min, size_pct in SIZE_TIERS:
        if noul >= noul_min:
            return size_pct
    return 0.0


def _reasoning(decision, noul, intel, veto):
    sym = intel.get("symbol") or "the token"
    bits = []
    if veto:
        bits.append("safety veto (honeypot or extreme tax) — automatic avoid")
    bits.append(f"Jev buy-Noul {noul:.2f}")
    if intel.get("price_usd"):
        pc = intel.get("price_change") or {}
        bits.append(f"${intel['price_usd']} (24h {_fmt_pct(pc.get('h24'))})")
    if intel.get("liquidity_usd"):
        bits.append(f"liquidity ${intel['liquidity_usd']}")
    if not (intel.get("safety") or {}).get("checked"):
        bits.append("safety unknown — caution")
    return f"{decision.upper()} {sym}: " + "; ".join(bits) + "."


@router.post("/v1/judge")
def judge_token(body: JudgeRequest, request: Request):
    check_rate_limit(request, "judge")
    chain = (body.chain or "").strip().lower()
    address = (body.address or "").strip()
    if not chain or not address:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="chain and address are required")
    if chain != "solana":
        if not (address.startswith("0x") and len(address) == 42):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="EVM address must be 0x + 40 hex chars")
    elif len(address) < 32:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Solana address looks too short")

    intel = {"chain": chain, "address": address, "resolved_chain": None,
             "symbol": None, "price_usd": None, "price_change": {},
             "volume_24h": None, "liquidity_usd": None,
             "safety": {"checked": False, "note": "not attempted"},
             "deployer": {"checked": False, "note": "not attempted"}}
    mkt = _dexscreener(chain, address)
    if mkt:
        intel.update(mkt)
    intel["safety"] = _safety(address, intel["resolved_chain"])
    intel["deployer"] = _deployer(address, intel["resolved_chain"])

    if not os.environ.get("TYPESAFE_API_KEY", ""):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "judge_unavailable",
                    "message": "Jev is not configured on this server yet."})

    veto = _safety_veto(intel["safety"])
    state = _noul_state(chain, address, intel, body.context)
    noul = _ask_noul(state)
    if noul is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "judge_unavailable",
                    "message": "Jev did not return a judgment; try again."})

    if veto or noul < WATCH_NOUL_MIN:
        decision = "avoid"
    elif noul < BUY_NOUL_MIN:
        decision = "watch"
    else:
        decision = "buy" if intel.get("price_usd") else "watch"

    return {
        "decision": decision,
        "noul": round(noul, 3),
        "size_pct": _size_for_noul(noul) if decision == "buy" else 0.0,
        "symbol": intel.get("symbol"),
        "reasoning": _reasoning(decision, noul, intel, veto),
        "intel": {
            "price_usd": intel.get("price_usd"),
            "price_change_24h": (intel.get("price_change") or {}).get("h24"),
            "volume_24h": intel.get("volume_24h"),
            "liquidity_usd": intel.get("liquidity_usd"),
            "safety": intel.get("safety"),
            "deployer": {k: intel["deployer"].get(k)
                         for k in ("checked", "address", "tx_count",
                                   "wallet_age_days", "note")},
        },
    }
