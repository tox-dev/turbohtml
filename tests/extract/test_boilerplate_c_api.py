from __future__ import annotations

import pytest

import turbohtml
from turbohtml._html import _boilerplate
from turbohtml.extract import Extraction, boilerplate

# Thresholds the binding takes as (min_length, max_link_density, keep_headings). Wide open, so only the segmentation
# decides the answer; a test that cares about one threshold passes its own.
_OPEN = (0, 1.0, True)
_ARTICLE = "<article><h2>brief</h2><p>ordinary prose long enough for this to score as the article body</p></article>"


def _classify(
    markup: str, thresholds: tuple[int, float, bool] = _OPEN, *, content: bool = True
) -> list[tuple[str, bool, bool]]:
    """Run the binding over markup, passing the scored content body unless the caller withholds it."""
    document = turbohtml.parse(markup)
    root = document.root
    assert root is not None  # a parsed document always has an <html> root, though the type admits None
    return _boilerplate(root, document.main_content() if content else None, *thresholds)


@pytest.mark.parametrize(
    ("tag", "wrapper"),
    [
        pytest.param("p", "{0}", id="unit-p"),
        pytest.param("li", "{0}", id="unit-li"),
        pytest.param("pre", "{0}", id="unit-pre"),
        pytest.param("blockquote", "{0}", id="unit-blockquote"),
        # a bare cell is foster-parented out of the tree, so it needs the table it belongs to
        pytest.param("td", "<table><tr>{0}</tr></table>", id="unit-td"),
        pytest.param("dd", "<dl>{0}</dl>", id="unit-dd"),
        pytest.param("dt", "<dl>{0}</dl>", id="unit-dt"),
        pytest.param("figcaption", "<figure>{0}</figure>", id="unit-figcaption"),
        pytest.param("h1", "{0}", id="unit-h1"),
        pytest.param("h3", "{0}", id="unit-h3"),
        pytest.param("h6", "{0}", id="unit-h6"),
    ],
)
def test_every_unit_tag_becomes_a_paragraph(tag: str, wrapper: str) -> None:
    markup = wrapper.format(f"<{tag}>text of the unit</{tag}>")
    assert [row[0] for row in _classify(markup)] == ["text of the unit"]


@pytest.mark.parametrize(
    ("tag", "heading"),
    [pytest.param("h2", True, id="heading"), pytest.param("p", False, id="not-a-heading")],
)
def test_a_unit_reports_whether_it_is_a_heading(tag: str, heading: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    assert _classify(f"<{tag}>words</{tag}>")[0][2] is heading


def test_a_container_yields_its_nested_units_not_itself() -> None:
    assert [row[0] for row in _classify("<li><p>inner one</p><p>inner two</p></li>")] == ["inner one", "inner two"]


def test_a_container_holding_a_unit_below_a_non_unit_is_still_a_container() -> None:
    # the <li>'s own child is a <div>, which is not a unit but carries one, so the <li> is a container
    assert [row[0] for row in _classify("<li><div><p>the real paragraph</p></div></li>")] == ["the real paragraph"]


def test_a_unit_nested_below_a_non_unit_is_still_found() -> None:
    assert [row[0] for row in _classify("<div><section><p>buried</p></section></div>")] == ["buried"]


def test_a_blank_unit_is_not_a_paragraph() -> None:
    assert [row[0] for row in _classify("<p>   </p><p>real</p>")] == ["real"]


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<p>a   b</p>", "a b", id="runs-collapse"),
        pytest.param("<p>\ta\nb\r</p>", "a b", id="tab-newline-return"),
        pytest.param("<p>a\x0cb</p>", "a b", id="form-feed"),
        pytest.param("<p>a\x0bb</p>", "a b", id="vertical-tab"),
        pytest.param("<p>  edges  </p>", "edges", id="edges-trimmed"),
    ],
)
def test_the_text_is_whitespace_collapsed(markup: str, expected: str) -> None:
    assert _classify(markup)[0][0] == expected


def test_a_unit_outside_the_content_body_is_boilerplate() -> None:
    assert _classify("<p>orphan</p>", content=False)[0][1] is True


def test_a_link_dense_unit_is_boilerplate() -> None:
    markup = "<article><p><a href='/x'>all of it is a link</a></p></article>"
    assert _classify(markup, (0, 0.5, True))[0][1] is True


def test_an_anchor_nested_below_another_element_still_counts() -> None:
    markup = "<article><p><span><a href='/x'>all of it is a link</a></span></p></article>"
    assert _classify(markup, (0, 0.5, True))[0][1] is True


def test_a_non_anchor_element_beside_an_anchor_is_not_counted_as_linked() -> None:
    # the <em> contributes text but no link, so the density stays under the limit and the unit is content
    markup = "<article><p><em>a good deal of ordinary prose</em> and <a href='/x'>one</a> link</p></article>"
    assert _classify(markup, (0, 0.5, True))[0][1] is False


def test_the_length_floor_drops_a_short_unit() -> None:
    assert _classify("<article><p>short</p></article>", (25, 1.0, True))[0][1] is True


def test_a_kept_heading_escapes_the_length_floor() -> None:
    assert _classify(_ARTICLE, (25, 1.0, True))[0][1] is False


def test_a_heading_faces_the_floor_when_headings_are_not_kept() -> None:
    assert _classify(_ARTICLE, (25, 1.0, False))[0][1] is True


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("notanelement", None, 0, 1.0, True), id="root-is-not-an-element"),
        pytest.param((None, 0, 1.0, True), id="too-few-arguments"),
    ],
)
def test_the_binding_rejects_bad_arguments(args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        _boilerplate(*args)  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_the_binding_rejects_a_content_that_is_not_an_element() -> None:
    document = turbohtml.parse("<p>x</p>")
    root = document.root
    assert root is not None
    with pytest.raises(TypeError):
        _boilerplate(root, "notanelement", 0, 1.0, True)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]  # positional-only C binding


def test_the_public_entry_point_still_answers_paragraphs() -> None:
    found = boilerplate("<article><p>a paragraph long enough to clear the default floor</p></article>", Extraction())
    assert [(unit.text, unit.is_boilerplate, unit.is_heading) for unit in found] == [
        ("a paragraph long enough to clear the default floor", False, False)
    ]
