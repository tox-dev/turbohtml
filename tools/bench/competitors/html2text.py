"""html2text: a streaming HTMLParser subclass that converts HTML to Markdown."""

from __future__ import annotations

import html2text

REQUIREMENTS = ("html2text>=2024.2.26",)


def _configured() -> html2text.HTML2Text:
    """Build a converter with the comparable non-default options engaged."""
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.emphasis_mark = "_"
    converter.strong_mark = "__"
    converter.inline_links = False
    converter.pad_tables = True
    converter.escape_snob = True
    return converter


def _google() -> html2text.HTML2Text:
    """Build a converter in google_doc mode, reading the inline-CSS styling a Google Docs export carries."""
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.google_doc = True
    return converter


_DEFAULT = html2text.HTML2Text()
_DEFAULT.body_width = 0
_CONFIGURED = _configured()
_GOOGLE = _google()


def markdown(case: tuple[str, str]) -> None:
    """Convert HTML to Markdown with html2text, default or with the comparable options engaged."""
    kind, text = case
    (_CONFIGURED if kind == "configured" else _DEFAULT).handle(text)


def markdown_google(text: str) -> None:
    """Convert a Google Docs export to Markdown with html2text's google_doc mode."""
    _GOOGLE.handle(text)


OPERATIONS = {"markdown": (markdown, "html2text"), "markdown-google": (markdown_google, "html2text")}
