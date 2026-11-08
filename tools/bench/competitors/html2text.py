"""html2text: a streaming HTMLParser subclass that converts HTML to Markdown."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Final

import html2text

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("html2text>=2024.2.26",)


def _configured() -> html2text.HTML2Text:
    # Build a converter with the comparable non-default options engaged.
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.emphasis_mark = "_"
    converter.strong_mark = "__"
    converter.inline_links = False
    converter.pad_tables = True
    converter.escape_snob = True
    return converter


def _google() -> html2text.HTML2Text:
    # Build a converter in google_doc mode, reading the inline-CSS styling a Google Docs export carries.
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.google_doc = True
    return converter


_DEFAULT: Final[html2text.HTML2Text] = html2text.HTML2Text()
_DEFAULT.body_width = 0
_ESCAPED: Final[html2text.HTML2Text] = html2text.HTML2Text()
_ESCAPED.body_width = 0
_ESCAPED.escape_snob = True
_CONFIGURED: Final[html2text.HTML2Text] = _configured()
_GOOGLE: Final[html2text.HTML2Text] = _google()


def markdown(case: tuple[str, str]) -> None:
    # Convert HTML to Markdown with html2text, default or with the comparable options engaged.
    kind, text = case
    (_CONFIGURED if kind == "configured" else _ESCAPED if kind == "escaped" else _DEFAULT).handle(text)


def markdown_google(text: str) -> None:
    # Convert a Google Docs export to Markdown with html2text's google_doc mode.
    _GOOGLE.handle(text)


def markdown_wrap(case: tuple[int, str]) -> str:
    # Reuse the converter settings while parsing and wrapping each HTML input.
    width, text = case
    return _wrapped(width).handle(text)


@cache
def _wrapped(width: int) -> html2text.HTML2Text:
    converter: Final = html2text.HTML2Text()
    converter.body_width = width
    return converter


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "markdown": (markdown, "html2text"),
    "markdown-google": (markdown_google, "html2text"),
    "markdown-wrap": (markdown_wrap, "html2text"),
}
