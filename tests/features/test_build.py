"""The turbohtml.build.E builder: construction sugar over Element() plus serialize()."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Element, Text, parse_fragment
from turbohtml.build import E

if TYPE_CHECKING:
    from collections.abc import Callable


def test_empty_element_serializes_with_end_tag() -> None:
    assert E.div().serialize() == "<div></div>"


def test_void_element_has_no_end_tag() -> None:
    assert E.br().serialize() == "<br>"


def test_text_child_becomes_a_text_node() -> None:
    paragraph = E.p("body")
    (child,) = paragraph.children
    assert isinstance(child, Text)
    assert child.data == "body"


def test_text_child_serializes() -> None:
    assert E.p("body").serialize() == "<p>body</p>"


def test_leading_mapping_is_attributes() -> None:
    assert E.div({"class": "card"}).serialize() == '<div class="card"></div>'


def test_list_valued_attribute_joins_on_space() -> None:
    assert E.div({"class": ["a", "b"]}).serialize() == '<div class="a b"></div>'


def test_none_attribute_is_valueless() -> None:
    assert E.input({"disabled": None}).serialize() == '<input disabled="">'


def test_nesting_builds_real_child_elements() -> None:
    card = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    assert card.serialize() == '<div class="card"><h1>Title</h1><p>body</p></div>'


def test_children_keep_their_order() -> None:
    assert E.p("a", E.b("b"), "c").serialize() == "<p>a<b>b</b>c</p>"


def test_prebuilt_text_node_child_is_appended_not_rewrapped() -> None:
    paragraph = E.p(Text("body"))
    (child,) = paragraph.children
    assert isinstance(child, Text)
    assert child.data == "body"


def test_non_string_node_child_passes_through_append() -> None:
    assert E.div(Comment("note")).serialize() == "<div><!--note--></div>"


def test_returns_a_real_element() -> None:
    assert isinstance(E.div(), Element)


def test_mixed_text_and_element_children() -> None:
    assert E.div("before", E.span("mid"), "after").serialize() == "<div>before<span>mid</span>after</div>"


@pytest.mark.parametrize(
    ("markup_builder", "expected"),
    [
        pytest.param(
            lambda: E.section(E.h2("Heading"), E.p("text")),
            "<section><h2>Heading</h2><p>text</p></section>",
            id="section",
        ),
        pytest.param(
            lambda: E.ul(E.li("one"), E.li("two")),
            "<ul><li>one</li><li>two</li></ul>",
            id="list",
        ),
    ],
)
def test_round_trip_through_parse(markup_builder: Callable[[], Element], expected: str) -> None:
    assert markup_builder().serialize() == expected
    assert parse_fragment(expected).inner_html == expected
