from __future__ import annotations

import gc
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.extract import Extraction, Paragraph, boilerplate

if TYPE_CHECKING:
    from types import ModuleType


_PROSE: Final[str] = (
    "A comet is an icy small body that, when it passes close to the Sun, warms up and releases gases, "
    "forming a glowing coma around it."
)
_PAGE: Final[str] = (
    "<html lang=en><head><title>Comets</title></head><body>"
    "<ul><li><a href='/'>Home</a></li><li><a href='/science'>Science</a></li></ul>"
    f"<article><h1>Comets</h1><p>{_PROSE}</p><p>{_PROSE}</p><p>Short.</p>"
    "<p>See <a href='/a'>this piece</a> and <a href='/b'>that piece</a> too</p></article>"
    "<footer><p>Copyright notice, all rights reserved here.</p></footer></body></html>"
)


def _classified(page: str, options: Extraction | None = None) -> dict[str, bool]:
    paragraphs = boilerplate(page) if options is None else boilerplate(page, options)
    return {paragraph.text: paragraph.is_boilerplate for paragraph in paragraphs}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Home", True, id="nav-item"),
        pytest.param("Comets", False, id="in-content-heading"),
        pytest.param(_PROSE, False, id="in-content-prose"),
        pytest.param("Short.", True, id="in-content-below-length-floor"),
        pytest.param("See this piece and that piece too", True, id="in-content-link-dense"),
        pytest.param("Copyright notice, all rights reserved here.", True, id="footer"),
    ],
)
def test_boilerplate_default_classification(text: str, *, expected: bool) -> None:
    assert _classified(_PAGE)[text] is expected


def test_boilerplate_preserves_document_order() -> None:
    texts = [paragraph.text for paragraph in boilerplate(_PAGE)]
    assert texts == [
        "Home",
        "Science",
        "Comets",
        _PROSE,
        _PROSE,
        "Short.",
        "See this piece and that piece too",
        "Copyright notice, all rights reserved here.",
    ]


def test_boilerplate_marks_headings_whatever_the_classification() -> None:
    headings = {paragraph.text for paragraph in boilerplate(_PAGE) if paragraph.is_heading}
    assert headings == {"Comets"}


def test_boilerplate_returns_paragraph_records() -> None:
    expected = Paragraph(text=_PROSE, is_boilerplate=False, is_heading=False)
    assert boilerplate(f"<article><p>{_PROSE}</p></article>") == [expected]


def test_boilerplate_no_content_body_flags_everything() -> None:
    page = "<body><ul><li><a href='/'>Home</a></li></ul><p>Short.</p></body>"
    assert all(paragraph.is_boilerplate for paragraph in boilerplate(page))


def test_boilerplate_skips_blank_units() -> None:
    texts = [paragraph.text for paragraph in boilerplate(f"<article><p>  </p><p>{_PROSE}</p></article>")]
    assert texts == [_PROSE]


def test_boilerplate_normalizes_whitespace() -> None:
    page = f"<article><p>{_PROSE}</p><p>spaced   out\n\ttext across lines and tabs in this one</p></article>"
    assert boilerplate(page)[1].text == "spaced out text across lines and tabs in this one"


def test_boilerplate_reports_nested_units_not_their_container() -> None:
    page = f"<article><blockquote><p>{_PROSE}</p></blockquote><p>{_PROSE}</p></article>"
    assert [paragraph.text for paragraph in boilerplate(page)] == [_PROSE, _PROSE]


def test_boilerplate_unlinked_prose_skips_the_density_check() -> None:
    assert _classified(f"<article><p>{_PROSE}</p></article>")[_PROSE] is False


def test_boilerplate_min_length_zero_keeps_short_prose() -> None:
    assert _classified(_PAGE, Extraction(min_length=0))["Short."] is False


def test_boilerplate_max_link_density_one_keeps_link_dense_prose() -> None:
    classified = _classified(_PAGE, Extraction(max_link_density=1.0))
    assert classified["See this piece and that piece too"] is False


def test_boilerplate_keep_headings_false_applies_the_length_floor() -> None:
    assert _classified(_PAGE, Extraction(keep_headings=False))["Comets"] is True


def test_boilerplate_justext_preset_values() -> None:
    assert Extraction.justext() == Extraction(min_length=70, max_link_density=0.2)


def test_boilerplate_justext_preset_drops_borderline_prose() -> None:
    borderline = "A borderline paragraph just over twenty-five characters."
    page = f"<article><p>{_PROSE}</p><p>{_PROSE}</p><p>{borderline}</p></article>"
    assert _classified(page)[borderline] is False
    assert _classified(page, Extraction.justext())[borderline] is True


@pytest.mark.parametrize(
    ("options", "message"),
    [
        pytest.param({"min_length": -1}, "min_length must be non-negative, got -1", id="negative-length"),
        pytest.param(
            {"max_link_density": 1.5}, r"max_link_density must be within \[0.0, 1.0\], got 1.5", id="density-above-one"
        ),
        pytest.param(
            {"max_link_density": -0.1},
            r"max_link_density must be within \[0.0, 1.0\], got -0.1",
            id="density-below-zero",
        ),
    ],
)
def test_extraction_rejects_invalid_thresholds(options: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Extraction(**options)  # ty: ignore[invalid-argument-type]  # parametrized mixed int/float kwargs


# Thresholds the binding takes as (min_length, max_link_density, keep_headings). Wide open, so only the segmentation
# decides the answer; a test that cares about one threshold passes its own.
_OPEN: Final[tuple[int, float, bool]] = (0, 1.0, True)
_ARTICLE: Final[str] = (
    "<article><h2>brief</h2><p>ordinary prose long enough for this to score as the article body</p></article>"
)


def _classify(markup: str, thresholds: tuple[int, float, bool] = _OPEN) -> list[tuple[str, bool, bool]]:
    options = Extraction(min_length=thresholds[0], max_link_density=thresholds[1], keep_headings=thresholds[2])
    return [(unit.text, unit.is_boilerplate, unit.is_heading) for unit in boilerplate(markup, options)]


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


def test_the_public_entry_point_still_answers_paragraphs() -> None:
    found = boilerplate("<article><p>a paragraph long enough to clear the default floor</p></article>", Extraction())
    assert [(unit.text, unit.is_boilerplate, unit.is_heading) for unit in found] == [
        ("a paragraph long enough to clear the default floor", False, False)
    ]


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('<a href="/comet">comet</a>', id="direct"),
        pytest.param('<span><a href="/comet">comet</a></span>', id="span"),
        pytest.param('<b><a href="/comet">comet</a></b>', id="bold"),
    ],
)
def test_boilerplate_keeps_text_after_snapshot_release(content: str) -> None:
    prose: Final = "A comet passes near the Sun, warms up and releases gas, forming a glowing coma around it."
    page: Final = f"<article><p>{prose}</p><p>Read about this {content} today.</p></article>"
    assert [paragraph.text for paragraph in boilerplate(page)] == [prose, "Read about this comet today."]


def test_boilerplate_classifies_owned_text() -> None:
    prose: Final = "A comet passes near the Sun, warms up and releases gas, forming a glowing coma around it."
    assert boilerplate(f"<article><p>{prose}</p></article>") == [
        Paragraph(text=prose, is_boilerplate=False, is_heading=False)
    ]


def test_boilerplate_releases_text_snapshots() -> None:
    # PyPy lacks tracemalloc and reclaims objects on a different schedule.
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    page: Final = f'<article><p>{"A comet passes near the Sun. " * 256}<a href="/more">more</a></p></article>'
    tracing.start()
    try:
        for _ in range(128):
            boilerplate(page)
        gc.collect()
        before: Final = tracing.get_traced_memory()[0]
        for _ in range(128):
            boilerplate(page)
        gc.collect()
        assert tracing.get_traced_memory()[0] - before < 256 * 1024
    finally:
        tracing.stop()
