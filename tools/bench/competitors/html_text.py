"""html-text: Zyte's plainer visible-text extractor, walking an lxml tree in Python."""

from __future__ import annotations

import html_text

REQUIREMENTS = ("html-text>=0.6",)


def text_render(text: str) -> None:
    """Extract layout-aware visible text with html-text, walking an lxml tree."""
    html_text.extract_text(text)


def text_collapsed(text: str) -> None:
    """Extract the collapsed word stream with html-text, layout guessing off."""
    html_text.extract_text(text, guess_layout=False)


OPERATIONS = {"text-render": (text_render, "html-text"), "text-collapsed": (text_collapsed, "html-text")}
