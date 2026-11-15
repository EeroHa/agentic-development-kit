import json
import os
import tempfile
import unittest
from pathlib import Path

from adk import state
from adk.paths import resolve


class StateTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.p = resolve(home=self.tmp / "home", root=self.tmp / "root")
        self.d = self.tmp / "proj"
        self.d.mkdir()

    def settings(self):
        return json.loads(self.p.claude_user_settings.read_text())

    def test_load_lists_defaults(self):
        self.assertEqual(state.load_lists(self.p), {"never_dirs": [], "integrations": {}})

    def test_add_allowed_dir_keeps_unrelated_settings_keys(self):
        self.p.claude_user_settings.parent.mkdir(parents=True)
        self.p.claude_user_settings.write_text(
            json.dumps({"theme": "dark", "permissions": {"deny": ["Read(x)"]}})
        )
        self.assertTrue(state.add_allowed_dir(self.p, self.d))
        s = self.settings()
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["permissions"]["deny"], ["Read(x)"])
        self.assertEqual(s["permissions"]["additionalDirectories"], [str(self.d)])
        self.assertEqual(state.allowed_dirs(self.p), [self.d])

    def test_add_allowed_dir_twice_returns_false_second_time(self):
        self.assertTrue(state.add_allowed_dir(self.p, self.d))
        self.assertFalse(state.add_allowed_dir(self.p, self.d))

    def test_no_rewrite_when_unchanged(self):
        state.add_allowed_dir(self.p, self.d)
        m = os.stat(self.p.claude_user_settings).st_mtime_ns
        self.assertFalse(state.add_allowed_dir(self.p, self.d))
        self.assertFalse(state.remove_allowed_dir(self.p, self.tmp / "other"))
        self.assertEqual(os.stat(self.p.claude_user_settings).st_mtime_ns, m)

    def test_remove_allowed_dir_leaves_empty_list(self):
        state.add_allowed_dir(self.p, self.d)
        self.assertTrue(state.remove_allowed_dir(self.p, self.d))
        self.assertEqual(self.settings()["permissions"]["additionalDirectories"], [])

    def test_invalid_user_settings_raises_and_file_is_untouched(self):
        self.p.claude_user_settings.parent.mkdir(parents=True)
        self.p.claude_user_settings.write_bytes(b"{not json")
        before = self.p.claude_user_settings.read_bytes()
        for fn in (
            lambda: state.add_allowed_dir(self.p, self.d),
            lambda: state.remove_allowed_dir(self.p, self.d),
            lambda: state.add_reopened(self.p, self.d),
            lambda: state.remove_reopened(self.p, self.d),
        ):
            with self.assertRaises(state.InvalidJson):
                fn()
        self.assertEqual(self.p.claude_user_settings.read_bytes(), before)

    def test_never_and_allow_are_mutually_exclusive(self):
        self.assertTrue(state.add_allowed_dir(self.p, self.d))
        self.assertTrue(state.add_never_dir(self.p, self.d))
        self.assertEqual(state.allowed_dirs(self.p), [])
        self.assertEqual(state.load_lists(self.p)["never_dirs"], [str(self.d)])
        self.assertFalse(state.add_never_dir(self.p, self.d))
        self.assertTrue(state.add_allowed_dir(self.p, self.d))
        self.assertEqual(state.load_lists(self.p)["never_dirs"], [])
        self.assertEqual(state.allowed_dirs(self.p), [self.d])

    def test_remove_never_dir(self):
        self.assertFalse(state.remove_never_dir(self.p, self.d))
        state.add_never_dir(self.p, self.d)
        self.assertTrue(state.remove_never_dir(self.p, self.d))
        self.assertEqual(state.load_lists(self.p)["never_dirs"], [])

    def test_set_integration_rejects_unknown_name(self):
        with self.assertRaises(ValueError):
            state.set_integration(self.p, self.d, "bogus", True)

    def test_set_integration_on_off(self):
        self.assertTrue(state.set_integration(self.p, self.d, "chrome", True))
        self.assertFalse(state.set_integration(self.p, self.d, "chrome", True))
        self.assertTrue(state.set_integration(self.p, self.d, "computer-use", True))
        self.assertEqual(
            sorted(state.integrations_for(self.p, self.d)), ["chrome", "computer-use"]
        )
        self.assertTrue(state.set_integration(self.p, self.d, "chrome", False))
        self.assertFalse(state.set_integration(self.p, self.d, "chrome", False))
        self.assertEqual(state.integrations_for(self.p, self.d), ["computer-use"])
        self.assertEqual(state.integrations_for(self.p, self.tmp / "x"), [])

    def test_dirs_are_stored_resolved(self):
        link = self.tmp / "link"
        link.symlink_to(self.d)
        state.add_allowed_dir(self.p, link)
        state.add_never_dir(self.p, link)
        state.set_integration(self.p, link, "chrome", True)
        self.assertEqual(self.settings()["permissions"]["additionalDirectories"], [])
        self.assertEqual(state.load_lists(self.p)["never_dirs"], [str(self.d)])
        self.assertEqual(state.integrations_for(self.p, self.d), ["chrome"])
        state.add_reopened(self.p, link)
        self.assertEqual(state.reopened(self.p), {"read": [str(self.d)], "write": []})

    def test_reopened_read_write_and_remove(self):
        self.assertEqual(state.reopened(self.p), {"read": [], "write": []})
        self.assertTrue(state.add_reopened(self.p, self.d, write=True))
        self.assertFalse(state.add_reopened(self.p, self.d, write=True))
        self.assertEqual(
            state.reopened(self.p), {"read": [str(self.d)], "write": [str(self.d)]}
        )
        self.assertTrue(state.remove_reopened(self.p, self.d))
        self.assertFalse(state.remove_reopened(self.p, self.d))
        self.assertEqual(state.reopened(self.p), {"read": [], "write": []})

    def test_add_reopened_write_upgrades_read_only(self):
        self.assertTrue(state.add_reopened(self.p, self.d))
        self.assertTrue(state.add_reopened(self.p, self.d, write=True))
        self.assertEqual(
            state.reopened(self.p), {"read": [str(self.d)], "write": [str(self.d)]}
        )


if __name__ == "__main__":
    unittest.main()
