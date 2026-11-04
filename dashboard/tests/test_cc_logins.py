"""Claude accounts with a web login (app/cc_logins.py): the login link we show, the code we accept, the folder names,
the sudo wrapper and the access token handed to a beta website's run. Plain unittest; no database, no real Claude."""
import time
import unittest
from unittest import mock

from flask import Flask

from app import cc_logins
from app.claude_code import CCError


class Acc:
    def __init__(self, slug="a3", name="Office"):
        self.id, self.slug, self.name, self.status = 3, slug, name, "ok"


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


class Base(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TEST_RUNNER_USER"] = "aided-runner"
        ctx = self.app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        p = mock.patch.object(cc_logins, "_home", return_value="/var/lib/aided-runner")
        p.start()
        self.addCleanup(p.stop)


class LinkAndCodeTests(Base):
    def test_only_a_claude_login_link_is_shown(self):
        url = "https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=y"
        self.assertEqual(cc_logins._clean_url(f"If the browser didn't open, visit: {url}"), url)
        self.assertEqual(cc_logins._clean_url("visit: https://platform.claude.com/oauth/x"), "https://platform.claude.com/oauth/x")
        for bad in ("visit: https://evil.example.com/claude.com", "visit: https://claude.com.evil.io/x", "visit: http://claude.com/x",
                    "visit: https://notclaude.ai/x", ""):
            self.assertEqual(cc_logins._clean_url(bad), "", bad)

    def test_the_code_must_look_like_one(self):
        self.assertTrue(cc_logins.CODE_RE.match("AbC-123_x.y~z#state-456"))
        for bad in ("", "short", "has space inside", "a;rm -rf /", "x" * 601, "line\nbreak"):
            self.assertFalse(cc_logins.CODE_RE.match(bad), bad)

    def test_names(self):
        self.assertTrue(cc_logins.NAME_RE.match("Office Max plan"))
        for bad in ("", "x" * 61, "<b>", "tab\there"):
            self.assertFalse(cc_logins.NAME_RE.match(bad), bad)


class FolderTests(Base):
    def test_each_account_has_its_own_folder_and_the_default_login_none(self):
        self.assertEqual(cc_logins.config_dir(Acc("a3")), "/var/lib/aided-runner/claude-accounts/a3")
        self.assertIsNone(cc_logins.config_dir(Acc("")))
        self.assertEqual(cc_logins.creds_file(Acc("")), "/var/lib/aided-runner/.claude/.credentials.json")
        self.assertEqual(cc_logins.creds_file(Acc("a3")), "/var/lib/aided-runner/claude-accounts/a3/.credentials.json")

    def test_a_bad_folder_name_is_refused(self):
        for bad in ("../x", "a3/../../root", "b3", "a", "a1234567"):
            with self.assertRaises(cc_logins.LoginError, msg=bad):
                cc_logins.config_dir(Acc(bad))

    def test_the_folder_goes_as_an_env_argument_inside_sudo(self):
        args, env = cc_logins.cli(Acc("a3"), ["auth", "status"])
        self.assertEqual(args[:6], ["sudo", "-n", "-H", "-u", "aided-runner", "--"])
        self.assertIn("CLAUDE_CONFIG_DIR=/var/lib/aided-runner/claude-accounts/a3", args)
        self.assertEqual(args[-3:], ["claude", "auth", "status"])
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        args, _ = cc_logins.cli(Acc(""), ["auth", "status"])
        self.assertFalse([a for a in args if a.startswith("CLAUDE_CONFIG_DIR=")])

    def test_without_a_runner_user_the_folder_goes_in_the_environment(self):
        self.app.config["TEST_RUNNER_USER"] = ""
        args, env = cc_logins.cli(Acc("a3"), ["auth", "status"])
        self.assertEqual(args, ["claude", "auth", "status"])
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].endswith("/claude-accounts/a3"))

    def test_logout_only_ever_deletes_an_account_folder(self):
        calls = []
        with mock.patch.object(cc_logins, "_run", side_effect=lambda a, **k: calls.append(a) or R()):
            cc_logins.logout(Acc("a3"))
            cc_logins.logout(Acc(""))
        rm = [a for a in calls if a[6:8] == ["/bin/sh", "-c"]]
        self.assertEqual(len(rm), 1)                                   # the default login is only signed out
        self.assertIn('*/claude-accounts/a[0-9]*) rm -rf', rm[0][8])     # the script itself refuses any other path
        self.assertEqual(rm[0][-1], "/var/lib/aided-runner/claude-accounts/a3")


class LoginStartTests(Base):
    def test_no_link_means_a_clear_error_and_the_process_is_stopped(self):
        proc = mock.Mock(stdout=iter(["Error: something went wrong\n"]), poll=mock.Mock(return_value=None))
        with mock.patch.object(cc_logins, "_popen", return_value=proc), mock.patch.object(cc_logins, "_run", return_value=R()):
            with self.assertRaises(cc_logins.LoginError) as ctx:
                cc_logins.start_login(Acc(), wait=2)
        self.assertIn("something went wrong", str(ctx.exception))
        proc.kill.assert_called()
        self.assertEqual(cc_logins.pending(3), "")

    def test_a_link_is_kept_until_the_code_comes(self):
        proc = mock.Mock(stdout=iter(["visit: https://claude.com/cai/oauth/authorize?x=1\n"]), poll=mock.Mock(return_value=None))
        with mock.patch.object(cc_logins, "_popen", return_value=proc) as po, mock.patch.object(cc_logins, "_run", return_value=R()):
            url = cc_logins.start_login(Acc(), wait=2)
        self.assertEqual(url, "https://claude.com/cai/oauth/authorize?x=1")
        self.assertEqual(cc_logins.pending(3), url)
        self.assertEqual(po.call_args.kwargs["stdin"], cc_logins.subprocess.PIPE)
        cc_logins.cancel_login(3)
        self.assertEqual(cc_logins.pending(3), "")


class AccessTokenTests(Base):
    def test_a_fresh_token_is_handed_over(self):
        out = f"{int((time.time() + 3 * 3600) * 1000)}\nsk-ant-oat01-fresh\n"
        with mock.patch.object(cc_logins, "_run", return_value=R(out)), mock.patch.object(cc_logins, "keep_alive") as ka:
            self.assertEqual(cc_logins.access_token(Acc()), "sk-ant-oat01-fresh")
        ka.assert_not_called()

    def test_an_old_token_is_renewed_first(self):
        outs = [R(f"{int((time.time() + 60) * 1000)}\nsk-ant-oat01-old\n"), R(f"{int((time.time() + 8 * 3600) * 1000)}\nsk-ant-oat01-new\n")]
        with mock.patch.object(cc_logins, "_run", side_effect=lambda *a, **k: outs.pop(0)), mock.patch.object(cc_logins, "keep_alive") as ka:
            self.assertEqual(cc_logins.access_token(Acc()), "sk-ant-oat01-new")
        ka.assert_called_once()

    def test_no_usable_token_is_a_clear_error(self):
        for out in ("", "123\nnot-a-token\n", f"{int((time.time() + 60) * 1000)}\nsk-ant-oat01-old\n"):
            with mock.patch.object(cc_logins, "_run", return_value=R(out)), mock.patch.object(cc_logins, "keep_alive"):
                with self.assertRaises(CCError, msg=out):
                    cc_logins.access_token(Acc())

    def test_the_script_prints_only_expiry_and_token_never_the_refresh_token(self):
        calls = []
        with mock.patch.object(cc_logins, "_run", side_effect=lambda a, **k: calls.append(a) or R("")), mock.patch.object(cc_logins, "keep_alive"):
            with self.assertRaises(CCError):
                cc_logins.access_token(Acc())
        self.assertNotIn("refresh", calls[0][8].lower())


if __name__ == "__main__":
    unittest.main()
