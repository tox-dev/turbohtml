"""The per-tag converter hook of Node.to_markdown().

A registered tag's built-in rendering is replaced by a callable that receives the
element and its already-converted child Markdown and returns the element's
Markdown. The cases pin the splice behavior (inline vs block framing, dropping,
unwrapping, custom elements) and the binding's argument handling and errors.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest

from turbohtml import Element, Markdown, parse

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pytest_mock import MockerFixture

    Converter = Callable[[Element, str], str]


def wrap(marker: str) -> Converter:
    """A converter that surrounds the rendered child Markdown with a marker."""
    return lambda _element, content: f"{marker}{content}{marker}"


@pytest.mark.parametrize(
    ("html", "tag", "converter", "expected"),
    [
        pytest.param(
            "<p>see <a href='https://x.test'>the site</a> now</p>",
            "a",
            lambda _el, text: f"[{text}]",
            "see [the site] now",
            id="inline-wrap",
        ),
        pytest.param("<p>a<b>x</b>b</p>", "b", wrap("=="), "a==x==b", id="inline-marker"),
        pytest.param("<p>a<span>x</span>b</p>", "span", lambda _e, _t: "", "ab", id="inline-drop"),
        pytest.param("<p>a<u>keep</u>b</p>", "u", lambda _e, text: text, "akeepb", id="inline-unwrap"),
        pytest.param("<p>only <i>italic</i></p>", "i", wrap("/"), "only /italic/", id="inline-trailing"),
    ],
)
def test_inline_converter(html: str, tag: str, converter: Converter, expected: str) -> None:
    assert parse(html).to_markdown(Markdown(converters={tag: converter})) == expected


def test_inner_trailing_break_is_trimmed() -> None:
    # the <br> leaves a trailing "  \n" in the rendered child Markdown that the hook strips
    out = parse("<p>go <a href='https://x.test'>x<br></a> on</p>").to_markdown(Markdown(converters={"a": wrap("|")}))
    assert out == "go |x| on"


def test_inner_all_whitespace_trims_to_empty() -> None:
    # a child that renders to only a break trims away entirely, so the hook sees ""
    out = parse("<p>a<i><br></i>b</p>").to_markdown(Markdown(converters={"i": lambda _e, content: f"[{content}]"}))
    assert out == "a[]b"


def test_custom_element_with_attribute() -> None:
    html = "<p>play <video src='m.mp4'>fallback</video> here</p>"
    out = parse(html).to_markdown(Markdown(converters={"video": lambda el, _t: f"[{el.attrs['src']}]"}))
    assert out == "play [m.mp4] here"


def test_converter_on_foreign_element() -> None:
    # a non-HTML (SVG) element matches by tag name and flows inline, never as a block
    out = parse("<p>see <svg><title>chart</title></svg> now</p>").to_markdown(Markdown(converters={"title": wrap("@")}))
    assert out == "see @chart@ now"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<section><p>a</p><div>x</div><p>b</p></section>",
            "a\n\n<<x>>\n\nb",
            id="block-between-paragraphs",
        ),
        pytest.param("<ul><li>one<div>x</div></li></ul>", "- one\n\n  <<x>>", id="block-in-list-item"),
        pytest.param(
            "<blockquote><div>x</div></blockquote>",
            "> <<x>>",
            id="block-in-blockquote",
        ),
    ],
)
def test_block_converter(html: str, expected: str) -> None:
    assert parse(html).to_markdown(Markdown(converters={"div": lambda _e, text: f"<<{text}>>"})) == expected


def test_block_converter_multiline_keeps_prefix() -> None:
    out = parse("<ul><li>one<div>x</div></li></ul>").to_markdown(
        Markdown(converters={"div": lambda _e, _t: "line1\nline2"}),
    )
    assert out == "- one\n\n  line1\n  line2"


def test_empty_converter_result_emits_nothing() -> None:
    out = parse("<section><div>x</div></section>").to_markdown(Markdown(converters={"div": lambda _e, _t: ""}))
    assert not out


def test_converter_on_root_element() -> None:
    section = parse("<section>hi <b>there</b></section>").find("section")
    assert section is not None
    out = section.to_markdown(Markdown(converters={"section": lambda _e, text: f"S[{text}]"}))
    assert out == "S[hi **there**]"


def test_reference_link_inside_converter_registers() -> None:
    html = "<div><a href='https://e.test'>e</a></div>"
    out = parse(html).to_markdown(Markdown(links=Markdown.Links(style="reference"), converters={"div": wrap("|")}))
    assert out == "|[e][1]|\n\n[1]: https://e.test"


def test_converter_receives_element_and_content(mocker: MockerFixture) -> None:
    converter = mocker.MagicMock(return_value="X")
    out = parse("<p><b>hi <i>there</i></b></p>").to_markdown(Markdown(converters={"b": converter}))
    assert out == "X"
    converter.assert_called_once()
    element, content = converter.call_args.args
    assert isinstance(element, Element)
    assert element.tag == "b"
    assert content == "hi *there*"


def test_unregistered_tag_renders_normally() -> None:
    out = parse("<p><b>x</b><i>y</i></p>").to_markdown(Markdown(converters={"b": wrap("@")}))
    assert out == "@x@*y*"


def test_content_node_passes_through_with_converters() -> None:
    out = parse("<p>a<template>b</template>c</p>").to_markdown(Markdown(converters={"unused": wrap("@")}))
    assert out == "abc"


@pytest.mark.parametrize(
    "converters",
    [
        pytest.param({}, id="empty-dict"),
        pytest.param(None, id="none"),
        pytest.param(MappingProxyType({}), id="empty-mapping"),
    ],
)
def test_no_op_converters_match_default(converters: Mapping[str, Converter] | None) -> None:
    html = "<p><b>x</b></p>"
    assert parse(html).to_markdown(Markdown(converters=converters)) == parse(html).to_markdown()


def test_non_dict_mapping_is_accepted() -> None:
    out = parse("<p><b>x</b></p>").to_markdown(Markdown(converters=MappingProxyType({"b": wrap("__")})))
    assert out == "__x__"


def test_non_str_return_raises_type_error() -> None:
    def convert(_element: Element, _content: str) -> str:
        return 123  # ty: ignore[invalid-return-type]  # a non-str on purpose, to exercise the runtime check

    with pytest.raises(TypeError, match=r"converter for <b> must return a str, not int"):
        parse("<p><b>x</b></p>").to_markdown(Markdown(converters={"b": convert}))


def test_converter_exception_propagates() -> None:
    def boom(_element: Element, _content: str) -> str:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        parse("<p><b>x</b></p>").to_markdown(Markdown(converters={"b": boom}))


def test_non_callable_value_raises() -> None:
    with pytest.raises(TypeError):
        # a non-callable value on purpose, to exercise the runtime call failure
        parse("<p><b>x</b></p>").to_markdown(
            Markdown(converters={"b": "not callable"}),  # ty: ignore[invalid-argument-type]
        )


def test_non_mapping_argument_raises() -> None:
    with pytest.raises((TypeError, AttributeError)):
        # a non-mapping on purpose, to exercise the binding's argument coercion
        parse("<p><b>x</b></p>").to_markdown(Markdown(converters=42))  # ty: ignore[invalid-argument-type]
