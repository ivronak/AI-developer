"""Reaching servers over SSH (F07 / F08 / F10 / F12). The dashboard has ONE key pair of its own (instance/ssh/); the owner
adds its public key to each server once. A server's host key is pinned on the first successful test, so a swapped
server is refused afterwards. Scripts are piped to `bash -se` on the server — they are written by the owner / dashboard
templates, never by Claude. Tests replace run() / pipe() / keyscan() — they never open a real connection."""
import re
import shlex
import subprocess
from pathlib import Path

from flask import current_app

TIMEOUT = 900
HOST_RE = re.compile(r"^[A-Za-z0-9.\-:]{1,190}$")
USER_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class RemoteError(Exception):
    pass


def _dir():
    d = Path(current_app.instance_path) / "ssh"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def key_path():
    return _dir() / "dashboard_ed25519"


def ensure_key():
    """Create the dashboard's own SSH key pair once. Returns the public key line."""
    k = key_path()
    if not k.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "ai-project-dashboard", "-f", str(k), "-q"],
                       check=True, capture_output=True, timeout=30)
        k.chmod(0o600)
    return Path(str(k) + ".pub").read_text().strip()


def validate(host, user, port):
    if not HOST_RE.match(host or ""):
        return "The server address can only have letters, numbers, dots, dashes and colons."
    if not USER_RE.match(user or ""):
        return "The SSH user name can only have letters, numbers, dots, dashes and underscores."
    if not 1 <= int(port or 0) <= 65535:
        return "The SSH port must be between 1 and 65535."
    return None


def _known_hosts(server):
    f = _dir() / f"known_hosts_{server.id}"
    f.write_text(server.host_key or "")
    f.chmod(0o600)
    return f


def ssh_args(server):
    return ["ssh", "-i", str(key_path()), "-p", str(server.port), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f'UserKnownHostsFile="{_known_hosts(server)}"',   # quoted: ssh splits this value on spaces
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", f"{server.ssh_user}@{server.host}"]


def keyscan(server):
    """The server's host keys, to pin on the first test."""
    try:
        r = subprocess.run(["ssh-keyscan", "-p", str(server.port), "-T", "10", server.host], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RemoteError("The server did not answer.")
    lines = "\n".join(l for l in r.stdout.splitlines() if l and not l.startswith("#"))
    if not lines:
        raise RemoteError("Could not read the server's SSH key — check the address and port.")
    return lines


def run(server, script, timeout=TIMEOUT):
    """Run a bash script on the server. Returns (exit code, output). Raises RemoteError if it can't connect."""
    ensure_key()
    try:
        r = subprocess.run(ssh_args(server) + ["bash", "-se"], input=script, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RemoteError(f"The server took longer than {timeout // 60} minutes.")
    out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
    if r.returncode == 255:
        raise RemoteError("Could not sign in to the server over SSH — is the dashboard's public key added for this user? "
                          + (out.splitlines()[-1][:150] if out else ""))
    return r.returncode, out[-8000:]


def pipe(src, src_cmd, dst, dst_cmd, timeout=3600):
    """Stream `src_cmd` output on one server into `dst_cmd` on another (e.g. a database copy). Returns error text or ''."""
    ensure_key()
    a = subprocess.Popen(ssh_args(src) + [src_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    b = subprocess.Popen(ssh_args(dst) + [dst_cmd], stdin=a.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    a.stdout.close()
    try:
        _, b_err = b.communicate(timeout=timeout)
        a_err = a.stderr.read()
        a.wait(timeout=30)
    except subprocess.TimeoutExpired:
        a.kill()
        b.kill()
        return "The copy took longer than an hour and was stopped."
    if a.returncode != 0:
        return "Reading the live database failed: " + a_err.decode(errors="replace").strip()[-300:]
    if b.returncode != 0:
        return "Loading into the beta database failed: " + b_err.decode(errors="replace").strip()[-300:]
    return ""


def push_dir(server, local_dir, remote_dir, timeout=1800):
    """Copy a local folder's files to a fresh folder on the server (tar over SSH; .git is left out). Returns error text or ''."""
    ensure_key()
    a = subprocess.Popen(["tar", "--exclude=./.git", "-C", str(local_dir), "-czf", "-", "."], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    q = shlex.quote(remote_dir)
    b = subprocess.Popen(ssh_args(server) + [f"rm -rf {q} && mkdir -p {q} && tar -xzf - -C {q}"], stdin=a.stdout,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    a.stdout.close()
    try:
        _, b_err = b.communicate(timeout=timeout)
        a.wait(timeout=30)
    except subprocess.TimeoutExpired:
        a.kill()
        b.kill()
        return "Copying the code to the server took too long."
    if a.returncode != 0 or b.returncode != 0:
        return "Copying the code to the server failed: " + (b_err.decode(errors="replace").strip()[-300:] or "tar error")
    return ""
