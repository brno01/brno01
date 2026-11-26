"""Smoke tests for the profile generator. Run: uv run --with pytest --with fonttools==4.69.1 pytest -q"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load("build")
snapshot = _load("snapshot")
SNAPSHOT = FIXTURES / "snapshot.json"


def _digest(directory: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(directory.iterdir()):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def test_generates_130_files_from_fixture(tmp_path):
    out = tmp_path / "dist"
    assert build.main(["assets", "--snapshot", str(SNAPSHOT), "--out", str(out)]) == 0
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == 130
    assert "hero-desktop-dark.svg" in files and "icon-python-light.svg" in files
    assert all(p.stat().st_size <= 60_000 for p in out.iterdir())
    assert not any(p.suffix != ".svg" for p in out.iterdir())


def test_output_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        assert build.main(["assets", "--snapshot", str(SNAPSHOT), "--out", str(d)]) == 0
    assert _digest(a) == _digest(b)


def test_readme_is_up_to_date():
    assert build.main(["readme", "--check"]) == 0


def test_readme_check_fails_when_content_changes(monkeypatch):
    content = build.load_content()
    content["lede"] = content["lede"] + " (alterado)"
    monkeypatch.setattr(build, "load_content", lambda *a: content)
    assert build.main(["readme", "--check"]) == 1


def test_missing_project_fails_without_writing(tmp_path, capsys):
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    del snap["repos"]["nomad"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(snap), encoding="utf-8")
    out = tmp_path / "dist"
    assert build.main(["assets", "--snapshot", str(bad), "--out", str(out)]) == 1
    assert "projeto nomad não está entre os repositórios públicos de brno01" in capsys.readouterr().err
    assert not out.exists()


def test_snapshot_from_recorded_response():
    resp = json.loads((FIXTURES / "graphql_response.json").read_text(encoding="utf-8"))
    snap = snapshot.snapshot_from_user(resp["data"]["user"], "2026-11-26")
    assert snap["today"] == "2026-11-26"
    assert snap["contributions_year"] == 321
    assert len(snap["weeks_26"]) == 26
    assert snap["weeks_26"][-1]["start"] == resp["data"]["user"]["contributionsCollection"][
        "contributionCalendar"]["weeks"][-1]["firstDay"]
    # 02:30 UTC is still the previous day in Sao Paulo; a repo without pushes is dropped
    assert snap["repos"] == {"alpha": {"pushed_at": "2026-11-25"}, "beta": {"pushed_at": "2026-11-20"}}


def test_snapshot_rejects_short_calendar():
    resp = json.loads((FIXTURES / "graphql_response.json").read_text(encoding="utf-8"))
    user = resp["data"]["user"]
    user["contributionsCollection"]["contributionCalendar"]["weeks"] = user[
        "contributionsCollection"]["contributionCalendar"]["weeks"][:10]
    with pytest.raises(snapshot.SnapshotError):
        snapshot.snapshot_from_user(user, "2026-11-26")


def test_snapshot_without_token_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    out = tmp_path / "s.json"
    assert snapshot.main(["--out", str(out)]) == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    assert not out.exists()
