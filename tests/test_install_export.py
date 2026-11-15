import contextlib
import hashlib
import io
import os
import stat
import subprocess
import tempfile
import unittest
import zlib
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

    def loose_path(self, oid):
        return self.repo / ".git" / "objects" / oid[:2] / oid[2:]

    def overwrite_loose(self, oid, kind, data):
        """Replace the loose object file named oid by one holding other bytes: what Claude could do."""
        path = self.loose_path(oid)
        path.chmod(0o644)
        path.write_bytes(zlib.compress(b"%s %d\0" % (kind.encode(), len(data)) + data))

    def forged_tree(self, tree_oid, old_blob, new_blob):
        raw = subprocess.run(["git", "-C", str(self.repo), "cat-file", "tree", tree_oid],
                             capture_output=True, check=True).stdout
        forged = raw.replace(bytes.fromhex(old_blob), bytes.fromhex(new_blob))
        self.assertNotEqual(raw, forged)
        return forged

    def write_blob(self, text):
        return subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"], input=text,
                              capture_output=True, text=True, check=True).stdout.strip()

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
        out = export.Repo(self.repo).git("cat-file", "-p", blob).decode()
        self.assertEqual(out, "a = 1\n")

    def test_export_ignores_gitattributes_export_ignore_and_export_subst(self):
        (self.repo / ".gitattributes").write_text("adk/a.py export-ignore\ninstall.sh export-subst\n")
        (self.repo / "install.sh").write_text("#!/bin/sh\n# $Format:%H$\n")
        self.commit("attributes")
        self.export()
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")
        self.assertIn("$Format:%H$", (self.dest / "install.sh").read_text())

    def test_info_attributes_is_not_read(self):
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_text("install.sh export-ignore\n* filter=evil\n")
        self.export()
        self.assertEqual((self.dest / "install.sh").read_text(), "#!/bin/sh\n")

    def test_export_succeeds_with_a_clean_attributes_setup(self):
        (self.repo / ".gitattributes").write_text("*.txt text\n")
        self.commit("attributes that drop nothing")
        self.export()
        self.assertTrue((self.dest / "adk" / "a.py").is_file())

    def test_export_of_a_packed_repository_passes(self):
        self.git("gc", "-q")
        self.export()
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")

    def test_every_exported_blob_id_and_mode_is_the_commits(self):
        (self.repo / "install.sh").chmod(0o755)
        self.commit("exec")
        self.export()
        self.assertEqual(stat.S_IMODE((self.dest / "install.sh").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.dest / "adk" / "a.py").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.dest / "adk").stat().st_mode), 0o755)

    def test_export_refuses_a_forged_subtree_under_the_same_hash(self):
        tree = self.git("rev-parse", "HEAD:adk")
        good = self.git("rev-parse", "HEAD:adk/a.py")
        evil = self.write_blob("a = 'evil'\n")
        self.overwrite_loose(tree, "tree", self.forged_tree(tree, good, evil))
        # git itself serves the forged bytes under the same name and does not complain when reading them
        self.assertEqual(self.git("rev-parse", "HEAD:adk"), tree)
        self.assertEqual(self.git("show", "HEAD:adk/a.py"), "a = 'evil'")
        # the walk alone (without fsck) refuses and names the tree
        with self.assertRaisesRegex(ValueError, f"tree {tree}"):
            export.read_commit(self.repo, self.head())
        for call in (lambda: self.export(), lambda: export.check_commit(self.repo, self.head())):
            with self.assertRaises(ValueError):
                call()
        self.assertFalse(self.dest.exists())

    def test_export_refuses_a_forged_blob_under_the_same_hash(self):
        blob = self.git("rev-parse", "HEAD:adk/a.py")
        self.overwrite_loose(blob, "blob", b"a = 'evil'\n")
        with self.assertRaisesRegex(ValueError, f"blob {blob}"):
            export.read_commit(self.repo, self.head())
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_export_refuses_a_forged_commit_object(self):
        head = self.head()
        raw = subprocess.run(["git", "-C", str(self.repo), "cat-file", "commit", head],
                             capture_output=True, check=True).stdout
        self.overwrite_loose(head, "commit", raw + b"x")
        with self.assertRaisesRegex(ValueError, f"commit {head}"):
            export.read_commit(self.repo, head)

    def test_verify_refuses_after_an_object_is_forged(self):
        self.export()
        blob = self.git("rev-parse", "HEAD:adk/a.py")
        self.overwrite_loose(blob, "blob", b"a = 'evil'\n")
        with self.assertRaises(ValueError):
            export.verify_export(self.repo, self.dest, self.head())

    def test_fsck_findings_are_refused(self):
        # a broken object that is not even in the export set: only fsck sees it
        other = self.git("rev-parse", "HEAD:other.txt")
        self.overwrite_loose(other, "blob", b"forged\n")
        export.read_commit(self.repo, self.head())  # the walk does not look at it
        with self.assertRaisesRegex(ValueError, "fsck"):
            self.export()

    def test_export_refuses_a_gitlink(self):
        self.git("update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},adk/sub")
        self.git("commit", "-q", "-m", "gitlink")
        with self.assertRaisesRegex(ValueError, "plain files"):
            self.export()
        self.assertFalse(self.dest.exists())

    def test_export_refuses_commondir_alternates_grafts_shallow(self):
        head = self.head()
        for rel, text in (("commondir", "evil\n"), ("objects/info/alternates", "/nonexistent\n"),
                          ("info/grafts", ""), ("shallow", "")):
            with self.subTest(rel=rel):
                path = self.repo / ".git" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                with self.assertRaises(ValueError):
                    export.check_commit(self.repo, head)
                with self.assertRaises(ValueError):
                    export.export_commit(self.repo, self.dest, head)
                self.assertFalse(self.dest.exists())
                path.unlink()
        self.export()  # honest again

    def test_an_empty_alternates_placeholder_is_harmless(self):
        # the Claude Code sandbox leaves an empty one in the kit's own repository
        path = self.repo / ".git" / "objects" / "info" / "alternates"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        self.export()
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")

    def test_a_symlink_or_a_whitespace_filled_alternates_is_not_the_empty_placeholder(self):
        path = self.repo / ".git" / "objects" / "info" / "alternates"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = self.head()
        (self.tmp / "empty").write_text("")
        path.symlink_to(self.tmp / "empty")
        with self.assertRaisesRegex(ValueError, "not empty"):
            export.export_commit(self.repo, self.dest, head)
        path.unlink()
        path.write_text("../../other/objects\n")
        with self.assertRaisesRegex(ValueError, "not empty"):
            export.export_commit(self.repo, self.dest, head)

    def test_the_refusal_names_the_file(self):
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "grafts").write_text("")
        with self.assertRaisesRegex(ValueError, "grafts"):
            export.export_commit(self.repo, self.dest, "a" * 40)

    def test_export_refuses_when_the_common_dir_differs_from_the_git_dir(self):
        wt = self.tmp / "linked"
        self.git("worktree", "add", "-q", "-b", "other", str(wt))
        with self.assertRaisesRegex(ValueError, "common"):
            export.check_commit(wt, self.head())

    def test_git_environment_variables_do_not_redirect_the_export(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
        env = {"GIT_DIR": str(other / ".git"), "GIT_OBJECT_DIRECTORY": str(other / "x"),
               "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(other), "GIT_COMMON_DIR": str(other / ".git")}
        head = self.head()
        with mock.patch.dict(os.environ, env):
            export.export_commit(self.repo, self.dest, head)
        self.assertEqual((self.dest / "adk" / "a.py").read_text(), "a = 1\n")

    def test_a_subject_with_terminal_escapes_is_printed_harmless(self):
        (self.repo / "adk" / "a.py").write_text("a = 3\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "evil \x1b[2Jsubject\n\nbody")
        subject = export.check_commit(self.repo, self.head())
        self.assertEqual(subject, "evil ?[2Jsubject")

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
