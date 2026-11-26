"""Active/idle logic: 14-day window, hero line, pulse animation and dots."""
import datetime
import json
import xml.etree.ElementTree as ET

import pytest

SVG_NS = "{http://www.w3.org/2000/svg}"
TODAY = "2026-11-26"


def _snap(snapshot_data, ages):
    """Copy of the fixture where repo i (in content order) was pushed ages[i] days ago."""
    snap = json.loads(json.dumps(snapshot_data))
    snap["today"] = TODAY
    base = datetime.date.fromisoformat(TODAY)
    for repo, age in ages.items():
        snap["repos"][repo]["pushed_at"] = (base - datetime.timedelta(days=age)).isoformat()
    return snap


def _generate(build, content, snap):
    """Cheap partial run: only the hero and the first project strip, all devices and themes."""
    build.check_projects(snap, content)
    ctx = build.Ctx(snap, content)
    for dev in ("desktop", "mobile"):
        for th in ("dark", "light"):
            build.hero(ctx, dev, th)
            build.strip(ctx, dev, th, 0, ctx.projects[0])
    return ctx


def _texts(svg):
    return [g.get("data-t") for g in ET.fromstring(svg).iter(SVG_NS + "g") if g.get("data-t") is not None]


@pytest.mark.parametrize("age,active", [(0, True), (13, True), (14, True), (15, False), (400, False)])
def test_active_window_boundary(build, content, snapshot_data, age, active):
    repo = content["projects"][0]["repo"]
    ctx = build.Ctx(_snap(snapshot_data, {repo: age}), content)
    assert ctx.active[repo] is active


def test_days_ago_uses_date_part_of_timestamp(build):
    assert build.Ctx.days_ago("2026-11-12T23:59:59Z", "2026-11-26") == 14
    assert build.Ctx.days_ago("2026-11-26", "2026-11-26") == 0


def _all(content, age):
    return {p["repo"]: age for p in content["projects"]}


def _hero_line(ctx, device="desktop", theme="dark"):
    return [t for t in _texts(ctx.files[f"hero-{device}-{theme}.svg"].decode()) if "6" in t and "14" in t]


@pytest.mark.parametrize("n", [0, 1, 3, 5, 6])
def test_hero_counts_active_projects(build, content, snapshot_data, n):
    repos = [p["repo"] for p in content["projects"]]
    ages = {r: (0 if i < n else 15) for i, r in enumerate(repos)}
    ctx = _generate(build, content, _snap(snapshot_data, ages))
    assert ctx.n_active == n
    for device in ("desktop", "mobile"):
        for theme in ("dark", "light"):
            lines = _hero_line(ctx, device, theme)
            assert len(lines) == 1, (device, theme, lines)
            assert lines[0].startswith(f"{n} de 6 "), lines[0]


def test_hero_line_for_fixture(assets):
    # fixture: all six pushed within 6 days of 2026-11-26
    for name in ("hero-desktop-dark.svg", "hero-mobile-light.svg"):
        assert any(t.startswith("6 de 6 ") for t in _texts(assets[name]))


def _pulses(svg):
    return [e for e in ET.fromstring(svg).iter(SVG_NS + "path") if e.get("class") == "pulse"]


def _dashed_wires(svg):
    return [e for e in ET.fromstring(svg).iter(SVG_NS + "path") if e.get("stroke-dasharray") == "2 5"]


def test_all_active_all_edges_pulse(build, content, snapshot_data):
    ctx = _generate(build, content, _snap(snapshot_data, _all(content, 0)))
    svg = ctx.files["hero-desktop-dark.svg"].decode()
    assert len(_pulses(svg)) == len(build.EDGES) == 8
    assert not _dashed_wires(svg)


def test_all_idle_no_pulse_and_dashed_wires(build, content, snapshot_data):
    ctx = _generate(build, content, _snap(snapshot_data, _all(content, 30)))
    for device in ("desktop", "mobile"):
        svg = ctx.files[f"hero-{device}-dark.svg"].decode()
        assert _pulses(svg) == []
        assert 'class="halo"' not in svg
        assert len(_dashed_wires(svg)) == len(build.EDGES)


def test_idle_project_loses_its_pulses(build, content, snapshot_data):
    active = _generate(build, content, _snap(snapshot_data, _all(content, 0)))
    one_idle = _generate(build, content, _snap(snapshot_data, {**_all(content, 0), "sinapses": 15}))
    a = len(_pulses(active.files["hero-desktop-dark.svg"].decode()))
    b = len(_pulses(one_idle.files["hero-desktop-dark.svg"].decode()))
    assert b < a
    assert len(_dashed_wires(one_idle.files["hero-desktop-dark.svg"].decode())) == a - b


def _accent_dots(svg, accent):
    return [e for e in ET.fromstring(svg).iter(SVG_NS + "circle")
            if e.get("fill") == accent and e.get("class") is None]


def test_idle_strip_has_no_active_dot_but_active_has(build, content, snapshot_data):
    accent = build.PALETTE["dark"]["accent"]
    slug = content["projects"][0]["slug"]
    repo = content["projects"][0]["repo"]
    idle = _generate(build, content, _snap(snapshot_data, {repo: 15}))
    live = _generate(build, content, _snap(snapshot_data, {repo: 14}))
    for device in ("desktop", "mobile"):
        name = f"proj-{slug}-{device}-dark.svg"
        assert _accent_dots(idle.files[name].decode(), accent) == []
        assert len(_accent_dots(live.files[name].decode(), accent)) >= 1


def test_idle_hero_node_is_hollow(build, content, snapshot_data):
    ages = _all(content, 0)
    ages["nomad"] = 15
    accent = build.PALETTE["dark"]["accent"]
    ctx = _generate(build, content, _snap(snapshot_data, ages))
    full = _generate(build, content, _snap(snapshot_data, _all(content, 0)))
    n_idle = len(_accent_dots(ctx.files["hero-desktop-dark.svg"].decode(), accent))
    n_full = len(_accent_dots(full.files["hero-desktop-dark.svg"].decode(), accent))
    assert n_idle == n_full - 1
