"""The code guard (deploy/aidash-code-guard): checkpoint, check, alert-once-per-kind, restore, and the safety nets around them. Runs the
real script against temporary folders; git and rsync are used for real (they exist on the server and on a Mac); nothing touches the
real dashboard."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock


def load_guard():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "aidash-code-guard")
    loader = importlib.machinery.SourceFileLoader("aidash_code_guard", path)
    spec = importlib.util.spec_from_loader("aidash_code_guard", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


HAVE_TOOLS = bool(shutil.which("git") and shutil.which("rsync"))
OK = mock.Mock(returncode=0, stdout="", stderr="")


class Base(unittest.TestCase):
    def setUp(self):
        self.g = load_guard()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.app, self.src, self.source = f"{t}/app", f"{t}/src", f"{t}/source"
        for d in (self.app, self.source):
            os.makedirs(d)
        self.g.APP, self.g.SRC = self.app, self.src
        self.g.STATE, self.g.STATUS_OUT = self.src + "/state.json", f"{t}/code-guard.json"
        self.g.DB, self.g.DEPLOYING = f"{t}/app.db", f"{t}/deploying"
        self.g.BACKUP_DIR = f"{t}/backups"
        self.g.STAGE_PARENT = f"{t}/stage"
        self.g.HEALTH_RETRY_WAIT = 0
        os.makedirs(self.g.BACKUP_DIR)
        self.alerts = []
        self.alert_ok = True
        p = mock.patch.object(self.g, "alert", side_effect=lambda m: (self.alerts.append(m), self.alert_ok)[1])
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.g, "write_status", side_effect=lambda s: setattr(self, "status", s))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.g, "health", return_value=200)
        p.start()
        self.addCleanup(p.stop)
        self.status = None

    def put(self, root, name, text="x"):
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def make_db(self, head):
        if os.path.exists(self.g.DB):
            os.unlink(self.g.DB)
        c = sqlite3.connect(self.g.DB)
        c.execute("create table alembic_version (version_num varchar(32))")
        c.execute("insert into alembic_version values (?)", (head,))
        c.commit()
        c.close()

    def seed_source(self, root):
        self.put(root, "app/__init__.py", "# app")
        self.put(root, "app/project_notes.py", "# notes")
        self.put(root, "migrations/versions/aaa111_one.py", "revision = 'aaa111'\ndown_revision = None\n")
        self.put(root, "migrations/versions/bbb222_two.py", "revision = 'bbb222'\ndown_revision = 'aaa111'\n")
        self.put(root, "wsgi.py", "app = 1")
        self.put(root, "tests/test_x.py", "# a test that is not deployed")


class TreeTests(Base):
    def test_files_map_ignores_noise_and_symlinks(self):
        self.put(self.app, "app/a.py", "a")
        self.put(self.app, "venv/lib/x.py", "v")
        self.put(self.app, "instance/app.db", "db")
        self.put(self.app, "app/__pycache__/a.pyc", "c")
        self.put(self.app, "app/b.pyc", "c")
        self.put(self.app, ".DS_Store", "m")
        self.put(self.app, "app/.a.py.swp", "editor")
        self.put(self.app, "app/a.py~", "backup")
        self.put(self.app, ".pytest_cache/v/cache", "c")
        if hasattr(os, "symlink"):
            os.symlink(os.path.join(self.app, "app/a.py"), os.path.join(self.app, "link.py"))
        self.assertEqual(sorted(self.g.files_map(self.app)), ["app/a.py"])

    def test_a_file_that_vanishes_while_we_look_is_just_not_there(self):
        self.put(self.app, "app/a.py", "a")
        self.put(self.app, "app/b.py", "b")
        real_open = os.open

        def flaky(path, *a, **kw):
            if str(path).endswith("b.py"):
                raise FileNotFoundError(path)
            return real_open(path, *a, **kw)
        with mock.patch.object(self.g.os, "open", side_effect=flaky):
            self.assertEqual(sorted(self.g.files_map(self.app)), ["app/a.py"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_a_named_pipe_left_in_the_folder_does_not_hang_the_guard(self):
        self.put(self.app, "app/a.py", "a")
        os.mkfifo(os.path.join(self.app, "app/trap.fifo"))
        done = {}
        t = threading.Thread(target=lambda: done.setdefault("map", self.g.files_map(self.app)), daemon=True)
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive(), "files_map hung on a FIFO")
        self.assertEqual(sorted(done["map"]), ["app/a.py"])

    def test_tests_folder_is_skipped_only_at_the_top(self):
        self.put(self.app, "tests/t.py")
        self.put(self.app, "app/tests/inner.py")
        self.assertEqual(sorted(self.g.files_map(self.app, skip_tests=True)), ["app/tests/inner.py"])
        self.assertEqual(sorted(self.g.files_map(self.app)), ["app/tests/inner.py", "tests/t.py"])

    def test_compare_tells_missing_different_and_new(self):
        a, b = {"x": "1", "y": "2", "z": "3"}, {"x": "1", "y": "CHANGED", "n": "4"}
        self.assertEqual(self.g.compare(a, b), (["z"], ["y"], ["n"]))

    def test_the_hash_follows_the_content_and_not_the_order(self):
        self.assertEqual(self.g.tree_hash({"a": "1", "b": "2"}), self.g.tree_hash({"b": "2", "a": "1"}))
        self.assertNotEqual(self.g.tree_hash({"a": "1"}), self.g.tree_hash({"a": "2"}))

    def test_migration_revisions_and_heads_are_read_from_the_code(self):
        self.seed_source(self.app)
        self.assertEqual(self.g.code_revisions(self.app), {"aaa111", "bbb222"})
        self.assertEqual(self.g.code_heads(self.app), {"bbb222"})
        self.assertEqual(self.g.code_revisions(self.app + "/nope"), set())
        self.put(self.app, "migrations/versions/ccc333_merge.py", "revision = 'ccc333'\ndown_revision = ('bbb222', 'aaa111')\n")
        self.assertEqual(self.g.code_heads(self.app), {"ccc333"})

    def test_db_head(self):
        self.assertIsNone(self.g.db_head())
        self.make_db("bbb222")
        self.assertEqual(self.g.db_head(), "bbb222")


class AlertTests(Base):
    """alert() and telegram() themselves (the other tests replace alert)."""

    def setUp(self):
        super().setUp()
        self.real = load_guard()
        self.real.APP, self.real.DB = self.app, self.g.DB

    def test_the_telegram_snippet_runs_as_the_site_user_never_as_root(self):
        with mock.patch.object(self.real.subprocess, "run") as run:
            self.real.telegram("hello")
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ["runuser", "-u", self.real.SITE_USER, "--"])
        self.assertEqual(args[4], self.real.VENV_PY)         # the site user's Python is only ever started by the site user

    def test_alert_says_whether_the_bell_row_was_written(self):
        with mock.patch.object(self.real.subprocess, "run") as run, mock.patch.object(self.real, "telegram") as tg:
            self.assertTrue(self.real.alert("a" * 400))
            self.assertEqual(len(run.call_args.args[0][-1]), 250)           # cut to the column
            tg.assert_called_once()
        with mock.patch.object(self.real.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "x")), mock.patch.object(self.real, "telegram"):
            self.assertFalse(self.real.alert("x"))


class LockTests(Base):
    def test_a_second_command_cannot_take_the_lock_while_one_runs(self):
        a, b = self.g.Lock(), self.g.Lock()
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire(blocking=False))
        a.release()
        self.assertTrue(b.acquire(blocking=False))
        b.release()

    def test_main_skips_a_check_that_would_wait_and_runs_the_others(self):
        holder = self.g.Lock()
        holder.acquire()
        with mock.patch.object(self.g, "cmd_check", return_value=1) as chk:
            self.assertEqual(self.g.main(["x", "check"]), 0)
            chk.assert_not_called()
        holder.release()
        with mock.patch.object(self.g, "cmd_check", return_value=0) as chk:
            self.g.main(["x", "check"])
            chk.assert_called_once()


@unittest.skipUnless(HAVE_TOOLS, "needs git and rsync")
class SaveAndCheckTests(Base):
    def deploy_from(self, source):
        shutil.rmtree(self.app)
        shutil.copytree(source, self.app, ignore=shutil.ignore_patterns("tests"))

    def checkpoint(self, message="first"):
        self.seed_source(self.source)
        self.deploy_from(self.source)
        self.make_db("bbb222")
        return self.g.cmd_save([self.source, message])

    def git(self, *args):
        return subprocess.run(["git", "-C", self.src] + list(args), capture_output=True, text=True).stdout.strip()

    def test_save_records_the_source_with_its_tests_and_remembers_the_deployed_state(self):
        self.assertEqual(self.checkpoint("Picker, notes and cards"), 0)
        state = self.g.load_state()
        self.assertEqual(state["checkpoint"]["message"], "Picker, notes and cards")
        self.assertEqual(state["checkpoint"]["deployed_hash"], self.g.tree_hash(self.g.files_map(self.app)))
        self.assertTrue(os.path.isfile(self.src + "/tree/tests/test_x.py"))             # the repo keeps the tests the server doesn't have
        self.assertIn("Picker, notes and cards", self.git("log", "--format=%s"))
        self.assertEqual(self.alerts, [])
        if os.name == "posix":
            self.assertEqual(oct(os.stat(self.src).st_mode & 0o777), oct(0o700))        # the source is not for the other site users
            self.assertEqual(oct(os.stat(self.g.STATE).st_mode & 0o777), oct(0o600))

    def test_save_refuses_a_source_that_is_not_what_is_deployed(self):
        self.seed_source(self.source)
        self.deploy_from(self.source)
        self.put(self.source, "app/project_notes.py", "# edited but not deployed")
        self.assertEqual(self.g.cmd_save([self.source, "x"]), 1)
        self.assertFalse(os.path.exists(self.g.STATE))

    def test_a_tests_folder_on_the_server_does_not_block_saving(self):
        self.seed_source(self.source)
        self.deploy_from(self.source)
        self.put(self.app, "tests/stray.py", "x")
        self.make_db("bbb222")
        self.assertEqual(self.g.cmd_save([self.source, "x"]), 0)

    def test_saving_twice_without_changes_makes_no_second_commit(self):
        self.checkpoint("one")
        self.g.cmd_save([self.source, "two"])
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "1")

    def test_a_working_copy_with_git_and_editor_leftovers_is_saved_without_them(self):
        self.seed_source(self.source)
        self.put(self.source, ".git/config", "[core]")
        self.put(self.source, "node_modules/x/index.js", "x")
        self.put(self.source, "app/.notes.py.swp", "x")
        self.put(self.source, ".DS_Store", "x")
        self.deploy_from(self.source)
        self.make_db("bbb222")
        self.assertEqual(self.g.cmd_save([self.source, "m"]), 0)
        self.assertFalse(os.path.exists(self.src + "/tree/.git"))                       # a nested repo would turn into an empty gitlink
        files = self.git("ls-files").splitlines()
        self.assertIn("tree/app/project_notes.py", files)
        self.assertFalse([f for f in files if ".swp" in f or "node_modules" in f or "DS_Store" in f])
        self.assertEqual(self.g.files_map(self.src + "/tree", skip_tests=True), self.g.files_map(self.app))

    def test_a_new_checkpoint_that_drops_files_or_migrations_is_refused_unless_forced(self):
        self.checkpoint("ours")
        older = self.source + "-older"                                                  # a deploy from the other, older copy
        shutil.copytree(self.source, older)
        os.unlink(os.path.join(older, "app/project_notes.py"))
        os.unlink(os.path.join(older, "migrations/versions/bbb222_two.py"))
        self.deploy_from(older)
        with mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_save([older, "older copy"]), 1)
        shown = " ".join(c.args[0] for c in log.call_args_list)
        self.assertIn("REFUSED", shown)
        self.assertIn("app/project_notes.py", shown)
        self.assertIn("bbb222", shown)
        self.assertEqual(self.g.load_state()["checkpoint"]["message"], "ours")           # the good checkpoint is still the one remembered
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "1")
        self.make_db("aaa111")
        self.assertEqual(self.g.cmd_save([older, "older copy on purpose", "--force"]), 0)
        self.assertEqual(self.g.load_state()["checkpoint"]["message"], "older copy on purpose")

    def test_a_checkpoint_that_only_adds_and_changes_is_fine(self):
        self.checkpoint("one")
        self.put(self.source, "app/new_feature.py", "new")
        self.put(self.source, "app/project_notes.py", "# notes v2")
        self.put(self.source, "migrations/versions/ccc333_three.py", "revision = 'ccc333'\ndown_revision = 'bbb222'\n")
        self.deploy_from(self.source)
        self.make_db("ccc333")
        with mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_save([self.source, "two"]), 0)
        self.assertIn("2 file(s) added, 1 changed, 0 gone", " ".join(c.args[0] for c in log.call_args_list))
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "2")

    def test_files_named_like_the_guards_own_files_are_source_code_and_are_kept(self):
        self.seed_source(self.source)
        self.put(self.source, "app/state.json", "{}")
        self.put(self.source, "app/.lock", "x")
        self.deploy_from(self.source)
        self.make_db("bbb222")
        self.assertEqual(self.g.cmd_save([self.source, "m"]), 0)
        files = self.git("ls-files").splitlines()
        self.assertIn("tree/app/state.json", files)
        self.assertIn("tree/app/.lock", files)

    def test_a_checkpoint_in_which_git_dropped_files_is_not_valid(self):
        self.seed_source(self.source)
        self.put(self.source, "app/.gitignore", "*.py\n")             # a nested ignore rule would hide every .py file from git
        self.deploy_from(self.source)
        self.make_db("bbb222")
        with mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_save([self.source, "m"]), 1)
        self.assertIn("NOT valid", " ".join(c.args[0] for c in log.call_args_list))
        self.assertFalse(os.path.exists(self.g.STATE))

    def test_a_symlinked_app_folder_is_refused(self):
        self.checkpoint()
        real = self.app + "-real"
        shutil.copytree(self.app, real)
        shutil.rmtree(self.app)
        os.symlink(real, self.app)
        with mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_save([self.source, "x"]), 1)
            self.assertEqual(self.g.cmd_restore([]), 1)
        self.assertEqual(" ".join(c.args[0] for c in log.call_args_list).count("symbolic link"), 2)

    def test_an_untouched_server_is_ok_and_writes_a_status(self):
        self.checkpoint()
        self.alerts.clear()
        self.assertEqual(self.g.cmd_check(), 0)
        self.assertTrue(self.status["ok"])
        self.assertEqual(self.status["checkpoint"]["message"], "first")
        self.assertEqual(self.alerts, [])

    def test_a_deploy_from_an_older_copy_is_noticed_once(self):
        self.checkpoint()
        self.alerts.clear()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))                     # the other copy didn't have our file …
        self.put(self.app, "app/__init__.py", "# older")                               # … and an older one of this
        self.put(self.app, "app/legacy.py", "# only in the old copy")
        os.unlink(os.path.join(self.app, "migrations/versions/bbb222_two.py"))        # … nor our newest migration
        self.assertEqual(self.g.cmd_check(), 1)
        text = " ".join(self.alerts)
        self.assertIn("2 file(s) missing, 1 different, 1 new", text)
        self.assertIn("app/project_notes.py", text)
        self.assertIn("migration bbb222", text)
        self.assertFalse(self.status["ok"])
        self.assertEqual(len(self.alerts), 2)                                           # one for the code, one for the database
        self.g.cmd_check()
        self.assertEqual(len(self.alerts), 2)                                           # the same problems do not alert again

    def test_a_changing_count_of_the_same_kind_of_problem_does_not_start_a_storm(self):
        self.checkpoint()
        self.alerts.clear()
        self.put(self.app, "app/extra.py", "x")
        self.g.cmd_check()
        self.assertEqual(len(self.alerts), 1)
        self.put(self.app, "app/extra2.py", "x")                                        # more of the same kind
        self.g.cmd_check()
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("2 new", self.status["problems"][0])                              # but the status page shows the current numbers

    def test_a_different_kind_alerts_and_a_fix_clears_and_a_return_alerts_again(self):
        self.checkpoint()
        self.alerts.clear()
        self.put(self.app, "app/extra.py", "x")
        self.g.cmd_check()
        with mock.patch.object(self.g, "health", return_value=500):
            self.g.cmd_check()
        self.assertEqual(len(self.alerts), 2)                                           # code, then the login page
        os.unlink(os.path.join(self.app, "app/extra.py"))
        self.assertEqual(self.g.cmd_check(), 0)
        self.assertEqual(self.g.load_state()["alerted_kinds"], [])
        self.put(self.app, "app/extra.py", "x")
        self.g.cmd_check()
        self.assertEqual(len(self.alerts), 3)                                           # and if it comes back, it alerts again

    def test_an_alert_that_could_not_be_delivered_is_tried_again_next_time(self):
        self.checkpoint()
        self.alerts.clear()
        self.put(self.app, "app/extra.py", "x")
        self.alert_ok = False
        self.g.cmd_check()
        self.assertEqual(self.g.load_state().get("alerted_kinds"), [])
        self.alert_ok = True
        self.g.cmd_check()
        self.assertEqual(len(self.alerts), 2)
        self.g.cmd_check()
        self.assertEqual(len(self.alerts), 2)

    def test_a_checkpoint_saved_while_a_check_is_alerting_is_not_overwritten(self):
        self.checkpoint("old")
        self.alerts.clear()
        self.put(self.app, "app/extra.py", "x")

        def alert_and_meanwhile_save(message):
            self.alerts.append(message)
            st = self.g.load_state()
            st["checkpoint"] = {"commit": "newer1", "at": "t", "message": "the new checkpoint", "deployed_hash": "h"}
            self.g.save_state(st)
            return True
        with mock.patch.object(self.g, "alert", side_effect=alert_and_meanwhile_save):
            self.g.cmd_check()
        st = self.g.load_state()
        self.assertEqual(st["checkpoint"]["message"], "the new checkpoint")
        self.assertEqual(st["alerted_kinds"], ["code"])

    def test_the_login_page_must_fail_twice_in_a_row_to_count(self):
        self.checkpoint()
        self.alerts.clear()
        with mock.patch.object(self.g, "health", side_effect=[500, 200]):
            self.assertEqual(self.g.cmd_check(), 0)
        with mock.patch.object(self.g, "health", return_value=500):
            self.assertEqual(self.g.cmd_check(), 1)
        self.assertIn("answers 500", self.alerts[0])

    def test_a_database_behind_the_code_is_a_problem(self):
        self.checkpoint()
        self.alerts.clear()
        self.make_db("aaa111")                                                          # the code has bbb222; a migration was never applied
        self.assertEqual(self.g.cmd_check(), 1)
        self.assertIn("never applied", self.alerts[0])

    def test_a_damaged_git_copy_is_a_problem(self):
        self.checkpoint()
        self.alerts.clear()
        shutil.rmtree(self.src + "/.git/objects")
        self.assertEqual(self.g.cmd_check(), 1)
        self.assertIn("git copy", self.alerts[0])

    def test_a_running_deploy_is_not_alarming_but_an_old_flag_is_ignored(self):
        self.checkpoint()
        self.alerts.clear()
        self.put(self.app, "app/extra.py", "x")
        open(self.g.DEPLOYING, "w").close()
        self.assertEqual(self.g.cmd_check(), 0)
        self.assertEqual(self.alerts, [])
        old = time.time() - 2 * 3600
        os.utime(self.g.DEPLOYING, (old, old))
        self.assertEqual(self.g.cmd_check(), 1)

    def test_no_checkpoint_is_said_plainly(self):
        found, _ = self.g.problems_now({})
        self.assertIn("No checkpoint", found[0])


@unittest.skipUnless(HAVE_TOOLS, "needs git and rsync")
class RestoreTests(SaveAndCheckTests):
    """The same fixtures; `run` is replaced so that rsync and tar really copy but systemctl, chown, runuser and pip are only recorded."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.rsync_works = True
        self.tar_works = True
        self.db_backup_works = True

        def fake_run(cmd, **kw):
            self.calls.append(cmd)
            if "rsync" in cmd and self.rsync_works:
                return subprocess.run(cmd[cmd.index("rsync"):], capture_output=True, text=True)      # `runuser -u user -- rsync …`: do the rsync part
            if cmd[0] == "tar" and self.tar_works:
                return subprocess.run(cmd, capture_output=True, text=True)
            if cmd[0] == "runuser" and "python3" in cmd:
                return mock.Mock(returncode=0 if self.db_backup_works else 1, stdout="", stderr="")
            return mock.Mock(returncode=2 if ("rsync" in cmd or cmd[0] == "tar") else 0, stdout="", stderr="failed")
        for target, kwargs in (("run", {"side_effect": fake_run}), ("as_site", {"return_value": OK}), ("sleep", None)):
            p = mock.patch.object(self.g.time, "sleep") if target == "sleep" else mock.patch.object(self.g, target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        self.as_site = self.g.as_site

    def test_restore_dry_run_changes_nothing_and_lists_what_would_happen(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        before = self.g.files_map(self.app)
        with mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_restore(["--dry-run"]), 0)
        self.assertEqual(self.g.files_map(self.app), before)
        shown = " ".join(c.args[0] for c in log.call_args_list)
        self.assertIn("app/project_notes.py", shown)
        self.assertIn("dry run", shown)

    def test_restore_refuses_when_it_would_leave_the_database_ahead_of_the_code(self):
        self.checkpoint()
        self.make_db("zzz999")
        with mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_restore([]), 1)
        self.assertIn("REFUSED", " ".join(c.args[0] for c in log.call_args_list))

    def test_restore_refuses_a_revision_that_is_not_a_whole_dashboard(self):
        self.seed_source(self.source)
        os.unlink(os.path.join(self.source, "wsgi.py"))
        self.deploy_from(self.source)
        self.make_db("bbb222")
        self.assertEqual(self.g.cmd_save([self.source, "broken"]), 0)
        before = self.g.files_map(self.app)
        with mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_restore([]), 1)
        self.assertIn("not a complete dashboard", " ".join(c.args[0] for c in log.call_args_list))
        self.assertEqual(self.g.files_map(self.app), before)
        self.assertFalse([c for c in self.calls if "rsync" in c])

    def test_restore_puts_the_checkpoint_back_after_two_backups_and_packages(self):
        self.checkpoint()
        self.put(self.source, "requirements.txt", "flask\n")
        self.deploy_from(self.source)
        self.g.cmd_save([self.source, "with requirements"])
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        self.put(self.app, "app/legacy.py", "old")
        scripts = []
        self.g.as_site = lambda script, timeout=900: (scripts.append(script), OK)[1]
        self.assertEqual(self.g.cmd_restore([]), 0)
        rsync = next(c for c in self.calls if "rsync" in c and "--checksum" in c)
        for flag in ("--delete", "--exclude=/venv", "--exclude=/instance", "--exclude=/tests"):
            self.assertIn(flag, rsync)
        self.assertEqual(rsync[:5], ["runuser", "-u", self.g.SITE_USER, "--", "rsync"])     # run as the site user, never as root, into its folder
        self.assertFalse([c for c in self.calls if c[0] == "chown"])                          # (its own files need no chown)
        self.assertTrue(os.path.isfile(os.path.join(self.app, "app/project_notes.py")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "app/legacy.py")))
        self.assertTrue(any(c[:3] == ["systemctl", "restart", "ai-dashboard"] for c in self.calls))
        self.assertTrue(any(c[0] == "tar" and "czf" in c for c in self.calls))          # a backup of the code first
        self.assertTrue(any(c[0] == "runuser" and "python3" in c for c in self.calls))  # and of the database
        self.assertTrue(any("pip install -q -r requirements.txt" in s for s in scripts))
        self.assertTrue(any("flask db upgrade" in s for s in scripts))
        self.assertLess([i for i, s in enumerate(scripts) if "pip install" in s][0], [i for i, s in enumerate(scripts) if "db upgrade" in s][0])
        self.assertFalse(os.path.exists(self.g.DEPLOYING))
        self.assertEqual(self.g.cmd_check(), 0)

    def test_restoring_an_older_checkpoint_makes_it_the_newest_one(self):
        self.checkpoint("one")
        first = self.git("rev-parse", "--short", "HEAD")
        self.put(self.source, "app/new_feature.py", "new")
        self.deploy_from(self.source)
        self.assertEqual(self.g.cmd_save([self.source, "two"]), 0)
        self.assertEqual(self.g.cmd_restore([first]), 0)
        cp = self.g.load_state()["checkpoint"]
        self.assertIn("restore of " + first, cp["message"])
        self.assertEqual(cp["commit"], self.git("rev-parse", "--short", "HEAD"))
        self.assertFalse(os.path.exists(os.path.join(self.app, "app/new_feature.py")))
        self.assertEqual(self.g.files_map(self.src + "/tree", skip_tests=True), self.g.files_map(self.app))
        self.alerts.clear()
        self.assertEqual(self.g.cmd_check(), 0)
        self.assertEqual(self.alerts, [])

    def test_restore_rolls_back_when_the_dashboard_does_not_come_up(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        with mock.patch.object(self.g, "health", return_value=500), mock.patch.object(self.g, "rollback", return_value=1) as rb:
            self.assertEqual(self.g.cmd_restore([]), 1)
        rb.assert_called_once()

    def test_restore_undoes_itself_when_a_file_did_not_copy(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))

        def fake(cmd, **kw):
            self.calls.append(cmd)
            if cmd[0] == "tar":
                return subprocess.run(cmd, capture_output=True, text=True)       # the backup is real
            return mock.Mock(returncode=0, stdout="", stderr="")                 # rsync claims success but copies nothing
        with mock.patch.object(self.g, "run", side_effect=fake), mock.patch.object(self.g, "rollback", return_value=1) as rb:
            self.assertEqual(self.g.cmd_restore([]), 1)
        rb.assert_called_once()

    def test_a_failing_package_install_or_migration_rolls_back(self):
        self.checkpoint()
        self.put(self.source, "requirements.txt", "flask\n")
        self.deploy_from(self.source)
        self.g.cmd_save([self.source, "with requirements"])
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        for failing in ("pip install", "db upgrade"):
            with mock.patch.object(self.g, "as_site", side_effect=lambda script, timeout=900, f=failing: mock.Mock(returncode=1 if f in script else 0, stdout="", stderr="boom")), \
                    mock.patch.object(self.g, "rollback", return_value=1) as rb:
                self.assertEqual(self.g.cmd_restore([]), 1, failing)
            rb.assert_called_once()

    def test_an_exception_in_the_middle_rolls_back_instead_of_leaving_it_half_done(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        def disk_full_on_the_migration(script, timeout=900):
            if "db upgrade" in script:
                raise RuntimeError("disk full")
            return OK
        with mock.patch.object(self.g, "as_site", side_effect=disk_full_on_the_migration), mock.patch.object(self.g, "rollback", return_value=1) as rb:
            self.assertEqual(self.g.cmd_restore([]), 1)          # any failure after the deploy flag was set goes through rollback
        rb.assert_called_once()

    def test_a_link_swapped_in_during_the_preparation_stops_the_restore_before_anything_is_copied(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        with mock.patch.object(self.g, "app_is_plain", side_effect=[True, False]):
            self.assertEqual(self.g.cmd_restore([]), 1)
        self.assertFalse([c for c in self.calls if "rsync" in c])
        self.assertFalse(os.path.exists(self.g.DEPLOYING))

    def test_an_interrupt_in_the_middle_rolls_back_and_gives_the_signal_handlers_back(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        before = (signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGTERM))
        def ctrl_c_during_the_migration(script, timeout=900):
            if "db upgrade" in script:
                raise KeyboardInterrupt("ctrl-c")
            return OK
        with mock.patch.object(self.g, "as_site", side_effect=ctrl_c_during_the_migration), mock.patch.object(self.g, "rollback", return_value=1) as rb:
            self.assertEqual(self.g.cmd_restore([]), 1)
        rb.assert_called_once()
        self.assertEqual((signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGTERM)), before)

    def test_a_closed_terminal_or_kill_during_a_restore_is_turned_into_a_rollback(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        for sig in (signal.SIGHUP, signal.SIGTERM):
            def hangup(script, timeout=900, s=sig):
                if "db upgrade" in script:                       # in the middle of the restore, after its signal handlers are in place
                    os.kill(os.getpid(), s)
                    time.sleep(0.5)
                return OK
            with mock.patch.object(self.g, "as_site", side_effect=hangup), mock.patch.object(self.g, "rollback", return_value=1) as rb:
                self.assertEqual(self.g.cmd_restore([]), 1, sig)
            rb.assert_called_once()

    def test_the_package_list_from_before_is_kept_and_named_when_things_go_wrong_or_right(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))

        def site(script, timeout=900):
            return mock.Mock(returncode=0, stdout="flask==3.0.0\nsqlalchemy==2.0.0\n" if "pip freeze" in script else "", stderr="")
        with mock.patch.object(self.g, "as_site", side_effect=site), mock.patch.object(self.g, "health", return_value=500), \
                mock.patch.object(self.g, "rollback", return_value=1) as rb:
            self.g.cmd_restore([])
        freeze = rb.call_args.args[2]
        self.assertTrue(freeze.startswith(self.g.BACKUP_DIR) and freeze.endswith(".txt"))
        self.assertEqual(open(freeze).read(), "flask==3.0.0\nsqlalchemy==2.0.0\n")
        if os.name == "posix":
            self.assertEqual(oct(os.stat(freeze).st_mode & 0o777), oct(0o600))

    def test_a_rollback_tells_where_the_database_copy_and_the_package_list_are(self):
        self.checkpoint()
        backup = self.g.BACKUP_DIR + "/b.tgz"
        subprocess.run(["tar", "czf", backup, "-C", self.app, "."], check=True)
        with mock.patch.object(self.g, "log") as log:
            self.g.rollback(backup, "/db/copy.db", "/root/pip.txt")
        shown = " ".join(c.args[0] for c in log.call_args_list)
        self.assertIn("/db/copy.db", shown)
        self.assertIn("/root/pip.txt", shown)

    def test_a_rollback_does_not_touch_a_folder_that_has_become_a_link(self):
        self.checkpoint()
        with mock.patch.object(self.g, "app_is_plain", return_value=False):
            self.assertEqual(self.g.rollback("/whatever.tgz", ""), 1)
        self.assertEqual(self.calls, [])

    def test_restore_does_not_go_on_without_its_code_backup(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        before = self.g.files_map(self.app)
        self.tar_works = False
        self.assertEqual(self.g.cmd_restore([]), 1)
        self.assertEqual(self.g.files_map(self.app), before)
        self.assertFalse(os.path.exists(self.g.DEPLOYING))
        self.assertFalse([c for c in self.calls if "rsync" in c])

    def test_restore_does_not_go_on_without_its_database_backup(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        before = self.g.files_map(self.app)
        self.db_backup_works = False
        self.assertEqual(self.g.cmd_restore([]), 1)
        self.assertEqual(self.g.files_map(self.app), before)
        self.assertFalse(os.path.exists(self.g.DEPLOYING))
        self.assertFalse([c for c in self.calls if "rsync" in c])

    def test_a_rollback_whose_backup_cannot_be_unpacked_deletes_nothing(self):
        self.checkpoint()
        before = self.g.files_map(self.app)
        self.tar_works = False
        self.calls.clear()
        open(self.g.DEPLOYING, "w").close()
        self.assertEqual(self.g.rollback("/nonexistent.tgz", ""), 1)
        self.assertFalse([c for c in self.calls if "rsync" in c])                       # no rsync --delete from a half-empty folder
        self.assertEqual(self.g.files_map(self.app), before)
        self.assertFalse(os.path.exists(self.g.DEPLOYING))

    def test_a_rollback_puts_the_backup_back_and_restarts_only_the_dashboard(self):
        self.checkpoint()
        backup = self.g.BACKUP_DIR + "/b.tgz"
        subprocess.run(["tar", "czf", backup, "-C", self.app, "."], check=True)
        before = self.g.files_map(self.app)
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        self.put(self.app, "app/junk.py", "x")
        self.assertEqual(self.g.rollback(backup, "/db/copy.db"), 1)
        self.assertEqual(self.g.files_map(self.app), before)
        self.assertEqual([c for c in self.calls if c[0] == "systemctl"], [["systemctl", "restart", "ai-dashboard"]])

    def test_the_bookkeeping_failing_after_a_good_restore_is_said_not_undone(self):
        self.checkpoint()
        os.unlink(os.path.join(self.app, "app/project_notes.py"))
        real_git = self.g.git

        def git(*a, **kw):
            if a[0] == "log":
                raise RuntimeError("git broke")
            return real_git(*a, **kw)
        with mock.patch.object(self.g, "git", side_effect=git), mock.patch.object(self.g, "rollback") as rb, mock.patch.object(self.g, "log") as log:
            self.assertEqual(self.g.cmd_restore([]), 1)
        rb.assert_not_called()
        self.assertTrue(os.path.isfile(os.path.join(self.app, "app/project_notes.py")))
        self.assertIn("checkpoint could not be updated", " ".join(c.args[0] for c in log.call_args_list))

    def test_the_state_file_is_not_part_of_the_history(self):
        self.checkpoint()
        self.assertEqual(self.git("ls-files").count("state.json"), 0)
        self.assertEqual(self.git("ls-files").count(".lock"), 0)

    def test_unknown_revision_and_no_checkpoint_are_refused(self):
        self.assertEqual(self.g.cmd_restore([]), 1)
        self.checkpoint()
        self.assertEqual(self.g.cmd_restore(["nosuchrev"]), 1)


class RunTests(Base):
    def test_a_command_that_hangs_gives_up_with_a_timeout_answer(self):
        with mock.patch.object(self.g.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 5)):
            r = self.g.run(["sleep", "99"], timeout=5)
        self.assertEqual(r.returncode, 124)
        self.assertIn("timed out", r.stderr)

    def test_the_database_backup_is_made_by_the_site_user_and_named(self):
        with mock.patch.object(self.g, "run", return_value=OK) as run:
            dest = self.g.backup_db("20261023-100000")
        self.assertTrue(dest.endswith("app-before-restore-20261023-100000.db"))
        self.assertEqual(run.call_args.args[0][:4], ["runuser", "-u", self.g.SITE_USER, "--"])
        with mock.patch.object(self.g, "run", return_value=mock.Mock(returncode=1)):
            self.assertEqual(self.g.backup_db("t"), "")


class UsageTests(Base):
    def test_unknown_command_prints_help(self):
        with mock.patch("builtins.print") as pr:
            self.assertEqual(self.g.main(["x", "bogus"]), 2)
        self.assertIn("save SOURCE", pr.call_args.args[0])

    def test_save_needs_a_dashboard_folder(self):
        self.assertEqual(self.g.cmd_save([self.source, "m"]), 2)
        self.assertEqual(self.g.cmd_save([self.source]), 2)

    def test_the_timer_unit_leaves_time_for_slow_alerts(self):
        unit = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "ai-dashboard-guard.service")).read()
        limit = int(next(l for l in unit.splitlines() if l.startswith("TimeoutStartSec=")).split("=")[1])
        self.assertGreaterEqual(limit, 300)


if __name__ == "__main__":
    unittest.main()
