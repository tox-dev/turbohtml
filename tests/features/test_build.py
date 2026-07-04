"""The turbohtml.build.E builder: construction sugar over Element() plus serialize(), and the factory mechanics."""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Element, Text, parse_fragment
from turbohtml.build import E, ElementMaker

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node


@pytest.fixture
def maker() -> ElementMaker:
    """A private builder, independent of the shared ``E`` singleton."""
    return ElementMaker()


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(E.div, "<div></div>", id="empty-element-keeps-end-tag"),
        pytest.param(E.br, "<br>", id="void-element-has-no-end-tag"),
        pytest.param(lambda: E.p("body"), "<p>body</p>", id="text-child"),
        pytest.param(lambda: E.div({"class": "card"}), '<div class="card"></div>', id="leading-mapping-is-attributes"),
        pytest.param(lambda: E.div({"class": ["a", "b"]}), '<div class="a b"></div>', id="list-attribute-joins"),
        pytest.param(lambda: E.input({"disabled": None}), '<input disabled="">', id="none-attribute-is-valueless"),
        pytest.param(
            lambda: E.div({"class": "card"}, E.h1("Title"), E.p("body")),
            '<div class="card"><h1>Title</h1><p>body</p></div>',
            id="nesting-builds-real-children",
        ),
        pytest.param(lambda: E.p("a", E.b("b"), "c"), "<p>a<b>b</b>c</p>", id="children-keep-order"),
        pytest.param(lambda: E.div(Comment("note")), "<div><!--note--></div>", id="non-string-node-passes-through"),
        pytest.param(
            lambda: E.div("before", E.span("mid"), "after"),
            "<div>before<span>mid</span>after</div>",
            id="mixed-text-and-element-children",
        ),
        pytest.param(lambda: E("div", "body"), "<div>body</div>", id="call-form-names-the-tag"),
        pytest.param(lambda: E("a", {"href": "/x"}, "link"), '<a href="/x">link</a>', id="call-form-leading-mapping"),
        pytest.param(lambda: E("my-widget"), "<my-widget></my-widget>", id="call-form-non-identifier-tag"),
    ],
)
def test_serialize(build: Callable[[], Element], expected: str) -> None:
    assert build().serialize() == expected


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(
            lambda: E.section(E.h2("Heading"), E.p("text")),
            "<section><h2>Heading</h2><p>text</p></section>",
            id="section",
        ),
        pytest.param(lambda: E.ul(E.li("one"), E.li("two")), "<ul><li>one</li><li>two</li></ul>", id="list"),
    ],
)
def test_round_trips_through_parse(build: Callable[[], Element], expected: str) -> None:
    assert build().serialize() == expected
    assert parse_fragment(expected).inner_html == expected


@pytest.mark.parametrize(
    "child",
    [
        pytest.param("body", id="string-becomes-text-node"),
        pytest.param(Text("body"), id="prebuilt-text-node-is-not-rewrapped"),
    ],
)
def test_text_child_is_a_text_node(child: str | Text) -> None:
    (built,) = E.p(child).children
    assert isinstance(built, Text)
    assert built.data == "body"


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(E.div, id="attribute-access"),
        pytest.param(lambda: E("p"), id="call-form"),
    ],
)
def test_builds_a_real_element(build: Callable[[], Node]) -> None:
    assert isinstance(build(), Element)


def test_attribute_access_returns_a_fresh_callable() -> None:
    assert E.div is not E.div  # each access builds its own factory; no shared mutable state


def test_a_separate_maker_builds_the_same_way(maker: ElementMaker) -> None:
    assert maker.span("x").serialize() == "<span>x</span>"


def test_non_leading_mapping_is_rejected() -> None:
    with pytest.raises(TypeError, match="must come first"):
        E.div("text", {"id": "b"})


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("a<b", id="less-than"),  # <  is the issue #413 regression: it used to slip through
        pytest.param('a"b', id="double-quote"),
        pytest.param("a'b", id="single-quote"),
        pytest.param("a=b", id="equals"),
        pytest.param("a b", id="space"),
        pytest.param("a/b", id="slash"),
        pytest.param("a>b", id="greater-than"),
    ],
)
def test_invalid_attribute_name_is_rejected(name: str) -> None:
    expected = rf"attribute name {re.escape(repr(name))} contains an invalid character: {re.escape(repr(name[1]))}"
    with pytest.raises(ValueError, match=expected):
        E.div({name: "x"}, "hi")


@pytest.mark.parametrize(
    ("build", "tag"),
    [
        pytest.param(lambda: E.br("x"), "br", id="br-text-child"),
        pytest.param(lambda: E.img("x", E.span("y")), "img", id="img-mixed-children"),
        pytest.param(lambda: E.hr(E.span("y")), "hr", id="hr-node-child"),
        pytest.param(lambda: E("input", "x"), "input", id="call-form-input"),
    ],
)
def test_void_element_rejects_children(build: Callable[[], Element], tag: str) -> None:
    with pytest.raises(ValueError, match=rf"void element '{tag}' cannot have children"):
        build()


def test_non_node_child_is_rejected() -> None:
    with pytest.raises(TypeError, match="child must be a node"):
        E.div(42)  # ty: ignore[invalid-argument-type]  # neither a node nor a string


def test_constructor_children_none_builds_empty() -> None:
    # the builder always passes a list, so None reaches the constructor only directly
    assert Element("div", None, None).serialize() == "<div></div>"


def test_constructor_accepts_a_children_tuple() -> None:
    # the builder always passes a list, so a tuple reaches the constructor only directly
    assert Element("div", None, (Text("a"), Text("b"))).serialize() == "<div>ab</div>"


def test_constructor_children_must_be_iterable() -> None:
    with pytest.raises(TypeError, match="children must be iterable"):
        Element("div", None, 42)  # ty: ignore[invalid-argument-type]  # children must be an iterable of nodes


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("__deepcopy__", id="deepcopy"),
        pytest.param("__setstate__", id="setstate"),
        pytest.param("__wrapped__", id="wrapped"),
    ],
)
def test_dunder_lookup_falls_through(maker: ElementMaker, name: str) -> None:
    with pytest.raises(AttributeError):
        getattr(maker, name)


def test_deepcopy_is_not_hijacked_by_getattr(maker: ElementMaker) -> None:
    assert isinstance(copy.deepcopy(maker), ElementMaker)
