from __future__ import annotations

import pytest

from turbohtml import MicrodataItem, RdfaItem, parse

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
def test_microdata(html: str, expected: list[MicrodataItem]) -> None:
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


_SCHEMA = "http://schema.org/"


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
    assert parse(html).rdfa(base_url=_BASE) == [
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
