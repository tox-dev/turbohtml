"""Input validation shared by annotation_surface() and annotation_tags().

Both run the same span parser, so the malformed-span checks are exercised
through either entry point; the str-argument checks are per function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import annotation_surface, annotation_tags

if TYPE_CHECKING:
    from collections.abc import Callable


def call_with(exporter: Callable[..., object], *args: object) -> object:
    """Invoke an exporter with arguments its static signature rejects, to drive the
    runtime validation; the exporter arrives signature-erased on purpose."""
    return exporter(*args)


@pytest.mark.parametrize("exporter", [annotation_surface, annotation_tags], ids=["surface", "tags"])
def test_text_must_be_str(exporter: Callable[..., object]) -> None:
    with pytest.raises(TypeError, match="str"):
        call_with(exporter, 5, [])


@pytest.mark.parametrize("exporter", [annotation_surface, annotation_tags], ids=["surface", "tags"])
def test_spans_must_be_iterable(exporter: Callable[..., object]) -> None:
    with pytest.raises(TypeError, match="iterable"):
        call_with(exporter, "ab", 5)


@pytest.mark.parametrize(
    ("spans", "exc", "match"),
    [
        pytest.param([[0, 1, "x"]], TypeError, "tuple", id="span-not-a-tuple"),
        pytest.param([(0, 1)], TypeError, "3 argument", id="span-wrong-arity"),
        pytest.param([(0, 1, 5)], TypeError, "str", id="label-not-str"),
        pytest.param([("a", 1, "x")], TypeError, "integer", id="start-not-int"),
        pytest.param([(-1, 1, "x")], ValueError, "out of range", id="negative-start"),
        pytest.param([(2, 1, "x")], ValueError, "out of range", id="end-before-start"),
        pytest.param([(0, 5, "x")], ValueError, "out of range", id="end-past-text"),
        pytest.param([(10**100, 0, "x")], OverflowError, "too large", id="offset-overflows"),
    ],
)
def test_malformed_spans_raise(spans: object, exc: type[Exception], match: str) -> None:
    with pytest.raises(exc, match=match):
        call_with(annotation_surface, "ab", spans)


def test_tags_validates_spans_too() -> None:
    # the inline exporter shares the parser, so it rejects the same out-of-range span
    with pytest.raises(ValueError, match="out of range"):
        call_with(annotation_tags, "ab", [(0, 9, "x")])
