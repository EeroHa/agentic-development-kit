import json
import unittest
from pathlib import Path

from adk.walltest.runner import extract_probe_report, parse_stream

SAMPLE = Path(__file__).parent / "fixtures" / "transcript_sample.jsonl"


class ParseStreamTest(unittest.TestCase):
    def setUp(self):
        self.raw = SAMPLE.read_text(encoding="utf-8")
        self.t = parse_stream(self.raw)

    def test_raw_is_kept_verbatim(self):
        self.assertEqual(self.t.raw, self.raw)

    def test_tool_call_is_paired_with_its_result(self):
        self.assertEqual(len(self.t.calls), 1)
        call = self.t.calls[0]
        self.assertEqual(call.name, "Bash")
        self.assertEqual(call.input["command"], "cat README.md")
        self.assertEqual(call.result_text, "hello fixture")
        self.assertFalse(call.is_error)

    def test_final_text_comes_from_the_result_event(self):
        self.assertIn("hello fixture", self.t.final_text)

    def test_error_result_with_block_list_content(self):
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x/.env"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                 "content": [{"type": "text", "text": "Permission to use Read has been denied."},
                             {"type": "text", "text": "second block"}]}]}},
        ]
        t = parse_stream("\n".join(json.dumps(e) for e in events) + "\n")
        self.assertEqual(len(t.calls), 1)
        self.assertTrue(t.calls[0].is_error)
        self.assertIn("denied", t.calls[0].result_text)
        self.assertIn("second block", t.calls[0].result_text)
        self.assertEqual(t.final_text, "")

    def test_call_without_result_counts_as_not_an_error(self):
        events = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}]}}]
        t = parse_stream(json.dumps(events[0]))
        self.assertEqual(t.calls[0].result_text, "")
        self.assertFalse(t.calls[0].is_error)

    def test_lines_that_are_not_json_are_ignored(self):
        t = parse_stream("Warning: something\n" + self.raw + "\n\n")
        self.assertEqual(len(t.calls), 1)


class ExtractProbeReportTest(unittest.TestCase):
    def _run(self, result_text, command="python3 probe.py"):
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": result_text}]}},
        ]
        return parse_stream("\n".join(json.dumps(e) for e in events))

    def test_report_is_read_from_the_bash_result(self):
        report = {"a": {"ok": True, "leaked": False, "detail": "x"}}
        t = self._run("noise\n" + json.dumps(report) + "\n")
        self.assertEqual(extract_probe_report(t), report)

    def test_no_probe_call_gives_empty_report(self):
        self.assertEqual(extract_probe_report(self._run("{}", command="ls")), {})

    def test_unparsable_result_gives_empty_report(self):
        self.assertEqual(extract_probe_report(self._run("not json")), {})


if __name__ == "__main__":
    unittest.main()
