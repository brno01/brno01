"""Minimal SVG kit for tools/build.py: font metrics, text drawn as outlines, SVG documents.

Text is always drawn as glyph outlines (no embedded font, no <text>), so rendering never
depends on font loading inside an <img>. Measuring uses the real glyph advances plus the GPOS
``kern`` pair adjustments; tracking is added after every character, the last one included.

Output is deterministic: same inputs give identical bytes (no timestamps, sorted glyph sets).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
CACHE_DIR = FONTS_DIR / ".cache"
MAX_SVG_BYTES = 60_000

REDUCED_MOTION_CSS = "@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}"

# logical name -> (file, variation axes or None, id letter). The letter keeps glyph ids stable.
_REGISTRY: dict[str, tuple[str, dict[str, float] | None, str]] = {
    "departure": ("DepartureMono-Regular.otf", None, "h"),
    "literata-400i": ("Literata-Italic-Variable.ttf", {"wght": 400}, "j"),
    "literata-600": ("Literata-Variable.ttf", {"wght": 600}, "k"),
}


# ---------------------------------------------------------------- small helpers

def esc(text: str) -> str:
    """Escape text for XML content and double-quoted attributes."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def num(x: float) -> str:
    """Compact number: at most 2 decimals, no trailing zeros, no '-0'."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _ymd(iso: str) -> tuple[int, int, int]:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso)
    if not m:
        raise ValueError(f"data ISO invalida: {iso!r}")
    return int(m[1]), int(m[2]), int(m[3])


def fmt_date_pt(iso: str) -> str:
    """'2026-11-26' -> '26 nov 2026'. The date part is used as written (no timezone shift)."""
    y, m, d = _ymd(iso)
    return f"{d} {MONTHS_PT[m - 1]} {y}"


def fmt_day_month_pt(iso: str) -> str:
    """'2026-11-26' -> '26 nov'."""
    _, m, d = _ymd(iso)
    return f"{d} {MONTHS_PT[m - 1]}"


def fmt_int_pt(n: int) -> str:
    """1269 -> '1.269'."""
    return f"{int(n):,}".replace(",", ".")


def sqrt_scale(values, out_max: float) -> list[float]:
    """sqrt(v)/sqrt(max)*out_max for each value (all zeros if max is 0)."""
    vals = [max(0.0, float(v)) for v in values]
    top = max(vals, default=0.0)
    if top == 0:
        return [0.0 for _ in vals]
    return [math.sqrt(v) / math.sqrt(top) * out_max for v in vals]


def picture(nome: str, alt: str, base: str = "assets") -> str:
    """The four-source <picture> block for ``<base>/<nome>-<device>-<theme>.svg``."""
    b = f"{base}/{nome}"
    return (
        "<picture>\n"
        f'  <source media="(max-width: 767px) and (prefers-color-scheme: dark)" srcset="{b}-mobile-dark.svg">\n'
        f'  <source media="(max-width: 767px)" srcset="{b}-mobile-light.svg">\n'
        f'  <source media="(prefers-color-scheme: dark)" srcset="{b}-desktop-dark.svg">\n'
        f'  <img src="{b}-desktop-light.svg" width="100%" alt="{esc(alt)}">\n'
        "</picture>"
    )


# ---------------------------------------------------------------- kerning (GPOS pair adjustment)

class _Kern:
    """Pair kerning from the GPOS 'kern' feature (lookup type 2, directly or through type 9)."""

    def __init__(self, tt: TTFont):
        self.subtables: list[list] = []  # per lookup: list of PairPos subtables
        gpos = tt["GPOS"].table if "GPOS" in tt else None
        if gpos is None or not gpos.FeatureList or not gpos.LookupList:
            return
        idx: set[int] = set()
        for fr in gpos.FeatureList.FeatureRecord:
            if fr.FeatureTag == "kern":
                idx.update(fr.Feature.LookupListIndex)
        for i in sorted(idx):
            lk = gpos.LookupList.Lookup[i]
            subs = []
            for st in lk.SubTable:
                if lk.LookupType == 9:
                    if st.ExtensionLookupType != 2:
                        continue
                    st = st.ExtSubTable
                elif lk.LookupType != 2:
                    continue
                subs.append(st)
            if subs:
                self.subtables.append(subs)
        self._cache: dict[tuple[str, str], int] = {}

    def pair(self, a: str, b: str) -> int:
        if not self.subtables:
            return 0
        key = (a, b)
        if key in self._cache:
            return self._cache[key]
        total = 0
        for subs in self.subtables:
            for st in subs:
                hit, val = self._apply(st, a, b)
                if hit:
                    total += val
                    break
        self._cache[key] = total
        return total

    @staticmethod
    def _xadv(v) -> int:
        return int(getattr(v, "XAdvance", 0) or 0) if v is not None else 0

    def _apply(self, st, a: str, b: str) -> tuple[bool, int]:
        cov = st.Coverage.glyphs
        try:
            ci = cov.index(a)
        except ValueError:
            return False, 0
        if st.Format == 1:
            for rec in st.PairSet[ci].PairValueRecord:
                if rec.SecondGlyph == b:
                    return True, self._xadv(rec.Value1)
            return False, 0
        c1 = st.ClassDef1.classDefs.get(a, 0)
        c2 = st.ClassDef2.classDefs.get(b, 0)
        rec = st.Class1Record[c1].Class2Record[c2]
        return True, self._xadv(rec.Value1)


# ---------------------------------------------------------------- fonts

class Font:
    def __init__(self, name: str, tt: TTFont):
        self.name = name
        self._tt = tt
        self.upm = tt["head"].unitsPerEm
        self._cmap = tt.getBestCmap()
        self._hmtx = tt["hmtx"].metrics
        self._kern = _Kern(tt)

    def glyph_name(self, ch: str) -> str:
        g = self._cmap.get(ord(ch))
        if g is None:
            raise ValueError(f"fonte {self.name}: sem glifo para {ch!r} (U+{ord(ch):04X})")
        return g

    def measure(self, texto: str, size: float, tracking: float = 0.0) -> float:
        """Advance width in px (kerning included; tracking added after every character)."""
        total = 0
        prev = None
        for ch in texto:
            g = self.glyph_name(ch)
            total += self._hmtx[g][0]
            if prev is not None:
                total += self._kern.pair(prev, g)
            prev = g
        return total / self.upm * size + tracking * len(texto)

    def wrap(self, texto: str, size: float, max_width: float, tracking: float = 0.0) -> list[str]:
        """Greedy word wrap. A single word wider than max_width stays alone on its line (never split)."""
        lines: list[str] = []
        cur = ""
        for word in texto.split():
            cand = word if not cur else cur + " " + word
            if not cur or self.measure(cand, size, tracking) <= max_width:
                cur = cand
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    def glyph_id(self, gname: str) -> str:
        """Short deterministic id for a glyph outline in <defs>: font letter + glyph index in base 36."""
        letter = _REGISTRY[self.name][2]
        n = self._tt.getGlyphID(gname)
        digits = ""
        while True:
            n, r = divmod(n, 36)
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + digits
            if n == 0:
                return "g" + letter + digits

    def outline_d(self, gname: str) -> str:
        """Path data of one glyph in font units: integer coordinates, relative commands, y up."""
        from fontTools.pens.basePen import BasePen

        class RelPen(BasePen):
            def __init__(self, gs):
                super().__init__(gs)
                self.cur = self.start = (0, 0)
                self.items: list[tuple[str, list[int]]] = []

            def _pt(self, p):
                return (int(round(p[0])), int(round(p[1])))

            def _emit(self, cmd, pts):
                nums: list[int] = []
                for q in pts:
                    nums += [q[0] - self.cur[0], q[1] - self.cur[1]]
                self.items.append((cmd, nums))
                self.cur = pts[-1]

            def _moveTo(self, p):
                p = self._pt(p)
                self._emit("m", [p])
                self.start = p

            def _lineTo(self, p):
                p = self._pt(p)
                dx, dy = p[0] - self.cur[0], p[1] - self.cur[1]
                if dy == 0:
                    self.items.append(("h", [dx]))
                elif dx == 0:
                    self.items.append(("v", [dy]))
                else:
                    self.items.append(("l", [dx, dy]))
                self.cur = p

            def _curveToOne(self, a, b, c):
                self._emit("c", [self._pt(a), self._pt(b), self._pt(c)])

            def _qCurveToOne(self, a, b):
                self._emit("q", [self._pt(a), self._pt(b)])

            def _closePath(self):
                self.items.append(("z", []))
                self.cur = self.start

            _endPath = _closePath

        gs = self._tt.getGlyphSet()
        pen = RelPen(gs)
        gs[gname].draw(pen)
        out: list[str] = []
        prev = None
        for cmd, nums in pen.items:
            if cmd != prev or cmd in ("m", "z"):
                out.append(cmd)
            for k, n in enumerate(nums):
                t = str(n)
                if out[-1][-1:].isdigit() and not t.startswith("-"):
                    out.append(" ")
                out.append(t)
            prev = cmd
        return "".join(out)

    def glyph_run(self, texto: str, size: float, tracking: float = 0.0):
        """[(glyph name, x in font units)] for each character, plus the total advance in font units.

        Same arithmetic as measure(): advance + pair kerning, tracking (px) converted to font units.
        """
        tr = tracking / size * self.upm
        x = 0.0
        prev = None
        run = []
        for ch in texto:
            g = self.glyph_name(ch)
            if prev is not None:
                x += self._kern.pair(prev, g)
            run.append((g, x))
            x += self._hmtx[g][0] + tr
            prev = g
        return run, x


_font_cache: dict[str, Font] = {}


def _load(name: str) -> TTFont:
    fname, axes, _ = _REGISTRY[name]
    src = FONTS_DIR / fname
    if axes is None:
        return TTFont(src, recalcTimestamp=False)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # the key includes a hash of the source TTF and the axes: a replaced font never reuses a stale instance
    digest = hashlib.sha256(src.read_bytes() + repr(sorted(axes.items())).encode()).hexdigest()[:12]
    cached = CACHE_DIR / f"{name}-{digest}.ttf"
    if not cached.exists():
        base = TTFont(src, recalcTimestamp=False)
        limits = {a.axisTag: (axes[a.axisTag] if a.axisTag in axes else a.defaultValue) for a in base["fvar"].axes}
        inst = instancer.instantiateVariableFont(base, limits, inplace=False)
        inst.recalcTimestamp = False
        tmp = cached.with_suffix(f".tmp{os.getpid()}")
        inst.save(tmp)
        os.replace(tmp, cached)
    return TTFont(cached, recalcTimestamp=False)


def font(nome: str) -> Font:
    """Logical font by name (departure, literata-400i, literata-600)."""
    if nome not in _REGISTRY:
        raise KeyError(f"fonte desconhecida {nome!r}; opcoes: {', '.join(sorted(_REGISTRY))}")
    if nome not in _font_cache:
        _font_cache[nome] = Font(nome, _load(nome))
    return _font_cache[nome]


# ---------------------------------------------------------------- document

_BASE_CSS = "text{font-kerning:normal;text-rendering:geometricPrecision}"


class Doc:
    def __init__(self, width: int, height: int, label: str):
        self.width, self.height, self.label = width, height, label
        self._body: list[str] = []
        self._defs: list[str] = []
        self._css: list[str] = []
        self._glyphs: dict[str, tuple[Font, set[str]]] = {}

    def text(self, x, y, s, font, size, fill, anchor="start", tracking=0.0) -> str:
        """Return a ready <g> with one <use> per glyph; pass it to ``add``.

        ``anchor="end"`` makes the last glyph touch X (the trailing tracking is discounted).
        """
        f = _resolve(font)
        run, total = f.glyph_run(s, size, tracking)
        scale = size / f.upm
        width = total * scale  # == f.measure(s, size, tracking)
        if anchor == "middle":
            x = x - width / 2
        elif anchor == "end":
            x = x - (width - tracking)
        used = self._glyphs.setdefault(f.name, (f, set()))[1]
        uses = []
        for g, gx in run:
            if not f.outline_d(g):
                continue  # blank glyph (space)
            used.add(g)
            uses.append(f'<use href="#{f.glyph_id(g)}" x="{num(gx)}"/>')
        sc = f"{scale:.8f}".rstrip("0").rstrip(".")
        attrs = [f'transform="translate({num(x)} {num(y)}) scale({sc} -{sc})"', f'fill="{esc(fill)}"']
        attrs += [f'data-fs="{num(size)}"', f'data-t="{esc(s)}"', 'aria-hidden="true"']
        return f"<g {' '.join(attrs)}>{''.join(uses)}</g>"

    def add(self, raw: str) -> None:
        self._body.append(raw)

    def defs(self, raw: str) -> None:
        self._defs.append(raw)

    def css(self, raw: str) -> None:
        self._css.append(raw)

    def render(self) -> str:
        style = "\n".join([_BASE_CSS] + self._css)
        paths = []
        for name in sorted(self._glyphs):
            f, names = self._glyphs[name]
            for gid, g in sorted((f.glyph_id(g), g) for g in names):
                paths.append(f'<path id="{gid}" d="{f.outline_d(g)}"/>')
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img" aria-label="{esc(self.label)}">',
            f"<style>\n{style}\n</style>",
        ]
        if self._defs or paths:
            out.append("<defs>\n" + "\n".join(paths + self._defs) + "\n</defs>")
        out.extend(self._body)
        out.append("</svg>")
        return "\n".join(out) + "\n"

    def to_bytes(self, label: str = "") -> bytes:
        """Render and check: well-formed XML and at most MAX_SVG_BYTES."""
        raw = self.render().encode("utf-8")
        try:
            ET.fromstring(raw)
        except ET.ParseError as e:
            raise ValueError(f"{label or self.label}: XML mal formado: {e}") from e
        if len(raw) > MAX_SVG_BYTES:
            raise ValueError(f"{label or self.label}: {len(raw)} bytes passa do limite de {MAX_SVG_BYTES}")
        return raw


def _resolve(f) -> Font:
    return f if isinstance(f, Font) else font(f)
