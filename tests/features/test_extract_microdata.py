"""microdata: the extract.microdata entry point and the MicrodataItem get/get_all/json accessors."""

from __future__ import annotations

import json

import pytest

from turbohtml import MicrodataItem, parse
from turbohtml.extract import microdata

_NESTED = (
    '<div itemscope itemtype="https://schema.org/Person">'
    '<span itemprop="name">Ada</span>'
    '<span itemprop="name">Lovelace</span>'
    '<div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">'
    '<span itemprop="city">London</span>'
    "</div>"
    "</div>"
)


def test_microdata_returns_top_level_items() -> None:
    items = microdata('<div itemscope><span itemprop="name">Ada</span></div>')
    assert items == [MicrodataItem(type=None, id=None, properties={"name": ["Ada"]})]


def test_microdata_matches_document_method() -> None:
    assert microdata(_NESTED) == parse(_NESTED).microdata()


def test_microdata_empty_without_items() -> None:
    assert microdata("<p>nothing here</p>") == []


@pytest.fixture
def person() -> MicrodataItem:
    return microdata(_NESTED)[0]


def test_get_returns_first_value(person: MicrodataItem) -> None:
    assert person.get("name") == "Ada"


def test_get_returns_nested_item(person: MicrodataItem) -> None:
    assert person.get("address") == MicrodataItem(
        type="https://schema.org/PostalAddress", id=None, properties={"city": ["London"]}
    )


def test_get_missing_property_is_none(person: MicrodataItem) -> None:
    assert person.get("missing") is None


def test_get_all_returns_every_value(person: MicrodataItem) -> None:
    assert person.get_all("name") == ["Ada", "Lovelace"]


def test_get_all_missing_property_is_empty(person: MicrodataItem) -> None:
    assert person.get_all("missing") == []


def test_json_renders_type_id_and_nested_tree() -> None:
    item = microdata(
        '<div itemscope itemtype="https://schema.org/Book https://schema.org/Thing" itemid="urn:isbn:1">'
        '<span itemprop="name">B</span>'
        '<div itemprop="author" itemscope><span itemprop="name">A</span></div>'
        "</div>"
    )[0]
    assert json.loads(item.json()) == {
        "type": ["https://schema.org/Book", "https://schema.org/Thing"],
        "id": "urn:isbn:1",
        "properties": {"name": ["B"], "author": [{"properties": {"name": ["A"]}}]},
    }


def test_json_omits_absent_type_and_id() -> None:
    item = microdata('<div itemscope><span itemprop="name">Ada</span></div>')[0]
    assert json.loads(item.json()) == {"properties": {"name": ["Ada"]}}


def test_json_is_two_space_indented() -> None:
    item = microdata('<div itemscope><span itemprop="name">Ada</span></div>')[0]
    assert item.json() == '{\n  "properties": {\n    "name": [\n      "Ada"\n    ]\n  }\n}'


def test_microdata_resolves_url_against_base() -> None:
    items = microdata('<div itemscope><a itemprop="u" href="/l">x</a></div>', "http://x.com/dir/")
    assert items == [MicrodataItem(type=None, id=None, properties={"u": ["http://x.com/l"]})]


def test_microdata_base_url_omitted_is_verbatim() -> None:
    items = microdata('<div itemscope><a itemprop="u" href="/l">x</a></div>')
    assert items == [MicrodataItem(type=None, id=None, properties={"u": ["/l"]})]
