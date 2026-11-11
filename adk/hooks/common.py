"""Shared plumbing for the wall's hooks: decision shapes and a fail-closed runner."""
from __future__ import annotations

import json
import os
import sys
from typing import Callable

from adk.paths import Paths, resolve


def _decision(kind: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": kind,
            "permissionDecisionReason": reason,
        }
    }


def deny(reason: str) -> dict:
    return _decision("deny", reason)


def ask(reason: str) -> dict:
    return _decision("ask", reason)


def run(decide: Callable[[dict, Paths], dict | None], *, stdin=None, stdout=None,
        environ=None, paths: Paths | None = None) -> int:
    """Read one hook event, print the decision (if any). Any failure prints a deny."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    environ = os.environ if environ is None else environ
    try:
        event = json.loads(stdin.read())
        if not isinstance(event, dict):
            raise TypeError("hook input is not a JSON object")
        # Only the environment may name the project; never trust the event for it.
        event.pop("_project_dir", None)
        project = environ.get("CLAUDE_PROJECT_DIR")
        if project:
            event["_project_dir"] = project
        result = decide(event, resolve() if paths is None else paths)
        out = None if result is None else json.dumps(result)
    except BaseException as exc:  # fail closed, whatever went wrong
        out = json.dumps(deny(f"adk wall hook error: {type(exc).__name__}"))
    if out is not None:
        stdout.write(out + "\n")
        stdout.flush()
    return 0
