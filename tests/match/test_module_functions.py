"""The module-level helpers compile a one-shot matcher and delegate, like soupsieve's free functions."""

from __future__ import annotations

from turbohtml import Element, parse
from turbohtml.match import closest, filter, iselect, match, select, select_one  # noqa: A004  # the soupsieve names

_DOC = "<div><a href=x>one</a><span><a href=y>two</a></span></div>"


def _root() -> Element:
    root = parse(_DOC).root
    assert root is not None
    return root


def test_select_collects_matches() -> None:
    assert [node.attr("href") for node in select("a[href]", _root())] == ["x", "y"]


def test_select_honors_limit() -> None:
    assert [node.attr("href") for node in select("a[href]", _root(), limit=1)] == ["x"]


def test_select_one_returns_first() -> None:
    found = select_one("a[href]", _root())
    assert found is not None
    assert found.attr("href") == "x"


def test_iselect_iterates_matches() -> None:
    assert [node.attr("href") for node in iselect("a[href]", _root(), limit=2)] == ["x", "y"]


def test_match_tests_an_element() -> None:
    anchor = select_one("a[href]", _root())
    assert anchor is not None
    assert match("a[href]", anchor) is True


def test_filter_keeps_matching_members() -> None:
    anchors = _root().select("a")
    assert [node.attr("href") for node in filter("[href=y]", anchors)] == ["y"]


def test_closest_walks_up() -> None:
    anchor = select_one("a[href=y]", _root())
    assert anchor is not None
    found = closest("div", anchor)
    assert found is not None
    assert found.tag == "div"
