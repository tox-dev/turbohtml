"""inscriptis: the layout-aware HTML-to-text renderer turbohtml.to_text succeeds (lays tables out as columns)."""

from __future__ import annotations

from functools import cache

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


def _text_annotation_rules(case: tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]) -> None:
    text, rules = case
    inscriptis.get_annotated_text(text, _annotation_config(rules))


@cache
def _annotation_config(rules: tuple[tuple[str, tuple[str, ...]], ...]) -> ParserConfig:
    return ParserConfig(annotation_rules={name: list(labels) for name, labels in rules})


OPERATIONS = {
    "text-render": (text_render, "inscriptis"),
    "text-annotated": (text_annotated, "inscriptis"),
    "text-annotation-rules": (_text_annotation_rules, "inscriptis"),
}
