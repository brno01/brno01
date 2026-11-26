"""Snapshot and content validation, strip/hero guards, days_ago, slug and dependency pins."""
import datetime
import json
import re
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
    del bad["footer"]
    assert "footer" in _gen_error(build, bad, snapshot_data)


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


# ---------------------------------------------------------------- strips

def test_mobile_desc_short_too_long_for_two_lines(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][1]["desc_short"] = " ".join(["palavra"] * 40)
    msg = _gen_error(build, bad, snapshot_data)
    assert bad["projects"][1]["repo"] in msg and "desc_short" in msg


def test_mobile_desc_short_word_wider_than_box(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][1]["desc_short"] = "x" * 80
    assert "desc_short" in _gen_error(build, bad, snapshot_data)


def test_desktop_title_and_kind_touch_the_stack(build, content, snapshot_data):
    bad = _copy(content)
    bad["projects"][0]["stack"] = ["Python"] * 12
    bad["projects"][0]["title"] = "um-titulo-muito-comprido-para-a-faixa"
    msg = _gen_error(build, bad, snapshot_data)
    assert bad["projects"][0]["repo"] in msg and "stack" in msg


# ---------------------------------------------------------------- hero

def test_hero_label_collision_is_an_error(build, content, snapshot_data, monkeypatch):
    geo = json.loads(json.dumps({k: {**v, "nodes": {n: list(p) for n, p in v["nodes"].items()}}
                                 for k, v in build.HERO.items()}))
    geo["desktop"]["nodes"]["nomad"] = [512, 228, "down"]  # same spot as tailnet-dns-failover
    for v in geo.values():
        v["nodes"] = {n: tuple(p) for n, p in v["nodes"].items()}
        v["hub"] = tuple(v["hub"])
        v["k"] = tuple(v["k"])
        v["hub_label"] = tuple(v["hub_label"])
    monkeypatch.setattr(build, "HERO", geo)
    assert "colide" in _gen_error(build, content, snapshot_data)


def test_hero_label_out_of_band_is_an_error(build, content, snapshot_data, monkeypatch):
    geo = {k: dict(v) for k, v in build.HERO.items()}
    geo["desktop"]["x_lo"] = 700
    monkeypatch.setattr(build, "HERO", geo)
    assert "faixa" in _gen_error(build, content, snapshot_data)


def test_hero_wire_crossing_label_is_an_error(build, content, snapshot_data, monkeypatch):
    geo = {k: dict(v) for k, v in build.HERO.items()}
    geo["desktop"]["hub_label"] = ("middle", 0, 4)  # the hub label sits on top of the wires leaving the hub
    monkeypatch.setattr(build, "HERO", geo)
    assert "cruza" in _gen_error(build, content, snapshot_data)


def test_hero_fallback_line_derives_count_and_window(build, content, snapshot_data, monkeypatch):
    long = _copy(content)
    long["labels"]["active_line"] = "{n} " + "projetos com push recentemente " * 5
    monkeypatch.setattr(build, "ACTIVE_DAYS", 21)
    ctx = build.generate(snapshot_data, long)
    import xml.etree.ElementTree as ET
    texts = [g.get("data-t") for g in ET.fromstring(ctx.files["hero-desktop-dark.svg"]).iter("{http://www.w3.org/2000/svg}g")]
    assert f"6 de {len(content['projects'])} ativos em 21 dias" in texts


# ---------------------------------------------------------------- days_ago

def test_days_ago_future_push_counts_as_zero(build):
    assert build.Ctx.days_ago("2026-11-27", "2026-11-26") == 0
    assert build.Ctx.days_ago("2027-01-01T00:00:00Z", "2026-11-26") == 0


def test_future_push_is_active(build, content, snapshot_data):
    snap = _copy(snapshot_data)
    snap["repos"]["nomad"]["pushed_at"] = (datetime.date.fromisoformat(snap["today"]) + datetime.timedelta(days=1)).isoformat()
    assert build.Ctx(snap, content).active["nomad"] is True


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
