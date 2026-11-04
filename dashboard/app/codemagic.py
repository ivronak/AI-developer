"""Codemagic — mobile app builds in the cloud (CHANGES Step 10). Every call goes through `api`, which tests replace.

REST API: https://api.codemagic.io, header `x-auth-token` (the owner's token from Codemagic → Account settings →
API token, stored encrypted in Settings → Codemagic). The Builds / Applications API is a preview API, so the answers
are read defensively."""
import time

import requests

from app.crypto import decrypt_secret
from app.models.setting import get_setting

BASE = "https://api.codemagic.io"
# Codemagic build states → ours. Anything else (queued, preparing, fetching, building, testing, publishing,
# finishing…) is still running.
DONE = {"finished": "ready", "warning": "ready", "failed": "failed", "timeout": "failed", "skipped": "failed",
        "canceled": "canceled", "cancelled": "canceled"}
FILE_ENDINGS = (".apk", ".aab", ".ipa")


class CodemagicError(Exception):
    pass


def token():
    t = decrypt_secret(get_setting("codemagic_token_enc") or "")
    if not t:
        raise CodemagicError("No Codemagic token yet — add it in Settings → Codemagic.")
    return t


def api(method, path, json=None):
    try:
        r = requests.request(method, BASE + path, json=json, timeout=30,
                             headers={"x-auth-token": token(), "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise CodemagicError(f"Codemagic did not answer ({e.__class__.__name__}).")
    if r.status_code in (401, 403):
        raise CodemagicError("Codemagic refused the token — make a new one (Account settings → API token) and save it in Settings.")
    if r.status_code >= 400:
        raise CodemagicError(f"Codemagic answered {r.status_code}: {r.text[:200]}")
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


def check():
    """The token works. Returns how many apps the account has."""
    return len(api("GET", "/apps").get("applications") or [])


def app_id_for(repo_full_name):
    """The Codemagic app for this GitHub repo (found by name); added from the repo when it isn't there yet."""
    name = repo_full_name.split("/")[-1].lower()
    for a in api("GET", "/apps").get("applications") or []:
        if (a.get("appName") or "").lower() == name:
            return a.get("_id")
    try:
        out = api("POST", "/apps", {"repositoryUrl": f"https://github.com/{repo_full_name}.git"})
    except CodemagicError as e:
        raise CodemagicError(f"{e} — in Codemagic press “Add application”, pick GitHub and the repo {repo_full_name}, then try again.")
    app_id = out.get("_id") or (out.get("application") or {}).get("_id")
    if not app_id:
        raise CodemagicError(f"Codemagic did not add {repo_full_name}. Add it there by hand (Add application → GitHub), then try again.")
    return app_id


def start_build(app_id, workflow_id, branch, variables):
    out = api("POST", "/builds", {"appId": app_id, "workflowId": workflow_id, "branch": branch,
                                  "environment": {"variables": dict(variables)}})
    if not out.get("buildId"):
        raise CodemagicError("Codemagic did not start the build.")
    return out["buildId"]


def build_state(build_id):
    """(our state: building | ready | failed | canceled, [artifact dicts with name / url], Codemagic's own status)."""
    out = api("GET", f"/builds/{build_id}")
    b = out.get("build") or out
    raw = str(b.get("status") or "").lower()
    files = [a for a in (b.get("artefacts") or b.get("artifacts") or [])
             if str(a.get("name") or a.get("url") or "").lower().endswith(FILE_ENDINGS) and a.get("url")]
    return DONE.get(raw, "building"), files, raw


def public_url(artifact_url, days=7):
    """A download link anyone can open (no Codemagic login) until it expires. Returns (url, expires unix seconds)."""
    path = artifact_url.split("api.codemagic.io", 1)[-1] if "api.codemagic.io" in artifact_url else artifact_url
    if not path.startswith("/artifacts/"):
        raise CodemagicError("Unexpected artifact address from Codemagic.")
    expires = int(time.time()) + days * 86400
    out = api("POST", path + "/public-url", {"expiresAt": expires})
    if not out.get("url"):
        raise CodemagicError("Codemagic did not give a download link.")
    return out["url"], expires


def cancel(build_id):
    api("POST", f"/builds/{build_id}/cancel")
