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


class _Counter(HTMLParser):
    """A stdlib HTMLParser subclass whose handler does minimal work, matching the turbohtml adapter."""

    def __init__(self) -> None:
        """Start the running tally."""
        super().__init__()
        self.work = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Tally a start tag and its attribute count."""
        self.work += len(tag) + len(attrs)


def htmlparser(text: str) -> None:
    """Drive the standard library's HTMLParser with the counting handler, the callback-driven model."""
    parser = _Counter()
    parser.feed(text)
    parser.close()


OPERATIONS = {
    "escape": (escape, "stdlib"),
    "unescape": (unescape, "stdlib"),
    "tokenize": (tokenize, "stdlib"),
    "htmlparser": (htmlparser, "html.parser"),
}
