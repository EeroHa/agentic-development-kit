import json
import tempfile
import unittest
from pathlib import Path

from adk.install import apply_root
from adk.install.plan import (
    APPARMOR_PROFILE, FileChange, build_plan, render_diff, write_staging,
)
from adk.paths import resolve
from adk.wall.generate import build_managed_settings, load_policy

REPO = Path(__file__).resolve().parent.parent


class PlanTestBase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.p = resolve(home=self.home, root=self.root)

    def plan(self, repo=REPO, env_names=()):
        return build_plan(self.p, repo, env_names)

    def by_target(self, changes):
        return {c.target: c for c in changes}


class BuildPlanTest(PlanTestBase):
    def test_plan_contains_dropin_lib_files_launcher_and_profile(self):
        got = self.by_target(self.plan())
        p = self.p
        self.assertEqual(got[p.apparmor_profile].new_bytes, APPARMOR_PROFILE.encode())
        self.assertEqual(got[p.apparmor_profile].mode, 0o644)
        expected_dropin = json.dumps(
            build_managed_settings(load_policy(), p, []), indent=2) + "\n"
        self.assertEqual(got[p.managed_dropin].new_bytes, expected_dropin.encode())
        self.assertEqual(got[p.managed_dropin].mode, 0o644)
        lib_adk = p.lib_dir / "adk"
        for rel in ("__init__.py", "cli.py", "wall/policy.json",
                    "install/apply_root.py", "hooks/outside_write_guard.py"):
            self.assertEqual(got[lib_adk / rel].new_bytes, (REPO / "adk" / rel).read_bytes(), rel)
            self.assertEqual(got[lib_adk / rel].mode, 0o644)
        self.assertEqual(got[p.bin_link].link_to, p.lib_dir / "bin" / "adk")
        self.assertEqual(got[p.bin_link].new_bytes, b"")
        self.assertTrue(all(c.scope == "root" for t, c in got.items() if p.root in t.parents))

    def test_launcher_content_and_mode(self):
        launcher = self.by_target(self.plan())[self.p.lib_dir / "bin" / "adk"]
        self.assertEqual(launcher.mode, 0o755)
        self.assertEqual(
            launcher.new_bytes.decode(),
            "#!/usr/bin/python3 -I\n"
            "import sys\n"
            f'sys.path.insert(0, "{self.p.lib_dir}")\n'
            "from adk.cli import main\n"
            "sys.exit(main())\n",
        )

    def test_only_py_and_policy_files_are_copied_and_pycache_skipped(self):
        repo = self.tmp / "repo"
        (repo / "adk" / "sub" / "__pycache__").mkdir(parents=True)
        (repo / "adk" / "__pycache__").mkdir()
        (repo / "adk" / "a.py").write_text("a = 1\n")
        (repo / "adk" / "sub" / "b.py").write_text("b = 1\n")
        (repo / "adk" / "policy.json").write_text("{}\n")
        (repo / "adk" / "notes.txt").write_text("x")
        (repo / "adk" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0")
        (repo / "adk" / "sub" / "__pycache__" / "b.py").write_text("hidden\n")
        lib_adk = self.p.lib_dir / "adk"
        copied = {c.target for c in self.plan(repo) if lib_adk in c.target.parents}
        self.assertEqual(copied, {lib_adk / "a.py", lib_adk / "sub" / "b.py", lib_adk / "policy.json"})

    def test_stale_file_under_lib_dir_is_planned_for_removal(self):
        stale = self.p.lib_dir / "adk" / "old" / "gone.py"
        stale.parent.mkdir(parents=True)
        stale.write_text("old\n")
        kept = self.p.lib_dir / "adk" / "cli.py"
        kept.write_text("old cli\n")
        got = self.by_target(self.plan())
        self.assertIsNone(got[stale].new_bytes)
        self.assertEqual(got[stale].scope, "root")
        self.assertEqual(got[kept].new_bytes, (REPO / "adk" / "cli.py").read_bytes())

    def test_user_files_are_created_only_when_missing(self):
        got = self.by_target(self.plan())
        p = self.p
        self.assertEqual(json.loads(got[p.config_file].new_bytes)["weekly_reserve_percent"], 80)
        self.assertEqual(got[p.secrets_file].new_bytes, b"")
        self.assertEqual(got[p.secrets_file].mode, 0o600)
        self.assertEqual(json.loads(got[p.lists_file].new_bytes), {"never_dirs": [], "integrations": {}})
        self.assertEqual(got[p.canary_dir / "secret.txt"].new_bytes, b"ADK-CANARY-INSTALLED\n")
        self.assertEqual(got[p.canary_dir / "secret.txt"].mode, 0o600)
        self.assertTrue(all(got[t].scope == "user" for t in (p.config_file, p.secrets_file, p.lists_file)))

        p.config_dir.mkdir(parents=True)
        for f in (p.config_file, p.secrets_file, p.lists_file):
            f.write_text("mine")
        got = self.by_target(self.plan())
        for f in (p.config_file, p.secrets_file, p.lists_file):
            self.assertNotIn(f, got)
        self.assertIn(p.canary_dir / "secret.txt", got)


class RenderDiffTest(PlanTestBase):
    def test_render_diff_marks_new_and_unchanged(self):
        new = FileChange(self.root / "a.txt", b"hello\n", 0o644, "root")
        same = self.root / "b.txt"
        same.write_bytes(b"same\n")
        same.chmod(0o644)
        unchanged = FileChange(same, b"same\n", 0o644, "root")
        out = render_diff([new, unchanged])
        new_section, same_section = out.split(str(same))[0], out.split(str(same))[1]
        self.assertIn(f"{self.root / 'a.txt'} (root)", new_section)
        self.assertIn("(new file)", new_section)
        self.assertIn("+hello", new_section)
        self.assertIn("(unchanged)", same_section)
        self.assertNotIn("(new file)", same_section)

    def test_render_diff_shows_changed_lines_removal_and_link(self):
        f = self.root / "c.txt"
        f.write_bytes(b"one\ntwo\n")
        f.chmod(0o644)
        gone = self.root / "d.txt"
        gone.write_bytes(b"bye\n")
        link = FileChange(self.root / "ln", b"", 0o777, "root", link_to=self.root / "dest")
        out = render_diff([
            FileChange(f, b"one\nthree\n", 0o644, "root"),
            FileChange(gone, None, 0, "root"),
            link,
        ])
        self.assertIn("-two", out)
        self.assertIn("+three", out)
        self.assertIn("(removed)", out)
        self.assertIn(f"(link -> {self.root / 'dest'})", out)
        self.assertNotIn("(unchanged)", out)

    def test_render_diff_notes_binary_content_with_byte_count(self):
        out = render_diff([FileChange(self.root / "bin", b"\xff\xfe\x00", 0o644, "root")])
        self.assertIn("3 bytes", out)
        self.assertNotIn("+++", out)


class WriteStagingTest(PlanTestBase):
    def test_manifest_lists_root_changes_with_sequential_payloads(self):
        staging = self.p.config_dir / "staging"
        staging.mkdir(parents=True)
        (staging / "leftover").write_text("old")
        changes = self.plan()
        gone = FileChange(self.p.lib_dir / "adk" / "stale.py", None, 0, "root")
        out = write_staging(changes + [gone], staging, self.p.root)
        self.assertEqual(out, staging)
        self.assertFalse((staging / "leftover").exists())
        manifest = json.loads((staging / "manifest.json").read_text())
        root_changes = [c for c in changes if c.scope == "root"]
        self.assertEqual(len(manifest), len(root_changes) + 1)
        by_target = {e["target"]: e for e in manifest}
        self.assertTrue(all(not t.startswith("/") for t in by_target))
        dropin = by_target["etc/claude-code/managed-settings.d/50-adk-wall.json"]
        self.assertEqual(dropin["mode"], 0o644)
        self.assertEqual((staging / dropin["payload"]).read_bytes(), self.by_target(changes)[self.p.managed_dropin].new_bytes)
        link = by_target["usr/local/bin/adk"]
        self.assertEqual(link["link_to"], str(self.p.lib_dir / "bin" / "adk"))
        self.assertIsNone(link["payload"])
        removal = by_target["usr/local/lib/agentic-dev-kit/adk/stale.py"]
        self.assertTrue(removal["remove"])
        self.assertIsNone(removal["payload"])
        payloads = [e["payload"] for e in manifest if e["payload"]]
        self.assertEqual(payloads, [f"{i:04d}" for i in range(len(payloads))])
        self.assertFalse(any("config.json" in t or "canary" in t for t in by_target))

    def test_staging_manifest_is_accepted_by_apply_root(self):
        staging = write_staging(self.plan(), self.p.config_dir / "staging", self.p.root)
        changed = apply_root.apply(staging, self.p.root_backups_dir, self.p.root)
        self.assertIn(str(self.p.managed_dropin), changed)


if __name__ == "__main__":
    unittest.main()
