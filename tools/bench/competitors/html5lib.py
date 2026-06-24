"""html5lib: the pure-Python WHATWG reference parser."""

from __future__ import annotations

import html5lib

REQUIREMENTS = ("html5lib>=1.1",)


def parse(text: str) -> None:
    """Parse a whole document with html5lib."""
    html5lib.parse(text)


OPERATIONS = {"parse": (parse, "html5lib")}
