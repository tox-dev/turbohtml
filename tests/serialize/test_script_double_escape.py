from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import CData, Comment, Document, DocumentFragment, Element, Html, Indent, Minify, Range, Text, parse
from turbohtml.clean import JSMinify

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node

_WRITERS: Final[list[pytest.ParameterSet]] = [
    pytest.param(lambda node: node.serialize(), id="serialize"),
    pytest.param(lambda node: "".join(node.serialize_iter()), id="serialize_iter"),
    pytest.param(lambda node: node.encode().decode(), id="encode"),
    pytest.param(lambda node: node.serialize(Html(layout=Indent(2))), id="indent"),
    pytest.param(lambda node: node.serialize(Html(layout=Minify(strip_comments=False))), id="minify"),
    pytest.param(lambda node: node.to_source(), id="to_source"),
]


# each ends inside a script that tree construction puts ahead of the earlier comment or style holding the </script>
_INPUT_ENDED: Final[list[pytest.ParameterSet]] = [
    pytest.param("<head></head><!--</script><img>--><script><!--<script>", id="after-head"),
    pytest.param("</body><!--</script><img>--><script><!--<SCRIPT ", id="after-body"),
    pytest.param("<table><!--</script><img>--><span><script><!-- --><!--<script/", id="fostered-comment"),
    pytest.param("<table><style></script><img></style><span><script><!--<script>", id="fostered-style"),
]


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize("markup", _INPUT_ENDED)
def test_script_the_input_ended_inside_a_double_escape_is_closed(markup: str, write: Callable[[Node], str]) -> None:
    # left open, the script would end at the comment's or the style's </script> and free the img
    output: Final[str] = write(parse(markup))
    assert "</script></script>" in output
    reparsed: Final[Document] = parse(output)
    assert reparsed.select("img") == []
    assert reparsed.serialize() == parse(reparsed.serialize()).serialize()


@pytest.mark.parametrize("markup", _INPUT_ENDED)
def test_to_source_closes_a_script_the_source_left_open_in_a_double_escape(markup: str) -> None:
    # to_source closes a script the source left open; one </script> alone would only end the double escape
    doc: Final[Document] = parse(markup, source_locations=True)
    output: Final[str] = doc.to_source()
    assert "</script></script>" in output
    assert parse(output).select("img") == []


def test_to_source_doubles_the_end_tag_the_source_wrote() -> None:
    doc: Final[Document] = parse("<script>a</script><!--</script><img>-->", source_locations=True)
    script: Final[Element | None] = doc.select_one("script")
    assert script is not None
    script.text = "<!--<script>"
    output: Final[str] = doc.to_source()
    assert "<script><!--<script></script></script>" in output
    assert parse(output).select("img") == []


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("<!--<script>", id="start-tag"),
        pytest.param("a<b<!--x<ScRiPt\t", id="any-case"),
        pytest.param("var answer = 42; // <!--<script>", id="after-plain-text"),
        pytest.param("<!--<script\r", id="carriage-return"),
        pytest.param("<!--<script/><script>", id="second-start-tag"),
        pytest.param("<!--<b>bold</b><script>", id="after-other-tags"),
        pytest.param("<!--<script>--x>", id="dashes-apart-from-gt"),
        pytest.param("<!--<script>->", id="one-dash"),
        pytest.param("<!--<script><", id="trailing-lt"),
        pytest.param("<!--<script></", id="trailing-end-tag-open"),
        pytest.param("<!--<script></script", id="end-tag-name-unterminated"),
        pytest.param("<!--<script></scriptx>", id="other-end-tag"),
        pytest.param("<!--<script></script><script>", id="entered-again"),
    ],
)
def test_script_text_ending_double_escaped_takes_two_end_tags(text: str, write: Callable[[Node], str]) -> None:
    # a raw-text CR is written as LF, which a re-parse reads back as the same whitespace
    written: Final[str] = text.replace("\r", "\n")
    assert write(Element("script", children=[Text(text)])) == f"<script>{written}</script></script>"


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("a<b", id="no-escape"),
        pytest.param("var answer = 42; // <script>", id="plain-text"),
        pytest.param("<script>", id="start-tag-unescaped"),
        pytest.param("<!-<script>", id="escape-not-started"),
        pytest.param("<!--<b>", id="escaped-only"),
        pytest.param("<!--<script", id="start-tag-name-unterminated"),
        pytest.param("<!--<scriptx>", id="other-start-tag"),
        pytest.param("<!--<script-->", id="start-tag-name-not-ended"),
        pytest.param("<!--><script>", id="escape-ended-at-once"),
        pytest.param("<!--<script>-->", id="escape-ended"),
        pytest.param("<!--<script>--->", id="escape-ended-after-three-dashes"),
        pytest.param("<!--<script></script>", id="double-escape-ended"),
        pytest.param("<!--<script></SCRIPT\f", id="double-escape-ended-any-case"),
    ],
)
def test_script_text_not_ending_double_escaped_takes_one_end_tag(text: str, write: Callable[[Node], str]) -> None:
    assert write(Element("script", children=[Text(text)])) == f"<script>{text}</script>"


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize(
    ("children", "expected"),
    [
        pytest.param([], "<script></script>", id="empty"),
        pytest.param([Text("<!--<b>")], "<script><!--<b></script>", id="one-end-tag"),
        pytest.param([Text("<!--<script>")], "<script><!--<script></script></script>", id="two-end-tags"),
        pytest.param([CData("<!--<script>")], "<script><!--<script></script></script>", id="cdata"),
    ],
)
def test_script_written_again_gets_the_same_end_tags(
    children: list[Node], expected: str, write: Callable[[Node], str]
) -> None:
    script: Final[Element] = Element("script", children=children)
    assert [write(script), write(script)] == [expected, expected]


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<script></script>", id="empty"),
        pytest.param("<script><!--<script></script>-->x</script>", id="double-escape-left"),
        pytest.param("<style><!--<script></style>", id="style"),
    ],
)
def test_parsed_raw_text_closed_by_its_end_tag_keeps_one(markup: str, write: Callable[[Node], str]) -> None:
    assert markup in write(parse(markup))


def _set_data(text: Text) -> None:
    text.data = "<!--<script>"


def _delete_range(text: Text) -> None:
    edit: Final[Range] = Range(text, 8)
    edit.set_end(text, 9)
    edit.delete_contents()


def _append_and_normalize(text: Text) -> None:
    script: Final[Element | Document | DocumentFragment | None] = text.parent
    assert isinstance(script, Element)
    script.append(Text("<script>"))
    script.normalize()


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize(
    ("before", "edit"),
    [
        pytest.param("<!--<b>", _set_data, id="set-data"),
        pytest.param("<!--<scrXipt>", _delete_range, id="range-delete"),
        pytest.param("<!--", _append_and_normalize, id="normalize"),
    ],
)
def test_script_text_edited_after_a_write_is_read_again(
    before: str, edit: Callable[[Text], None], write: Callable[[Node], str]
) -> None:
    # the first write remembers that the text needs one end tag; the edit makes it need two
    text: Final[Text] = Text(before)
    script: Final[Element] = Element("script", children=[text])
    assert write(script) == f"<script>{before}</script>"
    edit(text)
    assert write(script) == "<script><!--<script></script></script>"


def test_script_text_split_across_nodes_is_read_as_one() -> None:
    script: Final[Element] = Element("script", children=[Text("<!"), Text("--<scr"), Text("ipt>")])
    assert script.serialize() == "<script><!--<script></script></script>"
    assert script.to_source() == "<script><!--<script></script></script>"


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize("tag", ["style", "xmp", "iframe", "noembed", "noframes"])
def test_other_raw_text_has_no_double_escape(tag: str, write: Callable[[Node], str]) -> None:
    assert write(Element(tag, children=[Text("<!--<script>")])) == f"<{tag}><!--<script></{tag}>"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<!--<script>\nvar  a = 1 ;", "<script>var a=1</script>", id="comment-dropped"),
        pytest.param(
            "var  a = '<!--<script>' ;", "<script>var  a = '<!--<script>' ;</script></script>", id="kept-verbatim"
        ),
    ],
)
def test_minified_script_is_judged_on_the_text_written(source: str, expected: str) -> None:
    script: Final[Element] = Element("script", children=[Text(source)])
    assert script.serialize(Html(layout=Minify(minify_js=JSMinify()))) == expected


@pytest.mark.parametrize("write", _WRITERS)
def test_script_with_a_markup_child_in_a_double_escape_is_closed(write: Callable[[Node], str]) -> None:
    script: Final[Element] = Element("script", children=[Text("<!--<script>"), Element("b")])
    reparsed: Final[Document] = parse(write(Element("div", children=[script, Comment("</script><img>")])))
    assert reparsed.select("img") == []


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize("data", [pytest.param(Text, id="text"), pytest.param(CData, id="cdata")])
def test_script_data_after_a_markup_child_is_read_with_it(
    data: Callable[[str], Node], write: Callable[[Node], str]
) -> None:
    script: Final[Element] = Element("script", children=[Element("b"), data("<!--<script>")])
    assert write(script) == "<script><b></b><!--<script></script></script>"


@pytest.mark.parametrize("write", _WRITERS)
@pytest.mark.parametrize(
    "nested",
    [
        pytest.param(Element("script", children=[Text("<!--<script>")]), id="text"),
        pytest.param(Element("script", children=[Text("<!--<script>"), Element("i")]), id="markup"),
    ],
)
def test_script_nested_in_a_raw_text_element_adds_no_end_tag(nested: Element, write: Callable[[Node], str]) -> None:
    outer: Final[Element] = Element("script", children=[Element("b", children=[nested]), Text("x<img src=x>")])
    output: Final[str] = write(outer)
    assert "</script></script>" not in output
    assert parse(output).select("img") == []


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param(
            Element("script", children=[Text("<!--<script>")]),
            "<html><head></head><body><plaintext>a<script><!--<script>",
            id="text",
        ),
        pytest.param(
            Element("script", children=[Text("<!--<script>"), Element("i")]),
            "<html><head></head><body><plaintext>a<script><!--<script><i>",
            id="markup",
        ),
    ],
)
def test_script_after_a_parsed_plaintext_gets_no_end_tag(script: Element, expected: str) -> None:
    doc: Final[Document] = parse("<plaintext>a", source_locations=True)
    body: Final[Element | None] = doc.select_one("body")
    assert body is not None
    body.append(script)
    assert doc.to_source() == expected
