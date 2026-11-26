"""Generated SVG files: safety, accessibility, geometry, size, theme parity, contrast."""
import re
import xml.etree.ElementTree as ET

import pytest

SVG_NS = "{http://www.w3.org/2000/svg}"
GITHUB_BG = {"dark": "#0d1117", "light": "#ffffff"}
CHIP_BG = {"dark": "#151b23", "light": "#f6f8fa"}
ART = re.compile(r"(hero|proj-.+)-(desktop|mobile)-(dark|light)\.svg")
ICON = re.compile(r"icon-([a-z0-9-]+)-(light|dark)\.svg")


def _art(assets):
    return {n: t for n, t in assets.items() if ART.fullmatch(n)}


def _icons(assets):
    return {n: t for n, t in assets.items() if ICON.fullmatch(n)}


def _split(name):
    return ART.fullmatch(name).groups()


def test_expected_file_set(assets, content):
    slugs = [p["slug"] for p in content["projects"]]
    icons = sorted({c["icon"] for c in content["contacts"]} | {i["icon"] for g in content["stack"] for i in g["items"]})
    art = {f"{k}-{d}-{t}.svg" for k in ["hero"] + [f"proj-{s}" for s in slugs]
           for d in ("desktop", "mobile") for t in ("dark", "light")}
    stack = {f"icon-{i}-{t}.svg" for i in icons for t in ("light", "dark")}
    assert len(slugs) == 6 and len(icons) == 19
    assert len(art) == 28 and len(stack) == 38
    assert set(assets) == art | stack and len(assets) == 66
    assert not any(n.startswith("activity-") for n in assets)


def test_well_formed_xml(assets):
    for name, text in assets.items():
        root = ET.fromstring(text)
        assert root.tag == SVG_NS + "svg", name


@pytest.mark.parametrize("pattern", [r"<script", r"<text[\s>]", r"@font-face", r"<image[\s>]",
                                     r"foreignObject", r"@import", r"<iframe", r"\son\w+=", r"data:"])
def test_forbidden_content_absent(assets, pattern):
    for name, text in assets.items():
        assert not re.search(pattern, text, re.I), f"{name} matches {pattern}"


def test_no_embedded_fonts_anywhere(assets):
    for name, text in assets.items():
        assert "font-family" not in text and "font/" not in text, name


def test_no_external_references(assets):
    for name, text in assets.items():
        root = ET.fromstring(text)
        ids = {e.get("id") for e in root.iter() if e.get("id")}
        for el in root.iter():
            for attr, val in el.attrib.items():
                local = attr.split("}")[-1]
                if local == "href":
                    assert val.startswith("#") and val[1:] in ids, f"{name}: href {val}"
                for ref in re.findall(r"url\(([^)]*)\)", val):
                    assert ref.startswith("#") and ref[1:] in ids, f"{name}: url({ref})"
            if el.tag == SVG_NS + "style":
                assert "url(" not in (el.text or ""), name
        # the only absolute URL allowed is the XML namespace
        stripped = text.replace("http://www.w3.org/2000/svg", "")
        assert not re.search(r"https?://", stripped), name


def test_reduced_motion_rule_on_every_animated_file(assets):
    animated = [n for n, t in assets.items() if "animation" in t.replace("animation:none", "")]
    assert {n for n in animated} == {n for n in assets if n.startswith("hero-")}  # only the e-ink sweep moves
    for name in animated:
        assert re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[^}]*animation:\s*none", assets[name]), name


def test_art_has_role_img_and_label(assets, content):
    labels = {content["alt"]["hero"]} | {p["alt"] for p in content["projects"]}
    art = _art(assets)
    assert len(art) == 28
    for name, text in art.items():
        root = ET.fromstring(text)
        assert root.get("role") == "img", name
        label = root.get("aria-label")
        assert label and label.strip(), name
        assert label in labels, name
        # glyph groups are decorative; the label carries the meaning
        for g in root.iter(SVG_NS + "g"):
            if g.get("data-t") is not None:
                assert g.get("aria-hidden") == "true", name


def test_icons_are_decorative(assets):
    icons = _icons(assets)
    assert len(icons) == 38
    for name, text in icons.items():
        root = ET.fromstring(text)
        assert root.get("aria-hidden") == "true", name
        assert root.get("role") is None and root.get("aria-label") is None, name
        assert not root.findall(SVG_NS + "title"), name


def test_art_dimensions_match_device(assets):
    for name, text in _art(assets).items():
        _, device, _ = _split(name)
        root = ET.fromstring(text)
        w, h = int(root.get("width")), int(root.get("height"))
        assert w == (846 if device == "desktop" else 308), name
        assert root.get("viewBox") == f"0 0 {w} {h}", name
        assert h > 0


def test_fixed_heights(assets):
    for name, text in _art(assets).items():
        kind, device, _ = _split(name)
        h = int(ET.fromstring(text).get("height"))
        if kind == "hero":
            assert h == (332 if device == "desktop" else 388), name
        elif device == "desktop":
            assert h == 104, name
        else:
            assert 100 <= h <= 150, name  # one or two description lines


def test_icon_dimensions(assets):
    for name, text in _icons(assets).items():
        root = ET.fromstring(text)
        assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("24", "24", "0 0 24 24"), name


def test_size_budget(assets):
    sizes = {n: len(t.encode("utf-8")) for n, t in assets.items()}
    assert all(s <= 60_000 for s in sizes.values()), max(sizes.items(), key=lambda kv: kv[1])
    assert sum(sizes.values()) <= 700_000


def test_dark_and_light_differ_only_in_colors(assets, build):
    for name, dark in _art(assets).items():
        kind, device, theme = _split(name)
        if theme != "dark":
            continue
        light = assets[f"{kind}-{device}-light.svg"]
        assert dark != light, name
        keys = ("body", "edge", "dots") if kind == "hero" else ("page_ink", "page_muted")
        norm = {}
        for th, text in (("dark", dark), ("light", light)):
            for key in keys:
                text = text.replace(f'"{build.THEMES[th][key]}"', '"@theme"')
            norm[th] = text
        assert norm["dark"] == norm["light"], name


def test_icon_themes_differ_only_in_the_fill(assets):
    for name, dark in _icons(assets).items():
        slug, theme = ICON.fullmatch(name).groups()
        if theme != "dark":
            continue
        light = assets[f"icon-{slug}-light.svg"]
        strip = lambda t: re.sub(r'(<path fill=")#[0-9a-f]{6}', r"\1#", t)  # noqa: E731
        assert strip(dark) == strip(light), slug


def _lum(hexcolor):
    h = hexcolor.lstrip("#")
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_contrast_formula_sanity():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#777777") == pytest.approx(1.0)


def test_screen_ink_on_paper_meets_wcag_aa(build):
    assert contrast(build.INK, build.PAPER) >= 4.5


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_page_tokens_meet_wcag_aa(build, theme):
    pal = build.THEMES[theme]
    assert pal["page_bg"] == GITHUB_BG[theme]
    for token in ("page_ink", "page_muted"):
        assert contrast(pal[token], GITHUB_BG[theme]) >= 4.5, f"{theme}/{token}"


def test_every_text_fill_in_svgs_meets_wcag_aa(assets, build):
    seen = 0
    for name, text in _art(assets).items():
        kind, _, theme = _split(name)
        # text on the e-ink screen sits on paper in both themes; project pages sit on the GitHub page
        bg = build.PAPER if kind == "hero" else GITHUB_BG[theme]
        for g in ET.fromstring(text).iter(SVG_NS + "g"):
            if g.get("data-t") is not None:
                seen += 1
                assert contrast(g.get("fill"), bg) >= 4.5, f"{name}: {g.get('data-t')!r} {g.get('fill')}"
    assert seen > 100


def test_hero_text_is_all_screen_ink(assets, build):
    for name, text in _art(assets).items():
        if name.startswith("hero-"):
            fills = {g.get("fill") for g in ET.fromstring(text).iter(SVG_NS + "g") if g.get("data-t") is not None}
            assert fills == {build.INK}, name


def test_stack_icons_meet_3_5_to_1(assets, build):
    """Plain marks on the GitHub chip; letter logos (TypeScript, JavaScript) against their letters."""
    meta = build.load_icon_meta()
    for name, text in _icons(assets).items():
        slug, theme = ICON.fullmatch(name).groups()
        root = ET.fromstring(text)
        path = root.find(SVG_NS + "path")
        rect = root.find(SVG_NS + "rect")
        fill = path.get("fill")
        if meta[slug]["letters"]:
            assert rect is not None and rect.get("fill") == "#" + meta[slug]["letters"], name
            assert fill == "#" + meta[slug]["hex"].lower(), name
            assert contrast(fill, rect.get("fill")) >= 3.5, name
        else:
            assert rect is None, name
            assert contrast(fill, CHIP_BG[theme]) >= 3.5, f"{name} {fill}"
