"""inscriptis: the layout-aware HTML-to-text renderer turbohtml.to_text succeeds (lays tables out as columns)."""

from __future__ import annotations

import inscriptis
from inscriptis.model.config import ParserConfig

REQUIREMENTS = ("inscriptis>=2.5",)

_ANNOTATION_RULES = {"h1": ["heading"], "b": ["emphasis"], "a": ["link"]}
_ANNOTATION_CONFIG = ParserConfig(annotation_rules=_ANNOTATION_RULES)


def text_render(text: str) -> None:
    """Render layout-aware visible text with inscriptis, on an lxml tree."""
    inscriptis.get_text(text)


def text_annotated(text: str) -> None:
    """Render annotated layout text with inscriptis, on an lxml tree."""
    inscriptis.get_annotated_text(text, _ANNOTATION_CONFIG)


OPERATIONS = {"text-render": (text_render, "inscriptis"), "text-annotated": (text_annotated, "inscriptis")}
