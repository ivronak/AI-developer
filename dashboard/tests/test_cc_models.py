"""Claude Code model + effort picker: the model list file, resolving the chat form, the --model / --effort flags and the
friendly "needs usage credits" error. Plain unittest (pytest runs it too); no database, no real Claude Code — _spawn and the
login are replaced, like the other Claude Code tests."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from flask import Flask

from app import cc_models
from app import claude_code as cc

OPUS = {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5", "description": "For complex work",
        "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]}
SONNET_46 = {"value": "claude-sonnet-4-6", "displayName": "Sonnet 4.6", "description": "Efficient",
             "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "max"]}
HAIKU_45 = {"value": "claude-haiku-4-5-20251001", "displayName": "Haiku 4.5", "description": "Fastest"}
DEFAULT = {"value": "default", "resolvedModel": "claude-sonnet-5-5", "displayName": "Default (recommended)", "description": "Sonnet 5.5",
           "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]}


def stamp(delta=timedelta(0)):
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")



class _FakeAccount:
    id, name, slug, status = 7, "Office", "a7", "ok"


ACC = _FakeAccount()

class Base(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "claude-models.json")
        self.app.config["CLAUDE_MODELS_FILE"] = self.path
        ctx = self.app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        self.addCleanup(self.dir.cleanup)
        settings = {"cc_default_model": "default", "cc_default_effort": ""}
        patcher = mock.patch("app.cc_models.get_setting", side_effect=settings.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = settings

    writes = 0

    def write(self, models, fetched_at=None):
        with open(self.path, "w") as f:
            json.dump({"fetched_at": fetched_at or stamp(), "models": models}, f)
        Base.writes += 1                      # a distinct mtime per write, so load()'s cache never serves an older file
        os.utime(self.path, (1_700_000_000 + Base.writes, 1_700_000_000 + Base.writes))


class CatalogTests(Base):
    def test_reads_the_list_and_keeps_only_model_facts(self):
        self.write([DEFAULT, dict(OPUS, account={"email": "someone@example.com"}), HAIKU_45])
        cat = cc_models.load()
        self.assertTrue(cat["ok"])
        self.assertEqual([m["value"] for m in cat["models"]], ["default", "opus", "claude-haiku-4-5-20251001"])
        self.assertEqual(sorted(cat["models"][1]), ["description", "displayName", "resolvedModel", "supportedEffortLevels", "supportsEffort", "value"])
        self.assertNotIn("someone@example.com", json.dumps(cat))

    def test_drops_entries_that_do_not_look_like_models(self):
        bad = [{"value": "opus 5; rm -rf /", "displayName": "x"}, {"value": "ok-id"}, "text", {"value": "-flag", "displayName": "y"}]
        self.write(bad + [OPUS])
        self.assertEqual([m["value"] for m in cc_models.load()["models"]], ["opus"])

    def test_unknown_effort_levels_are_dropped_and_order_is_fixed(self):
        self.write([dict(OPUS, supportedEffortLevels=["max", "turbo", "low"])])
        self.assertEqual(cc_models.load()["models"][0]["supportedEffortLevels"], ["low", "max"])

    def test_model_without_levels_does_not_support_effort(self):
        self.write([dict(OPUS, supportsEffort=True, supportedEffortLevels=[])])
        self.assertFalse(cc_models.load()["models"][0]["supportsEffort"])

    def test_missing_or_broken_file_is_not_ok(self):
        self.assertFalse(cc_models.load()["ok"])
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertFalse(cc_models.load()["ok"])
        self.write([])
        self.assertFalse(cc_models.load()["ok"])

    def test_stale_when_the_hourly_job_has_stopped(self):
        self.write([OPUS], fetched_at=stamp(timedelta(minutes=50)))
        self.assertFalse(cc_models.load()["stale"])
        self.write([OPUS], fetched_at=stamp(timedelta(hours=5)))
        cat = cc_models.load()
        self.assertTrue(cat["ok"] and cat["stale"])
        self.assertGreaterEqual(cat["age_hours"], 4.9)


class ResolveTests(Base):
    def setUp(self):
        super().setUp()
        self.write([DEFAULT, OPUS, SONNET_46, HAIKU_45])

    def test_keeps_a_supported_choice(self):
        self.assertEqual(cc_models.resolve("opus", "xhigh"), ("opus", "xhigh"))

    def test_effort_the_model_lacks_becomes_default(self):
        self.assertEqual(cc_models.resolve("claude-sonnet-4-6", "xhigh"), ("claude-sonnet-4-6", ""))
        self.assertEqual(cc_models.resolve("claude-haiku-4-5-20251001", "high"), ("claude-haiku-4-5-20251001", ""))

    def test_unknown_model_or_garbage_falls_back_to_the_default_model(self):
        self.assertEqual(cc_models.resolve("gpt-9", "high"), ("default", "high"))
        self.assertEqual(cc_models.resolve(None, None), ("default", ""))
        self.assertEqual(cc_models.resolve("opus; ls", "max; ls"), ("default", ""))

    def test_the_owners_default_is_used_when_the_form_names_nothing_valid(self):
        self.settings["cc_default_model"] = "opus"
        self.assertEqual(cc_models.resolve("nope", ""), ("opus", ""))

    def test_no_list_means_the_old_presets_stay(self):
        os.remove(self.path)
        self.assertEqual(cc_models.resolve("opus", "high"), (None, None))

    def test_default_is_sent_as_the_model_it_stands_for(self):
        # a resumed Claude Code session keeps its last model unless --model is sent, so "Default" must name one
        self.assertEqual(cc_models.launch_value("default"), "claude-sonnet-5-5")
        self.assertEqual(cc_models.launch_value("opus"), "opus")
        self.assertEqual(cc_models.launch_value("claude-sonnet-4-6"), "claude-sonnet-4-6")

    def test_default_without_a_resolved_model_or_list_sends_nothing(self):
        self.write([dict(DEFAULT, resolvedModel="default"), OPUS])
        self.assertIsNone(cc_models.launch_value("default"))
        os.remove(self.path)
        self.assertIsNone(cc_models.launch_value("default"))


class CommandTests(Base):
    def args(self, model, effort=None, **kw):
        return cc.command("do it", model, "sid", False, 10, 1.0, effort=effort, **kw)

    def pair(self, args, flag):
        return args[args.index(flag) + 1] if flag in args else None

    def test_a_model_from_the_list_goes_through_unchanged(self):
        self.assertEqual(self.pair(self.args("fable"), "--model"), "fable")
        self.assertEqual(self.pair(self.args("claude-sonnet-5"), "--model"), "claude-sonnet-5")

    def test_default_model_sends_no_model_flag(self):
        self.assertNotIn("--model", self.args("default"))
        self.assertNotIn("--model", self.args(None))

    def test_old_preset_api_ids_become_aliases(self):
        self.assertEqual(self.pair(self.args("claude-sonnet-5-5"), "--model"), "sonnet")
        self.assertEqual(self.pair(self.args("claude-haiku-5-5"), "--model"), "haiku")
        self.assertEqual(self.pair(self.args("claude-opus-5-5"), "--model"), "opus")

    def test_a_value_that_is_not_a_plain_id_never_reaches_the_command_line(self):
        self.assertEqual(self.pair(self.args("opus; rm -rf /"), "--model"), "sonnet")

    def test_effort_flag_only_for_a_real_level(self):
        self.assertEqual(self.pair(self.args("opus", "xhigh"), "--effort"), "xhigh")
        self.assertNotIn("--effort", self.args("opus"))
        self.assertNotIn("--effort", self.args("opus", ""))
        self.assertNotIn("--effort", self.args("opus", "turbo"))

    def test_shape_other_code_relies_on_is_unchanged(self):
        args = self.args("opus", "high")
        self.assertEqual(args.count("stream-json"), 1)         # Settings → Test Claude Code swaps it for "json"
        self.assertEqual(args[:3], ["claude", "-p", "do it"])
        self.assertEqual(self.pair(args, "--permission-mode"), "acceptEdits")


class FakeProc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.stderr = mock.Mock(read=lambda: "")
        self.wait = mock.Mock()
        self.poll = mock.Mock(return_value=0)
        self.kill = mock.Mock()


CREDITS = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429, "api_error_code": "credits_required",
           "api_error": "model_requires_usage_credits", "result": "Fable 5.1 requires usage credits. Switch to another model to continue."}


class RunTests(Base):
    def run_with(self, result):
        line = json.dumps(result) + "\n"
        with mock.patch.object(cc, "login_for", return_value=cc.Login({}, [], "subscription", ACC)), \
                mock.patch.object(cc, "_spawn", return_value=FakeProc([line])):
            return cc.run("/tmp", "hi", "fable", "sid", False, 5, 1.0, lambda s: None, {})

    def test_usage_credits_error_is_friendly(self):
        with self.assertRaises(cc.CreditsRequired) as ctx:
            self.run_with(CREDITS)
        msg = str(ctx.exception)
        self.assertIn("Fable 5.1", msg)
        self.assertIn("Pick another model", msg)
        self.assertNotIn("429", msg)
        self.assertNotIn("Switch to another model to continue", msg)

    def test_credits_error_is_still_a_ccerror(self):
        with self.assertRaises(cc.CCError):
            self.run_with(CREDITS)

    def test_credits_error_without_a_model_name(self):
        with self.assertRaises(cc.CreditsRequired) as ctx:
            self.run_with(dict(CREDITS, result="Credits are needed for this.", api_error_code="credits_required"))
        self.assertTrue(str(ctx.exception).startswith("This model needs extra usage credits"))

    def test_other_failures_keep_their_own_message(self):
        with self.assertRaises(cc.CCError) as ctx:
            self.run_with({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""})
        self.assertNotIsInstance(ctx.exception, cc.CreditsRequired)
        self.assertIn("step limit", str(ctx.exception))

    def test_a_good_run_still_returns_the_answer(self):
        out = self.run_with({"type": "result", "subtype": "success", "is_error": False, "result": "Done.",
                             "session_id": "s1", "total_cost_usd": 0.01, "num_turns": 2})
        self.assertEqual((out["text"], out["ok"], out["session_id"]), ("Done.", True, "s1"))


class LoginTests(Base):
    """2026-11-04: Claude Code logs in with a web-login Claude account (cc_logins); the setup-token is retired."""

    def login(self, acc, settings=None, run_as=None, token="sk-ant-oat01-abc"):
        from app import cc_logins
        settings = settings or {}
        with mock.patch.object(cc, "get_setting", side_effect=settings.get), \
                mock.patch.object(cc, "decrypt_secret", side_effect=lambda v: v.replace("enc:", "")), \
                mock.patch.object(cc_logins, "default_account", return_value=acc), \
                mock.patch.object(cc_logins, "login_parts", return_value=({}, ["CLAUDE_CONFIG_DIR=/home/r/claude-accounts/a7"])), \
                mock.patch.object(cc_logins, "access_token", return_value=token):
            return cc.login_for(None, run_as)

    def test_the_default_account_is_used_and_passes_its_folder(self):
        lg = self.login(ACC, {"cc_oauth_token_enc": "enc:old", "claude_key_enc": "enc:key"})
        self.assertEqual((lg.env, lg.args, lg.plan, lg.account), ({}, ["CLAUDE_CONFIG_DIR=/home/r/claude-accounts/a7"], "subscription", ACC))

    def test_an_old_saved_token_is_never_used(self):
        lg = self.login(ACC, {"cc_oauth_token_enc": "enc:old"})
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", lg.env)

    def test_no_account_falls_back_to_the_api_key(self):
        lg = self.login(None, {"claude_key_enc": "enc:key"})
        self.assertEqual((lg.env, lg.plan, lg.account), ({"ANTHROPIC_API_KEY": "key"}, "api", None))

    def test_no_account_and_no_key_is_an_error(self):
        with self.assertRaises(cc.CCError):
            self.login(None, {"cc_oauth_token_enc": "enc:old"})

    def test_a_logged_out_account_is_refused_with_a_clear_message(self):
        acc = _FakeAccount()
        acc.status = "logged_out"
        with self.assertRaises(cc.CCError) as ctx:
            self.login(acc)
        self.assertIn("Office", str(ctx.exception))
        self.assertIn("Log in", str(ctx.exception))

    def test_a_beta_websites_user_gets_the_accounts_access_token_not_its_folder(self):
        lg = self.login(ACC, run_as="eyesa9835")
        self.assertEqual((lg.env, lg.args), ({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abc"}, []))

    def test_the_folder_goes_into_the_sudo_wrapper(self):
        self.app.config["TEST_RUNNER_USER"] = "aided-runner"
        args = cc.command("do it", "sonnet", "sid", False, 5, 1.0, login_args=["CLAUDE_CONFIG_DIR=/x/a7"])
        i = args.index("/usr/bin/env")
        self.assertEqual(args[i + 1: i + 3], ["PATH=/usr/local/bin:/usr/bin:/bin", "CLAUDE_CONFIG_DIR=/x/a7"])

    def test_tool_runs_get_web_tools_and_the_accounts_connectors(self):
        args = cc.command("fix", "sonnet", "sid", False, 5, 1.0, mcp_config="/tmp/m.json", tools=["mcp__beta"])
        allowed = args[args.index("--allowedTools") + 1: args.index("--disallowedTools")]
        denied = args[args.index("--disallowedTools") + 1: args.index("--mcp-config")]
        self.assertEqual(allowed, ["mcp__beta", "WebSearch", "WebFetch"])
        self.assertNotIn("WebSearch", denied)
        self.assertIn("Edit", denied)
        self.assertNotIn("--strict-mcp-config", args)

    def run_failing(self, result, auth):
        with mock.patch.object(cc, "login_for", return_value=auth), \
                mock.patch.object(cc, "_spawn", return_value=FakeProc([json.dumps(dict(type="result", is_error=True, **result)) + "\n"])):
            return cc.run("/tmp", "hi", "sonnet", "sid", False, 5, 1.0, lambda s: None, {})

    def test_a_401_on_an_account_says_which_account_to_log_in(self):
        with self.assertRaises(cc.CCError) as ctx:
            self.run_failing({"subtype": "success", "api_error_status": 401, "result": "Authentication failed"},
                             cc.Login({}, [], "subscription", ACC))
        self.assertIn("“Office” is logged out", str(ctx.exception))

    def test_a_401_with_the_api_key_keeps_claude_codes_own_message(self):
        with self.assertRaises(cc.CCError) as ctx:
            self.run_failing({"subtype": "success", "api_error_status": 401, "result": "Invalid API key · Please run /login"},
                             cc.Login({"ANTHROPIC_API_KEY": "x"}, [], "api", None))
        self.assertNotIn("logged out", str(ctx.exception))
        self.assertIn("Invalid API key", str(ctx.exception))

    def test_credits_error_wins_over_the_login_message(self):
        with self.assertRaises(cc.CreditsRequired):
            self.run_failing({k: v for k, v in CREDITS.items() if k not in ("type", "is_error")}, cc.Login({}, [], "subscription", ACC))

    def test_web_tools_are_not_pre_allowed_on_a_guarded_project(self):
        def allowed(no_web):
            args = cc.command("do it", "sonnet", "sid", False, 5, 1.0, no_web=no_web)
            return args[args.index("--allowedTools") + 1: args.index("--max-turns")]
        self.assertIn("WebFetch", allowed(False))
        self.assertIn("WebSearch", allowed(False))
        with_login = allowed(True)
        self.assertNotIn("WebFetch", with_login)
        self.assertNotIn("WebSearch", with_login)
        self.assertIn("Bash", with_login)

    def test_the_file_tools_may_not_read_the_login_files(self):
        args = cc.command("do it", "sonnet", "sid", False, 5, 1.0)
        denied = args[args.index("--disallowedTools") + 1:]
        self.assertIn("Read(~/.claude/.credentials.json)", denied)
        self.assertIn("Read(~/.claude.json)", denied)
        self.assertIn("Read(~/claude-accounts/**)", denied)
        self.assertIn("Read", args[args.index("--allowedTools") + 1: args.index("--disallowedTools")])     # plain Read stays allowed

    def test_the_rules_forbid_touching_the_claude_login(self):
        self.assertIn(".credentials.json", cc.RULES)
        self.assertIn("Never run `claude`", cc.RULES)

    def test_expired_server_login_is_friendly(self):
        with mock.patch.object(cc, "login_for", return_value=cc.Login({}, [], "subscription", ACC)), \
                mock.patch.object(cc, "_spawn", return_value=FakeProc([json.dumps(
                    {"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"}) + "\n"])):
            with self.assertRaises(cc.CCError) as ctx:
                cc.run("/tmp", "hi", "sonnet", "sid", False, 5, 1.0, lambda s: None, {})
        self.assertIn("“Office” is logged out", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, cc.CreditsRequired)

    def test_a_normal_failure_mentioning_login_is_not_taken_for_an_expired_login(self):
        with mock.patch.object(cc, "login_for", return_value=cc.Login({}, [], "subscription", ACC)), \
                mock.patch.object(cc, "_spawn", return_value=FakeProc([json.dumps(
                    {"type": "result", "subtype": "error_during_execution", "is_error": True,
                     "result": "I could not finish the /login page change."}) + "\n"])):
            with self.assertRaises(cc.CCError) as ctx:
                cc.run("/tmp", "hi", "sonnet", "sid", False, 5, 1.0, lambda s: None, {})
        self.assertNotIn("logged out", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
