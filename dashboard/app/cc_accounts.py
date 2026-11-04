"""The "Claude accounts" page: every Claude that can work for the dashboard, which login / account it uses, and in plain words
what each one can do, what it asks you first, and what it can't do.

Three kinds (2026-11-04, the setup-token is retired): the web-login Claude accounts (app/cc_logins.py — one card each, any
number), the VPS Claude (the one you talk to in the Claude app, running as root; reported by the hourly root job, cc_models)
and the Claude API key.

Every statement in CAPABILITIES was checked against the code (claude_code.py, agent.py, beta_tools.py, betas.py, releases.py).
Keep it that way: the owner trusts this page. When a rule in the code changes, change the matching cell here."""
import re
from decimal import Decimal

from app import cc_models
from app.crypto import decrypt_secret
from app.models.setting import get_setting

YES, ASK, NO = "yes", "ask", "no"
ACCOUNT_KEYS = ("web", "vps_claude", "api")

_SHELL_REACH = "Its editing tools stay in the project's copy; its shell reaches whatever the server user can read, including other projects' copies"
_GO_LIVE_NOTE = "Only your Go Live button. An approved live command can still deploy by hand, without Go Live's snapshot and automatic switch-back"
_BETA_NOTE = ("Automatic once the change is saved to GitHub and its tests pass. If the project has no beta yet, the dashboard first makes one "
              "and copies your live data into it")
_ROOT_CMD_NOTE = ("You read the exact command AND type a fresh authenticator code for it: one command, one code, valid 2 minutes. "
                  "Off until you link the authenticator app on the server")
_ROOT_NOTE = ("Not directly. The beta tools do root work for the BETA site through the dashboard: make it, lock it, start its services, "
              "restart the web server (every website, for a few seconds)")
_GUARDED = "on a project whose chat has a client's or developer's message it asks you first"

# What each Claude can do. (state, note) per kind, in the order of ACCOUNT_KEYS. States: YES / ASK (you get a card first) / NO.
CAPABILITIES = [
    ("Read and change the project's files",
     [(YES, _SHELL_REACH), (YES, "Anywhere on the server"), (YES, "With the dashboard's own file tools")]),
    ("Search the web and read web pages",
     [(YES, f"Without asking on your own projects; {_GUARDED}. A shell command like curl is never asked"), (YES, ""), (NO, "")]),
    ("Use your claude.ai connectors (Mobbin, Docs, …)",
     [(YES, f"The connectors of that account, without asking on your own projects; {_GUARDED} (a card for each call)"),
      (YES, "The connectors of its own account"), (NO, "")]),
    ("Run commands on the BETA site, as its own user",
     [(YES, "No card — the beta is a copy"), (YES, ""), (NO, "")]),
    ("Run a command on the LIVE site, as the site's own user",
     [(ASK, "You read the exact command first"), (YES, ""), (NO, "")]),
    ("Run ANY command as root on the server, even on other websites",
     [(ASK, _ROOT_CMD_NOTE), (YES, "Without asking"), (NO, "")]),
    ("Open and debug the beta site (requests, logs)",
     [(YES, ""), (YES, ""), (NO, "")]),
    ("Put a change on beta",
     [(YES, _BETA_NOTE), (YES, "By hand"), (YES, _BETA_NOTE)]),
    ("Go Live",
     [(NO, _GO_LIVE_NOTE), (YES, "By hand, on the server"), (NO, "Only your Go Live button")]),
    ("Act as root on its own: restart services, change server settings, touch other websites",
     [(NO, _ROOT_NOTE), (YES, "Every website on this server"), (NO, "")]),
    ("Read the dashboard's database and secrets",
     [(NO, "The login files are another matter — see the last row"), (YES, "Root can read everything"),
      (NO, "But the API key is in its environment: a run that goes wrong could read it (billed per use)")]),
    ("Use or copy your Claude account (for example create API keys)",
     [(YES, "Every account's login file is in the server user's home. EVERY run as that user — any account's runs, tests, installs — "
            "can read all of them. An automatic beta setup (as the beta website's user) gets the default account's short-lived access token"),
      (YES, "Root's own login"), (NO, "An API key, billed per use")]),
]

# the same pattern as the root job: "name: address - ✔ Connected" (the address may hold spaces for a local command, e.g. "npx -y x")
MCP_LINE = re.compile(r"^(?P<name>.+?):\s+.+?\s-\s+(?P<mark>✔|✘|!)(?:\s+(?P<text>.*))?$")


def parse_mcp(text):
    """`claude mcp list` → [{"name": "Mobbin", "status": "connected" | "needs-auth" | "failed"}]; names only, no addresses.
    The status comes from the mark: ✔ connected, ! needs a sign-in, ✘ failed."""
    out = []
    for line in (text or "").splitlines():
        m = MCP_LINE.match(line.strip())
        if not m:
            continue
        name = re.sub(r"^claude\.ai\s+", "", m.group("name")).strip()[:60]
        status = {"✔": "connected", "!": "needs-auth"}.get(m.group("mark"), "failed")
        if name:
            out.append({"name": name, "status": status})
    return out[:30]


def mask_email(email):
    """j***@example.com — never the whole address (a very short name shows no letter at all)."""
    if not isinstance(email, str) or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    return ((local[:1] if len(local) > 2 else "") + "***@" + domain[:50])[:60]


def _api_key_set():
    return bool(decrypt_secret(get_setting("claude_key_enc") or ""))


def _caps(index):
    """The can / asks first / can't lists of one Claude, ready for the page."""
    groups = {YES: [], ASK: [], NO: []}
    for label, cells in CAPABILITIES:
        state, note = cells[index]
        groups[state].append({"label": label, "note": note})
    return groups


def _connectors(conns, none_note):
    """(list or None, note): an empty list means 'no connectors', which is an answer; None means 'not known'."""
    if conns:
        return conns, ""
    return None, ("No connectors on this account." if conns == [] else none_note)


def build(month_cost=None, api_projects=0, cc_projects=()):
    """Everything the page shows: one dict per web-login account, then the VPS Claude, then the API key."""
    from app import cc_logins
    cat = cc_models.load()
    vps = cat.get("vps")
    accounts = []
    key_ok = _api_key_set()

    for acc in cc_logins.accounts():
        if acc.status == "ok":
            status = ("Logged in", "ok")
            facts = [("Plan", (acc.plan or "—").title()), ("Account", acc.email_hint or "—"),
                     ("Models", str(len(acc.models or []))), ("Login renews in", cc_logins.fmt_left(cc_logins.expires_in(acc)))]
        elif acc.status == "logged_out":
            status, facts = ("Logged out", "bad"), [("Fix", "Settings → Claude Code → Log in")]
        elif acc.status == "error":
            status, facts = ("Could not check", "warn"), [("Problem", acc.last_error or "no answer")]
        else:
            status, facts = ("Not checked yet", "muted"), [("Plan", "Press “Check now” in Settings")]
        facts.append(("Used for", "Chat messages that pick it" + (" — and every message that picks nothing, plus beta, release "
                                                                   "and live-steps runs (the default account)" if acc.is_default else "")))
        if acc.checked_at:
            facts.append(("Checked", acc.checked_at.strftime("%Y-%m-%d %H:%M") + " UTC"))
        conns, conn_note = _connectors(acc.connectors, "Press “Check now” in Settings to see them.")
        accounts.append({
            "key": f"acc{acc.id}", "title": acc.name, "icon": "users",
            "about": "A Claude account signed in through the web on this server. It has that account's claude.ai connectors "
                     "and every model of its plan — it is a full login: see the last row.",
            "status": status, "facts": facts, "connectors": conns, "connectors_note": conn_note,
            "in_use": acc.is_default, "caps": _caps(0), "link": ("Manage accounts", "settings.index", "claude-code"),
        })
    if not accounts:
        accounts.append({
            "key": "web", "title": "No Claude account yet", "icon": "users",
            "about": "Add one in Settings → Claude Code: a name, then Log in with your browser. Until then Claude Code uses the API key.",
            "status": ("Not added", "bad"), "facts": [], "connectors": None, "connectors_note": "—",
            "in_use": None, "caps": _caps(0), "link": ("Add an account", "settings.index", "claude-code"),
        })

    if vps is None:
        v_status, v_facts = ("Unknown", "muted"), [("Plan", "The hourly job has not reported yet")]
        v_conns, v_note = None, "Not reported yet."
    else:
        rc = vps["remote_control"]
        if cat.get("stale"):
            v_status = ("Unknown — report is out of date", "muted")
        else:
            v_status = ("Service running", "ok") if rc == "active" else (("Service stopped", "warn") if rc == "inactive" else ("Unknown", "muted"))
        v_facts = [("Plan", vps["plan"].title() or "—"), ("Account", vps["account_hint"] or "—"), ("Reported", cat.get("fetched_at") or "—")]
        v_conns, v_note = _connectors(vps.get("connectors"), "Not reported yet.")
    v_facts.append(("How you use it", "In the Claude app → Code → “Claude VPS Full Control”. It works through the Claude app, not through the "
                                      "dashboard: the dashboard can't start it, and its permission prompts are in the Claude app, not cards here."))
    accounts.append({
        "key": "vps_claude", "title": "VPS Claude", "icon": "rocket",
        "about": "The Claude you talk to in the Claude app. It runs as root on this server with its own Claude login, "
                 "so it can do anything here — including on every other website on this server.",
        "status": v_status, "facts": v_facts, "connectors": v_conns, "connectors_note": v_note,
        "in_use": None, "caps": _caps(1),
    })

    has_acc = any(a["key"].startswith("acc") for a in accounts)
    api_facts = [("Plan", "Pay per use (API)"),
                 ("Used for", f"{api_projects} project{'' if api_projects == 1 else 's'} set to “Claude API”"
                              + ("" if has_acc else (" — and every Claude Code run while no Claude account is added" if key_ok else "")))]
    if month_cost is not None:
        api_facts.append(("Spent this month", f"${Decimal(month_cost):.2f}"))
    accounts.append({
        "key": "api", "title": "Claude API key", "icon": "coin",
        "about": "A pay-per-use API key. Projects set to “Claude API” make each change with the dashboard's own tools — no connectors, no shell.",
        "status": ("Saved", "ok") if key_ok else ("Not saved", "bad"), "facts": api_facts,
        "connectors": None, "connectors_note": "None.", "in_use": None, "caps": _caps(2),
        "link": ("Change the key", "settings.index", "claude"),
    })
    return {"accounts": accounts, "cc_projects": list(cc_projects)}
