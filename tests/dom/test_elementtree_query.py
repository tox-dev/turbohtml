from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import Document, etree, parse
from turbohtml.etree import Element, SubElement, XPath, document_fromstring, fromstring, tostring

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from turbohtml.etree import ElementView


@pytest.mark.parametrize(
    ("select", "expected"),
    [
        pytest.param(lambda tree: tree.iterchildren(), ["a", "b"], id="children"),
        pytest.param(lambda tree: tree.iterchildren(reversed=True), ["b", "a"], id="reversed-children"),
        pytest.param(lambda tree: tree.iterchildren("b"), ["b"], id="filtered-children"),
        pytest.param(lambda tree: tree[0].itersiblings(), ["b"], id="following"),
        pytest.param(lambda tree: tree[1].itersiblings(preceding=True), ["a"], id="preceding"),
        pytest.param(lambda tree: tree[0][0].iterancestors("a", "div"), ["a", "div"], id="ancestors"),
        pytest.param(lambda tree: tree.iterdescendants(), ["a", "i", "b"], id="descendants"),
        pytest.param(lambda tree: tree.iterdescendants("b"), ["b"], id="filtered-descendants"),
        pytest.param(lambda tree: tree.iterfind(".//i"), ["i"], id="xpath-iterator"),
        pytest.param(lambda tree: iter(tree[1]), [], id="empty-children"),
    ],
)
def test_element_tree_axes(
    tree: ElementView, select: Callable[[ElementView], Iterator[ElementView]], expected: list[str]
) -> None:
    assert [element.tag for element in select(tree)] == expected


def test_element_tree_get_children_preserves_order(tree: ElementView) -> None:
    assert [child.tag for child in tree.getchildren()] == ["a", "b"]


def test_element_tree_length_counts_elements(tree: ElementView) -> None:
    assert len(tree) == 2


def test_element_tree_index_counts_elements(tree: ElementView) -> None:
    assert tree.index(tree[1]) == 1


def test_element_tree_absolute_root_xpath_returns_document() -> None:
    tree: Final = document_fromstring("<p>text</p>")
    result: Final = cast("list[Document]", tree.xpath("/"))
    assert [document.root for document in result] == [tree.node]


def test_element_tree_absolute_child_xpath_selects_root() -> None:
    tree: Final = document_fromstring("<p>text</p>")
    assert tree.find("/html") is tree


def test_element_tree_xpath_selects_unicode_name() -> None:
    tree: Final = Element("root")
    child: Final = SubElement(tree, "é")
    assert tree.find("//é") is child


@pytest.mark.parametrize("quote", [pytest.param("'", id="single"), pytest.param('"', id="double")])
def test_element_tree_xpath_does_not_rewrite_quoted_slashes(tree: ElementView, quote: str) -> None:
    tree.set("data-path", "//p")
    assert tree.findall(f"//*[@data-path={quote}//p{quote}]") == [tree]


_TEST_ELEMENT_TREE_REPR_CONTAINS_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"<Element div at 0x[0-9a-fA-F]+>")


def test_element_tree_repr_contains_tag(tree: ElementView) -> None:
    assert _TEST_ELEMENT_TREE_REPR_CONTAINS_TAG_PATTERN.fullmatch(repr(tree))


def test_element_tree_keyword_factory_retains_attributes() -> None:
    tree: Final = Element(tag="p", attrib={"id": "paragraph"}, title="label")
    assert tostring(tree, encoding="unicode") == '<p id="paragraph" title="label"/>'


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        pytest.param((), ["one", "two", "three", "end"], id="all"),
        pytest.param(("p",), ["one", "two"], id="paragraph"),
        pytest.param(("b",), ["two", "three", "end"], id="bold"),
    ],
)
def test_element_tree_itertext_keeps_comment_tails(tags: tuple[str, ...], expected: list[str]) -> None:
    node: Final = parse("<p>one<!--comment-->two<b>three</b>end<i/></p>").find("p")
    assert node is not None
    assert list(etree.ElementView(node).itertext(*tags)) == expected


def test_element_tree_slices_support_reverse_order(tree: ElementView) -> None:
    assert [child.tag for child in tree[::-1]] == ["b", "a"]


@pytest.mark.parametrize("index", [pytest.param(2, id="past-end"), pytest.param(-3, id="before-start")])
def test_element_tree_index_out_of_bounds(tree: ElementView, index: int) -> None:
    with pytest.raises(IndexError):
        tree[index]


def test_element_tree_find_absent_tag(tree: ElementView) -> None:
    assert tree.find(".//missing") is None


def test_element_tree_findall_ignores_scalar_results(tree: ElementView) -> None:
    assert tree.findall("count(.//*)") == []


def test_element_tree_findall_ignores_attribute_results(tree: ElementView) -> None:
    assert tree.findall("@class") == []


def test_element_tree_xpath_boolean(tree: ElementView) -> None:
    assert tree.xpath("boolean(.//missing)") is False


def test_element_tree_compiled_xpath_exposes_expression() -> None:
    expression: Final = XPath(".//p")
    assert (str(expression), expression.path) == (".//p", ".//p")


def test_element_tree_xpath_cache_eviction_keeps_results(tree: ElementView) -> None:
    matches: Final = [tree.find(f".//a[@data-value='{index}']") for index in range(150)]
    assert [element.text for element in matches if element is not None] == ["first"]


@pytest.mark.parametrize(
    "tags",
    [
        pytest.param((None,), id="none"),
        pytest.param(([],), id="empty-list"),
        pytest.param((["missing", "*"],), id="wildcard-in-list"),
    ],
)
def test_element_tree_unrestricted_tag_filter(tree: ElementView, tags: tuple[str | Iterable[str] | None, ...]) -> None:
    assert [element.tag for element in tree.iter(*tags)] == ["div", "a", "i", "b"]


@pytest.mark.parametrize(
    "tag",
    [pytest.param(42, id="integer"), pytest.param([42], id="integer-in-list")],
)
def test_element_tree_rejects_invalid_tag_filter(tree: ElementView, tag: int | list[int]) -> None:
    with pytest.raises(TypeError):
        list(tree.iter(cast("str", tag)))


def test_element_tree_attributes_return_strings() -> None:
    tree: Final = fromstring('<input disabled class="one two">')
    assert (dict(tree.attrib), tree.items(), tree.get("missing", "default")) == (
        {"disabled": "", "class": "one two"},
        [("disabled", ""), ("class", "one two")],
        "default",
    )


def test_element_tree_attribute_mapping_popitem_keeps_class_flat() -> None:
    tree: Final = fromstring('<p class="one two">text</p>')
    assert (tree.attrib.get("class"), tree.attrib.popitem(), dict(tree.attrib)) == (
        "one two",
        ("class", "one two"),
        {},
    )


@pytest.fixture
def tree() -> ElementView:
    return fromstring('<div class="one two"><a data-value="149">first<i>nested</i></a><b>last</b></div>')
