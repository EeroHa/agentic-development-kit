import json
import tempfile
import unittest
from pathlib import Path

from adk import doctor
from adk.paths import resolve
from adk.wall.generate import build_managed_settings, load_policy


def _which(name):
    return "/usr/bin/" + name


class DoctorStaticChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "home").mkdir()
        self.p = resolve(home=base / "home", root=base / "root")
        self.env = {"PATH": "/usr/bin"}

    def install(self, env=None, mutate=None):
        doc = build_managed_settings(load_policy(), self.p, (env or self.env).keys())
        if mutate:
            mutate(doc)
        self.p.managed_dropin.parent.mkdir(parents=True)
        self.p.managed_dropin.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    def run_checks(self, env=None):
        return {c.name: c for c in doctor.run_static_checks(self.p, env if env is not None else self.env, _which)}

    def test_missing_dropin_fails_managed_installed(self):
        c = self.run_checks()["managed-installed"]
        self.assertFalse(c.ok)
        self.assertFalse(c.warning)

    def test_matching_dropin_passes(self):
        self.install()
        self.assertTrue(self.run_checks()["managed-installed"].ok)

    def test_out_of_date_dropin_fails_with_rerun_hint(self):
        self.install(mutate=lambda d: d["sandbox"].update(enabled=False))
        c = self.run_checks()["managed-installed"]
        self.assertFalse(c.ok)
        self.assertIn("wall is out of date, re-run the installer", c.detail)

    def test_new_credential_name_is_reported_without_value(self):
        self.install()
        env = {"PATH": "/usr/bin", "FOO_SECRET": "hunter2"}
        checks = doctor.run_static_checks(self.p, env, _which)
        c = {x.name: x for x in checks}["new-credential-names"]
        self.assertFalse(c.ok)
        self.assertIn("FOO_SECRET", c.detail)
        for x in checks:
            self.assertNotIn("hunter2", x.detail)

    def test_credential_denied_in_dropin_is_not_new(self):
        env = {"PATH": "/usr/bin", "FOO_SECRET": "hunter2"}
        self.install(env=env)
        self.assertTrue(self.run_checks(env)["new-credential-names"].ok)

    def test_api_key_in_env_fails(self):
        c = self.run_checks({"ANTHROPIC_API_KEY": "sk-x"})["api-key-in-env"]
        self.assertFalse(c.ok)
        self.assertIn("headless runs would be billed to the API", c.detail)
        self.assertNotIn("sk-x", c.detail)

    def test_version_difference_is_warning_not_failure(self):
        old = doctor._claude_version
        doctor._claude_version = lambda which: "9.9.9"
        self.addCleanup(setattr, doctor, "_claude_version", old)
        c = self.run_checks()["versions"]
        self.assertTrue(c.ok)
        self.assertTrue(c.warning)
        self.assertIn("9.9.9", c.detail)

    def test_closed_tool_folder_suggests_reopen(self):
        (self.p.home / ".cargo").mkdir()
        (self.p.home / ".cargo" / "private.txt").write_text("x")
        c = self.run_checks()["closed-tool-folders"]
        self.assertTrue(c.warning)
        self.assertIn(f"adk reopen {self.p.home / '.cargo'}", c.detail)

    def test_reopened_tool_folder_is_not_reported(self):
        (self.p.home / ".cargo").mkdir()
        self.p.claude_user_settings.parent.mkdir(parents=True)
        self.p.claude_user_settings.write_text(json.dumps(
            {"sandbox": {"filesystem": {"allowRead": [str(self.p.home / ".cargo")]}}}))
        c = self.run_checks()["closed-tool-folders"]
        self.assertTrue(c.ok)
        self.assertNotIn(".cargo", c.detail)

    def test_group_writable_dropin_fails_root_owned(self):
        self.install()
        self.p.managed_dropin.chmod(0o664)
        self.p.lib_dir.mkdir(parents=True)
        c = self.run_checks()["managed-root-owned"]
        self.assertFalse(c.ok)


if __name__ == "__main__":
    unittest.main()
