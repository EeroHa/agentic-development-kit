import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adk import state
from adk.hooks import common, outside_write_guard as guard
from adk.paths import resolve


def ev(cwd, path, tool="Write", project=None, **extra):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    e = {"cwd": str(cwd), "tool_name": tool, "tool_input": {key: str(path)}}
    e["_project_dir"] = str(project if project is not None else cwd)
    e.update(extra)
    return e


def decision(result):
    return result["hookSpecificOutput"]["permissionDecision"]


def reason(result):
    return result["hookSpecificOutput"]["permissionDecisionReason"]


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.p = resolve(home=self.tmp / "home", root=self.tmp / "root")
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.other = self.tmp / "other"
        self.other.mkdir()
        # Tests live under the system temp dir, so hide it from rule 4.
        patcher = mock.patch.object(guard, "_temp_roots", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_inside_project_returns_none(self):
        self.assertIsNone(guard.decide(ev(self.proj, self.proj / "a.py"), self.p))

    def test_relative_path_resolves_against_cwd(self):
        self.assertIsNone(guard.decide(ev(self.proj, "a.py"), self.p))
        r = guard.decide(ev(self.proj, "../other/a.py"), self.p)
        self.assertEqual(decision(r), "ask")

    def test_outside_unlisted_asks_and_names_both_commands(self):
        r = guard.decide(ev(self.proj, self.other / "f.txt"), self.p)
        self.assertEqual(decision(r), "ask")
        self.assertIn("adk allow-dir", reason(r))
        self.assertIn("adk never-dir", reason(r))
        self.assertIn(str(self.other / "f.txt"), reason(r))
        self.assertIn(f"adk allow-dir {self.other}", reason(r))

    def test_allowed_dir_returns_none(self):
        state.add_allowed_dir(self.p, self.other)
        self.assertIsNone(guard.decide(ev(self.proj, self.other / "f.txt"), self.p))

    def test_never_dir_denies(self):
        state.add_never_dir(self.p, self.other)
        r = guard.decide(ev(self.proj, self.other / "f.txt"), self.p)
        self.assertEqual(decision(r), "deny")
        self.assertIn("is on the never-allow list", reason(r))
        self.assertIn(str(self.other), reason(r))

    def test_project_inside_never_dir_still_none(self):
        state.add_never_dir(self.p, self.tmp)
        self.assertIsNone(guard.decide(ev(self.proj, self.proj / "a.py"), self.p))

    def test_symlink_inside_project_pointing_outside_asks(self):
        (self.proj / "link").symlink_to(self.other)
        r = guard.decide(ev(self.proj, self.proj / "link" / "new.txt"), self.p)
        self.assertEqual(decision(r), "ask")
        (self.proj / "flink").symlink_to(self.other / "f.txt")
        r = guard.decide(ev(self.proj, self.proj / "flink"), self.p)
        self.assertEqual(decision(r), "ask")

    def test_dotdot_escape_asks(self):
        r = guard.decide(ev(self.proj, f"{self.proj}/../other/file"), self.p)
        self.assertEqual(decision(r), "ask")

    def test_dotdot_inside_project_returns_none(self):
        (self.proj / "sub").mkdir()
        self.assertIsNone(guard.decide(ev(self.proj, f"{self.proj}/sub/../a.py"), self.p))

    def test_sibling_with_same_prefix_is_outside(self):
        sib = self.tmp / "proj-evil"
        sib.mkdir()
        r = guard.decide(ev(self.proj, sib / "a"), self.p)
        self.assertEqual(decision(r), "ask")

    def test_project_settings_file_is_denied_even_inside_project(self):
        for rel in (".claude/settings.json", ".claude/settings.local.json", ".mcp.json",
                    "sub/.claude/settings.json"):
            r = guard.decide(ev(self.proj, self.proj / rel), self.p)
            self.assertEqual(decision(r), "deny", rel)

    def test_settings_json_elsewhere_is_not_protected(self):
        self.assertIsNone(guard.decide(ev(self.proj, self.proj / "settings.json"), self.p))
        self.assertIsNone(guard.decide(ev(self.proj, self.proj / "x" / "settings.json"), self.p))

    def test_protected_symlink_pointing_at_settings_is_denied(self):
        (self.proj / ".claude").mkdir()
        (self.proj / "alias").symlink_to(self.proj / ".claude" / "settings.json")
        r = guard.decide(ev(self.proj, self.proj / "alias"), self.p)
        self.assertEqual(decision(r), "deny")

    def test_settings_symlinked_elsewhere_is_still_denied(self):
        (self.proj / ".claude").mkdir()
        (self.proj / ".claude" / "settings.json").symlink_to(self.other / "s.json")
        r = guard.decide(ev(self.proj, self.proj / ".claude" / "settings.json"), self.p)
        self.assertEqual(decision(r), "deny")

    def test_user_settings_and_kit_config_are_denied(self):
        targets = [
            self.p.claude_user_settings,
            self.p.config_file,
            self.p.config_dir / "new" / "deep.json",
            self.p.lib_dir / "adk" / "x.py",
            self.p.managed_dir / "managed-settings.d" / "50.json",
        ]
        for t in targets:
            # Even when the project is the home directory, they stay denied.
            r = guard.decide(ev(self.p.home, t), self.p)
            self.assertEqual(decision(r), "deny", str(t))

    def test_protected_even_when_dir_is_allowed(self):
        state.add_allowed_dir(self.p, self.p.config_dir)
        r = guard.decide(ev(self.proj, self.p.config_file), self.p)
        self.assertEqual(decision(r), "deny")

    def test_git_hooks_denied(self):
        r = guard.decide(ev(self.proj, self.proj / ".git" / "hooks" / "pre-commit"), self.p)
        self.assertEqual(decision(r), "deny")
        r = guard.decide(ev(self.proj, self.proj / ".git" / "hooks"), self.p)
        self.assertEqual(decision(r), "deny")

    def test_git_config_denied(self):
        r = guard.decide(ev(self.proj, self.proj / ".git" / "config"), self.p)
        self.assertEqual(decision(r), "deny")
        self.assertIsNone(guard.decide(ev(self.proj, self.proj / "src" / "config"), self.p))

    def test_notebook_path_is_used_for_NotebookEdit(self):
        r = guard.decide(ev(self.proj, self.other / "n.ipynb", tool="NotebookEdit"), self.p)
        self.assertEqual(decision(r), "ask")
        self.assertIsNone(guard.decide(ev(self.proj, self.proj / "n.ipynb", tool="NotebookEdit"), self.p))

    def test_notebook_falls_back_to_file_path(self):
        e = ev(self.proj, self.other / "n.ipynb")
        e["tool_name"] = "NotebookEdit"
        self.assertEqual(decision(guard.decide(e, self.p)), "ask")

    def test_missing_file_path_denies(self):
        for ti in ({}, {"file_path": ""}, {"file_path": 5}, {"other": "x"}, None, "str"):
            e = {"cwd": str(self.proj), "_project_dir": str(self.proj),
                 "tool_name": "Write", "tool_input": ti}
            r = guard.decide(e, self.p)
            self.assertEqual(decision(r), "deny", repr(ti))
            self.assertEqual(reason(r), "adk: write target missing from hook input")

    def test_missing_project_dir_denies(self):
        e = {"tool_name": "Write", "tool_input": {"file_path": str(self.proj / "a")}}
        r = guard.decide(e, self.p)
        self.assertEqual(decision(r), "deny")
        self.assertEqual(reason(r), "adk: project directory missing from hook input")

    def test_cwd_is_used_when_project_dir_absent(self):
        e = ev(self.proj, self.proj / "a")
        del e["_project_dir"]
        self.assertIsNone(guard.decide(e, self.p))

    def test_new_file_in_new_subdir_of_project_returns_none(self):
        self.assertIsNone(guard.decide(ev(self.proj, self.proj / "a" / "b" / "c.txt"), self.p))

    def test_cwd_in_subdirectory_still_treats_project_root_as_inside(self):
        sub = self.proj / "sub"
        sub.mkdir()
        e = ev(sub, self.proj / "root.txt", project=self.proj)
        self.assertIsNone(guard.decide(e, self.p))

    def test_scratchpad_dir_returns_none(self):
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        e = ev(self.proj, scratch / "x", scratchpad_dir=str(scratch))
        self.assertIsNone(guard.decide(e, self.p))

    def test_invalid_user_settings_raises_for_run_to_catch(self):
        self.p.claude_user_settings.parent.mkdir(parents=True)
        self.p.claude_user_settings.write_text("{not json")
        with self.assertRaises(Exception):
            guard.decide(ev(self.proj, self.other / "f"), self.p)


class RealTempTest(unittest.TestCase):
    def test_temp_path_returns_none(self):
        # No patching: the real system temp directory is exempt.
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            home = d / "home"
            p = resolve(home=home, root=d / "root")
            t = Path(tempfile.gettempdir()).resolve() / "adk-guard-probe.txt"
            e = ev("/nonexistent-project-dir", t)
            self.assertIsNone(guard.decide(e, p))


class RunTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.p = resolve(home=self.tmp / "home", root=self.tmp / "root")

    def call(self, text, decide=guard.decide, environ=None):
        out = io.StringIO()
        code = common.run(decide, stdin=io.StringIO(text), stdout=out,
                          environ={} if environ is None else environ, paths=self.p)
        return code, out.getvalue()

    def assert_error_deny(self, code, out, name=None):
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1)
        r = json.loads(out)
        self.assertEqual(decision(r), "deny")
        self.assertTrue(reason(r).startswith("adk wall hook error: "))
        if name:
            self.assertEqual(reason(r), f"adk wall hook error: {name}")

    def test_run_with_empty_stdin_prints_deny(self):
        self.assert_error_deny(*self.call(""))

    def test_run_with_non_json_prints_deny(self):
        self.assert_error_deny(*self.call("not json"), name="JSONDecodeError")

    def test_run_with_non_object_json_prints_deny(self):
        self.assert_error_deny(*self.call("[1, 2]"))

    def test_run_when_decide_raises_prints_deny(self):
        def boom(event, p):
            raise RuntimeError("secret detail")
        code, out = self.call("{}", decide=boom)
        self.assert_error_deny(code, out, name="RuntimeError")
        self.assertNotIn("secret detail", out)

    def test_run_prints_nothing_when_decide_returns_none(self):
        code, out = self.call("{}", decide=lambda e, p: None)
        self.assertEqual((code, out), (0, ""))

    def test_run_prints_one_json_line(self):
        code, out = self.call("{}", decide=lambda e, p: common.ask("why"))
        self.assertEqual(code, 0)
        self.assertEqual(out, json.dumps(common.ask("why")) + "\n")

    def test_run_sets_project_dir_from_environment(self):
        seen = {}
        def spy(event, p):
            seen.update(event)
        self.call('{"cwd": "/x"}', decide=spy, environ={"CLAUDE_PROJECT_DIR": "/proj"})
        self.assertEqual(seen["_project_dir"], "/proj")
        seen.clear()
        self.call('{"cwd": "/x"}', decide=spy, environ={"CLAUDE_PROJECT_DIR": ""})
        self.assertNotIn("_project_dir", seen)

    def test_run_ignores_project_dir_smuggled_in_event(self):
        # An event cannot pick its own project via the _project_dir key when the env is set.
        seen = {}
        self.call('{"_project_dir": "/evil"}', decide=lambda e, p: seen.update(e),
                  environ={"CLAUDE_PROJECT_DIR": "/proj"})
        self.assertEqual(seen["_project_dir"], "/proj")
        seen.clear()
        self.call('{"_project_dir": "/evil"}', decide=lambda e, p: seen.update(e))
        self.assertNotIn("_project_dir", seen)

    def test_run_denies_when_settings_file_is_invalid(self):
        self.p.claude_user_settings.parent.mkdir(parents=True)
        self.p.claude_user_settings.write_text("{bad")
        proj = self.tmp / "proj"
        proj.mkdir()
        text = json.dumps({"cwd": str(proj), "tool_name": "Write",
                           "tool_input": {"file_path": str(self.tmp / "o" / "f")}})
        with mock.patch.object(guard, "_temp_roots", return_value=[]):
            self.assert_error_deny(*self.call(text))

    def test_run_end_to_end_ask(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        text = json.dumps({"cwd": str(proj), "tool_name": "Write",
                           "tool_input": {"file_path": str(self.tmp / "o" / "f")}})
        with mock.patch.object(guard, "_temp_roots", return_value=[]):
            code, out = self.call(text)
        self.assertEqual(code, 0)
        self.assertEqual(decision(json.loads(out)), "ask")


if __name__ == "__main__":
    unittest.main()
