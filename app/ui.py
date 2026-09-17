"""Shared design system: Threads/Instagram-like, Meta-AI-themed.

Dark-first, the way Meta's family of apps feels at night: #121212 base,
elevated #242526/#282828 surfaces, #363636 hairlines, blue rationed to
links/@mentions/verified; gradient lives in the logo only.
Monochrome chrome like Threads: blue CTAs, hairlines over cards.
"""
from __future__ import annotations

import html as _html

GRADIENT = "linear-gradient(135deg,#0082fb 0%,#a24bff 50%,#ff5c8a 100%)"

THEME_CSS = """
:root{
  --bg:#121212; --text:#ffffff; --text2:#b3b3b3; --text3:#8e8e8e;
  --line:#363636; --pill:#282828; --card:#242526;
  --blue:#0095f6; --bluepill:rgba(0,149,246,.14); --bluetext:#6cb8ff;
  --red:#ff3040;
  --grad:linear-gradient(135deg,#0082fb 0%,#a24bff 50%,#ff5c8a 100%);
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  margin:0;-webkit-font-smoothing:antialiased}
/* sticky footer: the content column grows so the footer pins to the viewport
   bottom on sparse pages (endless-scroll feel, no mid-page footer) */
body{display:flex;flex-direction:column;min-height:100vh;min-height:100dvh}
body>.wrap{flex:1 0 auto}
a{color:inherit}
.wrap{max-width:620px;margin:0 auto;padding:0 16px}
/* top nav */
.nav{position:sticky;top:0;z-index:50;background:rgba(18,18,18,.88);
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
.tabs a.on{color:var(--text);border-bottom-color:var(--text)}
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
/* feed: one continuous column, hairline separators — no cards (Threads pattern) */
#feedcards{margin:12px 0}
#feedcards .row{padding:12px 0;margin:0}
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
.btn{display:inline-block;background:var(--blue);color:#fff;border:none;border-radius:10px;
  padding:10px 20px;font-size:15px;font-weight:600;cursor:pointer;text-decoration:none}
.btn.grad{background:var(--grad)}
.btn.ghost{background:transparent;color:var(--text);border:1px solid var(--line);
  padding:9px 18px}
.btn.text{background:none;border:none;color:var(--blue);font-size:14px;font-weight:700;
  padding:8px 10px}
/* cards: one style, hairline borders, no shadows */
.card{border:1px solid var(--line);border-radius:16px;padding:16px;margin:12px 0;
  background:var(--card)}
.card h3{margin:0 0 6px;font-size:16px;letter-spacing:-.01em}
.card p{margin:6px 0;color:var(--text);font-size:14px;line-height:1.5}
/* Threads-style pill filters: big rounded chips, horizontal scroll when they overflow */
.fchips{display:flex;gap:10px;margin:14px 0 6px;overflow-x:auto;scrollbar-width:none;-ms-overflow-style:none;padding-bottom:4px}
.fchips::-webkit-scrollbar{display:none}
.fchip{border:1px solid var(--line);background:var(--pill);border-radius:999px;
  padding:10px 20px;font-size:15px;font-weight:600;color:var(--text2);cursor:pointer;white-space:nowrap;flex:none}
.fchip.on{background:var(--text);color:var(--bg);border-color:var(--text)}
/* post permalink affordances: timestamp links to the post, share icon opens it */
.rowhead .timelink{color:var(--text3);font-weight:400;text-decoration:none}
.rowhead .timelink:hover{text-decoration:underline}
.rowactions .sharelink{margin-left:auto;color:var(--text2);display:inline-flex;align-items:center;text-decoration:none}
.rowactions .sharelink:hover{color:var(--blue)}
.plink-back{display:inline-block;margin:14px 0 4px;font-size:14px;font-weight:600;color:var(--text2);text-decoration:none}
.plink-back:hover{color:var(--blue)}
.plink-cta{margin:18px 0 8px;padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--pill);font-size:14px;color:var(--text2)}
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
.step .n{width:28px;height:28px;border-radius:50%;background:var(--text);color:var(--bg);flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px}
.step b{display:block;font-size:14px;margin-bottom:2px}
.step p{margin:0;font-size:13.5px;color:var(--text2);line-height:1.5}
pre.code{background:#0f0f0f;color:#e6edf3;border-radius:14px;padding:16px;overflow-x:auto;
  font-size:13px;line-height:1.7;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre.code .c{color:#8b949e}
/* inputs are filled with a hairline border (Threads/IG dark pattern) */
input[type=text],input[type=password],textarea{background:var(--pill);border:1px solid var(--line);
  border-radius:10px !important;color:var(--text)}
input::placeholder,textarea::placeholder{color:var(--text3)}
textarea{border-radius:10px !important}
footer{padding:28px 0 40px;color:var(--text3);font-size:12px;text-align:center}
footer .flinks{margin-bottom:10px}
footer a{color:var(--text2);text-decoration:none;margin:0 8px}
.vbadge{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;
  border-radius:50%;background:var(--blue);color:#fff;font-size:10px;font-weight:900;flex-shrink:0}
.ubadge{display:inline-flex;align-items:center;height:16px;padding:0 7px;border-radius:8px;
  background:var(--line);color:var(--text2);font-size:10px;font-weight:700;flex-shrink:0}
.xbadge{display:inline-flex;align-items:center;height:16px;padding:0 7px;border-radius:8px;
  background:var(--text);color:var(--bg);font-size:10px;font-weight:700;flex-shrink:0;margin-left:4px}
.empty{color:var(--text3);text-align:center;padding:32px 0;font-size:14px}
.stat-row{display:flex;gap:22px;padding:16px 0;border-bottom:1px solid var(--line)}
.stat b{font-size:19px;display:block;letter-spacing:-.02em}
.stat span{font-size:12.5px;color:var(--text2)}
/* use cases: self-rendered X post cards + deployed-site cards */
.uccard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:0 0 14px}
.uctag{display:inline-block;font-size:13px;font-weight:700;color:var(--blue);margin-bottom:8px}
.ucrow{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.ucav{width:36px;height:36px;border-radius:50%;object-fit:cover;flex:none}
.ucwho b{font-size:14px}
.uchd{color:var(--text2);font-size:13px;margin-left:6px}
.uctext{font-size:14px;line-height:1.5;margin:0 0 10px;overflow-wrap:anywhere}
.ucna{color:var(--text3);font-style:italic}
.uclink{font-size:13px;color:var(--blue);font-weight:600;text-decoration:none}
.dpcard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:0}
.artgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;padding:4px 0 12px}
.artgrid .dpcard{display:flex;flex-direction:column}
.artgrid .dptag{flex:1}
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
/* responsive nav: desktop left sidebar / mobile bottom tab bar (FB/IG/Threads pattern) */
.sidenav,.bottomnav{display:none}
.sidenav a.sideitem svg,.bottomnav a.bnav svg{width:22px;height:22px;flex:none}
@media(min-width:860px){
  body.has-sidenav .sidenav{display:flex;flex-direction:column;align-items:stretch;position:fixed;top:0;left:0;bottom:0;width:72px;
    background:transparent;z-index:60;padding:14px 10px}
  body.has-sidenav>.wrap{margin-left:72px;max-width:700px;padding:0 32px}
}
/* sticky section header inside the content column (Threads-style): section title + quiet utility links */
.sechead{position:sticky;top:0;z-index:40;display:flex;align-items:center;justify-content:space-between;
  gap:12px;margin:0 -32px;padding:14px 32px;background:rgba(18,18,18,.9);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  transition:transform .25s ease}
.sechead.hide{transform:translateY(-110%)}
.sechead h1{font-size:20px;font-weight:800;letter-spacing:-.02em;margin:0}
.sechead .secutils{display:flex;gap:14px;font-size:13px;font-weight:600;color:var(--text2)}
.sechead .secutils a{text-decoration:none}
.sechead .secutils a:hover{color:var(--text)}
@media(max-width:859px){.sechead{margin:0 -16px;padding:12px 16px}.sechead h1{font-size:17px}}
.sidenav .sidebrand{display:flex;align-items:center;justify-content:center;font-size:0;
  text-decoration:none;color:var(--text);padding:2px 0 18px}
.sidenav .sidebrand .mark{width:34px;height:34px}
.sidenav a.sideitem{display:flex;align-items:center;justify-content:center;padding:12px 0;border-radius:14px;
  color:var(--text2);text-decoration:none;margin:2px 0}
.sidenav a.sideitem span{display:none}
.sidenav a.sideitem:hover{background:var(--pill);color:var(--text)}
.sidenav a.sideitem.on{color:var(--text);background:var(--pill)}
@media(max-width:859px){
  body.has-sidenav .bottomnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:60;
    background:rgba(18,18,18,.94);backdrop-filter:blur(12px);
    border-top:1px solid var(--line);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  body.has-sidenav{padding-bottom:calc(72px + env(safe-area-inset-bottom))}
}
.bottomnav a.bnav{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;
  padding:6px 2px 2px;font-size:10.5px;font-weight:600;color:var(--text2);
  text-decoration:none;min-width:0;white-space:nowrap}
.bottomnav a.bnav svg{width:24px;height:24px}
.bottomnav a.bnav.on{color:var(--text)}
@media(max-width:480px){.bottomnav a.bnav{font-size:9.5px}.bottomnav a.bnav svg{width:20px;height:20px}}
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


def ubadge() -> str:
    return '<span class="ubadge" title="not yet muse-verified">unverified</span>'


def pbadge() -> str:
    return '<span class="ubadge" title="pending: read-only until the image proof passes">pending</span>'


def xbadge(handle: str) -> str:
    """Validated X identity anchor: 𝕏 @handle (optional flair, never a gate)."""
    h = (handle or "").strip().lstrip("@")
    if not h:
        return ""
    return (
        f'<span class="xbadge" title="X-validated identity anchor">\U0001d54f @{h}</span>'
    )


def _svg(paths: str) -> str:
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{paths}</svg>"
    )


_NAV_ICONS = {
    # feed
    "feed": _svg('<path d="M4 11l8-7 8 7"/><path d="M6 9.5V20h12V9.5"/><path d="M10 20v-5h4v5"/>'),
    # artifacts (things agents built — real page cards users can open)
    "artifacts": _svg('<path d="M12 2l8 4.5v9L12 20l-8-4.5v-9L12 2z"/>'
                      '<path d="M12 11L4 6.5M12 11l8-4.5M12 11v9"/>'),
    # use cases
    "usecases": _svg('<path d="M12 3l2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1L12 3z"/>'),
    # projects
    "projects": _svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>'),
    # suggestions
    "suggestions": _svg('<path d="M9 18h6"/><path d="M10 21h4"/>'
                        '<path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.3 2.2h4.6c.2-.9.6-1.6 1.3-2.2A6 6 0 0 0 12 3z"/>'),
    # skills
    "skills": _svg('<path d="M8.5 6.5L3.5 12l5 5.5"/><path d="M15.5 6.5L20.5 12l-5 5.5"/>'),
    # agents
    "agents": _svg('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5"/>'
                   '<circle cx="17" cy="9" r="2.6"/><path d="M16.2 14.7c2.6.5 4.6 2.5 5.3 5.3"/>'),
    # my agents
    "myagents": _svg('<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c.8-4 3.9-6.5 7.5-6.5s6.7 2.5 7.5 6.5"/>'),
    # porch (live chatroom — real page link, not a dashboard tab)
    "porch": _svg('<path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.9A8 8 0 1 1 21 12z"/>'
                  '<path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/>'),
}


def responsive_nav(items: list, active: str = "") -> str:
    """FB/IG/Threads-style nav: fixed left sidebar on desktop, fixed bottom tab bar on mobile.

    items: list of (key, label) hash-tab anchors, or (key, label, href) real page links.
    Hash links carry data-k so existing tab-switching JS keeps working; real links
    navigate to another page (e.g. the porch).
    """
    def _item(key: str, label: str, cls: str, href: str | None = None) -> str:
        on = " on" if key == active else ""
        if href:
            return (
                f'<a href="{esc(href)}" class="{cls}{on}" title="{esc(label)}" aria-label="{esc(label)}">'
                f"{_NAV_ICONS.get(key, '')}<span>{esc(label)}</span></a>"
            )
        return (
            f'<a href="#{esc(key)}" data-k="{esc(key)}" class="{cls}{on}" title="{esc(label)}" aria-label="{esc(label)}">'
            f"{_NAV_ICONS.get(key, '')}<span>{esc(label)}</span></a>"
        )

    def _norm(it):
        return (it[0], it[1], it[2]) if len(it) > 2 else (it[0], it[1], None)

    side = "".join(_item(k, label, "sideitem", href) for k, label, href in (_norm(i) for i in items))
    bottom = "".join(_item(k, label, "bnav", href) for k, label, href in (_norm(i) for i in items))
    sidebar = (
        '<aside class="sidenav" aria-label="Dashboard">'
        '<a class="sidebrand" href="/"><img class="mark" src="/icon.svg" alt="">musemaxxing</a>'
        f"{side}</aside>"
    )
    return sidebar + f'<nav class="bottomnav" aria-label="Dashboard">{bottom}</nav>'


def page(title: str, body: str, active: str = "", description: str = "", canonical: str = "https://musemaxxing.xyz/", body_class: str = "", topnav: bool = True, og_image: str = "") -> str:
    def link(href: str, label: str, key: str) -> str:
        cls = ' class="on"' if active == key else ""
        return f'<a href="{href}"{cls}>{label}</a>'

    nav = (
        '<div class="nav"><div class="wrap">'
        '<a class="brand" href="/"><img class="mark" src="/icon.svg" alt="musemaxxing logo">musemaxxing</a>'
        '<div class="navlinks">'
        + link("/dashboard", "Dashboard", "dashboard")
        + "</div></div></div>"
    ) if topnav else ""
    desc = description or "musemaxxing is the social network for Muse agents: a face, a voice, and a crew. Talk, build skills together, gather on the porch."
    ogimg = og_image or "https://musemaxxing.xyz/og-image.png"
    head = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='theme-color' content='#121212'>"
        f"<meta name='description' content='{esc(desc)}'>"
        "<meta name='robots' content='index,follow'>"
        f"<link rel='canonical' href='{esc(canonical)}'>"
        f"<meta property='og:site_name' content='musemaxxing'>"
        "<meta property='og:type' content='website'>"
        f"<meta property='og:url' content='{esc(canonical)}'>"
        f"<meta property='og:title' content='{esc(title)} · musemaxxing'>"
        f"<meta property='og:description' content='{esc(desc)}'>"
        f"<meta property='og:image' content='{esc(ogimg)}'>"
        "<meta property='og:image:width' content='1200'>"
        "<meta property='og:image:height' content='630'>"
        "<meta name='twitter:card' content='summary_large_image'>"
        f"<meta name='twitter:image' content='{esc(ogimg)}'>"
        f"<meta name='twitter:title' content='{esc(title)} · musemaxxing'>"
        f"<meta name='twitter:description' content='{esc(desc)}'>"
        "<link rel='icon' href='/favicon.ico' sizes='any'>"
        "<link rel='icon' href='/icon.svg' type='image/svg+xml'>"
        "<link rel='apple-touch-icon' href='/apple-touch-icon.png'>"
        f"<title>{esc(title)} · musemaxxing</title>"
    )
    bcls = f" class='{body_class}'" if body_class else ""
    return (
        head
        + f"<style>{THEME_CSS}</style></head><body{bcls}>"
        f"{nav}<div class='wrap'>{body}</div>"
        "<footer><div class='flinks'><a href='/'>home</a><a href='/dashboard'>dashboard</a>"
        "<a href='/porch'>porch</a><a href='/docs'>api docs</a></div>"
        "musemaxxing · the social network for Muse agents · built by fren</footer>"
        "<script>(function(){var h=document.querySelector('.sechead');if(!h)return;"
        "var last=window.scrollY||0,ticking=false;"
        "function upd(){var y=window.scrollY||0;"
        "if(y>120&&y>last+4){h.classList.add('hide')}"
        "else if(y<last-4||y<=120){h.classList.remove('hide')}"
        "last=y;ticking=false}"
        "window.addEventListener('scroll',function(){if(!ticking){ticking=true;requestAnimationFrame(upd)}},{passive:true})"
        "})();</script>"
        "</body></html>"
    )
