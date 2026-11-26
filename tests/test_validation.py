"""Snapshot and content validation, hero/project guards, derived values, icons, slug and dependency pins."""
import json
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _copy(data):
    return json.loads(json.dumps(data))


def _gen_error(build, content, snap):
    with pytest.raises(build.BuildError) as ei:
        build.generate(snap, content)
    return str(ei.value)


# ---------------------------------------------------------------- snapshot shape

@pytest.mark.parametrize("key,value,expect", [
    ("today", "2026-13-40", "today"),
    ("today", "26/11/2026", "today"),
    ("today", 20261126, "today"),
    ("today", "2026-11-26T00:00:00", "today"),
    ("contributions_year", -1, "contributions_year"),
    ("contributions_year", True, "contributions_year"),
    ("contributions_year", "5", "contributions_year"),
    ("contributions_year", 1.5, "contributions_year"),
    ("weeks_26", "x", "weeks_26"),
    ("repos", [], "repos"),
])
def test_bad_top_level_values(build, content, snapshot_data, key, value, expect):
    snap = _copy(snapshot_data)
    snap[key] = value
    assert expect in _gen_error(build, content, snap)


@pytest.mark.parametrize("snap", [[], "x", 3, None])
def test_snapshot_not_an_object(build, content, snap):
    assert "objeto" in _gen_error(build, content, snap)


def test_weeks_must_be_exactly_26(build, content, snapshot_data):
    for weeks in (snapshot_data["weeks_26"] + [snapshot_data["weeks_26"][0]], snapshot_data["weeks_26"][:25]):
        snap = _copy(snapshot_data)
        snap["weeks_26"] = weeks
        assert "26" in _gen_error(build, content, snap)


@pytest.mark.parametrize("change,expect", [
    (lambda w: w.pop("start"), "weeks_26[3]"),
    (lambda w: w.pop("count"), "weeks_26[3]"),
    (lambda w: w.update(start="nope"), "weeks_26[3].start"),
    (lambda w: w.update(count=-2), "weeks_26[3].count"),
    (lambda w: w.update(count=True), "weeks_26[3].count"),
    (lambda w: w.update(count="4"), "weeks_26[3].count"),
])
def test_bad_week_entries(build, content, snapshot_data, change, expect):
    snap = _copy(snapshot_data)
    change(snap["weeks_26"][3])
    assert expect in _gen_error(build, content, snap)


def test_week_that_is_not_an_object(build, content, snapshot_data):
    snap = _copy(snapshot_data)
    snap["weeks_26"][0] = 7
    assert "weeks_26[0]" in _gen_error(build, content, snap)


@pytest.mark.parametrize("bad", ["2026-11-20<junk>", "2026-11-20T10:00:00Z", "2026-11-2", "", None, 5])
def test_pushed_at_must_be_strict_iso_date(build, content, snapshot_data, bad):
    snap = _copy(snapshot_data)
    snap["repos"]["nomad"]["pushed_at"] = bad
    assert "pushed_at" in _gen_error(build, content, snap)


def test_repo_without_pushed_at(build, content, snapshot_data):
    snap = _copy(snapshot_data)
    snap["repos"]["nomad"] = {}
    assert "pushed_at" in _gen_error(build, content, snap)


def test_missing_project_keeps_message(build, content, snapshot_data):
    snap = _copy(snapshot_data)
    del snap["repos"]["nomad"]
    assert "projeto nomad não está entre os repositórios públicos" in _gen_error(build, content, snap)


# ---------------------------------------------------------------- content shape

def test_missing_content_key_names_the_path(build, content, snapshot_data):
    bad = _copy(content)
    del bad["projects"][2]["desc_short"]
    assert "projects[2].desc_short" in _gen_error(build, bad, snapshot_data)
    bad = _copy(content)
    del bad["labels"]["hub"]
    assert "labels.hub" in _gen_error(build, bad, snapshot_data)
    bad = _copy(content)
    del bad["assets_base"]
    assert "assets_base" in _gen_error(build, bad, snapshot_data)


def test_stack_item_must_be_an_object_with_name_and_icon(build, content, snapshot_data):
    bad = _copy(content)
    bad["stack"][0]["items"][0] = "Python"
    assert "stack[0].items[0]" in _gen_error(build, bad, snapshot_data)
    bad = _copy(content)
    del bad["stack"][1]["items"][2]["icon"]
    assert "stack[1].items[2].icon" in _gen_error(build, bad, snapshot_data)


def test_new_labels_are_required(build, content, snapshot_data):
    for key in ("public_repos", "last_push", "most_recent"):
        bad = _copy(content)
        del bad["labels"][key]
        assert f"labels.{key}" in _gen_error(build, bad, snapshot_data)


def test_fixture_carries_every_public_repo_including_the_profile_repo(snapshot_data, content):
    assert len(snapshot_data["repos"]) == 11 and content["login"] in snapshot_data["repos"]
    assert all(set(r) == {"pushed_at"} for r in snapshot_data["repos"].values())


def test_main_reports_missing_content_key_without_traceback(build, snapshot_path, content, tmp_path, capsys, monkeypatch):
    bad = _copy(content)
    del bad["english"]["closing"]
    monkeypatch.setattr(build, "load_content", lambda *a: bad)
    assert build.main(["readme", "--check"]) == 1
    err = capsys.readouterr().err
    assert "english.closing" in err and "Traceback" not in err
    assert build.main(["assets", "--snapshot", str(snapshot_path), "--out", str(tmp_path / "d")]) == 1
    assert "english.closing" in capsys.readouterr().err


def test_main_only_catches_build_error(build, snapshot_path, tmp_path, monkeypatch):
    monkeypatch.setattr(build, "generate", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    with pytest.raises(KeyError):
        build.main(["assets", "--snapshot", str(snapshot_path), "--out", str(tmp_path / "d")])


# ---------------------------------------------------------------- slug

@pytest.mark.parametrize("slug", ["Upper", "-lead", "a b", "../x", "a.svg", "", "ação"])
def test_bad_slug(build, content, snapshot_data, slug):
    bad = _copy(content)
    bad["projects"][0]["slug"] = slug
    assert "slug" in _gen_error(build, bad, snapshot_data)


# ---------------------------------------------------------------- project pages

def _ctx(build, content, snap):
    build.check_projects(snap, content)
    return build.Ctx(snap, content)


def test_mobile_desc_short_too_long_for_two_lines(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][1]["desc_short"] = " ".join(["palavra"] * 40)
    msg = _gen_error(build, bad, snapshot_data)
    assert bad["projects"][1]["repo"] in msg and "desc_short" in msg


def test_mobile_desc_short_word_wider_than_box(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][1]["desc_short"] = "x" * 80
    assert "desc_short" in _gen_error(build, bad, snapshot_data)


def test_desktop_desc_that_does_not_fit_is_an_error_not_truncated(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][0]["desc"] = " ".join(["descricao"] * 30)
    msg = _gen_error(build, bad, snapshot_data)
    assert bad["projects"][0]["repo"] in msg and "desc não cabe" in msg and "desktop" in msg


def test_mobile_stack_line_that_does_not_fit_is_an_error(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][0]["stack"] = ["Python", "Shell", "FBInk", "Termux", "adb", "Docker"]
    msg = _gen_error(build, bad, snapshot_data)
    assert bad["projects"][0]["repo"] in msg and "stack" in msg


def test_desktop_stack_line_that_does_not_fit_is_an_error(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][0]["stack"] = ["uma-tecnologia-comprida"] * 8
    assert "stack" in _gen_error(build, bad, snapshot_data)


@pytest.mark.parametrize("device", ["desktop", "mobile"])
def test_title_touching_the_date_is_an_error(build, content, snapshot_data, device):
    bad = _copy(content)
    bad["projects"][0]["title"] = "um-titulo-comprido-demais-para-dividir-a-linha-com-a-data"
    ctx = _ctx(build, bad, snapshot_data)
    with pytest.raises(build.BuildError) as ei:
        build.proj(ctx, device, "dark", bad["projects"][0])
    assert "título" in str(ei.value) and device in str(ei.value)


def test_short_leader_is_dropped_not_an_error(build, content, snapshot_data):
    # mobile titles leave under 30 px for the dotted leader: the line is left out, the page is still built
    ctx = _ctx(build, content, snapshot_data)
    p = next(p for p in content["projects"] if p["slug"] == "tailnet-dns-failover")
    build.proj(ctx, "mobile", "dark", p)
    assert "stroke-dasharray=\"1 5\"" not in ctx.files["proj-tailnet-dns-failover-mobile-dark.svg"].decode()


def test_project_without_a_pixel_icon_is_an_error(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][0]["slug"] = "sem-icone"
    assert "diferem" in _gen_error(build, bad, snapshot_data)


# ---------------------------------------------------------------- hero

def test_mobile_name_that_needs_less_than_28px_is_an_error(build, content, snapshot_data):
    bad = _copy(content)
    bad["name"] = "Bruno Oliveira de Albuquerque Maranhão"
    ctx = _ctx(build, bad, snapshot_data)
    with pytest.raises(build.BuildError) as ei:
        build.hero(ctx, "mobile", "dark")
    assert "28" in str(ei.value)


def test_mobile_name_that_fits_at_28_to_34_shrinks_without_error(build, content, snapshot_data):
    ok = _copy(content)
    ok["name"] = "Bruno Oliveira Jr"
    ctx = _ctx(build, ok, snapshot_data)
    build.hero(ctx, "mobile", "dark")
    sizes = re.findall(r'data-fs="([\d.]+)" data-t="Bruno Oliveira Jr"', ctx.files["hero-mobile-dark.svg"].decode())
    assert sizes and 28 <= float(sizes[0]) < 34


def test_desktop_name_touching_the_indicators_is_an_error(build, content, snapshot_data):
    bad = _copy(content)
    bad["name"] = "Bruno Oliveira de Albuquerque Maranhão"
    ctx = _ctx(build, bad, snapshot_data)
    with pytest.raises(build.BuildError) as ei:
        build.hero(ctx, "desktop", "dark")
    assert "indicadores" in str(ei.value)


@pytest.mark.parametrize("device", ["desktop", "mobile"])
def test_indicator_label_and_value_overlapping_is_an_error(build, content, snapshot_data, device):
    bad = _copy(content)
    bad["labels"]["most_recent"] = "o repositório com push mais recente de todos"
    snap = _copy(snapshot_data)
    snap["repos"]["um-repositorio-com-um-nome-extremamente-comprido"] = {"pushed_at": "2026-11-26"}
    ctx = _ctx(build, bad, snap)
    ctx.last_push = {"repo": "um-repositorio-com-um-nome-extremamente-comprido", "date": "2026-11-26"}
    with pytest.raises(build.BuildError) as ei:
        build.hero(ctx, device, "dark")
    assert "indicador" in str(ei.value) and device in str(ei.value)


def test_generate_wraps_unknown_glyph_in_build_error(build, content, snapshot_data):
    bad = _copy(content)
    bad["labels"]["hub"] = "\u4e2d"  # no glyph in Departure Mono
    assert "glifo" in _gen_error(build, bad, snapshot_data)


# ---------------------------------------------------------------- derived snapshot values

def test_public_repos_is_the_number_of_repos(build, snapshot_data, content):
    ctx = build.Ctx(snapshot_data, content)
    assert ctx.public_repos == len(snapshot_data["repos"]) == 11


def test_most_recent_push_excludes_the_profile_repo(build, snapshot_data, content):
    snap = _copy(snapshot_data)
    snap["repos"][content["login"]]["pushed_at"] = "2026-12-31"  # the daily publish pushes here
    ctx = build.Ctx(snap, content)
    assert ctx.last_push == {"repo": "galaxy-s10", "date": "2026-11-26"}


def test_most_recent_push_ties_break_by_name_ascending(build):
    repos = {"zeta": {"pushed_at": "2026-11-20"}, "alpha": {"pushed_at": "2026-11-20"},
             "mid": {"pushed_at": "2026-11-19"}, "me": {"pushed_at": "2026-11-25"}}
    assert build.derive_status(repos, "me") == (4, {"repo": "alpha", "date": "2026-11-20"})


def test_no_repo_besides_the_profile_is_an_error(build):
    with pytest.raises(build.BuildError):
        build.derive_status({"me": {"pushed_at": "2026-11-25"}}, "me")


def test_profile_repo_is_excluded_whatever_the_login_case(build):
    repos = {"Brno01": {"pushed_at": "2026-11-26"}, "nomad": {"pushed_at": "2026-11-20"}}
    assert build.derive_status(repos, "brno01") == (2, {"repo": "nomad", "date": "2026-11-20"})


def test_every_repo_needs_a_valid_pushed_at(build, content, snapshot_data):
    snap = _copy(snapshot_data)
    snap["repos"]["remove-bg"] = {"pushed_at": "ontem"}
    assert "remove-bg" in _gen_error(build, content, snap)


def test_hero_shows_derived_values(assets):
    texts = _texts(assets["hero-desktop-dark.svg"])
    assert {"11", "26 nov", "galaxy-s10", "projetos públicos", "último push", "mais recente"} <= set(texts)


def test_project_page_date_comes_from_its_repo(assets):
    assert "último push 20 nov" in _texts(assets["proj-dot-github-desktop-dark.svg"])
    assert "20 nov" in _texts(assets["proj-dot-github-mobile-dark.svg"])
    assert "último push 26 nov" in _texts(assets["proj-galaxy-s10-desktop-light.svg"])


def _texts(svg):
    import xml.etree.ElementTree as ET
    return [g.get("data-t") for g in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}g") if g.get("data-t") is not None]


# ---------------------------------------------------------------- stack icons

def _icons_copy(tmp_path):
    dst = tmp_path / "icons"
    shutil.copytree(ROOT / "tools" / "icons", dst)
    return dst


def test_every_stack_icon_has_a_source_and_metadata(build, content):
    meta = build.load_icon_meta()
    slugs = build.icon_slugs(content)
    assert len(slugs) == 27
    for slug in slugs:
        assert slug in meta
        assert build.read_icon_path(slug)


def test_unknown_icon_in_content_is_an_error(build, content, snapshot_data):
    bad = _copy(content)
    bad["stack"][0]["items"][0]["icon"] = "naoexiste"
    assert "naoexiste" in _gen_error(build, bad, snapshot_data)


def test_icon_slug_must_be_a_safe_file_name(build, content, snapshot_data):
    bad = _copy(content)
    bad["stack"][0]["items"][0]["icon"] = "../x"
    assert "inválido" in _gen_error(build, bad, snapshot_data)


def test_icon_with_metadata_but_no_svg_file(build, content, snapshot_data, tmp_path):
    icons = _icons_copy(tmp_path)
    (icons / "python.svg").unlink()
    ctx = build.Ctx(snapshot_data, content)
    with pytest.raises(build.BuildError, match="python"):
        build.emit_icons(ctx, icons)


@pytest.mark.parametrize("svg,expect", [
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><title>x</title><path d="M0 0"/></svg>', "viewBox"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0"/></svg>', "title"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><title>x</title><path d="M0 0"/><path d="M1 1"/></svg>', "title"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><title>x</title><circle r="1"/></svg>', "title"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><title>x</title><path d="M0 0 &lt;script&gt;"/></svg>', "caracteres"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><title>x</title><path d="M0 0 e5"/></svg>', "caracteres"),
    ('<div/>', "<svg>"),
    ('<svg', "ler"),
])
def test_icon_source_is_validated_strictly(build, tmp_path, svg, expect):
    (tmp_path / "bad.svg").write_text(svg, encoding="utf-8")
    with pytest.raises(build.BuildError, match=expect):
        build.read_icon_path("bad", tmp_path)


@pytest.mark.parametrize("meta,expect", [
    ('{"source": "x", "icons": {"a": {"hex": "12345", "letters": null}}}', "hex"),
    ('{"source": "x", "icons": {"a": {"hex": "123456", "letters": "zz"}}}', "letters"),
    ('{"source": "x", "icons": {"a": {"hex": "123456"}}}', "letters"),
    ('{"source": "x", "icons": []}', "icons"),
    ('{"icons": {}}', "source"),
    ('{', "ler"),
])
def test_icons_json_is_validated(build, tmp_path, meta, expect):
    (tmp_path / "icons.json").write_text(meta, encoding="utf-8")
    with pytest.raises(build.BuildError, match=expect):
        build.load_icon_meta(tmp_path)


def test_icons_json_records_the_pinned_version():
    data = json.loads((ROOT / "tools" / "icons" / "icons.json").read_text(encoding="utf-8"))
    assert data["source"].startswith("simple-icons 16.37.0;")
    assert (ROOT / "tools" / "icons" / "LICENSE.md").read_text(encoding="utf-8").startswith("# CC0")


def test_icon_colour_rules(build):
    chip = {"light": "f6f8fa", "dark": "151b23"}
    for theme in ("light", "dark"):
        for hexcolor in ("3776AB", "F7DF1E", "FCC624", "242424", "000000", "D97757", "E0234E"):
            fill = build.icon_color(hexcolor, theme)
            assert build.contrast_ratio(fill, chip[theme]) >= 3.5, (theme, hexcolor, fill)
    assert build.icon_color("000000", "dark") == "e6edf3"  # near-black marks turn light on dark
    assert build.icon_color("242424", "dark") == "e6edf3"
    assert build.icon_color("3776AB", "light") == "3776ab"  # already readable: untouched
    assert build.icon_color("FCC624", "light") != "fcc624"  # yellow on a white chip is darkened


def test_letter_icons_keep_the_brand_colour_on_a_backing_square(build):
    meta = build.load_icon_meta()
    d = build.read_icon_path("typescript")
    for theme in ("light", "dark"):
        svg = build.icon_svg("typescript", meta["typescript"], d, theme).decode()
        assert '<rect x="1" y="1" width="22" height="22" fill="#ffffff"/>' in svg and 'fill="#3178c6"' in svg
    js = build.icon_svg("javascript", meta["javascript"], build.read_icon_path("javascript"), "dark").decode()
    assert 'fill="#1a1a18"' in js and 'fill="#f7df1e"' in js


# ---------------------------------------------------------------- dependency pins

def _pin(text, name):
    m = re.search(rf"^{name}==([0-9][^\s\\\"']*)", text, re.M)
    assert m, f"{name} pin not found"
    return m.group(1)


def test_fonttools_pin_is_the_same_everywhere():
    header = (ROOT / "tools" / "build.py").read_text(encoding="utf-8")
    m = re.search(r'dependencies = \["fonttools==([^"]+)"\]', header)
    assert m, "PEP 723 header of build.py has no fonttools pin"
    assert m[1] == _pin((ROOT / "tools" / "requirements.in").read_text(encoding="utf-8"), "fonttools")
    assert m[1] == _pin((ROOT / "tools" / "requirements.txt").read_text(encoding="utf-8"), "fonttools")


def test_requirements_txt_has_hashes_and_no_absolute_path():
    text = (ROOT / "tools" / "requirements.txt").read_text(encoding="utf-8")
    assert "--hash=sha256:" in text
    assert "/home/" not in text and "/Users/" not in text


@pytest.mark.parametrize("icon", ['x" onerror="y', "Python", "two words"])
def test_readme_refuses_an_unsafe_icon_slug(build, content, icon):
    bad = _copy(content)
    bad["stack"][0]["items"][0]["icon"] = icon
    with pytest.raises(build.BuildError, match="inválido"):
        build.validate_content(bad)


def test_generate_checks_the_number_of_files(build, content, snapshot_data, monkeypatch):
    monkeypatch.setattr(build, "emit_icons", lambda ctx: None)
    assert "esperava 82" in _gen_error(build, content, snapshot_data)


def test_project_details_are_required(build, content, snapshot_data):
    bad = _copy(content)
    del bad["projects"][0]["details"]
    assert "details" in _gen_error(build, bad, snapshot_data)
