"""matches() and closest(): self-test and nearest-ancestor CSS matching."""

from __future__ import annotations

import pytest

from turbohtml import Element, parse

_DOC = (
    '<section id="s" class="box">'
    '<article class="post"><h2>T</h2><p class="lead">one <a href="/x">link</a></p></article>'
    "</section>"
)


@pytest.fixture
def link() -> Element:
    element = parse(_DOC).select_one("a")
    assert element is not None
    return element


def test_matches_self(link: Element) -> None:
    assert link.matches("a")
    assert link.matches('[href="/x"]')
    assert not link.matches("p")


def test_matches_considers_ancestors_and_siblings(link: Element) -> None:
    assert link.matches("section a")  # a section ancestor exists
    assert link.matches("article p > a")  # the full chain holds
    assert not link.matches("h2 a")  # no h2 ancestor


def test_matches_non_element_is_false() -> None:
    document = parse(_DOC)
    assert not document.matches("section")  # the Document node is not an element
    heading = document.select_one("h2")
    assert heading is not None
    assert not heading.children[0].matches("h2")  # a Text node never matches


def test_matches_child_combinator_on_standalone_element() -> None:
    assert not Element("a").matches("div > a")


def test_closest_returns_self_when_it_matches() -> None:
    article = parse(_DOC).select_one("article")
    assert article is not None
    closest = article.closest(".post")
    assert closest is not None
    assert closest.tag == "article"


def test_closest_walks_up_to_an_ancestor(link: Element) -> None:
    section = link.closest("section")
    assert section is not None
    assert section.tag == "section"
    article = link.closest(".post")
    assert article is not None
    assert article.tag == "article"


def test_closest_returns_none_when_nothing_matches(link: Element) -> None:
    assert link.closest("table") is None


def test_closest_from_a_text_node() -> None:
    paragraph = parse(_DOC).select_one("p.lead")
    assert paragraph is not None
    nearest = paragraph.children[0].closest("p")  # a Text node's nearest matching ancestor
    assert nearest is not None
    assert nearest.tag == "p"


@pytest.mark.parametrize("method", [pytest.param("matches", id="matches"), pytest.param("closest", id="closest")])
def test_rejects_non_str(link: Element, method: str) -> None:
    with pytest.raises(TypeError):
        getattr(link, method)(123)


@pytest.mark.parametrize("method", [pytest.param("matches", id="matches"), pytest.param("closest", id="closest")])
def test_rejects_invalid_selector(link: Element, method: str) -> None:
    with pytest.raises(ValueError, match="selector"):
        getattr(link, method)("[")


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param('[id="unterminated]', id="plain"),
        # a string ending in a lone backslash (the escape yields U+FFFD) is still unterminated
        pytest.param('[id="x\\', id="trailing-backslash"),
        # a backslash before a bare CR at end of input is a line continuation with nothing after it
        pytest.param('[id="x\\\r', id="trailing-cr-continuation"),
    ],
)
def test_rejects_unterminated_attribute_string(link: Element, selector: str) -> None:
    with pytest.raises(ValueError, match="selector"):
        link.matches(selector)
