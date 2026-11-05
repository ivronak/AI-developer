"""GitHub sync (F04): the repos of one GitHub account / organization are the projects.
- A repo that appears on GitHub becomes a project marked "needs setup" (client, hosting, beta / live URLs to fill in).
- A project whose repo disappeared (renamed / deleted) is marked "repo missing" — never deleted automatically.
Runs from Settings → "Sync now" and from `flask github-sync` (every 5 minutes on the server, Step 12).
Tests replace fetch_repos() — they never call the real API."""
from datetime import datetime

import requests

from app.auth import log
from app.clock import now
from app.crypto import decrypt_secret
from app.extensions import db
from app.models import Project
from app.models.setting import get_setting, set_setting

API = "https://api.github.com"
TIMEOUT = 15
LANG_TO_TECH = {"PHP": "Laravel", "Blade": "Laravel", "Dart": "Flutter", "Python": "Flask"}


class GitHubError(Exception):
    pass


def _remember_expiry(resp):
    """GitHub sends the token's expiry date with every answer — keep it for the 7-day reminder."""
    raw = resp.headers.get("github-authentication-token-expiration", "")
    if raw[:10].count("-") == 2:
        set_setting("github_token_expires", raw[:10])


def _headers(token):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "ai-project-dashboard"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_repos(owner, owner_type, token):
    """Every repo of the owner as dicts. With a token: private repos too. Raises GitHubError with a plain message."""
    if not owner:
        raise GitHubError("Set the GitHub account / organization name first.")
    if owner_type == "org":
        url = f"{API}/orgs/{owner}/repos?per_page=100&type=all"
    elif token:
        url = f"{API}/user/repos?per_page=100&affiliation=owner"       # private + public repos of the token's user
    else:
        url = f"{API}/users/{owner}/repos?per_page=100&type=owner"     # public repos only
    out = []
    while url:
        try:
            r = requests.get(url, headers=_headers(token), timeout=TIMEOUT)
        except requests.RequestException as e:
            raise GitHubError(f"Could not reach GitHub ({e.__class__.__name__}).")
        if token:
            _remember_expiry(r)
        if r.status_code == 401:
            raise GitHubError("GitHub refused the token (401). Make a new token and save it again.")
        if r.status_code == 403:
            raise GitHubError("GitHub said no (403): the token has no access, or the rate limit is reached. Try again later.")
        if r.status_code == 404:
            raise GitHubError(f"“{owner}” was not found on GitHub as {'an organization' if owner_type == 'org' else 'an account'}.")
        if r.status_code != 200:
            raise GitHubError(f"GitHub answered {r.status_code}.")
        for x in r.json():
            if x.get("owner", {}).get("login", "").lower() != owner.lower():
                continue                                               # /user/repos can include other owners
            out.append({
                "id": x.get("id"), "full_name": x["full_name"], "name": x["name"], "private": bool(x.get("private")),
                "default_branch": x.get("default_branch") or "main", "language": x.get("language") or "",
                "archived": bool(x.get("archived")), "pushed_at": x.get("pushed_at"),
            })
        url = r.links.get("next", {}).get("url")
    return out


def check_token(token):
    """Who the token belongs to (for "Test connection")."""
    try:
        r = requests.get(f"{API}/user", headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GitHubError(f"Could not reach GitHub ({e.__class__.__name__}).")
    if r.status_code != 200:
        raise GitHubError("GitHub refused the token." if r.status_code in (401, 403) else f"GitHub answered {r.status_code}.")
    _remember_expiry(r)
    return r.json().get("login", "")


def _nice_name(repo_name):
    words = repo_name.replace("_", " ").replace("-", " ").split()
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in words) or repo_name


def _unique_slug(base):
    slug = "".join(c if c.isalnum() else "-" for c in base.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    cand, n = slug or "project", 2
    while Project.query.filter_by(slug=cand).first():
        cand, n = f"{slug}-{n}", n + 1
    return cand


def _parse_time(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ") if s else None
    except ValueError:
        return None


def sync(user=None):
    """Bring projects in line with GitHub. Returns (new, missing, total) — or raises GitHubError (also saved as the result)."""
    owner, kind = get_setting("github_owner"), get_setting("github_owner_type")
    token = decrypt_secret(get_setting("github_token_enc") or "")
    try:
        repos = [r for r in fetch_repos(owner, kind, token) if not r["archived"]]
    except GitHubError as e:
        set_setting("github_last_sync", now().isoformat())
        set_setting("github_last_result", f"Failed: {e}")
        db.session.commit()
        raise

    projects = Project.query.all()
    by_repo = {(p.github_repo or "").lower(): p for p in projects}
    by_id = {p.github_repo_id: p for p in projects if p.github_repo_id}
    seen, new, moved = set(), [], []
    for r in repos:
        key = r["full_name"].lower()
        seen.add(key)
        p = by_id.get(r.get("id")) or by_repo.get(key)
        if p is not None and (p.github_repo or "").lower() != key:
            # same repo (GitHub's id), new name or new owner — e.g. a transfer ivalema35 → ivronak: follow it
            moved.append(f"{p.github_repo} → {r['full_name']}")
            seen.add((p.github_repo or "").lower())
            p.github_repo = r["full_name"]
        if p is None:
            tech = LANG_TO_TECH.get(r["language"], r["language"])
            p = Project(name=_nice_name(r["name"]), slug=_unique_slug(r["name"]), client_name="Not set yet",
                        tech_stack=[tech] if tech else [], github_repo=r["full_name"], host_type="vps",
                        is_active=True, needs_setup=True)
            db.session.add(p)
            new.append(p)
        p.repo_missing = False
        if r.get("id"):
            p.github_repo_id = r["id"]
        p.repo_private = r["private"]
        p.default_branch = r["default_branch"]
        p.repo_pushed_at = _parse_time(r["pushed_at"])

    # a project whose repo belongs to this owner but is no longer on GitHub → flag it (never delete)
    prefix = f"{(owner or '').lower()}/"
    missing = [p for k, p in by_repo.items() if k.startswith(prefix) and k not in seen]
    for p in missing:
        p.repo_missing = True

    result = (f"{len(repos)} repo{'s' if len(repos) != 1 else ''} · {len(new)} new" + (f" · {len(missing)} missing" if missing else "")
              + (f" · {len(moved)} renamed / moved" if moved else ""))
    for m in moved:
        log("GitHub repo moved", details=m, user=user)
    set_setting("github_last_sync", now().isoformat())
    set_setting("github_last_result", result)
    if new or missing:
        log("GitHub sync", details=result + (": " + ", ".join(p.github_repo for p in new) if new else ""), user=user)
    db.session.commit()
    return new, missing, len(repos)


def resolve_repo(full_name):
    """What an owner/name really is today (GitHub follows renames / transfers): (id, current full_name) or None."""
    token = decrypt_secret(get_setting("github_token_enc") or "")
    try:
        r = requests.get(f"{API}/repos/{full_name}", headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    d = r.json()
    return d.get("id"), d.get("full_name") or full_name



MEMORY_SKIP = ("node_modules/", "vendor/", "storage/", ".git/", "dist/", "build/", "venv/", ".venv/")


def list_doc_files(full_name, branch):
    """Paths of the md / txt files in a repo's branch (for picking the project's memory files), shortest path first. Raises GitHubError."""
    token = decrypt_secret(get_setting("github_token_enc") or "")
    try:
        r = requests.get(f"{API}/repos/{full_name}/git/trees/{branch}?recursive=1", headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GitHubError(f"Could not reach GitHub ({e.__class__.__name__}).")
    if r.status_code in (404, 409):
        raise GitHubError("GitHub has no such repo or branch (or it is empty).")
    if r.status_code != 200:
        raise GitHubError(f"GitHub answered {r.status_code}.")
    paths = [t["path"] for t in r.json().get("tree", [])
             if t.get("type") == "blob" and t["path"].lower().endswith((".md", ".mdx", ".txt"))
             and not any(t["path"].startswith(s) or "/" + s in t["path"] for s in MEMORY_SKIP)]
    return sorted(paths, key=lambda p: (p.count("/"), p.lower()))[:300]
