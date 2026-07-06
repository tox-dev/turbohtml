"""tdewolff/minify: the Go ``minify`` binary, invoked as a subprocess over its CLI for both JS and CSS."""

from __future__ import annotations

import subprocess

REQUIREMENTS = ()


def _run(kind: str, source: str) -> str:
    """Pipe source through the ``minify`` CLI over stdin for the given ``--type`` and return its stdout."""
    return subprocess.run(["minify", f"--type={kind}"], input=source, capture_output=True, text=True, check=True).stdout


def minify_js(source: str) -> str:
    """Minify JavaScript with the tdewolff ``minify`` binary in js mode."""
    return _run("js", source)


def minify_css(css: str) -> str:
    """Minify a stylesheet with the tdewolff ``minify`` binary in css mode."""
    return _run("css", css)


def minify(text: str) -> str:
    """Minify an HTML document with the tdewolff ``minify`` binary in html mode."""
    return _run("html", text)


OPERATIONS = {
    "minify-js": (minify_js, "tdewolff"),
    "minify-css": (minify_css, "tdewolff"),
    "minify": (minify, "tdewolff"),
}
