"""rdfa: Node.rdfa() vocab/prefix expansion, property objects, nested typeof and URL resolution.

RDFa and microdata are the two attribute-based structured-data formats the same C walk drives; the
microdata surface lives in test_extract_microdata.py.
"""

from __future__ import annotations

import pytest

from turbohtml import RdfaItem, parse

_SCHEMA = "http://schema.org/"

_BASE = "http://ex.com/dir/"


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
