#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools==4.69.1"]
# ///
"""Profile README generator: the 82 SVG art files and README.md.

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

ICONS_DIR = HERE / "icons"

# the e-ink screen is always the opposite of the page: paper on the dark page, inverted (dark mode) on the
# light page; the body and the page colours follow the theme
PAPER, INK = "#e8e4d9", "#1a1a18"
SCREEN = {"dark": (PAPER, INK), "light": (INK, PAPER)}  # theme -> (screen background, screen ink)
THEMES = {
    "dark": {"body": "#1c1e22", "edge": "#3d444d", "page_ink": "#e8e4d9", "page_muted": "#a8a497", "page_bg": "#0d1117", "dots": "#59636e"},
    "light": {"body": "#3a3d43", "edge": "#1f2125", "page_ink": "#1a1a18", "page_muted": "#5c5a52", "page_bg": "#ffffff", "dots": "#101113"},
}
LIT6, LIT4I, DEP = "literata-600", "literata-400i", "departure"
CRISP = 'shape-rendering="crispEdges"'
MIN_NAME_SIZE = 28  # the mobile hero name never shrinks below this
CHIP_BG = {"light": "f6f8fa", "dark": "151b23"}  # GitHub kbd backgrounds
CHIP_CONTRAST = 3.5


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
HEX6 = re.compile(r"[0-9a-fA-F]{6}")
SVG_PATH_D = re.compile(r"[MmLlHhVvCcSsQqTtAaZz0-9.,\- ]+")
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
        if p["repo"] not in repos:
            raise BuildError(f'projeto {p["repo"]} não está entre os repositórios públicos de {content["login"]}')
    # every repository counts (public total, most recent push), so every entry must be well formed
    for name, r in repos.items():
        if not isinstance(r, dict) or "pushed_at" not in r:
            raise BuildError(f"snapshot: repos[{name!r}] deve ser um objeto com pushed_at")
        _iso_date(r["pushed_at"], f"repos[{name!r}].pushed_at")


def derive_status(repos: dict, login: str) -> tuple[int, dict]:
    """(number of public repositories, {"repo", "date"} of the most recent push).

    The profile repository itself is left out of the "most recent" pick: the daily publish pushes to it.
    Ties on the date are broken by repository name, ascending. No other repository is an error.
    """
    others = sorted((name, r["pushed_at"][:10]) for name, r in repos.items() if name.lower() != login.lower())
    if not others:
        raise BuildError(f"nenhum repositório público além de {login} para mostrar o push mais recente")
    name, date = max(others, key=lambda nd: nd[1])  # max keeps the first of equal dates: the smallest name
    return len(repos), {"repo": name, "date": date}


_STR, _LIST_STR = "str", "list[str]"
CONTENT_SCHEMA = {
    "name": _STR, "lede": _STR, "about": _LIST_STR,
    "login": _STR, "assets_base": _STR,
    "contacts": [{"label": _STR, "url": _STR, "icon": _STR}],
    "projects": [{"slug": _STR, "url": _STR, "title": _STR, "desc": _STR, "desc_short": _STR,
                  "details": _STR, "stack": _LIST_STR, "alt": _STR, "repo": _STR}],
    # private repositories: no link (visitors would get a 404) and no art, so the stack is plain text
    "also_private": [{"title": _STR, "desc": _STR, "details": _STR, "stack": _LIST_STR}],
    "studies": [{"title": _STR, "url": _STR, "note": _STR}],
    # "icon" is optional: a technology without a simple-icons mark gets a plain chip
    "stack": [{"group": _STR, "items": [{"name": _STR}]}],
    "labels": {k: _STR for k in ("updated", "about", "projects", "stack", "also_private", "studies", "hub",
                                 "contrib_line", "public_repos", "last_push", "most_recent",
                                 "projects_details", "private_details")},
    "alt": {"hero": _STR},
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
    # same rule as the file-name check of the publish job; also keeps the README URLs clean
    named = [(c["label"], c["icon"]) for c in content["contacts"]]
    named += [(i["name"], i["icon"]) for g in content["stack"] for i in g["items"] if "icon" in i]
    for name, icon in named:
        if not isinstance(icon, str) or not SLUG.fullmatch(icon):
            raise BuildError(f"content.json: ícone {icon!r} de {name} inválido (use [a-z0-9][a-z0-9-]*)")


def icon_slugs(content: dict) -> list[str]:
    """Icon slugs used by the contact and stack chips, without repeats, sorted."""
    return sorted({c["icon"] for c in content["contacts"]}
                  | {i["icon"] for g in content["stack"] for i in g["items"] if "icon" in i})


class Ctx:
    """Inputs and results of one generation run."""

    def __init__(self, snap: dict, content: dict):
        self.snap, self.copy = snap, content
        self.lab = content["labels"]
        self.projects = content["projects"]
        self.public_repos, self.last_push = derive_status(snap["repos"], content["login"])
        self.files: dict[str, bytes] = {}

    def emit(self, name: str, doc) -> None:
        self.files[name] = doc.to_bytes(name)

    def emit_raw(self, name: str, raw: bytes) -> None:
        if len(raw) > K.MAX_SVG_BYTES:
            raise BuildError(f"{name}: {len(raw)} bytes passa do limite de {K.MAX_SVG_BYTES}")
        self.files[name] = raw


# the six project pages: 16x16 pixel icons. '#' ink, '+' dotted (50% dither), '.' empty
PIXEL_ICONS = {
    "kindle-monitor": [
        "..############..",
        "..#..........#..",
        "..#.########.#..",
        "..#..........#..",
        "..#.######...#..",
        "..#..........#..",
        "..#.#######..#..",
        "..#..........#..",
        "..#..........#..",
        "..#....##....#..",
        "..#....##....#..",
        "..#..#.##.#..#..",
        "..#..#.##.#..#..",
        "..#..#.##.#..#..",
        "..############..",
        "..############..",
    ],
    "sinapses": [
        "................",
        ".####...........",
        ".#######........",
        ".####..#........",
        ".####..#........",
        ".......#........",
        "......++++......",
        "......++++###...",
        "......++++..#...",
        "......++++..#...",
        "............#...",
        "...........####.",
        "...........####.",
        "...........####.",
        "...........####.",
        "................",
    ],
    "tailnet-dns-failover": [
        ".####......####.",
        ".####......####.",
        "..##........++..",
        "..##........++..",
        "..##........++..",
        "..##........++..",
        "..##........++..",
        "..##........++..",
        "..######++++++..",
        "..######++++++..",
        ".......##.......",
        ".......##.......",
        ".......##.......",
        ".......##.......",
        ".......##.......",
        ".......##.......",
    ],
    "galaxy-s10": [
        ".#######........",
        ".#.....#........",
        ".#.+++.#...#....",
        ".#.+++.#....#...",
        ".#.+++.#.....#..",
        ".#.+++.#......#.",
        ".#.+++.#.#....#.",
        ".#.+++.#..#...#.",
        ".#.+++.#..#...#.",
        ".#.+++.#.#....#.",
        ".#.+++.#......#.",
        ".#.+++.#.....#..",
        ".#.+++.#....#...",
        ".#.....#...#....",
        ".#..#..#........",
        ".#######........",
    ],
    "nomad": [
        "................",
        "................",
        "..############..",
        "..#..........#..",
        "..#.++++++++.#..",
        "..#.++++++++.#..",
        "..#.++++++++.#..",
        "..#.++++++++.#..",
        "..#.++++++++.#..",
        "..#.++++++++.#..",
        "..#..........#..",
        "..############..",
        "################",
        ".##############.",
        "................",
        "................",
    ],
    "dot-github": [
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "####..++++..####",
        "#..#..++++..####",
        "#..###++++######",
        "#..#..++++..####",
        "####..++++..####",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
}
for _k, _v in PIXEL_ICONS.items():
    assert len(_v) == 16 and all(len(r) == 16 for r in _v), _k


def check_projects(snap: dict, content: dict) -> None:
    for p in content["projects"]:
        # same rule as the file-name check of the publish job in the workflow
        if not SLUG.fullmatch(p["slug"]):
            raise BuildError(f'projeto {p["repo"]}: slug {p["slug"]!r} inválido (use [a-z0-9][a-z0-9-]*)')
    drawn = set(PIXEL_ICONS)
    if {p["slug"] for p in content["projects"]} != drawn or len(content["projects"]) != len(drawn):
        raise BuildError("os projetos de content.json diferem dos ícones do desenho: " + ", ".join(sorted(drawn)))


def f(x):
    return K.num(x)


# ------------------------------------------------------------------ stack icons (simple-icons, CC0)


def load_icon_meta(icons_dir: Path = ICONS_DIR) -> dict:
    """icons.json: icon slug -> {"hex": brand colour, "letters": colour of knocked-out letters or None}."""
    path = icons_dir / "icons.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BuildError(f"não consegui ler {path}: {e}") from e
    icons = data.get("icons") if isinstance(data, dict) else None
    if not isinstance(icons, dict) or not isinstance(data.get("source"), str):
        raise BuildError(f"{path}: precisa ter 'source' (texto) e 'icons' (objeto)")
    for slug, m in icons.items():
        if not isinstance(m, dict) or not isinstance(m.get("hex"), str) or not HEX6.fullmatch(m["hex"]):
            raise BuildError(f"{path}: icons[{slug!r}].hex deve ter 6 dígitos hexadecimais")
        if "letters" not in m:
            raise BuildError(f"{path}: icons[{slug!r}] precisa da chave letters (null ou 6 dígitos hexadecimais)")
        letters = m["letters"]
        if letters is not None and not (isinstance(letters, str) and HEX6.fullmatch(letters)):
            raise BuildError(f"{path}: icons[{slug!r}].letters deve ser null ou 6 dígitos hexadecimais")
    return icons


def read_icon_path(slug: str, icons_dir: Path = ICONS_DIR) -> str:
    """The ``d`` of an icon SVG, after checking that the file has exactly the expected shape."""
    return read_icon(slug, icons_dir)[0]


# accepted source shapes: a square viewBox of one of these sides (simple-icons and Material Symbols
# draw on 24, devicon on 128, the Apache Camel mark on 256) and one <path>, after an optional <title>
ICON_BOXES = {f"0 0 {n} {n}": n for n in (24, 128, 256)}


def read_icon(slug: str, icons_dir: Path = ICONS_DIR) -> tuple[str, int]:
    """The ``d`` of an icon SVG and the side of its square viewBox."""
    import xml.etree.ElementTree as ET
    ns = "{http://www.w3.org/2000/svg}"
    path = icons_dir / f"{slug}.svg"
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as e:
        raise BuildError(f"ícone {slug}: não consegui ler {path.name}: {e}") from e
    if root.tag != ns + "svg":
        raise BuildError(f"ícone {slug}: a raiz deve ser <svg>")
    box = root.get("viewBox")
    if box not in ICON_BOXES:
        raise BuildError(f"ícone {slug}: viewBox deve ser quadrado de lado 24, 128 ou 256, veio {box!r}")
    if [k.tag for k in root] not in ([ns + "title", ns + "path"], [ns + "path"]):
        raise BuildError(f"ícone {slug}: o <svg> deve ter exatamente um <path>, depois de um <title> opcional")
    d = root.find(ns + "path").get("d")
    if d is None or not SVG_PATH_D.fullmatch(d):
        raise BuildError(f"ícone {slug}: o atributo d do <path> tem caracteres inesperados")
    return d, ICON_BOXES[box]


def _rgb(h: str) -> list[float]:
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def rel_luminance(h: str) -> float:
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in _rgb(h)]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((rel_luminance(a), rel_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(h: str, target: str, t: float) -> str:
    return "".join(f"{round((x + (y - x) * t) * 255):02x}" for x, y in zip(_rgb(h), _rgb(target)))


def icon_color(hexcolor: str, theme: str) -> str:
    """Brand colour nudged toward black (light) or white (dark) until it reaches 3.5:1 on the GitHub chip."""
    h = hexcolor.lower()
    if theme == "dark" and rel_luminance(h) < 0.03:  # black marks turn near-white on dark, as their own dark logos do
        return "e6edf3"
    target = "000000" if theme == "light" else "ffffff"
    t = 0.0
    while contrast_ratio(_mix(h, target, t), CHIP_BG[theme]) < CHIP_CONTRAST and t < 1:
        t += 0.02
    return _mix(h, target, t)


def icon_svg(slug: str, meta: dict, d: str, theme: str, box: int = 24) -> bytes:
    letters = meta["letters"]
    if letters and box != 24:
        raise BuildError(f"ícone {slug}: letters só vale para ícones de 24x24")
    if letters:  # a square logo: fill behind the letters so it reads as the real mark on any chip
        fill = meta["hex"].lower()
        back = f'<rect x="1" y="1" width="22" height="22" fill="#{letters}"/>'
    else:
        fill, back = icon_color(meta["hex"], theme), ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {box} {box}" width="24" height="24" aria-hidden="true">'
            f'{back}<path fill="#{fill}" d="{d}"/></svg>\n').encode("utf-8")


def emit_icons(ctx, icons_dir: Path = ICONS_DIR) -> None:
    meta = load_icon_meta(icons_dir)
    for slug in icon_slugs(ctx.copy):
        if not SLUG.fullmatch(slug):
            raise BuildError(f"ícone {slug!r}: nome inválido (use [a-z0-9][a-z0-9-]*)")
        if slug not in meta:
            raise BuildError(f"ícone {slug}: falta em {icons_dir.name}/icons.json")
        d, box = read_icon(slug, icons_dir)
        for theme in ("light", "dark"):
            ctx.emit_raw(f"icon-{slug}-{theme}.svg", icon_svg(slug, meta[slug], d, theme, box))


# ------------------------------------------------------------------ text helpers


def dep(d, x, y, s, size, fill, right=None, tracking=0.0):
    """Departure text at integer x/y; if right is given the text ends at that x."""
    fnt = K.font(DEP)
    if right is not None:
        x = right - fnt.measure(s, size, tracking) + tracking
    return d.text(round(x), y, s, fnt, size, fill, tracking=tracking)


def dither(pid, color):
    return (f'<pattern id="{pid}50" width="2" height="2" patternUnits="userSpaceOnUse">'
            f'<rect x="0" y="0" width="1" height="1" fill="{color}"/><rect x="1" y="1" width="1" height="1" fill="{color}"/></pattern>')


def month_marks(weeks):
    """Index and name of the first week starting on day <= 7 of each month."""
    marks, seen = [], None
    for i, w in enumerate(weeks):
        y, m, dd = int(w["start"][:4]), int(w["start"][5:7]), int(w["start"][8:10])
        if dd <= 7 and (y, m) != seen and i > 0:
            marks.append((i, K.MONTHS_PT[m - 1]))
            seen = (y, m)
    return marks


# ------------------------------------------------------------------ hero


def hero(ctx, device, theme):
    """Wide e-ink display: thin bezel, no device chin, panel laid out in two bands."""
    c = THEMES[theme]
    paper, ink = SCREEN[theme]
    snap, copy, L = ctx.snap, ctx.copy, ctx.lab
    mob = device == "mobile"
    if mob:
        W, bez, chin, sh = 308, 10, 22, 356
    else:
        W, bez, chin, sh = 846, 12, 28, 292
    H = bez + sh + chin
    sx, sy, sw = bez, bez, W - 2 * bez
    m = 16 if mob else 28
    right = sw - m
    d = K.Doc(W, H, copy["alt"]["hero"])
    d.css(".sweep{animation:sweep 28s steps(16,end) infinite}"
          f"@keyframes sweep{{0%,95%{{transform:translateY(0)}}100%{{transform:translateY({sh + 46}px)}}}}")
    d.css(K.REDUCED_MOTION_CSS)
    d.defs(f'<clipPath id="scr"><rect x="0" y="0" width="{sw}" height="{sh}" rx="2"/></clipPath>')
    d.defs(dither("d", ink))
    # body: a plain monitor frame, power LED on the lower bezel
    d.add(f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="{12 if mob else 14}" fill="{c["body"]}" stroke="{c["edge"]}"/>')
    led_y = sy + sh + chin // 2
    d.add(f'<rect x="{W - bez - 22}" y="{led_y - 1}" width="10" height="2" rx="1" fill="{c["dots"]}"/>')
    d.add(f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="2" fill="{paper}"/>')
    g = [f'<g transform="translate({sx} {sy})" clip-path="url(#scr)">']
    t = lambda *a, **k: g.append(dep(d, *a, **k))  # noqa: E731
    f11 = K.font(DEP)

    def leaders(lab, val, y, x_left, x_right):
        label_end = x_left + f11.measure(lab, 11)
        value_start = x_right - f11.measure(val, 11)
        x0, x1 = round(label_end) + 6, round(value_start) - 6
        if x0 > x1:
            raise BuildError(f"hero-{device}: o rótulo {lab!r} e o valor {val!r} do indicador se sobrepõem")
        t(x_left, y, lab, 11, ink)
        t(0, y, val, 11, ink, right=x_right)
        xs = range(x0 + (-x0) % 4, x1, 4)
        g.append(f'<path d="{"".join(f"M{x} {y - 1}h1v1h-1z" for x in xs)}" fill="{ink}" {CRISP}/>')

    def bars(x_left, base, bw, step, mh, mon_y, every=1):
        weeks = snap["weeks_26"]
        counts = [w["count"] for w in weeks]
        out = []
        for i, (cnt, h) in enumerate(zip(counts, K.sqrt_scale(counts, mh))):
            x = round(x_left + i * step)
            if cnt == 0:
                out.append(f'<rect x="{x}" y="{base - 1}" width="{bw}" height="1" fill="{ink}"/>')
                continue
            hh = max(2, int(round(h / 2)) * 2)
            fill = ink if i == len(weeks) - 1 else "url(#d50)"
            out.append(f'<rect x="{x}" y="{base - hh}" width="{bw}" height="{hh}" fill="{fill}"/>')
        g.append(f'<g {CRISP}>' + "".join(out) + "</g>")
        span = round(x_left + (len(weeks) - 1) * step) + bw - x_left
        g.append(f'<rect x="{x_left}" y="{base}" width="{span}" height="1" fill="{ink}" {CRISP}/>')
        for k, (i, name) in enumerate(month_marks(weeks)):
            if k % every:
                continue
            t(round(x_left + i * step), mon_y, name, 11, ink)

    lp = ctx.last_push
    inds = [(L["public_repos"], str(ctx.public_repos)), (L["last_push"], K.fmt_day_month_pt(lp["date"])),
            (L["most_recent"], lp["repo"])]
    big = K.font(LIT6)
    total = K.fmt_int_pt(snap["contributions_year"])
    if mob:
        t(m, 24, L["hub"], 11, ink)
        t(0, 24, f"{L['updated']} em {K.fmt_day_month_pt(snap['today'])}", 11, ink, right=right)
        g.append(f'<rect x="{m}" y="34" width="{right - m}" height="1" fill="{ink}" {CRISP}/>')
        size = 34
        while big.measure(copy["name"], size) > right - m:
            size -= 1
            if size < MIN_NAME_SIZE:
                raise BuildError(f"hero-mobile: o nome {copy['name']!r} exigiria menos de {MIN_NAME_SIZE} px para caber")
        g.append(d.text(m, 80, copy["name"], big, size, ink))
        g.append(f'<line x1="{m}" y1="100.5" x2="{right}" y2="100.5" stroke="{ink}" stroke-dasharray="1 3" {CRISP}/>')
        for (lab, val), y in zip(inds, (128, 150, 172)):
            leaders(lab, val, y, m, right)
        g.append(f'<line x1="{m}" y1="192.5" x2="{right}" y2="192.5" stroke="{ink}" stroke-dasharray="1 3" {CRISP}/>')
        t(m, 228, total, 22, ink)
        t(m, 248, L["contrib_line"], 11, ink)
        bars(m, 322, 7, 9.6, 56, 340, every=2)
    else:
        if m + big.measure(copy["name"], 52) + 12 > 500:
            raise BuildError(f"hero-desktop: o nome {copy['name']!r} encosta na coluna dos indicadores")
        t(m, 28, L["hub"], 11, ink)
        t(0, 28, f"{L['updated']} em {K.fmt_date_pt(snap['today'])}", 11, ink, right=right)
        g.append(f'<rect x="{m}" y="40" width="{right - m}" height="1" fill="{ink}" {CRISP}/>')
        g.append(d.text(m, 106, copy["name"], big, 52, ink))
        for (lab, val), y in zip(inds, (72, 92, 112)):
            leaders(lab, val, y, 500, right)
        g.append(f'<line x1="{m}" y1="136.5" x2="{right}" y2="136.5" stroke="{ink}" stroke-dasharray="1 3" {CRISP}/>')
        t(m, 172, total, 22, ink)
        f22 = f11.measure(total, 22)
        t(round(m + f22) + 12, 172, L["contrib_line"], 11, ink)
        bars(m, 256, 22, (right - m - 22) / 25, 66, 276)
    g.append(f'<rect class="sweep" x="0" y="-46" width="{sw}" height="46" fill="{ink}" opacity="0.1"/>')
    g.append(f'<rect x="0.5" y="0.5" width="{sw - 1}" height="{sh - 1}" rx="2" fill="none" stroke="{ink}" stroke-opacity="0.25"/>')
    g.append("</g>")
    d.add("\n".join(g))
    ctx.emit(f"hero-{device}-{theme}.svg", d)


# ------------------------------------------------------------------ project pages


def icon_path(rows, cell, ch):
    out = []
    for y, r in enumerate(rows):
        x = 0
        while x < 16:
            if r[x] == ch:
                x0 = x
                while x < 16 and r[x] == ch:
                    x += 1
                out.append(f"M{x0 * cell} {y * cell}h{(x - x0) * cell}v{cell}h-{(x - x0) * cell}z")
            else:
                x += 1
    return "".join(out)


def proj(ctx, device, theme, p):
    c = THEMES[theme]
    mob = device == "mobile"
    it = K.font(LIT4I)
    df = K.font(DEP)
    tf = K.font(LIT6)
    W = 308 if mob else 846
    cell, oy, tx = (2, 6, 44) if mob else (4, 22, 84)
    box = W - tx  # text column: 264 on mobile, 762 on desktop
    stack = ", ".join(p["stack"])
    if df.measure(stack, 11) > box:
        raise BuildError(f'projeto {p["repo"]}: a stack não cabe numa linha no {device}')
    if mob:
        lines = it.wrap(p["desc_short"], 13.5, box)
        if len(lines) > 2 or any(it.measure(ln, 13.5) > box for ln in lines):
            raise BuildError(f'projeto {p["repo"]}: desc_short não cabe em 2 linhas no celular '
                             "(texto longo demais ou palavra mais larga que a caixa)")
        H = 54 + 18 * (len(lines) - 1) + 26 + 21
    else:
        if it.measure(p["desc"], 15) > box:
            raise BuildError(f'projeto {p["repo"]}: desc não cabe numa linha no desktop')
        H = 104
    ink, muted = c["page_ink"], c["page_muted"]
    d = K.Doc(W, H, p["alt"])
    d.defs(dither("p", ink))
    rows = PIXEL_ICONS[p["slug"]]
    d.add(f'<g transform="translate(0 {oy})" {CRISP}><path d="{icon_path(rows, cell, "#")}" fill="{ink}"/>'
          f'<path d="{icon_path(rows, cell, "+")}" fill="url(#p50)"/></g>')
    tsize = 18 if mob else 23
    pushed = ctx.snap["repos"][p["repo"]]["pushed_at"]
    num = K.fmt_day_month_pt(pushed) if mob else f"{ctx.lab['last_push']} {K.fmt_day_month_pt(pushed)}"
    ty = 28 if mob else 38
    d.add(d.text(tx, ty, p["title"], tf, tsize, ink))
    d.add(dep(d, 0, ty, num, 11, muted, right=W))
    x0 = tx + round(tf.measure(p["title"], tsize)) + 12
    x1 = round(W - df.measure(num, 11)) - 12
    if x0 > x1:
        raise BuildError(f'projeto {p["repo"]}: o título encosta na data no {device}')
    if x1 - x0 >= 30:  # a leader shorter than 30 px is left out
        d.add(f'<line x1="{x0}" y1="{ty - 5.5}" x2="{x1}" y2="{ty - 5.5}" stroke="{ink}" stroke-dasharray="1 5" {CRISP}/>')
    if mob:
        for i, ln in enumerate(lines):
            d.add(d.text(tx, 54 + 18 * i, ln, it, 13.5, ink))
        sy = 54 + 18 * (len(lines) - 1) + 26
        d.add(dep(d, tx, sy, stack, 11, muted))
        foot = sy + 20.5
    else:
        d.add(d.text(tx, 64, p["desc"], it, 15, ink))
        d.add(dep(d, tx, 86, stack, 11, muted))
        foot = 103.5
    d.add(f'<line x1="0" y1="{foot}" x2="{W}" y2="{foot}" stroke="{ink}" stroke-dasharray="1 3" {CRISP}/>')
    ctx.emit(f'proj-{p["slug"]}-{device}-{theme}.svg', d)


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
                for p in ctx.projects:
                    proj(ctx, dev, th, p)
        emit_icons(ctx)
    except ValueError as e:  # svgkit: character without a glyph, SVG too big or malformed
        raise BuildError(str(e)) from e
    expected = 4 + 4 * len(ctx.projects) + 2 * len(icon_slugs(content))
    if len(ctx.files) != expected:
        raise BuildError(f"gerei {len(ctx.files)} arquivos, esperava {expected}")
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

    def chip(slug: str | None, text: str) -> str:
        if slug is None:
            return f"<kbd>{e(text)}</kbd>"
        return (f'<kbd><picture><source media="(prefers-color-scheme: dark)" srcset="{base}/icon-{slug}-dark.svg">'
                f'<img src="{base}/icon-{slug}-light.svg" width="11" height="11" alt="" align="top"></picture> '
                f'{e(text)}</kbd>')

    blocks = [
        "<!-- Arquivo gerado por tools/build.py a partir de tools/content.json. "
        "Não edite à mão: edite o JSON e rode `uv run tools/build.py readme`. -->",
        "<p>\n" + pic("hero", content["alt"]["hero"]) + "\n</p>",
        e(content["lede"]),
        "<p>" + " ".join(f'<a href="{e(x["url"])}">{chip(x["icon"], x["label"])}</a>' for x in content["contacts"]) + "</p>",
        f'## {lab["about"]}',
    ]
    blocks += [e(t) for t in content["about"]]
    blocks.append(f'## {lab["projects"]}')
    blocks.append("<p>\n" + "\n".join(
        f'<a href="{e(p["url"])}">\n{pic("proj-" + p["slug"], p["alt"])}\n</a>' for p in content["projects"]) + "\n</p>")
    # the art shows the short description; the full one stays one click away, as real text
    blocks.append(f'<details>\n<summary><b>{e(lab["projects_details"])}</b></summary>\n\n'
                  + "\n".join(f'- **[{e(p["title"])}]({p["url"]})**: {e(p["details"])}' for p in content["projects"])
                  + "\n\n</details>")
    private = content["also_private"]
    blocks.append(f'**{lab["also_private"]}**')
    blocks.append("\n".join(
        f'- **{e(p["title"])}**: {e(p["desc"])}<br>\n  <sub>{e(", ".join(p["stack"]))}</sub>' for p in private))
    blocks.append(f'<details>\n<summary><b>{e(lab["private_details"])}</b></summary>\n\n'
                  + "\n".join(f'- **{e(p["title"])}**: {e(p["details"])}' for p in private)
                  + "\n\n</details>")
    blocks.append(f'**{lab["studies"]}:** ' + ", ".join(
        f'[{e(s["title"])}]({s["url"]}) ({e(s["note"])})' for s in content["studies"]))
    blocks.append(f'## {lab["stack"]}')
    for gr in content["stack"]:
        blocks.append(f'**{e(gr["group"])}**<br>\n' + " ".join(chip(i.get("icon"), i["name"]) for i in gr["items"]))
    en = content["english"]
    blocks.append(
        f'<details>\n<summary><b>{e(en["summary"])}</b></summary>\n\n### {e(en["heading"])}\n\n{e(en["lede"])}\n\n'
        + "\n\n".join(e(t) for t in en["about"]) + f'\n\n{e(en["closing"])}\n\n</details>')
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
