"""Live folder vs GitHub main (owner 2026-11-05: no release folders — live is the git checkout, deploy is `git pull`).

Developers may edit the live folder by hand (FTP), commit there, or deploy themselves. The dashboard looks at the live
folder and says what differs from main:
  edits / unpushed commits in live  → "Commit & push" puts them on GitHub (never a force-push; if main also moved, the
                                       live commits go to a side branch and are merged into main here, a conflict stops)
  main has newer code than live      → "Update live from main" deploys it; nothing to do when live already equals main."""
import re
import shlex
from datetime import timedelta

from app import remote
from app import workspace as ws
from app.auth import log
from app.clock import now
from app.extensions import db
from app.jobs import note

CHECK_EVERY = timedelta(minutes=30)
SIDE_BRANCH = "dashboard/live-edits"
IDENT = "-c user.name='AI Dashboard' -c user.email='dashboard@ivinfotech.com'"
SECRET_NAME = re.compile(r"(^|/)(\.env(\.(?!example|sample|dist)[^/]*)?|id_(rsa|dsa|ecdsa|ed25519)[^/]*|[^/]*\.(pem|key|p12|pfx|sqlite3?|db)"
                         r"|[^/]*\.sql(\.gz)?|credentials[^/]*\.json|[^/]*service[-_]?account[^/]*\.json)$", re.I)


def state_script(path, base):
    """Shell that prints HEAD= / AHEAD= / BEHIND= / FETCH= and then the changed files (git status; files in .gitignore are left out)."""
    b = shlex.quote(base)
    return (f"cd {shlex.quote(path)}\n"
            "echo \"HEAD=$(git rev-parse HEAD)\"\n"
            f"if git fetch --quiet origin {b} 2>/dev/null; then echo FETCH=ok; else echo FETCH=fail; fi\n"
            f"echo \"AHEAD=$(git rev-list --count origin/{base}..HEAD 2>/dev/null || echo 0)\"\n"
            f"echo \"BEHIND=$(git rev-list --count HEAD..origin/{base} 2>/dev/null || echo 0)\"\n"
            "git status --porcelain\n")


def parse_state(out):
    lines = [l for l in out.splitlines() if l.strip()]
    def val(key):
        return next((l[len(key) + 1:].strip() for l in lines if l.startswith(key + "=")), "")
    num = lambda k: int(val(k)) if val(k).isdigit() else 0
    rows = [l for l in lines if len(l) > 3 and l[2] == " " and not re.match(r"[A-Z]+=", l)]
    path = lambda l: l[3:].strip().strip('"')
    return {"head": val("HEAD"), "ahead": num("AHEAD"), "behind": num("BEHIND"), "fetch_ok": val("FETCH") != "fail",
            "files": [path(l) for l in rows if not l.startswith("??")], "new": [path(l) for l in rows if l.startswith("??")]}


def read(project, live):
    code, out = remote.run(live.server, "set -uo pipefail\n" + state_script(live.deploy_path, project.default_branch or "main"), timeout=180)
    if code != 0:
        raise remote.RemoteError("Could not read the live folder with git — is it a git checkout of this repo?")
    return parse_state(out)


def refresh(project):
    """Look at the live folder now and remember what differs. Returns the state, or None when live is not set up."""
    live = project.env("live")
    if live is None or live.server is None or not live.deploy_path or live.layout == "releases":
        return None
    state = read(project, live)
    live.drift, live.drift_at = state, now()
    db.session.commit()
    return state


def refresh_due(projects):
    for p in projects:
        live = p.env("live")
        if live is None or live.server is None or not live.deploy_path:
            continue
        if live.drift_at and now() - live.drift_at < CHECK_EVERY:
            continue
        try:
            refresh(p)
        except remote.RemoteError:
            db.session.rollback()


def secrets_in(files):
    return [f for f in files if SECRET_NAME.search(f)]


def run_commit_push(job):
    from app.golive import _live
    from app.deploy import DeployError
    project = job.project
    live = _live(project)
    base = project.default_branch or "main"
    note(job, "Looking at the live folder")
    st = read(project, live)
    live.drift, live.drift_at = st, now()
    db.session.commit()
    if not st["fetch_ok"]:
        raise DeployError("The live server cannot reach GitHub, so it cannot be compared or pushed. Check the server's GitHub connection.")
    changed = st["files"] + st["new"]
    if not changed and not st["ahead"]:
        note(job, "Live has nothing that GitHub does not have")
        return
    bad = secrets_in(changed)
    if bad:
        note(job, "", "Files that look like secrets or data:\n" + "\n".join(bad))
        raise DeployError(f"Not committing: {', '.join(bad[:3])}{'…' if len(bad) > 3 else ''} look{'s' if len(bad) == 1 else ''} like a secret or data file. "
                          "Add them to .gitignore on the live folder (or remove them) and try again. Nothing was changed.")
    side = st["behind"] > 0
    msg = shlex.quote(f"Live folder edits committed by the dashboard ({job.by_user.name if job.by_user else 'owner'})")
    target = f"refs/heads/{SIDE_BRANCH}" if side else f"refs/heads/{base}"
    note(job, "Committing the live folder's changes" if changed else "Pushing the live folder's commits")
    code, out = remote.run(live.server, "set -eo pipefail\n" + f"cd {shlex.quote(live.deploy_path)}\n"
                           "if [ -n \"$(git status --porcelain)\" ]; then git add -A\n"
                           f"  git {IDENT} commit --quiet -m {msg}; fi\n"
                           f"git push --quiet origin HEAD:{shlex.quote(target)}\n", timeout=600)
    if code != 0:
        note(job, "", out)
        raise DeployError("Could not push from the live server to GitHub — see the output below. The live server needs write access to the repo. "
                          "Nothing was changed on GitHub.")
    if side:
        note(job, f"main moved on too — merging the live commits into {base}")
        try:
            root = ws.prepare(project, base, base)
            ws.git(root, "fetch", "--quiet", "origin", SIDE_BRANCH, auth=True)
            try:
                ws.git(root, "merge", "--no-edit", "-m", f"Merge live folder edits into {base}", f"origin/{SIDE_BRANCH}")
            except ws.WorkspaceError:
                ws.git(root, "merge", "--abort", check=False)
                raise DeployError(f"The live folder's edits conflict with {base}. They are saved on GitHub as the branch “{SIDE_BRANCH}” — "
                                  f"merge it into {base} by hand (or ask Claude), then check again. Nothing was lost.")
            ws.git(root, "push", "--quiet", "origin", base, auth=True)
            ws.git(root, "push", "--quiet", "origin", f":{SIDE_BRANCH}", auth=True, check=False)
        except ws.WorkspaceError as e:
            raise DeployError(f"Could not merge the live edits into {base} ({e}). They are saved on GitHub as the branch “{SIDE_BRANCH}”.")
    log("Live folder committed", project_id=project.id, user=job.by_user,
        details=f"{len(changed)} file{'s' if len(changed) != 1 else ''}, {st['ahead']} earlier commit{'s' if st['ahead'] != 1 else ''} → {base}"
                + (" (merged)" if side else ""))
    refresh(project)
    note(job, f"Done — GitHub {base} now has the live folder's code")


def run_update(job):
    """Deploy what main has and live lacks (a developer's work), the same steps as Go Live but without changes of ours."""
    from app import golive
    from app.deploy import DeployError
    from app.models import Deployment
    project = job.project
    live = golive._live(project)
    note(job, "Comparing live with main")
    st = read(project, live)
    live.drift, live.drift_at = st, now()
    db.session.commit()
    if not st["fetch_ok"]:
        raise DeployError("The live server cannot reach GitHub. Check the server's GitHub connection.")
    if st["files"] or st["new"] or st["ahead"]:
        raise DeployError("The live folder has edits or commits that GitHub does not have — use “Commit & push” first, so nothing is lost. Nothing was changed.")
    if not st["behind"]:
        note(job, "Live already has everything main has — nothing to do")
        return
    previous = st["head"]
    snap = golive.snapshot(project, live, job)
    golive._deploy_live(project, live, job)
    ok, status = golive._check_and_watch(project, live, job)
    new = refresh(project)
    sha = (new or {}).get("head", "")[:7]
    live.current_commit = sha or live.current_commit
    db.session.add(Deployment(project_id=project.id, environment_id=live.id, commit=sha, previous_commit=previous or None,
                              change_ids=[], snapshot_id=snap.id if snap else None, by_user_id=job.by_user_id,
                              result="ok" if ok else "failed", note=f"Update from {project.default_branch or 'main'} ({st['behind']} new commit{'s' if st['behind'] != 1 else ''})"))
    log("Live updated from main", project_id=project.id, user=job.by_user, details=f"{st['behind']} commit{'s' if st['behind'] != 1 else ''} → live {sha}")
    db.session.commit()
    if not ok:
        raise DeployError(f"Deployed, but the live site does not answer ({'no answer' if status == 0 else status}). Check it now — use Rollback if it is broken.")
    note(job, f"Live is on {sha} · watching closely for {golive.WATCH_MINUTES} minutes")
