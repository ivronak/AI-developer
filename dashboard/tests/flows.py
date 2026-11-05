"""End-to-end flow checks with the Flask test client, on a fresh throwaway database (never the dev / live DB).
Run:  cd dashboard && source venv/bin/activate && PYTHONPATH=. python tests/flows.py"""
import os
import re
import sys
import tempfile
import time

TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/flows.db"

import pyotp  # noqa: E402
from flask import g  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from app import create_app  # noqa: E402
from app.crypto import decrypt_secret  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import ActivityLog, Alert, Project, User  # noqa: E402

app = create_app()
app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SERVER_NAME="localhost")
app.instance_path = os.path.join(TMP, "instance")      # workspaces / uploads of the test stay in the temp folder
PASSED = []


class Client(FlaskClient):
    """Clears g before each request — Flask-Login caches the user on g, which leaks between clients in one context."""
    def open(self, *a, **kw):
        for k in list(g.__dict__.keys()):
            g.__dict__.pop(k, None)
        return super().open(*a, **kw)


app.test_client_class = Client


def ok(cond, label):
    if not cond:
        print("FAIL:", label)
        sys.exit(1)
    PASSED.append(label)


def code_for(user, offset=0):
    db.session.refresh(user)
    return pyotp.TOTP(decrypt_secret(user.totp_secret_enc)).at(int(time.time()) + offset)


def login(c, ident, pw):
    return c.post("/login", data={"login": ident, "password": pw})


with app.app_context():
    from flask_migrate import upgrade
    upgrade()
    import seed
    seed.run_seed()
    owner = User.query.filter_by(role="owner").one()

    c = app.test_client()
    r = c.get("/projects")
    ok(r.status_code == 302 and "/login" in r.headers["Location"], "signed-out user is sent to /login")

    # wrong password ×5 → locked + owner alert
    for i in range(4):
        r = login(c, "owner@demo.test", "nope")
        ok(r.status_code == 200 and b"Wrong email/mobile or password" in r.data, f"wrong password {i + 1} shows a generic error")
    r = login(c, "owner@demo.test", "nope")
    ok(b"Sign-in locked" in r.data, "5th wrong password locks sign-in")
    ok(Alert.query.filter_by(kind="login_locked").count() == 1, "lock creates an owner alert")
    r = login(c, "owner@demo.test", "demo1234")
    ok(b"Sign-in locked" in r.data, "right password is refused while locked")
    owner.locked_until = None
    db.session.commit()

    # first sign-in: authenticator required → setup
    r = login(c, "owner@demo.test", "demo1234")
    ok(r.status_code == 302 and r.headers["Location"].endswith("/login/setup"), "password OK → authenticator setup")
    r = c.get("/login/setup")
    ok(b"<svg" in r.data and b"Set up the authenticator" in r.data, "setup page shows a QR code")
    r = c.post("/login/setup", data={"code": "000000"})
    ok(b"That code is not right" in r.data, "wrong setup code is refused")
    setup_code = code_for(owner)
    r = c.post("/login/setup", data={"code": setup_code})
    ok(r.status_code == 302 and r.headers["Location"].endswith("/account/recovery-codes"), "right code turns it on")
    r = c.get("/account/recovery-codes")
    codes = re.findall(rb"<span>([0-9a-f]{4}-[0-9a-f]{4})</span>", r.data)
    ok(len(codes) == 8, "8 recovery codes shown once")
    ok(c.get("/account/recovery-codes").status_code == 302, "recovery codes are not shown a second time")
    ok(c.get("/projects").status_code == 200, "signed in after setup")
    ok(c.get("/users").status_code == 200, "owner opens Users & log")

    c.post("/logout")
    ok(c.get("/projects").status_code == 302, "sign out works")

    # second sign-in: the same 30-second code can't be reused; next code works; recovery code works once
    r = login(c, "+91 98765 44821", "demo1234")
    ok(r.headers["Location"].endswith("/login/code"), "sign in with mobile number → code step")
    r = c.post("/login/code", data={"code": setup_code, "use": "app"})        # the very code used at setup (not "now")
    ok(b"That code is not right" in r.data, "a code that was already used is refused (replay)")
    r = c.post("/login/code", data={"code": code_for(owner, 30), "use": "app"})
    ok(r.status_code == 302 and r.headers["Location"].endswith("/projects"), "next code signs in")
    c.post("/logout")
    login(c, "owner@demo.test", "demo1234")
    r = c.post("/login/code", data={"code": codes[0].decode(), "use": "recovery"})
    ok(r.status_code == 302, "recovery code signs in")
    db.session.refresh(owner)
    ok(len(owner.recovery_codes) == 7, "used recovery code is gone")
    c.post("/logout")
    login(c, "owner@demo.test", "demo1234")
    r = c.post("/login/code", data={"code": codes[0].decode(), "use": "recovery"})
    ok(b"That code is not right" in r.data, "a recovery code works only once")
    r = c.post("/login/code", data={"code": codes[1].decode(), "use": "recovery"})
    ok(r.status_code == 302, "owner signed in again")

    # owner adds a developer → one-time link → developer sets password → sets up authenticator
    r = c.post("/users/new", data={"name": "Developer 9", "email": "dev9@demo.test", "mobile": "+91 90000 00009", "projects": ["2", "5"]})
    r = c.get("/users")
    m = re.search(rb"/setup/([A-Za-z0-9_\-]+)", r.data)
    ok(m is not None, "new user gets a one-time setup link")
    token = m.group(1).decode()
    ok(b"/setup/" not in c.get("/users").data, "setup link is shown only once")

    d = app.test_client()
    ok(b"Set your password" in d.get(f"/setup/{token}").data, "setup link opens")
    r = d.post(f"/setup/{token}", data={"password": "short", "password2": "short"})
    ok(b"at least 10" in r.data, "short password refused")
    r = d.post(f"/setup/{token}", data={"password": "a-long-password-1", "password2": "a-long-password-1"})
    ok(r.status_code == 302, "password saved")
    ok(b"does not work" in d.get(f"/setup/{token}").data, "setup link works only once")
    dev = User.query.filter_by(email="dev9@demo.test").one()
    login(d, "dev9@demo.test", "a-long-password-1")
    d.post("/login/setup", data={"code": code_for(dev)})
    ok(d.get("/projects").status_code == 200, "developer signed in")
    ok(d.get("/users").status_code == 403, "developer can't open Users & log (server-side 403)")
    ok(d.post("/settings/security", data={"require_2fa": "0"}).status_code == 403, "developer can't change security settings")
    from app.auth import can_see_project
    ok(can_see_project(dev, 2) and not can_see_project(dev, 1), "developer sees only assigned projects")

    # owner turns the developer's access off → their session ends at once
    c.post(f"/users/{dev.id}/access", data={"on": "0"})
    ok(d.get("/projects").status_code == 302, "access off signs the developer out immediately")
    r = login(d, "dev9@demo.test", "a-long-password-1")
    ok(b"access is turned off" in r.data, "turned-off account can't sign in")
    c.post(f"/users/{dev.id}/access", data={"on": "1"})

    # owner resets the developer's authenticator → setup again at next sign-in
    c.post(f"/users/{dev.id}/reset-2fa")
    r = login(d, "dev9@demo.test", "a-long-password-1")
    ok(r.headers["Location"].endswith("/login/setup"), "after reset the developer sets the authenticator up again")

    # authenticator optional: a user without it signs in with the password only; they can't turn it off while required
    c.post("/settings/security", data={"require_2fa": "0"})
    e = app.test_client()
    r = login(e, "dev2@demo.test", "demo1234")
    ok(r.status_code == 302 and r.headers["Location"].endswith("/projects"), "optional: password-only sign-in works")
    c.post("/settings/security", data={"require_2fa": "1"})
    r = login(app.test_client(), "dev2@demo.test", "demo1234")
    ok(r.headers["Location"].endswith("/login/setup"), "required again: setup is asked")

    # My account: page, new recovery codes, can't turn off while required, can when optional, password change
    ok(c.get("/account").status_code == 200 and b"Authenticator app" in c.get("/account").data, "My account opens")

    from app.auth import new_totp_secret
    if not owner.totp_enabled:                      # an earlier check turned it off — Go Live needs it
        new_totp_secret(owner)
        owner.totp_enabled = True
        db.session.commit()

    def fresh_code():
        owner.totp_last_step = 0      # test only: forget the replay guard so the current code can be used again
        db.session.commit()
        return code_for(owner)
    r = c.post("/account/2fa/codes", data={"code": fresh_code()})
    ok(r.headers["Location"].endswith("/account/recovery-codes"), "new recovery codes with a current code")
    r = c.post("/account/2fa/off", data={"code": fresh_code()})
    db.session.refresh(owner)
    ok(owner.totp_enabled, "can't turn the authenticator off while it is required")
    c.post("/settings/security", data={"require_2fa": "0"})
    c.post("/account/2fa/off", data={"code": fresh_code()})
    db.session.refresh(owner)
    ok(not owner.totp_enabled, "optional: owner turns their own authenticator off")
    c.post("/settings/security", data={"require_2fa": "1"})
    r = c.post("/account/password", data={"current": "wrong", "password": "another-long-pw", "password2": "another-long-pw"})
    db.session.refresh(owner)
    ok(owner.check_password("demo1234"), "password change needs the current password")
    c.post("/account/password", data={"current": "demo1234", "password": "another-long-pw", "password2": "another-long-pw"})
    db.session.refresh(owner)
    ok(owner.check_password("another-long-pw"), "password changed")
    ok(c.get("/projects").status_code == 200, "this device stays signed in after a password change")

    # ---------- Step 4: projects overview + project page ----------
    from app.models import Change, Environment
    r = c.get("/projects")
    ok(r.status_code == 200 and r.data.count(b'class="pcard"') == 12, "owner sees all 12 project cards")
    ok(b"Parking Manager" in r.data and b"Live site down for 12 min" in r.data, "red project shows its incident")
    ok(b"Last backup is" in r.data, "stale backup shows as attention")
    ok(b"Pending 4" in r.data, "4 approvals pending")
    for pid in range(1, 13):
        ok(c.get(f"/projects/{pid}").status_code == 200, f"project page {pid} opens")
    r = c.get("/projects/1")
    ok(b"Search by vehicle number" in r.data and b"Tests: 24 / 24 passed" in r.data, "project page shows changes and tests")
    ok(c.get("/projects/999").status_code == 404, "unknown project is 404")

    dv = app.test_client()
    login(dv, "dev@demo.test", "demo1234")
    devu = User.query.filter_by(email="dev@demo.test").one()
    dv.post("/login/setup", data={"code": code_for(devu)})
    r = dv.get("/projects")
    ok(r.data.count(b'class="pcard"') == 3, "developer sees only their 3 projects")
    ok(dv.get("/projects/1").status_code == 404, "developer can't open an unassigned project")
    ok(dv.get("/projects/2").status_code == 200, "developer opens an assigned project")
    ok(dv.post("/changes/8/answer", data={"decision": "approved", "note": "x"}).status_code == 403, "developer can't record a client answer")
    dv.post("/projects/2/settings", data={"msg": "on", "pay": "sandbox", "test_numbers": ""})
    ok(db.session.get(Environment, db.session.get(Project, 2).env("beta").id).message_mode == "off", "developer can't turn beta messages on for real users")
    dv.post("/projects/2/settings", data={"msg": "test", "pay": "sandbox", "test_numbers": "+91 90000 00001"})
    db.session.expire_all()
    ok(db.session.get(Project, 2).env("beta").message_mode == "test", "developer can switch beta messages to test numbers")
    ok(dv.post("/projects/1/settings", data={"msg": "off", "pay": "sandbox"}).status_code == 404, "developer can't change an unassigned project")

    ch = Change.query.filter_by(title="Low-stock report as Excel").one()
    r = c.post(f"/changes/{ch.id}/answer", data={"decision": "approved", "note": ""})
    db.session.refresh(ch)
    ok(ch.status == "client", "approve without a note is refused")
    c.post(f"/changes/{ch.id}/answer", data={"decision": "approved", "note": "Client said OK on a call"})
    db.session.refresh(ch)
    ok(ch.status == "approved" and ch.approval.recorded_by_id == owner.id, "owner records the client's approval")
    ch2 = Change.query.filter_by(title="Barcode on the product label").one()
    c.post(f"/changes/{ch2.id}/answer", data={"decision": "changes", "note": "Bigger barcode"})
    db.session.refresh(ch2)
    ok(ch2.status == "changes" and ch2.messages[-1].body.endswith("Bigger barcode"), "owner records the client's changes request")
    ok(ActivityLog.query.filter_by(action="Approved for client").count() == 1, "recorded answer is logged")
    r = c.get("/projects")
    ok(b"Pending 2" in r.data and b"Approved 2" in r.data, "approvals panel updates")

    # ---------- Step 5: GitHub sync (fake GitHub — tests never call the real API) ----------
    import app.github as gh
    from app.models.setting import get_setting
    seeded = [p.github_repo for p in Project.query.order_by(Project.id).all()]
    fake = [{"full_name": r, "name": r.split("/")[1], "private": True, "default_branch": "main", "language": "PHP",
             "archived": False, "pushed_at": "2026-10-14T08:00:00Z"} for r in seeded[:-1]]           # last one "deleted"
    fake.append({"full_name": "company-org/new-crm", "name": "new-crm", "private": True, "default_branch": "main",
                 "language": "Dart", "archived": False, "pushed_at": "2026-10-14T09:00:00Z"})
    fake.append({"full_name": "company-org/old-thing", "name": "old-thing", "private": False, "default_branch": "main",
                 "language": "PHP", "archived": True, "pushed_at": None})
    seen_args = {}

    def fake_fetch(owner, kind, token):
        seen_args.update(owner=owner, kind=kind, token=token)
        return fake
    gh.fetch_repos = fake_fetch
    gh.check_token = lambda token: "test-user"

    ok(d.get("/settings").status_code in (302, 403), "developer can't open Settings")
    ok(c.get("/settings").status_code == 200, "owner opens Settings")
    c.post("/settings/github", data={"owner": "company-org", "owner_type": "org", "token": "github_pat_secret1234"})
    enc = get_setting("github_token_enc")
    ok(enc and "secret" not in enc, "GitHub token is stored encrypted")
    r = c.get("/settings")
    ok(b"1234" in r.data and b"github_pat_secret1234" not in r.data, "only the last 4 characters of the token are shown")
    r = c.post("/settings/github/sync", follow_redirects=True)
    ok(seen_args == {"owner": "company-org", "kind": "org", "token": "github_pat_secret1234"}, "sync uses the saved owner + decrypted token")
    newp = Project.query.filter_by(github_repo="company-org/new-crm").one()
    ok(newp.needs_setup and newp.name == "New Crm" and newp.tech_stack == ["Flutter"], "a new repo becomes a project that needs setup")
    ok(Project.query.filter_by(github_repo="company-org/old-thing").first() is None, "archived repos are skipped")
    gone = Project.query.filter_by(github_repo=seeded[-1]).one()
    ok(gone.repo_missing, "a project whose repo disappeared is flagged, not deleted")
    ok(Project.query.count() == 13, "no project was deleted")
    c.post("/settings/github/sync")
    ok(Project.query.count() == 13, "a second sync adds nothing twice")
    ok(b"Needs setup" in c.get("/projects").data, "new project shows 'Needs setup' on the overview")
    ok(c.get(f"/projects/{newp.id}").status_code == 200, "the new project's page opens before setup")
    r = c.post(f"/projects/{newp.id}/details", data={"name": "New CRM", "client": "", "live_url": "crm.example.com", "beta_url": "beta.crm.example.com"})
    db.session.refresh(newp)
    ok(newp.needs_setup, "details without a client are refused")
    c.post(f"/projects/{newp.id}/details", data={"name": "New CRM", "client": "Client M", "live_url": "crm.example.com",
                                                 "beta_url": "beta.crm.example.com", "host_type": "vps", "repo": "company-org/new-crm",
                                                 "tech": "Laravel, Flutter", "active": "1"})
    db.session.refresh(newp)
    ok(not newp.needs_setup and newp.env("live").url == "https://crm.example.com" and newp.env("beta").branch == "beta",
       "owner fills in the details: environments are created")
    ok(dv.post(f"/projects/2/details", data={"name": "x"}).status_code == 403, "developer can't change project details")
    gh.fetch_repos = lambda *a: (_ for _ in ()).throw(gh.GitHubError("GitHub refused the token (401)."))
    r = c.post("/settings/github/sync", follow_redirects=True)
    ok(b"GitHub refused the token" in r.data and get_setting("github_last_result").startswith("Failed"), "a GitHub error is shown and saved")

    # ---------- Step 6: monitor + Telegram alerts (fake Telegram + fake web — no real network) ----------
    import app.monitor as mon
    import app.telegram as tg
    sent_msgs, tg_calls = [], []

    def fake_api(token, method, payload=None):
        tg_calls.append(method)
        if token != "123:GOOD":
            raise tg.TelegramError("Telegram refused the bot token. Copy it again from @BotFather.")
        if method == "getMe":
            return {"username": "test_alerts_bot"}
        if method == "getUpdates":
            code = get_setting("telegram_connect_code")
            return [{"message": {"text": "hello", "chat": {"id": 1, "first_name": "Stranger"}}},
                    {"message": {"text": code.lower(), "chat": {"id": 42, "first_name": "Owner", "last_name": "Phone"}}}]
        if method == "sendMessage":
            sent_msgs.append((payload["chat_id"], payload["text"]))
            return {}
        return {}
    tg.api = fake_api

    r = c.post("/settings/telegram", data={"token": "bad"}, follow_redirects=True)
    ok(b"Telegram refused the bot token" in r.data and not get_setting("telegram_token_enc"), "a wrong bot token is refused, not saved")
    c.post("/settings/telegram", data={"token": "123:GOOD"})
    ok(get_setting("telegram_token_enc") and "GOOD" not in get_setting("telegram_token_enc"), "bot token is stored encrypted")
    c.post("/settings/telegram/code")
    code = get_setting("telegram_connect_code")
    ok(code.startswith("CONNECT-"), "a one-time connect code is made")
    c.post("/settings/telegram/connect")
    chats = get_setting("telegram_chats")
    ok(chats == [{"id": 42, "name": "Owner Phone"}], "only the chat that sent the code is linked (not a stranger)")
    ok(get_setting("telegram_connect_code") == "", "the connect code works once")
    ok(sent_msgs and sent_msgs[-1][0] == 42, "a 'Connected' message is sent")
    c.post("/settings/telegram/test")
    ok("Test alert" in sent_msgs[-1][1] and Alert.query.filter_by(kind="test").count() == 1, "test alert reaches Telegram and the bell")

    # watch a real-looking project; fake the web answers
    pw = newp                                       # "New CRM", has a live address from the Step 5 checks
    c.post(f"/projects/{pw.id}/details", data={"name": "New CRM", "client": "Client M", "live_url": "crm.example.com",
                                               "beta_url": "beta.crm.example.com", "host_type": "vps", "repo": "company-org/new-crm",
                                               "tech": "Laravel", "active": "1", "watch": "1"})
    db.session.refresh(pw)
    ok(pw.monitor_enabled, "owner turns watching on")
    answers = []
    mon.fetch = lambda url: answers.pop(0)
    page_a = "<html><title>CRM</title><body>Welcome to the CRM. Today 14 Oct 2026, 12:01</body></html>"
    page_a2 = "<html><title>CRM</title><script>x=9</script><body>Welcome to the CRM. Today 15 Oct 2026, 09:59</body></html>"
    page_hacked = "<html><title>Hacked</title><body>Hacked by someone</body></html>"
    answers += [(200, 120, page_a)]
    ok(mon.check(pw) == "ok" and pw.env("live").page_hash, "first check saves the page fingerprint")
    answers += [(200, 110, page_a2)]
    ok(mon.check(pw) == "ok", "dates / numbers / scripts changing is not a page change")
    n_alerts = len(sent_msgs)
    answers += [(0, 12000, "")]
    ok(mon.check(pw) == "retry" and not pw.incident and len(sent_msgs) == n_alerts, "one failed check alone is not an alert")
    answers += [(503, 300, "")]
    ok(mon.check(pw) == "down" and pw.incident.startswith("Live site down"), "two failed checks in a row = down")
    ok("live site down" in sent_msgs[-1][1] and len(sent_msgs) == n_alerts + 1, "down alert sent once to Telegram")
    answers += [(0, 12000, "")]
    mon.check(pw)
    ok(len(sent_msgs) == n_alerts + 1, "no repeat alert while it stays down")
    ok(b"Live site down" in c.get("/projects").data, "down project shows red on the overview")
    answers += [(200, 100, page_a)]
    ok(mon.check(pw) == "ok" and not pw.incident and "back up" in sent_msgs[-1][1], "site back → red cleared + 'back up' alert")
    answers += [(200, 100, page_hacked), (200, 100, page_hacked)]
    ok(mon.check(pw) == "ok", "one changed look is not yet an alert")
    ok(mon.check(pw) == "changed" and pw.incident == "Home page changed unexpectedly", "changed twice in a row → alert")
    ok("home page changed" in sent_msgs[-1][1], "page-change alert sent")
    ok(b"This change is OK" in c.get(f"/projects/{pw.id}").data, "owner sees 'This change is OK'")
    answers += [(200, 100, page_hacked)]
    c.post(f"/projects/{pw.id}/accept-page")
    db.session.refresh(pw)
    ok(not pw.incident, "accepting the change clears the red")
    answers += [(200, 100, page_hacked)]
    ok(mon.check(pw) == "ok", "the accepted page is now normal")
    ok(Project.query.filter_by(monitor_enabled=True).count() == 1, "demo projects (made-up addresses) are not watched")
    answers += [(200, 90, page_hacked)]
    res = mon.run_all()
    ok(res == {"New CRM": "ok"} and get_setting("monitor_last_run"), "run_all checks only watched projects")

    # ---------- Step 7: chat + Claude agent (fake Claude, local git "GitHub" — no real network) ----------
    import io
    import subprocess
    from types import SimpleNamespace as NS
    import app.agent as agent_mod
    import app.workspace as wsmod
    from app.models import AiUsage, ChangeAttachment, ChangeMessage
    from app.crypto import encrypt_secret
    from app.models.setting import set_setting

    remotes = os.path.join(TMP, "remotes")
    bare = os.path.join(remotes, "company-org", "new-crm.git")
    os.makedirs(os.path.dirname(bare))
    seed_dir = os.path.join(TMP, "seedrepo")
    os.makedirs(seed_dir)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    run_git = lambda *a, cwd=seed_dir: subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True)
    open(os.path.join(seed_dir, "README.md"), "w").write("Hello CRM\n")
    open(os.path.join(seed_dir, ".env"), "w").write("DB_PASSWORD=secret\n")
    open(os.path.join(seed_dir, "check.py"), "w").write("print('Tests: 2 passed')\n")
    run_git("init", "-q", "-b", "main")
    run_git("add", "-A")
    run_git("commit", "-qm", "init")
    subprocess.run(["git", "clone", "-q", "--bare", seed_dir, bare], check=True, capture_output=True)
    app.config.update(GIT_REMOTE_TEMPLATE=remotes + "/{repo}.git", AGENT_SYNC=True)
    newp.default_branch = "main"
    newp.test_command = "python3 check.py"
    db.session.commit()

    calls = []
    script = []

    def resp(stop, *blocks, inp=1000, out=200):
        return NS(stop_reason=stop, content=list(blocks), model=None,   # None → the agent uses the model it asked for
                  usage=NS(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=0, cache_read_input_tokens=0))

    def tool(i, name, **inp):
        return NS(type="tool_use", id=f"t{i}", name=name, input=inp)

    class FakeStream:
        def __init__(self, kw):
            calls.append(kw)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get_final_message(self):
            return script.pop(0)

    fake_client = NS(beta=NS(messages=NS(stream=lambda **kw: FakeStream(kw))))
    agent_mod._client = lambda: fake_client

    script[:] = [
        resp("tool_use", NS(type="text", text="Looking."), tool(1, "str_replace_based_edit_tool", command="view", path="")),
        resp("tool_use", tool(2, "str_replace_based_edit_tool", command="view", path=".env"),
             tool(3, "str_replace_based_edit_tool", command="view", path="../../etc/passwd")),
        resp("tool_use", tool(4, "str_replace_based_edit_tool", command="str_replace", path="README.md", old_str="Hello CRM", new_str="Hello CRM v2")),
        resp("tool_use", tool(5, "run_tests")),
        resp("end_turn", NS(type="text", text="मैंने README में version बदला। Beta पर पहला पेज देखें।")),
    ]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = c.post(f"/projects/{newp.id}/chat", data={"message": "Show v2 on the home page", "preset": "best", "shots": (io.BytesIO(png), "screen.png")},
               content_type="multipart/form-data")
    ok(r.status_code == 302, "owner starts a change from the chat")
    ch7 = Change.query.filter_by(project_id=newp.id).order_by(Change.id.desc()).first()
    db.session.refresh(ch7)
    ok(ch7.agent_state == "done" and ch7.status == "passed", "Claude finished and the tests passed")
    ok(ch7.tests_passed == 2 and ch7.tests_total == 2, "test counts read from the output")
    ok(ch7.files == ["README.md"], "files changed are recorded")
    ok(ch7.branch.startswith(f"change/{ch7.id}-"), "the change has its own branch")
    ls = subprocess.run(["git", "ls-remote", "--heads", bare], capture_output=True, text=True).stdout
    ok(f"refs/heads/{ch7.branch}" in ls, "the branch was pushed to GitHub (local stand-in)")
    ok("refs/heads/main" in ls and subprocess.run(["git", "--git-dir", bare, "show", "main:README.md"], capture_output=True, text=True).stdout == "Hello CRM\n",
       "main is untouched")
    first = calls[0]
    ok(first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"} and first["fallbacks"] == "default",
       "Opus 5.5, adaptive thinking, server-side fallbacks")
    ok(first["messages"][0]["content"][0].get("cache_control") == {"type": "ephemeral"}, "project brief is cached")
    ok(any(b.get("type") == "image" for b in first["messages"][0]["content"]), "the screenshot is sent to Claude")
    ok("Never edit .env" in first["system"], "system prompt carries the safety rules")
    tool_results = [b for kw in calls for m in kw["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                    for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    by_id = {b["tool_use_id"]: b for b in tool_results}
    ok(by_id["t2"]["is_error"] and "secret" not in by_id["t2"]["content"].lower().replace("secret and git files", ""), ".env can't be read")
    ok(by_id["t3"]["is_error"], "a path outside the project is refused")
    ok(".env" not in by_id["t1"]["content"], ".env is hidden from the file list")
    ok(AiUsage.query.filter_by(change_id=ch7.id).count() == 5 and float(ch7.agent_cost_usd) > 0, "every call is recorded with its cost")
    last = ChangeMessage.query.filter_by(change_id=ch7.id, sender="claude").order_by(ChangeMessage.id.desc()).first()
    ok("README" in last.body, "Claude's plain-language note is in the chat")
    att = ChangeAttachment.query.first()
    ok(c.get(f"/attachments/{att.id}").status_code == 200, "owner can open the screenshot")
    ok(dv.get(f"/attachments/{att.id}").status_code == 404, "a developer without the project can't")
    ok(b"Claude stopped" not in c.get(f"/projects/{newp.id}?change={ch7.id}").data, "project page shows the finished change")
    from app.textfmt import simple_md
    md = str(simple_md("**Done** <script>x</script>\n\n- one `a<b`\n- two"))
    ok("<strong>Done</strong>" in md and "&lt;script&gt;" in md and "<script>" not in md and "<li>one <code>a&lt;b</code></li>" in md,
       "Claude's note shows bold / lists / code, and HTML in it stays plain text")
    ok(b'<div class="md">' in c.get(f"/projects/{newp.id}?change={ch7.id}").data, "Claude's chat notes are shown formatted")

    # follow-up continues the same branch; the old client link closes
    script[:] = [resp("tool_use", tool(6, "str_replace_based_edit_tool", command="str_replace", path="README.md", old_str="v2", new_str="v3")),
                 resp("end_turn", NS(type="text", text="Changed to v3."))]
    c.post(f"/projects/{newp.id}/chat", data={"message": "Make it v3", "change_id": str(ch7.id)})
    out = subprocess.run(["git", "--git-dir", bare, "show", f"{ch7.branch}:README.md"], capture_output=True, text=True).stdout
    ok(out == "Hello CRM v3\n", "a follow-up continues the same branch")
    ok(calls[-1]["model"] == "claude-sonnet-5-5" and calls[-1]["output_config"] == {"effort": "medium"}, "no choice → the default (Normal = Sonnet 5.5, medium effort)")
    script[:] = [resp("end_turn", NS(type="text", text="Nothing to do."))]
    c.post(f"/projects/{newp.id}/chat", data={"message": "Quick look", "change_id": str(ch7.id), "preset": "quick"})
    ok(calls[-1]["model"] == "claude-haiku-5-5" and "fallbacks" not in calls[-1] and calls[-1]["output_config"] == {"effort": "low"},
       "Quick = Haiku 5.5, low effort, no server fallback")
    db.session.refresh(ch7)
    hu = AiUsage.query.filter_by(change_id=ch7.id, model="claude-haiku-5-5").first()
    ok(hu is not None and float(hu.cost_usd) < 0.01, "Haiku costs are counted at Haiku prices")

    # limits and errors
    set_setting("claude_max_usd", 0.5)
    db.session.commit()
    script[:] = [resp("tool_use", tool(7, "run_tests"), inp=1_000_000, out=10_000), resp("end_turn", NS(type="text", text="x"))]
    c.post(f"/projects/{newp.id}/chat", data={"message": "Another tweak", "change_id": str(ch7.id)})
    db.session.refresh(ch7)
    ok(ch7.agent_state == "failed" and "cost limit" in ch7.agent_note, "a run stops at the cost limit")
    set_setting("claude_max_usd", 3.0)
    ch7.agent_state = "working"
    db.session.commit()
    r = c.post(f"/projects/{newp.id}/chat", data={"message": "Something else"}, follow_redirects=True)
    ok(b"Claude is busy" in r.data, "only one Claude run per project at a time")
    ch7.agent_state = "idle"
    db.session.commit()
    agent_mod._client = lambda: (_ for _ in ()).throw(agent_mod.AgentStop("No Claude API key yet — the owner adds it in Settings → Claude."))
    c.post(f"/projects/{newp.id}/chat", data={"message": "No key test", "change_id": str(ch7.id)})
    db.session.refresh(ch7)
    ok(ch7.agent_state == "failed" and "API key" in ch7.agent_note, "missing API key is explained, not a crash")
    ok(c.get(f"/changes/{ch7.id}/status").json["state"] == "failed", "status endpoint reports the state")
    try:
        wsmod.safe_path(wsmod.Path(TMP), "../../x")
        ok(False, "path traversal is refused")
    except wsmod.WorkspaceError:
        ok(True, "path traversal is refused")
    ok(wsmod.parse_counts("FAILURES!\nTests: 24, Assertions: 50, Failures: 2.") == (22, 24), "PHPUnit failure summary is read")
    ok(wsmod.parse_counts("==== 3 failed, 21 passed in 2.1s ====") == (21, 24), "pytest summary is read")
    ok(dv.post("/settings/claude", data={"model": "claude-opus-5-5"}).status_code == 403, "developer can't change Claude settings")

    # ---------- servers, beta deploy, Live → Beta sync (Step 8) — fake SSH, nothing real is reached ----------
    from app import deploy as deploy_mod, remote as remote_mod
    from app.models import Deployment, Environment, Job, Server

    # a secret file is "exposed" only when its real content comes back — not when a single-page app answers 200 with HTML
    class _Resp:
        def __init__(self, status, ctype, text):
            self.status_code, self.headers, self.text = status, {"Content-Type": ctype}, text
    real_get = deploy_mod.requests.get
    answers = {"spa": _Resp(200, "text/html", "<!doctype html><html>app</html>"), "env": _Resp(200, "text/plain", "APP_KEY=base64:x\nDB_PASSWORD=y\n"),
               "git": _Resp(200, "application/octet-stream", "[core]\n\trepositoryformatversion = 0\n"), "403": _Resp(403, "text/html", "no")}
    for k, path, want in (("spa", "/.env", False), ("env", "/.env", True), ("git", "/.git/config", True), ("403", "/.env", False),
                          ("spa", "/.git/config", False)):
        deploy_mod.requests.get = lambda url, _k=k, **kw: answers[_k]
        ok(deploy_mod.exposed("https://x.example.com", path)[0] is want, f"leak check: {k} on {path} → {'leak' if want else 'no leak'}")
    deploy_mod.requests.get = real_get
    deploy_mod.exposed = lambda base, path, auth=None: (False, 404)      # tests never open real addresses
    remote_calls, pipe_calls = [], []
    run_answers = []

    def fake_run(server, script, timeout=900):
        remote_calls.append((server.name, script))
        return run_answers.pop(0) if run_answers else (0, "ok")

    def fake_pipe(src, src_cmd, dst, dst_cmd, timeout=3600):
        pipe_calls.append((src.name, src_cmd, dst.name, dst_cmd))
        return pipe_answer[0]

    pipe_answer = [""]
    health_answer = [(True, 200)]
    remote_mod.run, remote_mod.pipe = fake_run, fake_pipe
    remote_mod.keyscan = lambda s: f"{s.host} ssh-ed25519 AAAAC3NzaFAKE"
    deploy_mod.health_ok = lambda url: health_answer[0]

    real_args = remote_mod.ssh_args(NS(id=99, host="203.0.113.9", port=22, ssh_user="deploy", host_key="x"))
    ok(any(a.startswith('UserKnownHostsFile="') and a.endswith('"') for a in real_args), "the pinned host-key file path is quoted (folders with spaces)")
    r = c.get("/settings")
    ok(b"ssh-ed25519" in r.data and b"ai-project-dashboard" in r.data, "Settings shows the dashboard's own public SSH key")
    ok(os.path.exists(os.path.join(TMP, "instance", "ssh", "dashboard_ed25519")), "the key pair is made once, inside instance/")
    r = c.post("/settings/servers", data={"name": "VPS 1", "host": "1.2.3.4; rm -rf /", "user": "deploy", "port": "22"}, follow_redirects=True)
    ok(b"server address can only" in r.data and Server.query.count() == 0, "a bad server address is refused")
    c.post("/settings/servers", data={"name": "VPS 1", "host": "203.0.113.10", "user": "deploy", "port": "22"})
    c.post("/settings/servers", data={"name": "VPS 2", "host": "203.0.113.11", "user": "deploy", "port": "2222"})
    s1, s2 = Server.query.filter_by(name="VPS 1").one(), Server.query.filter_by(name="VPS 2").one()
    ok(dv.post("/settings/servers", data={"name": "X", "host": "x.y", "user": "u"}).status_code == 403, "developer can't add a server")
    run_answers[:] = [(0, "connected-ok\nLinux 6.8\ndeploy")]
    r = c.post(f"/settings/servers/{s1.id}/test", follow_redirects=True)
    db.session.refresh(s1)
    ok(b"Connected to VPS 1" in r.data and s1.host_key.startswith("203.0.113.10 ssh-ed25519") and s1.last_result.startswith("OK"),
       "first test pins the host key and connects")
    def refused(server, script, timeout=900):
        raise remote_mod.RemoteError("Could not sign in to the server over SSH — is the dashboard's public key added for this user?")
    remote_mod.run = refused
    r = c.post(f"/settings/servers/{s2.id}/test", follow_redirects=True)
    remote_mod.run = fake_run
    db.session.refresh(s2)
    ok(b"public key added" in r.data and not s2.last_ok_at, "a refused sign-in is explained")

    # beta deploy before the servers are set → explained, no crash
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "Set the beta server" in j.note, "beta deploy without a server is explained")
    ok(dv.post(f"/projects/{newp.id}/jobs/beta_deploy").status_code == 404, "a developer without the project can't deploy it")
    ok(c.post(f"/projects/{newp.id}/jobs/go_live").status_code == 404, "unknown job kinds are refused")

    base_form = {"beta_server": str(s1.id), "beta_path": "/home/beta/crm", "beta_db": "crm_beta",
                 "live_server": str(s1.id), "live_path": "/home/live/crm", "live_db": "crm_live", "db_engine": "mysql",
                 "deploy_script": deploy_mod.template_for(newp), "beta_env_off": "SMS_ENABLED=false\nMAIL_MAILER=log",
                 "beta_env_test": "", "beta_env_sandbox": "PAYMENT_KEY=test_123"}
    r = c.post(f"/projects/{newp.id}/deploy-settings", data=dict(base_form, beta_db="crm_live"), follow_redirects=True)
    ok(b"can&#39;t share the same folder or database" in r.data and newp.env("beta").db_name is None, "beta can't use live's database")
    r = c.post(f"/projects/{newp.id}/deploy-settings", data=dict(base_form, beta_path="/home/../etc"), follow_redirects=True)
    ok(b"must be a full path" in r.data, "a folder with .. is refused")
    r = c.post(f"/projects/{newp.id}/deploy-settings", data=dict(base_form, beta_env_off="sms enabled = no"), follow_redirects=True)
    ok(b"not a valid .env name" in r.data, "a bad .env line is refused")
    ok(dv.post(f"/projects/{newp.id}/deploy-settings", data=base_form).status_code == 403, "developer can't change servers & deploy")
    r = c.post(f"/projects/{newp.id}/deploy-settings", data=base_form, follow_redirects=True)
    db.session.refresh(newp)
    ok(b"Servers &amp; deploy saved" in r.data and newp.env("beta").server_id == s1.id and newp.env("live").db_name == "crm_live",
       "owner saves servers & deploy")
    ok(newp.deploy_script == "", "an unchanged template is not stored (it follows the tech stack)")
    r = c.post(f"/settings/servers/{s1.id}/delete", follow_redirects=True)
    ok(b"still used by 2" in r.data and db.session.get(Server, s1.id) is not None, "a server in use can't be removed")

    # .env writer: runs for real in bash on a temp folder (no server) — keeps other lines, replaces / adds keys, no injection
    envdir = os.path.join(TMP, "envtest")
    os.makedirs(envdir)
    open(os.path.join(envdir, ".env"), "w").write("APP_NAME=CRM\nSMS_ENABLED=true\nDB_PASSWORD=a=b\n")
    script_txt = deploy_mod.env_script(envdir, [("SMS_ENABLED", "false"), ("NEW_KEY", "x'; touch pwned; '")])
    subprocess.run(["bash", "-se"], input=script_txt, text=True, capture_output=True, cwd=envdir, check=True)
    ok(open(os.path.join(envdir, ".env")).read() == "APP_NAME=CRM\nSMS_ENABLED=false\nDB_PASSWORD=a=b\nNEW_KEY=x'; touch pwned; '\n"
       and not os.path.exists(os.path.join(envdir, "pwned")), ".env lines are replaced / added safely")

    # a second ready change that conflicts with ch7 → beta is built without it
    run_git("fetch", "-q", bare, f"{ch7.branch}:ch7")
    run_git("checkout", "-q", "-b", "change/99-clash", "main")
    open(os.path.join(seed_dir, "README.md"), "w").write("Totally different\n")
    run_git("commit", "-qam", "clash")
    run_git("push", "-q", bare, "change/99-clash")
    run_git("checkout", "-q", "main")
    ch7.status = "passed"
    clash = Change(project_id=newp.id, title="Clash", status="passed", branch="change/99-clash")
    db.session.add(clash)
    db.session.commit()
    remote_calls.clear()
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "done" and j.by_user_id == owner.id, "beta deploy runs to the end")
    beta_readme = subprocess.run(["git", "--git-dir", bare, "show", "beta:README.md"], capture_output=True, text=True).stdout
    ok(beta_readme == "Hello CRM v3\n", "beta branch = main + the passed change")
    ok(f"#{clash.id}" in j.note and "conflict" in j.note, "a conflicting change is skipped and named")
    ok(subprocess.run(["git", "--git-dir", bare, "show", "main:README.md"], capture_output=True, text=True).stdout == "Hello CRM\n",
       "main is still untouched")
    ok(len(remote_calls) == 2 and "SMS_ENABLED" in remote_calls[0][1] and "PAYMENT_KEY" in remote_calls[0][1],
       "beta safety lines are written before the deploy")
    ok("cd /home/beta/crm" in remote_calls[1][1] and "origin/beta" in remote_calls[1][1] and "/home/live" not in remote_calls[1][1],
       "the deploy runs in the beta folder on branch beta — never live")
    d = Deployment.query.order_by(Deployment.id.desc()).first()
    ok(d.environment.kind == "beta" and d.change_ids == [ch7.id] and d.result == "ok" and newp.env("beta").current_commit == d.commit,
       "the beta deployment is recorded")
    ok(ActivityLog.query.filter_by(action="Beta deploy", project_id=newp.id).count() == 1, "beta deploy is in the activity log")
    ok(c.get(f"/jobs/{j.id}/status").json["state"] == "done", "job status endpoint")
    r = c.get(f"/projects/{newp.id}")
    ok(b"Recent runs" in r.data and b"Beta updated" in r.data, "Deploys tab lists the run")

    run_answers[:] = [(0, "env ok"), (1, "composer: command not found")]
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "deploy script failed" in j.note and "composer: command not found" in j.output,
       "a failing deploy step stops the deploy and keeps the server output")
    health_answer[0] = (False, 502)
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "502" in j.note and Deployment.query.order_by(Deployment.id.desc()).first().result == "failed",
       "beta site not answering after a deploy → failed")
    health_answer[0] = (True, 200)

    # a developer pushed to beta by hand → the next beta deploy keeps that work (updated on top, normal push, no force)
    run_git("fetch", "-q", bare, "beta:devbeta")
    run_git("checkout", "-q", "devbeta")
    open(os.path.join(seed_dir, "dev.txt"), "w").write("developer work\n")
    run_git("add", "dev.txt")
    run_git("commit", "-qm", "dev: hotfix pushed straight to beta")
    dev_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=seed_dir, capture_output=True, text=True).stdout.strip()
    run_git("push", "-q", bare, "devbeta:beta")
    run_git("checkout", "-q", "main")
    run_git("branch", "-q", "-D", "devbeta")
    bare_show = lambda ref: subprocess.run(["git", "--git-dir", bare, "show", ref], capture_output=True, text=True).stdout
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "done" and "developer pushed 1 commit" in j.output, "beta deploy notices a developer's commit on beta")
    ok(bare_show("beta:dev.txt") == "developer work\n" and bare_show("beta:README.md") == "Hello CRM v3\n"
       and subprocess.run(["git", "--git-dir", bare, "merge-base", "--is-ancestor", dev_sha, "beta"]).returncode == 0,
       "the developer's work is still on beta, together with main and the ready change")
    ok(bare_show("main:dev.txt") == "", "the developer's beta work never reaches main by itself")
    # developer work that conflicts with main → nothing is pushed, plain message
    main_before_dev = subprocess.run(["git", "--git-dir", bare, "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
    run_git("fetch", "-q", bare, "main:devmain")
    run_git("checkout", "-q", "devmain")
    open(os.path.join(seed_dir, "README.md"), "w").write("main moved on\n")
    run_git("commit", "-qam", "main moved on")
    run_git("push", "-q", bare, "devmain:main")
    run_git("fetch", "-q", bare, "beta:devbeta2")
    run_git("checkout", "-q", "devbeta2")
    open(os.path.join(seed_dir, "README.md"), "w").write("developer's own README\n")
    run_git("commit", "-qam", "dev: README on beta")
    run_git("push", "-q", bare, "devbeta2:beta")
    beta_before = subprocess.run(["git", "--git-dir", bare, "rev-parse", "beta"], capture_output=True, text=True).stdout
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "conflicts with main" in j.note and
       subprocess.run(["git", "--git-dir", bare, "rev-parse", "beta"], capture_output=True, text=True).stdout == beta_before,
       "developer work on beta that conflicts with main stops the deploy and changes nothing")
    # put the shared test repo back as it was (main, and a beta that holds no hand-made work)
    run_git("checkout", "-q", "-f", "main")
    run_git("branch", "-q", "-D", "devmain", "devbeta2")
    subprocess.run(["git", "--git-dir", bare, "update-ref", "refs/heads/main", main_before_dev], check=True)
    subprocess.run(["git", "--git-dir", bare, "update-ref", "-d", "refs/heads/beta"], check=True)

    # Live → Beta sync
    remote_calls.clear()
    c.post(f"/projects/{newp.id}/jobs/sync")
    j = Job.query.order_by(Job.id.desc()).first()
    db.session.refresh(newp)
    ok(j.state == "done" and newp.env("beta").last_sync_at is not None, "Live → Beta sync runs")
    src_cmd, dst_cmd = pipe_calls[-1][1], pipe_calls[-1][3]
    ok(src_cmd.startswith("mysqldump") and src_cmd.endswith("crm_live") and dst_cmd == "mysql crm_beta",
       "live is only dumped (read); the copy loads into the beta database")
    ok(len(remote_calls) == 1 and "SMS_ENABLED" in remote_calls[0][1], "beta safety lines are applied again after the copy")
    pipe_answer[0] = "Reading the live database failed: Access denied"
    c.post(f"/projects/{newp.id}/jobs/sync")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "Access denied" in j.note, "a failed copy is explained")
    pipe_answer[0] = ""

    # one job at a time per project
    busy_job = Job(project_id=newp.id, kind="sync", state="running", by_user_id=owner.id)
    db.session.add(busy_job)
    db.session.commit()
    n_jobs = Job.query.count()
    r = c.post(f"/projects/{newp.id}/jobs/beta_deploy", follow_redirects=True)
    ok(b"Another job is running" in r.data and Job.query.count() == n_jobs, "only one job per project at a time")
    ok(b"data-job-poll" in r.data, "the project page follows a running job")
    busy_job.state = "done"
    db.session.commit()

    # a change whose tests pass goes on beta by itself; the change card says whether it is on beta
    agent_mod._client = lambda: fake_client
    script[:] = [resp("tool_use", tool(20, "str_replace_based_edit_tool", command="create", path="about.html", file_text="<h1>About</h1>\n")),
                 resp("end_turn", NS(type="text", text="Added an **About** page."))]
    n_jobs = Job.query.count()
    c.post(f"/projects/{newp.id}/chat", data={"message": "Add an about page"})
    auto = Change.query.filter_by(project_id=newp.id).order_by(Change.id.desc()).first()
    j = Job.query.order_by(Job.id.desc()).first()
    ok(auto.status == "passed" and Job.query.count() == n_jobs + 1 and j.kind == "beta_deploy" and j.state == "done"
       and auto.id in Deployment.query.order_by(Deployment.id.desc()).first().change_ids, "a passed change goes on beta automatically")
    r = c.get(f"/projects/{newp.id}?change={auto.id}")
    ok(b"This version is on the beta site" in r.data, "the change card says it is on beta")
    auto.status = "live"                             # keep it out of the later Go Live checks
    db.session.commit()

    # ---------- Claude Code engine: one session per project (owner, 2026-10-16) — fake CLI, nothing real starts ----------
    import json as _json
    import app.claude_code as cc_mod
    cc_calls, cc_mode = [], ["ok"]

    class FakeCC:
        def __init__(self, args, cwd, env):
            cc_calls.append((args, str(cwd), env))
            sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
            if cc_mode[0] == "lost" and "--resume" in args:
                self.stdout, self.stderr = iter([]), io.StringIO("Error: No conversation found with session ID: " + sid)
            else:
                open(os.path.join(cwd, "contact.html"), "a").write("<h1>Contact</h1>\n")
                lines = [{"type": "system", "subtype": "init", "session_id": sid},
                         {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "contact.html"}}]}},
                         {"type": "result", "subtype": "success", "is_error": False, "result": "Contact page **added**.", "session_id": sid,
                          "total_cost_usd": 0.12, "num_turns": 3, "usage": {"input_tokens": 100, "output_tokens": 50}}]
                self.stdout, self.stderr = iter([_json.dumps(l) + "\n" for l in lines]), io.StringIO("")
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    cc_mod._spawn = lambda args, cwd, env: FakeCC(args, cwd, env)
    ok(dv.post(f"/projects/{newp.id}/engine", data={"engine": "claude_code"}).status_code == 403, "developers can't change the AI engine")
    # Claude accounts with a web login (owner, 2026-11-04): a fake `claude` — auth login / status / logout, initialize, mcp list
    from app import cc_logins as cl_mod
    from app.models import ClaudeAccount
    from app.clock import now as _now
    from datetime import timedelta
    cl_state = {"logged": set(), "codes": [], "keepalive": 0, "init": 0}
    MODELS_BY = {"a1": [{"value": "default", "displayName": "Default", "resolvedModel": "sonnet"},
                        {"value": "opus", "displayName": "Opus 5.5", "supportsEffort": True, "supportedEffortLevels": ["low", "high"]}],
                 "a2": [{"value": "default", "displayName": "Default", "resolvedModel": "opus"},
                        {"value": "fable", "displayName": "Fable 5.1"}]}

    def _slug(kw, args):
        d = (kw.get("env") or {}).get("CLAUDE_CONFIG_DIR") or next((a.split("=", 1)[1] for a in args if str(a).startswith("CLAUDE_CONFIG_DIR=")), "")
        return os.path.basename(d) if d else ""

    class _R:
        def __init__(self, out="", rc=0, err=""):
            self.stdout, self.returncode, self.stderr = out, rc, err

    def fake_cl_run(args, **kw):
        slug = _slug(kw, args)
        if args[0] == "/bin/sh":                                  # _as_runner scripts
            if "expiresAt" in args[2] and "accessToken" in args[2]:
                return _R(f"{int((time.time() + 3 * 3600) * 1000)}\nsk-ant-oat01-access-{slug}\n")
            if "expiresAt" in args[2]:
                return _R(str(int((time.time() + 5 * 3600) * 1000)))
            if "mkdir" in args[2]:
                os.makedirs(args[-1], exist_ok=True)
            return _R()
        if "status" in args and "auth" in args:
            return _R(_json.dumps({"loggedIn": slug in cl_state["logged"], "subscriptionType": "max", "email": f"owner.{slug}@example.com"}))
        if "logout" in args:
            cl_state["logged"].discard(slug)
            return _R()
        if "--input-format" in args:
            cl_state["init"] += 1
            return _R(_json.dumps({"type": "control_response", "response": {"subtype": "success", "response": {"models": MODELS_BY.get(slug, [])}}}) + "\n")
        if "mcp" in args:
            return _R("claude.ai Mobbin: https://api.mobbin.com/mcp - ✔ Connected\n")
        if "Reply with the word OK." in args:
            cl_state["keepalive"] += 1
            return _R('{"is_error":false}')
        return _R("", 1)

    class FakeLoginProc:
        def __init__(self, args, **kw):
            self.slug, self.rc, self.args = _slug(kw, args), None, args
            self.stdout = iter(["Opening browser to sign in…\n",
                                "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=y\n"])
            outer = self

            class _In:
                def write(self, t):
                    cl_state["codes"].append(t)
                    if t.strip() == "goodcode#state123":
                        cl_state["logged"].add(outer.slug)

                def flush(self):
                    pass

                def close(self):
                    pass
            self.stdin = _In()

        def poll(self):
            return self.rc

        def wait(self, timeout=None):
            self.rc = 0
            return 0

        def kill(self):
            self.rc = -9

    cl_mod._run = fake_cl_run
    cl_mod._popen = lambda args, **kw: FakeLoginProc(args, **kw)
    ok("Add a Claude account" in c.get("/settings").data.decode() and dv.post("/settings/claude-accounts", data={"name": "X"}).status_code == 403,
       "Settings offers Claude accounts; developers can't add one")
    r = c.post("/settings/claude-accounts", data={"name": "Office Max"}, follow_redirects=True)
    acc1 = ClaudeAccount.query.filter_by(name="Office Max").first()
    page = r.data.decode()
    ok(acc1 and acc1.slug == f"a{acc1.id}" and acc1.is_default and "https://claude.com/cai/oauth/authorize" in page
       and "Code from the login page" in page, "adding an account starts a web login: the page shows the Claude login link and a code box")
    ok(os.path.isdir(cl_mod.config_dir(acc1)), "each account gets its own Claude Code folder")
    r = c.post(f"/settings/claude-accounts/{acc1.id}/code", data={"code": "bad code with spaces"}, follow_redirects=True)
    db.session.refresh(acc1)
    ok(acc1.status != "ok" and "doesn&#39;t look like the code" in r.data.decode(), "a code that doesn't look like one is refused")
    c.post(f"/settings/claude-accounts/{acc1.id}/login")
    r = c.post(f"/settings/claude-accounts/{acc1.id}/code", data={"code": "wrongcode#123"}, follow_redirects=True)
    db.session.refresh(acc1)
    ok(acc1.status == "logged_out" and "did not accept the code" in r.data.decode(), "a code Claude doesn't accept leaves the account logged out")
    c.post(f"/settings/claude-accounts/{acc1.id}/login")
    r = c.post(f"/settings/claude-accounts/{acc1.id}/code", data={"code": " goodcode#state123 "}, follow_redirects=True)
    db.session.refresh(acc1)
    ok(acc1.status == "ok" and acc1.plan == "max" and acc1.email_hint == "o***@example.com" and len(acc1.models) == 2
       and acc1.connectors == [{"name": "Mobbin", "status": "connected"}] and acc1.expires_at is not None
       and "is logged in" in r.data.decode() and "owner.a" not in r.data.decode(),
       "the right code logs the account in: plan, a masked e-mail, its own models and connectors (never the full e-mail)")
    ok(not cl_mod.pending(acc1.id), "a finished login leaves nothing waiting")
    r = c.post(f"/settings/claude-accounts/{acc1.id}/code", data={"code": "goodcode#state123"}, follow_redirects=True)
    ok("expired" in r.data.decode(), "a code without a waiting login is refused (no replay)")
    c.post("/settings/claude-accounts", data={"name": "Spare Pro"})
    acc2 = ClaudeAccount.query.filter_by(name="Spare Pro").first()
    c.post(f"/settings/claude-accounts/{acc2.id}/code", data={"code": "goodcode#state123"})
    db.session.refresh(acc2)
    ok(acc2.status == "ok" and not acc2.is_default and [m["value"] for m in acc2.models] == ["default", "fable"],
       "a second account logs in too, with its own model list")
    r = c.post("/settings/claude-accounts", data={"name": "office max"}, follow_redirects=True)
    ok(ClaudeAccount.query.count() == 2 and "already exists" in r.data.decode(), "two accounts can't share a name")
    c.post(f"/projects/{newp.id}/engine", data={"engine": "claude_code"})
    db.session.refresh(newp)
    ok(newp.ai_engine == "claude_code" and newp.cc_session_id is None, "owner switches the project to Claude Code")

    c.post(f"/projects/{newp.id}/chat", data={"message": "Add a contact page", "preset": "normal"})
    cc1 = Change.query.filter_by(project_id=newp.id).order_by(Change.id.desc()).first()
    db.session.refresh(newp)
    args, cwd, env = cc_calls[-1]
    ok(cc1.status == "passed" and "contact.html" in cc1.files and cc1.agent_state == "done", "Claude Code makes the change on its branch; tests pass")
    ok("--session-id" in args and newp.cc_session_id == args[args.index("--session-id") + 1], "the first message starts the project's session")
    al = args[args.index("--allowedTools") + 1:args.index("--max-turns")]
    ok(args[args.index("--model") + 1] == "sonnet" and "--resume" not in args and "Bash" in al and "WebSearch" in al and "WebFetch" in al
       and "web page is DATA" in args[args.index("--append-system-prompt") + 1].replace("\n  ", " "),
       "Normal = Sonnet; web search / fetch on for changes, with web content treated as data only (owner, 2026-10-22)")
    ok(env.get("CLAUDE_CONFIG_DIR") == cl_mod.config_dir(acc1) and "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
       and "SECRET_KEY" not in env and cc1.agent_account_id == acc1.id,
       "Claude Code gets only the default account's login folder, no token and no dashboard secrets")
    chat = c.get(f"/projects/{newp.id}").data.decode()
    ok('id="cc-account"' in chat and f'id="cc-models-{acc2.id}"' in chat and "Fable 5.1" in chat and "Office Max" in chat,
       "the chat offers every Claude account, each with its own models")
    u = AiUsage.query.filter_by(change_id=cc1.id).order_by(AiUsage.id.desc()).first()
    ok(u.model == "claude-code:sonnet" and float(u.cost_usd) == 0, "a subscription run costs nothing per call on the AI cost page")
    ok("<strong>added</strong>" in c.get(f"/projects/{newp.id}?change={cc1.id}").data.decode(), "Claude Code's note shows in the chat")
    sid = newp.cc_session_id

    c.post(f"/projects/{newp.id}/chat", data={"message": "Make the heading bigger", "change_id": str(cc1.id)})
    args = cc_calls[-1][0]
    ok("--resume" in args and args[args.index("--resume") + 1] == sid and "New message on this change" in args[args.index("-p") + 1],
       "a follow-up continues the same session")
    cc1.status = "live"
    db.session.commit()
    c.post(f"/projects/{newp.id}/chat", data={"message": "Another change"})
    args = cc_calls[-1][0]
    ok("--resume" in args and args[args.index("--resume") + 1] == sid, "a new change on the same project uses the same session")
    c.post(f"/projects/{newp.id}/chat", data={"message": "Use the spare account", "account": str(acc2.id), "model": "default",
                                              "change_id": str(Change.query.filter_by(project_id=newp.id).order_by(Change.id.desc()).first().id)})
    args, cwd, env = cc_calls[-1]
    chx = Change.query.filter_by(project_id=newp.id).order_by(Change.id.desc()).first()
    ok(env.get("CLAUDE_CONFIG_DIR") == cl_mod.config_dir(acc2) and chx.agent_account_id == acc2.id
       and args[args.index("--model") + 1] == "opus" and "--resume" in args and args[args.index("--resume") + 1] == sid,
       "a message can pick another account: its login and its own Default model, same project session")
    c.post(f"/projects/{newp.id}/chat", data={"message": "Unknown account", "account": "999", "change_id": str(chx.id)})
    args, cwd, env = cc_calls[-1]
    db.session.refresh(chx)
    ok(env.get("CLAUDE_CONFIG_DIR") == cl_mod.config_dir(acc1) and chx.agent_account_id == acc1.id,
       "an unknown account falls back to the default one")
    # staying logged in: the 5-minute tick checks hourly, renews a run-out login, alerts once when an account is logged out
    acc1.checked_at = acc2.checked_at = None
    acc1.expires_at = _now() + timedelta(minutes=5)
    db.session.commit()
    real_expiry = cl_mod._expiry
    cl_mod._expiry = lambda a: _now() + timedelta(minutes=5)
    cl_mod.tick()
    cl_mod._expiry = real_expiry
    ok(cl_state["keepalive"] == 2, "a login about to run out gets one tiny call so Claude Code renews it")
    n_alerts = Alert.query.filter_by(kind="claude_login").count()
    cl_state["logged"].discard(acc2.slug)
    acc1.checked_at, acc2.checked_at = _now(), None
    db.session.commit()
    cl_mod.tick()
    acc2.checked_at = None
    db.session.commit()
    cl_mod.tick()
    db.session.refresh(acc2)
    ok(acc2.status == "logged_out" and Alert.query.filter_by(kind="claude_login").count() == n_alerts + 1,
       "a logged-out account raises ONE alert, not one per check")
    r = c.post(f"/projects/{newp.id}/chat", data={"message": "On the logged-out one", "account": str(acc2.id), "change_id": str(chx.id)},
               follow_redirects=True)
    db.session.refresh(chx)
    ok(chx.agent_state == "failed" and "Spare Pro" in (chx.agent_note or ""), "a run on a logged-out account stops with a clear message")
    ok("· logged out" in c.get(f"/projects/{newp.id}").data.decode(), "the chat marks a logged-out account")
    c.post(f"/settings/claude-accounts/{acc2.id}/default")
    db.session.refresh(acc1)
    db.session.refresh(acc2)
    ok(acc2.is_default and not acc1.is_default, "another account can be made the default")
    c.post(f"/settings/claude-accounts/{acc2.id}/rename", data={"name": "Spare"})
    r = c.post(f"/settings/claude-accounts/{acc2.id}/remove", data={}, follow_redirects=True)
    ok(db.session.get(ClaudeAccount, acc2.id) is not None and "nothing was removed" in r.data.decode(), "removing needs the tick box")
    acc2_dir = cl_mod.config_dir(acc2)
    c.post(f"/settings/claude-accounts/{acc2.id}/remove", data={"confirm": "1"})
    db.session.expire_all()
    acc1 = db.session.get(ClaudeAccount, acc1.id)
    ok(db.session.get(ClaudeAccount, acc2.id) is None and acc1.is_default and ActivityLog.query.filter(
        ActivityLog.details.like("%Claude account removed%Spare%")).count() == 1,
       "removing the default account logs it out, forgets it and makes another one the default")
    ok(cl_mod.default_account().id == acc1.id and acc2_dir.endswith("/claude-accounts/a" + str(acc2.id)), "the remaining account is used")

    cc_mode[0] = "lost"
    c.post(f"/projects/{newp.id}/chat", data={"message": "One more", "change_id": str(Change.query.filter_by(project_id=newp.id).order_by(Change.id.desc()).first().id)})
    db.session.refresh(newp)
    ok(newp.cc_session_id != sid and "--session-id" in cc_calls[-1][0], "a lost session is replaced by a fresh one automatically")
    cc_mode[0] = "ok"
    for ch in Change.query.filter_by(project_id=newp.id).filter(Change.status != "live").all():
        ch.status = "live"
    c.post(f"/projects/{newp.id}/engine", data={"engine": "api"})
    db.session.refresh(newp)
    ok(newp.ai_engine == "api", "owner can switch back to the Claude API")

    # ---------- client approval link (Step 9) ----------
    from app.models import Approval
    from app.crypto import sha256 as _sha
    ch7.status, ch7.agent_state = "passed", "idle"
    db.session.commit()
    anon = app.test_client()
    r = c.post(f"/changes/{ch7.id}/send", data={"checklist": "  \n "}, follow_redirects=True)
    ok(b"at least one thing" in r.data and ch7.status == "passed", "a link needs at least one thing to check")
    ok(dv.post(f"/changes/{ch7.id}/send", data={"checklist": "x"}).status_code == 404, "a developer without the project can't send it")
    n_sent0 = len(sent_msgs)
    r = c.post(f"/changes/{ch7.id}/send", data={"checklist": "Open the test site\nCheck the home page says v3"}, follow_redirects=True)
    db.session.refresh(ch7)
    a1 = ch7.approval
    ok(ch7.status == "client" and a1.checklist == ["Open the test site", "Check the home page says v3"], "Send to client makes a link")
    m = re.search(rb'data-copy-v="(http://localhost/a/[^"]+)"', r.data)
    ok(m is not None and b"wa.me" in r.data, "the project page shows the link with Copy / WhatsApp")
    link1 = m.group(1).decode()
    tok1 = link1.rsplit("/", 1)[1]
    ok(a1.token == _sha(tok1) and tok1 not in a1.token and a1.token_enc and tok1 not in a1.token_enc, "only the hash is used to find the link; the secret is stored encrypted")
    ok(ActivityLog.query.filter_by(action="Sent to client", project_id=newp.id).count() == 1, "sending is logged")

    r = anon.get(f"/a/{tok1}")
    ok(r.status_code == 200 and b"Check the home page says v3" in r.data and b"Show v2 on the home page" in r.data and b"Open the test site" in r.data,
       "client page opens without login: request, checklist, beta link")
    ok(r.headers.get("Referrer-Policy") == "no-referrer" and "no-store" in r.headers.get("Cache-Control", ""), "the link never leaks via referrer or cache")
    ok(b"Owner" not in r.data and b"/projects/" not in r.data, "the client page shows nothing of the dashboard")
    ok(b"not valid" in anon.get("/a/not-a-real-token").data, "a wrong link is refused")

    # a new link closes the old one
    c.post(f"/changes/{ch7.id}/send", data={"checklist": "Open the test site"})
    db.session.refresh(ch7)
    a2 = ch7.approval
    tok2 = re.search(rb'data-copy-v="http://localhost/a/([^"]+)"', c.get(f"/projects/{newp.id}?change={ch7.id}").data).group(1).decode()
    ok(a2.id != a1.id and b"link is closed" in anon.get(f"/a/{tok1}").data and b"Please check" in anon.get(f"/a/{tok2}").data,
       "a new link closes the old one")

    # changes needed: comment required, screenshot kept, alert, status
    r = anon.post(f"/a/{tok2}", data={"decision": "changes", "comment": " "})
    ok(b"Please write what should change" in r.data and ch7.status == "client", "changes need a note")
    r = anon.post(f"/a/{tok2}", data={"decision": "changes", "comment": "Make the v3 text bigger", "shot": (io.BytesIO(png), "mine.png")},
                  content_type="multipart/form-data")
    db.session.refresh(ch7)
    ok(b"sent to the team" in r.data and ch7.status == "changes" and ch7.approval.decision == "changes", "client asks for changes")
    cm = ChangeMessage.query.filter_by(change_id=ch7.id, sender="client").order_by(ChangeMessage.id.desc()).first()
    ok(cm.body == "Make the v3 text bigger" and ChangeAttachment.query.filter_by(message_id=cm.id).count() == 1,
       "the client's note + screenshot land in the chat")
    ok(Alert.query.filter(Alert.message.contains("asked for changes")).count() == 1 and len(sent_msgs) > n_sent0, "the team gets an alert (bell + Telegram)")
    ok(ActivityLog.query.filter_by(action="Client asked changes", project_id=newp.id).count() == 1, "the answer is logged")
    ok(b"Already answered" in anon.get(f"/a/{tok2}").data, "the link closes after one answer")
    r = anon.post(f"/a/{tok2}", data={"decision": "approved"})
    db.session.refresh(ch7)
    ok(ch7.status == "changes", "a second answer on a closed link is ignored")

    # approve on a fresh link
    ch7.status = "passed"
    db.session.commit()
    c.post(f"/changes/{ch7.id}/send", data={"checklist": "Open the test site"})
    tok3 = re.search(rb'data-copy-v="http://localhost/a/([^"]+)"', c.get(f"/projects/{newp.id}?change={ch7.id}").data).group(1).decode()
    anon.post(f"/a/{tok3}", data={"decision": "approved"})
    db.session.refresh(ch7)
    ok(ch7.status == "approved" and ch7.approval.decided_ip is not None and Alert.query.filter(Alert.message.contains(f"{newp.name}: client approved")).count() == 1,
       "client approves → change approved + alert")
    ok(c.post(f"/changes/{ch7.id}/send", data={"checklist": "x"}, follow_redirects=True).data.count(b"Only a change whose tests passed") == 1,
       "an approved change can't be sent again")

    # expiry + a follow-up to Claude closes the link
    ch7.status = "passed"
    db.session.commit()
    c.post(f"/changes/{ch7.id}/send", data={"checklist": "Open the test site"})
    tok4 = re.search(rb'data-copy-v="http://localhost/a/([^"]+)"', c.get(f"/projects/{newp.id}?change={ch7.id}").data).group(1).decode()
    a4 = ch7.approval
    a4.expires_at = a4.sent_at
    db.session.commit()
    ok(b"link is closed" in anon.get(f"/a/{tok4}").data and b"The client link is closed" in c.get(f"/projects/{newp.id}?change={ch7.id}").data,
       "an expired link is closed (client and team both see it)")

    # ---------- AI cost & balance (Step 13) ----------
    from decimal import Decimal as _D
    from app import ai_budget
    from app.models import AiCredit
    ok(dv.get("/ai-cost").status_code == 403, "developers can't open the AI cost page")
    r = c.get("/ai-cost")
    ok(r.status_code == 200 and b"Balance not recorded yet" in r.data and b"Most expensive changes" in r.data, "owner opens the AI cost page")
    ok(b"Show v2 on the home page" in r.data or ch7.title.encode() in r.data, "the costly changes are listed")
    r = c.post("/ai-cost/credit", data={"kind": "balance", "amount": "abc"}, follow_redirects=True)
    ok(b"Enter the amount" in r.data and AiCredit.query.count() == 0, "a wrong amount is refused")
    c.post("/ai-cost/credit", data={"kind": "balance", "amount": "10", "note": "from Console"})
    ok(ai_budget.balance()[0] == _D("10.00"), "recorded Console balance = the estimate")
    db.session.add(AiUsage(project_id=newp.id, change_id=ch7.id, model="claude-sonnet-5-5", cost_usd=_D("2.5")))
    db.session.commit()
    ok(ai_budget.balance()[0] == _D("7.50"), "Claude's cost since then is taken off")
    c.post("/ai-cost/credit", data={"kind": "topup", "amount": "$5"})
    ok(ai_budget.balance()[0] == _D("12.50"), "a top-up is added")
    ok(ActivityLog.query.filter_by(action="AI credit").count() == 2, "balance / top-up entries are logged")

    n_alerts = Alert.query.filter_by(kind="ai_cost").count()
    c.post("/ai-cost/alert", data={"low": "20"})
    ok(Alert.query.filter_by(kind="ai_cost").count() == n_alerts + 1 and "balance is low" in Alert.query.order_by(Alert.id.desc()).first().message,
       "balance below the alert amount → alert")
    ai_budget.check()
    ok(Alert.query.filter_by(kind="ai_cost").count() == n_alerts + 1, "the low-balance alert goes only once")
    ok(b"Anthropic balance is low" in c.get("/ai-cost").data, "the page warns too")
    c.post("/ai-cost/credit", data={"kind": "topup", "amount": "50"})
    ok(get_setting("ai_alert_state").get("low") is False, "after a top-up the alert is armed again")
    c.post("/ai-cost/alert", data={"low": "5"})

    # per-project monthly limit: 80% alert, 100% → developers blocked, owner not
    lp = db.session.get(Project, 2)
    spent0 = ai_budget.limit_state(lp)[0]
    r = c.post(f"/ai-cost/limit/{lp.id}", data={"limit": "0.1"}, follow_redirects=True)
    ok(b"between $0.50" in r.data, "a too-small limit is refused")
    c.post(f"/ai-cost/limit/{lp.id}", data={"limit": str(spent0 + _D("10"))})
    db.session.refresh(lp)
    ok(lp.ai_monthly_limit_usd == spent0 + _D("10"), "owner sets a monthly limit")
    db.session.add(AiUsage(project_id=lp.id, model="claude-sonnet-5-5", cost_usd=_D("8.5")))
    db.session.commit()
    ai_budget.check()
    ok(Alert.query.filter(Alert.kind == "ai_cost", Alert.message.contains("85%")).count() == 1, "80% of the limit → alert")
    ai_budget.check()
    ok(Alert.query.filter(Alert.kind == "ai_cost", Alert.message.contains(lp.name)).count() == 1, "the 80% alert goes once")
    db.session.add(AiUsage(project_id=lp.id, model="claude-sonnet-5-5", cost_usd=_D("2")))
    db.session.commit()
    ai_budget.check()
    ok(Alert.query.filter(Alert.kind == "ai_cost", Alert.message.contains("needs the owner")).count() == 1, "100% → alert")
    n_changes = Change.query.filter_by(project_id=lp.id).count()
    r = dv.post(f"/projects/{lp.id}/chat", data={"message": "Small tweak please"}, follow_redirects=True)
    ok(b"used its AI limit" in r.data and Change.query.filter_by(project_id=lp.id).count() == n_changes, "at 100% a developer can't start Claude")
    ok(ai_budget.blocked_reason(lp, owner) == "", "the owner is never blocked by a limit")
    c.post(f"/ai-cost/limit/{lp.id}", data={"limit": ""})
    db.session.refresh(lp)
    ok(lp.ai_monthly_limit_usd is None and ai_budget.blocked_reason(lp, User.query.filter_by(email="dev@demo.test").one()) == "", "empty = no limit")

    ok(b"Anthropic balance" in c.get("/projects").data and b"Anthropic balance" not in dv.get("/projects").data,
       "the Projects overview shows the AI balance / cost strip to the owner only")
    # Anthropic refusing for low credit
    ok(ai_budget.is_credit_error(Exception("Your credit balance is too low to access the Anthropic API.")), "low-credit error is recognised")
    ok(ai_budget.credit_refused() is True and ai_budget.credit_refused() is False, "low-credit alert goes once a day")

    # ---------- Go Live / Rollback (Step 10) — fake SSH, local bare repo as GitHub ----------
    from app import golive as gl_mod
    from app.clock import now as _now
    from app.models import Backup, Deployment, Job
    main_sha = lambda: subprocess.run(["git", "--git-dir", bare, "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
    main_readme = lambda: subprocess.run(["git", "--git-dir", bare, "show", "main:README.md"], capture_output=True, text=True).stdout
    live_calls, drift_files = [], [""]

    def live_run(server, script, timeout=900):
        live_calls.append(script)
        if "git status --porcelain" in script:
            return 0, f"HEAD={main_sha()}\n{drift_files[0]}"
        if "gzip >" in script:
            return 0, "SIZE=2048\nPATH=/root/ai-dashboard-snapshots/new-crm-1.sql.gz"
        return 0, "ok"

    remote_mod.run = live_run
    health_answer[0] = (True, 200)

    from app.auth import new_totp_secret
    if not owner.totp_enabled:                      # an earlier check turned it off — Go Live needs it
        new_totp_secret(owner)
        owner.totp_enabled = True
        db.session.commit()

    def fresh_code():
        owner.totp_last_step = 0
        db.session.commit()
        return code_for(owner)

    clash.status = "beta"
    ch7.status = "approved"
    db.session.commit()
    ok(dv.post(f"/projects/{newp.id}/go-live", data={"code": "123456"}).status_code == 403, "developers can't Go Live")
    ok(b'data-open="dlg-golive"' in c.get(f"/projects/{newp.id}").data, "the owner gets the Go Live dialog")
    n_jobs = Job.query.count()
    r = c.post(f"/projects/{newp.id}/go-live", data={"code": "000000"}, follow_redirects=True)
    ok(b"code is wrong" in r.data and Job.query.count() == n_jobs, "a wrong authenticator code stops Go Live")

    # wrong repo on the live server → Go Live refuses before anything changes
    wrong_origin = ["https://github.com/someone-else/new-crm.git"]
    orig_live_run = live_run

    def origin_run(server, script, timeout=900):
        if "git status --porcelain" in script:
            return 0, f"HEAD={main_sha()}\nORIGIN={wrong_origin[0]}\n"
        return orig_live_run(server, script, timeout)

    remote_mod.run = origin_run
    gh.resolve_repo = lambda name: (111, "someone-else/new-crm")          # really another repo
    before = main_sha()
    c.post(f"/projects/{newp.id}/go-live", data={"code": fresh_code()})
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "someone-else/new-crm" in j.note and main_sha() == before,
       "a live folder pulling from another repo blocks Go Live before anything changes")
    db.session.refresh(newp)
    ok(b"pulls its code from someone-else/new-crm" in c.get(f"/projects/{newp.id}").data, "the project page warns about the wrong repo")
    # an OLD NAME of the same repo (after a transfer GitHub redirects it) is not a problem
    newp.github_repo_id = 222
    db.session.commit()
    gh.resolve_repo = lambda name: (222, newp.github_repo)
    wrong_origin[0] = "https://github.com/old-owner/new-crm.git"
    c.post(f"/projects/{newp.id}/go-live", data={"code": "000000"})        # wrong code: stops before the job, but…
    from app import gitcheck as _gc
    _gc._remember_alias("old-owner/new-crm")
    newp.env("live").git_origin = "old-owner/new-crm"
    db.session.commit()
    ok(_gc.problem(newp, newp.env("live")) == "", "an old name of the same repo (transfer redirect) is not flagged")
    newp.env("live").git_origin = None
    db.session.commit()
    remote_mod.run = live_run

    # drift: someone edited a file directly on the live server
    drift_files[0] = " M app/Http/Controllers/HomeController.php"
    before = main_sha()
    c.post(f"/projects/{newp.id}/go-live", data={"code": fresh_code()})
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.kind == "go_live" and j.state == "failed" and "directly on the live server" in j.note and main_sha() == before,
       "direct edits on the live server stop Go Live before anything changes")
    drift_files[0] = ""

    # the real thing
    live_calls.clear()
    n_alerts = Alert.query.filter_by(kind="golive").count()
    c.post(f"/projects/{newp.id}/go-live", data={"code": fresh_code()})
    j = Job.query.order_by(Job.id.desc()).first()
    db.session.refresh(ch7)
    d = Deployment.query.order_by(Deployment.id.desc()).first()
    ok(j.state == "done" and ch7.status == "live" and ch7.live_at is not None, "Go Live runs to the end; the change is live")
    ok(main_readme() == "Hello CRM v3\n" and d.previous_commit == before, "main now has the change; the earlier commit is kept for Rollback")
    ok(d.environment.kind == "live" and d.change_ids == [ch7.id] and d.snapshot is not None and d.snapshot.kind == "snapshot"
       and d.snapshot.storage_url.endswith("new-crm-1.sql.gz") and d.snapshot.size_bytes == 2048, "a database snapshot is taken first and linked")
    ok(any("mysqldump" in s and "crm_live" in s and "gzip" in s for s in live_calls), "the snapshot dumps the live database")
    ok(any("cd /home/live/crm" in s and "origin/main" in s for s in live_calls) and not any("/home/beta" in s for s in live_calls),
       "the deploy runs in the live folder on main")
    ok(newp.env("live").watch_until is not None and newp.env("live").watch_until > _now(), "15-minute close watch starts")
    ok(Alert.query.filter_by(kind="golive").count() == n_alerts + 1 and ActivityLog.query.filter_by(action="Go Live", project_id=newp.id).count() == 1,
       "Go Live raises an alert and is logged")

    # close watch: the first failed check already counts as down
    newp.monitor_enabled = False
    newp.incident = ""
    newp.env("live").fail_count = 0
    db.session.commit()
    mon.fetch = lambda url: (0, 0, "")
    mon.run_all()
    db.session.refresh(newp)
    ok(newp.incident.startswith("Live site down"), "during the close watch one failed check is enough (even when watching is off)")
    newp.incident = ""
    newp.env("live").fail_count, newp.env("live").down_since = 0, None
    db.session.commit()

    # override without client approval: reason needed; a conflict stops everything
    clash.status = "passed"
    db.session.commit()
    r = c.post(f"/projects/{newp.id}/go-live", data={"code": fresh_code(), "override": "1", "reason": " "}, follow_redirects=True)
    ok(b"Write why" in r.data, "sending unapproved changes needs a reason")
    before = main_sha()
    c.post(f"/projects/{newp.id}/go-live", data={"code": fresh_code(), "override": "1", "reason": "Client said yes on the phone"})
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "conflicts" in j.note and main_sha() == before and clash.status == "passed",
       "a conflicting change stops Go Live; nothing is pushed")
    clash.status = "beta"
    db.session.commit()

    # rollback (code + database snapshot)
    cur, target = gl_mod.rollback_target(newp)
    ok(cur.id == d.id and target == d.previous_commit, "Rollback goes back to what live ran before")
    ok(dv.post(f"/projects/{newp.id}/rollback", data={"deployment": str(cur.id), "code": "1"}).status_code == 403, "developers can't Rollback")
    live_calls.clear()
    c.post(f"/projects/{newp.id}/rollback", data={"deployment": str(cur.id), "code": fresh_code(), "with_db": "1"})
    j = Job.query.order_by(Job.id.desc()).first()
    db.session.refresh(ch7)
    db.session.refresh(cur)
    ok(j.kind == "rollback" and j.state == "done" and main_readme() == "Hello CRM\n", "Rollback puts the earlier code back on main and live")
    ok(cur.rolled_back and ch7.status == "beta" and ch7.live_at is None, "the release is marked rolled back; its change goes back to beta")
    ok(any("gunzip -c /root/ai-dashboard-snapshots/new-crm-1.sql.gz | mysql crm_live" in s for s in live_calls), "the database snapshot is restored when asked")
    ok("rolled back from live" in ChangeMessage.query.filter_by(change_id=ch7.id).order_by(ChangeMessage.id.desc()).first().body,
       "Claude's chat says the change was rolled back")
    ok(ActivityLog.query.filter_by(action="Rollback", project_id=newp.id).count() == 1, "Rollback is logged")

    # a rolled-back change can go live again later (squash merges)
    ch7.status = "approved"
    db.session.commit()
    c.post(f"/projects/{newp.id}/go-live", data={"code": fresh_code()})
    db.session.refresh(ch7)
    ok(ch7.status == "live" and main_readme() == "Hello CRM v3\n", "after a fix, the same change can go live again")

    # main moved on after that Go Live (a developer pushed) → Rollback undoes only the release and keeps the newer work
    cur2, target2 = gl_mod.rollback_target(newp)
    run_git("fetch", "-q", bare, "main:devmain")
    run_git("checkout", "-q", "devmain")
    open(os.path.join(seed_dir, "dev2.txt"), "w").write("later developer work\n")
    run_git("add", "dev2.txt")
    run_git("commit", "-qm", "dev: later work on main")
    run_git("push", "-q", bare, "devmain:main")
    run_git("checkout", "-q", "main")
    run_git("branch", "-q", "-D", "devmain")
    c.post(f"/projects/{newp.id}/rollback", data={"deployment": str(cur2.id), "code": fresh_code()})
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.kind == "rollback" and j.state == "done" and main_readme() == "Hello CRM\n"
       and subprocess.run(["git", "--git-dir", bare, "show", "main:dev2.txt"], capture_output=True, text=True).stdout == "later developer work\n",
       "Rollback undoes the release but keeps work a developer pushed to main afterwards")
    ch7.status = "approved"
    db.session.commit()
    c.post(f"/projects/{newp.id}/go-live", data={"code": fresh_code()})
    db.session.refresh(ch7)
    ok(ch7.status == "live" and main_readme() == "Hello CRM v3\n", "and it goes live again")

    # ---------- SQLite live database: snapshot + restore (release folders were dropped, owner 2026-11-05) ----------
    from app import golive as golive_mod
    remote_mod.push_dir = (lambda s, l, r, timeout=1800: "")
    health_answer[0] = (True, 200)
    deploy_mod.health_ok = lambda url, auth=None: health_answer[0]
    lv = newp.env("live")
    keep_live_db = lv.db_name
    lv.db_name, lv.restart_units, newp.db_engine = "sqlite:backend/crm.db", ["crm-api.service"], "sqlite"
    db.session.commit()
    snap_scripts = []
    remote_mod.run = lambda server, script, timeout=900: (snap_scripts.append(script), (0, "SIZE=10\nPATH=/root/ai-dashboard-snapshots/x.sqlite.gz"))[1]
    sj = Job(project_id=newp.id, kind="go_live", by_user_id=owner.id)
    db.session.add(sj)
    db.session.commit()
    sn = golive_mod.snapshot(newp, lv, sj)
    remote_mod.run = fake_run
    sj.state = "done"
    ok(sn and "s.backup(d)" in snap_scripts[0] and f"readlink -f {lv.deploy_path}/backend/crm.db" in snap_scripts[0] and ".sqlite.gz" in snap_scripts[0]
       and "mysqldump" not in snap_scripts[0], "a Go Live snapshot of a SQLite site is a consistent SQLite backup")
    rs = golive_mod.restore_script(newp, lv, "/root/ai-dashboard-snapshots/x.sqlite.gz")
    ok(rs.index("systemctl stop crm-api.service") < rs.index('mv -f "$T" "$D"') and "trap 'systemctl start crm-api.service" in rs
       and 'rm -f "$D-wal"' in rs, "restoring a SQLite snapshot swaps the file only while the service is stopped")
    lv.db_name, lv.restart_units, newp.db_engine = keep_live_db, [], "mysql"
    db.session.commit()
    from app.blueprints.projects import DB_RE
    ok(DB_RE.match("sqlite:backend/crm.db") and DB_RE.match("shop_live") and not DB_RE.match("sqlite:/etc/x") and not DB_RE.match("a b"),
       "a database is a name or sqlite:<file inside the folder>")
    ch7.status = "approved"
    db.session.commit()
    remote_mod.run = fake_run

    # ---------- live folder vs GitHub main: drift panel, Commit & push, Update live (real git, scripts run locally) ----------
    from app import livesync
    live_dir = os.path.join(TMP, "livecopy")
    subprocess.run(["git", "clone", "-q", "--branch", "main", bare, live_dir], check=True, capture_output=True, env=env)
    main_before_live = subprocess.run(["git", "--git-dir", bare, "rev-parse", "main"], capture_output=True, text=True).stdout.strip()

    def local_run(server, script, timeout=900):
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def bare_main(path):
        return subprocess.run(["git", "--git-dir", bare, "show", f"main:{path}"], capture_output=True, text=True).stdout

    def last_job():
        db.session.expire_all()
        return Job.query.order_by(Job.id.desc()).first()

    lv = newp.env("live")
    keep = (lv.deploy_path, lv.db_name, newp.deploy_script, lv.watch_until)
    lv.deploy_path, lv.db_name = live_dir, ""
    newp.deploy_script = "git fetch --quiet origin {branch}\ngit checkout --quiet -B {branch} origin/{branch}"
    db.session.commit()
    remote_mod.run = local_run

    st = livesync.refresh(newp)
    ok(st["files"] == [] and st["new"] == [] and st["ahead"] == 0 and st["behind"] == 0 and st["fetch_ok"], "a live folder equal to main shows no difference")
    r = c.get(f"/projects/{newp.id}")
    ok(b"same code as main" in r.data and b'disabled title="Live already has everything main has"' in r.data,
       "live equal to main: the update button is disabled")

    open(os.path.join(live_dir, "README.md"), "w").write("edited on the server by FTP\n")
    open(os.path.join(live_dir, "upload.txt"), "w").write("new file from FTP\n")
    st = livesync.refresh(newp)
    ok(st["files"] == ["README.md"] and st["new"] == ["upload.txt"], "edited and new files in live are noticed")
    r = c.get(f"/projects/{newp.id}")
    ok(b"Live differs from main" in r.data and b"Commit &amp; push to GitHub" in r.data and b"upload.txt" in r.data, "the project page says live differs and offers Commit & push")
    ok(dv.post(f"/projects/{newp.id}/live-push", data={"code": "1"}).status_code in (403, 404), "developers can't Commit & push")
    c.post(f"/projects/{newp.id}/live-push", data={"code": "000000"})
    ok(bare_main("upload.txt") == "", "a wrong authenticator code pushes nothing")

    open(os.path.join(live_dir, "server.pem"), "w").write("-----BEGIN KEY-----\n")
    c.post(f"/projects/{newp.id}/live-push", data={"code": fresh_code()})
    j = last_job()
    ok(j.kind == "live_push" and j.state == "failed" and "secret" in j.note and bare_main("README.md") == "Hello CRM v3\n",
       "a secret-looking file stops the commit; GitHub is untouched")
    os.remove(os.path.join(live_dir, "server.pem"))

    c.post(f"/projects/{newp.id}/live-push", data={"code": fresh_code()})
    j = last_job()
    ok(j.state == "done" and bare_main("README.md") == "edited on the server by FTP\n" and bare_main("upload.txt") == "new file from FTP\n",
       "Commit & push puts the live edits on GitHub main")
    ok("Live folder edits committed" in subprocess.run(["git", "--git-dir", bare, "log", "-1", "--format=%s", "main"], capture_output=True, text=True).stdout
       and ActivityLog.query.filter_by(action="Live folder committed", project_id=newp.id).count() == 1, "the commit is labelled and logged")
    db.session.refresh(lv)
    ok(lv.drift["files"] == [] and lv.drift["new"] == [] and lv.drift["ahead"] == 0 and lv.drift["behind"] == 0, "live equals main again afterwards")

    # a developer pushes to main; live gets an edit too → live commits go through a side branch and are merged here
    run_git("fetch", "-q", bare, "main:devmain")
    run_git("checkout", "-q", "devmain")
    open(os.path.join(seed_dir, "dev3.txt"), "w").write("developer v1\n")
    run_git("add", "dev3.txt")
    run_git("commit", "-qm", "dev: dev3")
    run_git("push", "-q", bare, "devmain:main")
    open(os.path.join(live_dir, "live2.txt"), "w").write("live only\n")
    st = livesync.refresh(newp)
    ok(st["new"] == ["live2.txt"] and st["behind"] == 1, "a new file in live and a newer main are both seen")
    c.post(f"/projects/{newp.id}/live-update", data={"code": fresh_code()})
    j = last_job()
    ok(j.state == "failed" and "Commit & push" in j.note, "Update live refuses while live has edits GitHub lacks (they would be lost)")
    c.post(f"/projects/{newp.id}/live-push", data={"code": fresh_code()})
    j = last_job()
    ok(j.state == "done" and bare_main("live2.txt") == "live only\n" and bare_main("dev3.txt") == "developer v1\n",
       "when main moved too, live's work is merged into main — nothing lost on either side")
    ok(subprocess.run(["git", "--git-dir", bare, "rev-parse", "--verify", "-q", "refs/heads/dashboard/live-edits"], capture_output=True).returncode != 0,
       "the side branch is removed after the merge")
    db.session.refresh(lv)
    ok(lv.drift["ahead"] == 0 and lv.drift["behind"] >= 1, "afterwards live only lacks the newer main")

    # Update live from main: deploys by git pull in the live folder, records a deployment
    deps = Deployment.query.filter_by(project_id=newp.id).count()
    c.post(f"/projects/{newp.id}/live-update", data={"code": fresh_code()})
    j = last_job()
    head_live = subprocess.run(["git", "rev-parse", "HEAD"], cwd=live_dir, capture_output=True, text=True).stdout.strip()
    head_main = subprocess.run(["git", "--git-dir", bare, "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
    ok(j.state == "done" and head_live == head_main and os.path.exists(os.path.join(live_dir, "dev3.txt")), "Update live from main brings live to main with git pull")
    d2 = Deployment.query.filter_by(project_id=newp.id).order_by(Deployment.id.desc()).first()
    ok(Deployment.query.filter_by(project_id=newp.id).count() == deps + 1 and d2.change_ids == [] and "Update from main" in d2.note, "the update is recorded as a deployment")
    deps += 1
    c.post(f"/projects/{newp.id}/live-update", data={"code": fresh_code()})
    j = last_job()
    ok(j.state == "done" and "nothing to do" in (j.note + j.output) and Deployment.query.filter_by(project_id=newp.id).count() == deps,
       "when live already equals main there is nothing to deploy")

    # conflict: developer changes dev3.txt on main while live edits it by hand → stops, nothing lost, main untouched
    run_git("checkout", "-q", "devmain")
    run_git("fetch", "-q", bare, "main")
    run_git("reset", "-q", "--hard", "FETCH_HEAD")
    open(os.path.join(seed_dir, "dev3.txt"), "w").write("developer v2\n")
    run_git("commit", "-qam", "dev: dev3 v2")
    run_git("push", "-q", bare, "devmain:main")
    open(os.path.join(live_dir, "dev3.txt"), "w").write("live v2\n")
    before = subprocess.run(["git", "--git-dir", bare, "rev-parse", "main"], capture_output=True, text=True).stdout
    c.post(f"/projects/{newp.id}/live-push", data={"code": fresh_code()})
    j = last_job()
    ok(j.state == "failed" and "conflict" in j.note and subprocess.run(["git", "--git-dir", bare, "rev-parse", "main"], capture_output=True, text=True).stdout == before
       and subprocess.run(["git", "--git-dir", bare, "show", "dashboard/live-edits:dev3.txt"], capture_output=True, text=True).stdout == "live v2\n",
       "conflicting live edits stop the merge; main is untouched and the edits are kept on a side branch")

    # put the shared test repo and the project back as they were
    run_git("checkout", "-q", "-f", "main")
    run_git("branch", "-q", "-D", "devmain")
    subprocess.run(["git", "--git-dir", bare, "update-ref", "refs/heads/main", main_before_live], check=True)
    subprocess.run(["git", "--git-dir", bare, "update-ref", "-d", "refs/heads/dashboard/live-edits"], check=True)
    lv.deploy_path, lv.db_name, newp.deploy_script, lv.watch_until = keep
    lv.drift = lv.drift_at = None
    db.session.commit()
    remote_mod.run = fake_run

    # ---------- automatic beta (owner, 2026-10-17) — fake server + fake Claude Code ----------
    from app import betas as betas_mod
    setup_scripts, recipe_exit = [], [0]

    def setup_run(server, script, timeout=900):
        setup_scripts.append(script)
        if "grep -o 'lsphp" in script:
            return 0, "PHP=84"
        if "stat -c %U /home/$H" in script:
            return 0, "USER=betauser1"
        if "grep -oE '^[A-Za-z0-9_]+='" in script:
            return 0, "APP_KEY\nAPP_DEBUG\nAPP_URL\nDB_DATABASE\nRAZORPAY_KEY_SECRET\nMAIL_PASSWORD\nCACHE_STORE"
        if script.startswith("runuser -u"):
            return recipe_exit[0], "npm ERR! Missing script: build" if recipe_exit[0] else "done"
        if "openssl passwd -1 -stdin" in script:
            return 0, "CHANGED=1" if not locked_once else ""
        if "[Service]" in script:
            return 0, "PORT=6101\nSTATE=active"
        if "lswsctrl restart" in script:
            restarted[0] = True
        if "for V in /usr/local/lsws/conf/vhosts" in script:
            return 0, "SITE eyenosis.com 200\nSITE crm.verixaatech.com 302\nSITE crm.ivinfotech.ca " + (sites_after[0] if restarted[0] else "200")
        return 0, "ok"

    cc_setup = []
    locked_once, sites_after, restarted = False, ["200"], [False]
    import secrets

    from app import beta_tools as bt
    from app.crypto import sha256 as _sha256
    plans, tool_log = {"setup": [], "fix": []}, []

    class FakeClaudeTools:
        """Stands in for Claude Code: reads the MCP config it was given and calls the beta tools in order."""
        def __init__(self, args, cwd, env):
            cc_setup.append((args, str(cwd), env))
            cfg = _json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["beta"]
            job = Job.query.filter_by(tool_token_hash=_sha256(cfg["headers"]["Authorization"][7:])).one()
            plan = plans["fix"] if "saved steps failed" in args[args.index("-p") + 1] else plans["setup"]
            for name, a in plan:
                tool_log.append((name, bt.call(job, name, a)))
            ev = {"type": "result", "subtype": "success", "is_error": False, "result": "The beta works.\nCheck the login page.",
                  "session_id": "s", "total_cost_usd": 0.3, "num_turns": 9, "usage": {}}
            self.stdout, self.stderr, self.returncode = iter([_json.dumps(ev) + "\n"]), io.StringIO(""), 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    plans["setup"] = [("status", {}), ("make_website", {}), ("copy_database", {"engine": "mysql"}), ("put_code", {}),
                      ("write_env", {"set": {"APP_NAME": "CRM beta"}}), ("run", {"command": "composer install --no-interaction"}),
                      ("write_file", {"path": ".htaccess", "content": "RewriteEngine On\nRewriteRule ^(.*)$ public/$1 [L]"}),
                      ("check_site", {"paths": ["/", "/login"]}),
                      ("save_recipe", {"steps": ["composer install --no-interaction", "php artisan migrate --force", "@restart api"]})]
    plans["fix"] = [("run", {"command": "npm install"}), ("save_recipe", {"steps": ["composer install --no-interaction", "php artisan migrate --force"]})]

    remote_mod.run, remote_mod.push_dir = setup_run, (lambda s, l, r, timeout=1800: "")
    cc_mod._spawn = lambda args, cwd, env: FakeClaudeTools(args, cwd, env)
    health_answer[0] = (True, 200)
    deploy_mod.health_ok = lambda url, auth=None: ((True, 404) if url.endswith((".env", "/.git/config")) else
                                                   (True, 401) if auth is None and ".beta.ivinfotech.com" in url else health_answer[0])
    n = betas_mod.names(newp)
    ok(n["host"] == f"{newp.slug}.beta.ivinfotech.com" and n["db"].endswith("_beta") and len(n["dbuser"]) <= 32, "beta names come from the project")
    ok(dv.post(f"/projects/{newp.id}/jobs/beta_setup").status_code in (403, 404), "developers can't make a beta")
    ok(b"Create beta automatically" in c.get(f"/projects/{newp.id}").data or b"Rebuild beta automatically" in c.get(f"/projects/{newp.id}").data,
       "the owner gets the automatic beta button")
    c.post(f"/projects/{newp.id}/jobs/beta_setup")
    j = Job.query.order_by(Job.id.desc()).first()
    db.session.refresh(newp)
    b = newp.env("beta")
    ok(j.kind == "beta_setup" and j.state == "done" and "Beta ready" in j.note, "Claude makes the whole beta with the tools")
    ok(all(not err for name, (txt, err) in tool_log), "every tool call in the setup succeeded: " + "; ".join(f"{n_}:{t[:60]}" for n_, (t, e) in tool_log if e))
    ok(b.managed and b.url == f"https://{n['host']}" and b.site_user == "betauser1" and b.db_name == n["db"]
       and decrypt_secret(b.db_password_enc), "the beta is recorded: its address, website user, own database")
    ok(any("cyberpanel createWebsite" in sc and "--php 8.4" in sc for sc in setup_scripts), "the website gets live's PHP version")
    dbs = next(sc for sc in setup_scripts if "mysqldump" in sc)
    ok("crm_live" in dbs and f"| mysql {n['db']}" in dbs and "DROP" not in dbs.upper().replace("IF NOT EXISTS", ""),
       "live's database is only dumped (read) into the beta's own database")
    envs = next(sc for sc in setup_scripts if "EF=" in sc and "RAZORPAY_KEY_SECRET" in sc)
    ok("APP_DEBUG" in envs and "VAL=''" in envs and "CRM beta" in envs and "crm_live" not in envs,
       "beta settings: secrets blanked, debug off, Claude's own keys added, never the live database")
    args, cwd, env = cc_setup[-1]
    al = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    ok("--strict-mcp-config" not in args and al == ["mcp__beta", "WebSearch", "WebFetch"] and "Bash" in args[args.index("--disallowedTools"):]
       and args[args.index("--append-system-prompt") + 1] == betas_mod.TOOL_RULES and "SECRET_KEY" not in env
       and env.get("CLAUDE_CONFIG_DIR") == cl_mod.config_dir(cl_mod.default_account()) and "CLAUDE_CODE_OAUTH_TOKEN" not in env,
       "beta tool runs: the beta tools + web, the default account's login (connectors load) — no shell, no own file access, no secrets")
    ok(any("openssl passwd -1 -stdin" in sc and "AIDASH-BETA-LOCK" in sc for sc in setup_scripts) and b.beta_password_enc
       and setup_scripts.index(next(sc for sc in setup_scripts if "openssl passwd" in sc)) < setup_scripts.index(dbs),
       "the beta is locked with a password before any data is copied")
    ok(f"https://beta:{decrypt_secret(b.beta_password_enc)}@{n['host']}".encode() in c.get(f"/projects/{newp.id}").data,
       "the beta card has an Open beta link that signs in by itself (no password popup needed)")
    ok("Locked — show login".encode() in c.get(f"/projects/{newp.id}").data and decrypt_secret(b.beta_password_enc).encode() in c.get(f"/projects/{newp.id}").data,
       "the project page shows the beta login")
    ok(newp.beta_recipe.splitlines() == ["composer install --no-interaction", "php artisan migrate --force", "@restart api"],
       "the steps Claude found are kept as the recipe")
    ok(Deployment.query.order_by(Deployment.id.desc()).first().note.startswith("Beta made by Claude") and j.tool_token_hash is None,
       "the setup is recorded and the tools' key is gone when the job ends")

    # the tools refuse what is not allowed
    j.state = "running"
    db.session.commit()
    refused = {
        "sudo": bt.call(j, "run", {"command": "sudo rm -rf /"}),
        "traversal": bt.call(j, "read_file", {"path": "../../etc/passwd"}),
        "env read": bt.call(j, "read_file", {"path": "backend/.env"}),
        "live secret": bt.call(j, "write_env", {"set": {"RAZORPAY_KEY_SECRET": "rzp_live_x"}}),
        "live db": bt.call(j, "write_env", {"set": {"DB_DATABASE": "crm_live"}}),
        "postgres": bt.call(j, "copy_database", {"engine": "postgres"}),
        "sqlite outside": bt.call(j, "copy_database", {"engine": "sqlite", "live_file": "../other/app.db"}),
        "not sqlite": bt.call(j, "copy_database", {"engine": "sqlite", "live_file": "notes.txt"}),
        "bad service": bt.call(j, "start_service", {"name": "API!", "command": "python app.py"}),
        "service sudo": bt.call(j, "start_service", {"name": "api", "command": "sudo python app.py"}),
        "recipe sudo": bt.call(j, "save_recipe", {"steps": ["sudo systemctl restart x"]}),
    }
    ok(all(err for txt, err in refused.values()), "tools refuse: " + ", ".join(k for k, (t, e) in refused.items() if not e))
    setup_scripts.clear()
    txt, err = bt.call(j, "copy_database", {"engine": "sqlite", "live_file": "backend/crm.db"})
    sq = next(sc for sc in setup_scripts if "sqlite3.connect" in sc)
    ok(not err and "mode=ro" in sq and "/home/live/crm/backend/crm.db" in sq and f"{b.deploy_path}/backend/crm.db" in sq,
       "SQLite is copied with a read-only online backup into the same path in the beta")
    txt, err = bt.call(j, "start_service", {"name": "api", "command": "venv/bin/gunicorn -b 127.0.0.1:$PORT app:app", "workdir": "backend"})
    unit = next(sc for sc in setup_scripts if "[Service]" in sc)
    ok(not err and "User=betauser1" in unit and "Environment=PORT=$PORT" in unit and f"WorkingDirectory={b.deploy_path}/backend" in unit,
       "a backend runs as the beta website's user on its own port")
    ok(any("AIDASH-PROXY-6101" in sc for sc in setup_scripts)
       and "127.0.0.1:" in betas_mod.PROXY_BLOCK.format(port=6100), "the service port is registered with the web server for this beta only")
    try:
        betas_mod.allow_proxy(newp.env("beta"), 5050, j)
        ok(False, "proxy entries only for beta service ports")
    except deploy_mod.DeployError:
        ok(True, "proxy entries only for beta service ports (live ports refused)")
    ok(not bt.call(j, "web_errors", {})[1], "Claude can read this beta's web-server errors")
    ok("abc123" not in bt._redact("DB_PASSWORD=abc123\nAPP_KEY=base64:zz") and "APP_NAME=CRM" in bt._redact("APP_NAME=CRM"),
       "secret values are hidden in what tools send back to Claude")

    # Step 4: code outside the web root (backends / single-page apps) + publishing the built files
    setup_scripts.clear()
    txt, err = bt.call(j, "put_code", {"place": "app"})
    db.session.refresh(newp)
    ok(not err and newp.env("beta").deploy_path == f"/home/{n['host']}/app", "put_code place=app keeps the code outside the web root")
    txt, err = bt.call(j, "write_file", {"path": "web:.htaccess", "content": "RewriteEngine On"})
    ok(not err and any(f"/home/{n['host']}/public_html/.htaccess" in sc for sc in setup_scripts), "write_file web: writes into the web root")
    txt, err = bt.call(j, "publish", {"folder": "web/dist"})
    pub = next(sc for sc in setup_scripts if "rsync -a --delete --exclude=/.htaccess" in sc)
    ok(not err and f"/home/{n['host']}/app/web/dist" in pub and f"/home/{n['host']}/public_html" in pub, "publish copies the built folder into the web root")
    keep_run = remote_mod.run
    remote_mod.run = lambda server, script, timeout=900: (3, "LIVE_ADDRESS_IN_BUILD") if "LIVE_ADDRESS_IN_BUILD" in script else keep_run(server, script, timeout)
    txt, err = bt.call(j, "publish", {"folder": "web/dist"})
    remote_mod.run = keep_run
    ok(err and "still call the LIVE site" in txt, "a build that still calls the live site is never published on a beta")
    restarted[0] = False
    remote_mod.run = lambda server, script, timeout=900: (0, "CHANGED=1") if "before-open-api" in script else keep_run(server, script, timeout)
    txt, err = bt.call(j, "publish", {"folder": "web/dist"})
    remote_mod.run = keep_run
    ok(not err and "asked once" in txt and restarted[0], "a single-page app with an API: the password stays on the pages (asked once), /api/ uses the app's own login")
    from app.betas import OPEN_API_PY, LOCK_BLOCK, LOCK_MARK
    import tempfile as _tf, subprocess as _sp2
    _d = _tf.mkdtemp()
    open(f"{_d}/v", "w").write(LOCK_BLOCK.format(mark=LOCK_MARK, host="x.beta.ivinfotech.com") + "\nvhssl  {\n}\n")
    open(f"{_d}/h", "w").write("RewriteRule ^api/(.*)$ http://127.0.0.1:6100/api/$1 [P,L]\n")
    r1 = _sp2.run(["python3", "-c", OPEN_API_PY, f"{_d}/v", f"{_d}/h", LOCK_MARK], capture_output=True, text=True).stdout
    r2 = _sp2.run(["python3", "-c", OPEN_API_PY, f"{_d}/v", f"{_d}/h", LOCK_MARK], capture_output=True, text=True).stdout
    vtxt = open(f"{_d}/v").read()
    ok("CHANGED=1" in r1 and r2 == "" and vtxt.count("realm                   aidashBeta") == 1 and "context exp:^/(?!api/)" in vtxt
       and "handler                 127.0.0.1:6100" in vtxt, "the vhost change: realm moves to every path except /api/, once")
    open(f"{_d}/h", "w").write("RewriteRule ^(.*)$ http://127.0.0.1:6100/$1 [P,L]\n")
    open(f"{_d}/v", "w").write(LOCK_BLOCK.format(mark=LOCK_MARK, host="x.beta.ivinfotech.com") + "\nvhssl  {\n}\n")
    ok(_sp2.run(["python3", "-c", OPEN_API_PY, f"{_d}/v", f"{_d}/h", LOCK_MARK], capture_output=True, text=True).stdout == "",
       "a site that is all one service keeps the whole password lock")
    ok(not bt.call(j, "save_recipe", {"steps": ["npm ci", "@publish web/dist"]})[1] and bt.call(j, "save_recipe", {"steps": ["@publish ../x"]})[1],
       "recipes may publish a built folder, never outside the code folder")
    bt.call(j, "put_code", {"place": "webroot"})
    bt.call(j, "save_recipe", {"steps": ["composer install --no-interaction", "php artisan migrate --force", "@restart api"]})

    # the tools endpoint: local only, one-job key, MCP messages
    db.session.refresh(newp)
    key = "test-key-" + secrets.token_hex(4)
    j.tool_token_hash = _sha256(key)
    db.session.commit()
    loc = {"REMOTE_ADDR": "127.0.0.1"}
    H = {"Authorization": f"Bearer {key}"}
    r = c.post("/internal/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, headers=H, environ_base=loc)
    ok(r.status_code == 200 and r.json["result"]["protocolVersion"] == "2025-06-18", "MCP initialize answers")
    ok(c.post("/internal/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=H, environ_base=loc).status_code == 202, "MCP notifications are accepted")
    r = c.post("/internal/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=H, environ_base=loc)
    ok({t["name"] for t in r.json["result"]["tools"]} >= {"make_website", "copy_database", "put_code", "write_env", "run", "start_service", "save_recipe", "report_problem"},
       "MCP lists the beta tools")
    r = c.post("/internal/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "run", "arguments": {"command": "sudo id"}}}, headers=H, environ_base=loc)
    ok(r.json["result"]["isError"] is True, "MCP tool refusals come back as errors Claude can read")
    ok(c.post("/internal/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, headers=dict(H, **{"X-Forwarded-For": "1.2.3.4"}), environ_base=loc).status_code == 404,
       "the tools can't be reached through the web server")
    ok(c.post("/internal/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"}, headers={"Authorization": "Bearer wrong"}, environ_base=loc).status_code == 401,
       "a wrong key is refused")
    j.state = "done"
    db.session.commit()
    ok(c.post("/internal/mcp", json={"jsonrpc": "2.0", "id": 6, "method": "tools/list"}, headers=H, environ_base=loc).status_code == 401,
       "the key stops working when the job is over")
    j.tool_token_hash = None
    db.session.commit()

    # a later beta deploy replays the recipe (no Claude); when a step fails, Claude fixes it with the tools
    setup_scripts.clear()
    n_cc = len(cc_setup)
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "done" and len(cc_setup) == n_cc and any(sc.startswith("runuser -u betauser1") and "php artisan migrate --force" in sc for sc in setup_scripts)
       and any("systemctl restart" in sc for sc in setup_scripts), "a beta deploy replays the recipe without Claude, restarts the service")
    rep = next(sc for sc in setup_scripts if sc.startswith("runuser -u betauser1"))
    lh, bh = betas_mod._hosts(newp)
    ok(".env.production.local" in rep and f"//{bh}" in rep and rep.index(".env.production.local") < rep.index("php artisan migrate"),
       "before every beta build, a frontend's live address is replaced by the beta's own (.env.production.local)")
    recipe_exit[0] = 1
    c.post(f"/projects/{newp.id}/jobs/beta_deploy")
    j = Job.query.order_by(Job.id.desc()).first()
    db.session.refresh(newp)
    ok(j.state == "done" and len(cc_setup) == n_cc + 1 and "Claude fixed a step" in j.note and "Missing script" in cc_setup[-1][0][cc_setup[-1][0].index("-p") + 1]
       and "@restart api" not in newp.beta_recipe, "a failing step is handed to Claude with the error; it fixes the beta and the steps")
    recipe_exit[0] = 0

    # Step 3: saving the details of a project whose live is known (and that has no beta) starts the beta by itself
    from app.models import Environment as _Env
    q = Project(name="Shop", slug="shop", client_name="Client S", github_repo="company-org/new-crm", tech_stack=["Laravel"],
                default_branch="main", needs_setup=True)
    q.environments.append(_Env(kind="live", branch="main", url="https://shop.example.com", server_id=s1.id,
                               deploy_path="/home/shop/public_html", db_name="shop_live"))
    db.session.add(q)
    db.session.commit()
    n_jobs = Job.query.filter_by(project_id=q.id).count()
    r = c.post(f"/projects/{q.id}/details", data={"name": "Shop", "client": "Client S", "live_url": "shop.example.com",
                                                 "beta_url": "", "tech": "Laravel", "host_type": "vps", "repo": "company-org/new-crm", "active": "1"},
               follow_redirects=True)
    db.session.refresh(q)
    qj = Job.query.filter_by(project_id=q.id).order_by(Job.id.desc()).first()
    ok(b"Claude is making the beta now" in r.data and qj is not None and qj.kind == "beta_setup" and Job.query.filter_by(project_id=q.id).count() == n_jobs + 1,
       "saving project details starts the beta by itself")
    ok(q.env("beta").url == "https://shop.beta.ivinfotech.com" and q.env("beta").managed, "an empty beta address becomes <project>.beta.ivinfotech.com")
    n_jobs = Job.query.filter_by(project_id=q.id).count()
    c.post(f"/projects/{q.id}/details", data={"name": "Shop", "client": "Client S", "live_url": "shop.example.com", "beta_url": "",
                                             "tech": "Laravel", "host_type": "vps", "repo": "company-org/new-crm", "active": "1"})
    ok(Job.query.filter_by(project_id=q.id).count() == n_jobs, "a project that already has a beta doesn't get another one")

    # Step 5: a repo that is renamed / transferred stays the same project (GitHub's id), never "new" or "missing"
    from app import gitcheck
    gp = Project(name="Moved", slug="moved", client_name="Client M2", github_repo="company-org/clinic-app", github_repo_id=987654)
    db.session.add(gp)
    db.session.commit()
    n_proj = Project.query.count()
    fake.append({"id": 987654, "full_name": "company-org/clinic-app-v2", "name": "clinic-app-v2", "private": True,
                 "default_branch": "main", "language": "PHP", "archived": False, "pushed_at": "2026-10-17T09:00:00Z"})
    gh.fetch_repos = fake_fetch
    new_p, missing_p, total_p = gh.sync()
    db.session.refresh(gp)
    ok(gp.github_repo == "company-org/clinic-app-v2" and Project.query.count() == n_proj and gp not in missing_p and not gp.repo_missing
       and ActivityLog.query.filter_by(action="GitHub repo moved").count() == 1, "a renamed / transferred repo is followed, not duplicated")
    fake.pop()
    ok([gitcheck.repo_slug(u) for u in ("https://github.com/ivalema35/eye-saas-hms.git", "git@github-x:IvRonak/A.git", "https://gitlab.com/a/b")]
       == ["ivalema35/eye-saas-hms", "ivronak/a", ""], "git origins are read the same in every URL form")

    # beta lock: a new password; a site that stops answering after the restart raises an alert
    old_pw = decrypt_secret(newp.env("beta").beta_password_enc)
    c.post(f"/projects/{newp.id}/jobs/beta_lock", data={"new_password": "1"})
    db.session.refresh(newp)
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.kind == "beta_lock" and j.state == "done" and decrypt_secret(newp.env("beta").beta_password_enc) != old_pw, "owner can renew the beta password")
    ok(dv.post(f"/projects/{newp.id}/jobs/beta_lock").status_code in (403, 404), "developers can't change the beta lock")
    newp.env("beta").beta_password_enc = ""
    db.session.commit()
    n_err = Alert.query.filter_by(kind="error").count()
    sites_after[0], restarted[0] = "000", False
    sc_before = len(setup_scripts)
    import app.betas as _b
    vh = newp.env("beta")
    orig_run = remote_mod.run
    remote_mod.run = lambda server, script, timeout=900: (0, "CHANGED=1") if "openssl passwd" in script else orig_run(server, script, timeout)
    c.post(f"/projects/{newp.id}/jobs/beta_lock")
    remote_mod.run = orig_run
    j = Job.query.order_by(Job.id.desc()).first()
    ok(j.state == "failed" and "crm.ivinfotech.ca" in j.note and Alert.query.filter_by(kind="error").count() == n_err + 1,
       "a site that stops answering after the restart is caught and alerted")
    sites_after[0] = "200"
    ok(_b.names(newp)["host"].endswith(".beta.ivinfotech.com"), "beta names stay under the beta domain")
    remote_mod.run = fake_run
    remote_mod.run = fake_run

    # ---------- expiry reminders (GitHub token / Anthropic key) ----------
    from datetime import timedelta
    from app import reminders
    from app.clock import to_local, now as _now
    today = to_local(_now()).date()
    gh._remember_expiry(NS(headers={"github-authentication-token-expiration": f"{(today + timedelta(days=6)).isoformat()} 00:00:00 UTC"}))
    ok(get_setting("github_token_expires") == (today + timedelta(days=6)).isoformat(), "GitHub token expiry is read from GitHub's answer")
    n0 = len(sent_msgs)
    msgs = reminders.check()
    ok(len(msgs) == 1 and "in 7 days" in msgs[0] and len(sent_msgs) == n0 + 1, "7-day reminder goes to Telegram")
    ok(reminders.check() == [], "the same reminder is not sent twice")
    set_setting("github_token_expires", (today + timedelta(days=1)).isoformat())
    db.session.commit()
    ok("tomorrow" in reminders.check()[0], "1-day reminder")
    c.post("/settings/claude", data={"model": "claude-opus-5-5", "effort": "high", "max_usd": "3", "max_turns": "40",
                                     "key_expires": (today - timedelta(days=1)).isoformat()})
    ok(get_setting("claude_key_expires") == (today - timedelta(days=1)).isoformat(), "owner saves the Anthropic key's expiry date")
    msgs = reminders.check()
    ok(msgs and "expired" in msgs[0], "expired key → alert")
    ok(b"expired on" in c.get("/settings").data, "Settings shows the expiry warning")

    # a checkout made with a narrow umask is opened up for the runner user (group write), Claude can edit it
    import stat as _st
    _rt = wsmod.prepare(newp, newp.default_branch or "main", newp.default_branch or "main")
    _f = next(p for p in _rt.rglob("*") if p.is_file() and ".git" not in p.parts)
    os.chmod(_f, 0o644)
    os.chmod(_f.parent, 0o755)
    wsmod.prepare(newp, newp.default_branch or "main", newp.default_branch or "main")
    ok(os.stat(_f).st_mode & _st.S_IWGRP and os.stat(_f.parent).st_mode & _st.S_IWGRP and os.stat(_f.parent).st_mode & _st.S_ISGID,
       "every checkout is group-writable for Claude Code's runner user (no 'Permission denied')")

    # ---------- mobile apps: test builds on Codemagic (CHANGES Step 10) — fake Codemagic, local repos ----------
    from app import codemagic as cm_mod, mobile as mobile_mod
    from app.models import AppBuild
    import tempfile as _tf2
    _ad = _tf2.mkdtemp()
    os.makedirs(f"{_ad}/eye_app/android")
    os.makedirs(f"{_ad}/eye_app/lib")
    os.makedirs(f"{_ad}/vendor/x/android")
    open(f"{_ad}/eye_app/pubspec.yaml", "w").write("name: eye_care_app\ndependencies:\n  flutter:\n    sdk: flutter\n")
    open(f"{_ad}/vendor/x/pubspec.yaml", "w").write("name: x\ndependencies:\n  flutter:\n    sdk: flutter\n")
    open(f"{_ad}/composer.json", "w").write("{}")
    ok(mobile_mod.find_apps(_ad) == [{"dir": "eye_app", "name": "eye_care_app"}], "Flutter apps are found in the repo (vendor folders skipped)")
    y = mobile_mod.yaml_for([{"dir": "eye_app", "name": "eye_care_app"}])
    ok("aidash_android_eye_app:" in y and "flutter build apk --release" in y and "$BETA_HOST" in y and "The live address is still in the app" in y
       and y.index("Point the app at the beta") < y.index("flutter build apk"), "the build points the app at the beta before building, and stops if live is left")

    calls, cm_state = [], {"status": "building"}

    def fake_cm(method, path, json=None):
        calls.append((method, path, json))
        if path == "/apps":
            return {"applications": [{"_id": "app1", "appName": "new-crm"}]} if method == "GET" else {"_id": "app1"}
        if path == "/builds":
            return {"buildId": f"b{len([c for c in calls if c[1] == '/builds'])}"}
        if path.startswith("/builds/"):
            return {"build": {"status": cm_state["status"], "artefacts": [
                {"name": "app-release.apk", "url": "https://api.codemagic.io/artifacts/u1/u2/app-release.apk"},
                {"name": "mapping.txt", "url": "https://api.codemagic.io/artifacts/u1/u2/mapping.txt"}]}}
        if path.endswith("/public-url"):
            return {"url": "https://api.codemagic.io/artifacts/.public-link", "expiresAt": "2030-01-01T00:00:00+00:00"}
        return {}

    cm_mod.api = fake_cm
    ok(dv.post("/settings/codemagic", data={"token": "x" * 40}).status_code == 403, "developers can't set the Codemagic token")
    c.post("/settings/codemagic", data={"token": "cm-token-" + "a" * 30})
    ok(get_setting("codemagic_token_enc") and "cm-token" not in get_setting("codemagic_token_enc"), "the Codemagic token is stored encrypted")
    c.post("/settings/codemagic/test")
    ok((get_setting("codemagic_last_test") or "").startswith("OK"), "Test Codemagic reads the account")

    newp.mobile_apps = [{"dir": "eye_app", "name": "eye_care_app"}]
    db.session.commit()
    lh, bh = betas_mod._hosts(newp)
    ok(lh and bh and lh != bh, "the project has a live and a beta address")
    c.post(f"/projects/{newp.id}/apps/build", data={"app_dir": "../etc"})
    ok(AppBuild.query.count() == 0, "only a known app folder can be built")
    c.post(f"/projects/{newp.id}/apps/build", data={"app_dir": "eye_app"})
    b1 = AppBuild.query.order_by(AppBuild.id.desc()).first()
    sb = next(cl for cl in calls if cl[1] == "/builds")
    yml = subprocess.run(["git", "--git-dir", bare, "show", "aidash/app-main:codemagic.yaml"], capture_output=True, text=True).stdout
    ok(b1 and b1.status == "building" and b1.branch == "aidash/app-main" and sb[2]["workflowId"] == "aidash_android_eye_app"
       and sb[2]["branch"] == "aidash/app-main" and sb[2]["environment"]["variables"] == {"LIVE_HOST": lh, "BETA_HOST": bh}
       and "aidash_android_eye_app" in yml, "a test build: own branch with codemagic.yaml, Codemagic told the live + beta addresses")
    main_yaml = subprocess.run(["git", "--git-dir", bare, "show", "main:codemagic.yaml"], capture_output=True, text=True)
    ok(main_yaml.returncode != 0, "the default branch is never touched by a test build")
    n_alerts = Alert.query.count()
    page = c.get(f"/projects/{newp.id}").data
    ok(b"Building" in page and AppBuild.query.get(b1.id).status == "building", "a running build shows as building")
    cm_state["status"] = "finished"
    page = c.get(f"/projects/{newp.id}").data
    db.session.refresh(b1)
    pub = [cl for cl in calls if cl[1].endswith("/public-url")]
    ok(b1.status == "ready" and b1.file_name == "app-release.apk" and b1.file_url.endswith(".public-link")
       and pub and pub[-1][1] == "/artifacts/u1/u2/app-release.apk/public-url" and b">Download</a>" in page and b"qr-box" in page and b"<svg" in page
       and Alert.query.count() == n_alerts + 1, "a finished build gets a public download link + QR code, and the owner is told")
    ch_app = Change(project_id=newp.id, title="App text", status="passed", branch=ch7.branch, files=["eye_app/lib/main.dart", "README.md"],
                    requested_by_id=owner.id)
    db.session.add(ch_app)
    db.session.commit()
    ok(mobile_mod.touched_apps(newp, ch_app) == ["eye_app"], "a change that touches an app is noticed")
    ok(mobile_mod.needs_build(newp, ch_app) == ["eye_app"], "it is highlighted for a test build (nothing builds by itself)")
    page = c.get(f"/projects/{newp.id}?change={ch_app.id}").data
    ok(b"changed the app code: eye_app" in page and b"Build test app" in page, "the project page highlights the change's app code with a Build button")
    cm_state["status"] = "failed"
    c.post(f"/projects/{newp.id}/apps/build", data={"app_dir": "eye_app", "change_id": ch_app.id})
    nb = AppBuild.query.order_by(AppBuild.id.desc()).first()
    ok(nb.change_id == ch_app.id and nb.branch == f"aidash/app-change-{ch_app.id}", "the owner builds the change's test app by hand")
    mobile_mod.refresh_due()
    db.session.refresh(nb)
    ok(nb.status == "failed" and "failed" in nb.error and mobile_mod.needs_build(newp, ch_app) == ["eye_app"],
       "a failed build is marked, reported and highlighted again")
    ch_app.files = ["README.md"]
    db.session.commit()
    ok(mobile_mod.needs_build(newp, ch_app) == [], "a change that doesn't touch an app asks for nothing")
    # the QR code holds a short public link (the Codemagic link is ~400 characters — too dense to scan)
    with app.test_request_context():
        tok = mobile_mod.download_token(b1)
    pub_c = app.test_client()
    r = pub_c.get(f"/d/{b1.id}/{tok}")
    ok(r.status_code == 302 and r.headers["Location"] == b1.file_url and r.headers.get("Referrer-Policy") == "no-referrer",
       "the short phone link opens the download without a dashboard login")
    ok(pub_c.get(f"/d/{b1.id}/{'0' * 24}").status_code == 404 and pub_c.get(f"/d/{nb.id}/{tok}").status_code == 404,
       "a wrong token or a build that isn't ready gives nothing")
    b1.url_expires_at = _now() - timedelta(hours=1)
    db.session.commit()
    ok(pub_c.get(f"/d/{b1.id}/{tok}").status_code == 404, "an expired link gives nothing")

    # change runs can look at the beta: reproduce on the beta, read its logs, run checks there (owner, 2026-10-22)
    from app import agent as agent_mod, claude_code as cc_mod2, beta_tools as bt3
    import requests as _rq
    beta_d = newp.env("beta")
    beta_d.managed, beta_d.site_user = True, beta_d.site_user or "betauser1"
    db.session.commit()
    dbg = agent_mod._beta_debug_start(ch7, newp)
    dj = Job.query.get(dbg[2])
    ok(dbg and dj.kind == "change_debug" and dj.state == "running" and dj.tool_token_hash and dj.context["mode"] == "change_debug"
       and {t["name"] for t in bt3.tool_list(dj)} == {"beta_status", "beta_request", "beta_logs", "beta_run", "report_problem",
                                                      "beta_probe", "beta_access_log", "beta_webserver", "ask_owner", "permission_prompt",
                                                      "server_command", "root_command"},
       "a change run gets the beta tools (status, request, logs, run) through a one-time key")
    with app.test_request_context():
        cargs = cc_mod2.command("x", "claude-sonnet-5-5", "s", False, 5, 1.0, mcp_config=dbg[0])
    al2 = cargs[cargs.index("--allowedTools") + 1:cargs.index("--mcp-config")]
    ok("mcp__beta" in al2 and "Edit" in al2 and "WebSearch" in al2 and "--strict-mcp-config" not in cargs
       and cargs[cargs.index("--permission-prompt-tool") + 1] == "mcp__beta__permission_prompt",
       "Claude Code keeps its normal tools, gets the beta tools, the account's connectors, and asks the owner for anything else")
    seen_req = []

    class _R:
        def __init__(self, status, text, ctype="text/html", url="", history=()):
            self.status_code, self.text, self.headers, self.url, self.history = status, text, {"Content-Type": ctype}, url, list(history)

    real_req = _rq.Session.request

    def fake_req(self, method, url, **kw):
        seen_req.append((method, url, kw.get("data"), self.auth))
        if method == "GET":
            return _R(200, '<form><input type="hidden" name="_token" value="tok123"></form><script>x()</script><p>Forgot password</p>', url=url)
        return _R(500, "<h1>Server Error</h1>", url=url)

    _rq.Session.request = fake_req
    _rq.Session.get = lambda self, url, **kw: fake_req(self, "GET", url, **kw)
    out = bt3.call(dj, "beta_request", {"method": "POST", "path": "/forgot-password", "form": {"email": "a@b.c"}})[0]
    _rq.Session.request = real_req
    del _rq.Session.get
    ok("POST /forgot-password → 500" in out and "Server Error" in out and seen_req[-1][2]["_token"] == "tok123"
       and seen_req[-1][3] == betas_mod.beta_auth(beta_d) and seen_req[0][1].startswith(beta_d.url.rstrip("/")),
       "beta_request opens the beta with its password and fills Laravel's CSRF token by itself")
    ok(bt3.call(dj, "beta_request", {"path": "https://evil.example.com/"})[1], "beta_request only opens paths on the beta")
    keep_run3 = remote_mod.run
    logs_seen = []
    remote_mod.run = lambda server, script, timeout=900: (logs_seen.append(script), (0, "--- app log:\n[2026] local.ERROR: boom APP_KEY=secret123"))[1]
    lo = bt3.call(dj, "beta_logs", {})[0]
    remote_mod.run = keep_run3
    ok("boom" in lo and "secret123" not in lo and "storage/logs" in logs_seen[0] and "stderr.log" in logs_seen[0],
       "beta_logs shows the beta's errors with secrets hidden")
    probes = []
    remote_mod.run = lambda server, script, timeout=900: (probes.append(script), (0, "" if "aidash-probe" not in script else "CURL_EXIT=92"))[1]
    pr = bt3.call(dj, "beta_probe", {"method": "POST", "path": "/forgot-password", "http": "2", "form": {"email": "a@b.c"}})[0]
    ok("--http2 -K -" in probes[-1] and 'request = "POST"' in probes[-1] and 'user = "beta:' in probes[-1]
       and "curl exit 92" in pr and "HTTP/2 stream error" in pr, "beta_probe tries a protocol (HTTP/1.1 vs 2) and explains curl's failure")
    ok(bt3.call(dj, "beta_probe", {"path": "/x' ; rm -rf /"})[1], "beta_probe refuses odd paths")
    bt3.call(dj, "beta_access_log", {"filter": "forgot"})
    bt3.call(dj, "beta_webserver", {})
    ok(any("access_log" in sc and "grep -F -- forgot" in sc for sc in probes) and any("vhost.conf" in sc and "(hidden)" in sc for sc in probes)
       and not any("lswsctrl" in sc or "sed -i" in sc for sc in probes), "access log + web-server settings are read only")
    remote_mod.run = keep_run3
    # Claude asks the owner (decision / permission) and waits; the owner answers on a card in the chat
    from app.models import ChangeQuestion
    keep_wait = bt3._wait_answer

    def answered_wait(qid, seconds):
        row = ChangeQuestion.query.get(qid)
        page_q = c.get(f"/projects/{newp.id}?change={ch7.id}").data
        seen_req.append(("page", b"Claude asks for permission" in page_q and b"Approve" in page_q))
        ok(dv.post(f"/changes/{ch7.id}/questions/{qid}/answer", data={"answer": "Approved"}).status_code in (403, 404),
           "only the owner gives permission")
        c.post(f"/changes/{ch7.id}/questions/{qid}/answer", data={"answer": "Approved", "note": "only on beta"})
        return ChangeQuestion.query.get(qid)

    bt3._wait_answer = answered_wait
    n_al = Alert.query.count()
    ans = bt3.call(dj, "ask_owner", {"question": "May I set HTTP/1.1 on the beta's web server?", "permission": True})[0]
    q1 = ChangeQuestion.query.order_by(ChangeQuestion.id.desc()).first()
    ok(("page", True) in seen_req and "answered: Approved — only on beta" in ans and q1.status == "answered" and Alert.query.count() == n_al + 1
       and any(m.body.startswith("Answer to Claude: Approved") for m in ch7.messages),
       "Claude asks for permission: a card in the chat + an alert; the owner's answer goes back to Claude")
    bt3._wait_answer = lambda qid, seconds: ChangeQuestion.query.get(qid)
    ans2 = bt3.call(dj, "ask_owner", {"question": "Which colour?", "options": ["Blue", "Green"]})[0]
    ok("No answer within 30 minutes" in ans2 and ChangeQuestion.query.order_by(ChangeQuestion.id.desc()).first().status == "expired",
       "no answer in time → Claude is told not to do it")
    # a tool outside the allowed list (e.g. a connector) → the owner decides: once / for this run / deny
    def allow_run(qid, seconds):
        c.post(f"/changes/{ch7.id}/questions/{qid}/answer", data={"answer": "Allow for this run"})
        return ChangeQuestion.query.get(qid)

    bt3._wait_answer = allow_run
    import json
    pp = json.loads(bt3.call(dj, "permission_prompt", {"tool_name": "mcp__mobbin__search_screens", "input": {"query": "login"}})[0])
    nq = ChangeQuestion.query.count()
    pp2 = json.loads(bt3.call(dj, "permission_prompt", {"tool_name": "mcp__mobbin__search_screens", "input": {"query": "otp"}})[0])
    ok(pp == {"behavior": "allow", "updatedInput": {"query": "login"}} and pp2["behavior"] == "allow" and ChangeQuestion.query.count() == nq,
       "the owner allows a tool for this run once — Claude isn't asked again")
    bt3._wait_answer = lambda qid, seconds: (c.post(f"/changes/{ch7.id}/questions/{qid}/answer", data={"answer": "Deny"}), ChangeQuestion.query.get(qid))[1]
    pp3 = json.loads(bt3.call(dj, "permission_prompt", {"tool_name": "Bash(rm -rf storage)", "input": {"command": "rm -rf storage"}})[0])
    ok(pp3["behavior"] == "deny", "a denied tool is refused")
    bt3._wait_answer = keep_wait
    r = c.post(f"/changes/{ch7.id}/questions/{q1.id}/answer", data={"answer": "Approved"}, follow_redirects=True)
    ok(b"already answered" in r.data, "a question is answered once")
    # live steps while Claude works, kept on its reply
    ch7.agent_state, ch7.agent_steps = "working", []
    db.session.commit()
    agent_mod._note(ch7, "Reading routes/web.php")
    agent_mod._note(ch7, "Running php artisan test")
    st = c.get(f"/changes/{ch7.id}/status").get_json()
    ok([x["text"] for x in st["steps"]] == ["Reading routes/web.php", "Running php artisan test"] and "open_questions" in st,
       "the chat shows Claude's steps live")
    from app.models import ChangeMessage as _CM
    db.session.add(_CM(change_id=ch7.id, sender="claude", body="Done.", steps=list(ch7.agent_steps)))
    ch7.agent_state = "done"
    db.session.commit()
    ok(b"2 steps" in c.get(f"/projects/{newp.id}?change={ch7.id}").data, "Claude's reply keeps its steps, folded (\"2 steps\")")
    agent_mod._beta_debug_end(dbg)
    db.session.refresh(dj)
    ok(dj.state == "done" and dj.tool_token_hash is None, "the beta tools' key ends with the Claude run")

    # the beta API is opened for the apps (Laravel: own artisan service; pages keep the password, asked once)
    from app.betas import OPEN_API_PY, LOCK_BLOCK, LOCK_MARK
    import subprocess as _sp3
    _ld = _tf2.mkdtemp()
    os.makedirs(f"{_ld}/public")
    open(f"{_ld}/public/index.php", "w").write("<?php")
    open(f"{_ld}/.htaccess", "w").write("RewriteRule ^(.*)$ public/$1 [L]\n")
    open(f"{_ld}/v", "w").write(LOCK_BLOCK.format(mark=LOCK_MARK, host="x.beta.ivinfotech.com") + "\nvhssl  {\n  enableSpdy              15\n}\n")
    r1 = _sp3.run(["python3", "-c", OPEN_API_PY, f"{_ld}/v", f"{_ld}/.htaccess", LOCK_MARK, "laravel", "6107"], capture_output=True, text=True).stdout
    vt = open(f"{_ld}/v").read()
    ok("CHANGED=1" in r1 and "handler                 127.0.0.1:6107" in vt and "$DOC_ROOT/public/$1" in vt and "RewriteRule ^ /index.php [L]" in vt
       and vt.count("realm                   aidashBeta") == 1 and "enableSpdy              0" in vt,
       "Laravel beta: /api/ → its own service, pages from public/ behind the password, HTTP/1.1 only (OLS H2 POST bug)")
    scripts_api = []
    keep_run2 = remote_mod.run

    def api_run(server, script, timeout=900):
        scripts_api.append(script)
        if "grep -q AIDASH-API-OPEN" in script:
            return 0, "LARAVEL=1\nPHP=lsphp84"
        if "[Service]" in script:
            return 0, "PORT=6107\nSTATE=active"
        if "before-open-api" in script:
            return 0, "CHANGED=1"
        if "for V in /usr/local/lsws/conf/vhosts" in script:
            return 0, "SITE eyenosis.com 200"
        return 0, "ok"

    remote_mod.run = api_run
    beta_e = newp.env("beta")
    beta_e.managed = True
    db.session.commit()
    jb = Job(project_id=newp.id, kind="beta_api", state="running", by_user_id=owner.id)
    db.session.add(jb)
    db.session.commit()
    changed = betas_mod.ensure_app_api(jb, beta_e)
    svc = next((sc for sc in scripts_api if "[Service]" in sc), "")
    ok(changed and "artisan serve --host=127.0.0.1 --port=$PORT" in svc and "/usr/local/lsws/lsphp84/bin/php" in svc
       and any("laravel 6107" in sc for sc in scripts_api) and any("lswsctrl restart" in sc for sc in scripts_api),
       "a project with apps: the beta API gets its own service and skips the beta password (pages keep it)")
    scripts_api.clear()
    remote_mod.run = lambda server, script, timeout=900: (scripts_api.append(script), (0, "OPEN=1"))[1]
    ok(betas_mod.ensure_app_api(jb, beta_e) is False and not any("[Service]" in sc for sc in scripts_api), "already open → nothing changes")
    remote_mod.run = keep_run2
    jb.state = "done"
    db.session.commit()

    # owner can't be edited / switched off from the users list
    ok(c.post(f"/users/{owner.id}/access", data={"on": "0"}).status_code == 403, "owner account can't be turned off from the list")

    ok(ActivityLog.query.filter_by(action="Sign in").count() >= 5, "sign-ins are logged")
    ok(ActivityLog.query.filter_by(action="Sign-in locked").count() == 1, "lock is logged")

    # CSRF is on outside tests
    app.config["WTF_CSRF_ENABLED"] = True
    ok(app.test_client().post("/login", data={"login": "x", "password": "y"}).status_code == 400, "POST without CSRF token is refused")

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print(f"OK — {len(PASSED)} checks passed")
