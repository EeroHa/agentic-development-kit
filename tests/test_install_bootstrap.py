"""adk/install/bootstrap.py: the first verified read of the one-liner. Temp repositories only; nothing is installed."""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO / "adk" / "install" / "bootstrap.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}
EVIL = b"open(__file__ + '.marker', 'w').write('EVIL ran')\n"


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.repo = self.tmp / "repo"
        (self.repo / "adk" / "install").mkdir(parents=True)
        (self.repo / "adk" / "wall").mkdir()
        shutil.copy(REPO / "install.sh", self.repo / "install.sh")
        shutil.copy(REPO / "adk" / "install" / "export.py", self.repo / "adk" / "install" / "export.py")
        (self.repo / "adk" / "__init__.py").write_text("")
        (self.repo / "adk" / "wall" / "policy.json").write_text("{}\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the approved subject")
        self.hash = self.git("rev-parse", "HEAD")
        self.dest = self.tmp / "home" / ".config" / "adk" / f"install-{self.hash}"

    def git(self, *args):
        env = {**os.environ, **GIT_ENV}
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    def overwrite_loose(self, oid, kind, data):
        path = self.repo / ".git" / "objects" / oid[:2] / oid[2:]
        path.chmod(0o644)
        path.write_bytes(zlib.compress(b"%s %d\0" % (kind.encode(), len(data)) + data))

    def raw(self, kind, oid):
        return subprocess.run(["git", "-C", str(self.repo), "cat-file", kind, oid],
                              capture_output=True, check=True).stdout

    def run_bootstrap(self, commit=None, path=None):
        env = {**os.environ, "PATH": path or os.environ["PATH"]}
        return subprocess.run([os.environ.get("PYTHON", "python3"), "-I", "-B", "-E", "-s", str(BOOTSTRAP),
                               str(self.repo), str(self.dest), self.hash if commit is None else commit],
                              capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)

    def assert_refused(self, done):
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("bootstrap refused", done.stderr)
        self.assertFalse(self.dest.exists())
        self.assertFalse((self.dest / "export.py.marker").exists())

    def test_an_honest_repository_passes_and_runs_the_verified_export(self):
        done = self.run_bootstrap()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = (REPO / "adk" / "install" / "export.py").read_bytes()
        self.assertEqual((self.dest / "export.py").read_bytes(), want)
        self.assertTrue((self.dest / "src" / "install.sh").is_file())
        self.assertTrue((self.dest / "src" / "adk" / "wall" / "policy.json").is_file())
        self.assertEqual(stat.S_IMODE(self.dest.stat().st_mode), 0o700)

    def test_a_forged_export_py_blob_under_the_same_name_is_refused(self):
        blob = self.git("rev-parse", f"{self.hash}:adk/install/export.py")
        self.overwrite_loose(blob, "blob", EVIL)
        self.assertEqual(self.git("show", f"{self.hash}:adk/install/export.py"), EVIL.decode().strip())  # git serves it
        done = self.run_bootstrap()
        self.assert_refused(done)
        self.assertIn(f"blob {blob} does not match its own hash", done.stderr)

    def test_a_forged_subtree_that_points_export_py_at_another_blob_is_refused(self):
        evil = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"], input=EVIL,
                              capture_output=True, check=True).stdout.decode().strip()
        adk_tree = self.git("rev-parse", f"{self.hash}:adk")
        install_tree = self.git("rev-parse", f"{self.hash}:adk/install")
        good = self.git("rev-parse", f"{self.hash}:adk/install/export.py")
        raw_adk, raw_install = self.raw("tree", adk_tree), self.raw("tree", install_tree)
        forged_install = raw_install.replace(bytes.fromhex(good), bytes.fromhex(evil))
        forged_install_id = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "-t", "tree", "--stdin"],
                                           input=forged_install, capture_output=True, check=True).stdout.decode().strip()
        forged_adk = raw_adk.replace(bytes.fromhex(install_tree), bytes.fromhex(forged_install_id))
        self.assertNotEqual(raw_install, forged_install)
        self.assertNotEqual(raw_adk, forged_adk)
        for tree, forged in ((install_tree, forged_install), (adk_tree, forged_adk)):
            with self.subTest(tree=tree):
                self.overwrite_loose(tree, "tree", forged)
                done = self.run_bootstrap()
                self.assert_refused(done)
                self.assertIn(f"tree {tree} does not match its own hash", done.stderr)
                self.overwrite_loose(tree, "tree", {install_tree: raw_install, adk_tree: raw_adk}[tree])

    def test_a_forged_commit_is_refused(self):
        raw = self.raw("commit", self.hash)
        self.overwrite_loose(self.hash, "commit", raw + b"x")
        done = self.run_bootstrap()
        self.assert_refused(done)
        self.assertIn("does not match its own hash", done.stderr)

    def test_a_missing_or_malformed_hash_is_refused(self):
        for bad in ("0" * 40, self.hash[:12], self.hash.upper(), "HEAD", "main", ""):
            with self.subTest(bad=bad):
                self.assert_refused(self.run_bootstrap(bad))

    def test_a_commit_without_export_py_is_refused(self):
        self.git("rm", "-q", "adk/install/export.py")
        self.git("commit", "-q", "-m", "drop it")
        new = self.git("rev-parse", "HEAD")
        self.dest = self.dest.with_name(f"install-{new}")
        done = self.run_bootstrap(new)
        self.assert_refused(done)
        self.assertIn("is not a plain entry", done.stderr)

    def test_a_destination_not_named_after_the_hash_is_refused(self):
        self.dest = self.tmp / "home" / ".config" / "adk" / "somewhere"
        done = self.run_bootstrap()
        self.assert_refused(done)
        self.assertIn("named install-<hash>", done.stderr)

    def test_the_bootstrap_reads_through_one_git_batch_session(self):
        log = self.tmp / "git.log"
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        real = shutil.which("git")
        script = fakebin / "git"
        script.write_text(f'#!/bin/sh\necho "$*" >>"{log}"\nexec "{real}" "$@"\n')
        script.chmod(0o755)
        done = self.run_bootstrap(path=f"{fakebin}:{os.environ['PATH']}")
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = log.read_text().splitlines()
        self.assertTrue(lines[0].endswith("cat-file --batch"), lines[0])
        self.assertEqual(sum("--batch" in line for line in lines), 1)

    def test_the_bootstrap_does_not_use_cat_file_blob_or_show_to_get_code(self):
        text = BOOTSTRAP.read_text()
        for banned in ('"show"', "cat-file blob", "archive", "fsck"):
            self.assertNotIn(banned, text)

    def test_the_documented_one_liner_is_this_file_and_runs_no_git_command_of_its_own(self):
        text = (REPO / "docs" / "operating-the-wall.md").read_text()
        block = [b.strip("\n") for b in text.split("```")[1::2] if "install-$H" in b][0]
        first, second, rest = block.split("\n", 2)
        self.assertEqual(rest, BOOTSTRAP.read_text().rstrip("\n") + "\nPY")
        for line in (first, second):
            self.assertNotIn("git ", line.replace("agentic-dev-kit", ""))
        self.assertTrue(second.startswith("/usr/bin/python3 -I -B -E -s - "))


if __name__ == "__main__":
    unittest.main()
