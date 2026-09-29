from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

import turbohtml
from turbohtml import Document, RdfaItem, StructuredData, parse
from turbohtml._html import _microdata_as_dict
from turbohtml.extract import MicrodataItem, OpenGraph, microdata, opengraph

if TYPE_CHECKING:
    from turbohtml.extract._structured_data import JSONValue


_RICH = (
    '<head><meta property="og:title" content="T"><meta name="dc.title" content="D"></head>'
    '<body><script type="application/ld+json">{"@type": "Thing"}</script>'
    '<div itemscope><span itemprop="name">Ada</span></div>'
    '<div vocab="http://schema.org/" typeof="Person"><span property="name">Grace</span></div></body>'
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
        rdfa=[
            RdfaItem(
                vocab="http://schema.org/",
                type=["http://schema.org/Person"],
                resource=None,
                properties={"http://schema.org/name": ["Grace"]},
            ),
        ],
        dublin_core={"dc.title": "D"},
    )


def test_fields_match_per_format_helpers(rich: Document) -> None:
    data = rich.structured_data()
    assert data.json_ld == rich.json_ld()
    assert data.microdata == rich.microdata()
    # structured_data keeps the raw og:/twitter: map; opengraph() returns the og:-stripped OpenGraph record
    assert data.opengraph == {"og:title": "T"}
    assert rich.opengraph() == {"title": "T"}
    assert data.rdfa == rich.rdfa()
    assert data.dublin_core == rich.dublin_core()


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
        dublin_core={},
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
            '<div itemscope><span itemprop="a b a">v</span></div>',
            [MicrodataItem(type=None, id=None, properties={"a": ["v"], "b": ["v"]})],
            id="duplicate-names-collapse",
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
            '<div id=a><span itemprop="a">first</span></div>'
            '<div id=ba><span itemprop="ba">different length</span></div>'
            '<div id=q><span itemprop="q">collision</span></div>'
            '<div id=a><span itemprop="a">duplicate</span></div>'
            '<div itemscope itemref="a ba q missing"></div>',
            [
                MicrodataItem(
                    type=None,
                    id=None,
                    properties={"a": ["first"], "ba": ["different length"], "q": ["collision"]},
                )
            ],
            id="itemref-keeps-first-duplicate-id-after-collision",
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


_FULL = (
    '<meta property="og:title" content="The Rock">'
    '<meta property="og:type" content="video.movie">'
    '<meta property="og:image" content="http://x/rock.jpg">'
    '<meta property="og:url" content="http://x/tt0117500/">'
)


def test_opengraph_strips_prefix() -> None:
    assert opengraph('<meta property="og:title" content="Hello">') == {"title": "Hello"}


def test_opengraph_drops_twitter_tags() -> None:
    html = '<meta property="og:title" content="Hi"><meta name="twitter:card" content="summary">'
    assert dict(opengraph(html)) == {"title": "Hi"}


def test_opengraph_keeps_structured_subkeys() -> None:
    html = '<meta property="og:image" content="http://x/a.png"><meta property="og:image:width" content="400">'
    assert opengraph(html) == {"image": "http://x/a.png", "image:width": "400"}


def test_opengraph_empty_without_tags() -> None:
    assert dict(opengraph("<p>plain</p>")) == {}


def test_getitem_reads_stripped_key() -> None:
    assert opengraph('<meta property="og:title" content="Hello">')["title"] == "Hello"


def test_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        _ = opengraph('<meta property="og:title" content="Hello">')["type"]


def test_membership_and_get() -> None:
    og = opengraph('<meta property="og:title" content="Hello">')
    assert "title" in og
    assert og.get("missing") is None


def test_repr_wraps_properties() -> None:
    assert repr(opengraph('<meta property="og:title" content="Hi">')) == "OpenGraph({'title': 'Hi'})"


def test_is_valid_with_all_four_required() -> None:
    assert opengraph(_FULL).is_valid() is True


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<meta property="og:title" content="only">', id="one-property"),
        pytest.param("<p>no meta</p>", id="no-properties"),
    ],
)
def test_is_valid_false_when_required_missing(html: str) -> None:
    assert opengraph(html).is_valid() is False


def test_is_valid_false_when_required_empty() -> None:
    html = _FULL + '<meta property="og:url" content="">'
    assert opengraph(html).is_valid() is False


def test_direct_construction() -> None:
    og = OpenGraph({"title": "Hi", "type": "article", "image": "i", "url": "u"})
    assert og.is_valid() is True
    assert len(og) == 4


def test_opengraph_function_resolves_url_against_base() -> None:
    html = '<meta property="og:image" content="/rock.jpg"><meta property="og:title" content="R">'
    assert opengraph(html, "http://x.com/dir/") == {"image": "http://x.com/rock.jpg", "title": "R"}


def test_opengraph_function_base_url_omitted_is_verbatim() -> None:
    assert opengraph('<meta property="og:image" content="/rock.jpg">') == {"image": "/rock.jpg"}


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta property="og:title" content="Hello"><meta property="og:type" content="article">',
            {"title": "Hello", "type": "article"},
            id="opengraph-via-property",
        ),
        pytest.param(
            '<meta name="twitter:card" content="summary"><meta name="twitter:site" content="@x">',
            {},
            id="twitter-keys-dropped",
        ),
        pytest.param('<meta name="og:title" content="By name">', {"title": "By name"}, id="og-via-name"),
        pytest.param('<meta property="twitter:card" content="summary">', {}, id="twitter-via-property-dropped"),
        pytest.param(
            '<meta property="og:title" name="twitter:title" content="P">',
            {"title": "P"},
            id="property-wins-over-name",
        ),
        pytest.param(
            '<meta property="og:title" content="Hi"><meta name="twitter:card" content="c">',
            {"title": "Hi"},
            id="og-kept-twitter-dropped",
        ),
        pytest.param(
            '<meta property="description" name="og:title" content="N">',
            {"title": "N"},
            id="name-used-when-property-not-social",
        ),
        pytest.param(
            '<meta property name="og:title" content="N">',
            {"title": "N"},
            id="name-used-when-property-valueless",
        ),
        pytest.param('<meta property="og:title">', {"title": ""}, id="missing-content-empty-string"),
        pytest.param('<meta property="og:title" content>', {"title": ""}, id="valueless-content-empty-string"),
        pytest.param(
            '<meta property="og:image" content="a"><meta property="og:image:width" content="400">',
            {"image": "a", "image:width": "400"},
            id="structured-subkey-kept",
        ),
        pytest.param(
            '<meta property="og:title" content="first"><meta property="og:title" content="second">',
            {"title": "second"},
            id="last-occurrence-wins",
        ),
        pytest.param('<meta name="description" content="x">', {}, id="non-social-name"),
        pytest.param('<meta property="article:author" content="x">', {}, id="non-social-property"),
        pytest.param('<meta property="x" content="y">', {}, id="property-shorter-than-prefix"),
        pytest.param('<meta charset="utf-8">', {}, id="no-property-or-name"),
        pytest.param('<meta property name content="x">', {}, id="both-valueless"),
        pytest.param(
            '<meta property="twitter:title" name="og:title" content="P">',
            {},
            id="twitter-property-prevents-og-name-fallback",
        ),
        pytest.param('<meta property="og:" content="empty">', {"": "empty"}, id="empty-property"),
        pytest.param('<meta property="og:標題" content="日本語">', {"標題": "日本語"}, id="unicode-property"),
        pytest.param('<meta property="OG:title" content="upper">', {}, id="prefix-case-sensitive"),
        pytest.param("<p>not a meta</p>", {}, id="no-meta"),
    ],
)
def test_opengraph_document_method(html: str, expected: dict[str, str]) -> None:
    assert parse(html).opengraph() == expected


@pytest.mark.parametrize("combined", [pytest.param(False, id="standalone"), pytest.param(True, id="combined")])
def test_opengraph_duplicate_preserves_insertion_order(*, combined: bool) -> None:
    document: Final = parse(
        '<meta property="og:title" content="first"><meta name="twitter:card" content="summary">'
        '<meta property="og:type" content="article"><meta property="og:title" content="last">'
    )
    if combined:
        assert list(document.structured_data().opengraph.items()) == [
            ("og:title", "last"),
            ("twitter:card", "summary"),
            ("og:type", "article"),
        ]
    else:
        assert list(document.opengraph().items()) == [("title", "last"), ("type", "article")]


def test_opengraph_returns_record() -> None:
    result = parse('<meta property="og:title" content="Hi">').opengraph()
    assert isinstance(result, OpenGraph)
    assert result["title"] == "Hi"


def test_opengraph_keeps_twitter_in_structured_data_but_drops_it_in_record() -> None:
    html = '<meta property="og:title" content="T"><meta name="twitter:card" content="summary">'
    assert parse(html).structured_data().opengraph == {"og:title": "T", "twitter:card": "summary"}
    assert parse(html).opengraph() == {"title": "T"}


_OPENGRAPH_BASE = "http://ex.com/dir/"


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("og:url", id="og-url"),
        pytest.param("og:image", id="og-image"),
        pytest.param("og:image:url", id="og-image-url"),
        pytest.param("og:image:secure_url", id="og-image-secure-url"),
        pytest.param("og:audio", id="og-audio"),
        pytest.param("og:audio:url", id="og-audio-url"),
        pytest.param("og:audio:secure_url", id="og-audio-secure-url"),
        pytest.param("og:video", id="og-video"),
        pytest.param("og:video:url", id="og-video-url"),
        pytest.param("og:video:secure_url", id="og-video-secure-url"),
    ],
)
def test_opengraph_resolves_url_valued_key(key: str) -> None:
    html = f'<meta property="{key}" content="/a.png">'
    assert parse(html).opengraph(base_url=_OPENGRAPH_BASE) == {key[len("og:") :]: "http://ex.com/a.png"}


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("twitter:image", id="twitter-image"),
        pytest.param("twitter:image:src", id="twitter-image-src"),
        pytest.param("twitter:player", id="twitter-player"),
        pytest.param("twitter:player:stream", id="twitter-player-stream"),
    ],
)
def test_opengraph_drops_twitter_url_valued_key(key: str) -> None:
    html = f'<meta property="{key}" content="/a.png"><meta property="og:title" content="T">'
    assert parse(html).opengraph(base_url=_OPENGRAPH_BASE) == {"title": "T"}


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta property="og:image" content="a.png"><meta property="og:title" content="a.png">',
            {"image": "http://ex.com/dir/a.png", "title": "a.png"},
            id="only-url-key-resolved-not-same-length-string-key",
        ),
        pytest.param(
            '<meta property="og:image" content="http://cdn.com/x.png">',
            {"image": "http://cdn.com/x.png"},
            id="absolute-url-kept",
        ),
        pytest.param('<meta property="og:image" content>', {"image": ""}, id="empty-url-stays-empty"),
        pytest.param(
            '<meta property="og:Image" content="/a.png">',
            {"Image": "http://ex.com/a.png"},
            id="key-case-insensitive",
        ),
        pytest.param(
            '<meta property="og:image" content="http://[bad">',
            {"image": "http://[bad"},
            id="malformed-value-kept-verbatim",
        ),
    ],
)
def test_opengraph_resolution_cases(html: str, expected: dict[str, str]) -> None:
    assert parse(html).opengraph(base_url=_OPENGRAPH_BASE) == expected


def test_opengraph_ignores_malformed_twitter_url() -> None:
    document: Final = parse(
        '<meta property="twitter:image" content="http://[bad"><meta property="og:title" content="T">'
    )
    assert document.opengraph(base_url=_OPENGRAPH_BASE) == {"title": "T"}


def test_opengraph_base_href_refines_base_url() -> None:
    html = '<base href="http://b.com/sub/"><meta property="og:image" content="a.png">'
    assert parse(html).opengraph(base_url="http://ex.com/") == {"image": "http://b.com/sub/a.png"}


def test_opengraph_malformed_base_href_falls_back_to_base_url() -> None:
    html = '<base href="http://[bad"><meta property="og:image" content="a.png">'
    assert parse(html).opengraph(base_url="http://ex.com/d/") == {"image": "http://ex.com/d/a.png"}


def test_opengraph_base_url_none_is_verbatim() -> None:
    html = '<meta property="og:image" content="/a.png">'
    assert parse(html).opengraph(base_url=None) == {"image": "/a.png"}


def test_opengraph_malformed_base_url_raises() -> None:
    with pytest.raises(ValueError, match="not a valid absolute URL"):
        parse('<meta property="og:image" content="/a.png">').opengraph(base_url="http://[bad")


def test_opengraph_base_url_wrong_type_raises() -> None:
    with pytest.raises(TypeError, match="base_url must be a str or None"):
        parse("<p>x</p>").opengraph(base_url=123)  # ty: ignore[invalid-argument-type]  # non-str exercises TypeError


def test_opengraph_rejects_unexpected_argument() -> None:
    with pytest.raises(TypeError):
        parse("<p>x</p>").opengraph(nope=1)  # ty: ignore[unknown-argument]  # exercises the argument-parse failure


_SCHEMA = "http://schema.org/"

_RDFA_BASE = "http://ex.com/dir/"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            f'<div vocab="{_SCHEMA}" typeof="Person"><span property="name">Ada</span></div>',
            [RdfaItem(vocab=_SCHEMA, type=[f"{_SCHEMA}Person"], resource=None, properties={f"{_SCHEMA}name": ["Ada"]})],
            id="vocab-expands-bare-term",
        ),
        pytest.param(
            '<div typeof="Thing"><span property="name">x</span></div>',
            [RdfaItem(vocab=None, type=["Thing"], resource=None, properties={"name": ["x"]})],
            id="no-vocab-keeps-terms-verbatim",
        ),
        pytest.param(
            f'<div vocab="{_SCHEMA}" typeof="Person Agent"><span property="a b">v</span></div>',
            [
                RdfaItem(
                    vocab=_SCHEMA,
                    type=[f"{_SCHEMA}Person", f"{_SCHEMA}Agent"],
                    resource=None,
                    properties={f"{_SCHEMA}a": ["v"], f"{_SCHEMA}b": ["v"]},
                ),
            ],
            id="multiple-typeof-and-property-tokens",
        ),
        pytest.param(
            '<div typeof="schema:Person"><span property="schema:name">x</span></div>',
            [RdfaItem(vocab=None, type=[f"{_SCHEMA}Person"], resource=None, properties={f"{_SCHEMA}name": ["x"]})],
            id="initial-context-prefix-schema",
        ),
        pytest.param(
            '<div prefix="ff: http://ff.test/" typeof="ff:Person"><span property="ff:name">x</span></div>',
            [
                RdfaItem(
                    vocab=None,
                    type=["http://ff.test/Person"],
                    resource=None,
                    properties={"http://ff.test/name": ["x"]},
                ),
            ],
            id="page-prefix-declaration",
        ),
        pytest.param(
            '<div prefix="junk ff: http://ff.test/" typeof="ff:Thing"></div>',
            [RdfaItem(vocab=None, type=["http://ff.test/Thing"], resource=None, properties={})],
            id="prefix-stray-token-skipped",
        ),
        pytest.param(
            '<div prefix="ff:" typeof="ff:Thing"></div>',
            [RdfaItem(vocab=None, type=["ff:Thing"], resource=None, properties={})],
            id="prefix-dangling-declaration-dropped",
        ),
        pytest.param(
            '<div prefix="  ff: http://ff.test/  " typeof="ff:Thing"></div>',
            [RdfaItem(vocab=None, type=["http://ff.test/Thing"], resource=None, properties={})],
            id="prefix-surrounding-whitespace-tolerated",
        ),
        pytest.param(
            '<div prefix typeof="schema:Thing"></div>',
            [RdfaItem(vocab=None, type=[f"{_SCHEMA}Thing"], resource=None, properties={})],
            id="valueless-prefix-inherits-initial-context",
        ),
        pytest.param(
            '<div typeof="zz:Foo"><span property="zz:bar">v</span></div>',
            [RdfaItem(vocab=None, type=["zz:Foo"], resource=None, properties={"zz:bar": ["v"]})],
            id="undeclared-prefix-kept-verbatim",
        ),
        pytest.param(
            f'<div vocab="{_SCHEMA}" typeof="http://ex.test/T"><span property="http://ex.test/p">v</span></div>',
            [
                RdfaItem(
                    vocab=_SCHEMA,
                    type=["http://ex.test/T"],
                    resource=None,
                    properties={"http://ex.test/p": ["v"]},
                ),
            ],
            id="absolute-iri-term-kept-verbatim",
        ),
        pytest.param(
            '<div typeof="T"><span property="p" content="C">ignored</span></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["C"]})],
            id="content-literal-overrides-text",
        ),
        pytest.param(
            '<div typeof="T"><span property="p" content>text</span></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": [""]})],
            id="valueless-content-is-empty",
        ),
        pytest.param(
            '<div typeof="T"><a property="p" resource="/r" href="/h">x</a></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["/r"]})],
            id="resource-object-precedes-href",
        ),
        pytest.param(
            '<div typeof="T"><a property="p" href="/h">x</a></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["/h"]})],
            id="href-object",
        ),
        pytest.param(
            '<div typeof="T"><img property="p" src="/s"></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["/s"]})],
            id="src-object",
        ),
        pytest.param(
            '<div typeof="T"><time property="p" datetime="2020-01-01">Jan</time></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["2020-01-01"]})],
            id="time-datetime-literal",
        ),
        pytest.param(
            '<div typeof="T"><time property="p" datetime>Feb</time></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["Feb"]})],
            id="time-valueless-datetime-falls-to-text",
        ),
        pytest.param(
            '<div typeof="T"><time property="p">Mar</time></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["Mar"]})],
            id="time-without-datetime-uses-text",
        ),
        pytest.param(
            '<div typeof="T"><span property="a">1</span><span property="a">2</span></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"a": ["1", "2"]})],
            id="repeated-property-collects-values",
        ),
        pytest.param(
            '<div typeof="T"><p><span property="a">x</span></p></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"a": ["x"]})],
            id="property-inside-non-typeof-wrapper",
        ),
        pytest.param(
            '<div typeof="T"><span property="a">outer<span property="b">inner</span></span></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"a": ["outerinner"], "b": ["inner"]})],
            id="property-inside-property",
        ),
        pytest.param(
            '<div typeof="T"><span property>a</span><span property="">b</span><span property="p">c</span></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={"p": ["c"]})],
            id="valueless-and-empty-property-are-not-properties",
        ),
        pytest.param(
            '<div typeof="T"><span>plain wrapper</span></div>',
            [RdfaItem(vocab=None, type=["T"], resource=None, properties={})],
            id="non-property-wrapper-yields-no-property",
        ),
        pytest.param(
            "<div typeof>x</div>",
            [RdfaItem(vocab=None, type=[], resource=None, properties={})],
            id="valueless-typeof-is-untyped-resource",
        ),
        pytest.param(
            '<div typeof="T" about="/a"></div>',
            [RdfaItem(vocab=None, type=["T"], resource="/a", properties={})],
            id="subject-about",
        ),
        pytest.param(
            '<div typeof="T" resource="/r"></div>',
            [RdfaItem(vocab=None, type=["T"], resource="/r", properties={})],
            id="subject-resource",
        ),
        pytest.param(
            '<div typeof="T" href="/h"></div>',
            [RdfaItem(vocab=None, type=["T"], resource="/h", properties={})],
            id="subject-href",
        ),
        pytest.param(
            '<div typeof="T" src="/s"></div>',
            [RdfaItem(vocab=None, type=["T"], resource="/s", properties={})],
            id="subject-src",
        ),
        pytest.param(
            f'<div vocab="{_SCHEMA}" typeof="Person">'
            f'<div property="address" typeof="PostalAddress"><span property="city">London</span></div></div>',
            [
                RdfaItem(
                    vocab=_SCHEMA,
                    type=[f"{_SCHEMA}Person"],
                    resource=None,
                    properties={
                        f"{_SCHEMA}address": [
                            RdfaItem(
                                vocab=_SCHEMA,
                                type=[f"{_SCHEMA}PostalAddress"],
                                resource=None,
                                properties={f"{_SCHEMA}city": ["London"]},
                            ),
                        ],
                    },
                ),
            ],
            id="nested-typeof-is-a-property-value",
        ),
        pytest.param(
            '<div typeof="Outer"><div typeof="Inner"></div></div>',
            [RdfaItem(vocab=None, type=["Outer"], resource=None, properties={})],
            id="nested-typeof-without-property-not-top-level",
        ),
        pytest.param(
            '<div typeof="A"></div><div typeof="B"></div>',
            [
                RdfaItem(vocab=None, type=["A"], resource=None, properties={}),
                RdfaItem(vocab=None, type=["B"], resource=None, properties={}),
            ],
            id="two-sibling-top-level-items",
        ),
        pytest.param(
            f'<section><article><div typeof="{_SCHEMA[:-1]}"></div></article></section>',
            [RdfaItem(vocab=None, type=[_SCHEMA[:-1]], resource=None, properties={})],
            id="item-found-below-non-rdfa-containers",
        ),
        pytest.param("<p>no rdfa here</p>", [], id="no-rdfa"),
    ],
)
def test_rdfa(html: str, expected: list[RdfaItem]) -> None:
    assert parse(html).rdfa() == expected


def test_rdfa_inner_vocab_empty_clears_expansion() -> None:
    html = f'<div vocab="{_SCHEMA}" typeof="Person"><div vocab=""><span property="name">x</span></div></div>'
    assert parse(html).rdfa() == [
        RdfaItem(vocab=_SCHEMA, type=[f"{_SCHEMA}Person"], resource=None, properties={"name": ["x"]}),
    ]


def test_rdfa_prefix_inherited_into_descendants() -> None:
    html = (
        '<div prefix="ff: http://ff.test/">'
        f'<div vocab="{_SCHEMA}" typeof="Person"><span property="ff:knows">x</span></div></div>'
    )
    assert parse(html).rdfa() == [
        RdfaItem(
            vocab=_SCHEMA,
            type=[f"{_SCHEMA}Person"],
            resource=None,
            properties={"http://ff.test/knows": ["x"]},
        ),
    ]


def test_rdfa_get_and_get_all() -> None:
    html = '<div typeof="T"><span property="a">1</span><span property="a">2</span></div>'
    item = parse(html).rdfa()[0]
    assert item.get("a") == "1"
    assert item.get("missing") is None
    assert item.get_all("a") == ["1", "2"]
    assert item.get_all("missing") == []


def test_rdfa_typeof_only_in_text_is_not_a_resource() -> None:
    # the no-typeof-attribute fast path keys on the attribute, not the word: prose mentioning typeof yields nothing
    assert parse("<p>the typeof operator and vocab of JS</p>").rdfa() == []


def test_rdfa_resolves_iri_objects_and_subject() -> None:
    html = (
        '<div typeof="T" resource="/me">'
        '<a property="link" href="/l">x</a>'
        '<span property="text">/keep</span>'
        '<span property="lit" content="/keep">x</span></div>'
    )
    assert parse(html).rdfa(base_url=_RDFA_BASE) == [
        RdfaItem(
            vocab=None,
            type=["T"],
            resource="http://ex.com/me",
            properties={"link": ["http://ex.com/l"], "text": ["/keep"], "lit": ["/keep"]},
        ),
    ]


def test_rdfa_base_href_refines_base_url() -> None:
    html = '<base href="http://b.com/sub/"><div typeof="T"><a property="p" href="x">y</a></div>'
    assert parse(html).rdfa(base_url="http://ex.com/")[0].properties["p"] == ["http://b.com/sub/x"]


def test_rdfa_base_url_none_is_verbatim() -> None:
    html = '<div typeof="T"><a property="p" href="/l">x</a></div>'
    assert parse(html).rdfa(base_url=None)[0].properties["p"] == ["/l"]


def test_rdfa_malformed_base_url_raises() -> None:
    with pytest.raises(ValueError, match="not a valid absolute URL"):
        parse('<div typeof="T"></div>').rdfa(base_url="http://[bad")


def test_rdfa_base_url_wrong_type_raises() -> None:
    with pytest.raises(TypeError, match="base_url must be a str or None"):
        parse("<p>x</p>").rdfa(base_url=123)  # ty: ignore[invalid-argument-type]  # non-str exercises TypeError


def test_rdfa_rejects_unexpected_argument() -> None:
    with pytest.raises(TypeError):
        parse("<p>x</p>").rdfa(nope=1)  # ty: ignore[unknown-argument]  # exercises the argument-parse failure


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta name="dc.title" content="Hi"><meta name="dc.creator" content="Ada">',
            {"dc.title": "Hi", "dc.creator": "Ada"},
            id="dc-names",
        ),
        pytest.param('<meta name="dcterms.created" content="2020">', {"dcterms.created": "2020"}, id="dcterms-name"),
        pytest.param('<meta name="DC.Title" content="Hi">', {"dc.title": "Hi"}, id="name-lower-cased"),
        pytest.param(
            '<meta name="dc.title" content="first"><meta name="dc.title" content="second">',
            {"dc.title": "second"},
            id="last-occurrence-wins",
        ),
        pytest.param('<meta name="dc.title">', {"dc.title": ""}, id="missing-content-empty-string"),
        pytest.param('<meta name="dc.title" content>', {"dc.title": ""}, id="valueless-content-empty-string"),
        pytest.param('<meta name="keywords" content="x">', {}, id="non-dc-name-ignored"),
        pytest.param('<meta name="dc" content="x">', {}, id="name-shorter-than-prefix-ignored"),
        pytest.param('<meta name="dcx.title" content="x">', {}, id="near-miss-prefix-ignored"),
        pytest.param('<meta property="dc.title" content="x">', {}, id="property-not-name-ignored"),
        pytest.param('<meta name content="x">', {}, id="valueless-name-ignored"),
        pytest.param('<meta content="x">', {}, id="no-name-ignored"),
        pytest.param("<p>not a meta</p>", {}, id="no-meta"),
    ],
)
def test_dublin_core(html: str, expected: dict[str, str]) -> None:
    assert parse(html).dublin_core() == expected


@pytest.mark.parametrize(
    "reference",
    [
        pytest.param("", id="absent"),
        pytest.param("itemref", id="valueless"),
        pytest.param('itemref=""', id="empty"),
        pytest.param('itemref=" "', id="whitespace"),
        pytest.param("itemref=missing", id="unresolved"),
        pytest.param('itemref="last first last"', id="overlapping"),
    ],
)
def test_microdata_repeated_values_follow_tree_order(reference: str) -> None:
    document: Final[Document] = parse(
        f"<div itemscope {reference}>stray<!--comment-->"
        "<section><span id=first itemprop=name>first</span></section>"
        "<div itemprop=name>outer<span itemprop=name>inner</span></div>"
        "<span id=last itemprop=name>last</span></div>"
    )
    assert document.microdata() == [
        MicrodataItem(type=None, id=None, properties={"name": ["first", "outerinner", "inner", "last"]})
    ]


@pytest.mark.parametrize(
    "reference", [pytest.param("outer inner", id="parent-first"), pytest.param("inner outer", id="child-first")]
)
@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            "<span id=outer itemprop=name><span id=inner itemprop=name>inside</span>outside</span>",
            ["insideoutside", "inside"],
            id="parent-and-first-child",
        ),
        pytest.param(
            "<span id=outer itemprop=name>outside</span><span id=inner itemprop=name>inside</span>",
            ["outside", "inside"],
            id="adjacent-siblings",
        ),
    ],
)
def test_microdata_referenced_properties_follow_tree_order(
    reference: str, markup: str, expected: list[str | MicrodataItem]
) -> None:
    document: Final[Document] = parse(f'<div itemscope itemref="{reference}"></div>{markup}')
    assert document.microdata() == [MicrodataItem(type=None, id=None, properties={"name": expected})]


def test_microdata_nested_scope_keeps_following_properties() -> None:
    document: Final[Document] = parse(
        "<div itemscope><section itemprop=child itemscope>"
        "<span itemprop=name>child</span></section><span itemprop=name>parent</span></div>"
    )
    assert document.microdata() == [
        MicrodataItem(
            type=None,
            id=None,
            properties={
                "child": [MicrodataItem(type=None, id=None, properties={"name": ["child"]})],
                "name": ["parent"],
            },
        )
    ]


@pytest.mark.parametrize(
    "size",
    [
        pytest.param(1, id="single"),
        pytest.param(4, id="tiny"),
        pytest.param(31, id="small"),
        pytest.param(32, id="indexed"),
        pytest.param(1000, id="wide"),
    ],
)
@pytest.mark.parametrize("depth", [pytest.param(0, id="siblings"), pytest.param(20, id="nested")])
def test_microdata_shuffled_references_keep_tree_order(size: int, depth: int) -> None:
    references: Final = " ".join(f"r{index}" for offset in (0, 1) for index in range(offset, size, 2))
    document: Final = parse(
        f'<div itemscope itemref="{references}"></div>'
        + "<section>" * depth
        + "".join(f'<span id="r{index}" itemprop=name>{index}</span>' for index in range(size))
        + "</section>" * depth
    )
    assert document.microdata() == [
        MicrodataItem(type=None, id=None, properties={"name": [str(index) for index in range(size)]})
    ]


def test_microdata_wide_item_keeps_all_values() -> None:
    document: Final[Document] = parse(
        "<div itemscope>" + "".join(f"<span itemprop=name>{index}</span>" for index in range(1_000)) + "</div>"
    )
    assert document.microdata() == [
        MicrodataItem(type=None, id=None, properties={"name": [str(index) for index in range(1_000)]})
    ]


@pytest.mark.parametrize("reference", [pytest.param("", id="local"), pytest.param("itemref=hidden", id="referenced")])
@pytest.mark.parametrize(
    "attribute", [pytest.param("itemprop", id="valueless"), pytest.param('itemprop=""', id="empty")]
)
def test_microdata_empty_property_skips_cyclic_item_graph(reference: str, attribute: str) -> None:
    document: Final[Document] = parse(
        f"<div itemscope {reference}><span itemprop=name>kept</span>"
        f"<div id=hidden itemscope {attribute} itemref=first></div></div>"
        "<div id=first itemscope itemprop=child itemref=second></div>"
        "<div id=second itemscope itemprop=child itemref=first></div>"
    )
    assert document.microdata() == [MicrodataItem(type=None, id=None, properties={"name": ["kept"]})]


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


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<script type="application/ld+json">{"@type": "Person", "name": "Ada"}</script>',
            [{"@type": "Person", "name": "Ada"}],
            id="single-object",
        ),
        pytest.param(
            '<script type="application/ld+json">[{"a": 1}, {"b": 2}]</script>',
            [[{"a": 1}, {"b": 2}]],
            id="array-value",
        ),
        pytest.param(
            '<script type="application/ld+json">{"n": 1}</script><script type="application/ld+json">{"n": 2}</script>',
            [{"n": 1}, {"n": 2}],
            id="multiple-blocks-in-document-order",
        ),
        pytest.param(
            '<script type="application/ld+json">{not valid json}</script>'
            '<script type="application/ld+json">{"ok": true}</script>',
            [{"ok": True}],
            id="invalid-block-skipped",
        ),
        pytest.param(
            '<script type="application/ld+json">{"a": NaN}</script>'
            '<script type="application/ld+json">{"a": Infinity}</script>'
            '<script type="application/ld+json">[-Infinity]</script>'
            '<script type="application/ld+json">{"ok": 1.5}</script>',
            [{"ok": 1.5}],
            id="non-json-constants-skipped",
        ),
        pytest.param('<script type="APPLICATION/LD+JSON">{"ok": 1}</script>', [{"ok": 1}], id="type-uppercase"),
        pytest.param('<script type="  application/ld+json  ">{"ok": 1}</script>', [{"ok": 1}], id="type-whitespace"),
        pytest.param('<script type="Application/LD+Json">{"ok": 1}</script>', [{"ok": 1}], id="type-mixed-case"),
        pytest.param('<script>{"ok": 1}</script>', [], id="no-type-attribute"),
        pytest.param('<script type>{"ok": 1}</script>', [], id="valueless-type-attribute"),
        pytest.param('<script type="text/javascript">{"ok": 1}</script>', [], id="other-type"),
        pytest.param('<script type="application/ld+jsox">{"ok": 1}</script>', [], id="same-length-mismatch"),
        pytest.param('<script type="   ">{"ok": 1}</script>', [], id="all-whitespace-type"),
        pytest.param("<p>no script at all</p>", [], id="no-script"),
        pytest.param(
            '<script type="application/ld+json">null</script>'
            '<script type="application/ld+json">{"@type": "Thing"}</script>',
            [{"@type": "Thing"}],
            id="null-payload-dropped",
        ),
        pytest.param(
            '<script type="application/ld+json">42</script>'
            '<script type="application/ld+json">"text"</script>'
            '<script type="application/ld+json">true</script>'
            '<script type="application/ld+json">[{"@id": "x"}]</script>',
            [[{"@id": "x"}]],
            id="scalar-payloads-dropped-list-kept",
        ),
    ],
)
def test_json_ld(html: str, expected: list[JSONValue]) -> None:
    assert parse(html).json_ld() == expected
