"""install.sh up to the first prompt. The install path itself (export, sudo) is never run here."""
import os
import shutil
import subprocess
import tempfile
import unittest
import zlib
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
        (self.repo / "adk" / "wall").mkdir()
        (self.repo / "adk" / "wall" / "policy.json").write_text("{}\n")
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

    def extract(self, name=None, only_script=False):
        """What the documented one-liner leaves: the commit's install.sh and adk/ under ~/.config/adk/install-<hash>/src."""
        name = name or self.hash
        base = self.home / ".config" / "adk" / f"install-{name}"
        dest = base / "src"
        dest.mkdir(parents=True, mode=0o700)
        base.chmod(0o700)
        paths = ("install.sh",) if only_script else ("install.sh", "adk")
        archive = subprocess.run(["git", "-C", str(self.repo), "archive", self.hash, *paths],
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
        dest.chmod(0o700)
        for folder, dirs, files in os.walk(dest):  # tar keeps the archive's group-write bits
            for d in dirs:
                os.chmod(os.path.join(folder, d), 0o755)
            for f in files:
                path = os.path.join(folder, f)
                os.chmod(path, 0o755 if os.stat(path).st_mode & 0o100 else 0o644)
        return dest

    def run_script(self, script, *args, stdin="", cwd=None):
        env = {**os.environ, "HOME": str(self.home), "ADK_REPO": str(self.repo), "PATH": os.environ["PATH"]}
        env.pop("GIT_NO_REPLACE_OBJECTS", None)
        return subprocess.run(["bash", str(script), *args], input=stdin, capture_output=True, text=True,
                              env=env, cwd=cwd or self.work, timeout=60)

    def assert_nothing_installed(self):
        """Nothing but the extracted copy itself is in the home folder: no src, no staging."""
        adk = self.home / ".config" / "adk"
        left = sorted(p.name for p in adk.iterdir()) if adk.exists() else []
        self.assertEqual([n for n in left if not n.startswith("install-")], [])
        self.assertEqual([p.name for p in self.home.iterdir() if p.name != ".config"], [])

    def test_no_arguments_prints_usage_and_exits_2(self):
        done = self.run_script(REPO / "install.sh")
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage:", done.stderr)
        self.assertIn('/usr/bin/python3 -I -B "$D/export.py" export "$R" "$D/src" "$H" && "$D/src/install.sh" "$H"',
                      done.stderr)
        self.assertIn('D=$HOME/.config/adk/install-$H', done.stderr)
        self.assertIn("fsck --no-dangling", done.stderr)
        self.assertNotIn("git archive", done.stderr)
        self.assertNotIn("TMPDIR", done.stderr)
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
        done = self.run_script(self.extract("a" * 40) / "install.sh", "a" * 40)
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
                shutil.rmtree(self.home / ".config", ignore_errors=True)
                dest = self.extract()
                with open(dest / rel, "a") as f:
                    f.write("\n# edited after review\n")
                done = self.run_script(dest / "install.sh", self.hash, stdin="y\n")
                self.assertEqual(done.returncode, 1)
                self.assertIn("is not the copy in commit", done.stderr)
                self.assertNotIn("Subject", done.stdout)
                self.assert_nothing_installed()

    def test_a_copy_without_export_py_is_refused(self):
        dest = self.extract(only_script=True)
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

    def test_a_copy_run_from_anywhere_but_the_export_folder_is_refused(self):
        other = self.tmp / "elsewhere"
        shutil.copytree(self.extract(), other)
        wrong = self.extract("b" * 40)
        for script in (self.repo / "install.sh", other / "install.sh", wrong / "install.sh"):
            with self.subTest(script=str(script)):
                done = self.run_script(script, self.hash, stdin="y\n")
                self.assertEqual(done.returncode, 1)
                self.assertIn(f".config/adk/install-{self.hash}/src", done.stderr)
                self.assertNotIn("Subject", done.stdout)
                self.assert_nothing_installed()

    def test_a_symlinked_export_folder_is_refused(self):
        real = self.extract()
        moved = self.tmp / "moved"
        real.rename(moved)
        real.symlink_to(moved)
        done = self.run_script(real / "install.sh", self.hash, stdin="y\n")
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("Subject", done.stdout)

    def test_an_export_with_an_extra_file_is_refused(self):
        dest = self.extract()
        (dest / "adk" / "extra.py").write_text("")
        done = self.run_script(dest / "install.sh", self.hash, stdin="y\n")
        self.assertEqual(done.returncode, 1)
        self.assertIn("is not exactly the export", done.stderr)
        self.assertNotIn("Subject", done.stdout)

    def test_a_forged_object_in_the_repo_is_refused_before_the_prompt(self):
        blob = self.git("rev-parse", f"{self.hash}:adk/__init__.py")
        path = self.repo / ".git" / "objects" / blob[:2] / blob[2:]
        path.chmod(0o644)
        path.write_bytes(zlib.compress(b"blob 5\0evil\n"))
        done = self.run_script(self.extract() / "install.sh", self.hash, stdin="y\n")
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("Subject", done.stdout)
        self.assertNotIn("[y/N]", done.stdout)

    def documented_lines(self, word):
        """The two lines of the documented install (or uninstall) one-liner in docs/operating-the-wall.md."""
        text = (REPO / "docs" / "operating-the-wall.md").read_text()
        blocks = [b.strip("\n") for b in text.split("```")[1::2] if "install-$H" in b]
        block = blocks[0] if word == "install" else blocks[1]
        self.assertEqual(len(block.splitlines()), 2)
        return block.replace("<hash>", self.hash)

    def test_the_documented_one_liner_runs_the_verified_export_and_stops_at_the_prompt(self):
        for word in ("install", "uninstall"):
            with self.subTest(word=word):
                line = self.documented_lines(word)
                self.assertEqual("uninstall" in line, word == "uninstall")
                env = {"HOME": str(self.home), "ADK_REPO": str(self.repo), "PATH": os.environ["PATH"]}
                done = subprocess.run(["bash", "-c", line], input="n\n", capture_output=True, text=True,
                                      env=env, cwd=self.work, timeout=120)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("Subject: the approved subject", done.stdout)
                self.assertIn("Nothing changed.", done.stdout)
                self.assertTrue((self.home / ".config" / "adk" / f"install-{self.hash}" / "export.py").is_file())
                self.assertTrue((self.home / ".config" / "adk" / f"install-{self.hash}" / "src" / "install.sh").is_file())
                self.assert_nothing_installed()

    def test_the_usage_text_and_the_docs_show_the_same_one_liner(self):
        usage = subprocess.run(["bash", str(REPO / "install.sh")], capture_output=True, text=True).stderr
        line = self.documented_lines("install").replace(self.hash, "<hash>")
        for part in line.splitlines():
            self.assertIn(part.strip(), usage)

    def test_the_one_liner_refuses_a_forged_repository_before_running_anything(self):
        blob = self.git("rev-parse", f"{self.hash}:adk/install/export.py")
        path = self.repo / ".git" / "objects" / blob[:2] / blob[2:]
        path.chmod(0o644)
        evil = b"print('EVIL ran')\n"
        path.write_bytes(zlib.compress(b"blob %d\0" % len(evil) + evil))
        env = {"HOME": str(self.home), "ADK_REPO": str(self.repo), "PATH": os.environ["PATH"]}
        done = subprocess.run(["bash", "-c", self.documented_lines("install")], input="y\n", capture_output=True,
                              text=True, env=env, cwd=self.work, timeout=120)
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("EVIL", done.stdout + done.stderr)
        self.assertFalse((self.home / ".config" / "adk" / f"install-{self.hash}").exists())

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
        self.assertNotIn("TMPDIR", text)
        self.assertNotIn("archive", text)
        self.assertIn("-B -E -s -m adk", text)  # -E ignores PYTHONDONTWRITEBYTECODE, so -B keeps the export free of .pyc files


if __name__ == "__main__":
    unittest.main()
