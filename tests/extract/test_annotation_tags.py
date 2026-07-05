"""turbohtml.annotation_tags(): weave the annotated spans into inline markup.

The inscriptis inline-tagged (XML) exporter over the (text, spans) pair
Node.to_annotated_text() returns. Properly nested spans stay well-formed because
the innermost span always closes first.
"""

from __future__ import annotations

import pytest

from turbohtml import annotation_tags, parse


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
