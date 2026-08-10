from __future__ import annotations

import copy

import pytest

from turbohtml import Comment, Document, Element, Html, Minify, ProcessingInstruction, parse, parse_fragment


@pytest.mark.parametrize(
    ("markup", "target", "data"),
    [
        pytest.param("<?one>", "one", "", id="empty-data"),
        pytest.param("<?one value?>", "one", "value", id="data"),
        pytest.param("<svg><?one value?></svg>", "one", "value", id="foreign-content"),
        pytest.param("<table><?one value?></table>", "one", "value", id="table"),
    ],
)
def test_html_parse_builds_processing_instruction(markup: str, target: str, data: str) -> None:
    node = _processing_instruction(markup)
    assert (node.target, node.data) == (target, data)


@pytest.mark.parametrize(
    ("markup", "parent_tag"),
    [
        pytest.param("<?pi><!doctype html>", None, id="initial"),
        pytest.param("<!doctype html><?pi><html>", None, id="before-html"),
        pytest.param("<html><?pi><head>", "html", id="before-head"),
        pytest.param("<head><?pi></head>", "head", id="in-head"),
        pytest.param("<head><noscript><?pi></noscript>", "noscript", id="in-head-noscript"),
        pytest.param("</head><?pi><body>", "html", id="after-head"),
        pytest.param("<frameset><?pi>", "frameset", id="in-frameset"),
        pytest.param("<frameset></frameset><?pi>", "html", id="after-frameset"),
        pytest.param("<body><?pi>", "body", id="in-body"),
        pytest.param("<table><?pi></table>", "table", id="in-table"),
        pytest.param("<table><colgroup><?pi></colgroup></table>", "colgroup", id="in-column-group"),
        pytest.param("<body></body><?pi>", "html", id="after-body"),
        pytest.param("<body></body></html><?pi>", None, id="after-after-body"),
        pytest.param("<frameset></frameset></html><?pi>", None, id="after-after-frameset"),
        pytest.param("<svg><?pi></svg>", "svg", id="foreign-content"),
    ],
)
def test_processing_instruction_uses_comment_insertion_location(markup: str, parent_tag: str | None) -> None:
    parent = _processing_instruction(markup).parent
    if parent_tag is None:
        assert isinstance(parent, Document)
    else:
        assert isinstance(parent, Element)
        assert parent.tag == parent_tag


@pytest.mark.parametrize("target", [pytest.param("xml", id="xml"), pytest.param("XmL-StYlEsHeEt", id="stylesheet")])
def test_reserved_xml_processing_instruction_target_stays_a_comment(target: str) -> None:
    node = next(node for node in parse(f"<?{target} value?>").descendants if isinstance(node, Comment))
    assert node.data == f"?{target} value?"


@pytest.mark.parametrize(
    ("markup", "error"),
    [
        pytest.param("<?1bad>", "invalid-first-character-of-processing-instruction-target", id="first-character"),
        pytest.param("<?bad.target>", "invalid-processing-instruction-target", id="target-character"),
        pytest.param("<?xml?>", "disallowed-processing-instruction-target", id="reserved-target"),
        pytest.param("<?", "eof-in-processing-instruction", id="open-eof"),
        pytest.param("<?pi", "eof-in-processing-instruction", id="target-eof"),
        pytest.param("<?pi data", "eof-in-processing-instruction", id="data-eof"),
        pytest.param("<?pi data?", "eof-in-processing-instruction", id="questionable-eof"),
    ],
)
def test_processing_instruction_parse_error(markup: str, error: str) -> None:
    assert [item.code for item in parse(markup).errors] == [error]


def test_processing_instruction_data_keeps_markup_and_null() -> None:
    document = parse("<body><?pi &amp;<tag\0?>")
    node = next(node for node in document.descendants if isinstance(node, ProcessingInstruction))
    assert node.data == "&amp;<tag\0"
    assert document.errors == []


def test_processing_instruction_in_fragment_and_template() -> None:
    fragment = parse_fragment("<template><?pi data></template><?tail>", "div")
    assert [(node.target, node.data) for node in fragment.descendants if isinstance(node, ProcessingInstruction)] == [
        ("pi", "data"),
        ("tail", ""),
    ]


def test_parsed_processing_instruction_clone_preserves_fields() -> None:
    node = _processing_instruction("<body><?pi old>")
    clone = copy.copy(node)
    assert (clone.target, clone.data) == ("pi", "old")


def test_parsed_processing_instruction_extract_preserves_fields() -> None:
    node = _processing_instruction("<body><?pi old>")
    host = Element("div")
    host.append(node.extract())
    assert (node.target, node.data, node.parent) == ("pi", "old", host)


def test_selectedcontent_clone_preserves_processing_instruction() -> None:
    selectedcontent = parse("<select><button><selectedcontent></button><option><?pi value?>").find("selectedcontent")
    assert isinstance(selectedcontent, Element)
    (node,) = selectedcontent.children
    assert isinstance(node, ProcessingInstruction)
    assert (node.target, node.data) == ("pi", "value")


def test_parsed_processing_instruction_serializes_and_minifies() -> None:
    document = parse("<body><?pi data?></body>")
    assert (document.html, document.serialize(Html(layout=Minify()))) == (
        "<html><head></head><body><?pi data></body></html>",
        "<body><?pi data>",
    )


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<head><?pi></head><body>x", id="head"),
        pytest.param("<body>x</body><?pi>", id="after-body"),
        pytest.param("<body>x</body></html><?pi>", id="after-html"),
    ],
)
def test_minifying_processing_instruction_preserves_insertion_location(markup: str) -> None:
    document = parse(markup)
    assert parse(document.serialize(Html(layout=Minify()))).equals(document)


def _processing_instruction(markup: str) -> ProcessingInstruction:
    return next(node for node in parse(markup).descendants if isinstance(node, ProcessingInstruction))
