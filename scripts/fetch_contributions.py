#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub GraphQL API.

    GH_TOKEN=... python scripts/fetch_contributions.py     # real data (used by the Action)
    python scripts/fetch_contributions.py --sample         # deterministic fake data for local previews

Besides GitHub's own contribution calendar, this also counts YOUR commits on every
branch of your repos (deduplicated by commit SHA) into "all_branch". GitHub itself
only counts commits on the default branch, so unmerged branch work is invisible in
its graph. See "all_branch" in cards.config.json.

Standard library only. The default GITHUB_TOKEN of a workflow can read public
contributions; add a PROFILE_TOKEN secret (read:user) to include private ones.
"""
import datetime as dt
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "cards.config.json").read_text())
OUT = ROOT / CFG["data"]


def graphql(query, variables, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise SystemExit(f"GraphQL error: {body['errors']}")
    return body["data"]


def rest(path, token, params=None):
    url = "https://api.github.com" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "User-Agent": "profile-cards"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 409:                      # empty repository
                return []
            limited = e.code == 403 and (e.headers.get("X-RateLimit-Remaining") == "0" or e.headers.get("Retry-After"))
            if (e.code in (429, 502, 503) or limited) and attempt < 2:   # rate limit or a GitHub hiccup: wait and retry
                time.sleep(min(int(e.headers.get("Retry-After") or 0) or 2 ** (attempt + 1) * 2, 60))
                continue
            raise


def paged(path, token, params=None, max_pages=30):
    out = []
    for page in range(1, max_pages + 1):
        items = rest(path, token, {**(params or {}), "per_page": 100, "page": page})
        out += items
        if len(items) < 100:
            break
    return out


def list_repos(token, ab):
    """Repos to scan, as {full_name: default_branch or None}. A personal access token (PROFILE_TOKEN)
    also sees private and org repos."""
    user = CFG["user"]
    try:
        repos = paged("/user/repos", token, {"affiliation": "owner,collaborator,organization_member"})
        scope = "all repos your token can see"
    except urllib.error.HTTPError:                 # the default GITHUB_TOKEN cannot call /user/repos
        repos = paged(f"/users/{user}/repos", token, {"type": "owner"})
        scope = "public repos only (add a PROFILE_TOKEN secret for private and org repos)"
    found = {r["full_name"]: r.get("default_branch") for r in repos
             if (ab.get("include_forks") or not r["fork"]) and not r["archived"]}
    for extra in ab.get("extra_repos", []):
        found.setdefault(extra, None)
    for gone in ab.get("exclude_repos", []):
        found.pop(gone, None)
    print(f"all-branch: scanning {len(found)} repos ({scope})")
    return found


def collect_all_branch(token, start, today):
    """Your commits on every branch of your repos, one per SHA, counted per UTC day.

    Also returns "extra_days": the commits that are NOT on a repo's default branch, i.e. the ones
    GitHub's own graph ignores. The combined card adds those to GitHub's count."""
    ab = CFG.get("all_branch", {})
    user, budget = CFG["user"], ab.get("max_requests", 3000)
    authors = [user] + list(ab.get("author_emails", []))   # extra emails catch commits not linked to the account
    seen, default_shas = {}, set()
    branches_n, used_repos, calls = 0, [], 0
    for repo, default in list_repos(token, ab).items():
        if default is None:
            try:
                default = rest(f"/repos/{repo}", token).get("default_branch")
            except urllib.error.HTTPError:
                default = None
        try:
            branches = [b["name"] for b in paged(f"/repos/{repo}/branches", token)]
        except urllib.error.HTTPError as e:
            print(f"warning: {repo}: cannot list branches ({e.code}), skipped", file=sys.stderr)
            continue
        repo_shas = set()
        for br in branches:
            found_here = False
            for author in authors:
                if calls >= budget:
                    print("warning: request budget reached, results are partial", file=sys.stderr)
                    break
                calls += 1
                try:
                    commits = paged(f"/repos/{repo}/commits", token,
                                    {"sha": br, "author": author, "since": f"{start}T00:00:00Z"}, max_pages=10)
                except urllib.error.HTTPError as e:
                    print(f"warning: {repo}@{br}: {e.code}", file=sys.stderr)
                    continue
                for c in commits:
                    when = c["commit"]["author"]["date"][:10]
                    if start.isoformat() <= when <= today.isoformat():
                        seen.setdefault(c["sha"], when)
                        repo_shas.add(c["sha"])
                        found_here = True
                        if br == default:
                            default_shas.add(c["sha"])
            branches_n += found_here               # only branches that hold commits of yours
        if default is None:                        # unknown default branch: do not guess, count nothing as extra
            default_shas |= repo_shas
        if repo_shas:
            used_repos.append(repo)

    def per_day(shas):
        counts = {}
        for sha in shas:
            counts[seen[sha]] = counts.get(seen[sha], 0) + 1
        return [{"date": (start + dt.timedelta(days=i)).isoformat(),
                 "count": counts.get((start + dt.timedelta(days=i)).isoformat(), 0)} for i in range(365)]

    extra = [sha for sha in seen if sha not in default_shas]
    print(f"all-branch: {len(seen)} commits ({len(extra)} outside default branches), "
          f"{branches_n} branches, {len(used_repos)} repos, {calls} API calls")
    return {"total": len(seen), "extra_total": len(extra), "branches": branches_n, "repos": used_repos,
            "days": per_day(seen), "extra_days": per_day(extra)}


def fetch(token):
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=364)
    repo_q = "".join(
        f'r{i}: repository(owner: "{f.split("/")[0]}", name: "{f.split("/")[1]}") '
        "{ nameWithOwner description url stargazerCount forkCount primaryLanguage { name color } }\n"
        for i, f in enumerate(CFG["featured"])
    )
    query = (
        "query($login: String!, $from: DateTime!, $to: DateTime!) {"
        " user(login: $login) { contributionsCollection(from: $from, to: $to) {"
        " contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } } }"
        f" {repo_q} }}"
    )
    data = graphql(query, {"login": CFG["user"], "from": f"{start}T00:00:00Z", "to": f"{today}T23:59:59Z"}, token)
    cal = data["user"]["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": d["date"], "count": d["contributionCount"]}
        for w in cal["weeks"] for d in w["contributionDays"]
    ]
    repos = []
    for i, f in enumerate(CFG["featured"]):
        r = data.get(f"r{i}")
        if not r:
            print(f"warning: {f} not found, skipped", file=sys.stderr)
            continue
        lang = r["primaryLanguage"] or {}
        repos.append({
            "nameWithOwner": r["nameWithOwner"], "description": r["description"] or "",
            "url": r["url"], "stars": r["stargazerCount"], "forks": r["forkCount"],
            "language": lang.get("name", ""), "color": lang.get("color", ""),
        })
    payload = {"user": CFG["user"], "total": cal["totalContributions"], "days": days, "repos": repos}
    if CFG.get("all_branch", {}).get("enabled"):
        try:
            payload["all_branch"] = collect_all_branch(token, start, today)
        except Exception as e:                     # never fail the whole refresh over this
            print(f"warning: all-branch scan failed ({e}); keeping the previous result", file=sys.stderr)
            if OUT.exists():
                old = json.loads(OUT.read_text()).get("all_branch")
                if old:
                    payload["all_branch"] = old
    return payload


def sample():
    """Fake but plausible data so the cards can be previewed offline."""
    rng = random.Random(2314)
    today = dt.date(2026, 11, 16)
    days = []
    for i in range(364, -1, -1):
        d = today - dt.timedelta(days=i)
        busy = 0.8 if d.weekday() < 5 else 0.35
        n = 0
        if rng.random() < busy * (0.55 + 0.4 * ((365 - i) / 365)):
            n = int(rng.expovariate(1 / 4.5)) + 1
        days.append({"date": d.isoformat(), "count": min(n, 28)})
    for k in range(5):  # guarantee a current streak
        days[-1 - k]["count"] = max(days[-1 - k]["count"], 2 + k)
    meta = {"kactlabs/toksum": ("One Stop Token Counter for All Models", "Python", "#3572A5"),
            "kactlabs/pypinindia": ("Indian Pincodes and related Information", "Python", "#3572A5")}
    repos = []
    for f in CFG["featured"]:
        desc, lang, col = meta.get(f, ("", "JavaScript", "#f1e05a"))
        repos.append({"nameWithOwner": f, "description": desc, "url": f"https://github.com/{f}",
                      "stars": 0, "forks": 0, "language": lang, "color": col})
    rnd = random.Random(7)
    extra_days = [{"date": d["date"], "count": rnd.randint(1, 6) if rnd.random() < 0.4 else 0} for d in days]
    ab_days = [{"date": d["date"], "count": d["count"] + e["count"]} for d, e in zip(days, extra_days)]
    all_branch = {"total": sum(d["count"] for d in ab_days), "extra_total": sum(e["count"] for e in extra_days),
                  "branches": 14, "repos": ["MEERAN2314/EduSynapse.AI", "kactlabs/toksum", "kactlabs/pypinindia"],
                  "days": ab_days, "extra_days": extra_days}
    return {"user": CFG["user"], "sample": True, "total": sum(d["count"] for d in days),
            "days": days, "repos": repos, "all_branch": all_branch}


if __name__ == "__main__":
    if "--sample" in sys.argv:
        payload = sample()
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("Set GH_TOKEN (or GITHUB_TOKEN), or pass --sample")
        payload = fetch(token)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {payload['total']} contributions, {len(payload['repos'])} repos")
