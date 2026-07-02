"""Tests for turbohtml.extract.boilerplate, the per-paragraph good/bad classifier over the main-content scoring."""

from __future__ import annotations

import pytest

from turbohtml.extract import Extraction, Paragraph, boilerplate

_PROSE = (
    "A comet is an icy small body that, when it passes close to the Sun, warms up and releases gases, "
    "forming a glowing coma around it."
)
_PAGE = (
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
