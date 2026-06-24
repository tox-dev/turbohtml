"""The Python standard library: html.escape/unescape and the html.parser tokenizer."""

from __future__ import annotations

from html import escape as _html_escape
from html import unescape as _html_unescape
from html.parser import HTMLParser

REQUIREMENTS = ()


def escape(text: str) -> None:
    """Escape text with the standard library's html.escape."""
    _html_escape(text)


def unescape(text: str) -> None:
    """Resolve character references with the standard library's html.unescape."""
    _html_unescape(text)


def tokenize(text: str) -> None:
    """Drive the stdlib parser with its default no-op handlers."""
    parser = HTMLParser()
    parser.feed(text)
    parser.close()


OPERATIONS = {
    "escape": (escape, "stdlib"),
    "unescape": (unescape, "stdlib"),
    "tokenize": (tokenize, "stdlib"),
}
