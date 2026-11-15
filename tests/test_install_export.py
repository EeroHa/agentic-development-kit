import contextlib
import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def head(self):
        return self.git("rev-parse", "HEAD")

    def export(self):
        return export.export_commit(self.repo, self.dest, self.head())

    def test_export_has_adk_and_policy_and_returns_the_typed_hash(self):
        commit = self.export()
        self.assertEqual(commit, self.head())
        self.assertTrue((self.dest / "adk" / "wall" / "policy.json").is_file())
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")
        self.assertFalse((self.dest / "other.txt").exists())

    def test_export_is_the_typed_commit_not_head_or_the_working_tree(self):
        first = self.head()
        (self.repo / "adk" / "a.py").write_text("a = 'second commit'\n")
        self.commit("second")
        (self.repo / "adk" / "a.py").write_text("a = 'uncommitted'\n")
        export.export_commit(self.repo, self.dest, first)
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")

    def test_export_wipes_the_destination_first(self):
        self.dest.mkdir()
        (self.dest / "stale.py").write_text("old")
        (self.dest / "adk").mkdir()
        (self.dest / "adk" / "gone.py").write_text("old")
        self.export()
        self.assertFalse((self.dest / "stale.py").exists())
        self.assertFalse((self.dest / "adk" / "gone.py").exists())

    def test_destination_is_private_and_owned_by_the_owner(self):
        self.export()
        self.assertEqual(self.dest.stat().st_mode & 0o077, 0)
        self.assertEqual(self.dest.stat().st_uid, os.geteuid())

    def test_install_sh_mode_is_kept(self):
        (self.repo / "install.sh").chmod(0o755)
        self.commit("exec")
        self.export()
        self.assertTrue(os.access(self.dest / "install.sh", os.X_OK))

    def test_export_refuses_a_commit_without_the_policy_file(self):
        self.git("rm", "-q", "adk/wall/policy.json")
        self.commit("drop policy")
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_export_refuses_a_hash_that_is_not_a_commit_in_the_repo(self):
        for bad in ("0" * 40, "a" * 40, self.git("rev-parse", "HEAD^{tree}"),
                    self.head()[:12], self.head().upper(), "HEAD", "main", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                export.export_commit(self.repo, self.dest, bad)
            self.assertFalse(self.dest.exists())

    def test_export_refuses_a_repo_with_a_replace_ref(self):
        blob = self.git("rev-parse", "HEAD:adk/a.py")
        evil = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
                              input="a = 'evil'\n", capture_output=True, text=True, check=True).stdout.strip()
        self.git("replace", blob, evil)
        # the bypass is real: git archive now serves the other bytes under the same hash
        env = {k: v for k, v in os.environ.items() if k != "GIT_NO_REPLACE_OBJECTS"}
        served = subprocess.run(["git", "-C", str(self.repo), "show", f"HEAD:adk/a.py"],
                                capture_output=True, text=True, env=env).stdout
        self.assertEqual(served, "a = 'evil'\n")
        with self.assertRaisesRegex(ValueError, "replace"):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_export_refuses_a_packed_replace_ref(self):
        blob = self.git("rev-parse", "HEAD:adk/a.py")
        self.git("replace", blob, self.git("rev-parse", "HEAD:adk/__init__.py"))
        self.git("pack-refs", "--all")
        self.assertFalse(any((self.repo / ".git" / "refs" / "replace").iterdir()))
        with self.assertRaisesRegex(ValueError, "replace"):
            self.export()

    def test_git_runs_with_replace_objects_off(self):
        blob = self.git("rev-parse", "HEAD:adk/a.py")
        self.git("replace", blob, self.git("rev-parse", "HEAD:adk/__init__.py"))
        out = export._git(self.repo, "cat-file", "-p", blob).decode()
        self.assertEqual(out, "a = 1\n")

    def test_export_refuses_when_export_ignore_hides_a_file(self):
        (self.repo / ".gitattributes").write_text("adk/a.py export-ignore\n")
        self.commit("ignore a.py")
        with self.assertRaisesRegex(ValueError, "missing"):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_export_refuses_export_ignore_set_in_info_attributes(self):
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_text("install.sh export-ignore\n")
        with self.assertRaisesRegex(ValueError, "missing|install.sh"):
            self.export()

    def test_export_refuses_export_subst_that_rewrites_a_file(self):
        (self.repo / ".gitattributes").write_text("adk/a.py export-subst\n")
        (self.repo / "adk" / "a.py").write_text("a = '$Format:%H$'\n")
        self.commit("subst")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.export()

    def test_export_succeeds_with_a_clean_attributes_setup(self):
        (self.repo / ".gitattributes").write_text("*.txt text\n")
        self.commit("attributes that drop nothing")
        self.export()
        self.assertTrue((self.dest / "adk" / "a.py").is_file())

    def test_export_refuses_a_symlink_in_the_commit(self):
        os.symlink("/etc/passwd", self.repo / "adk" / "link.py")
        self.commit("symlink")
        with self.assertRaisesRegex(ValueError, "plain files"):
            self.export()

    def test_verify_refuses_an_edited_extra_or_writable_export(self):
        self.export()
        export.verify_export(self.repo, self.dest, self.head())
        for what in ("edit", "extra", "writable", "link"):
            with self.subTest(what=what):
                self.export()
                a = self.dest / "adk" / "a.py"
                if what == "edit":
                    a.write_text("a = 2\n")
                elif what == "extra":
                    (self.dest / "adk" / "__pycache__").mkdir()
                    (self.dest / "adk" / "__pycache__" / "x.pyc").write_bytes(b"")
                elif what == "writable":
                    a.chmod(0o666)
                else:
                    a.unlink()
                    a.symlink_to("/etc/passwd")
                with self.assertRaises(ValueError):
                    export.verify_export(self.repo, self.dest, self.head())

    def test_verify_refuses_a_group_writable_folder_or_parent(self):
        self.export()
        self.dest.chmod(0o770)
        with self.assertRaisesRegex(ValueError, "writable"):
            export.verify_export(self.repo, self.dest, self.head())
        self.dest.chmod(0o700)
        self.tmp.chmod(0o770)
        self.addCleanup(self.tmp.chmod, 0o700)
        with self.assertRaisesRegex(ValueError, "writable"):
            export.verify_export(self.repo, self.dest, self.head())

    def test_verify_refuses_a_foreign_owner(self):
        self.export()
        real = os.lstat
        def fake(path, *a, **k):
            st = real(path, *a, **k)
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid + 1, *st[5:]))
        with mock.patch.object(os, "lstat", fake), self.assertRaisesRegex(ValueError, "not owned"):
            export.verify_export(self.repo, self.dest, self.head())

    def test_main_info_prints_the_subject_and_export_prints_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(export.main(["info", str(self.repo), self.head()]), 0)
            self.assertEqual(export.main(["export", str(self.repo), str(self.dest), self.head()]), 0)
            self.assertEqual(export.main(["verify", str(self.repo), str(self.dest), self.head()]), 0)
        self.assertEqual(out.getvalue(), "first\n")

    def test_main_refuses_and_exports_nothing_for_a_bad_hash(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(export.main(["export", str(self.repo), str(self.dest), "f" * 40]), 1)
            self.assertEqual(export.main(["bogus"]), 2)
        self.assertFalse(self.dest.exists())

    def test_script_runs_standalone_as_install_sh_runs_it(self):
        done = subprocess.run(["python3", "-I", str(REPO / "adk" / "install" / "export.py"),
                               "export", str(self.repo), str(self.dest), self.head()],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.dest / "adk" / "__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()
