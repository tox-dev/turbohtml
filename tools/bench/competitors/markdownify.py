"""markdownify: HTML to Markdown over BeautifulSoup."""

from __future__ import annotations

from typing import Final

import markdownify

_REQUIREMENTS: Final[tuple[str, ...]] = ("markdownify>=0.13",)


def markdown(case: tuple[str, str]) -> None:
    # Convert HTML to Markdown with markdownify, default or with the comparable option surface engaged.
    kind, text = case
    if kind == "configured":
        markdownify.markdownify(text, strong_em_symbol="_", heading_style="atx", escape_misc=True)
    else:
        markdownify.markdownify(text)


def markdown_wrap(case: tuple[int, str]) -> str:
    # Apply the requested width to the same short-word document as the core adapter.
    width, text = case
    return markdownify.markdownify(text, wrap=True, wrap_width=width)


OPERATIONS = {"markdown": (markdown, "markdownify"), "markdown-wrap": (markdown_wrap, "markdownify")}
