import ast
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from adk import cli
from adk.install import apply_root, apply_user
from adk.install.plan import FileChange, build_plan, write_staging
from adk.paths import resolve

REPO = Path(__file__).resolve().parent.parent
APPLY_ROOT_SOURCE = REPO / "adk" / "install" / "apply_root.py"


def snapshot(base: Path) -> dict:
    """Every path under base with its kind, content and mode."""
    out = {}
    for path in sorted(base.rglob("*")):
        rel = str(path.relative_to(base))
        if path.is_symlink():
            out[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            out[rel] = ("dir", stat.S_IMODE(path.stat().st_mode))
        else:
            out[rel] = ("file", path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    return out


class InstallTestBase(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "root"
        for d in ("etc/apparmor.d", "usr/local/bin", "usr/local/lib", "var/backups"):
            (self.root / d).mkdir(parents=True)
        (self.root / "etc/apparmor.d/other").write_text("unrelated\n")
        self.p = resolve(home=self.home, root=self.root)
        self.repo = self.tmp / "repo"
        (self.repo / "adk").mkdir(parents=True)
        self.set_repo_file("__init__.py", "")
        self.set_repo_file("a.py", "a = 1\n")

    def set_repo_file(self, rel, text):
        f = self.repo / "adk" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def stage(self):
        changes = build_plan(self.p, self.repo, [])
        return write_staging(changes, self.p.config_dir / "staging", self.p.root)

    def apply(self):
        return apply_root.apply(self.stage(), self.p.root_backups_dir, self.p.root)

    def backup_sets(self, backups=None):
        backups = backups or self.p.root_backups_dir
        return sorted(backups.iterdir()) if backups.exists() else []

    def manifest_staging(self, entries, payloads=None):
        staging = self.tmp / "evil-staging"
        staging.mkdir()
        for name, data in (payloads or {}).items():
            (staging / name).write_bytes(data)
        (staging / "manifest.json").write_text(json.dumps(entries))
        return staging

    @staticmethod
    def entry(target, payload="0000", mode=0o644, link_to=None, remove=False):
        return {"target": target, "payload": payload, "mode": mode,
                "link_to": link_to, "remove": remove}


class ApplyRootTest(InstallTestBase):
    def test_apply_root_writes_files_and_backs_up_replaced_ones(self):
        self.p.managed_dropin.parent.mkdir(parents=True)
        self.p.managed_dropin.write_text("old dropin\n")
        self.p.managed_dropin.chmod(0o600)
        changed = self.apply()
        self.assertIn(str(self.p.managed_dropin), changed)
        self.assertIn(str(self.p.apparmor_profile), changed)
        self.assertEqual(stat.S_IMODE(self.p.managed_dropin.stat().st_mode), 0o644)
        self.assertNotEqual(self.p.managed_dropin.read_text(), "old dropin\n")
        self.assertEqual((self.p.lib_dir / "adk" / "a.py").read_text(), "a = 1\n")
        launcher = self.p.lib_dir / "bin" / "adk"
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)
        self.assertEqual(os.readlink(self.p.bin_link), str(launcher))
        (backup,) = self.backup_sets()
        rel = "etc/claude-code/managed-settings.d/50-adk-wall.json"
        saved = backup / "files" / rel
        self.assertEqual(saved.read_text(), "old dropin\n")
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        created = json.loads((backup / "created.json").read_text())
        self.assertIn("etc/apparmor.d/bwrap", created)
        self.assertNotIn(rel, created)

    def test_apply_twice_changes_nothing_second_time_and_adds_no_backup(self):
        self.apply()
        before = snapshot(self.root)
        self.assertEqual(len(self.backup_sets()), 1)
        mtimes = {p: p.stat().st_mtime_ns for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.apply(), [])
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(len(self.backup_sets()), 1)
        for p, m in mtimes.items():
            if "backups" not in p.parts:
                self.assertEqual(p.stat().st_mtime_ns, m, p)

    def test_restore_after_two_applies_returns_original_tree(self):
        self.p.managed_dropin.parent.mkdir(parents=True)
        self.p.managed_dropin.write_text("old dropin\n")
        original = snapshot(self.root)
        self.apply()
        self.set_repo_file("a.py", "a = 2\n")
        self.set_repo_file("b.py", "b = 1\n")
        self.apply()
        self.assertEqual(len(self.backup_sets()), 2)
        self.assertEqual((self.p.lib_dir / "adk" / "a.py").read_text(), "a = 2\n")
        restored = apply_root.restore(self.p.root_backups_dir, self.p.root)
        self.assertTrue(restored)
        self.assertEqual(snapshot(self.root), original)
        self.assertEqual(self.backup_sets(), [])

    def test_stale_file_under_lib_dir_is_removed_and_comes_back_on_restore(self):
        old = self.p.lib_dir / "adk" / "gone" / "old.py"
        old.parent.mkdir(parents=True)
        old.write_text("old = 1\n")
        old.chmod(0o640)
        original = snapshot(self.root)
        changed = self.apply()
        self.assertFalse(old.exists())
        self.assertFalse(old.parent.exists() and any(old.parent.iterdir()))
        self.assertIn(str(old), changed)
        apply_root.restore(self.p.root_backups_dir, self.p.root)
        self.assertEqual(old.read_text(), "old = 1\n")
        self.assertEqual(stat.S_IMODE(old.stat().st_mode), 0o640)
        self.assertEqual(snapshot(self.root).keys(), original.keys())
        self.assertEqual(snapshot(self.root), original)

    def test_file_dropped_from_the_repo_between_applies_is_removed(self):
        self.set_repo_file("old.py", "old = 1\n")
        self.apply()
        pristine_lib = self.p.lib_dir / "adk" / "old.py"
        self.assertTrue(pristine_lib.exists())
        (self.repo / "adk" / "old.py").unlink()
        self.apply()
        self.assertFalse(pristine_lib.exists())

    def test_symlink_is_created_and_replaced_when_it_pointed_elsewhere(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.write_text("x")
        self.p.bin_link.symlink_to(elsewhere)
        self.apply()
        self.assertEqual(os.readlink(self.p.bin_link), str(self.p.lib_dir / "bin" / "adk"))
        self.assertTrue(elsewhere.exists())
        (backup,) = self.backup_sets()
        self.assertEqual(json.loads((backup / "links.json").read_text()),
                         {"usr/local/bin/adk": str(elsewhere)})
        apply_root.restore(self.p.root_backups_dir, self.p.root)
        self.assertEqual(os.readlink(self.p.bin_link), str(elsewhere))

    def test_symlink_replacing_a_regular_file_is_restored_as_file(self):
        self.p.bin_link.write_text("plain\n")
        self.p.bin_link.chmod(0o700)
        self.apply()
        self.assertTrue(self.p.bin_link.is_symlink())
        apply_root.restore(self.p.root_backups_dir, self.p.root)
        self.assertFalse(self.p.bin_link.is_symlink())
        self.assertEqual(self.p.bin_link.read_text(), "plain\n")
        self.assertEqual(stat.S_IMODE(self.p.bin_link.stat().st_mode), 0o700)

    def test_directories_it_creates_are_0755(self):
        self.apply()
        self.assertEqual(stat.S_IMODE((self.p.lib_dir / "bin").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.p.managed_dir.stat().st_mode), 0o755)


class ApplyRootRefusalTest(InstallTestBase):
    def assert_refused_and_nothing_written(self, staging):
        before = snapshot(self.root)
        with self.assertRaises(ValueError):
            apply_root.apply(staging, self.p.root_backups_dir, self.p.root)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(self.backup_sets(), [])

    def test_apply_root_refuses_staging_manifest_with_target_outside_allowed_roots(self):
        for target in ("etc/passwd", "etc/claude-codex/x", "usr/local/bin/other",
                       "usr/local/lib/agentic-dev-kit-evil/x", "etc/apparmor.d/other", "etc"):
            with self.subTest(target=target):
                staging = self.manifest_staging(
                    [self.entry("etc/claude-code/ok.json"), self.entry(target, "0001")],
                    {"0000": b"{}", "0001": b"x"})
                self.assert_refused_and_nothing_written(staging)
                for f in staging.iterdir():
                    f.unlink()
                staging.rmdir()

    def test_manifest_target_with_dotdot_is_refused(self):
        staging = self.manifest_staging(
            [self.entry("etc/claude-code/../../etc/passwd")], {"0000": b"x"})
        self.assert_refused_and_nothing_written(staging)

    def test_absolute_manifest_target_is_refused(self):
        staging = self.manifest_staging(
            [self.entry(str(self.p.managed_dropin))], {"0000": b"x"})
        self.assert_refused_and_nothing_written(staging)

    def test_link_target_outside_lib_dir_is_refused(self):
        for dest in ("/etc/passwd", str(self.root / "usr/local/lib/agentic-dev-kit/../../../etc/passwd"),
                     "relative/dest"):
            with self.subTest(dest=dest):
                staging = self.manifest_staging(
                    [self.entry("usr/local/bin/adk", None, 0o777, link_to=dest)])
                self.assert_refused_and_nothing_written(staging)
                (staging / "manifest.json").unlink()
                staging.rmdir()

    def test_payload_name_must_stay_inside_staging(self):
        (self.tmp / "outside").write_bytes(b"x")
        staging = self.manifest_staging([self.entry("etc/claude-code/a.json", "../outside")])
        self.assert_refused_and_nothing_written(staging)

    def test_bad_modes_are_refused(self):
        staging = self.manifest_staging(
            [self.entry("etc/claude-code/a.json", mode=0o4755)], {"0000": b"x"})
        self.assert_refused_and_nothing_written(staging)

    def test_command_line_refusal_exits_1_and_writes_nothing(self):
        staging = self.manifest_staging([self.entry("etc/passwd")], {"0000": b"x"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(apply_root.main(["apply", str(staging), str(self.tmp / "bk")]), 1)
            self.assertEqual(apply_root.main(["bogus"]), 1)
        self.assertIn("refusing", err.getvalue())
        self.assertFalse((self.tmp / "bk").exists())

    def test_apply_root_has_no_adk_imports(self):
        tree = ast.parse(APPLY_ROOT_SOURCE.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                self.assertNotEqual(name.split(".")[0], "adk", name)


class ApplyUserTest(InstallTestBase):
    def changes(self):
        return build_plan(self.p, self.repo, [])

    def test_user_files_not_overwritten_when_present(self):
        self.p.config_dir.mkdir(parents=True)
        self.p.config_file.write_text(json.dumps({"weekly_reserve_percent": 70}))
        apply_user.apply(self.changes(), self.p)
        self.assertEqual(json.loads(self.p.config_file.read_text()), {"weekly_reserve_percent": 70})
        self.assertTrue(self.p.lists_file.exists())

    def test_secrets_file_mode_is_0600(self):
        apply_user.apply(self.changes(), self.p)
        self.assertEqual(stat.S_IMODE(self.p.secrets_file.stat().st_mode), 0o600)
        self.assertEqual(self.p.secrets_file.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE((self.p.canary_dir / "secret.txt").stat().st_mode), 0o600)
        self.assertEqual((self.p.canary_dir / "secret.txt").read_text(), "ADK-CANARY-INSTALLED\n")

    def test_apply_user_ignores_root_scope_changes(self):
        apply_user.apply(self.changes(), self.p)
        self.assertFalse(self.p.lib_dir.exists())
        self.assertFalse(self.p.managed_dropin.exists())

    def test_apply_user_twice_changes_nothing_and_adds_no_backup(self):
        first = apply_user.apply(self.changes(), self.p)
        self.assertTrue(first)
        self.assertEqual(len(self.backup_sets(self.p.backups_dir)), 1)
        self.assertEqual(apply_user.apply(self.changes(), self.p), [])
        self.assertEqual(len(self.backup_sets(self.p.backups_dir)), 1)

    def test_restore_user_returns_original_tree(self):
        (self.home / ".config").mkdir()
        original = snapshot(self.home)
        apply_user.apply(self.changes(), self.p)
        self.p.canary_dir.joinpath("secret.txt").write_text("tampered")
        apply_user.apply(self.changes(), self.p)
        self.assertEqual(self.p.canary_dir.joinpath("secret.txt").read_text(), "ADK-CANARY-INSTALLED\n")
        apply_user.restore(self.p)
        self.assertEqual(snapshot(self.home), original)

    def test_user_target_outside_config_dir_is_refused(self):
        bad = FileChange(self.home / ".bashrc", b"evil\n", 0o600, "user")
        good = FileChange(self.p.config_dir / "ok.txt", b"ok\n", 0o600, "user")
        for target in (bad, FileChange(self.p.config_dir / ".." / ".bashrc", b"x", 0o600, "user")):
            with self.assertRaises(ValueError):
                apply_user.apply([good, target], self.p)
        self.assertFalse((self.p.config_dir / "ok.txt").exists())
        self.assertFalse((self.home / ".bashrc").exists())


class InstallCommandTest(InstallTestBase):
    def run_adk(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["install", *args, "--home", str(self.home), "--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def test_plan_prints_diff_and_staging_path_on_last_line(self):
        code, out, _ = self.run_adk("plan")
        self.assertEqual(code, 0)
        staging = self.p.config_dir / "staging"
        self.assertEqual(out.strip().splitlines()[-1], str(staging))
        self.assertIn("(new file)", out)
        self.assertTrue((staging / "manifest.json").exists())
        self.assertFalse(self.p.lib_dir.exists())
        self.assertFalse(self.p.config_file.exists())

    def test_apply_user_then_restore_user(self):
        code, out, _ = self.run_adk("apply-user")
        self.assertEqual(code, 0)
        self.assertIn(str(self.p.config_file), out)
        self.assertTrue(self.p.config_file.exists())
        code, _, _ = self.run_adk("restore-user")
        self.assertEqual(code, 0)
        self.assertFalse(self.p.config_file.exists())


if __name__ == "__main__":
    unittest.main()
