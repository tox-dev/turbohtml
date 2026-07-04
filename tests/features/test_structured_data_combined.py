from __future__ import annotations

import pytest

from turbohtml import Document, MicrodataItem, StructuredData, parse

_RICH = (
    '<head><meta property="og:title" content="T"></head>'
    '<body><script type="application/ld+json">{"@type": "Thing"}</script>'
    '<div itemscope><span itemprop="name">Ada</span></div></body>'
)


@pytest.fixture
def rich() -> Document:
    return parse(_RICH)


def test_structured_data_record(rich: Document) -> None:
    assert rich.structured_data() == StructuredData(
        json_ld=[{"@type": "Thing"}],
        microdata=[MicrodataItem(type=None, id=None, properties={"name": ["Ada"]})],
        opengraph={"og:title": "T"},
        microformats=[],
        rdfa=[],
    )


def test_fields_match_per_format_helpers(rich: Document) -> None:
    data = rich.structured_data()
    assert data.json_ld == rich.json_ld()
    assert data.microdata == rich.microdata()
    assert data.opengraph == rich.opengraph()


def test_record_is_read_only(rich: Document) -> None:
    data = rich.structured_data()
    field = "json_ld"
    with pytest.raises(AttributeError):
        setattr(data, field, [])


def test_empty_document() -> None:
    assert parse("<p>nothing</p>").structured_data() == StructuredData(
        json_ld=[],
        microdata=[],
        opengraph={},
        microformats=[],
        rdfa=[],
    )


_RELATIVE = (
    '<meta property="og:image" content="/pic.png">'
    '<script type="application/ld+json">{"@id": "/thing"}</script>'
    '<div itemscope><a itemprop="link" href="/l">x</a></div>'
)


def test_structured_data_base_url_resolves_microdata_and_opengraph_not_json_ld() -> None:
    data = parse(_RELATIVE).structured_data(base_url="http://ex.com/dir/")
    assert data.opengraph == {"og:image": "http://ex.com/pic.png"}
    assert data.microdata == [MicrodataItem(type=None, id=None, properties={"link": ["http://ex.com/l"]})]
    assert data.json_ld == [{"@id": "/thing"}]  # json_ld @id resolution is out of scope, left verbatim


def test_structured_data_base_url_none_is_verbatim() -> None:
    data = parse(_RELATIVE).structured_data(base_url=None)
    assert data.opengraph == {"og:image": "/pic.png"}
    assert data.microdata == [MicrodataItem(type=None, id=None, properties={"link": ["/l"]})]


def test_structured_data_malformed_base_url_raises() -> None:
    with pytest.raises(ValueError, match="not a valid absolute URL"):
        parse(_RELATIVE).structured_data(base_url="http://[bad")
