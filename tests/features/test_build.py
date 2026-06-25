"""The turbohtml.build.E builder: construction sugar over Element() plus serialize(), and the factory mechanics."""

from __future__ import annotations

import copy
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
        pytest.param(lambda: E.div(class_="card"), '<div class="card"></div>', id="keyword-trailing-underscore-drops"),
        pytest.param(lambda: E.li(data_i="0"), '<li data-i="0"></li>', id="keyword-underscore-becomes-hyphen"),
        pytest.param(lambda: E.input(disabled=None), '<input disabled="">', id="keyword-none-is-valueless"),
        pytest.param(lambda: E.span(class_=["a", "b"]), '<span class="a b"></span>', id="keyword-list-joins"),
        pytest.param(
            lambda: E.li("item 0", class_="item", data_i="0"),
            '<li class="item" data-i="0">item 0</li>',
            id="keyword-attributes-with-text-child",
        ),
        pytest.param(
            lambda: E.div({"id": "x"}, class_="card"),
            '<div id="x" class="card"></div>',
            id="keyword-merges-over-leading-mapping",
        ),
        pytest.param(
            lambda: E.div({"class": "a"}, class_="b"),
            '<div class="b"></div>',
            id="keyword-overrides-leading-mapping",
        ),
        pytest.param(lambda: E("a", href="/x"), '<a href="/x"></a>', id="call-form-keyword-attributes"),
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
