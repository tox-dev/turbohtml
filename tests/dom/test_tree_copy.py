"""copy.copy / copy.deepcopy duplicate a node's subtree into a standalone tree."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import pytest

from turbohtml import Doctype, Element, Text, parse

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    "duplicate",
    [pytest.param(copy.copy, id="shallow"), pytest.param(copy.deepcopy, id="deep")],
)
def test_duplicates_a_standalone_subtree(duplicate: Callable[[Element], Element]) -> None:
    div = parse('<div id="a"><b>x</b>y</div>').find("div")
    assert div is not None
    clone = duplicate(div)
    assert clone is not div
    assert clone.html == '<div id="a"><b>x</b>y</div>'
    assert clone.parent is None  # both shallow and deep yield a detached root, not a view


def test_copy_is_independent_of_the_original() -> None:
    div = parse("<div><b>x</b></div>").find("div")
    assert div is not None
    clone = copy.copy(div)
    clone.append(Element("z"))  # editing the copy must not touch the source
    assert div.html == "<div><b>x</b></div>"
    assert clone.html == "<div><b>x</b><z></z></div>"


def test_copy_of_a_constructed_node() -> None:
    element = Element("p", {"class": ["a", "b"]})
    element.append(Text("hi"))
    assert copy.copy(element).html == '<p class="a b">hi</p>'


@pytest.mark.parametrize(
    "duplicate",
    [pytest.param(copy.copy, id="shallow"), pytest.param(copy.deepcopy, id="deep")],
)
def test_copy_preserves_doctype_identifiers(duplicate: Callable[[Doctype], Doctype]) -> None:
    # the clone must carry the public-id split point so an embedded quote survives (part of #478)
    doctype = parse("<!DOCTYPE html PUBLIC 'pub\"lic' 'sys\"tem'>").children[0]
    assert isinstance(doctype, Doctype)
    clone = duplicate(doctype)
    assert (clone.public_id, clone.system_id) == ('pub"lic', 'sys"tem')
