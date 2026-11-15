import os
import stat
import tempfile
import unittest
from pathlib import Path

from adk.jsonio import InvalidJson, read_json, write_json_atomic


class JsonIoTest(unittest.TestCase):
    def test_read_json_missing_returns_default(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(read_json(Path(t) / "nope.json", {"a": 1}), {"a": 1})

    def test_read_json_invalid_raises_InvalidJson_naming_file(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "bad.json"
            f.write_text("{oops")
            with self.assertRaises(InvalidJson) as cm:
                read_json(f, {})
            self.assertIn(str(f), str(cm.exception))

    def test_write_json_atomic_sets_mode_0600_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "sub" / "x.json"
            write_json_atomic(f, {"k": [1]})
            self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)
            self.assertEqual(os.listdir(f.parent), ["x.json"])
            text = f.read_text()
            self.assertTrue(text.endswith("\n"))
            self.assertIn('  "k"', text)
            self.assertEqual(read_json(f, None), {"k": [1]})


if __name__ == "__main__":
    unittest.main()
