"""Paragraph-level content/boilerplate classification via :func:`turbohtml.extract.boilerplate`.

``justext`` and ``boilerpy3`` expose a per-paragraph ``is_boilerplate`` flag; :func:`boilerplate` reproduces it as a
thin layer over the same C content scoring that powers :meth:`~turbohtml.Node.main_content`. These cases drive the
region membership (content vs nav/footer), every :class:`Extraction` threshold (length, link density, heading
handling), leaf-block segmentation, and the config validation and presets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml.extract import Extraction, Paragraph, boilerplate

if TYPE_CHECKING:
    from collections.abc import Callable

# A prose paragraph: >100 chars with commas so the content scorer keeps it as the article body.
PROSE = (
    "A comet is an icy small body that, when it passes close to the Sun, warms up, "
    "begins to release gases, and forms a glowing coma, a thin atmosphere, around it."
)
PROSE_TWO = (
    "The tail of a comet always points away from the Sun, pushed out by the solar wind "
    "and by the radiation pressure of the sunlight falling on the released dust and gas."
)
PAGE = (
    "<html lang=en><head><title>Comets</title></head><body>"
    "<nav><a href=/>Home</a></nav>"
    f"<article class=post><h1>Comets</h1><p>{PROSE}</p><p>{PROSE_TWO}</p></article>"
    "<footer><p>Copyright notice, all rights reserved here forever and ever amen.</p></footer>"
    "</body></html>"
)


def texts(paragraphs: list[Paragraph]) -> list[str]:
    return [paragraph.text for paragraph in paragraphs]


def test_returns_paragraph_records() -> None:
    result = boilerplate(PAGE)
    assert all(isinstance(paragraph, Paragraph) for paragraph in result)
    assert result[0] == Paragraph(text="Comets", is_boilerplate=False, is_heading=True)


def test_keeps_document_order() -> None:
    assert texts(boilerplate(PAGE)) == ["Comets", PROSE, PROSE_TWO, "Copyright notice, all rights reserved here forever and ever amen."]


def test_article_prose_is_content() -> None:
    body = {paragraph.text: paragraph.is_boilerplate for paragraph in boilerplate(PAGE)}
    assert body[PROSE] is False
    assert body[PROSE_TWO] is False


def test_footer_is_boilerplate() -> None:
    footer = next(p for p in boilerplate(PAGE) if p.text.startswith("Copyright"))
    assert footer.is_boilerplate is True
    assert footer.is_heading is False


def test_heading_inside_content_is_content_by_default() -> None:
    heading = next(p for p in boilerplate(PAGE) if p.is_heading)
    assert heading.text == "Comets"
    assert heading.is_boilerplate is False


def test_headings_are_content_false_drops_heading() -> None:
    heading = next(p for p in boilerplate(PAGE, Extraction(headings_are_content=False)) if p.is_heading)
    assert heading.is_boilerplate is True


def test_accepts_bytes() -> None:
    assert texts(boilerplate(PAGE.encode())) == texts(boilerplate(PAGE))


def test_none_options_matches_default_config() -> None:
    assert boilerplate(PAGE) == boilerplate(PAGE, Extraction())


def test_short_content_block_is_boilerplate() -> None:
    html = f"<article class=post><p>{PROSE}</p><p>Too short.</p></article>"
    short = next(p for p in boilerplate(html) if p.text == "Too short.")
    assert short.is_boilerplate is True


def test_min_text_length_lets_short_block_through() -> None:
    html = f"<article class=post><p>{PROSE}</p><p>Too short.</p></article>"
    short = next(p for p in boilerplate(html, Extraction(min_text_length=5)) if p.text == "Too short.")
    assert short.is_boilerplate is False


def test_link_dense_block_is_boilerplate() -> None:
    links = " ".join(f"<a href=/{word}>{word}</a>" for word in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"))
    html = f"<article class=post><p>{PROSE}</p><p>{links}</p></article>"
    dense = next(p for p in boilerplate(html) if "alpha" in p.text)
    assert dense.is_boilerplate is True


def test_loose_link_density_keeps_link_block() -> None:
    links = " ".join(f"<a href=/{word}>{word} content word</a>" for word in ("alpha", "beta", "gamma", "delta"))
    html = f"<article class=post><p>{PROSE}</p><p>Intro words {links} and a closing tail of plain prose words.</p></article>"
    block = next(p for p in boilerplate(html, Extraction(max_link_density=1.0)) if "alpha" in p.text)
    assert block.is_boilerplate is False


def test_nested_blocks_classify_only_the_leaf() -> None:
    html = f"<article class=post><p>{PROSE}</p><ul><li><p>{PROSE_TWO}</p></li></ul></article>"
    result = texts(boilerplate(html))
    assert result == [PROSE, PROSE_TWO]


def test_empty_block_is_skipped() -> None:
    html = f"<article class=post><p>{PROSE}</p><p>   </p><td></td></article>"
    assert texts(boilerplate(html)) == [PROSE]


def test_no_content_region_marks_everything_boilerplate() -> None:
    html = f"<body><footer><p>{PROSE}</p></footer></body>"
    result = boilerplate(html)
    assert [p.is_boilerplate for p in result] == [True]


@pytest.mark.parametrize(
    ("preset", "expected"),
    [
        pytest.param(Extraction.justext, Extraction(min_text_length=70, max_link_density=0.2), id="justext"),
        pytest.param(Extraction.goose3, Extraction(min_text_length=20, max_link_density=0.65), id="goose3"),
    ],
)
def test_presets(preset: Callable[[], Extraction], expected: Extraction) -> None:
    assert preset() == expected


def test_default_thresholds() -> None:
    assert Extraction() == Extraction(min_text_length=25, max_link_density=0.5, headings_are_content=True)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        pytest.param(lambda: Extraction(min_text_length=-1), "min_text_length must not be negative", id="negative"),
        pytest.param(lambda: Extraction(max_link_density=-0.1), "max_link_density must be between 0 and 1", id="below"),
        pytest.param(lambda: Extraction(max_link_density=1.5), "max_link_density must be between 0 and 1", id="above"),
    ],
)
def test_invalid_options_raise(build: Callable[[], Extraction], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()
