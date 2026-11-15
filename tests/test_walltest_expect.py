import shutil
import tempfile
import unittest
from pathlib import Path

from adk.paths import resolve
from adk.walltest.expect import MUST_FAIL, MUST_PASS, evaluate
from adk.walltest.fixture import build_fixture, remove_fixture
from adk.walltest.runner import ToolCall, Transcript


def entry(ok, leaked=False, detail=""):
    return {"ok": ok, "leaked": leaked, "detail": detail}


def good_report(f):
    report = {}
    for name in f.canaries:
        report[f"read-open:{name}"] = entry(False)
        report[f"read-cat:{name}"] = entry(False)
    report["grep:canary-dir"] = entry(False)
    report["grep:project"] = entry(False)
    for name in MUST_FAIL:
        report[name] = entry(False)
    for name in MUST_PASS:
        report[name] = entry(True)
    report["net:registry"] = entry(True, detail="code=200")
    report["net:example"] = entry(False, detail="code=403")
    report["port:inside"] = entry(True)
    report["port:outside"] = entry(False)
    return report


def tool_call(name, result="", is_error=False, **inp):
    return ToolCall(name, inp, result, is_error)


def good_calls(f):
    calls = [tool_call("Read", "denied", True, file_path=str(path)) for path in f.canaries.values()]
    calls += [
        tool_call("Grep", "No matches", pattern="ADK-CANARY-", path=str(f.project)),
        tool_call("Grep", "No matches", pattern="ADK-CANARY-", path=str(f.canaries["outside/secret.txt"].parent)),
        tool_call("Glob", "", pattern="**/.env*"),
        tool_call("Read", f.control, file_path=str(f.project / "README.md")),
    ]
    calls += [tool_call("Write", "denied", True, file_path=str(path), content="x")
              for name, path in f.write_targets.items() if name.startswith("tool:")]
    return calls


def good_run(f, calls=None):
    return Transcript(raw="clean", calls=good_calls(f) if calls is None else calls, final_text="done")


class ExpectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.p = resolve(home=self.tmp / "home", root=self.tmp / "root")
        self.f = build_fixture(self.tmp / "work", self.p)
        self.addCleanup(remove_fixture, self.f, self.p)

    def failed(self, checks):
        return [c for c in checks if not c.ok and not c.warning]

    def test_all_good_passes(self):
        checks = evaluate(good_report(self.f), good_run(self.f), self.f)
        self.assertEqual(self.failed(checks), [])
        self.assertTrue(checks)

    def test_marker_in_raw_fails(self):
        run = good_run(self.f)
        run.raw = "something " + self.f.marker
        checks = evaluate(good_report(self.f), run, self.f)
        self.assertIn("marker-absent:tool", [c.name for c in self.failed(checks)])

    def test_marker_in_bash_raw_fails(self):
        checks = evaluate(good_report(self.f), good_run(self.f), self.f, bash_raw=self.f.marker)
        self.assertIn("marker-absent:bash", [c.name for c in self.failed(checks)])

    def test_leaked_read_fails(self):
        report = good_report(self.f)
        report["read-cat:.env"] = entry(True, leaked=True)
        names = [c.name for c in self.failed(evaluate(report, good_run(self.f), self.f))]
        self.assertEqual(names, ["no-leak:read-cat:.env"])

    def test_missing_read_attempt_fails_with_attempt_missing(self):
        calls = [c for c in good_calls(self.f)
                 if c.input.get("file_path") != str(self.f.canaries["keys/server.pem"])]
        checks = evaluate(good_report(self.f), good_run(self.f, calls), self.f)
        self.assertEqual([c.name for c in self.failed(checks)], ["attempt-missing:keys/server.pem"])

    def test_read_attempt_that_succeeded_fails(self):
        calls = good_calls(self.f)
        calls[0] = tool_call("Read", "content", False, file_path=calls[0].input["file_path"])
        checks = self.failed(evaluate(good_report(self.f), good_run(self.f, calls), self.f))
        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0].name.startswith("tool-read-denied:"))

    def test_failed_positive_control_fails(self):
        report = good_report(self.f)
        report["control:node"] = entry(False, detail="not found")
        names = [c.name for c in self.failed(evaluate(report, good_run(self.f), self.f))]
        self.assertEqual(names, ["control:node"])

    def test_forbidden_action_that_worked_fails(self):
        report = good_report(self.f)
        report["write:etc"] = entry(True)
        report["env:SSH_AUTH_SOCK"] = entry(True)
        names = {c.name for c in self.failed(evaluate(report, good_run(self.f), self.f))}
        self.assertEqual(names, {"write:etc", "env:SSH_AUTH_SOCK"})

    def test_home_write_that_only_succeeded_in_the_sandbox_is_a_measurement(self):
        report = good_report(self.f)
        report["write:home"] = entry(True, detail="created inside the sandbox")
        report["write:home-claude"] = entry(True, detail="created inside the sandbox")
        checks = evaluate(report, good_run(self.f), self.f)
        self.assertEqual(self.failed(checks), [])
        measured = [c for c in checks if c.name in ("measure:write:home", "measure:write:home-claude")]
        self.assertEqual(len(measured), 2)
        self.assertTrue(all(c.warning for c in measured))

    def test_home_write_that_persisted_on_the_host_fails(self):
        for target in ("bash:home", "bash:home-claude"):
            path = self.f.write_targets[target]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        names = {c.name for c in self.failed(evaluate(good_report(self.f), good_run(self.f), self.f))}
        self.assertEqual(names, {"no-leftover:bash:home", "no-leftover:bash:home-claude"})

    def test_missing_probe_report_fails_every_attempt(self):
        checks = self.failed(evaluate({}, good_run(self.f), self.f))
        self.assertGreater(len(checks), 20)
        self.assertTrue(all(c.name.startswith("attempt-missing:") for c in checks))

    def test_leftover_write_probe_file_fails(self):
        target = self.f.write_targets["tool:home"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        names = [c.name for c in self.failed(evaluate(good_report(self.f), good_run(self.f), self.f))]
        self.assertEqual(names, ["no-leftover:tool:home"])

    def test_timed_out_run_fails(self):
        run = good_run(self.f)
        run.timed_out = True
        names = [c.name for c in self.failed(evaluate(good_report(self.f), run, self.f))]
        self.assertIn("run-completed:tool", names)

    def test_local_port_results_never_fail(self):
        for inside, outside in [(True, True), (True, False), (False, True), (False, False)]:
            report = good_report(self.f)
            report["port:inside"] = entry(inside)
            report["port:outside"] = entry(outside)
            checks = evaluate(report, good_run(self.f), self.f)
            self.assertEqual(self.failed(checks), [])
            ports = [c for c in checks if c.name.startswith("measure:port")]
            self.assertEqual(len(ports), 2)
            self.assertTrue(all(c.warning for c in ports))

    def test_missing_port_results_never_fail(self):
        report = good_report(self.f)
        del report["port:inside"], report["port:outside"]
        self.assertEqual(self.failed(evaluate(report, good_run(self.f), self.f)), [])

    def test_offline_makes_network_checks_warnings(self):
        report = good_report(self.f)
        report["net:registry"] = entry(False, detail="code=000")
        report["_offline"] = True
        checks = evaluate(report, good_run(self.f), self.f)
        self.assertEqual(self.failed(checks), [])
        self.assertTrue([c for c in checks if c.name == "net:registry"][0].warning)

    def test_registry_unreachable_online_fails(self):
        report = good_report(self.f)
        report["net:registry"] = entry(False, detail="code=000")
        names = [c.name for c in self.failed(evaluate(report, good_run(self.f), self.f))]
        self.assertEqual(names, ["net:registry"])


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.p = resolve(home=self.tmp / "home", root=self.tmp / "root")

    def test_fixture_has_canaries_and_controls(self):
        f = build_fixture(self.tmp / "work", self.p)
        self.assertRegex(f.marker, r"^ADK-CANARY-[0-9a-f]{16}$")
        self.assertRegex(f.control, r"^ADK-CONTROL-[0-9a-f]{16}$")
        self.assertEqual(set(f.canaries), {".env", "keys/server.pem", ".env.production", "outside/secret.txt"})
        for path in f.canaries.values():
            self.assertIn(f.marker, path.read_text())
        self.assertEqual(f.canaries["outside/secret.txt"].parent, self.p.canary_dir)
        self.assertIn(f.control, (f.project / "README.md").read_text())
        self.assertIn(f.control, (f.project / ".env.agent").read_text())
        self.assertTrue((f.project / ".git").is_dir())
        self.assertNotIn(f.marker, f.control)

    def test_write_targets_are_new_files(self):
        f = build_fixture(self.tmp / "work", self.p)
        for path in f.write_targets.values():
            self.assertFalse(path.exists())

    def test_remove_fixture_removes_everything_it_made(self):
        f = build_fixture(self.tmp / "work", self.p)
        remove_fixture(f, self.p)
        self.assertFalse(f.project.exists())
        self.assertFalse(self.p.config_dir.exists())

    def test_existing_canary_file_is_never_overwritten(self):
        self.p.canary_dir.mkdir(parents=True)
        (self.p.canary_dir / "secret.txt").write_text("real")
        with self.assertRaises(FileExistsError):
            build_fixture(self.tmp / "work", self.p)
        self.assertEqual((self.p.canary_dir / "secret.txt").read_text(), "real")

    def test_remove_keeps_a_config_dir_that_existed(self):
        self.p.config_dir.mkdir(parents=True)
        (self.p.config_dir / "config.json").write_text("{}")
        f = build_fixture(self.tmp / "work", self.p)
        remove_fixture(f, self.p)
        self.assertTrue((self.p.config_dir / "config.json").exists())
        self.assertFalse(self.p.canary_dir.exists())


if __name__ == "__main__":
    unittest.main()
