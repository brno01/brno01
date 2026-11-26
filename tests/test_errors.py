"""build.py assets: bad inputs exit 1 with a message on stderr and never touch --out."""
import hashlib
import json

import pytest


def _digest(directory):
    h = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        h.update(p.name.encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _run(build, snap_path, out, capsys):
    code = build.main(["assets", "--snapshot", str(snap_path), "--out", str(out)])
    return code, capsys.readouterr().err


def _write(tmp_path, data):
    p = tmp_path / "snap.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


def _broken(snapshot_data, **changes):
    snap = json.loads(json.dumps(snapshot_data))
    for k, v in changes.items():
        if v is KeyError:
            del snap[k]
        else:
            snap[k] = v
    return snap


def _assert_failed_cleanly(build, snap_path, tmp_path, capsys, expect=None):
    out = tmp_path / "newparent" / "dist"
    code, err = _run(build, snap_path, out, capsys)
    assert code == 1
    assert err.strip() and "Traceback" not in err
    if expect:
        assert expect in err
    assert not out.exists() and not out.parent.exists()


def test_missing_snapshot_file(build, tmp_path, capsys):
    _assert_failed_cleanly(build, tmp_path / "nope.json", tmp_path, capsys, "snapshot")


def test_invalid_json(build, tmp_path, capsys):
    _assert_failed_cleanly(build, _write(tmp_path, "{not json"), tmp_path, capsys, "snapshot")


@pytest.mark.parametrize("key", ["today", "weeks_26", "contributions_year", "repos"])
def test_missing_required_key(build, snapshot_data, tmp_path, capsys, key):
    _assert_failed_cleanly(build, _write(tmp_path, _broken(snapshot_data, **{key: KeyError})), tmp_path, capsys)


def test_snapshot_top_level_not_an_object(build, tmp_path, capsys):
    _assert_failed_cleanly(build, _write(tmp_path, "[]"), tmp_path, capsys)


def test_invalid_today_date(build, snapshot_data, tmp_path, capsys):
    _assert_failed_cleanly(build, _write(tmp_path, _broken(snapshot_data, today="not-a-date")), tmp_path, capsys)


@pytest.mark.parametrize("n", [0, 1, 25])
def test_weeks_26_too_short(build, snapshot_data, tmp_path, capsys, n):
    snap = _broken(snapshot_data, weeks_26=snapshot_data["weeks_26"][:n])
    _assert_failed_cleanly(build, _write(tmp_path, snap), tmp_path, capsys)


def test_content_projects_differ_from_drawn_icons(build, snapshot_data, content, tmp_path, capsys, monkeypatch):
    bad = json.loads(json.dumps(content))
    bad["projects"][0] = dict(bad["projects"][0], repo="alpha", slug="alpha")
    snap = _broken(snapshot_data)
    snap["repos"]["alpha"] = {"pushed_at": "2026-11-20"}
    monkeypatch.setattr(build, "load_content", lambda *a: bad)
    _assert_failed_cleanly(build, _write(tmp_path, snap), tmp_path, capsys, "diferem")


def test_content_with_fewer_projects(build, snapshot_data, content, tmp_path, capsys, monkeypatch):
    bad = json.loads(json.dumps(content))
    bad["projects"] = bad["projects"][:5]
    monkeypatch.setattr(build, "load_content", lambda *a: bad)
    _assert_failed_cleanly(build, _write(tmp_path, snapshot_data), tmp_path, capsys, "diferem")


@pytest.mark.parametrize("case", ["missing_file", "bad_json", "no_today", "no_weeks", "no_year", "missing_repo"])
def test_existing_out_stays_intact_on_failure(build, snapshot_path, snapshot_data, tmp_path, capsys, case):
    out = tmp_path / "dist"
    assert build.main(["assets", "--snapshot", str(snapshot_path), "--out", str(out)]) == 0
    capsys.readouterr()
    before = _digest(out)
    snap = json.loads(json.dumps(snapshot_data))
    if case == "missing_file":
        path = tmp_path / "nope.json"
    elif case == "bad_json":
        path = _write(tmp_path, "{")
    else:
        if case == "missing_repo":
            del snap["repos"]["nomad"]
        else:
            del snap[{"no_today": "today", "no_weeks": "weeks_26", "no_year": "contributions_year"}[case]]
        path = _write(tmp_path, snap)
    code, err = _run(build, path, out, capsys)
    assert code == 1 and err.strip() and "Traceback" not in err
    assert _digest(out) == before
    assert len(list(out.iterdir())) == 58
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".build-")]


def test_content_mismatch_keeps_existing_out(build, snapshot_path, snapshot_data, content, tmp_path, capsys, monkeypatch):
    out = tmp_path / "dist"
    assert build.main(["assets", "--snapshot", str(snapshot_path), "--out", str(out)]) == 0
    capsys.readouterr()
    before = _digest(out)
    bad = json.loads(json.dumps(content))
    bad["projects"] = bad["projects"][:5]
    monkeypatch.setattr(build, "load_content", lambda *a: bad)
    code, err = _run(build, snapshot_path, out, capsys)
    assert code == 1 and err.strip()
    assert _digest(out) == before
