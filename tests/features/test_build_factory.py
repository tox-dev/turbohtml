"""The ElementMaker factory mechanics: the call form, custom makers, attribute merging, and dunder fall-through."""

from __future__ import annotations

import copy

import pytest

from turbohtml import Element
from turbohtml.build import E, ElementMaker


def test_call_form_names_the_tag() -> None:
    assert E("div", "body").serialize() == "<div>body</div>"


def test_call_form_takes_a_leading_mapping() -> None:
    assert E("a", {"href": "/x"}, "link").serialize() == '<a href="/x">link</a>'


def test_call_form_builds_a_non_identifier_tag() -> None:
    assert E("my-widget").serialize() == "<my-widget></my-widget>"


def test_attribute_access_returns_a_fresh_callable() -> None:
    assert E.div is not E.div  # each access builds its own factory; no shared mutable state


def test_a_separate_maker_builds_the_same_way() -> None:
    maker = ElementMaker()
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
def test_dunder_lookup_falls_through(name: str) -> None:
    with pytest.raises(AttributeError):
        getattr(E, name)


def test_deepcopy_is_not_hijacked_by_getattr() -> None:
    assert isinstance(copy.deepcopy(E), ElementMaker)


def test_builds_a_real_element_via_call_form() -> None:
    assert isinstance(E("p"), Element)
