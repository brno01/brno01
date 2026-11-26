"""Generated SVG files: safety, accessibility, geometry, size, theme parity, contrast."""
import re
import xml.etree.ElementTree as ET

import pytest

SVG_NS = "{http://www.w3.org/2000/svg}"
GITHUB_BG = {"dark": "#0d1117", "light": "#ffffff"}


def _split(name):
    m = re.fullmatch(r"(.+)-(desktop|mobile)-(dark|light)\.svg", name)
    assert m, name
    return m.groups()


def _names(assets):
    return sorted(assets)


def test_expected_file_set(assets, content):
    slugs = [p["slug"] for p in content["projects"]]
    expected = {f"{k}-{d}-{t}.svg" for k in ["hero", "activity"] + [f"proj-{s}" for s in slugs]
                for d in ("desktop", "mobile") for t in ("dark", "light")}
    assert len(slugs) == 6
    assert set(assets) == expected and len(expected) == 32


def test_well_formed_xml(assets):
    for name, text in assets.items():
        root = ET.fromstring(text)
        assert root.tag == SVG_NS + "svg", name


@pytest.mark.parametrize("pattern", [r"<script", r"<text[\s>]", r"@font-face", r"<image[\s>]",
                                     r"foreignObject", r"@import", r"<iframe", r"\son\w+="])
def test_forbidden_content_absent(assets, pattern):
    for name, text in assets.items():
        assert not re.search(pattern, text, re.I), f"{name} matches {pattern}"


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


def test_reduced_motion_rule_in_every_file(assets):
    for name, text in assets.items():
        assert re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[^}]*animation:\s*none", text), name


def test_accessible_label_on_root(assets, content):
    labels = {content["alt"]["hero"], content["alt"]["activity"]} | {p["alt"] for p in content["projects"]}
    for name, text in assets.items():
        root = ET.fromstring(text)
        assert root.get("role") == "img", name
        label = root.get("aria-label")
        assert label and label.strip(), name
        assert label in labels, name
        # glyph groups are decorative; the label carries the meaning
        for g in root.iter(SVG_NS + "g"):
            if g.get("data-t") is not None:
                assert g.get("aria-hidden") == "true", name


def test_dimensions_match_device(assets):
    for name, text in assets.items():
        _, device, _ = _split(name)
        root = ET.fromstring(text)
        w, h = int(root.get("width")), int(root.get("height"))
        assert w == (846 if device == "desktop" else 308), name
        assert root.get("viewBox") == f"0 0 {w} {h}", name
        assert h > 0


def test_fixed_heights(assets):
    for name, text in assets.items():
        kind, device, _ = _split(name)
        h = int(ET.fromstring(text).get("height"))
        if device == "desktop":
            assert h == {"hero": 300, "activity": 132}.get(kind, 88), name
        elif kind == "hero":
            assert h == 380, name


def test_size_budget(assets):
    sizes = {n: len(t.encode("utf-8")) for n, t in assets.items()}
    assert all(s <= 60_000 for s in sizes.values()), max(sizes.items(), key=lambda kv: kv[1])
    assert sum(sizes.values()) <= 700_000


def test_dark_and_light_differ_only_in_colors(assets, build):
    pal = build.PALETTE
    for name, dark in assets.items():
        kind, device, theme = _split(name)
        if theme != "dark":
            continue
        light = assets[f"{kind}-{device}-light.svg"]
        assert dark != light, name
        norm = {}
        for th, text in (("dark", dark), ("light", light)):
            for token, color in pal[th].items():
                text = text.replace(f'"{color}"', f'"@{token}"')
            norm[th] = text
        assert norm["dark"] == norm["light"], name
        assert len(list(ET.fromstring(dark).iter())) == len(list(ET.fromstring(light).iter()))


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


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_tokens_meet_wcag_aa(build, theme):
    pal = build.PALETTE[theme]
    assert pal["bg"] == GITHUB_BG[theme]
    for token in ("ink", "body", "muted"):
        assert contrast(pal[token], GITHUB_BG[theme]) >= 4.5, f"{theme}/{token}"


def test_every_text_fill_in_svgs_meets_wcag_aa(assets):
    seen = {"dark": set(), "light": set()}
    for name, text in assets.items():
        _, _, theme = _split(name)
        for g in ET.fromstring(text).iter(SVG_NS + "g"):
            if g.get("data-t") is not None:
                seen[theme].add(g.get("fill"))
    for theme, fills in seen.items():
        assert fills, theme
        for fill in fills:
            assert contrast(fill, GITHUB_BG[theme]) >= 4.5, f"{theme} {fill}"
