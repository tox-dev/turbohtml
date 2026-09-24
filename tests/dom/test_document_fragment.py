"""DocumentFragment: a parentless container whose insertion moves its children into place and leaves it empty."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Document, DocumentFragment, Element, Range, Text, parse

if TYPE_CHECKING:
    from collections.abc import Callable


def _fragment(*tags: str) -> DocumentFragment:
    fragment = DocumentFragment()
    for tag in tags:
        fragment.append(Element(tag))
    return fragment


def _host_page() -> tuple[Document, Element]:
    document = parse("<div id=a><i></i></div>")
    target = document.select_one("#a")
    assert target is not None
    return document, target


def test_document_fragment_starts_empty() -> None:
    assert (DocumentFragment().html, repr(DocumentFragment())) == ("", "DocumentFragment()")


def test_document_fragment_takes_no_arguments() -> None:
    with pytest.raises(TypeError):
        DocumentFragment("x")  # ty: ignore[too-many-positional-arguments]


def test_shadow_root_is_a_document_fragment() -> None:
    root = Element("div").attach_shadow("open")
    assert (isinstance(root, DocumentFragment), repr(root)) == (True, "ShadowRoot()")


@pytest.mark.parametrize("extract", [pytest.param(False, id="clone"), pytest.param(True, id="extract")])
def test_range_contents_are_a_document_fragment(*, extract: bool) -> None:
    _, target = _host_page()
    boundary = Range(target, 0)
    boundary.set_end(target, 1)
    contents = boundary.extract_contents() if extract else boundary.clone_contents()
    assert (type(contents), contents.html) == (DocumentFragment, "<i></i>")


_INSERTIONS = [
    pytest.param(
        lambda target, fragment: target.append(fragment), '<div id="a"><i></i><b></b><u></u></div>', id="append"
    ),
    pytest.param(
        lambda target, fragment: target.extend([fragment, Element("s")]),
        '<div id="a"><i></i><b></b><u></u><s></s></div>',
        id="extend",
    ),
    pytest.param(
        lambda target, fragment: target.insert(0, fragment), '<div id="a"><b></b><u></u><i></i></div>', id="insert"
    ),
    pytest.param(
        lambda target, fragment: target.children[0].insert_before(fragment),
        '<div id="a"><b></b><u></u><i></i></div>',
        id="insert-before",
    ),
    pytest.param(
        lambda target, fragment: target.children[0].insert_after(fragment),
        '<div id="a"><i></i><b></b><u></u></div>',
        id="insert-after",
    ),
    pytest.param(
        lambda target, fragment: target.children[0].replace_with(fragment),
        '<div id="a"><b></b><u></u></div>',
        id="replace-with",
    ),
    pytest.param(
        lambda target, fragment: Range(target, 1).insert_node(fragment),
        '<div id="a"><i></i><b></b><u></u></div>',
        id="range-insert-node",
    ),
]


@pytest.mark.parametrize(("insert", "expected"), _INSERTIONS)
def test_inserting_a_fragment_places_its_children(
    insert: Callable[[Element, DocumentFragment], object], expected: str
) -> None:
    _, target = _host_page()
    insert(target, _fragment("b", "u"))
    assert target.html == expected


@pytest.mark.parametrize(("insert", "expected"), _INSERTIONS)
def test_inserting_a_fragment_empties_it(insert: Callable[[Element, DocumentFragment], object], expected: str) -> None:
    _, target = _host_page()
    fragment = _fragment("b", "u")
    insert(target, fragment)
    assert (target.html, fragment.children) == (expected, ())


def test_inserting_a_same_tree_fragment_moves_its_children() -> None:
    _, target = _host_page()
    boundary = Range(target, 0)
    boundary.set_end(target, 1)
    fragment = boundary.extract_contents()
    target.append(Element("b"))
    target.append(fragment)
    assert (target.html, fragment.children) == ('<div id="a"><b></b><i></i></div>', ())


def test_element_constructor_places_a_fragment_s_children() -> None:
    assert Element("p", children=[_fragment("b", "u"), Text("t")]).html == "<p><b></b><u></u>t</p>"


def test_element_constructor_places_a_lone_fragment_s_children() -> None:
    assert Element("p", children=[_fragment("b", "u")]).html == "<p><b></b><u></u></p>"


def test_element_constructor_takes_a_tuple_of_one_child() -> None:
    assert Element("p", children=(Text("t"),)).html == "<p>t</p>"


def test_range_insert_of_a_fragment_extends_a_collapsed_range() -> None:
    _, target = _host_page()
    boundary = Range(target, 1)
    boundary.insert_node(_fragment("b", "u"))
    assert (boundary.start_offset, boundary.end_offset) == (1, 3)


def test_appending_a_shadow_root_moves_its_children() -> None:
    host = Element("div")
    root = host.attach_shadow("open")
    root.append(Element("span"))
    _, target = _host_page()
    target.append(root)
    assert (target.html, root.children, root.host == host) == ('<div id="a"><i></i><span></span></div>', (), True)


def test_appending_a_same_tree_shadow_root_keeps_it_attached() -> None:
    document, target = _host_page()
    root = target.attach_shadow("open")
    root.append(Comment("c"))
    body = document.select_one("body")
    assert body is not None
    body.append(root)
    assert (body.html, target.shadow_root == root) == ('<body><div id="a"><i></i></div><!--c--></body>', True)


def test_a_template_s_contents_move_out_and_the_template_keeps_its_fragment() -> None:
    document = parse("<template><b>x</b></template><div id=a></div>")
    template = document.select_one("template")
    target = document.select_one("#a")
    assert template is not None
    assert target is not None
    content = template.children[0]
    assert isinstance(content, DocumentFragment)
    target.append(content)
    assert (target.html, template.children, content.children) == ('<div id="a"><b>x</b></div>', (content,), ())


@pytest.mark.parametrize(
    "insert",
    [
        pytest.param(lambda fragment: fragment.append(fragment), id="into-itself"),
        pytest.param(lambda fragment: fragment.children[0].append(fragment), id="into-its-child"),
    ],
)
def test_a_fragment_cannot_go_into_itself(insert: Callable[[DocumentFragment], object]) -> None:
    fragment = _fragment("b")
    with pytest.raises(ValueError, match="own subtree"):
        insert(fragment)


def test_a_fragment_holding_two_elements_cannot_become_a_document_s_root() -> None:
    document = parse("<!DOCTYPE html><html></html>")
    root = document.root
    assert root is not None
    with pytest.raises(ValueError, match="only one element"):
        root.replace_with(_fragment("a", "b"))


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        pytest.param("x", "must be a node", id="non-node"),
        pytest.param(parse(""), "Document cannot be inserted", id="document"),
    ],
)
def test_sibling_insert_rejects_a_bad_argument_before_moving_anything(argument: object, message: str) -> None:
    _, target = _host_page()
    first = Element("b")
    with pytest.raises(TypeError, match=message):
        target.children[0].insert_before(first, argument)  # ty: ignore[invalid-argument-type]
    assert target.html == '<div id="a"><i></i></div>'
