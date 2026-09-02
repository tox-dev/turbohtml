from __future__ import annotations

import pytest

import turbohtml
from turbohtml._html import (
    _query_add_class,
    _query_attr,
    _query_has_class,
    _query_remove_class,
    _query_siblings,
    _query_text,
    _query_toggle_class,
    _query_unique,
)
from turbohtml.build import E
from turbohtml.query import Query


def test_unique_keeps_the_first_of_each_node() -> None:
    document = turbohtml.parse("<p>a</p><p>b</p>")
    first, second = document.find_all("p")
    assert _query_unique([first, second, first]) == [first, second]


def test_unique_reads_any_iterable() -> None:
    document = turbohtml.parse("<p>a</p><p>b</p>")
    assert len(_query_unique(iter(document.find_all("p")))) == 2


@pytest.mark.parametrize(
    "source",
    [pytest.param(5, id="not-iterable"), pytest.param(["text"], id="not-an-element")],
)
def test_unique_rejects_what_is_not_a_set_of_elements(source: object) -> None:
    with pytest.raises(TypeError):
        _query_unique(source)  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_unique_propagates_what_the_iterable_raises() -> None:
    def refusing() -> object:
        yield from ()
        msg = "no more elements"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="no more elements"):
        _query_unique(refusing())  # ty: ignore[invalid-argument-type]  # the iterable raises, which is the point


def test_siblings_skips_the_element_itself() -> None:
    document = turbohtml.parse("<ul><li id=a>a</li><li id=b>b</li>text<li id=c>c</li></ul>")
    first = document.select_one("#a")
    assert first is not None
    assert [node.attrs["id"] for node in _query_siblings([first])] == ["b", "c"]


def test_siblings_of_a_detached_element_is_empty() -> None:
    assert _query_siblings([E.div()]) == []


def test_siblings_of_the_root_is_empty() -> None:
    document = turbohtml.parse("<p>a</p>")
    root = document.root
    assert root is not None
    assert _query_siblings([root]) == []


def test_siblings_deduplicates_across_the_set() -> None:
    document = turbohtml.parse("<ul><li id=a>a</li><li id=b>b</li></ul>")
    both = document.find_all("li")
    assert [node.attrs["id"] for node in _query_siblings(both)] == ["b", "a"]


@pytest.mark.parametrize(
    ("call", "args"),
    [
        pytest.param(_query_siblings, ("notalist",), id="siblings"),
        pytest.param(_query_text, ("notalist",), id="text"),
        pytest.param(_query_attr, ("notalist", "id"), id="attr"),
        pytest.param(_query_has_class, ("notalist", "x"), id="has_class"),
        pytest.param(_query_add_class, ("notalist", "x"), id="add_class"),
        pytest.param(_query_remove_class, ("notalist", "x"), id="remove_class"),
        pytest.param(_query_toggle_class, ("notalist", "x"), id="toggle_class"),
    ],
)
def test_a_binding_rejects_a_non_list(call: object, args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        call(*args)  # ty: ignore[call-non-callable]  # the argument check is the point


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
def test_a_class_edit(markup: str, edit: object, expected: list[str]) -> None:
    document = turbohtml.parse(markup)
    elements = document.find_all("p")
    edit(elements, "new")  # ty: ignore[call-non-callable]  # the parametrized binding
    kept = document.select_one("p")
    assert kept is not None
    assert kept.attrs.get("class", []) == expected


def test_the_query_facade_still_composes() -> None:
    query = Query("<ul><li class='a'>one</li><li>two</li></ul>")("li")
    assert query.text() == "one two"
    assert query.add_class("z").has_class("z") is True
    assert query.attr("class") == "a z"
    assert query.remove_class("a").attr("class") == "z"
