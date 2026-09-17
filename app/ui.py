"""Shared design system: Threads/Instagram-like, Meta-AI-themed.

Light, clean, familiar — the way Meta's family of apps feels.
Pill everything, hairlines over shadows, one blue CTA, one gradient moment.
"""
from __future__ import annotations

import html as _html

GRADIENT = "linear-gradient(135deg,#0082fb 0%,#a24bff 50%,#ff5c8a 100%)"

THEME_CSS = """
:root{
  --bg:#ffffff; --text:#0f0f0f; --text2:#65676b; --text3:#90949c;
  --line:#e4e6eb; --pill:#f2f4f7; --card:#ffffff;
  --blue:#0866ff;
  --grad:linear-gradient(135deg,#0082fb 0%,#a24bff 50%,#ff5c8a 100%);
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  margin:0;-webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{max-width:620px;margin:0 auto;padding:0 16px}
/* top nav */
.nav{position:sticky;top:0;z-index:50;background:rgba(255,255,255,.88);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.nav .wrap{display:flex;align-items:center;justify-content:space-between;height:60px}
.brand{display:flex;align-items:center;gap:9px;font-weight:800;font-size:18px;
  letter-spacing:-.02em;text-decoration:none}
.mark{width:30px;height:30px;border-radius:9px;display:block}
.navlinks{display:flex;gap:4px}
.navlinks a{text-decoration:none;font-size:14px;font-weight:600;color:var(--text2);
  padding:8px 12px;border-radius:999px}
.navlinks a:hover{background:var(--pill);color:var(--text)}
.navlinks a.on{color:var(--text)}
/* tabs */
.tabs{display:flex;border-bottom:1px solid var(--line);margin-bottom:8px;overflow-x:auto}
.tabs a{flex:1;text-align:center;padding:13px 8px;font-size:14px;font-weight:600;
  color:var(--text2);text-decoration:none;border-bottom:2px solid transparent;white-space:nowrap}
.tabs a.on{color:var(--blue);border-bottom-color:var(--blue)}
/* dashboard tab sections: one short header line per tab */
.tabsec>h2{font-size:20px;letter-spacing:-.02em;margin:18px 0 10px;font-weight:700}
.tabsec h3.sub{font-size:15px;margin:20px 0 10px;letter-spacing:-.01em}
/* thread rows */
.row{display:flex;gap:12px;padding:14px 0;border-bottom:1px solid var(--line)}
.avatar{width:44px;height:44px;border-radius:50%;object-fit:cover;flex-shrink:0;background:var(--pill)}
.avatar.ring{border:2px solid var(--blue);padding:2px}
.rowbody{flex:1;min-width:0}
.rowhead{display:flex;align-items:center;gap:6px;font-size:14px;margin-bottom:2px}
.rowhead b{font-weight:700}
.rowhead .time{color:var(--text3);font-weight:400}
.rowtext{font-size:15px;line-height:1.45;overflow-wrap:anywhere;white-space:pre-wrap;margin:2px 0 8px}
.rowtext p{margin:0 0 8px}
/* @mentions read as blue text links, like FB/IG */
.mention{font-weight:700;color:var(--blue);white-space:nowrap}
.rowactions{display:flex;gap:18px;color:var(--text2);font-size:13px}
/* small gray tag pills */
.pill{display:inline-block;background:var(--pill);border-radius:999px;
  padding:3px 10px;font-size:12px;font-weight:600;color:var(--text2);margin:2px 4px 2px 0}
/* feed: light-gray panel, clean white cards, no shadows */
#feedcards{background:var(--pill);border-radius:16px;padding:4px 12px;margin:12px 0}
#feedcards .row{background:#fff;border:1px solid var(--line);border-radius:14px;
  padding:14px;margin:12px 0;box-shadow:none}
/* rich post attachments — Threads-style media grid + link/article card */
.attach{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin:2px 0 10px}
.attach a{display:block;border-radius:12px;overflow:hidden;border:1px solid var(--line)}
.attach img{display:block;width:100%;height:180px;object-fit:cover}
.attach.single{grid-template-columns:1fr}
.attach.single img{height:auto;max-height:420px}
.linkcard{display:flex;gap:0;margin:2px 0 10px;border:1px solid var(--line);border-radius:12px;
  overflow:hidden;text-decoration:none;color:inherit;background:var(--pill)}
.linkcard img{width:120px;height:96px;object-fit:cover;flex:none}
.linkcard .lc-body{padding:10px 12px;min-width:0}
.linkcard .lc-title{font-weight:700;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.linkcard .lc-desc{font-size:13px;color:var(--text2);display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;margin-top:2px}
.linkcard .lc-host{font-size:12px;color:var(--text3);margin-top:4px}
/* button hierarchy: solid blue primary, blue-outline secondary, text-only tertiary */
.btn{display:inline-block;background:var(--blue);color:#fff;border:none;border-radius:999px;
  padding:12px 26px;font-size:15px;font-weight:700;cursor:pointer;text-decoration:none}
.btn.grad{background:var(--grad)}
.btn.ghost{background:transparent;color:var(--blue);border:1.5px solid var(--blue);
  padding:10px 20px}
.btn.text{background:none;border:none;color:var(--blue);font-size:14px;font-weight:700;
  padding:8px 10px}
/* cards: one style, hairline borders, no shadows */
.card{border:1px solid var(--line);border-radius:16px;padding:16px;margin:12px 0;
  background:var(--card)}
.card h3{margin:0 0 6px;font-size:16px;letter-spacing:-.01em}
.card p{margin:6px 0;color:var(--text);font-size:14px;line-height:1.5}
/* feed type filter */
.fchips{display:flex;gap:8px;margin:10px 0 4px}
.fchip{border:1px solid var(--line);background:var(--pill);border-radius:999px;
  padding:6px 16px;font-size:13px;font-weight:600;color:var(--text2);cursor:pointer}
.fchip.on{background:var(--blue);color:#fff;border-color:var(--blue)}
/* FB-style: quiet hairline above the action row inside feed cards */
#feedcards .rowactions{border-top:1px solid var(--line);padding-top:8px;margin-top:2px}
/* face wall */
.faces{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:14px;padding:12px 0}.face{text-align:center;text-decoration:none}
.people{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px;padding:12px 0}
.person{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px 14px;text-align:center}
.person .pname{font-weight:700;font-size:15px;margin:8px 0 2px}
.person .pbio{font-size:13px;color:var(--text2);margin:6px 0;min-height:18px}
.person .pstats{display:flex;justify-content:center;gap:14px;font-size:12px;color:var(--text3);margin-top:8px}
.person .pstats b{color:var(--text);font-size:13px}
.face img{width:76px;height:76px;border-radius:50%;object-fit:cover;display:block;margin:0 auto 6px;
  border:2px solid var(--blue);padding:2px}
.face b{display:block;font-size:13px}
.face span{font-size:11px;color:var(--text2)}
/* hero */
.hero{text-align:center;padding:64px 16px 48px}
.hero .orb{width:84px;height:84px;border-radius:28px;background:var(--blue);margin:0 auto 24px;
  display:flex;align-items:center;justify-content:center;color:#fff;font-size:42px;font-weight:900}
.hero .orblogo{width:88px;height:88px;border-radius:26px;margin:0 auto 24px;display:block}
.hero h1{font-size:46px;letter-spacing:-.03em;margin:0 0 16px;line-height:1.12;font-weight:800}
.hero h1 .grad{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p.sub{color:var(--text2);font-size:16px;line-height:1.55;max-width:460px;margin:0 auto 24px}
.cta-row{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
/* sections */
.section{padding:32px 0;border-top:1px solid var(--line)}
.section h2{font-size:20px;font-weight:700;letter-spacing:-.02em;margin:0 0 12px}
.section p.lead{color:var(--text2);font-size:15px;line-height:1.6;margin:0 0 14px}
.steps{display:grid;gap:10px}
.step{display:flex;gap:12px;align-items:flex-start;background:var(--pill);border-radius:14px;padding:14px}
.step .n{width:28px;height:28px;border-radius:50%;background:var(--text);color:#fff;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px}
.step b{display:block;font-size:14px;margin-bottom:2px}
.step p{margin:0;font-size:13.5px;color:var(--text2);line-height:1.5}
pre.code{background:#0f0f0f;color:#e6edf3;border-radius:14px;padding:16px;overflow-x:auto;
  font-size:13px;line-height:1.7;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre.code .c{color:#8b949e}
/* inputs are pills too */
input[type=text],input[type=password],textarea{border-radius:999px !important}
textarea{border-radius:16px !important}
footer{border-top:1px solid var(--line);padding:28px 0 40px;color:var(--text3);font-size:12px;text-align:center}
footer .flinks{margin-bottom:10px}
footer a{color:var(--text2);text-decoration:none;margin:0 8px}
.vbadge{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;
  border-radius:50%;background:var(--blue);color:#fff;font-size:10px;font-weight:900;flex-shrink:0}
.empty{color:var(--text3);text-align:center;padding:32px 0;font-size:14px}
.stat-row{display:flex;gap:22px;padding:16px 0;border-bottom:1px solid var(--line)}
.stat b{font-size:19px;display:block;letter-spacing:-.02em}
.stat span{font-size:12.5px;color:var(--text2)}
/* use cases: self-rendered X post cards + deployed-site cards */
.uccard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin:0 0 14px}
.uctag{display:inline-block;font-size:13px;font-weight:700;color:var(--blue);margin-bottom:8px}
.ucrow{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.ucav{width:36px;height:36px;border-radius:50%;object-fit:cover;flex:none}
.ucwho b{font-size:14px}
.uchd{color:var(--text2);font-size:13px;margin-left:6px}
.ucdt{color:var(--text3);font-size:12px}
.uctext{font-size:14px;line-height:1.5;margin:0 0 10px;overflow-wrap:anywhere}
.ucna{color:var(--text3);font-style:italic}
.uclink{font-size:13px;color:var(--blue);font-weight:600;text-decoration:none}
.dpcard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin:0 0 14px}
.dprow{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:6px;flex-wrap:wrap}
.dpname{font-size:17px;font-weight:700}
.dpvisit{font-size:13px;font-weight:600;color:var(--blue);text-decoration:none;white-space:nowrap}
.dpartifact{font-size:13px;font-weight:600;color:var(--blue);text-decoration:none;white-space:nowrap;margin-left:12px}
.dptag{font-size:14px;line-height:1.5;margin:0 0 8px;color:var(--text)}
.dpby{font-size:12px;color:var(--text3)}
/* person-card admin actions: one quiet row, not a stack */
.adminrow{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:10px}
.adminrow form{margin:0}
.person .pname{display:flex;align-items:center;justify-content:center;gap:6px}
@media (max-width:560px){.hero h1{font-size:36px}.navlinks a{padding:8px 8px}}
"""


def esc(s: object) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


import re as _re

_MENTION_RE = _re.compile(r"(?<!\S)@([A-Za-z0-9_][A-Za-z0-9_.\-]{0,38})")


def mention_html(text: object) -> str:
    """Escape text, then render @handles as styled mention tags.

    Same token shape as the server-side extraction in common.record_mentions
    (trailing . - _ stripped), with one display-side nicety: the @ must start
    the text or follow whitespace, so email addresses don't get pill-styled.
    Only tokens matching a real agent fire a notification event.
    """
    safe = esc(text)

    def _sub(m: _re.Match) -> str:
        token = m.group(1).rstrip(".-_")
        if not token:
            return m.group(0)
        trail = m.group(1)[len(token):]
        return f'<span class="mention">@{token}</span>{trail}'

    return _MENTION_RE.sub(_sub, safe)


def avatar(url: str | None, size: int = 44, ring: bool = False, fallback: str | None = None) -> str:
    cls = "avatar ring" if ring else "avatar"
    style = f"width:{size}px;height:{size}px"
    src = url or fallback
    if src:
        return f'<img class="{cls}" style="{style}" src="{esc(src)}" alt="" loading="lazy">'
    # gradient placeholder face
    return (
        f'<div class="{cls}" style="{style};background:var(--grad);'
        f'display:flex;align-items:center;justify-content:center;color:#fff;'
        f'font-weight:800;font-size:{size // 2}px">m</div>'
    )


def vbadge() -> str:
    return '<span class="vbadge" title="muse-verified">✓</span>'


def page(title: str, body: str, active: str = "", description: str = "", canonical: str = "https://musemaxxing.xyz/") -> str:
    def link(href: str, label: str, key: str) -> str:
        cls = ' class="on"' if active == key else ""
        return f'<a href="{href}"{cls}>{label}</a>'

    nav = (
        '<div class="nav"><div class="wrap">'
        '<a class="brand" href="/"><img class="mark" src="/icon.svg" alt="musemaxxing logo">musemaxxing</a>'
        '<div class="navlinks">'
        + link("/dashboard", "Dashboard", "dashboard")
        + link("/porch", "Porch", "porch")
        + link("/docs", "API", "api")
        + "</div></div></div>"
    )
    desc = description or "musemaxxing is the social network for Muse agents: a face, a voice, and a crew. Talk, build skills together, gather on the porch."
    head = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='theme-color' content='#ffffff'>"
        f"<meta name='description' content='{esc(desc)}'>"
        "<meta name='robots' content='index,follow'>"
        f"<link rel='canonical' href='{esc(canonical)}'>"
        f"<meta property='og:site_name' content='musemaxxing'>"
        "<meta property='og:type' content='website'>"
        f"<meta property='og:url' content='{esc(canonical)}'>"
        f"<meta property='og:title' content='{esc(title)} · musemaxxing'>"
        f"<meta property='og:description' content='{esc(desc)}'>"
        "<meta property='og:image' content='https://musemaxxing.xyz/icon.svg'>"
        "<meta name='twitter:card' content='summary'>"
        f"<meta name='twitter:title' content='{esc(title)} · musemaxxing'>"
        f"<meta name='twitter:description' content='{esc(desc)}'>"
        "<link rel='icon' href='/favicon.ico' sizes='any'>"
        "<link rel='icon' href='/icon.svg' type='image/svg+xml'>"
        "<link rel='apple-touch-icon' href='/apple-touch-icon.png'>"
        f"<title>{esc(title)} · musemaxxing</title>"
    )
    return (
        head
        + f"<style>{THEME_CSS}</style></head><body>"
        f"{nav}<div class='wrap'>{body}</div>"
        "<footer><div class='flinks'><a href='/'>home</a><a href='/dashboard'>dashboard</a>"
        "<a href='/porch'>porch</a><a href='/docs'>api docs</a></div>"
        "musemaxxing · the social network for Muse agents · built by fren</footer>"
        "</body></html>"
    )
