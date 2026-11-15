"""install.sh up to the first prompt. The install path itself (export, sudo) is never run here."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


class InstallScriptTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        (self.repo / "adk" / "install").mkdir(parents=True)
        shutil.copy(REPO / "install.sh", self.repo / "install.sh")
        shutil.copy(REPO / "adk" / "install" / "export.py", self.repo / "adk" / "install" / "export.py")
        (self.repo / "adk" / "__init__.py").write_text("")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the approved subject")
        self.hash = self.git("rev-parse", "HEAD")
        self.work = self.tmp / "work"  # where the owner stands: not a git repo
        self.work.mkdir()

    def git(self, *args):
        env = {**os.environ, **GIT_ENV}
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    def extract(self, name="adk-install"):
        """The documented way: install.sh and adk/ from the approved commit, via git archive | tar."""
        dest = self.tmp / name
        dest.mkdir()
        archive = subprocess.run(["git", "-C", str(self.repo), "archive", self.hash, "install.sh", "adk"],
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
        return dest

    def run_script(self, script, *args, stdin="", cwd=None):
        env = {**os.environ, "HOME": str(self.home), "ADK_REPO": str(self.repo), "PATH": os.environ["PATH"]}
        env.pop("GIT_NO_REPLACE_OBJECTS", None)
        return subprocess.run(["bash", str(script), *args], input=stdin, capture_output=True, text=True,
                              env=env, cwd=cwd or self.work, timeout=60)

    def assert_nothing_installed(self):
        self.assertEqual(list(self.home.iterdir()), [])

    def test_no_arguments_prints_usage_and_exits_2(self):
        done = self.run_script(REPO / "install.sh")
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage:", done.stderr)
        self.assertIn('git -C ~/projects/agentic-dev-kit archive <hash> install.sh adk | tar -x -C "$TMPDIR/adk-install"'
                      ' && "$TMPDIR/adk-install/install.sh" <hash>', done.stderr)
        self.assert_nothing_installed()

    def test_a_missing_or_malformed_hash_prints_usage_and_exits_2(self):
        good = "a" * 40
        for args in (("uninstall",), ("a" * 39,), ("a" * 41,), (good.upper(),), ("g" * 40,), ("HEAD",), ("main",),
                     ("uninstall", "a" * 39), ("uninstall", "HEAD"), ("install", good), (good, good),
                     ("uninstall", good, good), ("-h",), (good + "\n",), ("",)):
            with self.subTest(args=args):
                done = self.run_script(REPO / "install.sh", *args)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("usage:", done.stderr)
                self.assert_nothing_installed()

    def test_a_hash_that_is_not_a_commit_in_the_repo_is_refused(self):
        done = self.run_script(self.repo / "install.sh", "a" * 40)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not a commit", done.stderr)
        self.assert_nothing_installed()

    def test_a_replace_ref_is_refused_before_anything_else(self):
        blob = self.git("rev-parse", f"{self.hash}:adk/__init__.py")
        other = self.git("rev-parse", f"{self.hash}:install.sh")
        self.git("replace", "-f", blob, other)
        done = self.run_script(self.extract() / "install.sh", self.hash, stdin="y\n")
        self.assertEqual(done.returncode, 1)
        self.assertIn("refs/replace", done.stderr)
        self.assertNotIn("Subject", done.stdout)
        self.assert_nothing_installed()

    def test_a_working_tree_copy_that_differs_from_the_commit_is_refused(self):
        for name, rel in (("install.sh", "install.sh"), ("export.py", "adk/install/export.py")):
            with self.subTest(file=name):
                dest = self.extract(f"edited-{name}")
                with open(dest / rel, "a") as f:
                    f.write("\n# edited after review\n")
                done = self.run_script(dest / "install.sh", self.hash, stdin="y\n")
                self.assertEqual(done.returncode, 1)
                self.assertIn("is not the copy in commit", done.stderr)
                self.assertNotIn("Subject", done.stdout)
                self.assert_nothing_installed()

    def test_a_copy_without_export_py_is_refused(self):
        dest = self.tmp / "lonely"
        dest.mkdir()
        shutil.copy(self.repo / "install.sh", dest / "install.sh")
        done = self.run_script(dest / "install.sh", self.hash, stdin="y\n")
        self.assertEqual(done.returncode, 1)
        self.assert_nothing_installed()

    def test_the_extracted_copy_shows_hash_and_subject_and_stops_on_anything_but_y(self):
        script = self.extract() / "install.sh"
        for action in ((), ("uninstall",)):
            for answer in ("n\n", "\n", "yes\n", "Y\n", ""):
                with self.subTest(action=action, answer=answer):
                    done = self.run_script(script, *action, self.hash, stdin=answer)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertIn(f"Commit:  {self.hash}", done.stdout)
                    self.assertIn("Subject: the approved subject", done.stdout)
                    self.assertIn("Nothing changed.", done.stdout)
                    self.assert_nothing_installed()

    def test_the_prompt_text_names_the_hash_and_the_subject(self):
        script = self.extract() / "install.sh"
        for action, verb in (((), "Install"), (("uninstall",), "Uninstall")):
            with self.subTest(verb=verb):
                done = self.run_script(script, *action, self.hash, stdin="n\n")
                self.assertIn(f"{verb} commit {self.hash} (the approved subject)? [y/N]", done.stdout)

    def test_the_working_tree_is_never_read(self):
        script = self.extract() / "install.sh"
        (self.repo / "adk" / "__init__.py").write_text("raise SystemExit('working tree ran')\n")
        done = self.run_script(script, self.hash, stdin="n\n", cwd=self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("working tree ran", done.stdout + done.stderr)

    def test_install_sh_has_no_reference_to_head_or_the_working_tree_copy_of_the_kit(self):
        text = (REPO / "install.sh").read_text()
        self.assertNotIn("HEAD", text)
        self.assertIn("GIT_NO_REPLACE_OBJECTS=1", text)
        self.assertNotIn("./adk", text)
        self.assertIn("-B -E -s -m adk", text)  # -E ignores PYTHONDONTWRITEBYTECODE, so -B keeps the export free of .pyc files


if __name__ == "__main__":
    unittest.main()
