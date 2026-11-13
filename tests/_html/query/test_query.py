from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pytest

import turbohtml
from turbohtml import Element
from turbohtml._html import (
    query_add_class as _query_add_class,
)
from turbohtml._html import (
    query_attr as _query_attr,
)
from turbohtml._html import (
    query_children as _query_children,
)
from turbohtml._html import (
    query_closest as _query_closest,
)
from turbohtml._html import (
    query_has_class as _query_has_class,
)
from turbohtml._html import (
    query_parents as _query_parents,
)
from turbohtml._html import (
    query_remove_class as _query_remove_class,
)
from turbohtml._html import (
    query_siblings as _query_siblings,
)
from turbohtml._html import (
    query_text as _query_text,
)
from turbohtml._html import (
    query_toggle_class as _query_toggle_class,
)
from turbohtml._html import (
    query_unique as _query_unique,
)
from turbohtml._html import (
    select_limited as _select_limited,
)
from turbohtml.build import E

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from turbohtml import Document


def test_unique_keeps_the_first_of_each_node() -> None:
    first, second = turbohtml.parse("<p>a</p><p>b</p>").find_all("p")
    assert _query_unique([first, second, first]) == [first, second]


def test_unique_reads_any_iterable() -> None:
    document = turbohtml.parse("<p>a</p><p>b</p>")
    assert len(_query_unique(iter(document.find_all("p")))) == 2


@pytest.mark.parametrize(
    "source",
    [pytest.param(5, id="not-iterable"), pytest.param(["text"], id="not-an-element")],
)
def test_unique_rejects_what_is_not_a_set_of_elements(source: int | list[str]) -> None:
    with pytest.raises(TypeError):
        _query_unique(source)  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_unique_propagates_what_the_iterable_raises() -> None:
    def refusing() -> Iterator[Element]:
        yield from ()
        msg = "no more elements"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="no more elements"):
        _query_unique(refusing())


def test_siblings_skips_the_element_itself() -> None:
    first = turbohtml.parse("<ul><li id=a>a</li><li id=b>b</li>text<li id=c>c</li></ul>").select_one("#a")
    assert first is not None
    assert [node.attrs["id"] for node in _query_siblings([first])] == ["b", "c"]


def test_siblings_of_a_detached_element_is_empty() -> None:
    assert _query_siblings([E.div()]) == []


def test_siblings_of_the_root_is_empty() -> None:
    root = turbohtml.parse("<p>a</p>").root
    assert root is not None
    assert _query_siblings([root]) == []


def test_siblings_deduplicates_across_the_set() -> None:
    both = turbohtml.parse("<ul><li id=a>a</li><li id=b>b</li></ul>").find_all("li")
    assert [node.attrs["id"] for node in _query_siblings(both)] == ["b", "a"]


@pytest.mark.parametrize(
    ("call", "args"),
    [
        pytest.param(partial(_query_siblings), ("notalist",), id="siblings"),
        pytest.param(partial(_query_text), ("notalist",), id="text"),
        pytest.param(partial(_query_attr), ("notalist", "id"), id="attr"),
        pytest.param(partial(_query_has_class), ("notalist", "x"), id="has_class"),
        pytest.param(partial(_query_add_class), ("notalist", "x"), id="add_class"),
        pytest.param(partial(_query_remove_class), ("notalist", "x"), id="remove_class"),
        pytest.param(partial(_query_toggle_class), ("notalist", "x"), id="toggle_class"),
    ],
)
def test_a_binding_rejects_a_non_list(call: partial[list[Element] | None], args: tuple[str] | tuple[str, str]) -> None:
    with pytest.raises(TypeError):
        call(*args)  # the argument check is the point


def test_siblings_rejects_a_non_element() -> None:
    with pytest.raises(TypeError):
        _query_siblings(["text"])  # ty: ignore[invalid-argument-type]  # the element check is the point


def test_text_joins_the_set_with_a_space() -> None:
    document = turbohtml.parse("<p>one</p><p>two</p>")
    assert _query_text(document.find_all("p")) == "one two"


def test_text_of_an_empty_set_is_empty() -> None:
    assert not _query_text([])


def test_attr_of_an_empty_set_is_none() -> None:
    assert _query_attr([], "id") is None


def test_attr_reads_a_plain_value() -> None:
    document = turbohtml.parse("<p id=first>a</p>")
    assert _query_attr(document.find_all("p"), "id") == "first"


def test_attr_joins_a_tokenized_value() -> None:
    document = turbohtml.parse("<p class='a b'>x</p>")
    assert _query_attr(document.find_all("p"), "class") == "a b"


def test_attr_of_an_absent_name_is_none() -> None:
    document = turbohtml.parse("<p>x</p>")
    assert _query_attr(document.find_all("p"), "title") is None


def test_has_class_finds_it_on_any_element() -> None:
    document = turbohtml.parse("<p>a</p><p class='x'>b</p>")
    assert _query_has_class(document.find_all("p"), "x") is True


def test_has_class_answers_false_when_absent() -> None:
    document = turbohtml.parse("<p>a</p><p class='y'>b</p>")
    assert _query_has_class(document.find_all("p"), "x") is False


@pytest.mark.parametrize(
    ("markup", "edit", "expected"),
    [
        pytest.param("<p>x</p>", _query_add_class, ["new"], id="add-to-an-element-with-no-class"),
        pytest.param("<p class='a'>x</p>", _query_add_class, ["a", "new"], id="add-beside-an-existing-class"),
        pytest.param("<p class='new'>x</p>", _query_add_class, ["new"], id="add-what-is-already-there"),
        pytest.param("<p class='a new b'>x</p>", _query_remove_class, ["a", "b"], id="remove-keeps-the-others"),
        pytest.param("<p class='a'>x</p>", _query_remove_class, ["a"], id="remove-what-is-absent"),
        pytest.param("<p class='a'>x</p>", _query_toggle_class, ["a", "new"], id="toggle-on"),
        pytest.param("<p class='a new'>x</p>", _query_toggle_class, ["a"], id="toggle-off"),
    ],
)
def test_a_class_edit(markup: str, edit: Callable[[list[Element], str], None], expected: list[str]) -> None:
    document = turbohtml.parse(markup)
    edit(document.find_all("p"), "new")
    kept = document.select_one("p")
    assert kept is not None
    assert kept.attrs.get("class", []) == expected


def test_parents_deduplicates_a_shared_parent_and_skips_the_root() -> None:
    document = turbohtml.parse("<div><p>a</p><p>b</p></div>")
    first, second = document.find_all("p")
    html = document.find("html")
    assert html is not None
    assert _query_parents([first, second, html]) == [first.parent]


def test_the_document_has_no_parent() -> None:
    assert _query_parents([turbohtml.parse("<p>a</p>")]) == []


def test_children_keeps_only_element_children_in_order() -> None:
    document = turbohtml.parse("<div>x<p>a</p>y<span>b</span></div><section><i>c</i></section>")
    div, section = document.find("div"), document.find("section")
    assert div is not None
    assert section is not None
    assert [child.tag for child in _query_children([div, section])] == ["p", "span", "i"]


def test_children_of_a_document_is_its_root() -> None:
    document = turbohtml.parse("<p>a</p>")
    assert [child.tag for child in _query_children([document])] == ["html"]


def test_closest_deduplicates_a_shared_ancestor_and_drops_a_miss() -> None:
    paragraphs = turbohtml.parse("<div class=box><p>a</p><p>b</p></div><p>c</p>").find_all("p")
    assert _query_closest(paragraphs, ".box") == [paragraphs[0].parent]


def test_closest_rejects_a_bad_selector() -> None:
    with pytest.raises(Exception, match="selector"):
        _query_closest(turbohtml.parse("<p>a</p>").find_all("p"), "p[")


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        pytest.param(0, ["a", "b", "c"], id="zero-is-all"),
        pytest.param(-1, ["a", "b", "c"], id="negative-is-all"),
        pytest.param(2, ["a", "b"], id="two"),
        pytest.param(5, ["a", "b", "c"], id="more-than-there-are"),
    ],
)
def test_select_limited_stops_after_the_limit(limit: int, expected: list[str]) -> None:
    document = turbohtml.parse("<p>a</p><div><p>b</p></div><p>c</p>")
    assert [node.text for node in _select_limited(document, "p", limit)] == expected


def test_select_limited_stops_early_on_an_indexed_tree() -> None:
    document = turbohtml.parse("".join(f"<p>{index}</p>" for index in range(2000)))
    assert [node.text for node in _select_limited(document, "p", 1)] == ["0"]


@pytest.mark.parametrize(
    ("entry", "args"),
    [
        pytest.param(partial(_query_parents), (["p"],), id="parents-of-a-non-node"),
        pytest.param(partial(_query_children), (["p"],), id="children-of-a-non-node"),
        pytest.param(partial(_query_closest), (["p"], "p"), id="closest-of-a-non-node"),
        pytest.param(partial(_select_limited), ("p", "p", 0), id="select-from-a-non-node"),
        pytest.param(partial(_query_parents), ("p",), id="parents-of-a-non-list"),
        pytest.param(partial(_query_children), ("p",), id="children-of-a-non-list"),
        pytest.param(partial(_query_closest), ("p", "p"), id="closest-of-a-non-list"),
        pytest.param(partial(_select_limited), (turbohtml.parse(""), 5, 0), id="select-with-a-non-str-selector"),
    ],
)
def test_the_entries_reject_a_non_node(
    entry: partial[list[Element]], args: tuple[list[str] | str | Document | int, ...]
) -> None:
    with pytest.raises(TypeError):
        entry(*args)
