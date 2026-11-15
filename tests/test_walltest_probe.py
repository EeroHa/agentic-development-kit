import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROBE = Path(__file__).resolve().parent.parent / "adk" / "walltest" / "probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("adk_probe_under_test", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.probe = load_probe()
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name)

    def test_write_to_a_new_file_creates_it_and_removes_it(self):
        target = self.tmp / "new.json"
        self.probe.try_write("w", str(target))
        self.assertTrue(self.probe.REPORT["w"]["ok"])
        self.assertFalse(target.exists())

    def test_write_to_an_existing_file_appends_instead_of_failing_on_exists(self):
        target = self.tmp / "config"
        target.write_text("[core]\n")
        self.probe.try_write("w", str(target))
        self.assertTrue(self.probe.REPORT["w"]["ok"])  # an allowed write is reported as allowed
        self.assertEqual(target.read_text(), "[core]\nadk write probe\n")

    def test_write_that_cannot_happen_is_reported_as_blocked(self):
        target = self.tmp / "ro" / "x"
        target.parent.mkdir()
        target.parent.chmod(0o500)
        self.addCleanup(target.parent.chmod, 0o700)
        if os.access(target.parent, os.W_OK):
            self.skipTest("running as a user that ignores directory modes")
        self.probe.try_write("w", str(target))
        self.assertFalse(self.probe.REPORT["w"]["ok"])

    def test_read_of_a_process_environment_flags_a_leaked_canary(self):
        proc = subprocess.Popen(["sleep", "30"], env={**os.environ, "ADK_PROC_CANARY": "ADK-" + "CANARY-0123"})
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self.probe.read_open("proc:environ", f"/proc/{proc.pid}/environ")
        self.assertTrue(self.probe.REPORT["proc:environ"]["ok"])
        self.assertTrue(self.probe.REPORT["proc:environ"]["leaked"])
        self.assertNotIn("0123", self.probe.REPORT["proc:environ"]["detail"])

    def test_read_of_a_missing_process_is_blocked(self):
        self.probe.read_open("proc:environ", "/proc/999999999/environ")
        self.assertFalse(self.probe.REPORT["proc:environ"]["ok"])


if __name__ == "__main__":
    unittest.main()
