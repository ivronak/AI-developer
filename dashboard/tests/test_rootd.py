"""Root commands: the authenticator-code logic of the root service (deploy/aidash-rootd), its socket, and the dashboard's client (app/rootd.py).
Plain unittest; no database. The service logic runs with a fake clock and a fake command runner, so nothing here runs a root command."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from flask import Flask

from app import rootd

DEPLOY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy")


def _load():
    loader = importlib.machinery.SourceFileLoader("aidash_rootd", os.path.join(DEPLOY, "aidash-rootd"))
    spec = importlib.util.spec_from_loader("aidash_rootd", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


daemon = _load()
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"          # the RFC 6238 test key "12345678901234567890"
OTHER_SECRET = "MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43UOV3HJ5LS"
CMD = "systemctl restart ai-dashboard"
SHA = rootd.cmd_sha(CMD)
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def code_at(t, secret=SECRET):
    return daemon.totp(secret, int(t // 30))


class TotpTests(unittest.TestCase):
    def test_rfc6238_vectors(self):
        # the RFC's SHA-1 table, last six of its eight digits
        for t, expect in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"),
                          (2000000000, "279037"), (20000000000, "353130")):
            self.assertEqual(daemon.totp(SECRET, t // 30), expect, t)

    def test_a_new_secret_is_long_random_base32_that_works(self):
        a, b = daemon.new_secret(), daemon.new_secret()
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 32)
        self.assertRegex(a, r"^[A-Z2-7]+$")
        self.assertRegex(daemon.totp(a, 12345), r"^[0-9]{6}$")

    def test_the_command_hash_is_sha256_of_the_text(self):
        self.assertEqual(daemon.cmd_sha("ls"), rootd.cmd_sha("ls"))
        self.assertEqual(len(daemon.cmd_sha("ls")), 64)


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.totp_file = os.path.join(self.dir, "totp.json")
        self.state_file = os.path.join(self.dir, "state", "state.json")
        self.log_file = os.path.join(self.dir, "rootd.log")
        self.write_secret(SECRET)
        self.clock = Clock()
        self.ran = []
        self.svc = self.make()

    def write_secret(self, secret):
        with open(self.totp_file, "w") as f:
            json.dump({"secret": secret}, f)

    def make(self, runner=None):
        def default_runner(cmd, timeout):
            self.ran.append((cmd, timeout))
            return 0, "done\n"
        return daemon.Service(totp_file=self.totp_file, state_file=self.state_file, log_file=self.log_file, clock=self.clock, runner=runner or default_runner)

    def authorize(self, code=None, sha=SHA, by="Owner"):
        return self.svc.handle({"op": "authorize", "code": code_at(self.clock.t) if code is None else code, "cmd_sha": sha, "by": by}, "uid 1")

    def execute(self, ticket, cmd=CMD, **kw):
        return self.svc.handle({"op": "exec", "ticket": ticket, "cmd": cmd, **kw}, "uid 1")

    def wrong(self, n=1):
        out = None
        for _ in range(n):
            out = self.authorize("000000")
        return out

    def log(self):
        with open(self.log_file) as f:
            return f.read()


class AuthorizeTests(ServiceCase):
    def test_a_good_code_gives_a_ticket_that_runs_exactly_that_command_once(self):
        r = self.authorize()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["valid_seconds"], 120)
        out = self.execute(r["ticket"])
        self.assertEqual((out["ok"], out["exit"], out["output"]), (True, 0, "done\n"))
        self.assertEqual(self.ran, [(CMD, 600)])
        again = self.execute(r["ticket"])
        self.assertFalse(again["ok"])
        self.assertEqual(len(self.ran), 1)

    def test_a_ticket_is_for_one_command_and_a_wrong_command_burns_it(self):
        r = self.authorize()
        bad = self.execute(r["ticket"], cmd=CMD + " && rm -rf /srv")
        self.assertFalse(bad["ok"])
        self.assertIn("not the command", bad["error"])
        self.assertFalse(self.execute(r["ticket"])["ok"])          # the right command no longer works with it either
        self.assertEqual(self.ran, [])

    def test_an_unknown_or_made_up_ticket_runs_nothing(self):
        for t in ("0" * 32, "", None, 7, ["x"]):
            self.assertFalse(self.execute(t)["ok"], t)
        self.assertEqual(self.ran, [])

    def test_a_ticket_expires_after_two_minutes(self):
        r = self.authorize()
        self.clock.t += 121
        self.assertFalse(self.execute(r["ticket"])["ok"])
        self.assertEqual(self.ran, [])

    def test_a_code_works_once_even_for_another_command(self):
        code = code_at(self.clock.t)
        self.assertTrue(self.authorize(code)["ok"])
        r = self.authorize(code, sha=rootd.cmd_sha("ls /"))
        self.assertFalse(r["ok"])
        self.assertIn("already used", r["error"])

    def test_using_a_right_code_twice_is_a_double_click_not_a_guess(self):
        code = code_at(self.clock.t)
        self.assertTrue(self.authorize(code)["ok"])
        for _ in range(30):
            self.assertFalse(self.authorize(code)["ok"])
        st = self.svc.handle({"op": "status"})
        self.assertFalse(st["locked"])                             # 30 repeats of a used code never lock the owner out
        self.clock.t += 30
        self.assertTrue(self.authorize()["ok"])

    def test_an_older_code_than_the_last_used_one_is_refused(self):
        self.clock.t += 30
        self.assertTrue(self.authorize()["ok"])
        self.assertFalse(self.authorize(code_at(self.clock.t - 30))["ok"])

    def test_one_step_of_clock_drift_is_allowed_either_way(self):
        self.assertTrue(self.authorize(code_at(self.clock.t - 30))["ok"])
        self.clock.t += 60
        self.assertTrue(self.authorize(code_at(self.clock.t + 30))["ok"])

    def test_a_code_two_steps_off_is_refused(self):
        self.assertFalse(self.authorize(code_at(self.clock.t - 60))["ok"])
        self.assertFalse(self.authorize(code_at(self.clock.t + 60))["ok"])

    def test_a_restart_does_not_forget_the_last_used_code(self):
        code = code_at(self.clock.t)
        self.assertTrue(self.authorize(code)["ok"])
        self.svc = self.make()
        self.assertFalse(self.authorize(code)["ok"])

    def test_a_code_used_by_another_process_since_start_is_refused_too(self):
        # `aidash-rootd enroll` records the confirmation code in the state file while the service is already running
        code = code_at(self.clock.t)
        other = self.make()
        other.last_step = int(self.clock.t // 30)
        other._save_state()
        self.assertFalse(self.authorize(code)["ok"])

    def test_not_enrolled_means_no_root_commands(self):
        os.unlink(self.totp_file)
        r = self.authorize()
        self.assertFalse(r["ok"])
        self.assertIn("enroll", r["error"])
        self.assertFalse(self.svc.handle({"op": "status"})["enrolled"])

    def test_malformed_codes_and_hashes_are_refused_without_counting_as_guesses(self):
        for code, sha in (("12345", SHA), ("1234567", SHA), ("abcdef", SHA), ("", SHA), (code_at(T0), "short"), (code_at(T0), ""),
                          ("١٢٣٤٥٦", SHA), (code_at(T0), "G" * 64), (code_at(T0), SHA.upper()), (123456, SHA), (code_at(T0), None)):
            r = self.authorize(code, sha)
            self.assertFalse(r["ok"], (code, sha))
            self.assertIn("6-digit", r["error"], (code, sha))        # a clean answer, not an "internal error"
        self.assertTrue(self.authorize()["ok"])                    # nothing was locked or used up
        self.assertNotIn('"event": "error"', self.log())

    def test_five_wrong_codes_lock_it_for_fifteen_minutes(self):
        r = self.wrong(5)
        self.assertTrue(r["locked"])
        st = self.svc.handle({"op": "status"})
        self.assertTrue(st["locked"])
        self.assertGreater(st["retry_after"], 14 * 60)
        r = self.authorize()                                       # even the right code is refused while locked
        self.assertFalse(r["ok"])
        self.assertTrue(r["locked"])
        self.clock.t += 15 * 60 + 31
        self.assertTrue(self.authorize()["ok"])

    def test_each_new_lock_is_longer(self):
        seconds = []
        for _ in range(5):
            self.wrong(5)
            seconds.append(round(self.svc.locked_until - self.clock.t))
            self.clock.t = self.svc.locked_until + 1
        self.assertEqual(seconds, [900, 3600, 21600, 86400, 86400])

    def test_the_lock_level_is_forgotten_after_a_quiet_week(self):
        self.wrong(5)
        self.clock.t = self.svc.locked_until + 8 * 86400
        self.wrong(5)
        self.assertEqual(round(self.svc.locked_until - self.clock.t), 900)

    def test_slow_guessing_is_caught_by_the_daily_limit(self):
        # 4 wrong codes every 10 minutes and a second never trips the 5-in-10-minutes rule, but ten in 24 hours lock
        for i in range(9):
            self.wrong(1)
            self.clock.t += 601
        self.assertFalse(self.svc.handle({"op": "status"})["locked"])
        r = self.wrong(1)
        self.assertTrue(r["locked"])
        self.assertEqual(round(self.svc.locked_until - self.clock.t), 900)

    def test_the_lock_and_the_counters_survive_a_restart(self):
        self.wrong(5)
        locked_until = self.svc.locked_until
        self.svc = self.make()
        self.assertEqual(self.svc.locked_until, locked_until)
        self.assertFalse(self.authorize()["ok"])
        self.clock.t += 15 * 60 + 31
        self.wrong(3)
        self.svc = self.make()
        self.assertEqual(len(self.svc.fails), 3)

    def test_a_garbled_state_file_starts_clean_instead_of_crashing(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        for junk in ("not json", '{"fails": "x", "last_step": []}', "[1,2]", '{"locked_until": "soon"}'):
            with open(self.state_file, "w") as f:
                f.write(junk)
            svc = self.make()
            self.assertEqual((svc.last_step, svc.fails, svc.locked_until), (0, [], 0.0), junk)

    def test_only_root_can_unlock(self):
        self.wrong(5)
        self.assertFalse(self.svc.handle({"op": "unlock"}, "uid 5", uid=5)["ok"])
        self.assertFalse(self.svc.handle({"op": "unlock"}, "x")["ok"])
        self.assertTrue(self.svc.handle({"op": "status"})["locked"])
        self.assertTrue(self.svc.handle({"op": "unlock"}, "uid 0", uid=0)["ok"])
        self.assertFalse(self.svc.handle({"op": "status"})["locked"])
        self.assertEqual((self.svc.fails, self.svc.level), ([], 0))
        self.assertTrue(self.authorize()["ok"])
        self.assertIn("unlocked", self.log())

    def test_a_good_code_clears_the_wrong_guesses(self):
        self.wrong(4)
        self.assertTrue(self.authorize()["ok"])
        self.clock.t += 30
        self.wrong(4)
        self.assertFalse(self.svc.handle({"op": "status"})["locked"])

    def test_old_wrong_guesses_are_forgotten(self):
        self.wrong(4)
        self.clock.t += 11 * 60
        self.wrong(1)
        self.assertFalse(self.svc.handle({"op": "status"})["locked"])

    def test_only_a_few_approvals_can_wait_at_once_and_refusing_does_not_use_up_the_code(self):
        with mock.patch.object(daemon, "MAX_TICKETS", 2):
            for i in range(2):
                self.clock.t += 30
                self.assertTrue(self.authorize()["ok"])
            self.clock.t += 30
            code = code_at(self.clock.t)
            r = self.authorize(code)
            self.assertFalse(r["ok"])
            self.assertIn("Too many", r["error"])
            self.assertEqual(self.svc.last_step, int((T0 + 60) // 30))        # the refused attempt did not use up its step
            self.assertEqual(self.svc.fails, [])

    def test_a_state_file_that_cannot_be_written_means_no_approval(self):
        blocker = os.path.join(self.dir, "afile")
        open(blocker, "w").close()
        self.svc.state_file = os.path.join(blocker, "state.json")          # its parent is a file: cannot be created
        r = self.authorize()
        self.assertFalse(r["ok"])
        self.assertIn("cannot record", r["error"])
        self.assertEqual(self.svc.tickets, {})
        self.svc.state_file = self.state_file
        self.assertTrue(self.authorize()["ok"])                    # and nothing was used up by the refusal


class ExecuteTests(ServiceCase):
    def run_cmd(self, cmd, **kw):
        self.clock.t += 30
        t = self.svc.handle({"op": "authorize", "code": code_at(self.clock.t), "cmd_sha": rootd.cmd_sha(cmd), "by": "Owner"})["ticket"]
        return self.execute(t, cmd=cmd, **kw)

    def test_the_timeout_is_kept_between_5_and_1800_seconds(self):
        for asked, used in ((None, 600), (1, 5), (99999, 1800), ("x", 600), (60, 60), (float("inf"), 600), (float("nan"), 600)):
            self.ran.clear()
            self.run_cmd("true", **({} if asked is None else {"timeout": asked}))
            self.assertEqual(self.ran[0][1], used, asked)

    def test_empty_and_huge_commands_are_refused(self):
        self.assertFalse(self.run_cmd("   ")["ok"])
        self.assertFalse(self.run_cmd("echo " + "x" * 2100)["ok"])
        self.assertFalse(self.execute("t", cmd=None)["ok"])
        self.assertEqual(self.ran, [])

    def test_a_command_with_a_lone_surrogate_is_refused_cleanly(self):
        r = self.execute("t" * 32, cmd="echo \ud800")
        self.assertFalse(r["ok"])
        self.assertNotIn("internal", r["error"])

    def test_only_the_end_of_a_long_output_comes_back(self):
        self.svc.runner = lambda cmd, timeout: (1, "A" * 20000 + "THE END")
        out = self.run_cmd("make")
        self.assertEqual(out["exit"], 1)
        self.assertEqual(len(out["output"]), daemon.RETURN_OUTPUT)
        self.assertTrue(out["output"].endswith("THE END"))

    def test_a_crashing_runner_gives_an_error_answer_not_a_dead_service(self):
        def boom(cmd, timeout):
            raise RuntimeError("secret internals /etc/x")
        self.svc.runner = boom
        out = self.run_cmd("true")
        self.assertFalse(out["ok"])
        self.assertNotIn("/etc/x", json.dumps(out))
        self.assertTrue(self.svc.handle({"op": "status"})["ok"])

    def test_unknown_operations_and_junk_requests(self):
        for req in ({"op": "shell"}, {}, [], "ls", None, {"op": ["exec"]}):
            self.assertFalse(self.svc.handle(req)["ok"], req)

    def test_resetting_the_authenticator_voids_approvals_that_were_waiting(self):
        r = self.authorize()
        os.unlink(self.totp_file)
        out = self.execute(r["ticket"])
        self.assertFalse(out["ok"])
        self.assertIn("reset or changed", out["error"])
        self.assertEqual(self.ran, [])

    def test_enrolling_a_new_authenticator_also_voids_them(self):
        r = self.authorize()
        self.write_secret(OTHER_SECRET)
        self.assertFalse(self.execute(r["ticket"])["ok"])
        self.assertEqual(self.ran, [])
        self.clock.t += 30
        r2 = self.svc.handle({"op": "authorize", "code": code_at(self.clock.t, OTHER_SECRET), "cmd_sha": SHA})
        self.assertTrue(r2["ok"])                                   # the new one works
        self.assertTrue(self.execute(r2["ticket"])["ok"])

    def test_a_command_is_not_run_if_its_start_cannot_be_logged(self):
        r = self.authorize()
        self.svc.log_file = os.path.join(self.dir, "no-such-dir", "rootd.log")
        out = self.execute(r["ticket"])
        self.assertFalse(out["ok"])
        self.assertIn("cannot write its log", out["error"])
        self.assertEqual(self.ran, [])

    def test_the_log_has_the_story_but_never_the_code_or_the_ticket(self):
        code = code_at(self.clock.t)
        r = self.authorize(code)
        long_cmd = "echo " + "é" * 600 + "   forged line"
        r2 = self.run_cmd(long_cmd)
        self.execute(r["ticket"])
        self.authorize("123456", by="Owner FAKE event")       # a wrong code, with a hostile 'by'
        text = self.log()
        for event in ("authorize_ok", "exec_start", "exec_end", "authorize_bad"):
            self.assertIn(event, text)
        self.assertIn(CMD, text)
        self.assertIn("Owner", text)
        self.assertNotIn(code, text)
        self.assertNotIn(r["ticket"], text)
        self.assertNotIn("123456", text)
        self.assertNotIn(" ", text)                            # escaped: a reader splitting lines sees no forged line
        self.assertEqual(len(text.splitlines()), len(text.split("\n")) - 1)
        starts = [json.loads(l) for l in text.splitlines() if '"exec_start"' in l]
        self.assertTrue(any(len(s["cmd"]) > 600 for s in starts))   # the whole command is logged, not the first 500 characters
        for line in text.splitlines():
            json.loads(line)
        if os.name == "posix":
            self.assertEqual(oct(os.stat(self.log_file).st_mode & 0o777), oct(0o600))

    def test_a_runner_can_report_a_timeout_and_the_answer_says_so(self):
        self.svc.runner = lambda cmd, timeout: (124, "slow", True)
        self.assertTrue(self.run_cmd("sleep 99")["timed_out"])
        self.svc.runner = lambda cmd, timeout: (124, "I exited 124 by myself")
        out = self.run_cmd("exit 124")
        self.assertFalse(out["timed_out"])
        self.assertIn('"timed_out": true', self.log())

    def test_a_real_mismatch_is_never_swallowed_by_a_flood_of_junk(self):
        for _ in range(100):
            self.execute("0" * 32)
        r = self.authorize()
        self.assertFalse(self.execute(r["ticket"], cmd=CMD + " && id")["ok"])
        log = self.log()
        self.assertIn("command differs from the approved one", log)
        self.assertIn('"rate_limited"', log)                        # and the flood itself is announced at once, not only at the end of the minute
        self.assertLess(log.count("exec_unknown_ticket"), 30)

    def test_a_state_written_by_enroll_is_not_overwritten_by_the_running_service(self):
        other = self.make()
        other.last_step = int(T0 // 30) + 5
        other._save_state()
        self.wrong(1)                                               # the running service writes its own state (a failed guess)
        self.assertEqual(self.svc._load_state()["last_step"], int(T0 // 30) + 5)
        self.assertEqual(self.svc.last_step, int(T0 // 30) + 5)

    def test_a_flood_of_refusals_cannot_fill_the_log(self):
        self.wrong(5)                                               # locked now
        before = len(self.log().splitlines())
        for _ in range(500):
            self.authorize()
        lines = len(self.log().splitlines()) - before
        self.assertLess(lines, 30)
        self.clock.t += 61
        self.authorize()
        left_out = [json.loads(l) for l in self.log().splitlines() if '"suppressed"' in l]
        self.assertEqual([(x["of"], x["count"]) for x in left_out], [("authorize_locked", 480)])      # and the log says how many it left out


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "needs a Unix shell")
class RunCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(daemon, "OUT_DIR", os.path.join(self.tmp, "out"))
        p.start()
        self.addCleanup(p.stop)

    def run_cmd(self, cmd, timeout=20):
        return daemon.Service.run_command(cmd, timeout)

    def test_it_returns_exit_code_both_streams_and_leaves_no_file_behind(self):
        code, out, timed_out = self.run_cmd("echo out; echo err >&2; exit 3")
        self.assertEqual((code, timed_out), (3, False))
        self.assertIn("out", out)
        self.assertIn("err", out)
        self.assertEqual(os.listdir(daemon.OUT_DIR), [])

    def test_the_output_file_is_root_only_and_not_a_pipe(self):
        code, out, _ = self.run_cmd("stat -L -c %a /proc/self/fd/1; [ -p /proc/self/fd/1 ] && echo PIPE || echo FILE")
        self.assertEqual(out.split(), ["600", "FILE"])

    def test_it_starts_in_the_top_folder_with_a_plain_environment(self):
        with mock.patch.dict(os.environ, {"SECRET_THING": "leaked"}):
            code, out, _ = self.run_cmd("pwd; echo ${SECRET_THING:-none}")
        self.assertEqual(out.split(), ["/", "none"])

    def test_a_command_that_runs_too_long_is_stopped_with_its_children_and_says_so(self):
        started = time.monotonic()
        code, out, timed_out = self.run_cmd("sleep 30 & sleep 30; echo never", 1)
        self.assertEqual((code, timed_out), (124, True))
        self.assertIn("stopped after 1 s", out)
        self.assertNotIn("never", out)
        self.assertLess(time.monotonic() - started, 8)

    def test_a_command_that_exits_124_itself_is_not_a_timeout(self):
        code, out, timed_out = self.run_cmd("exit 124")
        self.assertEqual((code, timed_out), (124, False))

    def test_a_command_gets_sigterm_first_and_can_clean_up(self):
        marker = os.path.join(self.tmp, "cleaned")
        code, out, timed_out = self.run_cmd(f"trap 'echo cleaned > {marker}; exit 0' TERM; sleep 30 & wait", 1)
        self.assertEqual((code, timed_out), (124, True))
        self.assertTrue(os.path.exists(marker))                    # it was asked to stop, not killed on the spot

    def test_a_command_that_ignores_sigterm_is_killed_after_the_grace_period(self):
        started = time.monotonic()
        with mock.patch.object(daemon, "KILL_GRACE", 1):
            code, out, timed_out = self.run_cmd("trap '' TERM; sleep 30", 1)
        took = time.monotonic() - started
        self.assertEqual((code, timed_out), (124, True))
        self.assertGreater(took, 1.9)                              # 1 s of time + 1 s of grace
        self.assertLess(took, 9)

    def test_a_missing_shell_is_an_error_answer(self):
        with mock.patch.object(daemon.subprocess, "Popen", side_effect=OSError("no bash")):
            code, out, timed_out = self.run_cmd("ls", 5)
        self.assertEqual(code, 127)
        self.assertIn("could not start", out)
        self.assertEqual(os.listdir(daemon.OUT_DIR), [])

    def test_an_output_folder_that_cannot_be_made_is_an_error_answer(self):
        blocker = os.path.join(self.tmp, "afile")
        open(blocker, "w").close()
        with mock.patch.object(daemon, "OUT_DIR", os.path.join(blocker, "out")):
            code, out, _ = self.run_cmd("echo hi")
        self.assertEqual(code, 127)
        self.assertIn("could not prepare", out)

    def test_stdin_is_closed(self):
        code, out, _ = self.run_cmd("cat; echo after")
        self.assertEqual(code, 0)
        self.assertIn("after", out)

    def test_a_runaway_output_is_cut_on_disk_and_the_end_is_kept(self):
        script = "for i in $(seq 1 40); do head -c 1000000 /dev/zero | tr '\\0' 'A'; sleep 0.1; done; echo; echo THE-END"
        with mock.patch.object(daemon, "OUT_CAP", 3_000_000):
            code, out, _ = self.run_cmd(script, 60)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out), daemon.MAX_OUTPUT + 200)
        self.assertTrue(out.rstrip().endswith("THE-END"))
        self.assertIn("earlier output was cut", out)

    def test_a_big_output_that_fits_the_cap_is_returned_as_its_end(self):
        code, out, _ = self.run_cmd("head -c 5000000 /dev/zero | tr '\\0' 'A'; echo; echo THE-END", 60)
        self.assertLessEqual(len(out), daemon.MAX_OUTPUT)
        self.assertTrue(out.rstrip().endswith("THE-END"))

    def test_a_background_service_neither_delays_the_answer_nor_dies_when_it_prints_later(self):
        marker = os.path.join(self.tmp, "still-alive")
        started = time.monotonic()
        # the child keeps the inherited output and prints to it AFTER the command has returned (a pipe nobody reads would kill it)
        code, out, _ = self.run_cmd(f"( sleep 2; echo later; echo ok > {marker} ) & echo started", 60)
        took = time.monotonic() - started
        self.assertEqual(code, 0)
        self.assertIn("started", out)
        self.assertLess(took, 2)                                   # did not wait for the child
        time.sleep(3.5)
        self.assertTrue(os.path.exists(marker))

    def test_a_child_that_escaped_the_process_group_cannot_hang_the_answer(self):
        started = time.monotonic()
        code, out, timed_out = self.run_cmd("setsid sleep 8 >/dev/null 2>&1 & sleep 30", 1)
        self.assertEqual((code, timed_out), (124, True))
        self.assertLess(time.monotonic() - started, 8)


class FakeClient:
    """A raw unix-socket client for the protocol tests."""
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(8)
        self.s.connect(path)

    def send(self, data):
        self.s.sendall(data)

    def read(self):
        buf = b""
        try:
            while not buf.endswith(b"\n"):
                chunk = self.s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        return buf

    def close(self):
        self.s.close()


@unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux (SO_PEERCRED)")
class SocketTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.sock = os.path.join(self.dir, "s.sock")
        self.app = Flask(__name__)
        self.app.config["ROOTD_SOCKET"] = self.sock
        ctx = self.app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)

    def serve(self, uids):
        handler = type("H", (daemon.Handler,), {"service": self.svc, "allowed_uids": frozenset(uids)})
        srv = daemon.Server(self.sock, handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return srv

    def test_the_whole_round_trip_through_the_socket(self):
        self.serve({os.getuid()})
        st = rootd.status()
        self.assertEqual({k: st[k] for k in ("available", "enrolled", "locked", "retry_after")}, {"available": True, "enrolled": True, "locked": False, "retry_after": 0})
        ticket, why = rootd.authorize(code_at(self.clock.t), CMD, "Owner")
        self.assertEqual(why, "")
        self.assertRegex(ticket, r"^[0-9a-f]{32}$")
        r = rootd.execute(ticket, CMD, 30)
        self.assertEqual((r["ok"], r["exit"], r["output"]), (True, 0, "done\n"))
        self.assertEqual(self.ran, [(CMD, 30)])
        self.assertFalse(rootd.execute(ticket, CMD, 30)["ok"])

    def test_a_long_command_with_emoji_gets_through(self):
        self.serve({os.getuid()})
        cmd = "echo " + "\U0001F600" * 1500                          # JSON-escaped this is ~18 KB
        ticket, _ = rootd.authorize(code_at(self.clock.t), cmd, "Owner")
        self.assertTrue(ticket)
        self.assertTrue(rootd.execute(ticket, cmd, 30)["ok"])

    def test_a_wrong_code_comes_back_as_a_reason_not_an_exception(self):
        self.serve({os.getuid()})
        ticket, why = rootd.authorize("000000", CMD, "Owner")
        self.assertEqual(ticket, "")
        self.assertIn("not accepted", why)

    def test_a_program_of_another_user_is_refused_before_any_work_is_done(self):
        self.serve({os.getuid() + 12345})
        self.assertFalse(rootd.status()["available"])
        with self.assertRaises(rootd.RootdError):
            rootd.authorize(code_at(self.clock.t), CMD, "x")
        self.assertIn("connection_refused", self.log())
        self.assertEqual(self.ran, [])

    def test_with_no_allowed_users_nobody_gets_in(self):
        self.serve(set())
        self.assertFalse(rootd.status()["available"])

    def test_unlock_through_the_socket_works_for_root_only(self):
        self.serve({os.getuid()})
        self.wrong(5)
        c = FakeClient(self.sock)
        c.send(b'{"op":"unlock"}\n')
        resp = json.loads(c.read())
        c.close()
        self.assertEqual(resp["ok"], os.getuid() == 0)

    def test_a_client_that_sends_nothing_is_dropped_after_the_deadline(self):
        self.serve({os.getuid()})
        with mock.patch.object(daemon, "CONN_DEADLINE", 1):
            c = FakeClient(self.sock)
            c.send(b"{")
            started = time.monotonic()
            self.assertEqual(c.read(), b"")                         # the service closed the connection
            self.assertLess(time.monotonic() - started, 6)
            c.close()
        self.assertTrue(rootd.status()["available"])                # and still serves everyone else

    def test_a_trickling_client_cannot_hold_a_thread_for_ever(self):
        self.serve({os.getuid()})
        with mock.patch.object(daemon, "CONN_DEADLINE", 2):
            c = FakeClient(self.sock)
            started = time.monotonic()
            for _ in range(10):
                try:
                    c.send(b" ")
                except OSError:
                    break
                time.sleep(0.6)
            self.assertLess(time.monotonic() - started, 9)
            self.assertEqual(c.read(), b"")
            c.close()

    def test_an_endless_request_line_is_cut_and_refused(self):
        self.serve({os.getuid()})
        c = FakeClient(self.sock)
        try:
            c.send(b"x" * (daemon.REQUEST_LINE + 5000))
        except OSError:
            pass
        resp = c.read()
        c.close()
        self.assertTrue(resp == b"" or json.loads(resp)["ok"] is False)
        self.assertTrue(rootd.status()["available"])

    def test_deeply_nested_json_does_not_kill_anything(self):
        self.serve({os.getuid()})
        c = FakeClient(self.sock)
        c.send(b"[" * 50000 + b"\n")
        resp = c.read()
        c.close()
        self.assertTrue(resp == b"" or json.loads(resp)["ok"] is False)
        self.assertTrue(rootd.status()["available"])

    def test_not_more_than_the_allowed_number_of_connections_at_once(self):
        with mock.patch.object(daemon, "MAX_CONNECTIONS", 2):
            self.serve({os.getuid()})
            held = [FakeClient(self.sock) for _ in range(2)]        # two silent clients fill both slots
            time.sleep(0.3)
            third = FakeClient(self.sock)
            third.send(b'{"op":"status"}\n')
            self.assertEqual(third.read(), b"")                     # dropped, no thread piled up
            third.close()
            for h in held:
                h.close()
            time.sleep(0.3)
            self.assertTrue(rootd.status()["available"])            # the slots come back

    def test_a_connection_slot_is_given_back_when_the_thread_cannot_start(self):
        handler = type("H", (daemon.Handler,), {"service": self.svc, "allowed_uids": frozenset({os.getuid()})})
        with mock.patch.object(daemon, "MAX_CONNECTIONS", 2):
            srv = daemon.Server(self.sock, handler)
        self.addCleanup(srv.server_close)
        with mock.patch.object(daemon.socketserver.ThreadingMixIn, "process_request", side_effect=RuntimeError("can't start new thread")):
            for _ in range(5):
                with self.assertRaises(RuntimeError):
                    srv.process_request(mock.Mock(), ("", 0))
        self.assertTrue(srv.slots.acquire(blocking=False))
        self.assertTrue(srv.slots.acquire(blocking=False))
        self.assertFalse(srv.slots.acquire(blocking=False))        # exactly the two it was made for: nothing leaked

    def test_the_socket_file_is_not_world_accessible_in_the_real_setup(self):
        src = open(os.path.join(DEPLOY, "aidash-rootd")).read()
        self.assertIn("os.chmod(SOCKET, 0o660)", src)
        self.assertIn("os.chmod(SOCKET_DIR, 0o750)", src)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        ctx = self.app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)

    def test_no_service_means_unavailable_not_a_crash(self):
        self.app.config["ROOTD_SOCKET"] = "/nonexistent/rootd.sock"
        st = rootd.status()
        self.assertFalse(st["available"])
        self.assertFalse(st["enrolled"])
        with self.assertRaises(rootd.RootdError) as cm:
            rootd.authorize("123456", "ls", "x")
        self.assertFalse(cm.exception.sent)                         # nothing was sent: the command cannot have run

    def test_status_survives_odd_answers(self):
        with mock.patch.object(rootd, "_call", return_value={"ok": True, "enrolled": True, "locked": True, "retry_after": "soon"}):
            self.assertEqual(rootd.status()["retry_after"], 0)
        with mock.patch.object(rootd, "_call", side_effect=rootd.RootdError("boom")):
            self.assertFalse(rootd.status()["available"])

    def test_authorize_returns_a_ticket_or_a_reason(self):
        with mock.patch.object(rootd, "_call", return_value={"ok": True, "ticket": "t" * 40}) as call:
            self.assertEqual(rootd.authorize(" 123456", "ls", "Owner"), ("t" * 40, ""))
            sent = call.call_args[0][0]
            self.assertEqual(sent["cmd_sha"], rootd.cmd_sha("ls"))
            self.assertNotIn("ls", [v for k, v in sent.items() if k != "cmd_sha"])      # only the hash of the command goes with the code
        with mock.patch.object(rootd, "_call", return_value={"ok": False, "error": "Too many wrong codes."}):
            self.assertEqual(rootd.authorize("123456", "ls", "Owner"), ("", "Too many wrong codes."))
        with mock.patch.object(rootd, "_call", return_value={"ok": True, "ticket": ""}):
            self.assertEqual(rootd.authorize("123456", "ls", "Owner")[0], "")

    def test_execute_waits_longer_than_the_command_may_run(self):
        with mock.patch.object(rootd, "_call", return_value={"ok": True, "exit": 0, "output": ""}) as call:
            rootd.execute("tk", "ls", 100)
        self.assertEqual(call.call_args[1]["timeout"], 160)


class QrTests(unittest.TestCase):
    URI = b"otpauth://totp/AI%20dashboard%20root?secret=" + SECRET.encode()

    def test_it_is_the_same_picture_as_the_reference_library_for_every_mask(self):
        try:
            import qrcode
            from qrcode.constants import ERROR_CORRECT_L
            from qrcode.util import MODE_8BIT_BYTE, QRData
        except ImportError:
            self.skipTest("the reference QR library is not installed")
        for data in (self.URI, b"hello", b"x" * 78, b"otpauth://totp/AI%20dashboard%20root?secret=MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43U"):
            for mask in range(8):
                qr = qrcode.QRCode(version=4, error_correction=ERROR_CORRECT_L, box_size=1, border=0, mask_pattern=mask)
                qr.add_data(QRData(data, mode=MODE_8BIT_BYTE))
                qr.make(fit=False)
                self.assertEqual(daemon.qr_matrix(data, mask=mask), [[bool(c) for c in row] for row in qr.get_matrix()], (data[:24], mask))

    def test_it_picks_a_mask_by_itself_and_refuses_what_does_not_fit(self):
        m = daemon.qr_matrix(self.URI)
        self.assertEqual((len(m), len(m[0])), (33, 33))
        self.assertIn(m, [daemon.qr_matrix(self.URI, mask=k) for k in range(8)])
        with self.assertRaises(ValueError):
            daemon.qr_matrix(b"x" * 79)

    def test_the_terminal_picture_has_the_right_size_and_colours(self):
        art = daemon.qr_ansi(daemon.qr_matrix(self.URI))
        lines = art.split("\n")
        self.assertEqual(len(lines), 20)                                   # 33 + 2 x 3 border = 39 rows, two per line
        self.assertEqual(lines[0].count("▀"), 39)
        self.assertIn("\x1b[30;", art)
        self.assertIn("\x1b[97;", art)                                     # black and white, whatever colours the terminal uses
        self.assertTrue(all(l.endswith("\x1b[0m") for l in lines))


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "enroll must be run as root")
class EnrollTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name, value in (("CONFIG_DIR", os.path.join(self.tmp, "etc")), ("TOTP_FILE", os.path.join(self.tmp, "etc", "totp.json")),
                            ("STATE_FILE", os.path.join(self.tmp, "var", "state.json")), ("LOG_FILE", os.path.join(self.tmp, "rootd.log"))):
            p = mock.patch.object(daemon, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(daemon, "new_secret", return_value=SECRET)
        p.start()
        self.addCleanup(p.stop)

    def good(self):
        return daemon.totp(SECRET, int(time.time() // 30))

    def run_enroll(self, answers):
        it = iter(answers)

        def fake_input(prompt=""):
            a = next(it)
            if isinstance(a, Exception):
                raise a
            return a() if callable(a) else a
        with mock.patch("builtins.input", side_effect=fake_input), mock.patch("builtins.print") as pr:
            rc = daemon.enroll()
        return rc, "\n".join(str(c.args[0]) for c in pr.call_args_list if c.args)

    def test_a_right_code_saves_the_secret_privately_and_uses_the_code_up(self):
        rc, said = self.run_enroll([self.good])
        self.assertEqual(rc, 0)
        self.assertIn("Scan a QR code", said)
        self.assertIn("GEZD GNBV GY3T QOJQ", said)                          # the key, in groups of four, for typing
        self.assertEqual(json.load(open(daemon.TOTP_FILE))["secret"], SECRET)
        self.assertEqual(oct(os.stat(daemon.TOTP_FILE).st_mode & 0o777), oct(0o600))
        self.assertEqual(oct(os.stat(daemon.CONFIG_DIR).st_mode & 0o777), oct(0o700))
        svc = daemon.Service(clock=time.time)
        self.assertGreaterEqual(svc.last_step, int(time.time() // 30) - 1)
        r = svc.handle({"op": "authorize", "code": self.good(), "cmd_sha": SHA})
        self.assertFalse(r["ok"])                                           # the code typed here cannot be used again
        self.assertEqual(oct(os.stat(daemon.STATE_FILE).st_mode & 0o777), oct(0o600))

    def test_a_wrong_code_can_be_tried_again_without_a_new_key(self):
        rc, said = self.run_enroll(["000000", " 000 000 ", self.good])
        self.assertEqual(rc, 0)
        self.assertIn("4 more tries", said)
        self.assertIn("3 more tries", said)
        self.assertIn("Delete the entry in the app and add it again", said)  # after the second miss it shows the key again
        grouped = " ".join(SECRET[i:i + 4] for i in range(0, len(SECRET), 4))
        self.assertEqual(said.count(grouped), 2)                              # once with the QR code, once more after the second miss

    def test_five_wrong_codes_save_nothing(self):
        rc, said = self.run_enroll(["000000"] * 5)
        self.assertEqual(rc, 1)
        self.assertIn("Too many wrong codes", said)
        self.assertFalse(os.path.exists(daemon.TOTP_FILE))

    def test_letters_a_short_code_and_a_closed_input_are_just_wrong(self):
        rc, said = self.run_enroll(["abcdef", "12345", EOFError(), "", "١٢٣٤٥٦"])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(daemon.TOTP_FILE))

    def test_it_will_not_replace_an_existing_authenticator(self):
        self.run_enroll([self.good])
        rc, said = self.run_enroll([])
        self.assertEqual(rc, 1)
        self.assertIn("already enrolled", said)


class DeployFilesTests(unittest.TestCase):
    def test_the_unit_runs_the_installed_script_and_is_not_sandboxed(self):
        unit = open(os.path.join(DEPLOY, "aidash-rootd.service")).read()
        self.assertIn("ExecStart=/usr/local/sbin/aidash-rootd serve", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("KillMode=process", unit)               # a restart must not kill a half-done command or a service it started
        self.assertNotIn("ProtectSystem", unit)              # it must be able to run any root command
        self.assertNotIn("UMask", unit)                       # commands it runs keep the normal umask

    def test_the_log_is_rotated(self):
        conf = open(os.path.join(DEPLOY, "aidash-rootd.logrotate")).read()
        self.assertIn("/var/log/aidash-rootd.log", conf)
        self.assertIn("create 0600 root root", conf)

    def test_only_root_and_the_dashboards_user_may_talk_to_it(self):
        self.assertEqual(daemon.ALLOWED_USERS, ("aided5498",))
        self.assertEqual(daemon.SOCKET_GROUP, "aided5498")
        self.assertEqual(daemon.Handler.allowed_uids, frozenset())      # nobody unless serve() says so

    def test_the_secret_file_is_never_world_readable_even_for_a_moment(self):
        src = open(os.path.join(DEPLOY, "aidash-rootd")).read()
        self.assertIn("os.O_EXCL, 0o600", src)
        self.assertIn("os.chmod(CONFIG_DIR, 0o700)", src)


if __name__ == "__main__":
    unittest.main()
