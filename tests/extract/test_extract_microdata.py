"""microdata: extract.microdata / Node.microdata() and the MicrodataItem get/get_all/json accessors.

Both entry points share one C extractor; test_microdata_matches_document_method pins their equivalence,
so the function-level accessor cases and the parametrized DOM-method cases together cover the surface
without duplicating each other.
"""

from __future__ import annotations

import json

import pytest

from turbohtml import MicrodataItem, parse
from turbohtml.extract import microdata

_BY_TAG = (
    "<div itemscope>"
    '<meta itemprop="meta" content="MC">'
    '<img itemprop="img" src="i.png">'
    '<a itemprop="a" href="/l">x</a>'
    '<object itemprop="obj" data="/d"></object>'
    '<data itemprop="data" value="42">forty</data>'
    '<meter itemprop="meter" value="7"></meter>'
    '<time itemprop="t1" datetime="2020-01-01">Jan</time>'
    '<time itemprop="t2">Feb</time>'
    '<time itemprop="t3" datetime>Mar</time>'
    '<span itemprop="text">plain text</span>'
    '<img itemprop="nosrc">'
    '<data itemprop="noval" value>z</data>'
    "</div>"
)

_NESTED = (
    '<div itemscope itemtype="https://schema.org/Person">'
    '<span itemprop="name">Ada</span>'
    '<div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">'
    '<span itemprop="city">London</span>'
    "</div>"
    "</div>"
)

_PERSON = (
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
    """The extract.microdata function and Node.microdata() are the same walk over one input."""
    assert microdata(_PERSON) == parse(_PERSON).microdata()


def test_microdata_empty_without_items() -> None:
    assert microdata("<p>nothing here</p>") == []


@pytest.fixture
def person() -> MicrodataItem:
    return microdata(_PERSON)[0]


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


def test_microdata_function_resolves_url_against_base() -> None:
    items = microdata('<div itemscope><a itemprop="u" href="/l">x</a></div>', "http://x.com/dir/")
    assert items == [MicrodataItem(type=None, id=None, properties={"u": ["http://x.com/l"]})]


def test_microdata_function_base_url_omitted_is_verbatim() -> None:
    items = microdata('<div itemscope><a itemprop="u" href="/l">x</a></div>')
    assert items == [MicrodataItem(type=None, id=None, properties={"u": ["/l"]})]


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<div itemscope><span itemprop="name">Ada</span></div>',
            [MicrodataItem(type=None, id=None, properties={"name": ["Ada"]})],
            id="simple-text-property",
        ),
        pytest.param(
            '<div itemscope itemtype="https://schema.org/Thing https://schema.org/Person"></div>',
            [MicrodataItem(type="https://schema.org/Thing https://schema.org/Person", id=None, properties={})],
            id="itemtype-verbatim",
        ),
        pytest.param(
            '<div itemscope itemtype><span itemprop="a">x</span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["x"]})],
            id="valueless-itemtype-is-none",
        ),
        pytest.param(
            '<div itemscope itemid="urn:isbn:1" itemtype="https://schema.org/Book"><span itemprop="n">B</span></div>',
            [MicrodataItem(type="https://schema.org/Book", id="urn:isbn:1", properties={"n": ["B"]})],
            id="itemid",
        ),
        pytest.param(
            '<div itemscope itemid><span itemprop="a">x</span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["x"]})],
            id="valueless-itemid-is-none",
        ),
        pytest.param(
            '<div itemscope><span itemprop="a b">v</span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["v"], "b": ["v"]})],
            id="multiple-names-share-value",
        ),
        pytest.param(
            '<div itemscope><span itemprop="x">1</span><span itemprop="x">2</span></div>',
            [MicrodataItem(type=None, id=None, properties={"x": ["1", "2"]})],
            id="repeated-name-collects-values",
        ),
        pytest.param(
            _BY_TAG,
            [
                MicrodataItem(
                    type=None,
                    id=None,
                    properties={
                        "meta": ["MC"],
                        "img": ["i.png"],
                        "a": ["/l"],
                        "obj": ["/d"],
                        "data": ["42"],
                        "meter": ["7"],
                        "t1": ["2020-01-01"],
                        "t2": ["Feb"],
                        "t3": ["Mar"],
                        "text": ["plain text"],
                        "nosrc": [""],
                        "noval": [""],
                    },
                ),
            ],
            id="property-values-by-tag",
        ),
        pytest.param(
            _NESTED,
            [
                MicrodataItem(
                    type="https://schema.org/Person",
                    id=None,
                    properties={
                        "name": ["Ada"],
                        "address": [
                            MicrodataItem(
                                type="https://schema.org/PostalAddress",
                                id=None,
                                properties={"city": ["London"]},
                            ),
                        ],
                    },
                ),
            ],
            id="nested-item",
        ),
        pytest.param(
            '<div itemscope><p><span itemprop="wrapped">W</span></p></div>',
            [MicrodataItem(type=None, id=None, properties={"wrapped": ["W"]})],
            id="property-inside-non-itemprop-wrapper",
        ),
        pytest.param(
            '<div itemscope><span itemprop>a</span><span itemprop="">b</span><span itemprop="real">c</span></div>',
            [MicrodataItem(type=None, id=None, properties={"real": ["c"]})],
            id="valueless-and-empty-itemprop-are-not-properties",
        ),
        pytest.param('<span itemscope itemprop="x">orphan</span>', [], id="itemscope-with-itemprop-not-top-level"),
        pytest.param(
            "<div itemscope>"
            '<a itemprop="a" href=" /l ">x</a>'
            '<img itemprop="i" src=" i.png ">'
            '<object itemprop="o" data=" /d "></object>'
            '<meta itemprop="m" content=" C ">'
            '<data itemprop="d" value=" V ">x</data>'
            '<meter itemprop="t" value=" 7 "></meter>'
            "</div>",
            [
                MicrodataItem(
                    type=None,
                    id=None,
                    properties={
                        "a": ["/l"],
                        "i": ["i.png"],
                        "o": ["/d"],
                        "m": [" C "],
                        "d": [" V "],
                        "t": [" 7 "],
                    },
                ),
            ],
            id="url-props-strip-whitespace-value-attrs-verbatim",
        ),
        pytest.param(
            '<div itemscope><a itemprop="v" href>x</a><a itemprop="b" href="   ">y</a></div>',
            [MicrodataItem(type=None, id=None, properties={"v": [""], "b": [""]})],
            id="url-props-valueless-or-blank-are-empty",
        ),
        pytest.param(
            '<div id=x><p itemprop="a">1</p></div><div itemscope itemref=x><p itemprop="a">2</p></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["1", "2"]})],
            id="itemref-merges-in-tree-order",
        ),
        pytest.param(
            '<div itemscope><span itemprop="b">x</span><div><span itemprop="a">y</span></div></div>',
            [MicrodataItem(type=None, id=None, properties={"b": ["x"], "a": ["y"]})],
            id="shallower-property-before-deeper-in-tree-order",
        ),
        pytest.param(
            "<div id></div><div id=ab></div><div id=y></div>"
            '<div id=t><span itemprop="b">ref</span></div>'
            '<div itemscope itemref=" t "><span itemprop="a">own</span></div>',
            [MicrodataItem(type=None, id=None, properties={"b": ["ref"], "a": ["own"]})],
            id="itemref-resolves-past-non-matching-ids",
        ),
        pytest.param(
            '<div itemscope itemref><span itemprop="a">x</span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["x"]})],
            id="valueless-itemref-ignored",
        ),
        pytest.param(
            '<div itemscope itemref=missing><span itemprop="a">x</span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["x"]})],
            id="unresolved-itemref-token-ignored",
        ),
        pytest.param(
            '<div itemscope itemref=inner><p id=inner itemprop="a">1</p></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["1"]})],
            id="itemref-to-own-descendant-not-double-counted",
        ),
        pytest.param(
            '<div itemscope><div><span itemprop="a">1</span></div><div><span itemprop="b">2</span></div></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["1"], "b": ["2"]})],
            id="properties-under-separate-wrappers",
        ),
        pytest.param(
            '<div itemscope>stray<span itemprop="a">x</span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["x"]})],
            id="text-node-child-skipped",
        ),
        pytest.param(
            '<div itemscope><span itemprop="a">outer<span itemprop="b">inner</span></span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["outerinner"], "b": ["inner"]})],
            id="nested-property-inside-property",
        ),
        pytest.param(
            '<div itemscope itemref=deep><span itemprop="a">outer<span id=deep itemprop="b">inner</span></span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["outerinner"], "b": ["inner"]})],
            id="itemref-to-descendant-sorts-under-its-ancestor",
        ),
        pytest.param("<p>nothing here</p>", [], id="no-microdata"),
    ],
)
def test_microdata_document_method(html: str, expected: list[MicrodataItem]) -> None:
    assert parse(html).microdata() == expected


_BASE = "http://ex.com/dir/"


def test_microdata_resolves_url_props_only() -> None:
    html = (
        "<div itemscope>"
        '<a itemprop="a" href="/l">x</a>'
        '<img itemprop="i" src="i.png">'
        '<object itemprop="o" data="/d"></object>'
        '<meta itemprop="m" content="/not-url">'
        '<data itemprop="d" value="/keep">z</data>'
        '<time itemprop="t" datetime="/keep">now</time>'
        "</div>"
    )
    assert parse(html).microdata(base_url=_BASE) == [
        MicrodataItem(
            type=None,
            id=None,
            properties={
                "a": ["http://ex.com/l"],
                "i": ["http://ex.com/dir/i.png"],
                "o": ["http://ex.com/d"],
                "m": ["/not-url"],
                "d": ["/keep"],
                "t": ["/keep"],
            },
        ),
    ]


def test_microdata_resolves_nested_item() -> None:
    html = (
        "<div itemscope>"
        '<a itemprop="a" href="/l">x</a>'
        '<div itemprop="child" itemscope><img itemprop="i" src="c.png"></div>'
        "</div>"
    )
    assert parse(html).microdata(base_url=_BASE) == [
        MicrodataItem(
            type=None,
            id=None,
            properties={
                "a": ["http://ex.com/l"],
                "child": [MicrodataItem(type=None, id=None, properties={"i": ["http://ex.com/dir/c.png"]})],
            },
        ),
    ]


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<div itemscope><a itemprop="p" href="http://cdn.com/x">x</a></div>',
            "http://cdn.com/x",
            id="absolute-url-kept",
        ),
        pytest.param('<div itemscope><img itemprop="p" src></div>', "", id="empty-url-stays-empty"),
        pytest.param(
            '<div itemscope><a itemprop="p" href="http://[bad">x</a></div>',
            "http://[bad",
            id="malformed-value-kept-verbatim",
        ),
    ],
)
def test_microdata_url_resolution_cases(html: str, expected: str) -> None:
    assert parse(html).microdata(base_url=_BASE)[0].properties["p"] == [expected]


def test_microdata_base_href_refines_base_url() -> None:
    html = '<base href="http://b.com/sub/"><div itemscope><a itemprop="a" href="p">x</a></div>'
    assert parse(html).microdata(base_url="http://ex.com/")[0].properties["a"] == ["http://b.com/sub/p"]


def test_microdata_base_url_none_is_verbatim() -> None:
    html = '<div itemscope><a itemprop="a" href="/l">x</a></div>'
    assert parse(html).microdata(base_url=None)[0].properties["a"] == ["/l"]


def test_microdata_malformed_base_url_raises() -> None:
    with pytest.raises(ValueError, match="not a valid absolute URL"):
        parse('<div itemscope><a itemprop="a" href="/l">x</a></div>').microdata(base_url="http://[bad")


def test_microdata_base_url_wrong_type_raises() -> None:
    with pytest.raises(TypeError, match="base_url must be a str or None"):
        parse("<p>x</p>").microdata(base_url=123)  # ty: ignore[invalid-argument-type]  # non-str exercises TypeError
