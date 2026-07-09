"""The Python standard library: html.escape/unescape, the html.parser tokenizer, and the legacy codecs."""

from __future__ import annotations

from html import escape as _html_escape
from html import unescape as _html_unescape
from html.parser import HTMLParser
from typing import Final

REQUIREMENTS = ()


def escape(text: str) -> None:
    """Escape text with the standard library's html.escape."""
    _html_escape(text)


def unescape(text: str) -> None:
    """Resolve character references with the standard library's html.unescape."""
    _html_unescape(text)


# The codec each label resolved to before turbohtml decoded natively. None of them is the spec's decoder: cp932 carries
# the spec's Shift_JIS table but not its error handling, and gb18030 is the 2000 revision where the spec follows 2005.
# They price what was replaced; tests/conformance measures whether it was right.
_NEAREST_CODEC: Final[dict[str, str]] = {
    "shift_jis": "cp932",
    "windows-1252": "cp1252",
    "gb18030": "gb18030",
    "iso-2022-jp": "iso2022_jp",
}


def decode(case: tuple[str, bytes]) -> None:
    """Decode a legacy byte stream with the nearest CPython codec, replacing what it cannot map."""
    label, data = case
    data.decode(_NEAREST_CODEC[label], errors="replace")


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
    "decode": (decode, "stdlib"),
    "escape": (escape, "stdlib"),
    "unescape": (unescape, "stdlib"),
    "tokenize": (tokenize, "stdlib"),
    "htmlparser": (htmlparser, "html.parser"),
    "sax": (htmlparser, "html.parser"),
}
