from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Html, Indent, parse, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps

    from turbohtml import Node

_XLINK: Final[str] = 'xmlns:xlink="http://www.w3.org/1999/xlink"'
# More unused declarations than a scope keeps inline, so the subtree is read the way a wide scope is.
_PADDING: Final[str] = " ".join(
    f'xmlns:{prefix}="urn:pad"' for prefix in ("a0", "a1", "a2", "a3", "z0", "z1", "z2", "z3", "z4")
)


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
        ('<root xmlns:p="urn:p"><child a="v" p:b="v"/></root>', '<child xmlns:p="urn:p" a="v" p:b="v"/>'),
        ('<root xmlns:pp="urn:unused"><p:child xmlns:p="urn:p"/></root>', '<p:child xmlns:p="urn:p"/>'),
        ('<root xmlns:p="urn:unused"><pp:child xmlns:pp="urn:pp"/></root>', '<pp:child xmlns:pp="urn:pp"/>'),
        ('<root xmlns:q="urn:unused"><p:child xmlns:p="urn:p"/></root>', '<p:child xmlns:p="urn:p"/>'),
        ('<root xmlns:p="urn:unused"><q:child xmlns:q="urn:q"/></root>', '<q:child xmlns:q="urn:q"/>'),
        (
            '<root xmlns:a="urn:a" xmlns:b="urn:b" xmlns:c="urn:c" xmlns:d="urn:d"><d:child a:x="v" c:y="v"/></root>',
            '<d:child xmlns:a="urn:a" xmlns:c="urn:c" xmlns:d="urn:d" a:x="v" c:y="v"/>',
        ),
        (
            '<top xmlns:p="urn:far" xmlns:q="urn:q"><root xmlns:p="urn:near"><q:child p:x="v"/></root></top>',
            '<q:child xmlns:p="urn:near" xmlns:q="urn:q" p:x="v"/>',
        ),
        (
            (
                '<top xmlns:a="urn:far" xmlns:b="urn:b" xmlns:c="urn:c" xmlns:d="urn:d" xmlns:e="urn:e" '
                'xmlns:f="urn:f" xmlns:g="urn:g" xmlns:h="urn:h">'
                '<root xmlns:a="urn:near"><b:child a:x="v"/></root></top>'
            ),
            '<b:child xmlns:a="urn:near" xmlns:b="urn:b" a:x="v"/>',
        ),
    ],
)
@pytest.mark.parametrize("padding", [pytest.param("", id="narrow"), pytest.param(_PADDING, id="wide")])
def test_xml_subtree_inherits_namespace_bindings(xml: str, expected: str, padding: str) -> None:
    node = _parse(xml, padding).select("*")[-1]
    assert node.serialize(Html(xml=True)) == expected


def _parse(xml: str, padding: str) -> Node:
    return parse_xml(f"<pad {padding}>{xml}</pad>")


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
        (
            '<root xmlns:p="urn:old"><child><wrap xmlns:p="urn:new"><p:a/></wrap><p:b/></child></root>',
            '<child xmlns:p="urn:old"><wrap xmlns:p="urn:new"><p:a/></wrap><p:b/></child>',
        ),
        (
            '<root xmlns="urn:a"><child><wrap xmlns=""><a><b/></a></wrap>text</child></root>',
            '<child xmlns="urn:a"><wrap xmlns=""><a><b/></a></wrap>text</child>',
        ),
        (
            '<root xmlns:p="urn:p" xmlns:u="urn:u"><child><p:a xmlns:u="urn:own">text</p:a><p:b/></child></root>',
            '<child xmlns:p="urn:p"><p:a xmlns:u="urn:own">text</p:a><p:b/></child>',
        ),
    ],
)
@pytest.mark.parametrize("padding", [pytest.param("", id="narrow"), pytest.param(_PADDING, id="wide")])
def test_xml_subtree_descendants_use_inherited_bindings(xml: str, expected: str, padding: str) -> None:
    node = _parse(xml, padding).select("*")[2]
    assert node.serialize(Html(xml=True)) == expected


def test_xml_inner_serialization_inherits_namespace_bindings() -> None:
    root = parse_xml('<root xmlns="urn:a"><child/><other/></root>').select("root")[0]
    assert root.inner_xml == '<child xmlns="urn:a"/><other xmlns="urn:a"/>'


def test_xml_inner_option_inherits_namespace_bindings() -> None:
    root = parse_xml('<root xmlns="urn:a"><child/><other/></root>').select("root")[0]
    assert root.serialize(Html(xml=True), inner=True) == '<child xmlns="urn:a"/><other xmlns="urn:a"/>'


@pytest.mark.parametrize("padding", [pytest.param("", id="narrow"), pytest.param(_PADDING, id="wide")])
def test_xml_inner_serialization_resolves_each_child_against_the_same_scope(padding: str) -> None:
    root = _parse(
        '<top xmlns="urn:far" xmlns:q="urn:q"><root xmlns="urn:a" xmlns:p="urn:p">'
        '<q:one p:x="v"/><two xmlns:p="urn:own"><p:three/></two><p:four/>text<q:five xmlns=""><six/></q:five>'
        "</root></top>",
        padding,
    ).select("root")[0]
    assert root.inner_xml == (
        '<q:one xmlns:p="urn:p" xmlns:q="urn:q" p:x="v"/><two xmlns="urn:a" xmlns:p="urn:own"><p:three/></two>'
        '<p:four xmlns:p="urn:p"/>text<q:five xmlns:q="urn:q" xmlns=""><six/></q:five>'
    )


@pytest.mark.parametrize("padding", [pytest.param("", id="narrow"), pytest.param(_PADDING, id="wide")])
def test_xml_subtree_name_with_an_empty_prefix_uses_no_inherited_binding(padding: str) -> None:
    document = _parse('<root xmlns="urn:a" xmlns:p="urn:p"><p:child><nested/></p:child></root>', padding)
    child = document.select("*")[2]
    document.select("*")[3].tag = ":nested"
    child.attrs[":attr"] = "v"
    assert child.serialize(Html(xml=True)) == '<p:child xmlns:p="urn:p"/>'


@pytest.mark.parametrize("padding", [pytest.param("", id="narrow"), pytest.param(_PADDING, id="wide")])
def test_xml_children_of_a_left_out_element_inherit_its_declarations(padding: str) -> None:
    document = _parse(
        '<root xmlns="urn:root" xmlns:p="urn:p" xmlns:q="urn:q">'
        '<run xmlns="urn:run" xmlns:p="urn:run-p" xmlns:r="urn:r"><p:a/><q:b/><c/></run></root>',
        padding,
    )
    run = document.select("*")[2]
    run.tag = "x:y:z"
    run.attrs["xmlns:s"] = ""
    copied = 'xmlns="urn:run" xmlns:p="urn:run-p" xmlns:r="urn:r"'
    expected = f'<p:a {copied}/><q:b {copied} xmlns:q="urn:q"/><c {copied}/>'
    assert document.select("*")[1].inner_xml == expected
    assert run.serialize(Html(xml=True)) == expected


@pytest.mark.parametrize("count", [pytest.param(3, id="narrow"), pytest.param(9, id="wide")])
def test_xml_subtree_stops_reading_once_every_inherited_binding_is_used(count: int) -> None:
    prefixes = [f"p{index}" for index in range(count)]
    declarations = " ".join(f'xmlns:{prefix}="urn:{prefix}"' for prefix in prefixes)
    attributes = " ".join(f'{prefix}:x="v"' for prefix in prefixes[:-1])
    child = parse_xml(
        f"<root {declarations}><child><one {attributes}/><{prefixes[-1]}:two><three/></{prefixes[-1]}:two><four/>"
        "</child></root>"
    ).select("child")[0]
    assert child.serialize(Html(xml=True)) == (
        f"<child {declarations}><one {attributes}/><{prefixes[-1]}:two><three/></{prefixes[-1]}:two><four/></child>"
    )


@pytest.mark.parametrize("padding", [pytest.param("", id="narrow"), pytest.param(_PADDING, id="wide")])
def test_xml_inherited_declaration_with_a_colon_in_its_prefix_is_not_copied(padding: str) -> None:
    document = _parse("<root><child><nested/></child></root>", padding)
    document.select("root")[0].attrs["xmlns:a:b"] = "urn:ab"
    document.select("nested")[0].attrs["a:b:c"] = "v"
    assert document.select("child")[0].serialize(Html(xml=True)) == "<child><nested/></child>"


def test_xml_inherited_bindings_serialize_in_linear_time(work_steps: WorkSteps) -> None:
    def build(depth: int) -> Callable[[], object]:
        ancestors = "".join(f'<a xmlns:ns{index}="urn:t{index}">' for index in range(depth))
        content = parse_xml(f"<r>{ancestors}<content>{'<leaf/>' * depth}</content>{'</a>' * depth}</r>")
        return partial(content.select("content")[0].serialize, Html(xml=True), inner=True)

    work_steps.assert_linear(build, (500, 2000))


def test_xml_pretty_subtree_inherits_namespace_bindings() -> None:
    node = parse_xml('<root xmlns="urn:a"><child><nested/></child></root>').select("child")[0]
    assert node.serialize(Html(xml=True, layout=Indent(2))) == '<child xmlns="urn:a">\n  <nested/>\n</child>'


def test_xml_chunked_subtree_inherits_namespace_bindings() -> None:
    node = parse_xml('<root xmlns="urn:a"><child><nested/></child></root>').select("child")[0]
    assert "".join(node.serialize_iter(Html(xml=True))) == '<child xmlns="urn:a"><nested/></child>'


def test_xml_chunked_inner_inherits_namespace_bindings() -> None:
    root = parse_xml('<root xmlns="urn:a"><child/></root>').select("root")[0]
    assert "".join(root.serialize_iter(Html(xml=True), inner=True)) == '<child xmlns="urn:a"/>'


def test_xml_chunk_that_gathers_a_wide_scope_runs_as_long_as_the_scope() -> None:
    count: Final[int] = 12_000
    declarations: Final[str] = " ".join(f'xmlns:ns{index}="urn:t{index}"' for index in range(count))
    root = parse_xml(f"<r {declarations}>{'<item/>' * count}</r>").select("r")[0]
    chunks = list(root.serialize_iter(Html(xml=True), inner=True))
    assert min(len(chunk) for chunk in chunks[:-1]) > count
    assert "".join(chunks) == "<item/>" * count


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


_INNER_XML_RENDERERS: Final[list[pytest.ParameterSet]] = [
    pytest.param(lambda node: node.inner_xml, id="inner_xml"),
    pytest.param(lambda node: node.serialize(Html(xml=True), inner=True), id="serialize"),
    pytest.param(lambda node: node.serialize(Html(xml=True, layout=Indent(2)), inner=True), id="serialize-indent"),
    pytest.param(lambda node: "".join(node.serialize_iter(Html(xml=True), inner=True)), id="serialize_iter"),
]


@pytest.mark.parametrize("render", _INNER_XML_RENDERERS)
@pytest.mark.parametrize(
    "xml",
    [
        pytest.param(f'<root xmlns:p="{"u" * 3000}">{"<p:b/>" * 64}</root>', id="long-name-many-children"),
        pytest.param('<root xmlns:p="{}">{}</root>'.format("u" * 3000, '<b p:a="v"/>' * 64), id="attribute-prefix"),
        pytest.param(f'<root xmlns="{"u" * 3000}">{"<b/>" * 64}</root>', id="default-namespace"),
        pytest.param(f'<top xmlns:p="{"u" * 3000}"><root>{"<p:b/>" * 64}</root></top>', id="declared-further-up"),
        pytest.param(f'<root xmlns:p="{"&amp;" * 3000}">{"<p:b/>" * 8}</root>', id="charged-as-written"),
    ],
)
def test_xml_inner_inherited_bindings_out_of_proportion_raise(render: Callable[[Node], str], xml: str) -> None:
    root = parse_xml(xml).select("root")[0]
    with pytest.raises(ValueError, match="exceed the allowance of 65536 characters plus 32 per character"):
        render(root)


@pytest.mark.parametrize("render", _INNER_XML_RENDERERS)
def test_xml_inner_inherited_bindings_in_proportion_are_kept(render: Callable[[Node], str]) -> None:
    name: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    root = parse_xml(f'<root xmlns:r="{name}">{"<r:id/>" * 4000}</root>').select("root")[0]
    assert render(root).replace("\n", "") == f'<r:id xmlns:r="{name}"/>' * 4000


@pytest.mark.parametrize("render", _INNER_XML_RENDERERS)
def test_xml_inner_long_inherited_binding_fits_a_short_fragment(render: Callable[[Node], str]) -> None:
    root = parse_xml(f'<root xmlns:p="{"u" * 30000}"><p:a/><p:b/></root>').select("root")[0]
    assert len(render(root)) > 60000


def test_xml_chunked_inner_stops_after_out_of_proportion_bindings() -> None:
    root = parse_xml(f'<root xmlns:p="{"u" * 3000}">{"<p:b/>" * 64}<tail/></root>').select("root")[0]
    chunks = root.serialize_iter(Html(xml=True), inner=True)
    with pytest.raises(ValueError, match="exceed the allowance"):
        list(chunks)
    assert list(chunks) == []


def test_xml_subtree_inherited_binding_has_no_allowance() -> None:
    node = parse_xml(f'<root xmlns:p="{"u" * 100000}"><p:child/></root>').select("*")[-1]
    assert node.serialize(Html(xml=True)) == f'<p:child xmlns:p="{"u" * 100000}"/>'
