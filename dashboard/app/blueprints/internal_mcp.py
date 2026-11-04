"""The beta tools as an MCP server for Claude Code (CHANGES Step 2). Streamable-HTTP transport, JSON responses only.

Reachable only from this machine: Claude Code connects straight to Gunicorn on 127.0.0.1 (no X-Forwarded-For); a
request through the web server carries that header and is refused. Every call needs the running job's one-time key
(Bearer), which ties it to ONE project. CSRF-exempt on purpose (no browser, no session) — see the app factory."""
from flask import Blueprint, jsonify, request

from app import beta_tools
from app.crypto import sha256
from app.models import Job

bp = Blueprint("internal_mcp", __name__)
JOB_KINDS = ("beta_setup", "beta_deploy", "go_live", "live_convert", "live_steps", "change_debug", "beta_api")


def _err(id_, code, message, status=200):
    return jsonify({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}), status


def _handle(job, msg):
    method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if id_ is None:                                     # a notification (e.g. notifications/initialized)
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "aidash-beta", "version": "1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": beta_tools.tool_list(job)}}
    if method == "tools/call":
        text, is_error = beta_tools.call(job, params.get("name"), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": f"Unknown method {method}"}}


@bp.route("/internal/mcp", methods=["GET", "POST", "DELETE"])
def mcp():
    if request.headers.get("X-Forwarded-For") or request.remote_addr not in ("127.0.0.1", "::1"):
        return ("Not found", 404)
    if request.method != "POST":
        return ("", 405)
    token = request.headers.get("Authorization", "")
    token = token[7:].strip() if token.startswith("Bearer ") else ""
    job = Job.query.filter_by(tool_token_hash=sha256(token)).first() if token else None
    if job is None or job.state != "running" or job.kind not in JOB_KINDS:
        return _err(None, -32001, "No running beta job for this key.", 401)
    msg = request.get_json(silent=True)
    if isinstance(msg, list):
        out = [r for r in (_handle(job, m) for m in msg if isinstance(m, dict)) if r is not None]
        return (jsonify(out), 200) if out else ("", 202)
    if not isinstance(msg, dict):
        return _err(None, -32700, "Parse error", 400)
    res = _handle(job, msg)
    return (jsonify(res), 200) if res is not None else ("", 202)
