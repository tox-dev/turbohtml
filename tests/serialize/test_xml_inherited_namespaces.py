from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Html, Indent, parse, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps

_XLINK: Final[str] = 'xmlns:xlink="http://www.w3.org/1999/xlink"'


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        pytest.param('<root xmlns="urn:a"><child/></root>', '<child xmlns="urn:a"/>', id="default-namespace"),
        pytest.param('<root xmlns:p="urn:p"><p:child/></root>', '<p:child xmlns:p="urn:p"/>', id="prefixed-element"),
        pytest.param(
            '<root xmlns:p="urn:p"><child p:attr="v"/></root>',
            '<child xmlns:p="urn:p" p:attr="v"/>',
            id="prefixed-attribute",
        ),
        pytest.param(
            '<root xmlns:p="urn:old"><wrap xmlns:p="urn:new"><p:child/></wrap></root>',
            '<p:child xmlns:p="urn:new"/>',
            id="rebound-by-ancestor",
        ),
        pytest.param(
            '<root xmlns:p="urn:old"><p:child xmlns:p="urn:local"/></root>',
            '<p:child xmlns:p="urn:local"/>',
            id="rebound-on-element",
        ),
        pytest.param('<root xmlns="urn:a"><child xmlns=""/></root>', '<child xmlns=""/>', id="default-reset"),
        pytest.param(
            '<root xmlns="urn:a" xmlns:p="urn:p"><child p:attr="v"/></root>',
            '<child xmlns="urn:a" xmlns:p="urn:p" p:attr="v"/>',
            id="default-and-prefixed",
        ),
        pytest.param(
            '<root xmlns:xlink="urn:custom"><child xlink:href="v"/></root>',
            '<child xmlns:xlink="urn:custom" xlink:href="v"/>',
            id="custom-xlink-prefix",
        ),
        pytest.param(
            '<root xmlns="urn:a" abcde="x" abcdef="y" abcdefg="z"><child title="v"/></root>',
            '<child xmlns="urn:a" title="v"/>',
            id="plain-attributes-of-varied-length",
        ),
        pytest.param(
            (
                '<root xmlns:xlink="urn:x"><outer><wrap id="x" placeholder="x">'
                '<child xlink:href="v"/></wrap></outer></root>'
            ),
            '<child xmlns:xlink="urn:x" xlink:href="v"/>',
            id="through-intermediate-elements",
        ),
        pytest.param('<root xmlns:u="urn:unused"><child/></root>', "<child/>", id="unused-prefix"),
        pytest.param(
            '<root xmlns="urn:a"><p:child xmlns:p="urn:p"/></root>',
            '<p:child xmlns:p="urn:p"/>',
            id="prefix-declared-on-element",
        ),
        pytest.param('<root xmlns:π="urn:pi"><π:child/></root>', '<π:child xmlns:π="urn:pi"/>', id="greek-prefix"),
        pytest.param('<root xmlns:中="urn:han"><中:child/></root>', '<中:child xmlns:中="urn:han"/>', id="han-prefix"),
        pytest.param(
            '<root xmlns:𐐀="urn:astral"><𐐀:child/></root>', '<𐐀:child xmlns:𐐀="urn:astral"/>', id="astral-prefix"
        ),
        pytest.param('<root xmlns:π="urn:unused"><child/></root>', "<child/>", id="unused-greek-prefix"),
        pytest.param('<root xmlns:long="urn:unused"><x/></root>', "<x/>", id="unused-long-prefix-short-element"),
        pytest.param('<root xmlns:long="urn:unused"><l/></root>', "<l/>", id="unused-long-prefix-one-letter-element"),
        pytest.param('<root xmlns:p="urn:unused"><p/></root>', "<p/>", id="unused-prefix-same-element-name"),
        pytest.param(
            '<root xmlns:p="urn:unused"><pchild/></root>', "<pchild/>", id="unused-prefix-element-starts-with-it"
        ),
        pytest.param('<root xmlns:p="urn:p"><child q="v"/></root>', '<child q="v"/>', id="unrelated-attribute"),
        pytest.param(
            '<root xmlns:p="urn:unused"><child long="v"/></root>',
            '<child long="v"/>',
            id="unused-prefix-long-attribute",
        ),
        pytest.param(
            '<root xmlns:p="urn:unused" xmlns:q="urn:q"><child q:attr="v"/></root>',
            '<child xmlns:q="urn:q" q:attr="v"/>',
            id="used-prefix-next-to-unused",
        ),
        pytest.param(
            '<root xmlns:p="urn:p"><child a="v" p:b="v"/></root>',
            '<child xmlns:p="urn:p" a="v" p:b="v"/>',
            id="plain-and-prefixed-attributes",
        ),
    ],
)
def test_xml_subtree_inherits_namespace_bindings(xml: str, expected: str) -> None:
    node = parse_xml(xml).select("*")[-1]
    assert node.serialize(Html(xml=True)) == expected


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        pytest.param(
            '<root xmlns="urn:a" xmlns:p="urn:p"><p:child><nested/></p:child></root>',
            '<p:child xmlns="urn:a" xmlns:p="urn:p"><nested/></p:child>',
            id="prefixed-element-with-default",
        ),
        pytest.param(
            '<root xmlns:p="urn:p"><child><p:nested/></child></root>',
            '<child xmlns:p="urn:p"><p:nested/></child>',
            id="prefixed-descendant",
        ),
        pytest.param(
            '<root xmlns="urn:a"><p:child xmlns:p="urn:p">text<nested/></p:child></root>',
            '<p:child xmlns="urn:a" xmlns:p="urn:p">text<nested/></p:child>',
            id="prefix-declared-on-root-child",
        ),
        pytest.param(
            '<root xmlns:u="urn:unused"><child><a/><b/></child></root>',
            "<child><a/><b/></child>",
            id="unused-prefix",
        ),
        pytest.param(
            '<root xmlns:p="urn:old"><child><p:nested xmlns:p="urn:new"/></child></root>',
            '<child><p:nested xmlns:p="urn:new"/></child>',
            id="rebound-on-descendant",
        ),
    ],
)
def test_xml_subtree_descendants_use_inherited_bindings(xml: str, expected: str) -> None:
    node = parse_xml(xml).select("*")[1]
    assert node.serialize(Html(xml=True)) == expected


def test_xml_inner_serialization_inherits_namespace_bindings() -> None:
    root = parse_xml('<root xmlns="urn:a"><child/><other/></root>').select("root")[0]
    assert root.inner_xml == '<child xmlns="urn:a"/><other xmlns="urn:a"/>'


def test_xml_inner_option_inherits_namespace_bindings() -> None:
    root = parse_xml('<root xmlns="urn:a"><child/><other/></root>').select("root")[0]
    assert root.serialize(Html(xml=True), inner=True) == '<child xmlns="urn:a"/><other xmlns="urn:a"/>'


def test_xml_pretty_subtree_inherits_namespace_bindings() -> None:
    node = parse_xml('<root xmlns="urn:a"><child><nested/></child></root>').select("child")[0]
    assert node.serialize(Html(xml=True, layout=Indent(2))) == '<child xmlns="urn:a">\n  <nested/>\n</child>'


def test_xml_chunked_subtree_inherits_namespace_bindings() -> None:
    node = parse_xml('<root xmlns="urn:a"><child><nested/></child></root>').select("child")[0]
    assert "".join(node.serialize_iter(Html(xml=True))) == '<child xmlns="urn:a"><nested/></child>'


def test_xml_chunked_inner_inherits_namespace_bindings() -> None:
    root = parse_xml('<root xmlns="urn:a"><child/></root>').select("root")[0]
    assert "".join(root.serialize_iter(Html(xml=True), inner=True)) == '<child xmlns="urn:a"/>'


def test_xml_nested_xlink_uses_inherited_binding() -> None:
    node = parse_xml('<root xmlns:xlink="urn:x"><child><nested xlink:href="v"/></child></root>').select("child")[0]
    assert node.serialize(Html(xml=True)) == '<child xmlns:xlink="urn:x"><nested xlink:href="v"/></child>'


def test_xml_mutation_without_xlink_binding_adds_one() -> None:
    node = parse_xml("<root><child/></root>").select("child")[0]
    node.attrs["xlink:href"] = "v"
    assert node.serialize(Html(xml=True)) == ('<child xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="v"/>')


def test_xml_subtree_foreign_namespace_takes_precedence() -> None:
    root = parse_xml('<root xmlns="urn:outer"/>').select("root")[0]
    svg = parse("<svg><g/></svg>").select("svg")[0]
    root.append(svg)
    assert svg.serialize(Html(xml=True)) == '<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>'


@pytest.mark.parametrize(
    ("xml", "unbound", "expected"),
    [
        pytest.param(
            '<r xmlns:xlink="urn:x"><a xlink:h="1"><b xlink:h="2"><c xlink:h="3"/></b></a></r>',
            [],
            '<r xmlns:xlink="urn:x"><a xlink:h="1"><b xlink:h="2"><c xlink:h="3"/></b></a></r>',
            id="nested-under-one-declaration",
        ),
        pytest.param(
            '<r xmlns:xlink="urn:x"><a><b xlink:h="1"/></a><c xlink:h="2"/><d/></r>',
            [],
            '<r xmlns:xlink="urn:x"><a><b xlink:h="1"/></a><c xlink:h="2"/><d/></r>',
            id="sibling-after-closed-subtree",
        ),
        pytest.param(
            '<r xmlns:xlink="urn:x" xlink:h="1"><a xlink:h="2"/></r>',
            [],
            '<r xmlns:xlink="urn:x" xlink:h="1"><a xlink:h="2"/></r>',
            id="declared-where-used",
        ),
        pytest.param(
            '<r><a xmlns:xlink="urn:x"><b xlink:h="1"/></a><c/></r>',
            ["c"],
            f'<r><a xmlns:xlink="urn:x"><b xlink:h="1"/></a><c {_XLINK} xlink:href="v"/></r>',
            id="closed-declaration-leaves-scope",
        ),
        pytest.param(
            '<r xmlns:xlink="urn:x"><a xmlns:xlink="urn:y"><b xlink:h="1"/></a><c xlink:h="2"/></r>',
            [],
            '<r xmlns:xlink="urn:x"><a xmlns:xlink="urn:y"><b xlink:h="1"/></a><c xlink:h="2"/></r>',
            id="closed-inner-declaration-keeps-outer",
        ),
        pytest.param(
            '<r><a/><d xmlns:xlink="urn:x"><e xlink:h="1"/></d><f/></r>',
            ["a", "f"],
            (
                f'<r><a {_XLINK} xlink:href="v"/><d xmlns:xlink="urn:x"><e xlink:h="1"/></d>'
                f'<f {_XLINK} xlink:href="v"/></r>'
            ),
            id="declaration-opens-scope-mid-walk",
        ),
        pytest.param(
            "<r><a/></r>",
            ["r", "a"],
            f'<r {_XLINK} xlink:href="v"><a {_XLINK} xlink:href="v"/></r>',
            id="unbound-root",
        ),
    ],
)
def test_xml_xlink_binding_follows_document_order(xml: str, unbound: list[str], expected: str) -> None:
    document = parse_xml(xml)
    for name in unbound:
        document.select(name)[0].attrs["xlink:href"] = "v"
    options = Html(xml=True)
    assert (document.serialize(options), "".join(document.serialize_iter(options))) == (expected, expected)


def test_xml_pretty_xlink_binding_follows_document_order() -> None:
    document = parse_xml('<r><a xmlns:xlink="urn:x"><b xlink:h="1"/></a><c/></r>')
    document.select("c")[0].attrs["xlink:href"] = "v"
    assert document.serialize(Html(xml=True, layout=Indent(2))) == (
        f'<r>\n  <a xmlns:xlink="urn:x">\n    <b xlink:h="1"/>\n  </a>\n  <c {_XLINK} xlink:href="v"/>\n</r>'
    )


def test_xml_xlink_binding_survives_unwritten_parent() -> None:
    document = parse_xml('<r xmlns:xlink="urn:x"><a xlink:h="1"/><f><g xlink:h="2"/></f></r>')
    document.select("f")[0].tag = "1x"
    assert document.serialize(Html(xml=True)) == '<r xmlns:xlink="urn:x"><a xlink:h="1"/><g xlink:h="2"/></r>'


def test_xml_inner_xlink_binding_follows_document_order() -> None:
    root = parse_xml('<r><a/><d xmlns:xlink="urn:x"><e xlink:h="1"/></d><f/></r>').select("r")[0]
    for name in ("a", "f"):
        root.select(name)[0].attrs["xlink:href"] = "v"
    assert root.inner_xml == (
        f'<a {_XLINK} xlink:href="v"/><d xmlns:xlink="urn:x"><e xlink:h="1"/></d><f {_XLINK} xlink:href="v"/>'
    )


def test_xml_xlink_binding_carries_across_chunks() -> None:
    filler = "t" * 9000  # longer than one serialize_iter chunk, so each start tag after it opens a later chunk
    document = parse_xml(
        f'<r><a xmlns:xlink="urn:x"><b xlink:h="1"/>{filler}<c xlink:h="2"/>{filler}</a><d/>{filler}<e/></r>'
    )
    document.select("e")[0].attrs["xlink:href"] = "v"
    chunks = list(document.serialize_iter(Html(xml=True)))
    expected = (
        f'<r><a xmlns:xlink="urn:x"><b xlink:h="1"/>{filler}<c xlink:h="2"/>{filler}</a><d/>{filler}'
        f'<e {_XLINK} xlink:href="v"/></r>'
    )
    assert (len(chunks), "".join(chunks)) == (4, expected)


@pytest.mark.parametrize(
    "opening",
    [
        pytest.param('<n xlink:role="">', id="nested"),
        pytest.param('<n><m xlink:role=""/>', id="under-plain-parents"),
    ],
)
def test_xml_inherited_xlink_serializes_in_linear_time(opening: str, work_steps: WorkSteps) -> None:
    def build(depth: int) -> Callable[[], object]:
        return partial(parse_xml(f"<r {_XLINK}>{opening * depth}<n/>{'</n>' * depth}</r>").serialize, Html(xml=True))

    work_steps.assert_linear(build, (500, 2000))
