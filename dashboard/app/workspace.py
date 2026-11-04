"""The git checkout Claude works in: one folder per project under instance/workspaces/, one branch per change.
Everything here runs plain git / the project's own test command as a list of arguments — never through a shell —
with a minimal environment (no dashboard secrets) and time limits. Paths Claude asks for are checked to stay inside
the checkout, and secret files (.env, keys, .git) can't be read or written.
Production: run this inside a throwaway container per change (Step 12)."""
import base64
import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

from flask import current_app

from app.crypto import decrypt_secret
from app.models.setting import get_setting

GIT_TIMEOUT = 120
TEST_TIMEOUT = 600
BLOCKED_NAMES = re.compile(r"(^|/)(\.git(/|$)|\.env($|\.)|.*\.(pem|key|p12|pfx)$|id_rsa|id_ed25519|.*\.keystore$)", re.I)


class WorkspaceError(Exception):
    pass


def root_for(project):
    base = Path(current_app.config.get("WORKSPACE_ROOT") or Path(current_app.instance_path) / "workspaces")
    base.mkdir(parents=True, exist_ok=True)
    return base / project.slug


def remote_url(project):
    tpl = current_app.config.get("GIT_REMOTE_TEMPLATE")     # tests point this at local bare repos
    return tpl.format(repo=project.github_repo) if tpl else f"https://github.com/{project.github_repo}.git"


def _env():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "AI Dashboard",
            "GIT_AUTHOR_EMAIL": "ai-dashboard@localhost", "GIT_COMMITTER_NAME": "AI Dashboard",
            "GIT_COMMITTER_EMAIL": "ai-dashboard@localhost"}


def _auth_args():
    """GitHub token as a one-command HTTP header — never written into .git/config or a URL."""
    token = decrypt_secret(get_setting("github_token_enc") or "")
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {basic}"]


# Claude's shell (the runner user, same group) can write in the checkout, so what it leaves in .git is NOT trusted. Before every git
# call the dashboard keeps only the config keys git itself writes (no hook / fsmonitor / filter / ssh / push-URL / include settings
# that could run a command or send the GitHub token elsewhere), empties .git/hooks, and refuses a .git that is not its own. The
# flags below are a second layer; the umask keeps what git writes from being group-writable again.
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "protocol.ext.allow=never"]
GIT_CONFIG_KEEP = re.compile(r"^(core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks)"
                             r"|remote\.origin\.(url|fetch)|branch\..+\.(remote|merge))$", re.I)


def harden_git(cwd):
    """Make the checkout's .git safe to run git in. Raises WorkspaceError when it is not the dashboard's own folder or its config is unreadable."""
    gitdir = Path(cwd) / ".git"
    try:
        st = os.lstat(gitdir)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise WorkspaceError("The project's .git folder is not the dashboard's own (it was replaced), so no git command was run.")
    cfg = gitdir / "config"
    r = subprocess.run(["git", "config", "--file", str(cfg), "--list", "-z"], env=_env(), capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise WorkspaceError("The project's git settings can't be read, so no git command was run.")
    for item in r.stdout.split("\0"):
        key = item.partition("\n")[0]
        if key and not GIT_CONFIG_KEEP.match(key):
            subprocess.run(["git", "config", "--file", str(cfg), "--unset-all", key], env=_env(), capture_output=True, text=True, timeout=30)
    hooks = gitdir / "hooks"
    try:
        if hooks.is_symlink() or not hooks.is_dir():
            if hooks.is_symlink() or hooks.exists():
                hooks.unlink() if hooks.is_symlink() or hooks.is_file() else shutil.rmtree(hooks)
            hooks.mkdir()
        for entry in hooks.iterdir():
            if not entry.name.endswith(".sample"):
                shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        for stray in (gitdir / "info" / "attributes", gitdir / "commondir", gitdir / "gitdir"):      # a plain checkout has none of these
            if stray.exists() or stray.is_symlink():
                stray.unlink()
    except OSError:
        raise WorkspaceError("The project's git hooks could not be cleaned, so no git command was run.")


def lock_git(cwd):
    """Nobody but the dashboard's user may change anything under .git (group write off, other write off). The runner can still read it."""
    gitdir = Path(cwd) / ".git"
    if not gitdir.is_dir() or gitdir.is_symlink():
        return
    for top, dirs, files in os.walk(gitdir):
        for name in [top] + [os.path.join(top, f) for f in files]:
            try:
                st = os.lstat(name)
                if stat.S_ISLNK(st.st_mode):
                    continue
                mode = stat.S_IMODE(st.st_mode)
                if mode & (stat.S_IWGRP | stat.S_IWOTH):
                    os.chmod(name, mode & ~(stat.S_IWGRP | stat.S_IWOTH))
            except OSError:
                pass


def git(cwd, *args, auth=False, check=True):
    if (Path(cwd) / ".git").exists():
        harden_git(cwd)
    cmd = ["sh", "-c", 'umask 027; exec "$@"', "sh", "git"] + GIT_SAFE + (_auth_args() if auth else []) + list(args)
    try:
        r = subprocess.run(cmd, cwd=cwd, env=_env(), capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"git {args[0]} took too long.")
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
        raise WorkspaceError(f"git {args[0]} failed: {msg[0][:200]}")
    return r.stdout


def prepare(project, branch, base_branch):
    """Fresh checkout of `branch` (continue it if it exists on GitHub, otherwise start it from base_branch)."""
    if not project.github_repo:
        raise WorkspaceError("This project has no GitHub repo set.")
    root = root_for(project)
    url = remote_url(project)
    if (root / ".git").exists():
        try:
            harden_git(root)
        except WorkspaceError:                       # the checkout was tampered with: start again from GitHub
            shutil.rmtree(root, ignore_errors=True)
        else:
            lock_git(root)
    if not (root / ".git").exists():
        if root.exists():
            shutil.rmtree(root)
        git(root.parent, "clone", "--quiet", url, str(root), auth=True)
    git(root, "remote", "set-url", "origin", url)          # the repo may have been renamed / moved since the last run
    git(root, "fetch", "--quiet", "--prune", "origin", auth=True)
    git(root, "reset", "--hard", "--quiet")
    git(root, "clean", "-fdq")
    remote_branches = git(root, "branch", "-r")
    if f"origin/{branch}" in remote_branches:
        git(root, "checkout", "-B", branch, f"origin/{branch}", "--quiet")
    else:
        if f"origin/{base_branch}" not in remote_branches:
            raise WorkspaceError(f"The base branch “{base_branch}” is not on GitHub.")
        git(root, "checkout", "-B", branch, f"origin/{base_branch}", "--quiet")
    share_with_runner(root)
    return root.resolve()        # real path (e.g. /var → /private/var on macOS) so every path check compares like with like


def share_with_runner(root):
    """Claude Code works in the checkout as the low-rights runner user (same group). A checkout first made by a command
    with another umask (e.g. 022) is read-only for it — "Permission denied" on every edit (seen 2026-10-22). Make every
    file group-writable and every folder group-writable + setgid (new files keep the group). Skips .git."""
    import stat
    for top, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name, want in [(top, stat.S_IRWXG | stat.S_ISGID)] + [(os.path.join(top, f), stat.S_IRGRP | stat.S_IWGRP) for f in files]:
            try:
                st = os.lstat(name)
                if stat.S_ISLNK(st.st_mode) or (st.st_mode & want) == want:
                    continue
                os.chmod(name, st.st_mode | want)
            except OSError:
                pass


def safe_path(root, rel):
    """Resolve a path Claude gave us; refuse anything outside the checkout or any secret file."""
    rel = (rel or "").strip()
    if rel.startswith("/repo"):
        rel = rel[len("/repo"):]
    rel = rel.lstrip("/")
    p = (root / rel).resolve()
    root_r = root.resolve()
    if p != root_r and root_r not in p.parents:
        raise WorkspaceError("That path is outside the project.")
    shown = str(p.relative_to(root_r)) if p != root_r else ""
    if shown and BLOCKED_NAMES.search(shown):
        raise WorkspaceError("Secret and git files can't be read or changed.")
    return p


def changed_files(root, base_ref):
    """Files that differ from the base branch: committed on the change branch plus not yet committed."""
    files = {l.strip() for l in git(root, "diff", "--name-only", f"{base_ref}...HEAD", check=False).splitlines() if l.strip()}
    for line in git(root, "status", "--porcelain", "--untracked-files=all", check=False).splitlines():
        if len(line) > 3:
            files.add(line[3:].split(" -> ")[-1].strip())
    return sorted(files)


def commit_and_push(root, branch, message):
    """Commit everything Claude changed and push the change branch. Returns (commit, pushed, note)."""
    git(root, "add", "-A")
    if not git(root, "status", "--porcelain").strip():
        return git(root, "rev-parse", "--short", "HEAD").strip(), False, "No file changed."
    git(root, "commit", "--quiet", "-m", message)
    sha = git(root, "rev-parse", "--short", "HEAD").strip()
    try:
        git(root, "push", "--quiet", "-u", "origin", branch, auth=True)
        return sha, True, ""
    except WorkspaceError as e:
        return sha, False, f"Saved on the dashboard but not pushed to GitHub ({e}). The GitHub token needs “Contents: Read and write”."


# ---------- tests ----------
_PATTERNS = [
    (re.compile(r"Tests:\s+(?:(\d+) failed, )?(?:\d+ \w+, )*(\d+) passed"), "artisan"),   # Laravel / Pest
    (re.compile(r"OK \((\d+) tests?,"), "phpunit-ok"),
    (re.compile(r"Tests: (\d+), Assertions: \d+, (?:Errors: (\d+), )?Failures: (\d+)"), "phpunit-fail"),
    (re.compile(r"(?:(\d+) failed, )?(\d+) passed"), "pytest"),
]


def parse_counts(output):
    """(passed, total) from common test runners' summary lines, or None if not recognised."""
    for rx, kind in _PATTERNS:
        m = None
        for m in rx.finditer(output or ""):
            pass
        if not m:
            continue
        if kind == "phpunit-ok":
            n = int(m.group(1))
            return n, n
        if kind == "phpunit-fail":
            total, errors, failures = int(m.group(1)), int(m.group(2) or 0), int(m.group(3))
            return max(0, total - errors - failures), total
        failed, passed = int(m.group(1) or 0), int(m.group(2))
        return passed, passed + failed
    return None


def run_tests(root, command):
    """Run the project's test command (set by the owner) without a shell. Returns (ok, counts, output tail)."""
    if not command.strip():
        return None, None, "No test command is set for this project."
    try:
        args = shlex.split(command)
    except ValueError:
        return False, None, "The test command can't be read."
    runner = current_app.config.get("TEST_RUNNER_USER")
    if runner:      # server: the tests run as a low-rights user (sudoers allows only that user); umask keeps files shared
        args = ["sudo", "-n", "-H", "-u", runner, "--", "/usr/bin/env", f"PATH={_env()['PATH']}", "LANG=C.UTF-8",
                "sh", "-c", 'umask 007; exec "$@"', "sh"] + args
    try:
        r = subprocess.run(args, cwd=root, env=_env(), capture_output=True, text=True, timeout=TEST_TIMEOUT)
    except FileNotFoundError:
        return False, None, f"“{args[0]}” is not installed where the dashboard runs."
    except subprocess.TimeoutExpired:
        return False, None, "The tests took longer than 10 minutes and were stopped."
    out = (r.stdout + "\n" + r.stderr).strip()
    return r.returncode == 0, parse_counts(out), out[-6000:]
