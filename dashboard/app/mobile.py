"""Mobile apps (CHANGES Step 10): Flutter apps in a project's repo get TEST builds on Codemagic that talk to the
beta — never to live.

How a test build is made:
1. The dashboard's checkout of the change's branch (or the default branch) gets a `codemagic.yaml` and is pushed as
   its own branch `aidash/app-…` — the change branch and the default branch are never touched.
2. Codemagic builds that branch. Before building, the app's live address (e.g. http://eyenosis.com/api) is replaced
   with the beta's (https://<slug>.beta…), and the build stops if the live address is still there.
3. The dashboard reads the build state (page view + the 5-minute monitor timer) and, when it is ready, keeps a public
   download link (7 days) — shown with a QR code to open on a phone."""
import os
import re
from datetime import timedelta

from app import codemagic
from app import workspace as ws
from app.alerts import raise_alert
from app.auth import log
from app.clock import now
from app.extensions import db
from app.models import AppBuild

SKIP_DIRS = {".git", "vendor", "node_modules", ".dart_tool", "build", ".idea", "ios", "android", "storage", "public"}
DIR_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
RUNNING = ("queued", "building")


class MobileError(Exception):
    pass


def find_apps(root):
    """Flutter apps in a checkout: folders (up to 3 deep) with a pubspec.yaml that uses the Flutter SDK + an android/ folder."""
    found = []
    root = str(root)
    for top, dirs, files in os.walk(root):
        rel = os.path.relpath(top, root)
        depth = 0 if rel == "." else rel.count("/") + 1
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")] if depth < 3 else []
        if "pubspec.yaml" in files and os.path.isdir(os.path.join(top, "android")):
            try:
                text = open(os.path.join(top, "pubspec.yaml"), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if "sdk: flutter" not in text:
                continue
            m = re.search(r"^name:\s*([\w-]+)", text, re.M)
            d = "." if rel == "." else rel
            if DIR_RE.match(d) or d == ".":
                found.append({"dir": d, "name": m.group(1) if m else os.path.basename(top)})
    return sorted(found, key=lambda a: a["dir"])


def workflow_id(app_dir):
    return "aidash_android_" + re.sub(r"\W", "_", "root" if app_dir == "." else app_dir)


def yaml_for(apps):
    """codemagic.yaml with one Android test workflow per app. LIVE_HOST / BETA_HOST come in as build variables."""
    out = ["# Made by the AI Project Dashboard on its own build branch — TEST builds only.",
           "# The app is pointed at the beta before it is built; a build that still has the live address stops.",
           "workflows:"]
    for a in apps:
        d = a["dir"]
        art = ("" if d == "." else d + "/") + "build/app/outputs/flutter-apk/*.apk"
        out += [f"  {workflow_id(d)}:",
                f"    name: Test APK - {a['name']} (beta)",
                "    instance_type: mac_mini_m2",
                "    max_build_duration: 60",
                "    environment:",
                "      flutter: stable",
                "    scripts:",
                "      - name: Point the app at the beta (never live)",
                "        script: |",
                f"          cd \"{d}\"",
                "          LIVE_RE=$(printf '%s' \"$LIVE_HOST\" | sed 's/[.]/\\\\./g')",
                "          for f in $(grep -rlE \"https?://(www\\.)?$LIVE_RE\" lib assets 2>/dev/null || true); do",
                "            perl -pi -e \"s#https?://(www\\.)?\\Q$LIVE_HOST\\E(?![\\w-]|\\.\\w)#https://$BETA_HOST#g\" \"$f\"",
                "            echo \"beta address in $f\"",
                "          done",
                "          if grep -rqE \"//(www\\.)?$LIVE_RE\" lib assets 2>/dev/null; then echo \"The live address is still in the app\"; exit 1; fi",
                "      - name: Build the test APK",
                "        script: |",
                f"          cd \"{d}\"",
                "          flutter pub get",
                "          flutter build apk --release",
                "    artifacts:",
                f"      - {art}"]
    return "\n".join(out) + "\n"


def _hosts(project):
    from app.betas import _hosts as hosts
    live, beta = hosts(project)
    if not live or not beta or live == beta:
        raise MobileError("Set the live and beta addresses first — the test app is pointed at the beta.")
    return live, beta


def scan(project):
    """Look for Flutter apps on the default branch and remember them on the project."""
    base = project.default_branch or "main"
    try:
        root = ws.prepare(project, base, base)
    except ws.WorkspaceError as e:
        raise MobileError(f"Could not read the repo: {e}")
    project.mobile_apps = find_apps(root)
    db.session.commit()
    return project.mobile_apps


def start(project, app_dir, user, change=None):
    """Start a test build (Android APK) of one app — from the change's branch, or the default branch."""
    apps = {a["dir"]: a for a in (project.mobile_apps or [])}
    if app_dir not in apps:
        raise MobileError("That app folder is not one of this project's apps — press “Look for apps” first.")
    live_host, beta_host = _hosts(project)
    base = project.default_branch or "main"
    source = change.branch if change is not None and change.branch else base
    build_branch = f"aidash/app-{'change-' + str(change.id) if change is not None else 'main'}"
    try:
        root = ws.prepare(project, source, base)
        ws.git(root, "checkout", "-B", build_branch, "--quiet")
        open(os.path.join(str(root), "codemagic.yaml"), "w").write(yaml_for(list(apps.values())))
        ws.git(root, "add", "codemagic.yaml")
        if ws.git(root, "status", "--porcelain").strip():
            ws.git(root, "commit", "--quiet", "-m", "AI Project Dashboard: test build settings (codemagic.yaml)")
        ws.git(root, "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{build_branch}", auth=True)
    except ws.WorkspaceError as e:
        raise MobileError(f"Could not prepare the build branch: {e}")
    try:
        app_id = codemagic.app_id_for(project.github_repo)
        build_id = codemagic.start_build(app_id, workflow_id(app_dir), build_branch,
                                         {"LIVE_HOST": live_host, "BETA_HOST": beta_host})
    except codemagic.CodemagicError as e:
        raise MobileError(str(e))
    b = AppBuild(project_id=project.id, change_id=change.id if change is not None else None, app_dir=app_dir,
                 branch=build_branch, cm_build_id=build_id, status="building", by_user_id=user.id if user else None)
    db.session.add(b)
    log("App build", project_id=project.id, user=user,
        details=f"Test APK of {app_dir} started ({'change #' + str(change.id) if change is not None else base}) → beta {beta_host}")
    db.session.commit()
    return b


def refresh(build):
    """Read a running build's state from Codemagic; when ready keep a 7-day download link and tell the owner."""
    if build.status not in RUNNING or not build.cm_build_id:
        return build
    try:
        state, files, raw = codemagic.build_state(build.cm_build_id)
    except codemagic.CodemagicError as e:
        build.error = str(e)[:255]
        db.session.commit()
        return build
    if state == "building":
        return build
    build.finished_at = now()
    if state == "ready" and files:
        try:
            url, expires = codemagic.public_url(files[0]["url"])
        except codemagic.CodemagicError as e:
            build.status, build.error = "failed", f"Built, but no download link: {e}"[:255]
            db.session.commit()
            return build
        build.status, build.file_url = "ready", url
        build.file_name = str(files[0].get("name") or "app.apk")[:200]
        build.url_expires_at = now() + timedelta(seconds=max(0, expires - int(__import__("time").time())))
        db.session.commit()
        raise_alert("approval", f"{build.project.name}: test app {build.app_dir} is ready (talks to the beta) — open the project to install it",
                    build.project)
    else:
        build.status = "canceled" if state == "canceled" else "failed"
        build.error = ("Built, but no app file came out." if state == "ready" else f"Codemagic: {raw or state}")[:255]
        db.session.commit()
        raise_alert("error", f"{build.project.name}: test app {build.app_dir} did not build ({build.error})", build.project)
    return build


def refresh_due():
    """Monitor timer: every running build once."""
    for b in AppBuild.query.filter(AppBuild.status.in_(RUNNING)).all():
        refresh(b)


def touched_apps(project, change):
    dirs = [a["dir"] for a in (project.mobile_apps or [])]
    files = [str(f.get("path") if isinstance(f, dict) else f) for f in (change.files or [])]
    return [d for d in dirs if d == "." and files or any(f == d or f.startswith(d.rstrip("/") + "/") for f in files)]


def qr_svg(url):
    """A QR code (inline SVG) so the download link opens straight on a phone."""
    import qrcode
    import qrcode.image.svg
    from markupsafe import Markup
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=2)
    return Markup(img.to_string(encoding="unicode"))


def needs_build(project, change):
    """App folders this change touched that have no test build since Claude's last edit — the page highlights them
    (owner, 2026-10-22: builds are started by hand, after checking the web on beta)."""
    if change is None:
        return []
    last_claude = max((m.created_at for m in change.messages if m.sender == "claude"), default=None)
    out = []
    for d in touched_apps(project, change):
        b = AppBuild.query.filter_by(project_id=project.id, change_id=change.id, app_dir=d).order_by(AppBuild.id.desc()).first()
        if b is None or (last_claude is not None and b.created_at < last_claude) or b.status in ("failed", "canceled"):
            out.append(d)
    return out


def download_token(build):
    """Short secret for the phone link (the Codemagic link is ~400 characters — too dense to scan as a QR code)."""
    import hashlib
    import hmac
    from flask import current_app
    key = (current_app.config.get("SECRET_KEY") or "").encode()
    return hmac.new(key, f"apk:{build.id}:{build.file_url}".encode(), hashlib.sha256).hexdigest()[:24]
