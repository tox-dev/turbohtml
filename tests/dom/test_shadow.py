from __future__ import annotations

import gc
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import (
    Comment,
    Document,
    DocumentFragment,
    Element,
    Range,
    ShadowRoot,
    Text,
    parse,
    parse_fragment,
    parse_xml,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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


@pytest.mark.parametrize("content", [pytest.param("", id="empty"), pytest.param("text", id="text-only")])
def test_assigned_elements_without_elements(content: str) -> None:
    host: Final = Element("div")
    host.set_inner_html(content)
    root: Final = host.attach_shadow()
    root.set_inner_html("<slot></slot>")
    assert _element(root.select_one("slot")).assigned_elements() == []


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


def test_flatten_expands_deep_fallback_slots_iteratively() -> None:
    root = Element("div").attach_shadow("open")
    outer = Element("slot")
    root.append(outer)
    deepest = outer
    for _ in range(1_200):
        child = Element("slot")
        deepest.append(child)
        deepest = child
    fallback = Text("deep")
    deepest.append(fallback)
    assert outer.assigned_nodes(flatten=True) == [fallback]


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


def test_shadow_root_append_rejects_its_host() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    with pytest.raises(ValueError, match="own subtree"):
        root.append(host)


def test_shadow_descendant_append_rejects_the_host_ancestor() -> None:
    outer = Element("section")
    host = Element("div")
    outer.append(host)
    inner = Element("span")
    host.attach_shadow("open").append(inner)
    with pytest.raises(ValueError, match="own subtree"):
        inner.append(outer)


def test_range_insert_node_rejects_the_shadow_host() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    with pytest.raises(ValueError, match="own subtree"):
        Range(root, 0).insert_node(host)


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


def _declarative_shadow_element(node: object) -> Element:
    """Narrow a find result to a non-None Element."""
    assert isinstance(node, Element)
    return node


def _shadow(node: object) -> ShadowRoot:
    """Narrow a shadow_root result to a non-None ShadowRoot."""
    assert isinstance(node, ShadowRoot)
    return node


def test_open_shadowrootmode_attaches_a_shadow_root() -> None:
    host = _declarative_shadow_element(
        parse("<div id=h><template shadowrootmode=open><p>shadow</p></template></div>").find(id="h")
    )
    root = _shadow(host.shadow_root)
    assert root.mode == "open"
    assert root.html == "<p>shadow</p>"


def test_declarative_template_is_off_the_light_tree() -> None:
    host = _declarative_shadow_element(
        parse("<div id=h><template shadowrootmode=open><p>x</p></template><b>light</b></div>").find(id="h")
    )
    assert host.find("template") is None
    assert host.html == '<div id="h"><b>light</b></div>'


def test_closed_shadowrootmode_hides_the_root_but_keeps_its_content() -> None:
    host = _declarative_shadow_element(
        parse("<div id=h><template shadowrootmode=closed><b>secret</b></template></div>").find(id="h")
    )
    assert host.shadow_root is None  # a closed root is not exposed, like the mutation API
    assert host.find("template") is None
    assert [node.tag for node in host.flattened_children if isinstance(node, Element)] == ["b"]


@pytest.mark.parametrize("mode", ["open", "closed"])
def test_shadowrootmode_value_is_case_insensitive(mode: str) -> None:
    host = _declarative_shadow_element(
        parse(f"<div id=h><template shadowrootmode={mode.upper()}>x</template></div>").find(id="h")
    )
    assert host.find("template") is None  # OPEN / CLOSED still make a declarative root


def test_delegates_focus_and_clonable_flags_are_set() -> None:
    markup = "<div id=h><template shadowrootmode=open shadowrootdelegatesfocus shadowrootclonable>x</template></div>"
    root = _shadow(_declarative_shadow_element(parse(markup).find(id="h")).shadow_root)
    assert root.delegates_focus is True
    assert root.clonable is True


def test_flags_default_off_without_the_attributes() -> None:
    root = _shadow(
        _declarative_shadow_element(
            parse("<div id=h><template shadowrootmode=open>x</template></div>").find(id="h")
        ).shadow_root
    )
    assert root.delegates_focus is False
    assert root.clonable is False


def test_mutation_attached_shadow_has_no_declarative_flags() -> None:
    root = Element("div").attach_shadow("open")
    assert root.delegates_focus is False
    assert root.clonable is False


def test_a_slot_in_the_shadow_assigns_light_children() -> None:
    markup = "<div id=h><template shadowrootmode=open><slot></slot></template><p>light</p></div>"
    host = _declarative_shadow_element(parse(markup).find(id="h"))
    slot = _declarative_shadow_element(_shadow(host.shadow_root).find("slot"))
    assert [node.tag for node in slot.assigned_nodes() if isinstance(node, Element)] == ["p"]


def test_nested_declarative_shadow_roots() -> None:
    markup = (
        "<div id=o><template shadowrootmode=open>"
        "<section id=i><template shadowrootmode=open><b>deep</b></template></section>"
        "</template></div>"
    )
    outer = _shadow(_declarative_shadow_element(parse(markup).find(id="o")).shadow_root)
    inner = _shadow(_declarative_shadow_element(outer.find(id="i")).shadow_root)
    assert inner.html == "<b>deep</b>"


def test_second_template_under_a_host_stays_a_normal_template() -> None:
    markup = "<div id=h><template shadowrootmode=open>a</template><template shadowrootmode=open>b</template></div>"
    host = _declarative_shadow_element(parse(markup).find(id="h"))
    assert _shadow(host.shadow_root).html == "a"
    assert _declarative_shadow_element(host.find("template")) is not None  # the host already had a shadow root


@pytest.mark.parametrize("tag", ["div", "span", "section", "article", "h1", "body", "my-widget"])
def test_valid_shadow_host_elements_attach(tag: str) -> None:
    host = _declarative_shadow_element(
        parse(f"<body><{tag} id=h><template shadowrootmode=open>x</template></{tag}></body>").find(id="h")
    )
    assert host.shadow_root is not None


@pytest.mark.parametrize("tag", ["b", "ul", "foo"])
def test_invalid_shadow_host_elements_keep_a_normal_template(tag: str) -> None:
    host = _declarative_shadow_element(
        parse(f"<body><{tag} id=h><template shadowrootmode=open>x</template></{tag}></body>").find(id="h")
    )
    assert host.shadow_root is None
    assert host.find("template") is not None


def test_foreign_integration_point_is_not_a_shadow_host() -> None:
    # foreignObject runs HTML rules while the current node is SVG-namespaced, so it is no host
    doc = parse("<div><svg><foreignObject id=h><template shadowrootmode=open>x</template></foreignObject></svg></div>")
    host = _declarative_shadow_element(doc.find(id="h"))
    assert host.shadow_root is None
    assert host.find("template") is not None


@pytest.mark.parametrize(
    "markup",
    [
        "<template>x</template>",
        "<template shadowrootmode>x</template>",
        "<template shadowrootmode=off>x</template>",
        "<template shadowrootmode=ope1>x</template>",
    ],
)
def test_non_declarative_template_makes_a_content_fragment(markup: str) -> None:
    host = _declarative_shadow_element(parse(f"<div id=h>{markup}</div>").find(id="h"))
    assert host.shadow_root is None
    template = _declarative_shadow_element(host.find("template"))
    assert template.inner_html == "x"  # a normal template content fragment, not a shadow root


def test_a_dummy_same_length_attribute_is_not_shadowrootmode() -> None:
    # the 14-char attribute matches shadowrootmode's length but not its bytes
    markup = "<div id=h><template aaaaaaaaaaaaaa=1 shadowrootmode=open>x</template></div>"
    assert _declarative_shadow_element(parse(markup).find(id="h")).shadow_root is not None


def test_template_at_the_document_root_is_not_declarative() -> None:
    # the adjusted current node is the topmost element, so it makes a normal template
    doc = parse("<template shadowrootmode=open>x</template>")
    assert _declarative_shadow_element(doc.find("template")) is not None


def test_document_parsing_can_disable_declarative_shadow() -> None:
    markup = "<div id=h><template shadowrootmode=open>x</template></div>"
    host = _declarative_shadow_element(parse(markup, allow_declarative_shadow_roots=False).find(id="h"))
    assert host.shadow_root is None
    assert host.find("template") is not None


def test_fragment_parsing_defaults_to_no_declarative_shadow() -> None:
    host = _declarative_shadow_element(
        parse_fragment("<section><template shadowrootmode=open>x</template></section>", "body").find("section")
    )
    assert host.shadow_root is None
    assert host.find("template") is not None


def test_fragment_parsing_opts_in_to_declarative_shadow() -> None:
    fragment = parse_fragment(
        "<section><template shadowrootmode=open>x</template></section>", "body", allow_declarative_shadow_roots=True
    )
    assert _declarative_shadow_element(fragment.find("section")).shadow_root is not None


def test_fragment_context_element_is_the_shadow_host() -> None:
    fragment = parse_fragment(
        "<template shadowrootmode=open><p>x</p></template>", "div", allow_declarative_shadow_roots=True
    )
    assert _shadow(fragment.shadow_root).html == "<p>x</p>"


def test_fragment_context_that_is_not_a_valid_host_keeps_a_template() -> None:
    fragment = parse_fragment("<template shadowrootmode=open>x</template>", "html", allow_declarative_shadow_roots=True)
    assert fragment.shadow_root is None
    assert _declarative_shadow_element(fragment.find("template")) is not None


@pytest.mark.parametrize("mode", ["open", "closed"])
@pytest.mark.parametrize(
    ("light", "shadow", "expected"),
    [
        pytest.param(
            "",
            '<slot name="a">first</slot><slot name="b">second</slot>',
            ["first", "second"],
            id="empty-host",
        ),
        pytest.param(
            '<b slot="b">one</b><i slot="a">two</i><u slot="b">three</u>',
            '<slot name="a"></slot><slot name="b"></slot>',
            ['<i slot="a">two</i>', '<b slot="b">one</b>', '<u slot="b">three</u>'],
            id="host-order",
        ),
        pytest.param(
            '<b slot="a">one</b>',
            '<slot name="a"></slot><slot name="a">fallback</slot><slot name="a">last</slot>',
            ['<b slot="a">one</b>', "fallback", "last"],
            id="duplicate-first-wins",
        ),
        pytest.param(
            '<b slot="a">one</b>',
            '<section><slot name="a"></slot></section><slot name="a">fallback</slot><slot name="a">last</slot>',
            ['<section><slot name="a"></slot></section>', "fallback", "last"],
            id="first-slot-nested",
        ),
        pytest.param(
            '<!--ignore-->text<b>one</b><i slot="">two</i><u slot="absent">three</u>',
            '<slot name="absent"></slot><slot name>fallback</slot><slot name="">duplicate</slot>',
            ['<u slot="absent">three</u>', "text", "<b>one</b>", '<i slot="">two</i>', "duplicate"],
            id="default-name-and-text",
        ),
        pytest.param(
            '<b slot="é">one</b><i slot="水">two</i><u slot="🦀">three</u>',
            '<slot name="é"></slot><slot name="🦀"></slot><slot name="水"></slot>',
            ['<b slot="é">one</b>', '<u slot="🦀">three</u>', '<i slot="水">two</i>'],
            id="unicode-names",
        ),
        pytest.param(
            '<b slot="missing">one</b>',
            '<slot name="first">first</slot><slot name="second"><slot name="third">nested</slot></slot>',
            ["first", "nested"],
            id="unmatched-nested-fallback",
        ),
    ],
)
def test_flattened_assignment_order(mode: str, light: str, shadow: str, expected: list[str]) -> None:
    host: Final = Element("div")
    host.set_inner_html(light)
    root: Final = host.attach_shadow(mode)
    root.set_inner_html(shadow)
    assert [node.serialize() for node in host.flattened_children] == expected


def test_flattened_assignment_updates_after_edit() -> None:
    host: Final = Element("div")
    host.set_inner_html('<b slot="a">one</b><i slot="b">two</i>')
    root: Final = host.attach_shadow()
    root.set_inner_html('<slot name="a"></slot><slot name="b"></slot>')
    before: Final = host.flattened_children
    host.set_inner_html('<u slot="b">new</u>')
    root.set_inner_html('<slot name="b"></slot><slot name="a">fallback</slot>')
    assert (
        [node.serialize() for node in before],
        [node.serialize() for node in host.flattened_children],
    ) == (['<b slot="a">one</b>', '<i slot="b">two</i>'], ['<u slot="b">new</u>', "fallback"])


def test_flattened_assignment_many_names() -> None:
    host: Final = Element("div")
    host.set_inner_html("".join(f'<i slot="name-{index}">{index}</i>' for index in range(1000)))
    root: Final = host.attach_shadow()
    root.set_inner_html("".join(f'<slot name="name-{index}">fallback</slot>' for index in reversed(range(1000))))
    assert [node.text for node in host.flattened_children] == [str(index) for index in reversed(range(1000))]


def test_shadow_flatten_keeps_sibling_fallback_order() -> None:
    shadow: Final = Element("div").attach_shadow("open")
    shadow.set_inner_html("<slot>" + "".join(f"<slot>{index}</slot>" for index in range(100)) + "</slot>")
    assert [node.text for node in shadow.select("slot")[0].assigned_nodes(flatten=True)] == [
        str(index) for index in range(100)
    ]


@pytest.mark.parametrize("mode", [pytest.param("open", id="open"), pytest.param("closed", id="closed")])
def test_late_slot_collects_children_in_order(mode: str) -> None:
    host: Final[Element] = Element("div")
    host.set_inner_html(
        "<!--before-->" + "<!--between-->".join(f'<span slot="target">{index}</span>' for index in range(64))
    )
    root: Final[ShadowRoot] = host.attach_shadow(mode)
    root.set_inner_html('<slot name="unused"></slot>' * 64 + '<slot name="target"></slot>')
    assert root.select('slot[name="target"]')[0].assigned_nodes() == host.select("span")


def test_renaming_first_slot_reassigns_children() -> None:
    host: Final[Element] = Element("div")
    host.set_inner_html('<span slot="target">a</span><b slot="other">b</b>')
    root: Final[ShadowRoot] = host.attach_shadow("open")
    root.set_inner_html('<slot name="target"></slot><slot name="target"></slot>')
    first, second = root.select("slot")
    before: Final = (first.assigned_nodes(), second.assigned_nodes())
    first.attrs["name"] = "other"
    assert (before, first.assigned_nodes(), second.assigned_nodes()) == (
        ([host.children[0]], []),
        [host.children[1]],
        [host.children[0]],
    )


def test_editing_child_slot_reassigns_children() -> None:
    host: Final[Element] = Element("div")
    host.set_inner_html('<span slot="target">a</span>')
    root: Final[ShadowRoot] = host.attach_shadow("open")
    root.set_inner_html('<slot name="target"></slot><slot></slot>')
    named, default = root.select("slot")
    before: Final = named.assigned_nodes()
    del host.select("span")[0].attrs["slot"]
    assert (before, named.assigned_nodes(), default.assigned_nodes()) == (
        [host.children[0]],
        [],
        [host.children[0]],
    )


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("<!--comment-->" * 64, id="comments"),
        pytest.param('<span slot="other">value</span>', id="nonmatching"),
    ],
)
def test_unassigned_slot_keeps_fallback(content: str) -> None:
    host: Final[Element] = Element("div")
    host.set_inner_html(content)
    root: Final[ShadowRoot] = host.attach_shadow("open")
    root.set_inner_html('<slot name="other"></slot>' * 64 + '<slot name="target"><b>fallback</b></slot>')
    slot: Final[Element] = root.select('slot[name="target"]')[0]
    assert (slot.assigned_nodes(), slot.assigned_nodes(flatten=True)) == ([], slot.select("b"))


@pytest.mark.parametrize("mode", ["open", "closed"])
@pytest.mark.parametrize("depth", [1, 80])
def test_shadow_adoption_preserves_aliases(mode: str, depth: int) -> None:
    host: Final = Element("div", children=[Element("span"), Text("light")])
    children: Final = host.children
    roots: Final[list[tuple[Element, ShadowRoot, Element, int]]] = []
    current = host
    for _ in range(depth):
        root = current.attach_shadow(mode=mode)
        root.set_inner_html("<section><slot></slot></section>")
        child = root.find("section")
        assert child is not None
        roots.append((current, root, child, hash(root)))
        current = child
    target: Final = Element("main")
    target.append(host)
    assert host.children == children
    for owner, root, child, saved_hash in roots:
        assert (root.host, root.children, hash(root)) == (owner, (child,), saved_hash)
        assert owner.shadow_root == (root if mode == "open" else None)


@pytest.mark.parametrize("method", ["assigned_nodes", "assigned_elements", "flattened_children"])
def test_shadow_results_during_adoption(method: str) -> None:
    host: Final = Element("div")
    for index in range(32):
        host.append(Element("span", children=[Text(str(index))]))
        host.append(Text(" "))
    expected: Final = [node for node in host.children if method != "assigned_elements" or isinstance(node, Element)]
    root: Final = host.attach_shadow()
    root.set_inner_html("<slot></slot>")
    slot: Final = root.find("slot")
    assert slot is not None
    targets: Final = (Element("main"), Element("section"))
    start: Final = Barrier(2)
    collect: Final = slot.assigned_elements if method == "assigned_elements" else slot.assigned_nodes

    def read() -> None:
        start.wait()
        for _ in range(200):
            assert (slot.flattened_children if method == "flattened_children" else collect()) == expected

    with ThreadPoolExecutor(max_workers=1) as pool:
        reader: Final = pool.submit(read)
        start.wait()
        for index in range(200):
            targets[index % 2].append(host)
        reader.result()
    assert host.parent == targets[1]


@pytest.mark.parametrize("markup", ["", "<i>one</i><b>two</b>"])
@pytest.mark.parametrize("xml", [False, True])
def test_shadow_adoption_preserves_content(markup: str, *, xml: bool) -> None:
    host: Final = Element("div")
    root: Final = host.attach_shadow()
    root.set_inner_html(markup)
    children: Final = root.children
    target: Final = parse_xml("<main/>").find("main") if xml else Element("main")
    assert target is not None
    target.append(host)
    assert (root.inner_html, root.children, root.host) == (markup, children, host)


@pytest.mark.parametrize("shadow", [False, True])
def test_fragment_adopts_shadow_hosts(*, shadow: bool) -> None:
    fragment: Final = Element("section").attach_shadow() if shadow else DocumentFragment()
    host: Final = Element("div", children=[Element("span")])
    fragment.append(host)
    root: Final = host.attach_shadow()
    root.set_inner_html("<slot></slot>")
    slot: Final = root.find("slot")
    assert slot is not None
    child: Final = host.children[0]
    target: Final = Element("main")
    target.append(fragment)
    assert (target.children, host.shadow_root, root.host, slot.assigned_nodes()) == ((host,), root, host, [child])


def test_shadow_adoption_preserves_declarative_flags() -> None:
    document: Final = parse(
        "<div><template shadowrootmode=open shadowrootdelegatesfocus shadowrootclonable>x</template></div>"
    )
    host: Final = document.find("div")
    assert host is not None
    root: Final = host.shadow_root
    assert root is not None
    Element("main").append(host)
    assert (root.mode, root.delegates_focus, root.clonable, root.text) == ("open", True, True, "x")


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("method", ["assigned_nodes", "assigned_elements", "flattened_children"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_shadow_results_survive_collection_adoption(method: str, offset: int) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    document: Final = parse("<div>text<span>hello</span></div>")
    host: Final = document.find("div")
    assert host is not None
    expected: Final = [child for child in host.children if method != "assigned_elements" or isinstance(child, Element)]
    root: Final = host.attach_shadow()
    root.set_inner_html("<slot></slot>")
    slot: Final = root.find("slot")
    assert slot is not None
    target: Final = Element("main")
    changed = False

    def adopt(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            target.append(host)

    collect: Final = slot.assigned_elements if method == "assigned_elements" else slot.assigned_nodes
    # Retained empty lists exhaust the freelist so result allocation can trigger collection.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(adopt)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = slot.flattened_children if method == "flattened_children" else collect()
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(adopt)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    assert (changed, result, host.parent) == (True, expected, target)


def _fragment(*tags: str) -> DocumentFragment:
    fragment = DocumentFragment()
    for tag in tags:
        fragment.append(Element(tag))
    return fragment


def _host_page() -> tuple[Document, Element]:
    document = parse("<div id=a><i></i></div>")
    target = document.select_one("#a")
    assert target is not None
    return document, target


def test_document_fragment_starts_empty() -> None:
    assert (DocumentFragment().html, repr(DocumentFragment())) == ("", "DocumentFragment()")


def test_document_fragment_takes_no_arguments() -> None:
    with pytest.raises(TypeError):
        DocumentFragment("x")  # ty: ignore[too-many-positional-arguments]


def test_document_fragment_adoption_preserves_descendants() -> None:
    fragment = _fragment("section", "aside")
    section = fragment.children[0]
    assert isinstance(section, Element)
    section.append(Element("b"))
    descendant = section.select_one("b")
    assert descendant is not None
    attributes = descendant.attrs
    held = {descendant}
    target = Element("main")
    target.append(fragment)
    attributes["title"] = "moved"
    assert (target.select_one("b") in held, target.html, fragment.html) == (
        True,
        '<main><section><b title="moved"></b></section><aside></aside></main>',
        "",
    )


def test_shadow_root_is_a_document_fragment() -> None:
    root = Element("div").attach_shadow("open")
    assert (isinstance(root, DocumentFragment), repr(root)) == (True, "ShadowRoot()")


@pytest.mark.parametrize("extract", [pytest.param(False, id="clone"), pytest.param(True, id="extract")])
def test_range_contents_are_a_document_fragment(*, extract: bool) -> None:
    _, target = _host_page()
    boundary = Range(target, 0)
    boundary.set_end(target, 1)
    contents = boundary.extract_contents() if extract else boundary.clone_contents()
    assert (type(contents), contents.html) == (DocumentFragment, "<i></i>")


_INSERTIONS = [
    pytest.param(
        lambda target, fragment: target.append(fragment), '<div id="a"><i></i><b></b><u></u></div>', id="append"
    ),
    pytest.param(
        lambda target, fragment: target.extend([fragment, Element("s")]),
        '<div id="a"><i></i><b></b><u></u><s></s></div>',
        id="extend",
    ),
    pytest.param(
        lambda target, fragment: target.insert(0, fragment), '<div id="a"><b></b><u></u><i></i></div>', id="insert"
    ),
    pytest.param(
        lambda target, fragment: target.children[0].insert_before(fragment),
        '<div id="a"><b></b><u></u><i></i></div>',
        id="insert-before",
    ),
    pytest.param(
        lambda target, fragment: target.children[0].insert_after(fragment),
        '<div id="a"><i></i><b></b><u></u></div>',
        id="insert-after",
    ),
    pytest.param(
        lambda target, fragment: target.children[0].replace_with(fragment),
        '<div id="a"><b></b><u></u></div>',
        id="replace-with",
    ),
    pytest.param(
        lambda target, fragment: Range(target, 1).insert_node(fragment),
        '<div id="a"><i></i><b></b><u></u></div>',
        id="range-insert-node",
    ),
]


@pytest.mark.parametrize(("insert", "expected"), _INSERTIONS)
def test_inserting_a_fragment_places_its_children(
    insert: Callable[[Element, DocumentFragment], object], expected: str
) -> None:
    _, target = _host_page()
    insert(target, _fragment("b", "u"))
    assert target.html == expected


@pytest.mark.parametrize(("insert", "expected"), _INSERTIONS)
def test_inserting_a_fragment_empties_it(insert: Callable[[Element, DocumentFragment], object], expected: str) -> None:
    _, target = _host_page()
    fragment = _fragment("b", "u")
    insert(target, fragment)
    assert (target.html, fragment.children) == (expected, ())


def test_inserting_a_same_tree_fragment_moves_its_children() -> None:
    _, target = _host_page()
    boundary = Range(target, 0)
    boundary.set_end(target, 1)
    fragment = boundary.extract_contents()
    target.append(Element("b"))
    target.append(fragment)
    assert (target.html, fragment.children) == ('<div id="a"><b></b><i></i></div>', ())


def test_element_constructor_places_a_fragment_s_children() -> None:
    assert Element("p", children=[_fragment("b", "u"), Text("t")]).html == "<p><b></b><u></u>t</p>"


def test_element_constructor_places_a_lone_fragment_s_children() -> None:
    assert Element("p", children=[_fragment("b", "u")]).html == "<p><b></b><u></u></p>"


def test_element_constructor_takes_a_tuple_of_one_child() -> None:
    assert Element("p", children=(Text("t"),)).html == "<p>t</p>"


def test_range_insert_of_a_fragment_extends_a_collapsed_range() -> None:
    _, target = _host_page()
    boundary = Range(target, 1)
    boundary.insert_node(_fragment("b", "u"))
    assert (boundary.start_offset, boundary.end_offset) == (1, 3)


def test_appending_a_shadow_root_moves_its_children() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    root.append(Element("span"))
    _, target = _host_page()
    target.append(root)
    assert (target.html, root.children, root.host == host) == ('<div id="a"><i></i><span></span></div>', (), True)


def test_appending_a_same_tree_shadow_root_keeps_it_attached() -> None:
    document, target = _host_page()
    root = target.attach_shadow("open")
    root.append(Comment("c"))
    body = document.select_one("body")
    assert body is not None
    body.append(root)
    assert (body.html, target.shadow_root == root) == ('<body><div id="a"><i></i></div><!--c--></body>', True)


def test_a_template_s_contents_move_out_and_the_template_keeps_its_fragment() -> None:
    document = parse("<template><b>x</b></template><div id=a></div>")
    template = document.select_one("template")
    target = document.select_one("#a")
    assert template is not None
    assert target is not None
    content = template.children[0]
    assert isinstance(content, DocumentFragment)
    target.append(content)
    assert (target.html, template.children, content.children) == ('<div id="a"><b>x</b></div>', (content,), ())


@pytest.mark.parametrize(
    "insert",
    [
        pytest.param(lambda fragment: fragment.append(fragment), id="into-itself"),
        pytest.param(lambda fragment: fragment.children[0].append(fragment), id="into-its-child"),
    ],
)
def test_a_fragment_cannot_go_into_itself(insert: Callable[[DocumentFragment], object]) -> None:
    fragment = _fragment("b")
    with pytest.raises(ValueError, match="own subtree"):
        insert(fragment)


def test_a_fragment_holding_two_elements_cannot_become_a_document_s_root() -> None:
    document = parse("<!DOCTYPE html><html></html>")
    root = document.root
    assert root is not None
    with pytest.raises(ValueError, match="only one element"):
        root.replace_with(_fragment("a", "b"))


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        pytest.param("x", "must be a node", id="non-node"),
        pytest.param(parse(""), "Document cannot be inserted", id="document"),
    ],
)
def test_sibling_insert_rejects_a_bad_argument_before_moving_anything(argument: object, message: str) -> None:
    _, target = _host_page()
    first = Element("b")
    with pytest.raises(TypeError, match=message):
        target.children[0].insert_before(first, argument)  # ty: ignore[invalid-argument-type]
    assert target.html == '<div id="a"><i></i></div>'
