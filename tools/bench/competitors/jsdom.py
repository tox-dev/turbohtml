"""jsdom child-output workflow, including the Python-to-Node process boundary."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

REQUIREMENTS = ()

_INNER_RUNNER: Final = str(Path(__file__).resolve().parent.parent / "node" / "inner_runner.js")


def _parse_inner(text: str) -> str:
    return subprocess.run(
        ["node", _INNER_RUNNER, "jsdom"], input=text, capture_output=True, text=True, check=True
    ).stdout


def _parse_inner_encode(text: str) -> bytes:
    return subprocess.run(["node", _INNER_RUNNER, "jsdom"], input=text.encode(), capture_output=True, check=True).stdout


OPERATIONS = {
    "parse-inner": (_parse_inner, "jsdom"),
    "parse-inner-encode": (_parse_inner_encode, "jsdom"),
}

__all__ = []
