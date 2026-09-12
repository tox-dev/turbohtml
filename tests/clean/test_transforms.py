from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import repeat
from typing import TYPE_CHECKING, Final, cast

import pytest
from typing_extensions import assert_type

from turbohtml import Comment, Document, Element, Node, Text, parse, parse_fragment, parse_xml
from turbohtml.clean import collapse_whitespace_node, sanitize_node, strip_comments_node, transform_node
from turbohtml.mutations import MutationObserver

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p>  alpha\t\n beta  gamma</p>", "<p> alpha beta gamma</p>", id="runs"),
        pytest.param("<p>a\fb\rc</p>", "<p>a b c</p>", id="form-feed-carriage-return"),
        pytest.param("<p>é\t中  😀</p>", "<p>é 中 😀</p>", id="unicode"),
        pytest.param("<p>a&nbsp;\u2003b</p>", "<p>a&nbsp;\u2003b</p>", id="unicode-spaces"),
        pytest.param("<p>a <!--x--> b</p>", "<p>a <!--x--> b</p>", id="comment-boundary"),
        pytest.param("<p> a <b> b </b> c </p>", "<p> a <b> b </b> c </p>", id="element-boundary"),
        pytest.param("<template>  a  </template>", "<template> a </template>", id="template"),
        pytest.param("<svg><text>  a  b</text></svg>", "<svg><text>  a  b</text></svg>", id="foreign"),
        pytest.param("", "", id="empty"),
    ],
)
def test_collapse_text(source: str, expected: str) -> None:
    root: Final = parse_fragment(source)
    assert assert_type(collapse_whitespace_node(root), Element).inner_html == expected


@pytest.mark.parametrize("tag", ["pre", "textarea", "listing", "title", "script", "style", "xmp", "plaintext"])
def test_collapse_preserves_context(tag: str) -> None:
    root: Final = Element(tag, children=[Text("  a\n b  ")])
    collapse_whitespace_node(root)
    assert root.text == "  a\n b  "


def test_collapse_preserves_ancestor() -> None:
    root: Final = Element("pre", children=[Element("span", children=[Text("  a  ")])])
    collapse_whitespace_node(root.children[0])
    assert root.text == "  a  "


def test_collapse_adjacent_text_identity() -> None:
    root: Final = Element("p", children=[Text("a "), Text(""), Text(" \tb"), Text("  ")])
    children: Final = tuple(root.children)
    collapse_whitespace_node(root)
    assert (tuple(root.children), tuple(child.text for child in children)) == (children, ("a ", "", "b", " "))


def test_collapse_empty_replacement() -> None:
    root: Final = Element("p", children=[Text(" "), Text("  ")])
    collapse_whitespace_node(root)
    assert tuple(child.text for child in root.children) == (" ", "")


def test_collapse_observer_and_idempotence() -> None:
    root: Final = Element("p", children=[Text(" a  b ")])
    observer: Final = MutationObserver()
    observer.observe(root, subtree=True, character_data=True, character_data_old_value=True)
    collapse_whitespace_node(root)
    assert [(record.old_value, record.target.text) for record in observer.take_records()] == [(" a  b ", " a b ")]
    collapse_whitespace_node(root)
    assert observer.take_records() == []


@pytest.mark.parametrize(
    "text",
    [pytest.param("a  b", id="ascii"), pytest.param("中  文", id="ucs2"), pytest.param("😀  🎉", id="ucs4")],
)
def test_collapse_source_observer(text: str) -> None:
    root: Final = parse_fragment(f"<p>{text}</p>")
    observer: Final = MutationObserver()
    observer.observe(root, subtree=True, character_data=True, character_data_old_value=True)
    collapse_whitespace_node(root)
    assert [(record.old_value, record.target.text) for record in observer.take_records()] == [
        (text, text.replace("  ", " "))
    ]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a>b", id="ascii"),
        pytest.param("a\u00a0b", id="nbsp"),
        pytest.param("中>文", id="ucs2"),
        pytest.param("😀>🎉", id="ucs4"),
    ],
)
def test_collapse_preserves_unchanged_source(text: str) -> None:
    root: Final = parse_fragment(f"<p>{text}</p>", source_locations=True)
    collapse_whitespace_node(root)
    assert root.to_source() == f"<div><p>{text}</p></div>"


def test_collapse_xml_rejected() -> None:
    root: Final = parse_xml("<p>  a  </p>")
    with pytest.raises(ValueError, match="HTML tree"):
        collapse_whitespace_node(root)
    assert root.text == "  a  "


@pytest.mark.parametrize("operation", [collapse_whitespace_node, strip_comments_node])
def test_clean_identity(operation: Callable[[Node], Node]) -> None:
    root: Final = parse_fragment("<p>  a  <!--x--></p>")
    assert operation(root) is root


@pytest.mark.parametrize("operation", [collapse_whitespace_node, strip_comments_node, transform_node])
def test_transform_invalid_root(operation: Callable[..., object]) -> None:
    with pytest.raises(TypeError):
        operation("<p>x</p>")


def test_strip_comments_retains_references() -> None:
    root: Final = parse_fragment("<!--a--><p>x<!--b--><b>y</b><!--c--></p><!--d-->")
    comments: Final = [node for node in root.descendants if isinstance(node, Comment)]
    strip_comments_node(root)
    assert (root.inner_html, [(node.parent, node.data) for node in comments]) == (
        "<p>x<b>y</b></p>",
        [(None, "a"), (None, "b"), (None, "c"), (None, "d")],
    )


def test_strip_comments_observed() -> None:
    root: Final = parse_fragment("a<!--b-->c")
    observer: Final = MutationObserver()
    observer.observe(root, child_list=True)
    strip_comments_node(root)
    assert [tuple(node.html for node in record.removed_nodes) for record in observer.take_records()] == [("<!--b-->",)]


def test_strip_comments_keeps_context() -> None:
    root: Final = Comment("keep")
    assert assert_type(strip_comments_node(root), Comment) is root


def test_transform_empty() -> None:
    root: Final = Text("a")
    assert assert_type(transform_node(root), Text) is root


def test_transform_native_mutator() -> None:
    root: Final = Element("p", children=[Text("a"), Text("b")])
    assert assert_type(transform_node(root, Element.normalize), Element).inner_html == "ab"
    assert len(root.children) == 1


def test_transform_copy_before_mutating() -> None:
    root: Final = parse_fragment("<b>  a  </b><!--x-->")
    result: Final = assert_type(transform_node(root, sanitize_node, collapse_whitespace_node), Element)
    assert (root.inner_html, result.inner_xml) == ("<b>  a  </b><!--x-->", "<b> a </b>")


def test_transform_order() -> None:
    root: Final = Element("p", children=[Text("a "), Comment("x"), Text(" b")])
    assert transform_node(root, strip_comments_node, collapse_whitespace_node).inner_html == "a b"


def test_transform_replaces_node_type() -> None:
    def replace(node: Node) -> Document:
        return parse(node.serialize())

    result: Final = assert_type(transform_node(Element("p"), replace, strip_comments_node), Element | Document)
    assert isinstance(result, Document)
    assert result.serialize() == "<html><head></head><body><p></p></body></html>"


def test_transform_partial() -> None:
    root: Final = Text("  a  ")
    assert transform_node(root, partial(collapse_whitespace_node)).text == " a "


def test_transform_callable_object() -> None:
    root: Final = Text("  a  ")
    assert transform_node(root, partial(collapse_whitespace_node).__call__).text == " a "


@pytest.mark.parametrize("result", ["text", 1, (), False])
def test_transform_invalid_result(result: object) -> None:
    def stage(_node: Node) -> object:
        return result

    operation: Final = cast("Callable[..., object]", transform_node)
    with pytest.raises(TypeError, match="stage 1 returned"):
        operation(Text("a"), stage)


def test_transform_failure_keeps_mutations() -> None:
    failure: Final = ValueError("stage failed")

    def fail(_node: Node) -> Node:
        raise failure

    root: Final = parse_fragment("a<!--x-->")
    with pytest.raises(ValueError, match="stage failed") as caught:
        transform_node(root, strip_comments_node, fail)
    assert (root.inner_html, caught.value) == ("a", failure)


def test_transform_noncallable() -> None:
    operation: Final = cast("Callable[..., object]", transform_node)
    with pytest.raises(TypeError, match="not callable"):
        operation(Text("a"), 1)


def test_transform_requires_root() -> None:
    operation: Final = cast("Callable[..., object]", transform_node)
    with pytest.raises(TypeError, match="requires a node"):
        operation()


def test_collapse_foreign_ancestor() -> None:
    root: Final = parse_fragment("<svg><foreignObject><p>  a  </p></foreignObject></svg>")
    paragraph: Final = root.find_all("p")[0]
    collapse_whitespace_node(paragraph)
    assert paragraph.text == "  a  "


def test_clean_shadow_boundary() -> None:
    root: Final = Element("div", children=[Text("  a  "), Comment("remove")])
    shadow: Final = root.attach_shadow()
    shadow.set_inner_html("  b  <!--keep-->")
    transform_node(root, strip_comments_node, collapse_whitespace_node)
    assert (root.inner_html, shadow.inner_html) == (" a ", "  b  <!--keep-->")


@pytest.mark.parametrize(
    ("parse_tree", "source", "expected"),
    [
        (parse_xml, "<root><!--x--><child><!--y--></child></root>", "<root><child></child></root>"),
        (parse_fragment, "<template><!--x--><b>y</b></template>", "<template><b>y</b></template>"),
    ],
)
def test_strip_comments_context(parse_tree: Callable[[str], Node], source: str, expected: str) -> None:
    root: Final = parse_tree(source)
    strip_comments_node(root)
    assert root.inner_html == expected


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (collapse_whitespace_node, "<p>alpha beta<!--x--></p>"),
        (strip_comments_node, "<p>alpha   beta</p>"),
    ],
)
def test_clean_concurrent_tree(operation: Callable[[Node], Node], expected: str) -> None:
    root: Final = parse_fragment("<p>alpha   beta<!--x--></p>")
    with ThreadPoolExecutor(max_workers=4) as executor:
        results: Final = tuple(executor.map(operation, repeat(root, 32)))
    assert (all(result is root for result in results), root.inner_html) == (True, expected)
