"""tools/snapshot.py: pagination, error mapping, token hygiene, time zone, CLI."""
import io
import json
import urllib.error
import urllib.request

import pytest

TOKEN = "ghp_secretTOKEN123"


def _user(names, has_next, cursor=None):
    return {
        "contributionsCollection": {"contributionCalendar": {
            "totalContributions": 5,
            "weeks": [{"firstDay": f"2026-01-{i + 1:02d}", "contributionDays": [{"contributionCount": 1}]}
                      for i in range(26)]}},
        "repositories": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                         "nodes": [{"name": n, "pushedAt": "2026-11-20T12:00:00Z"} for n in names]},
    }


def test_pagination_merges_pages_and_passes_cursor(snapmod):
    calls = []
    pages = [{"user": _user(["alpha", "beta"], True, "CUR1")}, {"user": _user(["gamma"], False, None)}]

    def post(query, variables, token):
        calls.append((variables, token))
        return pages[len(calls) - 1]

    user = snapmod.fetch_user("someone", TOKEN, post=post)
    assert [n["name"] for n in user["repositories"]["nodes"]] == ["alpha", "beta", "gamma"]
    assert [c[0]["after"] for c in calls] == [None, "CUR1"]
    assert all(c[0]["login"] == "someone" and c[1] == TOKEN for c in calls)
    snap = snapmod.snapshot_from_user(user, "2026-11-26")
    assert set(snap["repos"]) == {"alpha", "beta", "gamma"}


def test_single_page_makes_one_call(snapmod):
    n = []
    snapmod.fetch_user("x", TOKEN, post=lambda q, v, t: n.append(1) or {"user": _user(["alpha"], False)})
    assert n == [1]


def test_missing_user_is_snapshot_error(snapmod):
    with pytest.raises(snapmod.SnapshotError, match="não encontrado"):
        snapmod.fetch_user("ghost", TOKEN, post=lambda q, v, t: {"user": None})


def test_missing_user_on_second_page_is_error(snapmod):
    pages = iter([{"user": _user(["alpha"], True, "C")}, {"user": None}])
    with pytest.raises(snapmod.SnapshotError):
        snapmod.fetch_user("x", TOKEN, post=lambda q, v, t: next(pages))


def test_snapshot_from_user_none(snapmod):
    with pytest.raises(snapmod.SnapshotError):
        snapmod.snapshot_from_user(None, "2026-11-26")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _urlopen_returning(body: bytes):
    return lambda req, timeout=None: _Resp(body)


def test_http_401_maps_to_error_without_token(snapmod, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(snapmod.SnapshotError) as ei:
        snapmod._post("q", {}, TOKEN)
    assert "401" in str(ei.value)
    assert TOKEN not in str(ei.value)
    assert TOKEN not in repr(ei.value)


def test_network_failure_maps_to_error_without_token(snapmod, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(snapmod.SnapshotError) as ei:
        snapmod._post("q", {}, TOKEN)
    assert TOKEN not in str(ei.value)


def test_timeout_maps_to_error(snapmod, monkeypatch):
    def boom(req, timeout=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(snapmod.SnapshotError):
        snapmod._post("q", {}, TOKEN)


def test_token_sent_as_bearer_header_only(snapmod, monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        seen["url"] = req.full_url
        seen["body"] = req.data.decode()
        return _Resp(json.dumps({"data": {"ok": 1}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert snapmod._post("q", {"login": "x"}, TOKEN) == {"ok": 1}
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert TOKEN not in seen["url"] and TOKEN not in seen["body"]


def test_graphql_errors_map_to_error(snapmod, monkeypatch):
    body = json.dumps({"errors": [{"message": "Bad credentials"}], "data": None}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(body))
    with pytest.raises(snapmod.SnapshotError, match="Bad credentials"):
        snapmod._post("q", {}, TOKEN)


def test_non_json_response_maps_to_error(snapmod, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(b"<html>gateway</html>"))
    with pytest.raises(snapmod.SnapshotError, match="JSON"):
        snapmod._post("q", {}, TOKEN)


@pytest.mark.parametrize("body", [b"{}", b"[]", b"null"])
def test_json_without_data_maps_to_error(snapmod, monkeypatch, body):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(body))
    with pytest.raises(snapmod.SnapshotError):
        snapmod._post("q", {}, TOKEN)


@pytest.mark.parametrize("iso,expected", [
    ("2026-11-26T02:59:59Z", "2026-11-25"),   # 23:59:59 in Sao Paulo
    ("2026-11-26T03:00:00Z", "2026-11-26"),   # 00:00:00 in Sao Paulo
    ("2026-11-26T02:30:00Z", "2026-11-25"),
    ("2026-11-26T15:00:00Z", "2026-11-26"),
    ("2026-01-01T00:30:00Z", "2025-12-31"),   # year boundary
])
def test_local_date_around_sao_paulo_midnight(snapmod, iso, expected):
    assert snapmod.local_date(iso) == expected


def test_main_rejects_invalid_today(snapmod, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(snapmod, "fetch_user", lambda *a, **k: pytest.fail("must not fetch"))
    out = tmp_path / "s.json"
    for bad in ("2026-13-40", "yesterday", "2026/11/26"):
        assert snapmod.main(["--out", str(out), "--today", bad]) == 1
        err = capsys.readouterr().err
        assert "--today" in err and TOKEN not in err
    assert not out.exists()


def test_main_writes_snapshot_with_fake_fetch(snapmod, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    got = {}

    def fake_fetch(login, token, post=None):
        got.update(login=login, token=token)
        return _user(["alpha", "beta"], False)

    monkeypatch.setattr(snapmod, "fetch_user", fake_fetch)
    out = tmp_path / "nested" / "s.json"
    assert snapmod.main(["--out", str(out), "--today", "2026-11-26"]) == 0
    snap = json.loads(out.read_text(encoding="utf-8"))
    assert snap["today"] == "2026-11-26"
    assert snap["repos"] == {"alpha": {"pushed_at": "2026-11-20"}, "beta": {"pushed_at": "2026-11-20"}}
    assert len(snap["weeks_26"]) == 26 and snap["contributions_year"] == 5
    assert got["token"] == TOKEN and got["login"]
    assert TOKEN not in capsys.readouterr().out


def test_main_uses_gh_token_fallback(snapmod, monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "fallback")
    seen = []
    monkeypatch.setattr(snapmod, "fetch_user", lambda l, t, post=None: seen.append(t) or _user(["alpha"], False))
    assert snapmod.main(["--out", str(tmp_path / "s.json")]) == 0
    assert seen == ["fallback"]


def test_main_api_error_exits_1_and_writes_nothing(snapmod, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)

    def fail(*a, **k):
        raise snapmod.SnapshotError("a API do GitHub respondeu HTTP 401")

    monkeypatch.setattr(snapmod, "fetch_user", fail)
    out = tmp_path / "s.json"
    assert snapmod.main(["--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "401" in err and TOKEN not in err
    assert not out.exists()


@pytest.mark.parametrize("data", [
    {"user": {"contributionsCollection": None}},
    {"user": {"contributionsCollection": {"contributionCalendar": {"weeks": "x"}}}},
    {"user": {"contributionsCollection": {"contributionCalendar": {"totalContributions": 1, "weeks": [{}]}}}},
    {"user": {"repositories": 3}},
])
def test_unexpected_structure_maps_to_error(snapmod, data):
    with pytest.raises(snapmod.SnapshotError, match="resposta inesperada") as ei:
        snapmod.snapshot_from_user(data["user"], "2026-11-26")
    assert TOKEN not in str(ei.value)


@pytest.mark.parametrize("page", [
    {"user": {"repositories": None}},
    {"user": {"repositories": {"nodes": []}}},          # no pageInfo
    {"user": {"repositories": {"nodes": [], "pageInfo": []}}},
    {"user": "oops"},
    [],
])
def test_fetch_user_unexpected_structure_maps_to_error(snapmod, page):
    with pytest.raises(snapmod.SnapshotError, match="resposta inesperada"):
        snapmod.fetch_user("x", TOKEN, post=lambda q, v, t: page)


def test_unexpected_error_does_not_dump_the_body(snapmod):
    secret = "SECRET-BODY-CONTENT"
    with pytest.raises(snapmod.SnapshotError) as ei:
        snapmod.snapshot_from_user({"contributionsCollection": {"contributionCalendar": {"weeks": [secret]}}}, "2026-11-26")
    assert secret not in str(ei.value)


def test_graphql_errors_with_non_dict_items(snapmod, monkeypatch):
    body = json.dumps({"errors": ["boom"], "data": None}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning(body))
    with pytest.raises(snapmod.SnapshotError, match="boom"):
        snapmod._post("q", {}, TOKEN)
