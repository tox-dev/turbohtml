"""The DOM pre-insertion validity rules: a doctype lives only in a Document, which holds no Text, one element, and one
doctype placed before that element. Every insertion path rejects a violation before it moves anything."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Doctype, Document, Element, Range, Text, parse

if TYPE_CHECKING:
    from collections.abc import Callable

_PAGE = "<!DOCTYPE html><html><head></head><body><p>x</p></body></html>"


def _page() -> Document:
    return parse(_PAGE)


def _doctype(document: Document) -> Doctype:
    doctype = document.children[0]
    assert isinstance(doctype, Doctype)
    return doctype


def _root(document: Document) -> Element:
    root = document.root
    assert root is not None
    return root


def _paragraph(document: Document) -> Element:
    paragraph = document.select_one("p")
    assert paragraph is not None
    return paragraph


def _rootless() -> Document:
    document = _page()
    _root(document).extract()
    return document


def _doctypeless() -> Document:
    document = _page()
    _doctype(document).extract()
    return document


def _commented() -> Document:
    return parse("<!--a--><!--b--><html><head></head><body></body></html>")


def _comment(document: Document, index: int) -> Comment:
    comment = document.children[index]
    assert isinstance(comment, Comment)
    return comment


_REJECTED = [
    pytest.param(
        _commented,
        lambda doc: _comment(doc, 0).wrap_siblings(Element("div"), until=_comment(doc, 1)),
        "only one element",
        id="wrap-comment-run-beside-root",
    ),
    pytest.param(
        _rootless,
        lambda doc: _doctype(doc).insert_after(Comment("c"), _doctype(parse(_PAGE))),
        "only be a child of a Document",
        id="doctype-among-several-nodes-in-document",
    ),
    pytest.param(
        _page,
        lambda doc: _paragraph(doc).append(_doctype(parse(_PAGE))),
        "only be a child of a Document",
        id="doctype-under-element",
    ),
    pytest.param(
        _page,
        lambda doc: _paragraph(doc).insert_after(Comment("c"), _doctype(parse(_PAGE))),
        "only be a child of a Document",
        id="doctype-among-several-nodes",
    ),
    pytest.param(
        _page, lambda doc: _root(doc).insert_before(_doctype(parse(_PAGE))), "only one doctype", id="second-doctype"
    ),
    pytest.param(_page, lambda doc: _root(doc).insert_after(Element("div")), "only one element", id="second-element"),
    pytest.param(_page, lambda doc: _root(doc).insert_after(Text("t")), "cannot hold a Text", id="text-under-document"),
    pytest.param(
        _page,
        lambda doc: _doctype(doc).replace_with(Element("div")),
        "only one element",
        id="doctype-replaced-by-element",
    ),
    pytest.param(
        _page,
        lambda doc: _doctype(doc).insert_after(Element("div")),
        "only one element",
        id="element-between-doctype-and-root",
    ),
    pytest.param(
        _doctypeless, lambda doc: _root(doc).insert_before(Element("div")), "only one element", id="element-before-root"
    ),
    pytest.param(
        _doctypeless,
        lambda doc: _root(doc).insert_after(_doctype(parse(_PAGE))),
        "before its element",
        id="doctype-after-root",
    ),
    pytest.param(
        _rootless,
        lambda doc: _doctype(doc).insert_before(Element("html")),
        "after its doctype",
        id="element-before-doctype",
    ),
    pytest.param(
        _rootless,
        lambda doc: _doctype(doc).insert_after(Element("a"), Element("b")),
        "only one element",
        id="two-elements-at-once",
    ),
    pytest.param(_page, lambda doc: _doctype(doc).wrap(Element("div")), "only one element", id="wrap-doctype"),
    pytest.param(
        _rootless,
        lambda doc: _doctype(doc).wrap(Element("html")),
        "only be a child of a Document",
        id="wrap-moves-doctype-into-element",
    ),
    pytest.param(
        _rootless,
        lambda doc: _doctype(doc).wrap_siblings(Element("html")),
        "only be a child of a Document",
        id="wrap-siblings-moves-doctype",
    ),
    pytest.param(
        _page, lambda doc: Range(doc, 2).insert_node(Element("div")), "only one element", id="range-second-element"
    ),
    pytest.param(
        _page,
        lambda doc: Range(_paragraph(doc).children[0], 1).insert_node(_doctype(parse(_PAGE))),
        "only be a child of a Document",
        id="range-doctype-into-text",
    ),
]


@pytest.mark.parametrize(("build", "edit", "message"), _REJECTED)
def test_invalid_insertion_is_rejected(
    build: Callable[[], Document], edit: Callable[[Document], object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        edit(build())


@pytest.mark.parametrize(("build", "edit", "message"), _REJECTED)
def test_rejected_insertion_leaves_the_document_unchanged(
    build: Callable[[], Document], edit: Callable[[Document], object], message: str
) -> None:
    document = build()
    before = document.html
    with pytest.raises(ValueError, match=message):
        edit(document)
    assert document.html == before


@pytest.mark.parametrize(
    ("build", "edit", "expected"),
    [
        pytest.param(
            _page,
            lambda doc: _root(doc).wrap(Element("div")),
            "<!DOCTYPE html><div><html><head></head><body><p>x</p></body></html></div>",
            id="wrap-root",
        ),
        pytest.param(
            _page,
            lambda doc: _root(doc).wrap_siblings(Element("div")),
            "<!DOCTYPE html><div><html><head></head><body><p>x</p></body></html></div>",
            id="wrap-siblings-root",
        ),
        pytest.param(
            _page,
            lambda doc: _root(doc).replace_with(Element("main")),
            "<!DOCTYPE html><main></main>",
            id="element-for-element",
        ),
        pytest.param(
            _page,
            lambda doc: _root(doc).replace_with(_root(doc), Comment("c")),
            "<!DOCTYPE html><!--c--><html><head></head><body><p>x</p></body></html>",
            id="replace-keeping-self",
        ),
        pytest.param(
            _page,
            lambda doc: _root(doc).insert_after(Comment("a"), Comment("b")),
            "<!DOCTYPE html><html><head></head><body><p>x</p></body></html><!--a--><!--b-->",
            id="comments-after-root",
        ),
        pytest.param(
            _doctypeless, lambda doc: _root(doc).insert_before(_doctype(parse(_PAGE))), _PAGE, id="doctype-before-root"
        ),
        pytest.param(
            _rootless,
            lambda doc: _doctype(doc).insert_after(Element("html")),
            "<!DOCTYPE html><html></html>",
            id="element-after-doctype",
        ),
        pytest.param(
            _page,
            lambda doc: Range(doc, 1).insert_node(Comment("c")),
            "<!DOCTYPE html><!--c--><html><head></head><body><p>x</p></body></html>",
            id="range-comment",
        ),
    ],
)
def test_valid_insertion_is_applied(
    build: Callable[[], Document], edit: Callable[[Document], object], expected: str
) -> None:
    document = build()
    edit(document)
    assert document.html == expected
