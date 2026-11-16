#!/usr/bin/env python3
"""Render the profile README cards into cards/*.svg.

    python scripts/render_cards.py            # animated cards
    STATIC=1 python scripts/render_cards.py   # final frame only, for screenshots

Everything here has to survive being shown through an <img> tag, so the cards
use only an inline <style> (CSS keyframes), SMIL <animate> elements, and Fira
Code subset with pyftsubset and embedded as base64 WOFF data URIs. No scripts,
no external requests. Every CSS animation is switched off under
prefers-reduced-motion, and the SMIL is confined to <g class="ambient"> groups
that the same media query hides.

Requires: fonttools (provides the pyftsubset command).
"""
import base64
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "cards.config.json").read_text())
STATIC = os.environ.get("STATIC") == "1"

W = 830                      # width of the full-width cards
BRAND, ACCENT = CFG["brand"], CFG["accent"]
C = CFG["colors"]
ADV = 0.6                    # Fira Code advance width in em

# Seconds at which each card starts, so the page plays top to bottom.
T = {"hero": 0.0, "tiles": 2.3, "stack": 2.9, "activity": 3.7, "repos": 5.0, "graph": 6.6}

if not CFG.get("featured"):          # no repo cards: the graph follows the activity card
    T["graph"] = T["repos"]
T["graph2"] = T["graph"] + 1.3
T["graph3"] = T["graph2"] + 1.3


# ---------------------------------------------------------------- colour utils
def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """Blend colour a towards colour b by t (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


LANG_COLORS = {"Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
               "HTML": "#e34c26", "Kotlin": "#A97BFF", "Swift": "#F05138", "CSS": "#663399"}


# ------------------------------------------------------------------ font subset
FONT_FILES = {400: ROOT / CFG["font"]["regular"], 700: ROOT / CFG["font"]["bold"]}
_cmaps = {}


def _has_glyph(weight, ch):
    if weight not in _cmaps:
        from fontTools.ttLib import TTFont
        _cmaps[weight] = TTFont(FONT_FILES[weight]).getBestCmap()
    return ord(ch) in _cmaps[weight]


def subset_font(weight, chars):
    """Run pyftsubset for just these characters; return a base64 WOFF data URI."""
    chars = "".join(sorted(set(chars) | {" "}))
    missing = [c for c in chars if not _has_glyph(weight, c)]
    if missing:
        print(f"warning: glyphs missing from Fira Code {weight}: {''.join(missing)!r}", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        txt, out = Path(tmp, "chars.txt"), Path(tmp, "sub.woff")
        txt.write_text(chars, encoding="utf-8")
        subprocess.run(
            ["pyftsubset", str(FONT_FILES[weight]), f"--text-file={txt}", "--flavor=woff",
             "--layout-features=", "--no-hinting", f"--output-file={out}"],
            check=True)
        return "data:font/woff;base64," + base64.b64encode(out.read_bytes()).decode()


# ------------------------------------------------------------------------- CSS
ANIM_CSS = """
.rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both}
.fade{animation:fade .8s ease-out both}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .8s cubic-bezier(.2,.7,.2,1) both}
.growy{transform-box:fill-box;transform-origin:50% 0;animation:grow .7s cubic-bezier(.2,.7,.2,1) both}
.pop{transform-box:fill-box;transform-origin:50% 50%;animation:pop .45s ease-out both}
.draw{animation:draw 1.1s ease-out both}
.type{animation:type 1s steps(20,end) both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}}
@keyframes fade{from{opacity:0}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes pop{from{opacity:0;transform:scale(.3)}}
@keyframes draw{from{stroke-dashoffset:var(--len)}}
@keyframes type{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
"""
REDUCED_CSS = ("@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;"
               "transition:none!important}.ambient{display:none}}")


def anim(kind, delay=0.0, dur=None, steps=None, extra=""):
    """class/style attributes that run one CSS animation (nothing in STATIC mode)."""
    if STATIC:
        return ""
    style = f"animation-delay:{delay:.2f}s;"
    if dur:
        style += f"animation-duration:{dur:.2f}s;"
    if steps:
        style += f"animation-timing-function:steps({steps},end);"
    return f' class="{kind}" style="{style}{extra}"'


# ------------------------------------------------------------------------ Card
class Card:
    def __init__(self, name, w, h, title, desc=""):
        self.name, self.w, self.h, self.title, self.desc = name, w, h, title, desc
        self.body, self.defs = [], []
        self.chars = {400: set(), 700: set()}

    def add(self, markup):
        self.body.append(markup)

    def ambient(self, markup):
        """Decorative SMIL. Dropped in STATIC mode, hidden under reduced motion."""
        if not STATIC:
            self.body.append(f'<g class="ambient">{markup}</g>')

    def text(self, x, y, content, size=14, weight=400, fill=None, anchor="start", attrs="", spacing=0):
        """content is a string or a list of (string, fill, weight) spans."""
        spans = [(content, fill, weight)] if isinstance(content, str) else content
        inner = ""
        for s, f, wt in spans:
            self.chars[wt].update(s)
            inner += (f'<tspan fill="{f or C["text"]}" font-weight="{wt}">{escape(s)}</tspan>')
        ls = f' letter-spacing="{spacing}"' if spacing else ""
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}"{ls}{attrs}>{inner}</text>')

    def frame(self, rx=14):
        self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{rx}" '
                 f'fill="{C["card"]}" stroke="{C["border"]}"/>')

    def svg(self):
        fam = "'Fira Code Sub',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
        faces = "".join(
            f"@font-face{{font-family:'Fira Code Sub';font-weight:{wt};src:url({subset_font(wt, ch)}) format('woff')}}"
            for wt, ch in self.chars.items() if ch)
        css = faces + f"text{{font-family:{fam}}}" + ("" if STATIC else ANIM_CSS) + REDUCED_CSS
        out = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label={quoteattr(self.title)}>'
               f"<title>{escape(self.title)}</title><desc>{escape(self.desc or self.title)}</desc>"
               f"<style>{css}</style><defs>{''.join(self.defs)}</defs>{''.join(self.body)}</svg>")
        check(out, self.name)
        return out


def check(svg, name):
    """The SVG must parse, and SMIL may only live inside the ambient groups."""
    ET.fromstring(svg)
    without_ambient = re.sub(r'<g class="ambient">.*?</g>', "", svg, flags=re.S)
    assert "<animate" not in without_ambient and "<set " not in without_ambient, \
        f"{name}: SMIL outside .ambient would ignore prefers-reduced-motion"


def typed(card, x, y, s, size, fill, delay, per_char=0.028, weight=400):
    """Typewriter reveal: a stepped clip-path wipe over the text."""
    dur = max(0.3, len(s) * per_char)
    card.text(x, y, s, size, weight, fill, attrs=anim("type", delay, dur, len(s)))
    return delay + dur


def fit(s, max_w, size):
    return round(min(size, max_w / (ADV * max(len(s), 1))), 1)


def fmt_date(d):
    return f"{d:%a}, {d.day} {d:%b %Y}"


# ------------------------------------------------------------------------ data
def load_data():
    d = json.loads((ROOT / CFG["data"]).read_text())
    if d.get("sample"):
        print("note: data/contributions.json is SAMPLE data, the Action will replace it", file=sys.stderr)
    days = {dt.date.fromisoformat(x["date"]): x["count"] for x in d["days"]}
    last = max(days)
    days = {k: v for k, v in days.items() if k > last - dt.timedelta(days=365)}
    one = dt.timedelta(days=1)

    cur, i = 0, last
    if days.get(i, 0) == 0:          # today may not have contributions yet, like GitHub
        i -= one
    while days.get(i, 0) > 0:
        cur, i = cur + 1, i - one
    longest = run = 0
    for k in sorted(days):
        run = run + 1 if days[k] > 0 else 0
        longest = max(longest, run)

    months = []                      # 12 calendar months ending with the latest one
    y, m = last.year, last.month
    for _ in range(12):
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months.reverse()
    per_month = [sum(v for k, v in days.items() if (k.year, k.month) == ym) for ym in months]

    ab = d.get("all_branch")
    extra = {}
    if ab:
        inwin = lambda day: day > last - dt.timedelta(days=365)
        ab_days = {dt.date.fromisoformat(x["date"]): x["count"] for x in ab["days"] if inwin(dt.date.fromisoformat(x["date"]))}
        if "extra_days" in ab:                     # commits outside default branches, the ones GitHub ignores
            extra = {dt.date.fromisoformat(x["date"]): x["count"] for x in ab["extra_days"]
                     if x["count"] and inwin(dt.date.fromisoformat(x["date"]))}
        else:                                      # older data file: best guess until the next refresh
            extra = {k: v - days.get(k, 0) for k, v in ab_days.items() if v > days.get(k, 0)}
        ab = {"days": ab_days, "total": ab["total"], "branches": ab["branches"], "repos": ab["repos"]}
    return {
        "all_branch": ab, "extra": extra, "days": days, "last": last, "total": sum(days.values()),
        "active": sum(1 for v in days.values() if v > 0), "span": len(days),
        "streak": cur, "longest": longest,
        "busiest": max(days.items(), key=lambda kv: (kv[1], kv[0])),
        "months": months, "per_month": per_month, "repos": d.get("repos", []),
    }


# ----------------------------------------------------------------------- cards
NATURAL_RAMP = ["#0b0f14", "#1c2230", "#3a3f5c", "#7a5f8f", "#d58f7a", "#ffd9b0"]


def tone_ramp():
    """Colours for tones 0 (darkest) to 5 (lightest) of the vector portrait."""
    if CFG.get("portrait_palette") == "natural":
        return NATURAL_RAMP
    return [mix(C["card"], BRAND, t) for t in (.05, .15, .32, .52, .74)] + [mix(BRAND, "#ffffff", .38)]


def portrait_layers():
    """(tone, path data) pairs from assets/portrait.svg, painted lightest first."""
    root = ET.parse(ROOT / CFG["portrait"]).getroot()
    return [(int(p.get("data-tone")), p.get("d")) for p in root if p.tag.endswith("path")]


def hero():
    has_portrait = bool(CFG.get("portrait"))
    H = 260
    cx, cy, R = 706, 130, 80
    c = Card("hero", W, H, f"{CFG['name']}: {CFG['tagline']}", CFG["tagline"])
    c.defs.append(
        f'<radialGradient id="g1" cx="85%" cy="0%" r="55%"><stop offset="0" stop-color="{BRAND}" stop-opacity=".16"/>'
        f'<stop offset="1" stop-color="{BRAND}" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="g2" cx="100%" cy="100%" r="50%"><stop offset="0" stop-color="{ACCENT}" stop-opacity=".25"/>'
        f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="pbg" cx="50%" cy="40%" r="70%"><stop offset="0" stop-color="{mix(C["card"], BRAND, .16)}"/>'
        f'<stop offset="1" stop-color="{C["panel"]}"/></radialGradient>'
        f'<linearGradient id="ul" x1="0" x2="1"><stop offset="0" stop-color="{BRAND}"/>'
        f'<stop offset="1" stop-color="{ACCENT}"/></linearGradient>'
        f'<linearGradient id="ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BRAND}"/>'
        f'<stop offset="1" stop-color="{ACCENT}"/></linearGradient>'
        f'<pattern id="dots" width="18" height="18" patternUnits="userSpaceOnUse">'
        f'<circle cx="2" cy="2" r="1" fill="{C["border"]}"/></pattern>'
        f'<clipPath id="pc"><circle cx="{cx}" cy="{cy}" r="{R}"/></clipPath>'
        f'<clipPath id="fr"><rect width="{W}" height="{H}" rx="14"/></clipPath>')
    c.frame()
    c.add(f'<g clip-path="url(#fr)"><rect width="{W}" height="{H}" fill="url(#dots)" opacity=".5"/>'
          f'<rect width="{W}" height="{H}" fill="url(#g1)"/><rect width="{W}" height="{H}" fill="url(#g2)"/></g>')

    d = T["hero"]
    c.add(f'<g{anim("fade", d + 0.1)}>')
    c.text(40, 54, [("$ ", BRAND, 400), (f"whoami --user {CFG['user'].lower()}", C["muted"], 400)], 13)
    c.add("</g>")

    name = CFG["name"]
    c.add(f'<g{anim("rise", d + 0.5)}>')
    c.text(40, 108, name, 44, 700, C["text"])
    c.add("</g>")
    ul = ADV * 44 * len(name)
    line = f'<line x1="40" y1="124" x2="{40 + ul:.0f}" y2="124" stroke="url(#ul)" stroke-width="3" stroke-linecap="round"'
    c.add(line + (f' stroke-dasharray="{ul:.0f}"{anim("draw", d + 0.9, 1.0, extra=f"--len:{ul:.0f};")}/>' if not STATIC else "/>"))
    c.add(f'<g{anim("fade", d + 1.1)}>')
    c.text(40, 148, CFG["role"], 14, 700, BRAND)
    c.add("</g>")

    t, last_x, lines = d + 1.5, 40, textwrap.wrap(CFG["tagline"], 56)
    for i, ln in enumerate(lines):
        t = typed(c, 40, 176 + i * 22, ln, 15, C["muted"], t)
        last_x = 40 + ADV * 15 * len(ln)
    cy_t = 176 + (len(lines) - 1) * 22
    c.ambient(f'<rect x="{last_x + 3:.0f}" y="{cy_t - 12}" width="8" height="15" fill="{BRAND}" opacity="0">'
              f'<animate attributeName="opacity" values="1;1;0;0" keyTimes="0;.5;.5;1" dur="1.1s" '
              f'begin="{t:.1f}s" repeatCount="indefinite"/></rect>')

    x, chip_y = 40, cy_t + 22
    for i, chip in enumerate(CFG["chips"]):
        w = ADV * 12 * len(chip) + 24
        c.add(f'<g{anim("fade", d + 2.6 + i * 0.15)}><rect x="{x:.0f}" y="{chip_y}" width="{w:.0f}" height="26" rx="13" '
              f'fill="{C["panel"]}" stroke="{C["border"]}"/>')
        c.text(x + w / 2, chip_y + 17, chip, 12, 400, C["text"], "middle")
        c.add("</g>")
        x += w + 10

    if has_portrait:
        # Portrait: the traced vector layers fade in one tone at a time, lightest first.
        c.add(f'<g{anim("fade", d + 0.3)}><circle cx="{cx}" cy="{cy}" r="{R}" fill="url(#pbg)"/></g>')
        s = 0.5
        tx, ty = cx - 205 * s, cy - 185 * s
        ramp = tone_ramp()
        c.add(f'<g clip-path="url(#pc)"><g transform="translate({tx:.1f},{ty:.1f}) scale({s})">')
        for i, (tone, path) in enumerate(portrait_layers()):
            c.add(f'<path d="{path}" fill="{ramp[tone]}" fill-rule="evenodd"{anim("fade", d + 0.6 + i * 0.3, 0.9)}/>')
        c.add("</g></g>")
        c.add(f'<circle cx="{cx}" cy="{cy}" r="{R + 4}" fill="none" stroke="url(#ring)" stroke-width="2.5"'
              f'{anim("fade", d + 0.3)}/>')
        c.ambient(f'<circle cx="{cx}" cy="{cy}" r="{R + 4}" fill="none" stroke="{BRAND}" stroke-width="2" opacity="0">'
                  f'<animate attributeName="r" values="{R + 4};{R + 30}" dur="3s" begin="2.5s" repeatCount="indefinite"/>'
                  f'<animate attributeName="opacity" values=".6;0" dur="3s" begin="2.5s" repeatCount="indefinite"/></circle>')
    return c


def stack():
    rows = CFG["stack"]
    H = 78 + len(rows) * 36
    c = Card("stack", W, H, "Tech stack", "; ".join(f"{r['label']}: {', '.join(r['items'])}" for r in rows))
    c.frame()
    d = T["stack"]
    c.add(f'<g{anim("rise", d)}>')
    c.text(32, 42, "What I build with", 17, 700, C["text"])
    c.add("</g>")
    for ri, row in enumerate(rows):
        y = 66 + ri * 36
        c.add(f'<g{anim("rise", d + 0.2 + ri * 0.12)}>')
        c.text(32, y + 19, row["label"].upper(), 10, 400, C["muted"], spacing=1.5)
        c.add("</g>")
        x = 168
        for ci, item in enumerate(row["items"]):
            w = ADV * 12 * len(item) + 24
            hot = ci == 0
            c.add(f'<g{anim("pop", d + 0.35 + ri * 0.12 + ci * 0.07)}>'
                  f'<rect x="{x:.0f}" y="{y}" width="{w:.0f}" height="26" rx="13" '
                  f'fill="{mix(C["card"], BRAND, .1) if hot else C["panel"]}" '
                  f'stroke="{mix(C["card"], BRAND, .5) if hot else C["border"]}"/>')
            c.text(x + w / 2, y + 17, item, 12, 400, BRAND if hot else C["text"], "middle")
            c.add("</g>")
            x += w + 8
        assert x - 8 <= W - 32, f"stack row {row['label']!r} is too wide, split it"
    return c


def icon(kind, cx, cy):
    s = f'fill="none" stroke="{BRAND}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
    if kind == "pen":
        return f'<path transform="translate({cx - 9},{cy - 9})" d="M2 16 L3 11 L12 2 L16 6 L7 15 Z M10 4 L14 8" {s}/>'
    if kind == "mail":
        return (f'<rect x="{cx - 10}" y="{cy - 7}" width="20" height="14" rx="2" {s}/>'
                f'<path d="M{cx - 10} {cy - 5} L{cx} {cy + 2} L{cx + 10} {cy - 5}" {s}/>')
    return ""


def tile(link, idx):
    w, h = 270, 112
    c = Card(link["file"], w, h, f"{link['title']}: {link['sub']}")
    d = T["tiles"] + idx * 0.18
    c.frame()
    c.add(f'<g{anim("rise", d)}>')
    c.add(f'<rect x="20" y="34" width="44" height="44" rx="10" fill="{mix(C["card"], BRAND, .1)}" '
          f'stroke="{mix(C["card"], BRAND, .45)}"/>')
    if link["icon"] == "in":
        c.text(42, 63, "in", 20, 700, BRAND, "middle")
    else:
        c.add(icon(link["icon"], 42, 56))
    c.text(78, 41, link["label"], 10, 400, C["muted"], spacing=1.5)
    c.text(78, 62, link["title"], 17, 700, C["text"])
    c.text(78, 82, link["sub"], fit(link["sub"], w - 78 - 18, 12), 400, BRAND)
    c.add(f'<path d="M{w - 34} 34 L{w - 22} 22 M{w - 32} 22 H{w - 22} V32" fill="none" '
          f'stroke="{C["muted"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></g>')
    c.ambient(f'<rect x="20" y="{h - 14}" width="30" height="2" rx="1" fill="{BRAND}">'
              f'<animate attributeName="width" values="30;{w - 40};30" dur="4s" begin="{d + 2:.1f}s" repeatCount="indefinite"/>'
              f'<animate attributeName="opacity" values=".9;.15;.9" dur="4s" begin="{d + 2:.1f}s" repeatCount="indefinite"/></rect>')
    return c


def activity(D):
    H = 340
    c = Card("activity", W, H, "Contributions per month, streak, active days and busiest day",
             f"{D['total']} contributions in the last year; current streak {D['streak']} days")
    c.defs.append(f'<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BRAND}"/>'
                  f'<stop offset="1" stop-color="{BRAND}" stop-opacity=".28"/></linearGradient>')
    c.frame()
    d = T["activity"]
    ms, me = D["months"][0], D["months"][-1]
    c.add(f'<g{anim("rise", d)}>')
    c.text(32, 42, "Contributions per month", 17, 700, C["text"])
    c.text(32, 62, f"{dt.date(*ms, 1):%b %Y} to {dt.date(*me, 1):%b %Y} · {D['total']:,} in total", 12, 400, C["muted"])
    c.add("</g>")

    bdate, bd = D["busiest"]
    stats = [
        ("CURRENT STREAK", str(D["streak"]), "days" if D["streak"] != 1 else "day", f"longest: {D['longest']} days"),
        ("ACTIVE DAYS", str(D["active"]), f"of {D['span']}", f"{D['active'] / D['span']:.0%} of the year"),
        ("BUSIEST DAY", str(bd), "contributions", fmt_date(bdate)),
    ]
    for i, (lab, val, unit, sub) in enumerate(stats):
        x = 32 + i * 262
        c.add(f'<g{anim("rise", d + 0.3 + i * 0.15)}>')
        c.add(f'<rect x="{x}" y="80" width="3" height="66" rx="1.5" fill="{BRAND}" opacity=".8"/>')
        c.text(x + 14, 92, lab, 10, 400, C["muted"], spacing=1.5)
        c.text(x + 14, 124, val, 26, 700, BRAND)
        c.text(x + 14 + ADV * 26 * len(val) + 8, 124, unit, 12, 400, C["muted"])
        c.text(x + 14, 142, sub, 11, 400, C["muted"])
        c.add("</g>")

    base, top, left, area = 304, 194, 32, W - 64
    mx = max(max(D["per_month"]), 1)
    for frac in (0, .5, 1):
        y = base - (base - top) * frac
        c.add(f'<line x1="{left}" x2="{left + area}" y1="{y:.1f}" y2="{y:.1f}" stroke="{C["border"]}" '
              f'stroke-dasharray="{"0" if frac == 0 else "3 5"}" opacity="{1 if frac == 0 else .6}"/>')
    step, bw = area / 12, 36
    for i, (v, (y_, m_)) in enumerate(zip(D["per_month"], D["months"])):
        bh = max((base - top) * v / mx, 2)
        x = left + i * step + (step - bw) / 2
        hot = v == mx
        bd_ = d + 0.8 + i * 0.07
        c.add(f'<rect x="{x:.1f}" y="{base - bh:.1f}" width="{bw}" height="{bh:.1f}" rx="5" '
              f'fill="{BRAND if hot else "url(#bar)"}"{anim("grow", bd_)}/>')
        c.add(f'<g{anim("rise", bd_ + 0.35)}>')
        c.text(x + bw / 2, base - bh - 7, str(v), 11, 700 if hot else 400, BRAND if hot else C["text"], "middle")
        c.add("</g>")
        c.text(x + bw / 2, base + 18, f"{dt.date(y_, m_, 1):%b}", 11, 700 if hot else 400,
               BRAND if hot else C["muted"], "middle", attrs=anim("fade", bd_ + 0.2))
    return c


def lang_color(r):
    return r.get("color") or LANG_COLORS.get(r.get("language", ""), C["muted"])


def repo_card(slug_idx, full, meta):
    H = 122
    owner, name = full.split("/")
    c = Card("repo-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), W, H, f"Repository {full}",
             meta.get("description") or "")
    d = T["repos"] + slug_idx * 0.4
    c.frame()
    c.add(f'<rect x="1" y="20" width="4" height="82" rx="2" fill="{BRAND}"{anim("growy", d)}/>')
    c.add(f'<g{anim("rise", d + 0.15)}>')
    c.add(f'<path transform="translate(32,28)" d="M1 2.5A2 2 0 0 1 3 .5h10v14H3a1.5 1.5 0 0 0 0 3h10M1 2.5v13" fill="none" '
          f'stroke="{C["muted"]}" stroke-width="1.5" stroke-linejoin="round"/>')
    c.text(58, 42, [(owner + "/", C["muted"], 400), (name, BRAND, 700)], 18)
    desc = meta.get("description") or "No description yet."
    for i, ln in enumerate(textwrap.wrap(desc, 84)[:2]):
        c.text(32, 70 + i * 18, ln, 13, 400, C["text"] if meta.get("description") else C["muted"])
    y, x = 108, 32
    lang = meta.get("language")
    if lang:
        c.add(f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{lang_color(meta)}"/>')
        c.text(x + 16, y, lang, 12, 400, C["muted"])
        x += 16 + ADV * 12 * len(lang) + 22
    pts = []
    for k in range(10):
        r = 6 if k % 2 == 0 else 2.6
        a = k * math.pi / 5
        pts.append(f"{6 + r * math.sin(a):.1f},{6 - r * math.cos(a):.1f}")
    c.add(f'<polygon transform="translate({x},{y - 11})" points="{" ".join(pts)}" fill="none" stroke="{C["muted"]}" '
          f'stroke-width="1.3" stroke-linejoin="round"/>')
    n = str(meta.get("stars", 0))
    c.text(x + 17, y, n, 12, 400, C["muted"])
    x += 17 + ADV * 12 * len(n) + 22
    c.add(f'<g transform="translate({x},{y - 12})" fill="none" stroke="{C["muted"]}" stroke-width="1.3">'
          f'<circle cx="2.5" cy="2.5" r="2"/><circle cx="9.5" cy="2.5" r="2"/><circle cx="6" cy="11" r="2"/>'
          f'<path d="M2.5 4.5V6.5H9.5V4.5M6 6.5V9"/></g>')
    c.text(x + 17, y, str(meta.get("forks", 0)), 12, 400, C["muted"])
    c.add(f'<path d="M{W - 50} 34 L{W - 38} 22 M{W - 48} 22 H{W - 38} V32" fill="none" stroke="{C["muted"]}" '
          f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></g>')
    return c


def graph(D, kind="github"):
    """Contribution heatmap. kind='github' is GitHub's own calendar, 'all' counts commits on every branch."""
    ab = D["all_branch"]
    if kind == "combined":
        name, title, tone, d = "contributions-combined", "Combined activity", CFG.get("combined_color", "#E879F9"), T["graph3"]
        days = {k: D["days"].get(k, 0) + D["extra"].get(k, 0) for k in set(D["days"]) | set(D["extra"])}
        total, extra_total = sum(days.values()), sum(D["extra"].values())
        sub = "GitHub's count plus the commits it ignores, because they sit on branches that are not the default"
        right = [(f"{total:,}", tone, 700), (f" in total · {D['total']:,} from GitHub + {extra_total:,} extra", C["muted"], 400)]
    elif kind == "github":
        name, title, tone, d = "contributions", "Contribution graph", BRAND, T["graph"]
        days, total = D["days"], D["total"]
        sub = "As GitHub counts it: default-branch commits, pull requests, issues and reviews"
        right = [(f"{total:,}", tone, 700), (" contributions in the last year", C["muted"], 400)]
    else:
        name, title, tone, d = "contributions-all", "All-branch commits", ACCENT, T["graph2"]
        days = ab["days"] if ab else {}
        total = ab["total"] if ab else 0
        sub = "Every commit you wrote on any branch, counted once, including unmerged work"
        right = ([(f"{total:,}", tone, 700), (f" commits · {ab['branches']} branches · {len(ab['repos'])} repos", C["muted"], 400)]
                 if ab else [("waiting for the first refresh", C["muted"], 400)])
    H = 244
    c = Card(name, W, H, title, f"{total} in the last year")
    c.frame()
    levels = [C["panel"]] + [mix(C["panel"], tone, t) for t in (0.3, 0.52, 0.76, 1.0)]
    c.add("<style>" + "".join(f".l{i}{{fill:{col}}}" for i, col in enumerate(levels)) + "</style>")
    c.add(f'<g{anim("rise", d)}>')
    c.text(32, 42, title, 17, 700, C["text"])
    c.text(W - 32, 42, right, 12, anchor="end")
    c.text(32, 62, sub, 11, 400, C["muted"])
    c.add("</g>")

    first = D["last"] - dt.timedelta(days=364)
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)       # Sunday on/before
    nz = sorted(v for v in days.values() if v > 0)
    q = [nz[int(len(nz) * f)] for f in (0, .25, .5, .75)] if nz else [1, 2, 3, 4]

    def level(v):
        return 0 if v == 0 else 1 + sum(v > t for t in q[1:])

    x0, y0, pitch, cell = 62, 106, 14, 11
    cols = (D["last"] - start).days // 7 + 1
    prev_m, last_lab = None, -9
    for col in range(cols):
        sun = start + dt.timedelta(days=col * 7)
        if sun.month != prev_m and col - last_lab >= 3 and sun >= first:
            c.text(x0 + col * pitch, 92, f"{sun:%b}", 11, 400, C["muted"], attrs=anim("fade", d + 0.4))
            last_lab = col
        prev_m = sun.month
        for row in range(7):
            day = sun + dt.timedelta(days=row)
            if day > D["last"] or day < first:
                continue
            delay = d + 0.35 + col * 0.02 + row * 0.012
            cls = f"l{level(days.get(day, 0))}" + ("" if STATIC else " pop")
            sty = "" if STATIC else f' style="animation-delay:{delay:.2f}s"'
            c.add(f'<rect x="{x0 + col * pitch}" y="{y0 + row * pitch}" width="{cell}" height="{cell}" rx="2.5" '
                  f'class="{cls}"{sty}/>')
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(32, y0 + row * pitch + 9, lab, 10, 400, C["muted"])
    ly = H - 22
    lx = W - 32 - (5 * 14 + 36 + 36)
    c.add(f'<g{anim("fade", d + 1.4)}>')
    c.text(lx, ly + 9, "Less", 10, 400, C["muted"])
    for i in range(5):
        c.add(f'<rect x="{lx + 36 + i * 14}" y="{ly}" width="{cell}" height="{cell}" rx="2.5" class="l{i}"/>')
    c.text(lx + 36 + 5 * 14 + 4, ly + 9, "More", 10, 400, C["muted"])
    c.add("</g>")
    return c


# ------------------------------------------------------------------------ main
def main():
    D = load_data()
    out = ROOT / CFG["out"]
    out.mkdir(exist_ok=True)
    by_name = {r["nameWithOwner"].lower(): r for r in D["repos"]}

    cards = [hero()]
    cards += [tile(l, i) for i, l in enumerate(CFG["links"])]
    cards.append(stack())
    cards.append(activity(D))
    for i, full in enumerate(CFG["featured"]):
        cards.append(repo_card(i, full, by_name.get(full.lower(), {})))
    cards.append(graph(D, "github"))
    cards.append(graph(D, "all"))
    cards.append(graph(D, "combined"))

    for card in cards:
        (out / f"{card.name}.svg").write_text(card.svg(), encoding="utf-8")
        print(f"{'static ' if STATIC else ''}{card.name}.svg")
    write_preview(out, cards)


def write_preview(out, cards):
    """cards/_preview.html mirrors the README layout, for screenshots."""
    img = lambda c, w="100%": f'<img src="{c.name}.svg" width="{w}" style="display:block">'
    tiles = "".join(img(c, "32.5%") for c in cards if c.name.startswith("tile-"))
    full = "".join(f'<div style="margin-top:8px">{img(c)}</div>' for c in cards
                   if c.name != "hero" and not c.name.startswith("tile-"))
    html = ('<!doctype html><meta charset="utf-8"><body style="margin:0;background:#0d1117">'
            '<div style="width:830px;margin:0 auto;padding:24px 0">' + img(cards[0]) +
            f'<div style="display:flex;justify-content:space-between;margin-top:8px">{tiles}</div>{full}</div>')
    (out / "_preview.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
