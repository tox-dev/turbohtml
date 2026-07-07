"""
parse5: the reference JavaScript WHATWG parser, run as a Node subprocess for its source-location model.

parse5 is JavaScript, so it runs through a small committed Node runner over stdin, the way the JS minifiers and
DOMPurify do. Its ``sourceCodeLocationInfo`` option is the model turbohtml's ``parse(source_locations=True)`` matches;
here it is the speed baseline for the granular start/end-tag and per-attribute spans. Regenerating the table needs
``npm install`` in ``tools/bench/node`` first (parse5 is a library, not a CLI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REQUIREMENTS = ()

_RUNNER = str(Path(__file__).resolve().parent.parent / "node" / "parse5_runner.js")


def parse_locations(text: str) -> str:
    """Parse a document with parse5's sourceCodeLocationInfo on, piping it through the Node runner."""
    return subprocess.run(["node", _RUNNER], input=text, capture_output=True, text=True, check=True).stdout


OPERATIONS = {"parse-locations": (parse_locations, "parse5")}
