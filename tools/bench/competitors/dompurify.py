"""
DOMPurify (via isomorphic-dompurify): the reference SAFE_FOR_TEMPLATES sanitizer, run as a Node subprocess.

DOMPurify is JavaScript, so it runs through a small committed Node runner over stdin, the way the JS minifiers do. Its
``SAFE_FOR_TEMPLATES`` mode is the oracle turbohtml's ``strip_template_markers`` matches; here it is the speed baseline.
Regenerating the table needs ``npm install`` in ``tools/bench/node`` first (dompurify is a library, not a CLI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REQUIREMENTS = ()

_RUNNER = str(Path(__file__).resolve().parent.parent / "node" / "dompurify_runner.js")


def sanitize_templates(text: str) -> str:
    """Sanitize a document with DOMPurify's SAFE_FOR_TEMPLATES on, piping it through the Node runner."""
    return subprocess.run(["node", _RUNNER], input=text, capture_output=True, text=True, check=True).stdout


OPERATIONS = {"sanitize-templates": (sanitize_templates, "DOMPurify")}
