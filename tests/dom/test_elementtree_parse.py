from __future__ import annotations

import pytest

from turbohtml.etree import document_fromstring, fragment_fromstring, fromstring, tostring


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("", "<html/>", id="empty"),
        pytest.param("<p>text</p>", "<html><body><p>text</p></body></html>", id="body"),
        pytest.param("<p>ab\u00a0é</p>", "<html><body><p>ab\u00a0é</p></body></html>", id="unicode-text"),
        pytest.param("<p>abé</p>", "<html><body><p>abé</p></body></html>", id="unicode-non-whitespace"),
        pytest.param("<title>title</title>", "<html><head><title>title</title></head></html>", id="head"),
        pytest.param("<frameset></frameset>", "<html><frameset/></html>", id="frameset"),
        pytest.param("<head><title>x</title>", "<html><head><title>x</title></head></html>", id="explicit-head-only"),
        pytest.param("<body><p>x</p><body><p>y</p>", "<html><body><p>x</p><p>y</p></body></html>", id="repeated-body"),
        pytest.param(
            '<p>x</p><head><meta name="x"><body><p>y</p>',
            '<html><head><meta name="x"/></head><body><p>x</p><p>y</p></body></html>',
            id="content-before-head",
        ),
        pytest.param(
            '<body><head><meta name="x"><p>article</p>',
            '<html><body><meta name="x"/><p>article</p></body></html>',
            id="head-after-body",
        ),
        pytest.param(
            '<head></head><body><p></p><p id="x"></p></p>',
            '<html><body><p/><p id="x"/></body></html>',
            id="preserve-explicit-empty-paragraphs",
        ),
        pytest.param(
            "<head>\n<div><b>nav</b></div>\n</head>\n<body>\n<p>article</p>",
            "<html><head>\n<div><b>nav</b></div></head><body>\n\n\n<p>article</p></body></html>",
            id="multiline-head-content",
        ),
        pytest.param(
            "<head><div>\n<b>nav</b><body><p>article</p>",
            "<html><body><div>\n<b>nav</b><p>article</p></div></body></html>",
            id="multiline-unclosed-head-child",
        ),
        pytest.param("<body class='page'></body>", '<html><body class="page"/></html>', id="empty-with-attributes"),
        pytest.param(
            "<p>one<!-- comment -->two</p></p>", "<html><body><p>onetwo</p></body></html>", id="implicit-paragraph"
        ),
        pytest.param(b"<p>caf\xc3\xa9</p>", "<html><body><p>café</p></body></html>", id="utf8"),
        pytest.param(
            '<head><li>navigation</li><meta name="author" content="Ada"></head><body><p>article</p></body>',
            '<html><head><li>navigation</li><meta name="author" content="Ada"/></head>'
            "<body><p>article</p></body></html>",
            id="misplaced-head-content",
        ),
        pytest.param(
            '<head><div><meta name="author" content="Ada"><body><p>article</p>',
            '<html><head><meta name="author" content="Ada"/></head><body><div><p>article</p></div></body></html>',
            id="unclosed-head-child",
        ),
        pytest.param(
            '<head><div><title>Title</title><link href="x"><base href="y"><body><p>article</p>',
            '<html><head><title>Title</title><link href="x"/><base href="y"/></head>'
            "<body><div><p>article</p></div></body></html>",
            id="nested-head-metadata",
        ),
        pytest.param(
            "<head><b><i>nav</b>tail</i></head><body><p>article</p>",
            "<html><head><b><i>nav</i></b><i>tail</i></head><body><p>article</p></body></html>",
            id="reconstructed-formatting",
        ),
    ],
)
def test_element_tree_document_parsing(markup: str | bytes, expected: str) -> None:
    assert tostring(document_fromstring(markup), encoding="unicode") == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("", "<span/>", id="empty"),
        pytest.param("  \n", "<span/>", id="whitespace-only"),
        pytest.param("\u00a0<p>text</p>\u00a0", "<p>text</p>\u00a0", id="unicode-whitespace"),
        pytest.param("é<p>text</p>", "<div>é<p>text</p></div>", id="unicode-leading-text"),
        pytest.param("<!doctype html><p>text</p>", "<html><body><p>text</p></body></html>", id="doctype"),
        pytest.param("<", "<span>&lt;</span>", id="incomplete-tag"),
        pytest.param("<frameset></frameset>", "<html><head/><frameset/></html>", id="frameset"),
        pytest.param("<p>text</p>", "<p>text</p>", id="single-element"),
        pytest.param("<p>text</p> \n", "<p>text</p> \n", id="trailing-whitespace"),
        pytest.param("text", "<span>text</span>", id="text"),
        pytest.param("<b>one</b><i>two</i>", "<span><b>one</b><i>two</i></span>", id="inline-siblings"),
        pytest.param("<p>one</p><p>two</p>", "<div><p>one</p><p>two</p></div>", id="block-siblings"),
        pytest.param("before<b>bold</b>after", "<span>before<b>bold</b>after</span>", id="mixed-text"),
        pytest.param(
            "<title>title</title><p>text</p>",
            "<html><head><title>title</title></head><body><p>text</p></body></html>",
            id="metadata",
        ),
        pytest.param(" \n<HTML><p>text</p>", "<html><body><p>text</p></body></html>", id="full-document"),
        pytest.param(b"<p>invalid \xff</p>", "<p>invalid �</p>", id="invalid-utf8"),
    ],
)
def test_element_tree_snippet_parsing(markup: str | bytes, expected: str) -> None:
    assert tostring(fromstring(markup), encoding="unicode") == expected


@pytest.mark.parametrize(
    ("markup", "parent", "expected"),
    [
        pytest.param("<p>text</p>tail", False, "<p>text</p>tail", id="detached-tail"),
        pytest.param("one<!-- comment -->two<b>bold</b>", True, "<div>onetwo<b>bold</b></div>", id="default-parent"),
        pytest.param("<i>one</i><b>two</b>", "section", "<section><i>one</i><b>two</b></section>", id="named-parent"),
    ],
)
def test_element_tree_fragment_parsing(markup: str, expected: str, *, parent: str | bool) -> None:
    assert tostring(fragment_fromstring(markup, create_parent=parent), encoding="unicode") == expected


@pytest.mark.parametrize(
    ("markup", "message"),
    [
        pytest.param("text", "No elements found", id="no-elements"),
        pytest.param("<i>one</i><b>two</b>", r"Multiple elements found \(2\)", id="multiple-elements"),
    ],
)
def test_element_tree_fragment_requires_one_element(markup: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        fragment_fromstring(markup)
