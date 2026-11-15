"""Safe JSON reading and atomic writing."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class InvalidJson(Exception):
    """A JSON file could not be parsed; the message names the file."""


def read_json(path: Path, default: Any) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(text)
    except ValueError as e:
        raise InvalidJson(f"{path}: invalid JSON ({e})") from e


def write_json_atomic(path: Path, data: Any, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
