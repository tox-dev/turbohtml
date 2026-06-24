"""html5-parser: the gumbo C parser returning an lxml tree."""

from __future__ import annotations

import html5_parser

REQUIREMENTS = ("html5-parser>=0.4.12", "lxml>=6.1.1")


def parse(text: str) -> None:
    """Parse a whole document with html5-parser (gumbo, into an lxml tree)."""
    html5_parser.parse(text)


OPERATIONS = {"parse": (parse, "html5-parser")}
