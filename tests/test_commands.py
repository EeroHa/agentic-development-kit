import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from adk import cli
from adk.paths import resolve


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.p = resolve(home=self.home, root=self.tmp / "root")
        self.proj = self.tmp / "proj"
        self.proj.mkdir()

    def run_adk(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(
                [args[0], "--home", str(self.home), "--root", str(self.tmp / "root"), *args[1:]]
            )
        return code, out.getvalue(), err.getvalue()

    def settings(self):
        return json.loads(self.p.claude_user_settings.read_text())

    def test_allow_dir_adds_and_status_shows_it(self):
        code, out, _ = self.run_adk("allow-dir", str(self.proj))
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn(str(self.p.claude_user_settings), out)
        self.assertEqual(self.settings()["permissions"]["additionalDirectories"], [str(self.proj)])
        code, out, _ = self.run_adk("allow-dir", str(self.proj))
        self.assertEqual(code, 0)
        self.assertIn("already allowed", out)
        code, out, _ = self.run_adk("status", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertIn(str(self.proj), out)

    def test_status_empty_sections_print_none(self):
        code, out, _ = self.run_adk("status", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertEqual(out.count("(none)"), 4)

    def test_allow_dir_nonexistent_directory_exits_1(self):
        code, out, err = self.run_adk("allow-dir", str(self.tmp / "missing"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("missing", err)
        self.assertFalse(self.p.claude_user_settings.exists())

    def test_never_dir_removes_from_allow_list(self):
        self.run_adk("allow-dir", str(self.proj))
        code, out, _ = self.run_adk("never-dir", str(self.proj))
        self.assertEqual(code, 0)
        self.assertIn(str(self.p.lists_file), out)
        self.assertEqual(self.settings()["permissions"]["additionalDirectories"], [])
        self.assertEqual(json.loads(self.p.lists_file.read_text())["never_dirs"], [str(self.proj)])
        code, out, _ = self.run_adk("never-dir", "--remove", str(self.proj))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.p.lists_file.read_text())["never_dirs"], [])

    def test_allow_dir_refuses_home_and_secret_dirs(self):
        (self.home / ".ssh").mkdir()
        for target in (self.home, self.home / ".ssh", self.tmp):
            code, out, err = self.run_adk("allow-dir", str(target))
            self.assertEqual(code, 1, target)
            self.assertEqual(out, "")
            self.assertIn("refusing", err)
        self.assertFalse(self.p.claude_user_settings.exists())

    def test_reopen_refuses_secret_dir(self):
        (self.home / ".ssh").mkdir()
        code, out, err = self.run_adk("reopen", str(self.home / ".ssh"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn(".ssh", err)
        self.assertFalse(self.p.claude_user_settings.exists())

    def test_reopen_refuses_parent_of_secret_dir(self):
        (self.home / ".config").mkdir()
        code, _, err = self.run_adk("reopen", str(self.home / ".config"))
        self.assertEqual(code, 1)
        self.assertIn(".config", err)
        self.assertFalse(self.p.claude_user_settings.exists())

    def test_reopen_refuses_inside_secret_dir(self):
        (self.home / ".ssh" / "sub").mkdir(parents=True)
        code, _, _ = self.run_adk("reopen", str(self.home / ".ssh" / "sub"))
        self.assertEqual(code, 1)

    def test_reopen_refuses_home(self):
        code, _, err = self.run_adk("reopen", str(self.home))
        self.assertEqual(code, 1)
        self.assertIn("home directory itself", err)
        self.assertFalse(self.p.claude_user_settings.exists())

    def test_reopen_write_adds_to_both_lists(self):
        tool = self.home / ".cache-tool"
        tool.mkdir()
        code, out, _ = self.run_adk("reopen", str(tool), "--write")
        self.assertEqual(code, 0)
        fs = self.settings()["sandbox"]["filesystem"]
        self.assertEqual(fs["allowRead"], [str(tool)])
        self.assertEqual(fs["allowWrite"], [str(tool)])
        code, out, _ = self.run_adk("reopen", "--remove", str(tool))
        self.assertEqual(code, 0)
        fs = self.settings()["sandbox"]["filesystem"]
        self.assertEqual(fs["allowRead"], [])
        self.assertEqual(fs["allowWrite"], [])

    def test_reopen_read_only_leaves_write_list(self):
        tool = self.home / ".cache-tool"
        tool.mkdir()
        self.run_adk("reopen", str(tool))
        fs = self.settings()["sandbox"]["filesystem"]
        self.assertEqual(fs["allowRead"], [str(tool)])
        self.assertNotIn("allowWrite", fs)

    def test_integration_on_then_off(self):
        code, out, _ = self.run_adk("integration", "on", "chrome", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertIn(str(self.p.lists_file), out)
        code, out, _ = self.run_adk("status", "--project", str(self.proj))
        self.assertIn("chrome", out)
        code, out, _ = self.run_adk("integration", "on", "chrome", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        code, out, _ = self.run_adk("integration", "off", "chrome", "--project", str(self.proj))
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(self.p.lists_file.read_text())["integrations"][str(self.proj)], []
        )

    def test_invalid_settings_json_exits_1_and_changes_nothing(self):
        self.p.claude_user_settings.parent.mkdir(parents=True)
        self.p.claude_user_settings.write_text("{not json")
        code, out, err = self.run_adk("allow-dir", str(self.proj))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("settings.json", err)
        self.assertEqual(self.p.claude_user_settings.read_text(), "{not json")
        code, _, _ = self.run_adk("never-dir", str(self.proj))
        self.assertEqual(code, 1)
        self.assertFalse(self.p.lists_file.exists())


if __name__ == "__main__":
    unittest.main()
