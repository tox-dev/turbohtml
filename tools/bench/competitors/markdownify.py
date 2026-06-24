"""markdownify: HTML to Markdown over BeautifulSoup."""

from __future__ import annotations

import markdownify

REQUIREMENTS = ("markdownify>=0.13",)


def markdown(case: tuple[str, str]) -> None:
    """Convert HTML to Markdown with markdownify, default or with the comparable option surface engaged."""
    kind, text = case
    if kind == "configured":
        markdownify.markdownify(text, strong_em_symbol="_", heading_style="atx", escape_misc=True)
    else:
        markdownify.markdownify(text)


OPERATIONS = {"markdown": (markdown, "markdownify")}
