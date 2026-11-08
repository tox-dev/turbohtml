"""readabilipy: the Readability.js wrapper turbohtml.article succeeds, in its pure-Python mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from readabilipy import simple_json_from_html_string

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("readabilipy>=0.3",)


def article(text: str) -> None:
    # Extract title, byline, date, and text blocks with readabilipy's Python mode (html5lib on BeautifulSoup).
    simple_json_from_html_string(text, use_readability=False)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {"article": (article, "readabilipy")}
