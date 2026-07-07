"""The Shadow DOM tree model: attach_shadow / ShadowRoot, slot assignment, and the flattened tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Element, Range, ShadowRoot, Text, parse

if TYPE_CHECKING:
    from collections.abc import Iterable

    from turbohtml import Node


def _tags(nodes: Iterable[Node]) -> list[str]:
    """The tag of each node, asserting every one is an element (so a stray text node fails the test)."""
    tags = []
    for node in nodes:
        assert isinstance(node, Element)
        tags.append(node.tag)
    return tags


def _element(node: Node | None) -> Element:
    """Narrow a find/select_one result to a non-None Element."""
    assert isinstance(node, Element)
    return node


@pytest.fixture
def slotted() -> tuple[Element, ShadowRoot]:
    """A host of light children attached to a shadow root exposing a named and a default slot."""
    host = Element("div")
    host.append(Element("span", {"slot": "title"}, [Text("Hello")]))
    host.append(Element("p", None, [Text("body")]))
    host.append(Element("em", {"slot": "title"}, [Text("World")]))
    host.append(Text("loose"))
    root = host.attach_shadow("open")
    root.set_inner_html('<header><slot name="title">fallback</slot></header><main><slot>default</slot></main>')
    return host, root


@pytest.mark.parametrize("mode", ["open", "closed"])
def test_attach_shadow_returns_shadow_root(mode: str) -> None:
    root = Element("div").attach_shadow(mode)
    assert isinstance(root, ShadowRoot)
    assert root.mode == mode


def test_attach_shadow_defaults_to_open() -> None:
    assert Element("div").attach_shadow().mode == "open"


def test_attach_shadow_links_host() -> None:
    host = Element("section")
    root = host.attach_shadow("open")
    assert root.host == host


def test_shadow_root_host_identity_survives_rewrap() -> None:
    host = Element("div")
    host.attach_shadow("open")
    root = host.shadow_root
    assert root is not None
    assert root.host == host


def test_open_shadow_root_is_exposed() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    assert host.shadow_root == root


def test_closed_shadow_root_is_hidden() -> None:
    host = Element("div")
    host.attach_shadow("closed")
    assert host.shadow_root is None


def test_element_without_shadow_reports_none() -> None:
    assert Element("div").shadow_root is None


def test_attach_shadow_twice_raises() -> None:
    host = Element("div")
    host.attach_shadow("open")
    with pytest.raises(ValueError, match="already has a shadow root"):
        host.attach_shadow("open")


def test_attach_shadow_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="must be 'open' or 'closed'"):
        Element("div").attach_shadow("half")


def test_attach_shadow_rejects_non_str_mode() -> None:
    with pytest.raises(TypeError):
        Element("div").attach_shadow(123)  # ty: ignore[invalid-argument-type]


def test_shadow_root_not_instantiable() -> None:
    with pytest.raises(TypeError):
        ShadowRoot()


def test_shadow_root_parent_is_none() -> None:
    root = Element("div").attach_shadow("open")
    assert root.parent is None


def test_named_slot_collects_matching_children(slotted: tuple[Element, ShadowRoot]) -> None:
    _host, root = slotted
    title = _element(root.select_one('slot[name="title"]'))
    assert _tags(title.assigned_nodes()) == ["span", "em"]


def test_default_slot_collects_unnamed_children(slotted: tuple[Element, ShadowRoot]) -> None:
    _host, root = slotted
    default = _element(root.select_one("main slot"))
    assert [type(node).__name__ for node in default.assigned_nodes()] == ["Element", "Text"]


def test_assigned_elements_drops_text_nodes(slotted: tuple[Element, ShadowRoot]) -> None:
    _host, root = slotted
    default = _element(root.select_one("main slot"))
    assert _tags(default.assigned_elements()) == ["p"]


def test_valueless_slot_name_is_the_default_slot() -> None:
    host = Element("div")
    host.append(Element("p", None, [Text("x")]))
    root = host.attach_shadow("open")
    root.set_inner_html("<slot name></slot>")
    assert _tags(_element(root.select_one("slot")).assigned_nodes()) == ["p"]


def test_first_matching_slot_wins() -> None:
    host = Element("div")
    host.append(Element("p", {"slot": "a"}, [Text("x")]))
    root = host.attach_shadow("open")
    root.set_inner_html('<slot name="a"></slot><slot name="a"></slot>')
    first, second = root.select("slot")
    assert first.assigned_nodes()
    assert not second.assigned_nodes()


def test_slot_name_of_equal_length_does_not_match() -> None:
    host = Element("div")
    child = Element("p", {"slot": "ab"})
    host.append(child)
    root = host.attach_shadow("open")
    root.set_inner_html('<slot name="xy"></slot><slot name="ab"></slot>')
    first, second = root.select("slot")
    assert child.assigned_slot == second
    assert first.assigned_nodes() == []


def test_assigned_slot_of_named_child(slotted: tuple[Element, ShadowRoot]) -> None:
    host, _root = slotted
    assert _element(host.children[0].assigned_slot).attr("name") == "title"


def test_assigned_slot_of_text_child(slotted: tuple[Element, ShadowRoot]) -> None:
    host, _root = slotted
    loose_slot = host.children[3].assigned_slot
    assert loose_slot is not None
    assert loose_slot.attr("name") is None


def test_parentless_slottable_has_no_slot() -> None:
    assert Element("span").assigned_slot is None


def test_non_slottable_host_children_are_skipped() -> None:
    host = Element("div")
    host.append(Comment("ignored"))
    host.append(Element("p"))
    root = host.attach_shadow("open")
    root.set_inner_html("<slot></slot>")
    assert _tags(_element(root.select_one("slot")).assigned_nodes()) == ["p"]


def test_find_slot_skips_foreign_elements() -> None:
    host = Element("div")
    child = Element("p")
    host.append(child)
    root = host.attach_shadow("open")
    root.set_inner_html("<svg></svg><slot></slot>")
    assert child.assigned_slot == root.select_one("slot")


def test_assigned_nodes_rejects_extra_positional() -> None:
    with pytest.raises(TypeError):
        Element("slot").assigned_nodes(1, 2)  # ty: ignore[too-many-positional-arguments]


def test_unassigned_child_has_no_slot() -> None:
    host = Element("div")
    child = Element("p", {"slot": "missing"})
    host.append(child)
    host.attach_shadow("open").set_inner_html('<slot name="other"></slot>')
    assert child.assigned_slot is None


def test_child_of_hostless_element_has_no_slot() -> None:
    parent = Element("div")
    child = Element("p")
    parent.append(child)
    assert child.assigned_slot is None


def test_closed_shadow_hides_assigned_slot() -> None:
    host = Element("div")
    child = Element("p", {"slot": "x"})
    host.append(child)
    host.attach_shadow("closed").set_inner_html('<slot name="x"></slot>')
    assert child.assigned_slot is None


def test_non_slottable_has_no_slot() -> None:
    assert Comment("c").assigned_slot is None


def test_assigned_nodes_requires_a_slot() -> None:
    with pytest.raises(TypeError, match="only valid on a <slot>"):
        Element("div").assigned_nodes()


def test_slot_outside_shadow_tree_assigns_nothing() -> None:
    loose = Element("slot")
    assert loose.assigned_nodes() == []
    assert loose.assigned_nodes(flatten=True) == []


def test_slot_in_a_plain_fragment_assigns_nothing() -> None:
    # a Range-cloned document fragment is a content root but not a shadow root
    box = _element(parse("<div id=box><slot name='x'></slot></div>").find(id="box"))
    selection = Range(box)
    selection.select_node_contents(box)
    slot = _element(selection.clone_contents().children[0])
    assert slot.assigned_nodes() == []
    assert slot.flattened_children == []


def test_flatten_falls_back_to_slot_children() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    root.set_inner_html('<slot name="x">fallback<b>bold</b></slot>')
    slot = _element(root.select_one("slot"))
    assert slot.assigned_nodes() == []
    assert [type(node).__name__ for node in slot.assigned_nodes(flatten=True)] == ["Text", "Element"]


def test_flatten_expands_nested_fallback_slot() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    root.set_inner_html('<slot name="outer"><slot name="inner">deep</slot></slot>')
    outer = _element(root.select_one('slot[name="outer"]'))
    assert [type(node).__name__ for node in outer.assigned_nodes(flatten=True)] == ["Text"]


def test_flatten_fallback_skips_non_slottable_content() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    root.set_inner_html("<slot name='x'>text<!--comment--></slot>")
    slot = _element(root.select_one("slot"))
    assert [type(node).__name__ for node in slot.assigned_nodes(flatten=True)] == ["Text"]


def test_flatten_keeps_a_light_slot_unexpanded() -> None:
    host = Element("div")
    host.append(Element("slot"))  # a slot in the light DOM, not a shadow tree
    root = host.attach_shadow("open")
    root.set_inner_html("<slot></slot>")
    shadow_slot = _element(root.select_one("slot"))
    assert [type(node).__name__ for node in shadow_slot.assigned_nodes(flatten=True)] == ["Element"]


def test_flattened_children_of_host_expand_top_level_slots() -> None:
    host = Element("div")
    host.append(Element("a", None, [Text("x")]))
    host.append(Element("b", {"slot": "n"}, [Text("y")]))
    root = host.attach_shadow("open")
    root.set_inner_html('<slot name="n"></slot><slot></slot>')
    assert _tags(host.flattened_children) == ["b", "a"]


def test_flattened_children_of_host_descend_into_shadow(slotted: tuple[Element, ShadowRoot]) -> None:
    host, _root = slotted
    assert _tags(host.flattened_children) == ["header", "main"]


def test_flattened_children_of_slot_are_its_slottables(slotted: tuple[Element, ShadowRoot]) -> None:
    _host, root = slotted
    title = _element(root.select_one('slot[name="title"]'))
    assert _tags(title.flattened_children) == ["span", "em"]


@pytest.mark.parametrize(
    ("element", "expected"),
    [
        # a light-DOM slot (root is not a shadow root) is never expanded, so it yields its own children
        pytest.param(Element("slot", None, [Element("b")]), ["b"], id="light-slot-yields-its-children"),
        pytest.param(Element("div", None, [Element("slot"), Element("p")]), ["slot", "p"], id="light-slot-child-kept"),
        pytest.param(Element("ul", None, [Element("li"), Element("li")]), ["li", "li"], id="plain-element-children"),
    ],
)
def test_flattened_children_of_a_light_tree(element: Element, expected: list[str]) -> None:
    assert _tags(element.flattened_children) == expected


def test_shadow_content_is_off_the_light_tree(slotted: tuple[Element, ShadowRoot]) -> None:
    host, _root = slotted
    assert host.find_all("slot") == []
    assert [type(node).__name__ for node in host.children] == ["Element", "Element", "Element", "Text"]


def test_host_serialization_excludes_shadow(slotted: tuple[Element, ShadowRoot]) -> None:
    host, _root = slotted
    assert "fallback" not in host.html  # the shadow-only content never reaches the light serialization
    assert host.html == '<div><span slot="title">Hello</span><p>body</p><em slot="title">World</em>loose</div>'


def test_shadow_root_serializes_its_content(slotted: tuple[Element, ShadowRoot]) -> None:
    _host, root = slotted
    assert root.html == '<header><slot name="title">fallback</slot></header><main><slot>default</slot></main>'


def test_set_inner_html_replaces_content() -> None:
    root = Element("div").attach_shadow("open")
    root.set_inner_html("<p>one</p>")
    root.set_inner_html("<span>two</span>")
    assert root.html == "<span>two</span>"


def test_set_inner_html_requires_str() -> None:
    root = Element("div").attach_shadow("open")
    with pytest.raises(TypeError, match="html must be a str"):
        root.set_inner_html(123)  # ty: ignore[invalid-argument-type]


def test_shadow_root_append_moves_a_same_tree_node() -> None:
    host = Element("div")
    moved = Element("span")
    host.append(moved)
    root = host.attach_shadow("open")
    root.append(moved)
    assert moved.parent == root
    assert host.children == ()


def test_shadow_root_append_copies_a_foreign_node() -> None:
    root = Element("div").attach_shadow("open")
    root.append(Element("p", None, [Text("hi")]))
    assert root.html == "<p>hi</p>"


def test_shadow_root_append_rejects_non_node() -> None:
    root = Element("div").attach_shadow("open")
    with pytest.raises(TypeError, match="must be a node"):
        root.append("nope")  # ty: ignore[invalid-argument-type]


def test_multiple_shadow_hosts_in_one_tree() -> None:
    child = Element("section")
    host = Element("div", None, [child])  # the child is adopted into the host's tree
    host_root = host.attach_shadow("open")
    child_root = child.attach_shadow("open")
    assert host.shadow_root == host_root
    assert child.shadow_root == child_root
    assert host_root.host == host
    assert child_root.host == child


def test_attach_shadow_on_a_parsed_element() -> None:
    app = _element(parse("<main id=app></main>").find(id="app"))
    root = app.attach_shadow("open")
    root.set_inner_html("<slot></slot>")
    app.append(Element("p", None, [Text("slotted")]))
    assert _tags(_element(root.select_one("slot")).assigned_nodes()) == ["p"]
    assert app.shadow_root == root
