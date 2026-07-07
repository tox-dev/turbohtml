"""
DOMPurify (via isomorphic-dompurify): the reference HTML sanitizer, run as a Node subprocess.

DOMPurify is JavaScript, so it runs through a small committed Node runner over stdin, the way the JS minifiers do. Its
``SAFE_FOR_TEMPLATES`` mode is the oracle turbohtml's ``strip_template_markers`` matches, and its
``SANITIZE_NAMED_PROPS`` mode the oracle ``isolate_named_props`` matches; here each is the speed baseline for its
turbohtml counterpart. Regenerating the table needs ``npm install`` in ``tools/bench/node`` first (dompurify is a
library, not a CLI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REQUIREMENTS = ()

_RUNNER = str(Path(__file__).resolve().parent.parent / "node" / "dompurify_runner.js")


def _run(mode: str, text: str) -> str:
    """Pipe ``text`` through the Node runner in ``mode`` and read the sanitized result back."""
    return subprocess.run(["node", _RUNNER, mode], input=text, capture_output=True, text=True, check=True).stdout


def sanitize_templates(text: str) -> str:
    """Sanitize a document with DOMPurify's SAFE_FOR_TEMPLATES on."""
    return _run("templates", text)


def sanitize_named_props(text: str) -> str:
    """Sanitize a document with DOMPurify's SANITIZE_NAMED_PROPS on, prefixing id/name with ``user-content-``."""
    return _run("named-props", text)


def sanitize_custom_elements(text: str) -> str:
    """Sanitize with DOMPurify's CUSTOM_ELEMENT_HANDLING keeping x-* elements and their data-* attributes."""
    return _run("custom-elements", text)


OPERATIONS = {
    "sanitize-templates": (sanitize_templates, "DOMPurify"),
    "sanitize-named-props": (sanitize_named_props, "DOMPurify"),
    "sanitize-custom-elements": (sanitize_custom_elements, "DOMPurify"),
}
