"""selectolax: parse with the lexbor C engine (WHATWG-conformant)."""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

REQUIREMENTS = ("selectolax>=0.4.10",)


def parse(text: str) -> None:
    """Parse a whole document with lexbor through selectolax (its native input is UTF-8 bytes)."""
    LexborHTMLParser(text.encode())


OPERATIONS = {"parse": (parse, "selectolax")}
