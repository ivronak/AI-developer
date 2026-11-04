"""Which repo does a server folder really pull from? (CHANGES Step 5.)

A live folder whose git `origin` points at another repo (an old owner after a transfer, a developer's own copy) would
make a Go Live put the WRONG code live. The dashboard reads the origin (daily, and before every Go Live), shows a
warning on the project page, and Go Live refuses until it matches. Go Live v2 (Step 6) removes the need for git on the
server altogether: the code then always comes from the dashboard's own checkout of the project's repo."""
import re
import shlex
from datetime import timedelta

from app import remote
from app.clock import now
from app.extensions import db

_URL = re.compile(r"(?:github\.com|github-[\w.-]+)[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/*$", re.I)
CHECK_EVERY = timedelta(hours=24)


def repo_slug(url):
    """'https://github.com/a/b.git', 'git@github.com:a/b.git', 'git@github-alias:a/b' → 'a/b' (lower case); '' if not GitHub."""
    m = _URL.search((url or "").strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else ""


def check_origin(env):
    """Read the folder's git origin over SSH and remember it. Returns the repo slug ('' = not a git folder / not GitHub)."""
    if env is None or env.server is None or not env.deploy_path:
        return None
    code, out = remote.run(env.server, f"git -C {shlex.quote(env.deploy_path)} remote get-url origin 2>/dev/null || true\n", timeout=60)
    env.git_origin = repo_slug(out.strip().splitlines()[-1] if out.strip() else "")
    env.git_checked_at = now()
    p = env.project
    if env.git_origin and p.github_repo and env.git_origin != p.github_repo.lower():
        _remember_alias(env.git_origin)              # an old name after a transfer may still be the same repo
    db.session.commit()
    return env.git_origin


def _remember_alias(slug):
    from app import github
    from app.models.setting import get_setting, set_setting
    aliases = dict(get_setting("repo_aliases") or {})
    found = github.resolve_repo(slug)
    if found:
        aliases[slug] = {"id": found[0], "full_name": found[1]}
        set_setting("repo_aliases", aliases)


def _same_repo(project, slug):
    from app.models.setting import get_setting
    a = (get_setting("repo_aliases") or {}).get(slug)
    return bool(a) and ((project.github_repo_id and a.get("id") == project.github_repo_id)
                        or (a.get("full_name") or "").lower() == (project.github_repo or "").lower())


def problem(project, env):
    """A plain sentence when the folder pulls from another repo than the project's, else ''."""
    if env is None or not env.git_origin or not project.github_repo:
        return ""
    if env.git_origin != project.github_repo.lower() and not _same_repo(project, env.git_origin):
        return (f"The {env.kind} server folder pulls its code from {env.git_origin}, not from this project's repo "
                f"{project.github_repo}.")
    return ""


def check_due(projects):
    """Monitor timer: re-read the live origins once a day (managed betas get their code from the dashboard)."""
    for p in projects:
        live = p.env("live")
        if live is None or live.server is None or not live.deploy_path:
            continue
        if live.git_checked_at and now() - live.git_checked_at < CHECK_EVERY:
            continue
        try:
            check_origin(live)
        except remote.RemoteError:
            db.session.rollback()
