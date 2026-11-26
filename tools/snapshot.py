#!/usr/bin/env python3
"""Fetch the profile data from GitHub and write the snapshot that tools/build.py reads.

    tools/snapshot.py --out FILE [--today YYYY-MM-DD]

Standard library only. The token comes from GITHUB_TOKEN (or GH_TOKEN) and is never printed.
Dates use the America/Sao_Paulo time zone.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
API = "https://api.github.com/graphql"
TZ = ZoneInfo("America/Sao_Paulo")
WEEKS = 26
TIMEOUT = 30

QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { firstDay contributionDays { contributionCount } }
      }
    }
    repositories(first: 100, after: $after, privacy: PUBLIC, isFork: false, ownerAffiliations: OWNER) {
      pageInfo { hasNextPage endCursor }
      nodes { name pushedAt }
    }
  }
}
"""


class SnapshotError(Exception):
    """A failure the user must know about; the CLI prints it to stderr and exits with 1."""


def local_date(iso_utc: str) -> str:
    """'2026-11-26T02:30:00Z' (UTC) -> the calendar date in Sao Paulo, 'YYYY-MM-DD'."""
    dt = datetime.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return dt.astimezone(TZ).date().isoformat()


def snapshot_from_user(user: dict | None, today: str) -> dict:
    """Turn the ``user`` object of the GraphQL response into the snapshot format."""
    if not user:
        raise SnapshotError("usuário não encontrado na resposta do GitHub")
    try:
        cal = user["contributionsCollection"]["contributionCalendar"]
        weeks = [{"start": w["firstDay"], "count": sum(d["contributionCount"] for d in w["contributionDays"])}
                 for w in cal["weeks"]]
        if len(weeks) < WEEKS:
            raise SnapshotError(f"o calendário veio com {len(weeks)} semanas; preciso de pelo menos {WEEKS}")
        repos = {r["name"]: {"pushed_at": local_date(r["pushedAt"])}
                 for r in user["repositories"]["nodes"] if r.get("pushedAt")}
        return {
            "today": today,
            "contributions_year": cal["totalContributions"],
            "weeks_26": weeks[-WEEKS:],
            "repos": repos,
        }
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise _unexpected(e) from None


def _unexpected(e: Exception) -> SnapshotError:
    # only the kind of failure: never the response body, never the token
    return SnapshotError(f"resposta inesperada da API do GitHub: {type(e).__name__} em {e!s:.80}")


def _post(query: str, variables: dict, token: str) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-readme-snapshot"},
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # URL is the fixed https API constant
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise SnapshotError(f"a API do GitHub respondeu HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SnapshotError(f"falha de rede ao falar com a API do GitHub: {e}") from None
    except ValueError:
        raise SnapshotError("resposta da API do GitHub não é JSON") from None
    if not isinstance(body, dict):
        raise SnapshotError("resposta inesperada da API do GitHub: o corpo não é um objeto JSON")
    if body.get("errors"):
        msgs = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in body["errors"])
        raise SnapshotError(f"erro do GraphQL: {msgs}")
    if not isinstance(body.get("data"), dict):
        raise SnapshotError("resposta inesperada da API do GitHub: o corpo não tem o campo data")
    return body["data"]


def fetch_user(login: str, token: str, post=_post) -> dict:
    """Run the query, following pagination, and return one ``user`` object with all repositories."""
    user = None
    nodes: list[dict] = []
    after = None
    while True:
        data = post(QUERY, {"login": login, "after": after}, token)
        try:
            page = data.get("user")
            if not page:
                raise SnapshotError(f"usuário {login} não encontrado")
            user = user or page
            nodes += page["repositories"]["nodes"]
            info = page["repositories"]["pageInfo"]
            if not info["hasNextPage"]:
                break
            after = info["endCursor"]
        except (KeyError, TypeError, AttributeError) as e:
            raise _unexpected(e) from None
    user["repositories"] = {"nodes": nodes}
    return user


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--today", help="YYYY-MM-DD; default: today in America/Sao_Paulo")
    args = ap.parse_args(argv)
    try:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise SnapshotError("defina GITHUB_TOKEN (ou GH_TOKEN) no ambiente")
        today = args.today or datetime.datetime.now(TZ).date().isoformat()
        try:
            datetime.date.fromisoformat(today)
        except ValueError:
            raise SnapshotError(f"--today inválido: {today!r} (use AAAA-MM-DD)") from None
        login = json.loads((HERE / "content.json").read_text(encoding="utf-8"))["login"]
        snap = snapshot_from_user(fetch_user(login, token), today)
    except SnapshotError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"snapshot escrito em {args.out} ({len(snap['repos'])} repositórios)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
