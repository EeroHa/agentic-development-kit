import unittest
from pathlib import Path

from adk.paths import resolve


class PathsTest(unittest.TestCase):
    def test_resolve_uses_home_and_root_prefix(self):
        p = resolve(home=Path("/h"), root=Path("/r"))
        self.assertEqual(
            p.managed_dropin,
            Path("/r/etc/claude-code/managed-settings.d/50-adk-wall.json"),
        )
        self.assertEqual(p.lists_file, Path("/h/.config/adk/lists.json"))
        self.assertEqual(p.claude_user_settings, Path("/h/.claude/settings.json"))
        self.assertEqual(p.bin_link, Path("/r/usr/local/bin/adk"))
        self.assertEqual(p.root_backups_dir, Path("/r/var/backups/agentic-dev-kit"))

    def test_default_home_is_path_home(self):
        self.assertEqual(resolve().home, Path.home())


if __name__ == "__main__":
    unittest.main()
