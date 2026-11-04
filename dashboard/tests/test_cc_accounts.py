"""Claude accounts page: the can / asks / can't matrix, what the page builds for each Claude (web accounts, VPS, API), the parsing of `claude mcp list`,
and the VPS Claude facts that the hourly root job writes (deploy/aidash-claude-models). Plain unittest; no database, no real Claude."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

from flask import Flask

from app import cc_accounts, cc_models

MCP_OUT = """Checking MCP server health…

claude.ai Claude Docs: https://api.anthropic.com/v1/pages/mcp - ✔ Connected
claude.ai Supabase: https://mcp.supabase.com/mcp - ! Needs authentication
claude.ai Invoice Bot n8n: https://example.up.railway.app/mcp-server/http - ✘ Failed to connect — HTTP 404: Error POSTing to endpoint: {"type":"error",
claude.ai Mobbin: https://api.mobbin.com/mcp - ✔ Connected
context7: npx -y @upstash/context7-mcp - ✔ Connected
sentry: https://mcp.sentry.dev/mcp (HTTP) - ! Needs authentication
plugin:github:github: https://api.githubcopilot.com/mcp/ - ✘ Failed: authentication_error

MCP config diagnostics ✘
[Failed to parse] Project config (shared via .mcp.json)
"""

EXPECTED = [{"name": "Claude Docs", "status": "connected"}, {"name": "Supabase", "status": "needs-auth"},
            {"name": "Invoice Bot n8n", "status": "failed"}, {"name": "Mobbin", "status": "connected"},
            {"name": "context7", "status": "connected"}, {"name": "sentry", "status": "needs-auth"},
            {"name": "plugin:github:github", "status": "failed"}]


class ParsingTests(unittest.TestCase):
    def test_mcp_list_becomes_names_and_statuses_only(self):
        got = cc_accounts.parse_mcp(MCP_OUT)
        self.assertEqual(got, EXPECTED)
        self.assertNotIn("http", json.dumps(got))

    def test_the_status_comes_from_the_mark_not_from_the_words(self):
        # a failed connector whose error text says "authentication" is red, not amber
        got = cc_accounts.parse_mcp("x: https://x - ✘ Failed: authentication_error")
        self.assertEqual(got, [{"name": "x", "status": "failed"}])

    def test_nothing_listed_is_an_empty_list(self):
        self.assertEqual(cc_accounts.parse_mcp("No MCP servers configured. Use `claude mcp add` to add a server."), [])
        self.assertEqual(cc_accounts.parse_mcp(""), [])

    def test_email_is_masked(self):
        self.assertEqual(cc_accounts.mask_email("jane.doe@example.com"), "j***@example.com")
        self.assertEqual(cc_accounts.mask_email("ab@x.com"), "***@x.com")          # a very short name shows no letter at all
        self.assertEqual(cc_accounts.mask_email("nope"), "")
        self.assertEqual(cc_accounts.mask_email(None), "")


class MatrixTests(unittest.TestCase):
    def test_every_row_has_one_cell_per_claude(self):
        for label, cells in cc_accounts.CAPABILITIES:
            self.assertEqual(len(cells), len(cc_accounts.ACCOUNT_KEYS), label)
            for state, note in cells:
                self.assertIn(state, (cc_accounts.YES, cc_accounts.ASK, cc_accounts.NO), label)

    def cell(self, label_start, key):
        row = next(c for l, c in cc_accounts.CAPABILITIES if l.startswith(label_start))
        return row[cc_accounts.ACCOUNT_KEYS.index(key)]

    def test_the_facts_the_owner_relies_on(self):
        # web-login accounts: full power on the owner's own projects, a card on projects with a client's / developer's text
        self.assertEqual(self.cell("Use your claude.ai connectors", "web")[0], cc_accounts.YES)
        self.assertIn("asks you first", self.cell("Use your claude.ai connectors", "web")[1])
        self.assertEqual(self.cell("Search the web", "web")[0], cc_accounts.YES)
        self.assertIn("asks you first", self.cell("Search the web", "web")[1])
        self.assertEqual(self.cell("Use your claude.ai connectors", "vps_claude")[0], cc_accounts.YES)
        self.assertEqual(self.cell("Run a command on the LIVE site", "web")[0], cc_accounts.ASK)
        self.assertEqual(self.cell("Run commands on the BETA site", "web")[0], cc_accounts.YES)
        for key in ("web", "api"):
            self.assertEqual(self.cell("Act as root", key)[0], cc_accounts.NO, key)
            self.assertEqual(self.cell("Go Live", key)[0], cc_accounts.NO, key)
            self.assertEqual(self.cell("Read the dashboard's database", key)[0], cc_accounts.NO, key)
        self.assertEqual(self.cell("Act as root", "vps_claude")[0], cc_accounts.YES)
        self.assertEqual(self.cell("Run ANY command as root", "web")[0], cc_accounts.ASK)
        self.assertIn("authenticator code", self.cell("Run ANY command as root", "web")[1])
        self.assertEqual(self.cell("Run ANY command as root", "vps_claude")[0], cc_accounts.YES)
        self.assertEqual(self.cell("Run ANY command as root", "api")[0], cc_accounts.NO)
        self.assertEqual(self.cell("Use or copy your Claude account", "web")[0], cc_accounts.YES)

    def test_the_caveats_the_review_found_are_on_the_page(self):
        # every account's login file is readable by every run as that user
        self.assertIn("EVERY run as that user", self.cell("Use or copy your Claude account", "web")[1])
        self.assertIn("access token", self.cell("Use or copy your Claude account", "web")[1])
        self.assertIn("without Go Live's snapshot", self.cell("Go Live", "web")[1])
        self.assertIn("Not directly", self.cell("Act as root", "web")[1])
        self.assertIn("other projects' copies", self.cell("Read and change", "web")[1])
        self.assertIn("copies your live data", self.cell("Put a change on beta", "web")[1])
        self.assertFalse([n for _, cells in cc_accounts.CAPABILITIES for _, n in cells if "25" in n])
        self.assertFalse([n for _, cells in cc_accounts.CAPABILITIES for _, n in cells if "token" in n.lower() and "access token" not in n])


class _Acc:
    def __init__(self, **kw):
        self.id, self.name, self.slug, self.is_default, self.status = 1, "Office", "a1", True, "ok"
        self.plan, self.email_hint, self.models, self.connectors = "max", "o***@x.com", [{"value": "opus"}], [{"name": "Mobbin", "status": "connected"}]
        self.expires_at, self.checked_at, self.last_error = None, None, ""
        self.__dict__.update(kw)


class BuildTests(unittest.TestCase):
    def setUp(self):
        from app import cc_logins
        self.app = Flask(__name__)
        ctx = self.app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        self.settings = {"claude_key_enc": "enc:key"}
        p = mock.patch.object(cc_accounts, "get_setting", side_effect=lambda k: self.settings.get(k))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(cc_accounts, "decrypt_secret", side_effect=lambda v: v.replace("enc:", ""))
        p.start()
        self.addCleanup(p.stop)
        self.accs = [_Acc(), _Acc(id=2, name="Spare", slug="a2", is_default=False, status="logged_out")]
        p = mock.patch.object(cc_logins, "accounts", side_effect=lambda: self.accs)
        p.start()
        self.addCleanup(p.stop)
        self.vps = {"plan": "Claude Pro", "account_hint": "a***@x.com", "remote_control": "active",
                    "connectors": [{"name": "Mobbin", "status": "connected"}]}
        self.stale = False
        p = mock.patch.object(cc_models, "load", side_effect=lambda: {"vps": self.vps, "stale": self.stale, "fetched_at": "2026-10-23T06:00:00Z"})
        p.start()
        self.addCleanup(p.stop)

    def get(self, key, **kw):
        data = cc_accounts.build(**kw)
        return next(a for a in data["accounts"] if a["key"] == key)

    def test_one_card_per_account_then_vps_then_api(self):
        self.assertEqual([a["key"] for a in cc_accounts.build()["accounts"]], ["acc1", "acc2", "vps_claude", "api"])

    def test_account_cards(self):
        a = self.get("acc1")
        self.assertEqual(a["status"], ("Logged in", "ok"))
        self.assertEqual(a["title"], "Office")
        self.assertIn(("Plan", "Max"), a["facts"])
        self.assertTrue(a["in_use"])
        self.assertEqual(a["connectors"], [{"name": "Mobbin", "status": "connected"}])
        b = self.get("acc2")
        self.assertEqual(b["status"], ("Logged out", "bad"))
        self.assertFalse(b["in_use"])

    def test_no_account_yet(self):
        self.accs = []
        a = self.get("web")
        self.assertEqual(a["status"], ("Not added", "bad"))
        self.assertIn("every Claude Code run while no Claude account is added", dict(self.get("api")["facts"])["Used for"])

    def test_vps_claude_card(self):
        a = self.get("vps_claude")
        self.assertEqual(a["status"], ("Service running", "ok"))
        self.assertIn(("Plan", "Claude Pro"), a["facts"])
        self.assertIn(("Reported", "2026-10-23T06:00:00Z"), a["facts"])
        self.assertIsNone(a["in_use"])
        self.vps["remote_control"] = "inactive"
        self.assertEqual(self.get("vps_claude")["status"], ("Service stopped", "warn"))
        self.vps = None
        self.assertEqual(self.get("vps_claude")["status"], ("Unknown", "muted"))

    def test_an_old_report_is_not_shown_as_current(self):
        self.stale = True
        self.assertEqual(self.get("vps_claude")["status"], ("Unknown — report is out of date", "muted"))

    def test_api_card_counts_projects_and_cost(self):
        a = self.get("api", month_cost=1.5, api_projects=3)
        self.assertEqual(a["status"], ("Saved", "ok"))
        self.assertIn(("Used for", "3 projects set to “Claude API”"), a["facts"])
        self.assertIn(("Spent this month", "$1.50"), a["facts"])

    def test_capability_groups_are_ready_for_the_page(self):
        caps = self.get("acc1")["caps"]
        self.assertEqual(sorted(caps), ["ask", "no", "yes"])
        self.assertTrue(any("connectors" in i["label"] for i in caps["yes"]))
        self.assertTrue(any("Act as root" in i["label"] for i in caps["no"]))


class VpsFactsTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "claude-models.json")
        self.app.config["CLAUDE_MODELS_FILE"] = self.path
        ctx = self.app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(cc_models, "get_setting", return_value="")
        p.start()
        self.addCleanup(p.stop)

    def write(self, doc):
        with open(self.path, "w") as f:
            json.dump(doc, f)
        os.utime(self.path, (1_800_000_000, 1_800_000_000 + len(json.dumps(doc))))

    MODEL = {"value": "opus", "displayName": "Opus"}

    def test_the_vps_facts_are_read_and_cleaned(self):
        self.write({"fetched_at": "2026-10-23T05:59:06Z", "models": [self.MODEL], "vps_claude": {
            "plan": "Claude Pro", "account_hint": "s***@x.com", "remote_control": "active", "email": "full@x.com",
            "connectors": [{"name": "Mobbin", "status": "connected", "url": "https://x"}, {"name": "Bad", "status": "weird"}, "junk"]}})
        vps = cc_models.load()["vps"]
        self.assertEqual(vps["plan"], "Claude Pro")
        self.assertEqual(vps["connectors"], [{"name": "Mobbin", "status": "connected"}])
        self.assertEqual(vps["remote_control"], "active")
        self.assertNotIn("full@x.com", json.dumps(vps))

    def test_an_old_file_without_vps_facts_still_loads(self):
        self.write({"fetched_at": "2026-10-23T05:59:06Z", "models": [self.MODEL]})
        cat = cc_models.load()
        self.assertTrue(cat["ok"])
        self.assertIsNone(cat["vps"])


@unittest.skipUnless(hasattr(os, "getuid"), "the root job is a Unix script")
class RootJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "aidash-claude-models")
        loader = importlib.machinery.SourceFileLoader("aidash_claude_models", path)
        spec = importlib.util.spec_from_loader("aidash_claude_models", loader)
        cls.job = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.job)

    def fake_systemctl(self, load_state="loaded", active="active"):
        def run(args, **kw):
            if args[:2] == ["systemctl", "show"]:
                return mock.Mock(stdout=load_state + "\n", returncode=0)
            if args[:2] == ["systemctl", "is-active"]:
                return mock.Mock(stdout=active + "\n", returncode=0)
            return mock.Mock(stdout=MCP_OUT, returncode=0)
        return run

    def test_it_parses_like_the_dashboard_does(self):
        self.assertEqual(self.job.parse_mcp(MCP_OUT), cc_accounts.parse_mcp(MCP_OUT))
        self.assertEqual(self.job.parse_mcp(MCP_OUT), EXPECTED)
        for email in ("jane@example.com", "ab@x.com", "x@y.z", "nope", None):
            self.assertEqual(self.job.mask_email(email), cc_accounts.mask_email(email), email)

    def test_vps_claude_facts_hold_no_full_email_and_no_addresses(self):
        with mock.patch.object(self.job.subprocess, "run", side_effect=self.fake_systemctl()):
            info = self.job.vps_claude({"subscriptionType": "Claude Pro", "email": "jane@example.com", "organization": "Org"}, {}, "/tmp")
        self.assertEqual(info["plan"], "Claude Pro")
        self.assertEqual(info["account_hint"], "j***@example.com")
        self.assertEqual(info["remote_control"], "active")
        self.assertEqual(len(info["connectors"]), len(EXPECTED))
        self.assertNotIn("jane@example.com", json.dumps(info))
        self.assertNotIn("Org", json.dumps(info))
        self.assertNotIn("http", json.dumps(info))

    def test_the_service_state_is_only_known_for_a_unit_that_exists(self):
        with mock.patch.object(self.job.subprocess, "run", side_effect=self.fake_systemctl(load_state="not-found", active="inactive")):
            self.assertEqual(self.job.vps_claude({}, {}, "/tmp")["remote_control"], "unknown")
        with mock.patch.object(self.job.subprocess, "run", side_effect=self.fake_systemctl(active="failed")):
            self.assertEqual(self.job.vps_claude({}, {}, "/tmp")["remote_control"], "inactive")
        with mock.patch.object(self.job.subprocess, "run", side_effect=self.fake_systemctl(active="activating")):
            self.assertEqual(self.job.vps_claude({}, {}, "/tmp")["remote_control"], "unknown")

    def test_a_failing_probe_never_raises_and_leaves_the_facts_unknown(self):
        import subprocess as sp
        for boom in (OSError("no claude"), sp.TimeoutExpired("claude", 30), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")):
            with mock.patch.object(self.job.subprocess, "run", side_effect=boom):
                info = self.job.vps_claude({"subscriptionType": "Claude Pro"}, {}, "/tmp")
            self.assertIsNone(info["connectors"])
            self.assertEqual(info["remote_control"], "unknown")
            self.assertEqual(info["plan"], "Claude Pro")

    def test_the_whole_job_stays_inside_the_unit_timeout(self):
        # claude -p (60) + mcp list (30) + two systemctl calls (10 + 10) must fit in TimeoutStartSec
        unit = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "ai-dashboard-models.service")).read()
        limit = int(next(l for l in unit.splitlines() if l.startswith("TimeoutStartSec=")).split("=")[1])
        self.assertGreater(limit, 60 + 30 + 10 + 10)


if __name__ == "__main__":
    unittest.main()
