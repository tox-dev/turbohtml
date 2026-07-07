"""
sanitize-html: the standard Node HTML sanitizer, run as a subprocess for the tag-transform comparison.

sanitize-html is JavaScript, so it runs through a small committed Node runner over stdin, the way the JS minifiers and
DOMPurify do. Its ``transformTags``/``simpleTransform`` is the API turbohtml's :attr:`Policy.transform_tags` mirrors;
here it is the speed baseline. Regenerating the table needs ``npm install`` in ``tools/bench/node`` first (sanitize-html
is a library, not a CLI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REQUIREMENTS = ()

_RUNNER = str(Path(__file__).resolve().parent.parent / "node" / "sanitize_html_runner.js")


def sanitize_transform(text: str) -> str:
    """Sanitize a document while renaming deprecated presentational tags, piping it through the Node runner."""
    return subprocess.run(["node", _RUNNER], input=text, capture_output=True, text=True, check=True).stdout


OPERATIONS = {"sanitize-transform": (sanitize_transform, "sanitize-html")}
