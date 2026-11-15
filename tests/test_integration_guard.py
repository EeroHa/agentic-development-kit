import io
import json
import tempfile
import unittest
from pathlib import Path

from adk import state
from adk.hooks import common, integration_guard as guard
from adk.paths import resolve

CHROME = "mcp__claude-in-chrome__tabs_context_mcp"
CU = "mcp__computer-use__screenshot"


def ev(cwd, tool, **extra):
    e = {"cwd": str(cwd), "tool_name": tool, "tool_input": {}}
    e.update(extra)
    return e


def decision(result):
    return result["hookSpecificOutput"]["permissionDecision"]


def reason(result):
    return result["hookSpecificOutput"]["permissionDecisionReason"]


class IntegrationGuardTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.p = resolve(home=self.tmp / "home", root=self.tmp / "root")
        self.proj = self.tmp / "proj"
        (self.proj / "sub").mkdir(parents=True)

    def test_other_tool_returns_none(self):
        for tool in ("Write", "Bash", "mcp__other__thing", "", None, 5):
            self.assertIsNone(guard.decide(ev(self.proj, tool), self.p), repr(tool))

    def test_browser_pane_tools_follow_the_chrome_switch(self):
        for tool in ("mcp__Claude_Browser__navigate", "mcp__remote-devices__Claude_Browser__navigate"):
            r = guard.decide(ev(self.proj, tool), self.p)
            self.assertEqual(decision(r), "deny", tool)
            self.assertIn("adk integration on chrome", reason(r))
        state.set_integration(self.p, self.proj, "chrome", True)
        for tool in ("mcp__Claude_Browser__navigate", "mcp__remote-devices__Claude_Browser__navigate"):
            self.assertIsNone(guard.decide(ev(self.proj, tool), self.p), tool)

    def test_remote_computer_tools_follow_the_computer_use_switch(self):
        tool = "mcp__remote-devices__computer_screenshot"
        r = guard.decide(ev(self.proj, tool), self.p)
        self.assertEqual(decision(r), "deny")
        self.assertIn("adk integration on computer-use", reason(r))
        state.set_integration(self.p, self.proj, "chrome", True)  # the other switch does not help
        self.assertEqual(decision(guard.decide(ev(self.proj, tool), self.p)), "deny")
        state.set_integration(self.p, self.proj, "computer-use", True)
        self.assertIsNone(guard.decide(ev(self.proj, tool), self.p))

    def test_other_remote_device_tools_are_not_touched(self):
        self.assertIsNone(guard.decide(ev(self.proj, "mcp__remote-devices__list_devices"), self.p))

    def test_chrome_off_denies_and_names_command(self):
        r = guard.decide(ev(self.proj, CHROME), self.p)
        self.assertEqual(decision(r), "deny")
        self.assertEqual(
            reason(r),
            "adk: chrome is off for this project. Eero can switch it on with "
            "`adk integration on chrome`.",
        )

    def test_chrome_on_for_project_returns_none(self):
        state.set_integration(self.p, self.proj, "chrome", True)
        self.assertIsNone(guard.decide(ev(self.proj, CHROME), self.p))

    def test_on_for_parent_applies_to_subdirectory(self):
        state.set_integration(self.p, self.proj, "chrome", True)
        self.assertIsNone(guard.decide(ev(self.proj / "sub", CHROME), self.p))
        # A sibling directory is not covered.
        other = self.tmp / "other"
        other.mkdir()
        self.assertEqual(decision(guard.decide(ev(other, CHROME), self.p)), "deny")

    def test_computer_use_is_separate_from_chrome(self):
        state.set_integration(self.p, self.proj, "chrome", True)
        r = guard.decide(ev(self.proj, CU), self.p)
        self.assertEqual(decision(r), "deny")
        self.assertIn("`adk integration on computer-use`", reason(r))
        state.set_integration(self.p, self.proj, "computer-use", True)
        self.assertIsNone(guard.decide(ev(self.proj, CU), self.p))

    def test_missing_cwd_denies(self):
        r = guard.decide({"tool_name": CHROME}, self.p)
        self.assertEqual(decision(r), "deny")
        self.assertEqual(reason(r), "adk: project directory missing from hook input")

    def test_project_dir_is_preferred_over_cwd(self):
        state.set_integration(self.p, self.proj, "chrome", True)
        other = self.tmp / "other"
        other.mkdir()
        self.assertIsNone(guard.decide(ev(other, CHROME, _project_dir=str(self.proj)), self.p))

    def test_run_end_to_end(self):
        text = json.dumps(ev(self.proj, CHROME))
        out = io.StringIO()
        code = common.run(guard.decide, stdin=io.StringIO(text), stdout=out,
                          environ={}, paths=self.p)
        self.assertEqual(code, 0)
        self.assertEqual(decision(json.loads(out.getvalue())), "deny")


if __name__ == "__main__":
    unittest.main()
