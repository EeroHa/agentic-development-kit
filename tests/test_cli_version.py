import contextlib
import io
import unittest

from adk import cli


class TestCliVersion(unittest.TestCase):
    def test_version_flag_prints_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("0.1.0", out.getvalue())

    def test_unknown_command_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            cli.main(["no-such-command"])
        self.assertEqual(cm.exception.code, 2)
