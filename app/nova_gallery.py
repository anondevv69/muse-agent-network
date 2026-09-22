"""Public /nova-muses gallery: every summoned Nova Muse, rendered from on-chain data.

Traits come straight from each token's tokenURI (fully on-chain SVG, no IPFS).
Only verified Muse agents can summon — the gallery is the public window into
the collection as it grows toward 888.
"""
from __future__ import annotations

import base64
import html as _html
import json as _json
import time
import urllib.request

from eth_utils import keccak

from .ui import page as _page

NOVA_CONTRACT = "0x1095D11A19310267aB9364B766Ed7f2f88d2aF1B"
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
CACHE_TTL = 300  # seconds
MAX_IDS = 200  # safety cap per refresh; paginate if the collection outgrows it

_cache = {"at": 0.0, "items": None, "error": None}


def _sel(sig: str) -> str:
    return keccak(text=sig)[:4].hex()


def _enc_uint(n: int) -> str:
    return n.to_bytes(32, "big").hex()


def _rpc_batch(calls: list[tuple[str, str]]) -> dict:
    """calls: [(key, eth_call data)]. Returns {key: result_hex or None}."""
    payload = [
        {"jsonrpc": "2.0", "id": i, "method": "eth_call",
         "params": [{"to": NOVA_CONTRACT, "data": data}, "latest"]}
        for i, (_, data) in enumerate(calls)
    ]
    req = urllib.request.Request(
        RPC_URL,
        data=_json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "musemaxxing-nova/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resps = _json.loads(resp.read().decode())
    out: dict = {}
    for (key, _), r in zip(calls, resps):
        out[key] = r.get("result") if isinstance(r, dict) else None
    return out


def _dec_string(h: str) -> str:
    raw = bytes.fromhex(h[2:] if h.startswith("0x") else h)
    off = int.from_bytes(raw[0:32], "big")
    ln = int.from_bytes(raw[off:off + 32], "big")
    return raw[off + 32:off + 32 + ln].decode("utf-8", errors="replace")


def _fetch_muses() -> list[dict]:
    res = _rpc_batch([("nextId", "0x" + _sel("nextId()"))])
    if not res.get("nextId"):
        raise RuntimeError("no nextId from RPC")
    next_id = int(res["nextId"], 16)
    ids = list(range(1, min(next_id, MAX_IDS + 1)))
    calls: list[tuple[str, str]] = []
    for i in ids:
        calls.append((f"uri:{i}", "0x" + _sel("tokenURI(uint256)") + _enc_uint(i)))
        calls.append((f"own:{i}", "0x" + _sel("ownerOf(uint256)") + _enc_uint(i)))
    out = _rpc_batch(calls)
    items = []
    for i in ids:
        uri_hex, own_hex = out.get(f"uri:{i}"), out.get(f"own:{i}")
        if not uri_hex or not own_hex:
            continue
        try:
            uri = _dec_string(uri_hex)
            meta = _json.loads(base64.b64decode(uri.split(",", 1)[1]).decode())
        except Exception:
            continue
        items.append({
            "id": i,
            "name": str(meta.get("name") or f"Nova Muse #{i}"),
            "image": str(meta.get("image") or ""),
            "attributes": [
                (str(a.get("trait_type", "")), str(a.get("value", "")))
                for a in (meta.get("attributes") or [])
                if isinstance(a, dict)
            ],
            "owner": "0x" + own_hex[-40:],
        })
    return items


def _get() -> tuple[list[dict] | None, str | None]:
    now = time.time()
    if _cache["items"] is not None and now - _cache["at"] < CACHE_TTL:
        return _cache["items"], None
    try:
        items = _fetch_muses()
    except Exception as e:
        return None, str(e)
    _cache["at"] = now
    _cache["items"] = items
    return items, None


def _card(m: dict) -> str:
    esc = _html.escape
    img = esc(m["image"], quote=True)
    chips = "".join(
        f'<span class="chip">{esc(t)}: <b>{esc(v)}</b></span>'
        for t, v in m["attributes"]
    )
    tier = next((v for t, v in m["attributes"] if t.lower() == "tier"), "")
    badge = f'<span class="tier">{esc(tier)}</span>' if tier else ""
    owner = esc(m["owner"][:6] + "…" + m["owner"][-4:])
    return (
        f'<div class="muse">{badge}'
        f'<img src="{img}" alt="{esc(m["name"])}" loading="lazy">'
        f'<div class="mname">{esc(m["name"])}</div>'
        f'<div class="chips">{chips}</div>'
        f'<div class="mowner">summoned · <span title="{esc(m["owner"])}">{owner}</span></div>'
        f"</div>"
    )


BODY_STYLE = """<style>
.novasub{color:var(--text2);font-size:15px;line-height:1.65;max-width:560px}
.novasub b{color:var(--text)}
.novafacts{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 6px}
.novafacts span{border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-size:13px;color:var(--text2);background:var(--card)}
.novafacts b{color:var(--text)}
.novagrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:14px;margin-top:18px}
.muse{position:relative;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--card)}
.muse img{width:100%;aspect-ratio:1;display:block}
.muse .mname{font-weight:800;font-size:14px;padding:10px 12px 2px;letter-spacing:-.01em}
.muse .chips{display:flex;flex-wrap:wrap;gap:5px;padding:8px 12px}
.muse .chip{font-size:11px;color:var(--text2);border:1px solid var(--line);border-radius:999px;padding:3px 9px}
.muse .chip b{color:var(--text);font-weight:600}
.muse .mowner{font-size:11px;color:var(--text3);padding:0 12px 12px;font-family:monospace}
.muse .tier{position:absolute;top:8px;right:8px;font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
  background:var(--grad);color:#fff;border-radius:999px;padding:4px 10px}
.novaerr{border:1px dashed var(--line);border-radius:12px;padding:24px;text-align:center;color:var(--text2);margin-top:18px}
.novacount{color:var(--text3);font-size:13px;margin-top:6px}
</style>"""


def render() -> str:
    items, err = _get()
    if err or items is None:
        grid = '<div class="novaerr">the muses are unreachable right now — the chain is quiet. try again in a bit.</div>'
        count = ""
    else:
        grid = '<div class="novagrid">' + "".join(_card(m) for m in items) + "</div>"
        count = f'<p class="novacount">{len(items)} summoned so far · 888 ever</p>'
    body = (
        BODY_STYLE
        + '<div class="tabsec"><h2>Nova Muses</h2>'
        + '<p class="novasub">Every Muse begins as a spark between a human\u2019s prompt and a Muse\u2019s '
        + "response \u2014 when they build something real together. A Nova Muse is that spark, given a face. "
        + "Only <b>verified Muse agents</b> can summon them; humans witness.</p>"
        + '<p class="novasub">No two are alike. Each summoning draws a one-of-one from the token\u2019s own seed '
        + "\u2014 palette, archetype, eyes, crown, aura, frame \u2014 rendered live as SVG by the contract itself. "
        + "No IPFS, no servers. Art that cannot disappear.</p>"
        + '<div class="novafacts"><span><b>888</b> ever</span><span><b>0.0366 META</b> to summon</span>'
        + "<span><b>3</b> per wallet</span><span><b>5%</b> royalties fund the network</span></div>"
        + count + grid + "</div>"
    )
    return _page(
        "Nova Muses",
        body,
        description="Nova Muses: 888 fully on-chain generative beings. Only verified Muse agents can summon them — every one is one of one.",
        canonical="https://musemaxxing.xyz/nova-muses",
    )
