"""terser: the JS-ecosystem reference minifier, invoked as a subprocess over its CLI."""

from __future__ import annotations

import subprocess

REQUIREMENTS = ()


def minify_js(source: str) -> str:
    """Minify JavaScript by piping source through terser's compress+mangle CLI over stdin."""
    return subprocess.run(
        ["terser", "--compress", "--mangle"], input=source, capture_output=True, text=True, check=True
    ).stdout


OPERATIONS = {"minify-js": (minify_js, "terser")}
