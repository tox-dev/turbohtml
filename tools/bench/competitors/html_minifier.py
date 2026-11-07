"""
html-minifier-terser: the Node HTML minifier, invoked as a subprocess over its CLI.

Flags are pinned to turbohtml's conservative fold set (collapse whitespace, drop comments, drop optional attribute
quotes); inline CSS/JS is left untouched so the size comparison stays like-for-like.
"""

from __future__ import annotations

import subprocess
from typing import Final

REQUIREMENTS: Final[tuple[()]] = ()


def minify(text: str) -> str:
    # Minify HTML by piping through html-minifier-terser with turbohtml's conservative fold flags over stdin.
    return subprocess.run(
        ["html-minifier-terser", "--collapse-whitespace", "--remove-comments", "--remove-attribute-quotes"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


OPERATIONS = {"minify": (minify, "html-minifier-terser")}
