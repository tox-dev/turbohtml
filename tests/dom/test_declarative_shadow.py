"""Declarative Shadow DOM: a `<template shadowrootmode>` attaches a shadow root to its parent."""

from __future__ import annotations

import pytest

from turbohtml import Element, ShadowRoot, parse, parse_fragment


def _element(node: object) -> Element:
    """Narrow a find result to a non-None Element."""
    assert isinstance(node, Element)
    return node


def _shadow(node: object) -> ShadowRoot:
    """Narrow a shadow_root result to a non-None ShadowRoot."""
    assert isinstance(node, ShadowRoot)
    return node


def test_open_shadowrootmode_attaches_a_shadow_root() -> None:
    host = _element(parse("<div id=h><template shadowrootmode=open><p>shadow</p></template></div>").find(id="h"))
    root = _shadow(host.shadow_root)
    assert root.mode == "open"
    assert root.html == "<p>shadow</p>"


def test_declarative_template_is_off_the_light_tree() -> None:
    host = _element(parse("<div id=h><template shadowrootmode=open><p>x</p></template><b>light</b></div>").find(id="h"))
    assert host.find("template") is None
    assert host.html == '<div id="h"><b>light</b></div>'


def test_closed_shadowrootmode_hides_the_root_but_keeps_its_content() -> None:
    host = _element(parse("<div id=h><template shadowrootmode=closed><b>secret</b></template></div>").find(id="h"))
    assert host.shadow_root is None  # a closed root is not exposed, like the mutation API
    assert host.find("template") is None
    assert [node.tag for node in host.flattened_children if isinstance(node, Element)] == ["b"]


@pytest.mark.parametrize("mode", ["open", "closed"])
def test_shadowrootmode_value_is_case_insensitive(mode: str) -> None:
    host = _element(parse(f"<div id=h><template shadowrootmode={mode.upper()}>x</template></div>").find(id="h"))
    assert host.find("template") is None  # OPEN / CLOSED still make a declarative root


def test_delegates_focus_and_clonable_flags_are_set() -> None:
    markup = "<div id=h><template shadowrootmode=open shadowrootdelegatesfocus shadowrootclonable>x</template></div>"
    root = _shadow(_element(parse(markup).find(id="h")).shadow_root)
    assert root.delegates_focus is True
    assert root.clonable is True


def test_flags_default_off_without_the_attributes() -> None:
    root = _shadow(
        _element(parse("<div id=h><template shadowrootmode=open>x</template></div>").find(id="h")).shadow_root
    )
    assert root.delegates_focus is False
    assert root.clonable is False


def test_mutation_attached_shadow_has_no_declarative_flags() -> None:
    root = Element("div").attach_shadow("open")
    assert root.delegates_focus is False
    assert root.clonable is False


def test_a_slot_in_the_shadow_assigns_light_children() -> None:
    markup = "<div id=h><template shadowrootmode=open><slot></slot></template><p>light</p></div>"
    host = _element(parse(markup).find(id="h"))
    slot = _element(_shadow(host.shadow_root).find("slot"))
    assert [node.tag for node in slot.assigned_nodes() if isinstance(node, Element)] == ["p"]


def test_nested_declarative_shadow_roots() -> None:
    markup = (
        "<div id=o><template shadowrootmode=open>"
        "<section id=i><template shadowrootmode=open><b>deep</b></template></section>"
        "</template></div>"
    )
    outer = _shadow(_element(parse(markup).find(id="o")).shadow_root)
    inner = _shadow(_element(outer.find(id="i")).shadow_root)
    assert inner.html == "<b>deep</b>"


def test_second_template_under_a_host_stays_a_normal_template() -> None:
    markup = "<div id=h><template shadowrootmode=open>a</template><template shadowrootmode=open>b</template></div>"
    host = _element(parse(markup).find(id="h"))
    assert _shadow(host.shadow_root).html == "a"
    assert _element(host.find("template")) is not None  # the host already had a shadow root


@pytest.mark.parametrize("tag", ["div", "span", "section", "article", "h1", "body", "my-widget"])
def test_valid_shadow_host_elements_attach(tag: str) -> None:
    host = _element(parse(f"<body><{tag} id=h><template shadowrootmode=open>x</template></{tag}></body>").find(id="h"))
    assert host.shadow_root is not None


@pytest.mark.parametrize("tag", ["b", "ul", "foo"])
def test_invalid_shadow_host_elements_keep_a_normal_template(tag: str) -> None:
    host = _element(parse(f"<body><{tag} id=h><template shadowrootmode=open>x</template></{tag}></body>").find(id="h"))
    assert host.shadow_root is None
    assert host.find("template") is not None


def test_foreign_integration_point_is_not_a_shadow_host() -> None:
    # foreignObject runs HTML rules while the current node is SVG-namespaced, so it is no host
    doc = parse("<div><svg><foreignObject id=h><template shadowrootmode=open>x</template></foreignObject></svg></div>")
    host = _element(doc.find(id="h"))
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
    host = _element(parse(f"<div id=h>{markup}</div>").find(id="h"))
    assert host.shadow_root is None
    template = _element(host.find("template"))
    assert template.inner_html == "x"  # a normal template content fragment, not a shadow root


def test_a_dummy_same_length_attribute_is_not_shadowrootmode() -> None:
    # the 14-char attribute matches shadowrootmode's length but not its bytes
    markup = "<div id=h><template aaaaaaaaaaaaaa=1 shadowrootmode=open>x</template></div>"
    assert _element(parse(markup).find(id="h")).shadow_root is not None


def test_template_at_the_document_root_is_not_declarative() -> None:
    # the adjusted current node is the topmost element, so it makes a normal template
    doc = parse("<template shadowrootmode=open>x</template>")
    assert _element(doc.find("template")) is not None


def test_document_parsing_can_disable_declarative_shadow() -> None:
    markup = "<div id=h><template shadowrootmode=open>x</template></div>"
    host = _element(parse(markup, allow_declarative_shadow_roots=False).find(id="h"))
    assert host.shadow_root is None
    assert host.find("template") is not None


def test_fragment_parsing_defaults_to_no_declarative_shadow() -> None:
    host = _element(
        parse_fragment("<section><template shadowrootmode=open>x</template></section>", "body").find("section")
    )
    assert host.shadow_root is None
    assert host.find("template") is not None


def test_fragment_parsing_opts_in_to_declarative_shadow() -> None:
    fragment = parse_fragment(
        "<section><template shadowrootmode=open>x</template></section>", "body", allow_declarative_shadow_roots=True
    )
    assert _element(fragment.find("section")).shadow_root is not None


def test_fragment_context_element_is_the_shadow_host() -> None:
    fragment = parse_fragment(
        "<template shadowrootmode=open><p>x</p></template>", "div", allow_declarative_shadow_roots=True
    )
    assert _shadow(fragment.shadow_root).html == "<p>x</p>"


def test_fragment_context_that_is_not_a_valid_host_keeps_a_template() -> None:
    fragment = parse_fragment("<template shadowrootmode=open>x</template>", "html", allow_declarative_shadow_roots=True)
    assert fragment.shadow_root is None
    assert _element(fragment.find("template")) is not None
