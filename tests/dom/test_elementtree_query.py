from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import Document, etree, parse
from turbohtml.clean import Linkify, linkify_node
from turbohtml.etree import Element, SubElement, XPath, document_fromstring, fragment_fromstring, fromstring, tostring

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from traced_bytes import PeakBytes
    from work_steps import WorkSteps

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


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, "a", id="first"),
        pytest.param(2, "c", id="last"),
        pytest.param(-1, "c", id="from-end"),
        pytest.param(-3, "a", id="from-end-first"),
        pytest.param(True, "b", id="bool"),
    ],
)
def test_element_tree_subscript_skips_non_elements(mixed: ElementView, index: int, expected: str) -> None:
    assert mixed[index].tag == expected


def test_element_tree_slice_skips_non_elements(mixed: ElementView) -> None:
    assert [child.tag for child in mixed[1:]] == ["b", "c"]


def test_element_tree_subscript_too_large_for_an_index(tree: ElementView) -> None:
    with pytest.raises(IndexError, match="cannot fit"):
        tree[2**70]


def test_element_tree_subscript_rejects_non_index(tree: ElementView) -> None:
    with pytest.raises(TypeError, match="integers or slices"):
        tree[cast("int", "a")]


def test_element_tree_index_skips_non_elements(mixed: ElementView) -> None:
    assert [mixed.index(child) for child in mixed] == [0, 1, 2]


@pytest.mark.parametrize(
    "select",
    [
        pytest.param(lambda tree: tree[0][0], id="grandchild"),
        pytest.param(lambda tree: tree, id="itself"),
        pytest.param(lambda _tree: etree.Element("a"), id="other-tree"),
        pytest.param(lambda _tree: cast("ElementView", "a"), id="not-a-view"),
    ],
)
def test_element_tree_index_rejects_non_child(tree: ElementView, select: Callable[[ElementView], ElementView]) -> None:
    with pytest.raises(ValueError, match="not in sequence"):
        tree.index(select(tree))


@pytest.mark.parametrize(
    "read",
    [
        pytest.param("tree[0]", id="subscript"),
        pytest.param("tree[-1]", id="subscript-from-end"),
        pytest.param("tree.index(tree[-1])", id="index"),
        pytest.param("next(iter(tree))", id="iter"),
        pytest.param("next(tree.iterchildren(reversed=True))", id="iterchildren-reversed"),
    ],
)
def test_element_tree_reads_one_child_without_wrapping_its_siblings(read: str, peak_bytes: PeakBytes) -> None:
    peak: Final = peak_bytes(
        "import tracemalloc\n"
        "from turbohtml import etree\n"
        "tree = etree.fromstring('<ul>' + '<li>x</li>' * 5_000 + '</ul>')\n"
        "tracemalloc.start()\n"
        f"{read}\n"
        "print(tracemalloc.get_traced_memory()[1])\n",
        (),
    )
    assert peak < 32 * 1024, peak


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
def mixed() -> ElementView:
    return etree.fromstring("<div>start<a>A</a>one<!--note--><b>B</b>two<c>C</c>end</div>")


@pytest.fixture
def tree() -> ElementView:
    return fromstring('<div class="one two"><a data-value="149">first<i>nested</i></a><b>last</b></div>')


def test_element_tree_positions_follow_edits_between_reads() -> None:
    # a position is remembered to step on from; every edit of the child list has to drop what was remembered
    tree: Final = fragment_fromstring("<ul><li>0</li>t<li>1</li><li>2</li><li>3</li></ul>")
    assert [tree[2].text, tree[-2].text, tree.index(tree[3])] == ["2", "2", 3]
    tree.insert(0, etree.Element("li"))
    assert [tree[2].text, tree[-2].text, tree.index(tree[3])] == ["1", "2", 3]
    tree[1].drop_tree()
    assert [tree[2].text, tree[-2].text, tree.index(tree[3])] == ["2", "2", 3]
    tree.node.children[-1].decompose()
    assert [tree[2].text, tree[-1].text, tree.index(tree[1])] == ["2", "2", 1]
    with pytest.raises(IndexError):
        tree[3]


def test_element_tree_positions_of_two_lists_read_in_turn() -> None:
    markup: Final = f"<div><ul>{'<li>a</li>' * 5}</ul><ol>{'<li>b</li>' * 5}</ol></div>"
    both: Final = fragment_fromstring(markup)
    left, right = both[0], both[1]
    order: Final = (0, 3, 1, 4, 4, 2)
    pairs: Final = [(left[index].node, right[index].node) for index in order]
    assert pairs == [(left.node.children[index], right.node.children[index]) for index in order]


def test_element_tree_positions_read_in_reverse_order() -> None:
    tree: Final = _numbered_items(6)
    assert [tree[index].text for index in (5, 4, 2, 1, 0)] == ["5", "4", "2", "1", "0"]


def _numbered_items(count: int) -> ElementView:
    return fragment_fromstring(f"<ol>{''.join(f'<li>{number}</li>' for number in range(count))}</ol>")


def test_element_tree_position_restarts_when_far_or_reversed() -> None:
    tree: Final = _numbered_items(6)
    assert [tree[4].text, tree[0].text, tree[-2].text, tree[1].text] == ["4", "0", "4", "1"]


@pytest.mark.parametrize(
    ("reads", "expected"),
    [
        pytest.param((4, 3), ("4", "3"), id="nearer-than-the-last-read"),
        pytest.param((4, 0), ("4", "0"), id="farther-than-the-last-read"),
        pytest.param((4, -2), ("4", "4"), id="other-direction"),
    ],
)
def test_element_tree_position_steps_from_the_last_read_only_when_nearer(
    reads: tuple[int, ...], expected: tuple[str, ...]
) -> None:
    tree: Final = _numbered_items(6)
    assert tuple(tree[index].text for index in reads) == expected


@pytest.mark.parametrize("index", [pytest.param(0, id="first"), pytest.param(-1, id="last")])
def test_element_tree_subscript_of_a_childless_element_raises(index: int) -> None:
    with pytest.raises(IndexError):
        fromstring("<ul></ul>")[index]


def test_element_tree_index_after_a_read_from_the_end() -> None:
    tree: Final = _numbered_items(6)
    second: Final = tree[1]
    assert tree[-1].text == "5"
    assert tree.index(second) == 1


def test_element_tree_index_of_a_child_before_the_last_read_position() -> None:
    tree: Final = _numbered_items(6)
    before: Final = tree[3]
    assert tree[4].text == "4"
    assert tree.index(before) == 3


@pytest.mark.parametrize(
    ("index", "tag"),
    [pytest.param(0, "i", id="first"), pytest.param(1, "s", id="second")],
)
def test_element_tree_position_of_a_child_unwrapped_out_of_its_parent(index: int, tag: str) -> None:
    root: Final = etree.fromstring("<div><a href='http://c.org'><i>1</i><s>2</s></a><u>3</u></div>")
    anchor: Final = root[0]
    assert anchor[index].tag == tag
    linkify_node(root.node, Linkify(callbacks=[lambda _link: None], process_existing=True))
    with pytest.raises(IndexError):
        anchor[index]


def test_element_tree_index_after_an_edit_drops_the_read_position() -> None:
    tree: Final = _numbered_items(6)
    third: Final = tree[2]
    tree.insert(0, etree.Element("li"))
    assert tree.index(third) == 3


def test_element_tree_reads_every_position_in_linear_steps(work_steps: WorkSteps) -> None:
    def read_positions(count: int) -> Callable[[], object]:
        tree: Final = fragment_fromstring(f"<ol>{'<li>a</li>' * count}</ol>")
        return lambda: [tree.index(tree[index]) for index in range(count)]

    work_steps.assert_linear(read_positions, (1_500, 6_000))
