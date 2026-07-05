"""turbohtml.annotation_surface(): group the annotated substrings by label.

The inscriptis surface-form extractor over the (text, spans) pair
Node.to_annotated_text() returns, as a pure transform.
"""

from __future__ import annotations

import pytest

from turbohtml import annotation_surface, parse


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
