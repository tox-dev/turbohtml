"""esbuild: the Go bundler's minifier, invoked as a subprocess over its CLI for both JS and CSS."""

from __future__ import annotations

import subprocess

REQUIREMENTS = ()


def _run(args: list[str], source: str) -> str:
    """Pipe source through the esbuild CLI over stdin and return its minified stdout."""
    return subprocess.run(["esbuild", *args], input=source, capture_output=True, text=True, check=True).stdout


def minify_js(source: str) -> str:
    """Minify JavaScript with esbuild's ``--minify`` under the js loader."""
    return _run(["--minify", "--loader=js"], source)


def minify_css(css: str) -> str:
    """Minify a stylesheet with esbuild's ``--minify`` under the css loader."""
    return _run(["--minify", "--loader=css"], css)


OPERATIONS = {"minify-js": (minify_js, "esbuild"), "minify-css": (minify_css, "esbuild")}
