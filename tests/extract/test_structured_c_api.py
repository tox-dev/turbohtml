from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

import turbohtml
from turbohtml._html import _microdata_as_dict

if TYPE_CHECKING:
    from turbohtml.extract import MicrodataItem


def _item(markup: str) -> MicrodataItem:
    """Return the first Microdata item the page carries."""
    items = turbohtml.parse(markup).microdata()
    assert items
    return items[0]


def test_a_bare_item_renders_its_properties() -> None:
    markup = '<div itemscope><span itemprop="name">Ada</span></div>'
    assert _microdata_as_dict(_item(markup)) == {"properties": {"name": ["Ada"]}}


def test_the_type_is_whitespace_split() -> None:
    markup = '<div itemscope itemtype="https://schema.org/Person https://schema.org/Thing"></div>'
    shaped = _microdata_as_dict(_item(markup))
    assert shaped["type"] == ["https://schema.org/Person", "https://schema.org/Thing"]


def test_the_identifier_is_carried_verbatim() -> None:
    markup = '<div itemscope itemid="urn:isbn:1"></div>'
    assert _microdata_as_dict(_item(markup))["id"] == "urn:isbn:1"


def test_an_item_without_a_type_or_id_omits_them() -> None:
    shaped = _microdata_as_dict(_item("<div itemscope></div>"))
    assert "type" not in shaped
    assert "id" not in shaped


def test_a_nested_item_shapes_recursively() -> None:
    markup = (
        '<div itemscope itemtype="https://schema.org/Person">'
        '<span itemprop="name">Ada</span>'
        '<div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">'
        '<span itemprop="street">1 Main</span>'
        "</div></div>"
    )
    shaped = _microdata_as_dict(_item(markup))
    properties = shaped["properties"]
    assert isinstance(properties, dict)
    address = properties["address"]
    assert isinstance(address, list)
    assert address[0] == {"type": ["https://schema.org/PostalAddress"], "properties": {"street": ["1 Main"]}}


def test_a_property_with_several_values_keeps_document_order() -> None:
    markup = '<div itemscope><span itemprop="tag">one</span><span itemprop="tag">two</span></div>'
    properties = _microdata_as_dict(_item(markup))["properties"]
    assert isinstance(properties, dict)
    assert properties["tag"] == ["one", "two"]


def test_the_public_json_matches_the_shaping() -> None:
    markup = '<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">Ada</span></div>'
    item = _item(markup)
    assert json.loads(item.json()) == _microdata_as_dict(item)


class _WrongProperties:
    """A stand-in carrying a `properties` attribute that is not a mapping."""

    properties = "not a mapping"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("notanitem", id="a-string"),
        pytest.param(5, id="a-number"),
        pytest.param(None, id="none"),
        pytest.param(_WrongProperties(), id="properties-is-not-a-mapping"),
    ],
)
def test_the_binding_rejects_what_is_not_an_item(value: object) -> None:
    with pytest.raises(TypeError, match="expected a MicrodataItem"):
        _microdata_as_dict(value)  # ty: ignore[invalid-argument-type]  # the argument check is the point


@pytest.mark.parametrize(
    ("block", "kept"),
    [
        pytest.param('{"@type": "Person"}', True, id="an-object"),
        pytest.param('[{"@type": "Person"}]', True, id="an-array"),
        pytest.param("null", False, id="null-carries-no-node"),
        pytest.param('"a string"', False, id="a-scalar-string"),
        pytest.param("42", False, id="a-scalar-number"),
        pytest.param("{not json at all}", False, id="malformed"),
        pytest.param("", False, id="empty"),
    ],
)
def test_only_a_node_object_survives_json_ld(block: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    found = turbohtml.parse(f'<script type="application/ld+json">{block}</script>').json_ld()
    assert bool(found) is kept


def test_several_blocks_keep_only_the_ones_carrying_data() -> None:
    markup = (
        '<script type="application/ld+json">{"a": 1}</script>'
        '<script type="application/ld+json">null</script>'
        '<script type="application/ld+json">[2]</script>'
    )
    assert turbohtml.parse(markup).json_ld() == [{"a": 1}, [2]]
