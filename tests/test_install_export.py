import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from adk.install import export

REPO = Path(__file__).resolve().parent.parent
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


class ExportTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.repo = self.tmp / "repo"
        (self.repo / "adk" / "wall").mkdir(parents=True)
        (self.repo / "adk" / "__init__.py").write_text("")
        (self.repo / "adk" / "wall" / "policy.json").write_text("{}\n")
        (self.repo / "adk" / "a.py").write_text("a = 1\n")
        (self.repo / "other.txt").write_text("not exported\n")
        (self.repo / "install.sh").write_text("#!/bin/sh\n")
        self.git("init", "-q", "-b", "main")
        self.commit("first")
        self.dest = self.tmp / "dest"

    def git(self, *args):
        env = {**os.environ, **GIT_ENV}
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def test_export_has_adk_and_policy_and_returns_full_hash(self):
        commit = export.export_head(self.repo, self.dest)
        self.assertEqual(commit, self.git("rev-parse", "HEAD"))
        self.assertEqual(len(commit), 40)
        self.assertTrue((self.dest / "adk" / "wall" / "policy.json").is_file())
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")
        self.assertFalse((self.dest / "other.txt").exists())

    def test_export_is_head_not_the_working_tree(self):
        (self.repo / "adk" / "a.py").write_text("a = 'changed after the commit'\n")
        export.export_head(self.repo, self.dest)
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")

    def test_export_wipes_the_destination_first(self):
        self.dest.mkdir()
        (self.dest / "stale.py").write_text("old")
        (self.dest / "adk").mkdir()
        (self.dest / "adk" / "gone.py").write_text("old")
        export.export_head(self.repo, self.dest)
        self.assertFalse((self.dest / "stale.py").exists())
        self.assertFalse((self.dest / "adk" / "gone.py").exists())

    def test_destination_is_private(self):
        export.export_head(self.repo, self.dest)
        self.assertEqual(self.dest.stat().st_mode & 0o077, 0)

    def test_export_refuses_a_repo_without_the_policy_file(self):
        self.git("rm", "-q", "adk/wall/policy.json")
        self.commit("drop policy")
        with self.assertRaises(ValueError):
            export.export_head(self.repo, self.dest)
        self.assertFalse(self.dest.exists())

    def test_clean_tree_has_no_dirty_paths(self):
        self.assertEqual(export.dirty_paths(self.repo), [])

    def test_modified_and_untracked_files_are_dirty(self):
        (self.repo / "adk" / "a.py").write_text("a = 2\n")
        (self.repo / "adk" / "new.py").write_text("")
        self.assertEqual(len(export.dirty_paths(self.repo)), 2)

    def test_changes_outside_the_kit_are_not_dirty(self):
        (self.repo / "other.txt").write_text("edited\n")
        self.assertEqual(export.dirty_paths(self.repo), [])

    def test_skip_worktree_does_not_hide_a_change(self):
        self.git("update-index", "--skip-worktree", "adk/a.py")
        (self.repo / "adk" / "a.py").write_text("a = 2\n")
        status = self.git("status", "--porcelain", "--", "adk")
        self.assertEqual(status, "")  # plain git status is blind to the edit
        self.assertEqual(export.dirty_paths(self.repo), ["hidden from git status: adk/a.py"])

    def test_main_prints_hash_and_branch(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = export.main([str(self.repo), str(self.dest)])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().split(), [self.git("rev-parse", "HEAD"), "main"])

    def test_main_refuses_a_dirty_tree_and_exports_nothing(self):
        (self.repo / "install.sh").write_text("#!/bin/sh\necho hacked\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = export.main([str(self.repo), str(self.dest)])
        self.assertEqual(code, 1)
        self.assertFalse(self.dest.exists())

    def test_script_runs_standalone_as_install_sh_runs_it(self):
        source = (REPO / "adk" / "install" / "export.py").read_text()
        done = subprocess.run(["python3", "-I", "-", str(self.repo), str(self.dest)],
                              input=source, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.dest / "adk" / "__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()
