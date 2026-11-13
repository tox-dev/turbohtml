from __future__ import annotations

import gc
from typing import TYPE_CHECKING, Final, cast

import pytest

import turbohtml
from turbohtml import (
    CData,
    Comment,
    Document,
    Element,
    NodeFilter,
    NodeIterator,
    ProcessingInstruction,
    Text,
    TreeWalker,
    parse,
)
from turbohtml.clean import linkify_node

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node

_SHOW_ELEMENT: Final[int] = NodeFilter.SHOW_ELEMENT
_SHOW_TEXT: Final[int] = NodeFilter.SHOW_TEXT
_ACCEPT: Final[int] = NodeFilter.FILTER_ACCEPT
_REJECT: Final[int] = NodeFilter.FILTER_REJECT
_SKIP: Final[int] = NodeFilter.FILTER_SKIP


@pytest.fixture
def doc() -> Document:
    return parse(
        "<!DOCTYPE html><html><head><title>T</title></head>"
        "<body><div id=r><p>a<b>x</b></p><span>y</span><!--c--></div></body></html>"
    )


@pytest.fixture
def root(doc: Document) -> Element:
    return _el(doc.select_one("#r"))


def _el(node: Node | None) -> Element:
    # Narrow a select_one/find result to the Element it is known to be at the call site.
    assert isinstance(node, Element)
    return node


def _named(node: Node, tag: str) -> bool:
    return isinstance(node, Element) and node.tag == tag


def _verdict_on(tag: str, hit: int, miss: int = _ACCEPT) -> Callable[[Node], int]:
    # A filter returning ``hit`` for elements named ``tag`` and ``miss`` otherwise.
    return lambda node: hit if _named(node, tag) else miss


def test_node_filter_constants() -> None:
    assert NodeFilter.SHOW_ALL == 0xFFFFFFFF
    assert (_SHOW_ELEMENT, _SHOW_TEXT, NodeFilter.SHOW_COMMENT) == (0x1, 0x4, 0x80)
    assert (_ACCEPT, _REJECT, _SKIP) == (1, 2, 3)


def test_defaults(root: Element) -> None:
    walker = TreeWalker(root)
    assert walker.root.equals(root)
    assert walker.what_to_show == NodeFilter.SHOW_ALL
    assert walker.filter is None
    assert walker.current_node.equals(root)


def test_iterator_defaults(root: Element) -> None:
    it = NodeIterator(root)
    assert it.root.equals(root)
    assert it.what_to_show == NodeFilter.SHOW_ALL
    assert it.filter is None
    assert it.reference_node.equals(root)
    assert it.pointer_before_reference_node is True


@pytest.mark.parametrize("kind", [pytest.param(TreeWalker, id="walker"), pytest.param(NodeIterator, id="iterator")])
def test_root_must_be_a_node(kind: type) -> None:
    with pytest.raises(TypeError, match="root must be a Node"):
        kind("not a node")


@pytest.mark.parametrize("kind", [pytest.param(TreeWalker, id="walker"), pytest.param(NodeIterator, id="iterator")])
def test_filter_must_be_callable(kind: type, root: Element) -> None:
    with pytest.raises(TypeError, match="filter must be callable or None"):
        kind(root, NodeFilter.SHOW_ALL, 3)


@pytest.mark.parametrize("kind", [pytest.param(TreeWalker, id="walker"), pytest.param(NodeIterator, id="iterator")])
def test_what_to_show_must_be_int(kind: type, root: Element) -> None:
    with pytest.raises(TypeError):
        kind(root, [])


def test_walker_elements_only(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    node = walker.first_child()
    tags = []
    while node is not None:
        tags.append(_tags(node))
        node = walker.next_node()
    assert tags == ["p", "b", "span"]


def _tags(node: Node | None) -> str:
    return node.tag if isinstance(node, Element) else type(node).__name__


def test_walker_reject_prunes_subtree() -> None:
    tree = _el(parse("<div id=r><p>a<b>x</b></p><span>y</span><i>z</i></div>").select_one("#r"))
    walker = TreeWalker(tree, _SHOW_ELEMENT, _verdict_on("p", _REJECT))
    tags = [_tags(walker.first_child())]
    while (node := walker.next_node()) is not None:
        tags.append(_tags(node))
    assert tags == ["span", "i"]  # p and its <b> child are gone; the later siblings survive


def test_walker_skip_keeps_subtree(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("p", _SKIP))
    tags = [_tags(walker.first_child())]
    while (node := walker.next_node()) is not None:
        tags.append(_tags(node))
    assert tags == ["b", "span"]  # p itself skipped, its <b> child kept


def test_first_child_none_on_childless_node() -> None:
    tree = _el(parse("<div id=r><hr></div>").select_one("#r"))
    walker = TreeWalker(tree)
    walker.current_node = _el(tree.select_one("hr"))  # <hr> has no children at all
    assert walker.first_child() is None


def test_first_child_all_filtered() -> None:
    tree = _el(parse("<div id=r><span></span></div>").select_one("#r"))
    assert TreeWalker(tree, _SHOW_TEXT).first_child() is None  # the subtree holds no text at all


def test_first_child_descends_into_skipped(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("p", _SKIP))
    assert _tags(walker.first_child()) == "b"


def test_first_child_reject_moves_to_sibling(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("p", _REJECT))
    assert _tags(walker.first_child()) == "span"


def test_first_child_climbs_back_to_origin(root: Element) -> None:
    # descend into <p> (skip), exhaust its <b> subtree (skip), climb back to the origin -> nothing accepted
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("span", _ACCEPT, _SKIP))
    walker.current_node = _el(root.select_one("p"))  # a <p>-only origin, so span is not a sibling to fall onto
    assert walker.first_child() is None


def test_last_child(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    assert _tags(walker.last_child()) == "span"


def test_last_child_descends_into_skipped(root: Element) -> None:
    walker = TreeWalker(_el(root.select_one("p")), _SHOW_ELEMENT, _verdict_on("b", _SKIP))
    # <p>'s only element child is <b>; skipping it descends but finds nothing
    assert walker.last_child() is None


@pytest.mark.parametrize(
    ("walk", "detach", "verdict"),
    [
        pytest.param(TreeWalker.first_child, Element.extract, _REJECT, id="first-child-reject-extract"),
        pytest.param(TreeWalker.first_child, Element.extract, _SKIP, id="first-child-skip-extract"),
        pytest.param(
            TreeWalker.first_child,
            lambda element: _el(element.parent).set_text("z"),
            _REJECT,
            id="first-child-reject-parent-set-text",
        ),
        pytest.param(TreeWalker.last_child, Element.extract, _REJECT, id="last-child-reject-extract"),
    ],
)
def test_traverse_children_survives_filter_detaching_node(
    walk: Callable[[TreeWalker], Node | None], detach: Callable[[Element], Element | None], verdict: int
) -> None:
    # The DOM lets a NodeFilter mutate the tree; a filter that detaches the walked node must not crash the climb.
    def node_filter(node: Node) -> int:
        detach(_el(node))
        return verdict

    tree: Final = Element(
        "root", children=[Element("a", children=[Element("aa")]), Element("b", children=[Element("bb")])]
    )
    assert walk(TreeWalker(tree, _SHOW_ELEMENT, node_filter)) is None


def test_parent_node_none_at_root(root: Element) -> None:
    assert TreeWalker(root).parent_node() is None


def test_parent_node_climbs(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    walker.first_child()  # -> p
    walker.first_child()  # -> b
    assert _tags(walker.parent_node()) == "p"


def test_parent_node_skips_then_accepts(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("p", _SKIP))
    walker.current_node = _el(root.select_one("b"))
    assert _tags(walker.parent_node()) == "div"  # p skipped, div accepted


def test_parent_node_stops_at_document_top(doc: Document, root: Element) -> None:
    walker = TreeWalker(root)  # rooted at #r
    walker.current_node = doc  # above the root, parent chain runs out before reaching it
    assert walker.parent_node() is None


def test_next_sibling(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    walker.first_child()  # -> p
    assert _tags(walker.next_sibling()) == "span"


def test_next_sibling_none_at_root(root: Element) -> None:
    assert TreeWalker(root).next_sibling() is None


def test_next_sibling_skip_with_children() -> None:
    tree = _el(parse("<div id=r><a></a><section><b>x</b></section></div>").select_one("#r"))
    walker = TreeWalker(tree, _SHOW_ELEMENT, _verdict_on("section", _SKIP))
    walker.current_node = _el(tree.select_one("a"))
    # a's next sibling <section> is skipped, so the search descends into it and accepts <b>
    assert _tags(walker.next_sibling()) == "b"


def test_next_sibling_all_skip_climbs_to_root(doc: Document) -> None:
    walker = TreeWalker(_el(doc.select_one("body")), _SHOW_ELEMENT, lambda _node: _SKIP)
    walker.current_node = _el(doc.select_one("b"))  # every node skips, so the search climbs to the root and yields None
    assert walker.next_sibling() is None


def test_next_sibling_climbs_and_returns_none(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    walker.current_node = _el(root.select_one("b"))  # deepest; no sibling, climb to root -> None
    assert walker.next_sibling() is None


def test_next_sibling_parent_runs_out(doc: Document, root: Element) -> None:
    walker = TreeWalker(root)
    walker.current_node = doc  # document has no sibling and no parent
    assert walker.next_sibling() is None


def test_previous_sibling(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    walker.last_child()  # -> span
    assert _tags(walker.previous_sibling()) == "p"


def test_previous_sibling_skip_with_children(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("p", _SKIP))
    walker.current_node = _el(root.select_one("span"))
    # previous sibling p is skipped; descend to its last child b
    assert _tags(walker.previous_sibling()) == "b"


def test_next_node_none_walks_to_end(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    seen = []
    while (node := walker.next_node()) is not None:
        seen.append(_tags(node))
    assert seen == ["p", "b", "span"]


def test_next_node_reject_skips_descent(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("p", _REJECT))
    # rejecting p means next_node does not descend into b
    assert _tags(walker.next_node()) == "span"


def test_next_node_climbs_out_of_current_above_root() -> None:
    tree = parse("<html><head><title>t</title></head><body></body></html>")
    walker = TreeWalker(_el(tree.select_one("head")), _SHOW_ELEMENT)  # rooted at head
    walker.current_node = _el(tree.select_one("body"))  # a childless sibling; the climb runs off the top of the tree
    assert walker.next_node() is None


def test_previous_node_none_at_root(root: Element) -> None:
    assert TreeWalker(root).previous_node() is None


def test_previous_node_walks_back(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    while walker.next_node() is not None:
        pass  # advance to the last accepted node (span)
    seen = []
    while (node := walker.previous_node()) is not None:
        seen.append(_tags(node))
    assert seen == ["b", "p", "div"]  # previous_node lands on the accepted root last


def test_previous_node_descends_to_last_descendant(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    walker.current_node = _el(root.select_one("span"))
    # previous of span descends into p to its deepest last accepted descendant b
    assert _tags(walker.previous_node()) == "b"


def test_previous_node_climbs_to_parent(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    walker.current_node = _el(root.select_one("b"))
    # b has no previous sibling, so previous_node returns the parent p
    assert _tags(walker.previous_node()) == "p"


def test_previous_node_reject_stops_descent(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _verdict_on("p", _REJECT))
    walker.current_node = _el(root.select_one("span"))
    # p is rejected: previous_node does not descend into it, and p is not accepted -> climbs to div
    assert _tags(walker.previous_node()) == "div"


def test_previous_node_reaches_root_sibling() -> None:
    tree = parse("<div><a id=start></a><b id=cur>x</b></div>")
    walker = TreeWalker(_el(tree.select_one("#start")), _SHOW_ELEMENT, _verdict_on("a", _SKIP))
    walker.current_node = _el(
        tree.select_one("#cur")
    )  # start (the root) is current's previous sibling; skip lands there
    assert walker.previous_node() is None


def test_previous_node_parent_runs_out(doc: Document, root: Element) -> None:
    walker = TreeWalker(root)
    walker.current_node = doc  # document is above root with no parent
    assert walker.previous_node() is None


def test_previous_node_descends_to_accepted_child() -> None:
    tree = _el(parse("<div id=r><p>a<b></b></p><span>y</span></div>").select_one("#r"))
    walker = TreeWalker(tree, _SHOW_ELEMENT)
    walker.current_node = _el(tree.select_one("span"))
    # p's last child <b> is empty, so the backward descent stops on it as the accepted node
    assert _tags(walker.previous_node()) == "b"


def test_previous_node_skips_parent_then_continues() -> None:
    tree = _el(parse("<div id=r><a></a><wrap><b></b></wrap></div>").select_one("#r"))
    walker = TreeWalker(tree, _SHOW_ELEMENT, _verdict_on("wrap", _SKIP))
    walker.current_node = _el(tree.select_one("b"))
    # b has no previous sibling; its parent <wrap> is skipped, so previous_node keeps going to <a>
    assert _tags(walker.previous_node()) == "a"


@pytest.mark.parametrize(
    ("start", "move", "expected"),
    [
        pytest.param("a", "next_sibling", "c", id="next"),
        pytest.param("c", "previous_sibling", "a", id="previous"),
    ],
)
def test_sibling_reject_skips_to_next(start: str, move: str, expected: str) -> None:
    tree = _el(parse("<div id=r><a>1</a><section><b>2</b></section><c>3</c></div>").select_one("#r"))
    walker = TreeWalker(tree, _SHOW_ELEMENT, _verdict_on("section", _REJECT))
    walker.current_node = _el(tree.select_one(start))
    # section is rejected, so the search does not descend into <b> and lands on <c>
    assert _tags(getattr(walker, move)()) == expected


@pytest.mark.parametrize(
    ("start", "move", "expected"),
    [
        pytest.param("a", "next_sibling", "c", id="next"),
        pytest.param("c", "previous_sibling", "a", id="previous"),
    ],
)
def test_sibling_skip_leaf_moves_past(start: str, move: str, expected: str) -> None:
    tree = _el(parse("<div id=r><a>1</a><br><c>3</c></div>").select_one("#r"))
    walker = TreeWalker(tree, _SHOW_ELEMENT, _verdict_on("br", _SKIP))
    walker.current_node = _el(tree.select_one(start))
    assert _tags(getattr(walker, move)()) == expected  # <br> is a skipped leaf, stepped over


def test_iterator_forward(root: Element) -> None:
    it = NodeIterator(root, _SHOW_ELEMENT)
    assert [_tags(node) for node in iter(it.next_node, None)] == ["div", "p", "b", "span"]


def test_iterator_reject_is_skip(root: Element) -> None:
    # a flat view has no subtree: REJECT and SKIP behave identically, so <b> under a rejected <p> still appears
    reject = NodeIterator(root, _SHOW_ELEMENT, _verdict_on("p", _REJECT))
    skip = NodeIterator(root, _SHOW_ELEMENT, _verdict_on("p", _SKIP))
    forward_reject = [_tags(node) for node in iter(reject.next_node, None)]
    forward_skip = [_tags(node) for node in iter(skip.next_node, None)]
    assert forward_reject == forward_skip == ["div", "b", "span"]


def test_iterator_iter_protocol(root: Element) -> None:
    it = NodeIterator(root, _SHOW_ELEMENT)
    assert iter(it) is it
    assert [_tags(node) for node in it] == ["div", "p", "b", "span"]


def test_iterator_previous_none_at_start(root: Element) -> None:
    assert NodeIterator(root).previous_node() is None


def test_iterator_back_and_forth(root: Element) -> None:
    it = NodeIterator(root, _SHOW_ELEMENT)
    assert _tags(it.next_node()) == "div"
    assert _tags(it.next_node()) == "p"
    assert _tags(it.next_node()) == "b"
    assert it.pointer_before_reference_node is False
    assert _tags(it.previous_node()) == "b"  # flips the pointer, re-yields b
    assert _tags(it.previous_node()) == "p"
    assert _tags(it.reference_node) == "p"


def test_iterator_forward_exhausts_to_none(root: Element) -> None:
    it = NodeIterator(root, _SHOW_ELEMENT)
    for _ in range(4):
        it.next_node()
    assert it.next_node() is None


def test_iterator_previous_exhausts_to_none(root: Element) -> None:
    it = NodeIterator(root, _SHOW_ELEMENT)
    it.next_node()  # div (the root), pointer now after it
    assert _tags(it.previous_node()) == "div"  # flipping the pointer re-yields the root
    assert it.previous_node() is None  # nothing precedes the root


@pytest.mark.parametrize(
    ("what", "expected"),
    [
        pytest.param(_SHOW_ELEMENT, ["div", "p", "b", "span"], id="element"),
        pytest.param(_SHOW_TEXT, ["Text", "Text", "Text"], id="text"),
        pytest.param(NodeFilter.SHOW_COMMENT, ["Comment"], id="comment"),
    ],
)
def test_what_to_show_bits(root: Element, what: int, expected: list[str]) -> None:
    assert [_tags(node) for node in NodeIterator(root, what)] == expected


def test_show_document_and_doctype(doc: Document) -> None:
    which = NodeFilter.SHOW_DOCUMENT | NodeFilter.SHOW_DOCUMENT_TYPE
    assert [type(node).__name__ for node in NodeIterator(doc, which)] == ["Document", "Doctype"]


def test_show_cdata() -> None:
    node = CData("payload")
    assert [type(node).__name__ for node in NodeIterator(node, NodeFilter.SHOW_CDATA_SECTION)] == ["CData"]


def test_show_processing_instruction() -> None:
    node = ProcessingInstruction("t", "d")
    which = NodeFilter.SHOW_PROCESSING_INSTRUCTION
    assert [type(node).__name__ for node in NodeIterator(node, which)] == ["ProcessingInstruction"]


def test_show_document_fragment() -> None:
    tpl = _el(parse("<template><p>x</p></template>").select_one("template"))
    assert len(list(NodeIterator(tpl, NodeFilter.SHOW_DOCUMENT_FRAGMENT))) == 1  # the <template> content node


def test_filter_receives_node(root: Element) -> None:
    seen: list[str] = []

    def record(node: Node) -> int:
        seen.append(_tags(node))
        return _ACCEPT if isinstance(node, Element) else _SKIP

    list(NodeIterator(root, NodeFilter.SHOW_ALL, record))
    assert "div" in seen
    assert "Text" in seen


def test_filter_raising_first_child(root: Element) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        TreeWalker(root, _SHOW_ELEMENT, _raise_on("p")).first_child()


def _raise_on(tag: str) -> Callable[[Node], int]:
    def node_filter(node: Node) -> int:
        if _named(node, tag):
            msg = "boom"
            raise RuntimeError(msg)
        return _ACCEPT

    return node_filter


def test_filter_raising_parent_node(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _raise_on("p"))
    walker.current_node = _el(root.select_one("b"))
    with pytest.raises(RuntimeError, match="boom"):
        walker.parent_node()


def test_filter_raising_sibling(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _raise_on("span"))
    walker.current_node = _el(root.select_one("p"))
    with pytest.raises(RuntimeError, match="boom"):
        walker.next_sibling()


def test_filter_raising_sibling_parent_climb(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _raise_on("p"))
    walker.current_node = _el(root.select_one("b"))  # no next sibling, so the search climbs to p and raises
    with pytest.raises(RuntimeError, match="boom"):
        walker.next_sibling()


def test_filter_raising_next_node_after_climb() -> None:
    tree = _el(parse("<div id=r><hr><p>x</p></div>").select_one("#r"))
    walker = TreeWalker(tree, _SHOW_ELEMENT, _raise_on("p"))
    walker.current_node = _el(tree.select_one("hr"))  # childless: next_node climbs to the <p> sibling and raises
    with pytest.raises(RuntimeError, match="boom"):
        walker.next_node()


def test_filter_raising_previous_sibling_node(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _raise_on("p"))
    walker.current_node = _el(root.select_one("span"))  # previous_node visits the previous sibling p and raises
    with pytest.raises(RuntimeError, match="boom"):
        walker.previous_node()


def test_filter_raising_previous_node_descent(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, _raise_on("b"))
    walker.current_node = _el(root.select_one("span"))  # descends into p, raises on its child b
    with pytest.raises(RuntimeError, match="boom"):
        walker.previous_node()


def test_filter_raising_previous_node_parent(root: Element) -> None:
    para = _el(root.select_one("p"))
    walker = TreeWalker(root, _SHOW_ELEMENT, _raise_on("p"))
    walker.current_node = para.children[0]  # the leading text node: previous_node climbs to p and raises
    with pytest.raises(RuntimeError, match="boom"):
        walker.previous_node()


def test_filter_raising_iterator_next(root: Element) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        NodeIterator(root, _SHOW_ELEMENT, _raise_on("div")).next_node()


def test_filter_raising_iterator_previous(root: Element) -> None:
    armed = [False]

    def node_filter(node: Node) -> int:
        if armed[0] and _named(node, "b"):
            msg = "boom"
            raise RuntimeError(msg)
        return _ACCEPT

    it = NodeIterator(root, _SHOW_ELEMENT, node_filter)
    for _ in range(3):
        it.next_node()  # div, p, b -- placed after b
    armed[0] = True
    with pytest.raises(RuntimeError, match="boom"):
        it.previous_node()  # re-filters b going backward and raises


def test_filter_raising_iterator_iteration(root: Element) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        list(NodeIterator(root, _SHOW_ELEMENT, _raise_on("p")))


def test_filter_non_int_verdict(root: Element) -> None:
    with pytest.raises(TypeError):
        TreeWalker(root, _SHOW_ELEMENT, lambda _node: "yes").first_child()  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_filter_overflowing_verdict(root: Element) -> None:
    with pytest.raises(OverflowError):
        TreeWalker(root, _SHOW_ELEMENT, lambda _node: 10**100).first_child()


def test_filter_negative_verdict_reads_as_skip(root: Element) -> None:
    # an unrecognized (here negative) verdict is a plain non-accept, not an error
    assert list(NodeIterator(root, _SHOW_ELEMENT, lambda _node: -1)) == []


def test_filter_reentrancy_rejected(root: Element) -> None:
    walker: TreeWalker = None  # ty: ignore[invalid-assignment]  # the closure reads the walker before it is assigned, so the name is None first

    def reenter(_node: Node) -> int:
        return int(walker.next_node() is None)  # re-enters the walker; the active-filter guard raises here

    walker = TreeWalker(root, _SHOW_ELEMENT, reenter)
    with pytest.raises(ValueError, match="already running"):
        walker.first_child()


def test_iterator_filter_reentrancy_rejected(root: Element) -> None:
    it: NodeIterator = None  # ty: ignore[invalid-assignment]  # the closure reads the iterator before it is assigned, so the name is None first

    def reenter(_node: Node) -> int:
        return int(it.next_node() is None)  # re-enters the iterator; the active-filter guard raises here

    it = NodeIterator(root, _SHOW_ELEMENT, reenter)
    with pytest.raises(ValueError, match="already running"):
        it.next_node()


def test_iterator_reentry_error_caught_inside_filter_keeps_the_outer_step(root: Element) -> None:
    errors: Final[list[str]] = []
    it: NodeIterator = None  # ty: ignore[invalid-assignment]  # the closure reads the iterator before it is assigned, so the name is None first

    def reenter(_node: Node) -> int:
        try:
            it.next_node()
        except ValueError as error:
            errors.append(str(error))
        return _ACCEPT

    it = NodeIterator(root, _SHOW_ELEMENT, reenter)
    assert ([_tags(it.next_node()), _tags(it.next_node())], len(errors)) == (["div", "p"], 2)


@pytest.mark.parametrize("kind", [pytest.param(TreeWalker, id="walker"), pytest.param(NodeIterator, id="iterator")])
def test_filter_verdict_conversion_cannot_reenter(root: Element, kind: type[TreeWalker | NodeIterator]) -> None:
    traverser: TreeWalker | NodeIterator = None  # ty: ignore[invalid-assignment]  # the verdict closure reads the traverser before it is assigned

    class Verdict:
        def __index__(self) -> int:
            return int(traverser.next_node() is None)

    traverser = kind(root, _SHOW_ELEMENT, cast("Callable[[Node], int]", lambda _node: Verdict()))
    with pytest.raises(ValueError, match="already running"):
        traverser.next_node()


def test_current_node_assignable(root: Element) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT)
    walker.current_node = _el(root.select_one("span"))
    assert _tags(walker.current_node) == "span"


def test_current_node_cannot_delete(root: Element) -> None:
    walker = TreeWalker(root)
    with pytest.raises(TypeError, match="cannot delete"):
        del walker.current_node  # ty: ignore[invalid-assignment]  # deletes the read-only current_node on purpose to check the TypeError


def test_current_node_must_be_node(root: Element) -> None:
    walker = TreeWalker(root)
    with pytest.raises(TypeError, match="current_node must be a Node"):
        walker.current_node = "x"  # ty: ignore[invalid-assignment]  # assigns a non-Node on purpose to check the TypeError


def test_current_node_must_share_tree(root: Element) -> None:
    walker = TreeWalker(root)
    with pytest.raises(ValueError, match="own tree"):
        walker.current_node = parse("<p>other</p>")


def test_filter_getter_returns_callable(root: Element) -> None:
    keep = _verdict_on("x", _ACCEPT)
    assert TreeWalker(root, _SHOW_ELEMENT, keep).filter is keep
    assert NodeIterator(root, _SHOW_ELEMENT, keep).filter is keep


def test_standalone_leaf_nodes_wrap() -> None:
    for node in (Text("t"), Comment("c")):
        first = NodeIterator(node).next_node()
        assert first is not None
        assert first.equals(node)


@pytest.mark.parametrize(
    "node_filter", [pytest.param(_verdict_on("x", _ACCEPT), id="filtered"), pytest.param(None, id="plain")]
)
def test_gc_survives_collection(root: Element, node_filter: Callable[[Node], int] | None) -> None:
    walker = TreeWalker(root, _SHOW_ELEMENT, node_filter)
    iterator = NodeIterator(root, _SHOW_ELEMENT, node_filter)
    gc.collect()  # visits the tracked traversers via tp_traverse
    assert walker.first_child() is not None
    assert iterator.next_node() is not None


def test_iterate_module_exports() -> None:
    assert turbohtml.TreeWalker is TreeWalker
    assert turbohtml.NodeIterator is NodeIterator
    assert turbohtml.NodeFilter is NodeFilter


def test_iterator_continues_after_removing_the_reference_ancestor() -> None:
    removal_doc = _removal_doc()
    iterator = NodeIterator(_el(removal_doc.select_one("#r")))
    visited: list[Node] = [next(iterator) for _ in range(3)]
    _el(removal_doc.select_one("#p0")).decompose()
    visited.extend(iterator)
    assert _ids(visited) == ["r", "p0", "b", "p1", "p2"]


def _removal_doc() -> Document:
    # A fresh tree per test: these tests mutate it, and parallel runs must not share it.
    return parse("<div id=r><p id=p0><b></b></p><p id=p1></p><p id=p2></p></div>")


def _ids(nodes: list[Node]) -> list[str]:
    return [str(_el(node).attrs.get("id", _el(node).tag)) for node in nodes]


def test_iterator_removal_after_reference_moves_to_previous_last_descendant() -> None:
    removal_doc = _removal_doc()
    iterator = NodeIterator(_el(removal_doc.select_one("#r")))
    _walk_to(iterator, 4)  # reference: p1, pointer after
    _el(removal_doc.select_one("#p1")).decompose()
    assert (_tags(iterator.reference_node), iterator.pointer_before_reference_node) == ("b", False)


def _walk_to(iterator: NodeIterator, forward: int, back: int = 0) -> None:
    for _ in range(forward):
        iterator.next_node()
    for _ in range(back):
        iterator.previous_node()


def test_iterator_removal_before_reference_moves_to_next_sibling() -> None:
    removal_doc = _removal_doc()
    iterator = NodeIterator(_el(removal_doc.select_one("#r")))
    _walk_to(iterator, 5, back=2)  # reference: p1, pointer before
    _el(removal_doc.select_one("#p1")).decompose()
    assert _ids([iterator.reference_node]) == ["p2"]


def test_iterator_removal_before_reference_without_following_falls_back() -> None:
    removal_doc = _removal_doc()
    iterator = NodeIterator(_el(removal_doc.select_one("#r")))
    _walk_to(iterator, 5, back=1)  # reference: p2, pointer before
    _el(removal_doc.select_one("#p2")).decompose()
    assert (_ids([iterator.reference_node]), iterator.pointer_before_reference_node) == (["p1"], False)


@pytest.mark.parametrize(
    "removed",
    [pytest.param("#p2", id="unrelated"), pytest.param("#p0", id="root-itself")],
)
def test_iterator_ignores_removal_outside_its_reference_path(removed: str) -> None:
    removal_doc = _removal_doc()
    iterator = NodeIterator(_el(removal_doc.select_one("#p0")))
    _walk_to(iterator, 2)  # reference: b, pointer after
    _el(removal_doc.select_one(removed)).decompose()
    assert _tags(iterator.reference_node) == "b"


def test_iterator_adjusts_the_candidate_when_the_filter_removes_nodes() -> None:
    removal_doc = _removal_doc()

    def drop_first_paragraph(node: Node) -> int:
        if _named(node, "b"):
            _el(removal_doc.select_one("#p0")).decompose()
            return _SKIP
        return _ACCEPT

    visited = list(NodeIterator(_el(removal_doc.select_one("#r")), filter=drop_first_paragraph))
    assert _ids(visited) == ["r", "p0", "p1", "p2"]


@pytest.mark.parametrize(
    "remove",
    [
        pytest.param(lambda document: _select_p0(document).extract_contents(), id="range-extract"),
        pytest.param(lambda document: _select_p0(document).delete_contents(), id="range-delete"),
        pytest.param(
            lambda document: turbohtml.Range(_el(document.select_one("#r")), 3).insert_node(
                _el(document.select_one("#p0"))
            ),
            id="range-insert",
        ),
        pytest.param(lambda document: _select_p0(document).surround_contents(Element("section")), id="range-surround"),
        pytest.param(lambda document: _el(document.select_one("#r")).remove("#p0"), id="query-remove"),
        pytest.param(lambda document: _el(document.select_one("#r")).prune("#p1, #p2"), id="query-prune"),
    ],
)
@pytest.mark.parametrize(
    ("forward", "back", "expected"),
    [
        pytest.param(3, 0, ("r", False), id="after"),
        pytest.param(4, 2, ("p1", True), id="before"),
    ],
)
def test_iterator_follows_removals_outside_the_node_api(
    remove: Callable[[Document], object], *, forward: int, back: int, expected: tuple[str, bool]
) -> None:
    removal_doc = _removal_doc()
    iterator = NodeIterator(_el(removal_doc.select_one("#r")))
    _walk_to(iterator, forward, back=back)  # reference: b, pointer before or after
    remove(removal_doc)
    assert (_ids([iterator.reference_node])[0], iterator.pointer_before_reference_node) == expected


def _select_p0(document: Document) -> turbohtml.Range:
    selection = turbohtml.Range(_el(document.select_one("#r")))
    selection.select_node(_el(document.select_one("#p0")))
    return selection


def test_iterator_follows_strip_tags_unwrapping_the_reference_parent() -> None:
    removal_doc = _removal_doc()
    iterator = NodeIterator(_el(removal_doc.select_one("#r")))
    _walk_to(iterator, 4, back=2)  # reference: b, pointer before
    _el(removal_doc.select_one("#r")).strip_tags("#p0")
    # b leaves p0 first, moving the pointer on to p1, and p0 then leaves holding no pointer
    assert (_ids([iterator.reference_node]), iterator.pointer_before_reference_node) == (["p1"], True)


def test_iterator_follows_normalize_merging_the_reference_text() -> None:
    root = Element("div", children=[Text("a"), Text("b")])
    iterator = NodeIterator(root)
    _walk_to(iterator, 3)  # reference: the second text node
    root.normalize()
    assert (iterator.reference_node, iterator.pointer_before_reference_node) == (root.children[0], False)


def test_iterator_follows_linkify_replacing_the_reference_text() -> None:
    root = Element("p", children=[Text("see https://example.com")])
    iterator = NodeIterator(root)
    _walk_to(iterator, 2)  # reference: the text node
    linkify_node(root)
    assert (iterator.reference_node, iterator.pointer_before_reference_node) == (root.children[1].children[0], False)


def test_iterator_follows_shadow_root_inner_html_replacement() -> None:
    shadow = Element("div").attach_shadow()
    shadow.set_inner_html("<p><b></b></p>")
    iterator = NodeIterator(shadow)
    _walk_to(iterator, 3)  # reference: b
    shadow.set_inner_html("<i></i>")
    assert (iterator.reference_node, iterator.pointer_before_reference_node) == (shadow, False)


def test_many_iterators_on_one_tree_all_follow_removals() -> None:
    removal_doc = _removal_doc()
    iterators = [NodeIterator(_el(removal_doc.select_one("#r"))) for _ in range(6)]
    for iterator in iterators:
        _walk_to(iterator, 3)  # reference: b, pointer after
    _el(removal_doc.select_one("#p0")).decompose()
    assert [_ids([iterator.reference_node]) for iterator in iterators] == [["r"]] * 6
