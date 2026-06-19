"""Annotated layout text via Node.to_annotated_text() (the inscriptis annotation
role): each element matching a rule gets a labeled span over its rendered text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import parse

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    Rules = Mapping[str, Sequence[str]]


def annotate(html: str, rules: Rules) -> tuple[str, list[tuple[int, int, str]]]:
    return parse(html).to_annotated_text(rules)


def spans_text(html: str, rules: Rules) -> list[tuple[str, str]]:
    """Return (label, covered-text) pairs, the offset-independent view."""
    text, labels = annotate(html, rules)
    return [(label, text[start:end]) for start, end, label in labels]


def call_with(convert: Callable[..., object], *args: object, **kwargs: str | int) -> object:
    """Invoke the converter with arguments the static signature would reject, to drive
    the runtime validation; the converter arrives signature-erased on purpose."""
    return convert(*args, **kwargs)


@pytest.mark.parametrize(
    ("html", "rules", "expected"),
    [
        pytest.param(
            "<h1>Title</h1><p>Some <b>bold</b> words.</p>",
            {"h1": ["heading"], "b": ["emphasis"]},
            [("heading", "Title"), ("emphasis", "bold")],
            id="tag-rules",
        ),
        pytest.param(
            "<p>plain</p><p class='lead intro'>special</p>",
            {"p#class=lead": ["lead"]},
            [("lead", "special")],
            id="tag-attr-value",
        ),
        pytest.param(
            "<a href='/x' rel='nofollow'>link</a><a href='/y'>plain</a>",
            {"a#rel": ["tracked"]},
            [("tracked", "link")],
            id="tag-attr-present",
        ),
        pytest.param(
            "<p class='note'>x</p><b class='note'>y</b><i>z</i>",
            {"#class=note": ["noted"]},
            [("noted", "x"), ("noted", "y")],
            id="any-tag-attr-value",
        ),
        pytest.param(
            "<p id='a'>one</p><p>two</p>",
            {"#id": ["has-id"]},
            [("has-id", "one")],
            id="any-tag-attr-present",
        ),
        pytest.param(
            "<p>a <b><i>both</i></b> c</p>",
            {"b": ["bold"], "i": ["italic"]},
            [("italic", "both"), ("bold", "both")],
            id="nested",
        ),
        pytest.param(
            "<h2>Head</h2>",
            {"h2": ["heading", "important"]},
            [("heading", "Head"), ("important", "Head")],
            id="multiple-labels",
        ),
        pytest.param("<p>x</p>", {}, [], id="empty-rules"),
        pytest.param("<p>x</p>", {"b": ["bold"]}, [], id="no-match"),
        pytest.param(
            "<table><tr><th>Name</th></tr><tr><td>Bob</td></tr></table>",
            {"th": ["header"], "td": ["cell"]},
            [("header", "Name"), ("cell", "Bob")],
            id="table-cells",
        ),
        pytest.param(
            "<p>plain</p><svg><desc>x</desc></svg>",
            {"desc": ["svg-desc"]},
            [],
            id="foreign-element-not-matched",
        ),
        pytest.param(
            "<p class=''>empty</p><p>x</p>",
            {"p#class=note": ["noted"]},
            [],
            id="valueless-no-match",
        ),
        pytest.param(
            "<p class='lead intro'>x</p>",
            {"#class=intro": ["second"]},
            [("second", "x")],
            id="value-matches-second-token",
        ),
        pytest.param("<p class='other '>x</p>", {"#class=note": ["noted"]}, [], id="value-no-token-matches"),
        pytest.param("<p>keep</p><b></b>", {"b": ["bold"]}, [], id="empty-element-no-span"),
        pytest.param("<p class='lead'>x</p>", {"#class=lear": ["noted"]}, [], id="value-same-length-differs"),
        pytest.param(
            "<p>intro</p><h2>Head</h2>",
            {"h2": ["heading"]},
            [("heading", "Head")],
            id="block-not-first-trims-leading",
        ),
        pytest.param("<div><p>a<br></p></div>", {"p": ["para"]}, [("para", "a")], id="trailing-break-trimmed"),
    ],
)
def test_annotation_spans(html: str, rules: Rules, expected: list[tuple[str, str]]) -> None:
    assert spans_text(html, rules) == expected


def test_offsets_are_exact() -> None:
    text, labels = annotate("<h1>Title</h1><p>a <b>bold</b> b</p>", {"h1": ["h"], "b": ["e"]})
    assert text == "Title\n\na bold b"
    assert labels == [(0, 5, "h"), (9, 13, "e")]


def test_table_cell_offsets_map_into_the_grid() -> None:
    text, labels = annotate(
        "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>",
        {"td": ["cell"]},
    )
    assert text == "Name   Age\nAlice  30"
    assert [(text[s:e]) for s, e, _ in labels] == ["Alice", "30"]


def test_content_inside_a_cell_is_not_annotated() -> None:
    # the cell renders into a throwaway buffer, so inner elements get no span
    _, labels = annotate("<table><tr><td>a <b>x</b></td></tr></table>", {"b": ["bold"], "td": ["cell"]})
    assert [label for _, _, label in labels] == ["cell"]


def test_leading_whitespace_shifts_offsets() -> None:
    text, labels = annotate("   <h1>Hi</h1>", {"h1": ["h"]})
    assert text == "Hi"
    assert labels == [(0, 2, "h")]


def test_links_and_options_compose_with_annotations() -> None:
    text, _labels = annotate("<p><a href='http://x'>site</a></p>", {"a": ["link"]})
    assert text == "site"
    text2, labels2 = parse("<p><a href='http://x'>site</a></p>").to_annotated_text({"a": ["link"]}, links="inline")
    assert text2 == "site (http://x)"
    assert [text2[s:e] for s, e, _ in labels2] == ["site (http://x)"]


def test_many_spans_grow_the_buffer() -> None:
    html = "<p>" + "".join(f"<b>{i}</b>" for i in range(20)) + "</p>"
    _, labels = annotate(html, {"b": ["bold"]})
    assert len(labels) == 20


def test_deeply_nested_annotations_grow_the_active_stack() -> None:
    text, labels = annotate("<span>" * 12 + "deep" + "</span>" * 12, {"span": ["s"]})
    assert len(labels) == 12
    assert {text[s:e] for s, e, _ in labels} == {"deep"}


@pytest.mark.parametrize(
    ("rules", "exc", "match"),
    [
        pytest.param("notadict", TypeError, "dict", id="rules-not-dict"),
        pytest.param({5: ["x"]}, TypeError, "string", id="key-not-string"),
        pytest.param({"b": "bold"}, TypeError, "list", id="value-is-string"),
        pytest.param({"b": 5}, TypeError, "iterable", id="value-not-iterable"),
    ],
)
def test_invalid_rules(rules: object, exc: type[Exception], match: str) -> None:
    with pytest.raises(exc, match=match):
        call_with(parse("<p>x</p>").to_annotated_text, rules)


@pytest.mark.parametrize(
    ("kwargs", "exc", "match"),
    [
        pytest.param({"width": "x"}, TypeError, "int", id="width-wrong-type"),
        pytest.param({"links": "bad"}, ValueError, "links", id="invalid-links"),
        pytest.param({"layout": "bad"}, ValueError, "layout", id="invalid-layout"),
    ],
)
def test_invalid_options(kwargs: Mapping[str, str], exc: type[Exception], match: str) -> None:
    with pytest.raises(exc, match=match):
        call_with(parse("<p>x</p>").to_annotated_text, {}, **kwargs)
