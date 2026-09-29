"""turbohtml.annotation_surface(): group the annotated substrings by label.

The inscriptis surface-form extractor over the (text, spans) pair
Node.to_annotated_text() returns, as a pure transform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import annotation_surface, annotation_tags, parse

if TYPE_CHECKING:
    from collections.abc import Callable
from typing import Final


@pytest.mark.parametrize(
    ("text", "spans", "expected"),
    [
        pytest.param("Title", [(0, 5, "heading")], {"heading": ["Title"]}, id="single-span"),
        pytest.param(
            "Title bold",
            [(0, 5, "heading"), (6, 10, "emphasis")],
            {"heading": ["Title"], "emphasis": ["bold"]},
            id="two-labels",
        ),
        pytest.param(
            "x y",
            [(0, 1, "noted"), (2, 3, "noted")],
            {"noted": ["x", "y"]},
            id="same-label-groups-in-document-order",
        ),
        pytest.param(
            "both",
            [(0, 4, "italic"), (0, 4, "bold")],
            {"italic": ["both"], "bold": ["both"]},
            id="overlapping-spans-same-text",
        ),
        pytest.param("ignored", [], {}, id="no-spans-empty-dict"),
        pytest.param("ab", [(0, 0, "empty")], {"empty": [""]}, id="zero-width-span"),
        pytest.param("ab", [(0, 2, "all")], {"all": ["ab"]}, id="whole-text"),
    ],
)
def test_surface_groups_by_label(text: str, spans: list[tuple[int, int, str]], expected: dict[str, list[str]]) -> None:
    assert annotation_surface(text, spans) == expected


def test_label_order_follows_first_appearance_and_forms_keep_span_order() -> None:
    # labels appear in first-seen order; each label's forms keep the order of their spans
    surface = annotation_surface("abc", [(2, 3, "second"), (0, 1, "first"), (1, 2, "second")])
    assert list(surface) == ["second", "first"]
    assert surface == {"second": ["c", "b"], "first": ["a"]}


def test_accepts_any_iterable_of_spans() -> None:
    # the spans sequence may be any iterable of triples, not only the tuple list to_annotated_text returns
    assert annotation_surface("ab", iter([(0, 1, "x"), (1, 2, "x")])) == {"x": ["a", "b"]}


def test_accepts_spans_as_a_tuple() -> None:
    # a tuple (not a list) exercises the non-list branch of the PySequence_Fast access macros
    assert annotation_surface("ab", ((0, 1, "x"), (1, 2, "x"))) == {"x": ["a", "b"]}


def test_round_trips_to_annotated_text() -> None:
    text, spans = parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text({
        "h1": ["heading"],
        "b": ["metric"],
    })
    assert annotation_surface(text, spans) == {"heading": ["Q3"], "metric": ["12%"]}


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


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="one"), pytest.param(4, id="four"), pytest.param(32, id="thirty-two")]
)
def test_annotation_surface_owns_generator_labels(count: int) -> None:
    assert annotation_surface("a" * count, ((index, index + 1, f"label-{index:04d}") for index in range(count))) == {
        f"label-{index:04d}": ["a"] for index in range(count)
    }


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="one"), pytest.param(4, id="four"), pytest.param(32, id="thirty-two")]
)
def test_annotation_tags_owns_generator_labels(count: int) -> None:
    expected: Final = "".join(f"<label-{index:04d}>a</label-{index:04d}>" for index in range(count))
    assert (
        annotation_tags("a" * count, ((index, index + 1, f"label-{index:04d}") for index in range(count))) == expected
    )


@pytest.mark.parametrize(
    ("text", "spans", "expected"),
    [
        pytest.param("Title", [(0, 5, "h")], "<h>Title</h>", id="single-span"),
        pytest.param("ab", [], "ab", id="no-spans-returns-text"),
        pytest.param(
            "a b",
            [(0, 1, "x"), (2, 3, "y")],
            "<x>a</x> <y>b</y>",
            id="two-disjoint-spans",
        ),
        pytest.param(
            "abcdef",
            [(0, 6, "outer"), (2, 4, "inner")],
            "<outer>ab<inner>cd</inner>ef</outer>",
            id="nested-inner-closes-first",
        ),
        pytest.param(
            "abcd",
            [(0, 4, "a"), (0, 2, "b")],
            "<a><b>ab</b>cd</a>",
            id="shared-start-outer-opens-first",
        ),
        pytest.param(
            "abcd",
            [(0, 4, "a"), (2, 4, "b")],
            "<a>ab<b>cd</b></a>",
            id="shared-end-inner-closes-first",
        ),
        pytest.param(
            "abcdef",
            [
                (0, 0, "z1"),
                (0, 0, "z2"),
                (0, 0, "z3"),
                (1, 4, "r1"),
                (1, 4, "r2"),
                (1, 4, "r3"),
                (1, 4, "r4"),
                (1, 4, "r5"),
                (1, 4, "r6"),
                (4, 5, "after"),
            ],
            "<z1></z1><z2></z2><z3></z3>a<r1><r2><r3><r4><r5><r6>bcd</r6></r5></r4></r3></r2></r1><after>e</after>f",
            id="coincident-events-order-deterministically",
        ),
        pytest.param(
            "ab",
            [(0, 2, "x"), (0, 2, "y")],
            "<x><y>ab</y></x>",
            id="identical-range-lifo",
        ),
        pytest.param("ab", [(0, 0, "z")], "<z></z>ab", id="zero-width-span"),
        pytest.param(
            "ab",
            [(0, 0, "a"), (0, 0, "b")],
            "<a></a><b></b>ab",
            id="two-zero-width-spans-keep-span-order",
        ),
        pytest.param(
            "abcd",
            [(0, 2, "a"), (2, 4, "b")],
            "<a>ab</a><b>cd</b>",
            id="adjacent-spans-close-before-open",
        ),
        pytest.param("x", [(0, 1, "")], "<>x</>", id="empty-label"),
    ],
)
def test_tags_weave_spans(text: str, spans: list[tuple[int, int, str]], expected: str) -> None:
    assert annotation_tags(text, spans) == expected


def test_non_ascii_text_and_label_widen_the_result() -> None:
    # the output kind grows to cover the widest of the text and every label
    assert annotation_tags("héllo", [(0, 5, "ünïcode")]) == "<ünïcode>héllo</ünïcode>"


def test_non_ascii_label_widens_ascii_text() -> None:
    # a label wider than the text alone still widens the output kind
    assert annotation_tags("ab", [(0, 2, "ü")]) == "<ü>ab</ü>"


def test_round_trips_nested_annotations_to_well_formed_markup() -> None:
    text, spans = parse("<p>a <b><i>both</i></b> c</p>").to_annotated_text({"b": ["bold"], "i": ["italic"]})
    assert annotation_tags(text, spans) == "a <italic><bold>both</bold></italic> c"
