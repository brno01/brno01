#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools==4.69.1"]
# ///
"""Profile README generator: the 32 SVG art files and README.md.

    tools/build.py assets --snapshot FILE --out DIR   write the SVG files (flat) into DIR
    tools/build.py readme [--check]                   write README.md (--check only compares)

Deterministic: the same snapshot and content give the same bytes. The date comes from
snapshot["today"]; the generator never reads the clock.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import svgkit as K  # noqa: E402

CONTENT_PATH = HERE / "content.json"
README_PATH = ROOT / "README.md"

PALETTE = {
    "dark": {"bg": "#0d1117", "ink": "#f0f6fc", "body": "#d1d7e0", "muted": "#9198a1", "strand": "#d8d2c4", "accent": "#f2a33a"},
    "light": {"bg": "#ffffff", "ink": "#1f2328", "body": "#3b424b", "muted": "#59636e", "strand": "#3d444d", "accent": "#c2500a"},
}
ACTIVE_DAYS = 14


class BuildError(Exception):
    """A problem the user must fix; the CLI prints it and exits with 1."""


def load_content(path: Path = CONTENT_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BuildError(f"não consegui ler o conteúdo {path}: {e}") from e


def load_snapshot(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BuildError(f"nao consegui ler o snapshot {path}: {e}") from e


# ------------------------------------------------------------------ validation

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
WEEKS = 26


def _iso_date(value, where: str) -> None:
    ok = isinstance(value, str) and ISO_DATE.fullmatch(value) is not None
    if ok:
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            ok = False
    if not ok:
        raise BuildError(f"snapshot: {where} deve ser uma data AAAA-MM-DD válida, veio {value!r}")


def _count(value, where: str) -> None:
    # bool is an int in Python; a flag is not a count
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BuildError(f"snapshot: {where} deve ser um inteiro maior ou igual a 0, veio {value!r}")


def validate_snapshot(snap, content: dict) -> None:
    """Check the shape of the snapshot before anything is drawn; raise BuildError naming the bad key."""
    if not isinstance(snap, dict):
        raise BuildError("snapshot: o JSON precisa ser um objeto")
    for key in ("today", "contributions_year", "weeks_26", "repos"):
        if key not in snap:
            raise BuildError(f"snapshot: falta a chave {key!r}")
    _iso_date(snap["today"], "today")
    _count(snap["contributions_year"], "contributions_year")
    weeks = snap["weeks_26"]
    if not isinstance(weeks, list) or len(weeks) != WEEKS:
        n = len(weeks) if isinstance(weeks, list) else type(weeks).__name__
        raise BuildError(f"snapshot: weeks_26 deve ser uma lista de exatamente {WEEKS} semanas, veio {n}")
    for i, w in enumerate(weeks):
        if not isinstance(w, dict) or "start" not in w or "count" not in w:
            raise BuildError(f"snapshot: weeks_26[{i}] deve ser um objeto com start e count")
        _iso_date(w["start"], f"weeks_26[{i}].start")
        _count(w["count"], f"weeks_26[{i}].count")
    repos = snap["repos"]
    if not isinstance(repos, dict):
        raise BuildError("snapshot: repos deve ser um objeto")
    for p in content["projects"]:
        r = repos.get(p["repo"])
        if r is None:
            raise BuildError(f'projeto {p["repo"]} não está entre os repositórios públicos de {content["login"]}')
        if not isinstance(r, dict) or "pushed_at" not in r:
            raise BuildError(f'snapshot: repos[{p["repo"]!r}] deve ser um objeto com pushed_at')
        _iso_date(r["pushed_at"], f'repos[{p["repo"]!r}].pushed_at')


_STR, _LIST_STR = "str", "list[str]"
CONTENT_SCHEMA = {
    "name": _STR, "tagline": _STR, "tagline_parts": _LIST_STR, "lede": _STR, "about": _LIST_STR,
    "also_private": _LIST_STR, "login": _STR, "assets_base": _STR, "footer": _STR,
    "contacts": [{"label": _STR, "url": _STR}],
    "projects": [{"slug": _STR, "url": _STR, "title": _STR, "kind": _STR, "desc": _STR, "desc_short": _STR,
                  "stack": _LIST_STR, "alt": _STR, "repo": _STR}],
    "studies": [{"title": _STR, "url": _STR, "note": _STR}],
    "stack": [{"group": _STR, "items": _LIST_STR}],
    "labels": {k: _STR for k in ("updated", "about", "projects", "stack", "activity_heading", "also_private",
                                 "studies", "hub", "active_line", "push", "contrib_line")},
    "alt": {"hero": _STR, "activity": _STR},
    "english": {"summary": _STR, "heading": _STR, "lede": _STR, "about": _LIST_STR, "closing": _STR},
}


def _check_shape(value, schema, path: str) -> None:
    if schema == _STR:
        if not isinstance(value, str):
            raise BuildError(f"content.json: {path} deve ser texto")
    elif schema == _LIST_STR:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise BuildError(f"content.json: {path} deve ser uma lista de textos")
    elif isinstance(schema, list):
        if not isinstance(value, list):
            raise BuildError(f"content.json: {path} deve ser uma lista")
        for i, item in enumerate(value):
            _check_shape(item, schema[0], f"{path}[{i}]")
    else:
        if not isinstance(value, dict):
            raise BuildError(f"content.json: {path} deve ser um objeto")
        for key, sub in schema.items():
            if key not in value:
                raise BuildError(f"content.json: falta a chave {path}.{key}" if path else f"content.json: falta a chave {key}")
            _check_shape(value[key], sub, f"{path}.{key}" if path else key)


def validate_content(content) -> None:
    """Check that every key the generator reads exists in content.json, with the right type."""
    _check_shape(content, CONTENT_SCHEMA, "")
    if len(content["tagline_parts"]) != 3:
        raise BuildError("content.json: tagline_parts deve ter exatamente 3 itens")


class Ctx:
    """Inputs and results of one generation run."""

    def __init__(self, snap: dict, content: dict):
        self.snap, self.copy = snap, content
        self.lab = content["labels"]
        self.projects = content["projects"]
        today = snap["today"]
        self.active = {p["repo"]: self.days_ago(snap["repos"][p["repo"]]["pushed_at"], today) <= ACTIVE_DAYS
                       for p in self.projects}
        self.n_active = sum(self.active.values())
        self.updated = f'{self.lab["updated"]} {K.fmt_date_pt(today)}'
        self.files: dict[str, bytes] = {}

    @staticmethod
    def days_ago(iso: str, today: str) -> int:
        days = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(iso[:10])).days
        # a push dated after "today" (runner clock slightly behind) counts as today, never as negative days
        return max(0, days)

    def emit(self, name: str, doc) -> None:
        self.files[name] = doc.to_bytes(name)


def check_projects(snap: dict, content: dict) -> None:
    repos = snap["repos"]
    for p in content["projects"]:
        if p["repo"] not in repos:
            raise BuildError(f'projeto {p["repo"]} não está entre os repositórios públicos de {content["login"]}')
        # same rule as the file-name check of the publish job in the workflow
        if not SLUG.fullmatch(p["slug"]):
            raise BuildError(f'projeto {p["repo"]}: slug {p["slug"]!r} inválido (use [a-z0-9][a-z0-9-]*)')
    nodes = set(HERO["desktop"]["nodes"])
    if {p["repo"] for p in content["projects"]} != nodes:
        raise BuildError("os projetos de content.json diferem dos nós do desenho do hero: " + ", ".join(sorted(nodes)))


def f(x):
    return K.num(x)


# ------------------------------------------------------------------ hero

HERO = {
    "desktop": dict(
        W=846, H=300, name_size=52, name_track=-1, name_y=132, tag_size=12, tag_track=1.6, tag_y=164,
        text_max=372, state_y=208, state_size=11.5, hub=(636, 150), lab=11, gx=(400, 846), grid=22,
        nodes={"sinapses": (640, 36, "up"), "kindle-monitor": (498, 72, "up"), "galaxy-s10": (776, 86, "up"),
               ".github": (800, 196, "down"), "nomad": (690, 258, "down"), "tailnet-dns-failover": (512, 228, "down")},
        k=(18, 26), hub_label=("end", -26, 4), x_lo=404, x_hi=846),
    "mobile": dict(
        W=308, H=380, name_size=36, name_track=-0.6, name_y=40, tag_size=10.5, tag_track=1.1, tag_y=62,
        text_max=308, state_y=364, state_size=10, hub=(154, 212), lab=10, gx=(0, 308), grid=22,
        nodes={"sinapses": (154, 106, "up"), "kindle-monitor": (54, 152, "up"), "galaxy-s10": (256, 152, "up"),
               ".github": (280, 236, "down"), "nomad": (236, 300, "down"), "tailnet-dns-failover": (72, 290, "down")},
        k=(10, 16), hub_label=("middle", 0, 38), x_lo=0, x_hi=308),
}
EDGES = [("hub", "sinapses"), ("hub", "kindle-monitor"), ("hub", "galaxy-s10"), ("hub", ".github"),
         ("hub", "nomad"), ("hub", "tailnet-dns-failover"),
         ("nomad", "tailnet-dns-failover"), ("kindle-monitor", "galaxy-s10")]
# per-edge duration (s) and negative delay; odd edges reversed
DURS = [3.4, 4.0, 3.1, 4.4, 3.7, 2.9, 4.2, 3.3]
DELAYS = [-0.4, -2.1, -1.2, -3.3, -0.9, -2.6, -1.7, -3.0]
# (device, edge index) -> -1 flips the bow of that edge; chosen after looking at renders
EDGE_SIGN = {("desktop", 6): -1, ("desktop", 2): -1}


def bez(p0, c1, c2, p3, t):
    u = 1 - t
    return (u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p3[1])


def hero(ctx, device, theme):
    c = PALETTE[theme]
    g = HERO[device]
    mob = device == "mobile"
    W, H = g["W"], g["H"]
    d = K.Doc(W, H, ctx.copy["alt"]["hero"])
    bold = K.font("bricolage-800")
    mono5 = K.font("plexmono-500")
    mono4 = K.font("plexmono-400")
    # ---- text block
    size = g["name_size"]
    while bold.measure(ctx.copy["name"], size, g["name_track"]) > g["text_max"]:
        size -= 1
    d.add(d.text(0, g["name_y"], ctx.copy["name"], bold, size, c["ink"], tracking=g["name_track"]))
    tag = ctx.copy["tagline"].upper()
    parts = [p.upper() for p in ctx.copy["tagline_parts"]]
    ts, tt = g["tag_size"], g["tag_track"]
    tx = 0 if mob else 2
    if mono5.measure(tag, ts, tt) + tx <= (308 if mob else g["text_max"]):
        d.add(d.text(tx, g["tag_y"], tag, mono5, ts, c["muted"], tracking=tt))
    else:
        d.add(d.text(tx, g["tag_y"], " · ".join(parts[:2]) + " ·", mono5, ts, c["muted"], tracking=tt))
        d.add(d.text(tx, g["tag_y"] + 16, parts[2], mono5, ts, c["muted"], tracking=tt))
    ss = g["state_size"]
    line = ctx.lab["active_line"].replace("{n}", str(ctx.n_active))
    if mono4.measure(line, ss) + (14 if mob else 18) > (290 if mob else g["text_max"]):
        line = f"{ctx.n_active} de {len(ctx.projects)} ativos em {ACTIVE_DAYS} dias"
    sy = g["state_y"]
    d.add(f'<circle class="breathe-dot" cx="3.5" cy="{f(sy - 4)}" r="3.5" fill="{c["accent"]}"/>' if not mob else
          f'<circle class="breathe-dot" cx="3.5" cy="{f(sy - 3.5)}" r="3.5" fill="{c["accent"]}"/>')
    d.add(d.text(14 if mob else 18, sy, line, mono4, ss, c["muted"]))
    if not mob:
        d.add(d.text(18, 228, ctx.updated, mono4, 11, c["muted"]))

    # ---- graph
    hx, hy = g["hub"]
    gx0, gx1 = g["gx"]
    gs = g["grid"]
    d.defs(f'<pattern id="dots" width="{gs}" height="{gs}" patternUnits="userSpaceOnUse"><circle cx="{gs / 2}" cy="{gs / 2}" r="0.9" fill="{c["strand"]}"/></pattern>')
    rx = (gx1 - gx0) / 2
    d.defs(f'<radialGradient id="fade" gradientUnits="userSpaceOnUse" cx="{f(hx)}" cy="{f(hy)}" r="{f(max(rx, H / 2) * 1.0)}">'
           '<stop offset="0" stop-color="#fff"/><stop offset=".55" stop-color="#fff" stop-opacity=".85"/><stop offset="1" stop-color="#000"/></radialGradient>')
    d.defs(f'<mask id="gmask"><rect x="{gx0}" y="0" width="{gx1 - gx0}" height="{H}" fill="url(#fade)"/></mask>')
    y0 = 84 if mob else 0
    y1 = 340 if mob else H
    d.add(f'<g mask="url(#gmask)" opacity=".16"><rect x="{gx0}" y="{y0}" width="{gx1 - gx0}" height="{y1 - y0}" fill="url(#dots)"/></g>')

    pos = {"hub": (hx, hy, 14)}
    for repo, (x, y, side) in g["nodes"].items():
        pos[repo] = (x, y, 9)
    labels = []  # (repo, x0, x1, ytop, ybot)
    lab = g["lab"]
    for repo, (x, y, side) in g["nodes"].items():
        w = mono5.measure(repo, lab)
        base = y - 9 - 9 if side == "up" else y + 9 + 9 + mono5.cap_height(lab)
        labels.append((repo, x - w / 2, x + w / 2, base - mono5.cap_height(lab) - 1, base + 2.5, base, side))
        if x - w / 2 < g["x_lo"] or x + w / 2 > g["x_hi"]:
            raise BuildError(f"hero-{device}: o rótulo {repo} sai da faixa do desenho")
    anc, dx, dy = g["hub_label"]
    hw = mono5.measure(ctx.lab["hub"], lab)
    hlx = hx + dx
    hly = hy + dy
    hx0 = hlx - hw if anc == "end" else (hlx - hw / 2 if anc == "middle" else hlx)
    labels.append(("hub", hx0, hx0 + hw, hly - mono5.cap_height(lab) - 1, hly + 2.5, hly, anc))

    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            if a[1] - 2 <= b[2] and b[1] - 2 <= a[2] and a[3] - 2 <= b[4] and b[3] - 2 <= a[4]:
                raise BuildError(f"hero-{device}: o rótulo {a[0]} colide com o rótulo {b[0]}")

    paths = []
    kmin, kmax = g["k"]
    for i, (a, b) in enumerate(EDGES):
        ax, ay, ar = pos[a]
        bx, by, br = pos[b]
        L = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / L, (by - ay) / L
        p0 = (ax + ux * ar, ay + uy * ar)
        p3 = (bx - ux * br, by - uy * br)
        nx, ny = -uy, ux
        k = (kmin + (kmax - kmin) * ((i * 37) % 9) / 8) * (1 if i % 2 == 0 else -1)
        k *= EDGE_SIGN.get((device, i), 1)
        c1 = (p0[0] + (p3[0] - p0[0]) * .35 + nx * k, p0[1] + (p3[1] - p0[1]) * .35 + ny * k)
        c2 = (p0[0] + (p3[0] - p0[0]) * .65 - nx * k, p0[1] + (p3[1] - p0[1]) * .65 - ny * k)
        paths.append((a, b, p0, c1, c2, p3))
        # collision check vs labels
        for t in [j / 60 for j in range(61)]:
            px, py = bez(p0, c1, c2, p3, t)
            for (nm, lx0, lx1, lt, lb, _, _) in labels:
                if lx0 - 2 <= px <= lx1 + 2 and lt - 2 <= py <= lb + 2:
                    raise BuildError(f"hero-{device}: o fio {a}-{b} cruza o rótulo {nm} em ({px:.0f},{py:.0f})")

    css = [".breathe-dot{animation:dotpulse 2.4s ease-in-out infinite}",
           "@keyframes dotpulse{0%,100%{opacity:.45}50%{opacity:1}}",
           ".breath{transform-box:fill-box;transform-origin:center;animation:breath 3.6s ease-out infinite}",
           "@keyframes breath{0%{transform:scale(1);opacity:.5}100%{transform:scale(1.571);opacity:0}}",
           ".pulse{stroke-dasharray:7 200;stroke-dashoffset:7;animation-name:travel;animation-timing-function:linear;animation-iteration-count:infinite}",
           ".halo{stroke-dasharray:7 200;stroke-dashoffset:7;animation-name:travel;animation-timing-function:linear;animation-iteration-count:infinite}",
           "@keyframes travel{from{stroke-dashoffset:7}to{stroke-dashoffset:-100}}"]
    for line_css in css:
        d.css(line_css)
    d.css(K.REDUCED_MOTION_CSS)

    wires, pulses = [], []
    for i, (a, b, p0, c1, c2, p3) in enumerate(paths):
        dd = f"M{f(p0[0])} {f(p0[1])}C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p3[0])} {f(p3[1])}"
        act = ctx.active[b] and (a == "hub" or ctx.active[a])
        dash = "" if act else ' stroke-dasharray="2 5"'
        wires.append(f'<path d="{dd}" fill="none" stroke="{c["strand"]}" stroke-width="1.25" stroke-opacity=".55" stroke-linecap="round"{dash}/>')
        if act:
            st = f'animation-duration:{DURS[i]}s;animation-delay:{DELAYS[i]}s' + (";animation-direction:reverse" if i % 2 else "")
            pulses.append(f'<path class="halo" d="{dd}" pathLength="100" fill="none" stroke="{c["accent"]}" stroke-width="5" stroke-opacity=".18" stroke-linecap="round" style="{st}"/>')
            pulses.append(f'<path class="pulse" d="{dd}" pathLength="100" fill="none" stroke="{c["accent"]}" stroke-width="2" stroke-linecap="round" style="{st}"/>')
    for w in wires:
        d.add(w)
    for p in pulses:
        d.add(p)
    # hub
    d.add(f'<circle class="breath" cx="{hx}" cy="{hy}" r="14" fill="none" stroke="{c["ink"]}" stroke-width="1.2"/>')
    d.add(f'<circle cx="{hx}" cy="{hy}" r="14" fill="none" stroke="{c["ink"]}" stroke-width="1.5"/>')
    d.add(f'<circle cx="{hx}" cy="{hy}" r="5" fill="{c["ink"]}"/>')
    for repo, (x, y, side) in g["nodes"].items():
        d.add(f'<circle cx="{x}" cy="{y}" r="9" fill="none" stroke="{c["strand"]}" stroke-width="1" stroke-opacity=".45"/>')
        if ctx.active[repo]:
            d.add(f'<circle cx="{x}" cy="{y}" r="5" fill="{c["accent"]}"/>')
        else:
            d.add(f'<circle cx="{x}" cy="{y}" r="4.5" fill="none" stroke="{c["strand"]}" stroke-width="1.25"/>')
    for (nm, lx0, lx1, lt, lb, base, side) in labels:
        if nm == "hub":
            d.add(d.text(hlx, hly, ctx.lab["hub"], mono5, lab, c["muted"], anchor=anc))
        else:
            d.add(d.text((lx0 + lx1) / 2, base, nm, mono5, lab, c["ink"], anchor="middle"))
    ctx.emit(f"hero-{device}-{theme}.svg", d)


# ------------------------------------------------------------------ project strips


def strip(ctx, device, theme, idx, p):
    c = PALETTE[theme]
    mob = device == "mobile"
    n = len(ctx.projects)
    if mob:
        dfont = K.font("bricolage-400")
        dlines = dfont.wrap(p["desc_short"], 13.5, 278)
        if len(dlines) > 2 or any(dfont.measure(ln, 13.5) > 278 for ln in dlines):
            raise BuildError(f'projeto {p["repo"]}: desc_short não cabe em 2 linhas no celular '
                             "(texto longo demais ou palavra mais larga que a caixa)")
        foot_y = 50 + 18 * (len(dlines) - 1) + 26
        W, H = 308, foot_y + 16
    else:
        W, H = 846, 88
    first, last = idx == 0, idx == n - 1
    act = ctx.active[p["repo"]]
    d = K.Doc(W, H, p["alt"])
    t6 = K.font("bricolage-600")
    b4 = K.font("bricolage-400")
    m5 = K.font("plexmono-500")
    m4 = K.font("plexmono-400")
    sx = 8.5 if mob else 12.5
    ny = 22 if mob else 44
    ya = ny if first else 0
    yb = ny if last else H
    spine = f"M{sx} {ya}V{yb}"
    d.css(".sp{stroke-dasharray:14 200;stroke-dashoffset:14;animation:drop 7.2s linear infinite}"
          "@keyframes drop{0%{stroke-dashoffset:14}16.66%{stroke-dashoffset:-100}100%{stroke-dashoffset:-100}}")
    d.css(K.REDUCED_MOTION_CSS)
    d.add(f'<path d="{spine}" fill="none" stroke="{c["strand"]}" stroke-width="1.25" stroke-opacity=".5"/>')
    if not mob:
        d.add(f'<path d="M{sx + 10} {ny}H40" fill="none" stroke="{c["strand"]}" stroke-width="1.25" stroke-opacity=".5"/>')
    d.add(f'<path class="sp" d="{spine}" pathLength="100" fill="none" stroke="{c["accent"]}" stroke-width="2" stroke-linecap="round" style="animation-delay:{f(idx * 1.2)}s"/>')
    d.add(f'<circle cx="{sx}" cy="{ny}" r="9" fill="none" stroke="{c["strand"]}" stroke-width="1" stroke-opacity=".45"/>')
    if act:
        d.add(f'<circle cx="{sx}" cy="{ny}" r="5" fill="{c["accent"]}"/>')
    else:
        d.add(f'<circle cx="{sx}" cy="{ny}" r="4.5" fill="none" stroke="{c["strand"]}" stroke-width="1.25"/>')
    kind = p["kind"].upper()
    stack = " · ".join(p["stack"]).upper()
    date = K.fmt_day_month_pt(ctx.snap["repos"][p["repo"]]["pushed_at"])
    if not mob:
        d.add(d.text(52, 38, p["title"], t6, 21, c["ink"]))
        tw = t6.measure(p["title"], 21)
        sw = m5.measure(stack, 11, 1)
        if 52 + tw + 12 + m5.measure(kind, 11, 1.2) + 16 > 846 - sw + 1:
            raise BuildError(f'projeto {p["repo"]}: título e tipo encostam na stack na faixa do desktop')
        d.add(d.text(52 + tw + 12, 37, kind, m5, 11, c["muted"], tracking=1.2))
        desc = b4.fit(p["desc"], 14.5, 588)
        d.add(d.text(52, 63, desc, b4, 14.5, c["body"]))
        d.add(d.text(846 - sw + 1, 37, stack, m5, 11, c["muted"], tracking=1))
        ptxt = f'{ctx.lab["push"]} · {date}'
        pw = m4.measure(ptxt, 11)
        d.add(d.text(846 - pw, 63, ptxt, m4, 11, c["muted"]))
        if act:
            d.add(f'<circle cx="{f(846 - pw - 9)}" cy="59.5" r="3" fill="{c["accent"]}"/>')
    else:
        d.add(d.text(28, 27, p["title"], t6, 17, c["ink"]))
        tw = t6.measure(p["title"], 17)
        kw = m5.measure(kind, 10, 1)
        if 308 - kw - (28 + tw) >= 10:
            d.add(d.text(308 - kw + 1, 26, kind, m5, 10, c["muted"], tracking=1))
        for j, ln in enumerate(dlines):
            d.add(d.text(28, 50 + 18 * j, ln, b4, 13.5, c["body"]))
        dw = m4.measure(date, 10)
        smax = 308 - 28 - dw - 12
        trk = 1 if m5.measure(stack, 10, 1) <= smax else 0.5
        st = m5.fit(stack, 10, smax, trk)
        d.add(d.text(28, foot_y, st, m5, 10, c["muted"], tracking=trk))
        d.add(d.text(308 - dw, foot_y, date, m4, 10, c["muted"]))
    ctx.emit(f'proj-{p["slug"]}-{device}-{theme}.svg', d)


# ------------------------------------------------------------------ activity


def monotone(pts):
    """Fritsch-Carlson monotone cubic through pts; returns SVG path d (never overshoots data)."""
    n = len(pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    sl = [(ys[i + 1] - ys[i]) / dx[i] for i in range(n - 1)]
    m = [sl[0]] + [0 if sl[i - 1] * sl[i] <= 0 else (sl[i - 1] + sl[i]) / 2 for i in range(1, n - 1)] + [sl[-1]]
    for i in range(n - 1):
        if sl[i] == 0:
            m[i] = m[i + 1] = 0
        else:
            a, b = m[i] / sl[i], m[i + 1] / sl[i]
            s = a * a + b * b
            if s > 9:
                t = 3 / math.sqrt(s)
                m[i], m[i + 1] = t * a * sl[i], t * b * sl[i]
    out = f"M{f(xs[0])} {f(ys[0])}"
    for i in range(n - 1):
        h = dx[i] / 3
        out += f"C{f(xs[i] + h)} {f(ys[i] + m[i] * h)} {f(xs[i + 1] - h)} {f(ys[i + 1] - m[i + 1] * h)} {f(xs[i + 1])} {f(ys[i + 1])}"
    return out


def activity(ctx, device, theme):
    c = PALETTE[theme]
    mob = device == "mobile"
    W, H = (308, 156) if mob else (846, 132)
    d = K.Doc(W, H, ctx.copy["alt"]["activity"])
    b8 = K.font("bricolage-800")
    m5 = K.font("plexmono-500")
    m4 = K.font("plexmono-400")
    total = K.fmt_int_pt(ctx.snap["contributions_year"])
    cl = ctx.lab["contrib_line"].upper()
    weeks = ctx.snap["weeks_26"]
    counts = [w["count"] for w in weeks]
    if mob:
        x0, x1, top, ymax, base_y, ty, my = 6, 302, 122, 56, 126.5, 22, 146
        d.add(d.text(0, 22, total, b8, 24, c["ink"]))
        d.add(d.text(0, 40, cl, m5, 10, c["muted"], tracking=1))
        if m5.measure(cl, 10, 1) + m4.measure(ctx.updated, 10) + 12 <= 308:
            d.add(d.text(308 - m4.measure(ctx.updated, 10), 40, ctx.updated, m4, 10, c["muted"]))
        fs = 10
    else:
        x0, x1, top, ymax, base_y, ty, my = 16, 830, 100, 62, 104.5, 26, 124
        d.add(d.text(0, 26, total, b8, 28, c["ink"]))
        d.add(d.text(b8.measure(total, 28) + 10, 26, cl, m5, 11, c["muted"], tracking=1))
        d.add(d.text(846 - m4.measure(ctx.updated, 11), 26, ctx.updated, m4, 11, c["muted"]))
        fs = 11
    ys = [top - v for v in K.sqrt_scale(counts, ymax)]
    n = len(counts)
    xs = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]
    pts = list(zip(xs, ys))
    curve = monotone(pts)
    d.css(".br{transform-box:fill-box;transform-origin:center;animation:br 3.6s ease-out infinite}"
          "@keyframes br{0%{transform:scale(1);opacity:.55}100%{transform:scale(2.2);opacity:0}}"
          ".tr{stroke-dasharray:4 200;stroke-dashoffset:4;animation:tr 6s linear infinite}"
          "@keyframes tr{from{stroke-dashoffset:4}to{stroke-dashoffset:-100}}")
    d.css(K.REDUCED_MOTION_CSS)
    d.add(f'<path d="M{f(x0 - 4 if not mob else 0)} {base_y}H{f(x1 + 4 if not mob else 308)}" stroke="{c["strand"]}" stroke-width="1" stroke-opacity=".3" fill="none"/>')
    d.add(f'<path d="{curve}L{f(xs[-1])} {base_y}L{f(xs[0])} {base_y}Z" fill="{c["strand"]}" fill-opacity=".06"/>')
    d.add(f'<path d="{curve}" fill="none" stroke="{c["strand"]}" stroke-width="1.5" stroke-opacity=".85" stroke-linejoin="round"/>')
    d.add(f'<path class="tr" d="{curve}" pathLength="100" fill="none" stroke="{c["accent"]}" stroke-width="2" stroke-linecap="round"/>')
    for i, (x, y) in enumerate(pts):
        if counts[i] > 0 and i != n - 1:
            d.add(f'<circle cx="{f(x)}" cy="{f(y)}" r="2" fill="{c["strand"]}"/>')
    d.add(f'<circle class="br" cx="{f(xs[-1])}" cy="{f(ys[-1])}" r="4.5" fill="none" stroke="{c["accent"]}" stroke-width="1.2"/>')
    d.add(f'<circle cx="{f(xs[-1])}" cy="{f(ys[-1])}" r="4.5" fill="{c["accent"]}"/>')
    # month axis: first week whose Thursday falls in a new month
    seen = None
    shown = 0
    for i, w in enumerate(weeks):
        dt = datetime.date.fromisoformat(w["start"]) + datetime.timedelta(days=3)
        if dt.month != seen:
            seen = dt.month
            if (not mob) or shown % 2 == 0:
                lbl = K.MONTHS_PT[dt.month - 1]
                lw = m4.measure(lbl, fs)
                d.add(d.text(max(xs[i], lw / 2), my, lbl, m4, fs, c["muted"], anchor="middle"))
            shown += 1
    ctx.emit(f"activity-{device}-{theme}.svg", d)


# ------------------------------------------------------------------ run

def generate(snap: dict, content: dict) -> Ctx:
    """Build every SVG in memory (name -> bytes). Raises BuildError; writes nothing."""
    validate_content(content)
    validate_snapshot(snap, content)
    check_projects(snap, content)
    ctx = Ctx(snap, content)
    try:
        for dev in ("desktop", "mobile"):
            for th in ("dark", "light"):
                hero(ctx, dev, th)
                for i, p in enumerate(ctx.projects):
                    strip(ctx, dev, th, i, p)
                activity(ctx, dev, th)
    except ValueError as e:  # svgkit: character without a glyph, SVG too big or malformed
        raise BuildError(str(e)) from e
    return ctx


def write_assets(files: dict[str, bytes], out: Path) -> None:
    """Write to a temporary directory next to out and move the files in only when all are written."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".build-", dir=out.parent))
    try:
        for name, data in files.items():
            (tmp / name).write_bytes(data)
        out.mkdir(exist_ok=True)
        for name in files:
            os.replace(tmp / name, out / name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ README


def render_readme(content: dict) -> str:
    e = K.esc
    lab = content["labels"]
    base = content["assets_base"].rstrip("/")

    def pic(name: str, alt: str) -> str:
        return K.picture(name, alt, base)

    blocks = [
        "<!-- Arquivo gerado por tools/build.py a partir de tools/content.json. "
        "Não edite à mão: edite o JSON e rode `uv run tools/build.py readme`. -->",
        "<p>\n" + pic("hero", content["alt"]["hero"]) + "\n</p>",
        e(content["lede"]),
        "<p>" + " ".join(f'<a href="{e(x["url"])}"><kbd>{e(x["label"])} ↗</kbd></a>' for x in content["contacts"]) + "</p>",
        f'## {lab["about"]}',
    ]
    blocks += [e(t) for t in content["about"]]
    blocks.append(f'## {lab["projects"]}')
    blocks.append("<p>\n" + "\n".join(
        f'<a href="{e(p["url"])}">\n{pic("proj-" + p["slug"], p["alt"])}\n</a>' for p in content["projects"]) + "\n</p>")
    blocks.append(f'**{lab["also_private"]}**')
    blocks.append("\n".join(f"- {e(t)}" for t in content["also_private"]))
    blocks.append(f'**{lab["studies"]}:** ' + ", ".join(
        f'[{e(s["title"])}]({s["url"]}) ({e(s["note"])})' for s in content["studies"]))
    blocks.append(f'## {lab["stack"]}')
    for gr in content["stack"]:
        blocks.append(f'**{e(gr["group"])}**<br>\n' + " ".join(f"<kbd>{e(i)}</kbd>" for i in gr["items"]))
    blocks.append(f'## {lab["activity_heading"]}')
    blocks.append("<p>\n" + pic("activity", content["alt"]["activity"]) + "\n</p>")
    en = content["english"]
    blocks.append(
        f'<details>\n<summary><b>{e(en["summary"])}</b></summary>\n\n### {e(en["heading"])}\n\n{e(en["lede"])}\n\n'
        + "\n\n".join(e(t) for t in en["about"]) + f'\n\n{e(en["closing"])}\n\n</details>')
    footer = e(content["footer"]).replace("tools/build.py", '<a href="tools/build.py">tools/build.py</a>')
    blocks.append(f"<p><sub>{footer}</sub></p>")
    return "\n\n".join(blocks) + "\n"


# ------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assets", help="generate the SVG files")
    a.add_argument("--snapshot", required=True, type=Path)
    a.add_argument("--out", required=True, type=Path)
    r = sub.add_parser("readme", help="write README.md")
    r.add_argument("--check", action="store_true", help="only compare; exit 1 if it differs")
    args = ap.parse_args(argv)
    try:
        content = load_content()
        if args.cmd == "assets":
            ctx = generate(load_snapshot(args.snapshot), content)
            write_assets(ctx.files, args.out)
            print(f"{len(ctx.files)} arquivos em {args.out}")
            return 0
        validate_content(content)
        text = render_readme(content)
        current = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else None
        if args.check:
            if current != text:
                print("README.md desatualizado: rode `uv run tools/build.py readme`", file=sys.stderr)
                return 1
            print("README.md em dia")
            return 0
        README_PATH.write_text(text, encoding="utf-8")
        print("README.md escrito")
        return 0
    except BuildError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
