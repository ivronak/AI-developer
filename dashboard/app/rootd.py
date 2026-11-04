"""The dashboard's side of root commands: talks to the root service (deploy/aidash-rootd) over its unix socket.

The dashboard never holds root. It can only (1) pass on the 6-digit authenticator code the owner typed, together with the SHA-256 of
the exact command the owner was shown, and get back a one-use ticket; (2) hand that ticket and the same command back to run it. The
service checks the code, ties the ticket to that one command and writes every step to /var/log/aidash-rootd.log."""
import hashlib
import json
import socket

from flask import current_app

DEFAULT_SOCKET = "/run/aidash-rootd/rootd.sock"


class RootdError(Exception):
    """The root service can't be reached, or its answer is not usable. `sent` says whether the request had left before it failed:
    False means the service never saw it, so a command cannot have run."""

    def __init__(self, message="", sent=False):
        super().__init__(message)
        self.sent = sent


def cmd_sha(cmd):
    return hashlib.sha256(cmd.encode("utf-8")).hexdigest()


def _call(req, timeout=15):
    path = current_app.config.get("ROOTD_SOCKET") or DEFAULT_SOCKET
    buf = b""
    sent = False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except (AttributeError, OSError):
        raise RootdError("this system has no unix sockets") from None
    try:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        sent = True
        while not buf.endswith(b"\n") and len(buf) < 2_000_000:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError as e:
        raise RootdError(e.__class__.__name__, sent=sent) from None
    finally:
        s.close()
    try:
        out = json.loads(buf.decode("utf-8"))
    except ValueError:
        raise RootdError("no valid answer", sent=True) from None
    if not isinstance(out, dict):
        raise RootdError("no valid answer", sent=True)
    return out


def status():
    """{"available", "enrolled", "locked", "retry_after"} — never raises."""
    try:
        r = _call({"op": "status"}, timeout=5)
    except RootdError as e:
        return {"available": False, "enrolled": False, "locked": False, "retry_after": 0, "error": str(e)}
    try:
        retry = int(r.get("retry_after") or 0)
    except (TypeError, ValueError):
        retry = 0
    return {"available": bool(r.get("ok")), "enrolled": bool(r.get("enrolled")), "locked": bool(r.get("locked")), "retry_after": retry}


def authorize(code, cmd, by):
    """The owner's code for exactly `cmd`. Returns (ticket, "") or ("", why not). Raises RootdError when the service doesn't answer."""
    r = _call({"op": "authorize", "code": str(code), "cmd_sha": cmd_sha(cmd), "by": str(by or "")[:60]})
    if r.get("ok") and isinstance(r.get("ticket"), str) and r["ticket"]:
        return r["ticket"][:64], ""
    return "", str(r.get("error") or "That code was not accepted.")[:300]


def execute(ticket, cmd, timeout):
    """Run the approved command. Returns the service's answer: {"ok": True, "exit", "output"} or {"ok": False, "error"} (nothing ran).
    Raises RootdError when the connection fails — if its `sent` is True the command may have run."""
    return _call({"op": "exec", "ticket": ticket, "cmd": cmd, "timeout": timeout}, timeout=timeout + 60)
