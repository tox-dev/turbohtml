"""Each round-trip oracle passes turbohtml's output and flags a deliberately broken printer, reader or entry point."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING

import pytest
from fuzz.round_trip_oracles import (
    ORACLES,
    OutOfScopeError,
    css_semantics_check,
    fixpoint_check,
    html_check,
    markdown_check,
    selector_entry_check,
    span_check,
    style_check,
    xml_check,
    xpath_entry_check,
)

from turbohtml import Html, Node, SourceLocation
from turbohtml.clean import minify_css
from turbohtml.convert import css_to_xpath
from turbohtml.cssom import StyleDeclaration

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Element


def _xml(node: Node) -> str:
    return node.serialize(Html(xml=True))


def _settles_on_numbers(text: str) -> str:
    # the first pass tags the text, the second shortens its number and changes nothing else
    return text.replace("1.50", "1.5") if text.startswith("#") else f"#{text} 1.50"


def _reject(_text: str) -> str:
    msg = "rejected"
    raise ValueError(msg)


def _reject_own_output(text: str) -> str:
    return _reject(text) if text.startswith("#") else f"#{text}"


@pytest.mark.parametrize(
    "name",
    [pytest.param(name, id=name) for name in sorted(ORACLES) if name != "js-names"],
)
def test_round_trip_negative_controls_fire(name: str) -> None:
    controls = ORACLES[name].controls()
    assert (len(controls) > 0, all(controls.values())) == (True, True)


@pytest.mark.parametrize(
    ("numeric", "expected"),
    [
        pytest.param(True, None, id="numeric-settles-on-third-pass"),
        pytest.param(False, "not a fixpoint: digit vs end", id="strict"),
    ],
)
def test_fixpoint_numeric_exception(*, numeric: bool, expected: str | None) -> None:
    assert fixpoint_check("w", _settles_on_numbers, numeric=numeric) == expected


def test_fixpoint_holds_for_minify_css() -> None:
    assert fixpoint_check("a{color:#ff0000;margin:0px 0px}", minify_css, numeric=True) is None


def test_fixpoint_skips_an_input_the_printer_rejects() -> None:
    with pytest.raises(OutOfScopeError):
        fixpoint_check("w", _reject, numeric=True)


def test_fixpoint_flags_a_printer_rejecting_its_own_output() -> None:
    assert fixpoint_check("w", _reject_own_output, numeric=True) == "rejects its own output"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<p>a &amp; b <b title='x\"y'>c</b></p>", id="escapes"),
        pytest.param("<p><table><d><form>", id="quirks-re-nesting-keeps-content"),
    ],
)
def test_html_check_passes_the_serializer(markup: str) -> None:
    assert html_check(markup) is None


def test_html_check_flags_a_serializer_that_drops_content() -> None:
    assert html_check("<p>a<b>c</b></p>", lambda node: node.serialize().replace("<b>", "", 1)) == (
        "document reparse changed content"
    )


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<plaintext>x", id="plaintext"),
        pytest.param("<pre>&#13;x</pre>", id="carriage-return"),
        pytest.param("<a><table><a>x", id="nested-anchor"),
        pytest.param("<script><!--<script></script>x", id="script-double-escape"),
    ],
)
def test_html_check_skips_trees_the_standard_does_not_round_trip(markup: str) -> None:
    with pytest.raises(OutOfScopeError):
        html_check(markup)


def test_xml_check_passes_the_serializer() -> None:
    assert xml_check("<p a=1>x<svg><circle r=1></circle></svg>") is None


@pytest.mark.parametrize(
    ("markup", "serialize", "expected"),
    [
        pytest.param(
            "<p>a&amp;b</p>",
            lambda node: _xml(node).replace("&amp;", "&"),
            "parse_xml rejects the XML serialization",
            id="not-well-formed",
        ),
        pytest.param(
            "<p a=1>x</p>",
            lambda node: _xml(node).replace(' a="1"', ""),
            "xml re-read tree differs: < vs <",
            id="dropped-attribute",
        ),
    ],
)
def test_xml_check_flags_a_broken_serializer(markup: str, serialize: Callable[[Node], str], expected: str) -> None:
    assert xml_check(markup, serialize) == expected


def test_xml_check_waives_the_tree_for_content_xml_cannot_hold() -> None:
    assert xml_check("<!--a--b--><p a=1>x", lambda node: _xml(node).replace(' a="1"', "")) is None


def test_style_check_passes_the_declaration_printer() -> None:
    assert style_check("color : RED;; margin:0 auto !important; x: 'a;b'") is None


def test_style_check_flags_a_reader_that_changes_a_value() -> None:
    assert style_check("a:b", lambda text: StyleDeclaration.parse(text.replace(": ", ":x", 1))) == (
        "declarations differ after re-read"
    )


def test_markdown_check_passes_to_markdown() -> None:
    assert markdown_check("<h2>t</h2><p>*a* and <em>b</em></p><ul><li>c</li></ul>") is None


def test_markdown_check_flags_unescaped_text() -> None:
    assert markdown_check("<p>*a*</p>", lambda node: node.text) == "not a fixpoint: '<' vs letter"


def test_markdown_check_skips_a_block_inside_an_inline() -> None:
    with pytest.raises(OutOfScopeError):
        markdown_check("<del><pre>x</pre></del>")


def test_css_semantics_check_passes_minify_css() -> None:
    markup = "<style>p{color:#ff0000;margin:0px 1px}p{margin-top:0}div>p{z-index:01}</style><div><p>x</p></div>"
    assert css_semantics_check(markup) is None


@pytest.mark.parametrize(
    ("markup", "minify", "expected"),
    [
        pytest.param(
            "<style>p{color:red}</style><p>x",
            lambda css: css.replace("red", "#00f"),
            "computed color: value changed",
            id="value-changed",
        ),
        pytest.param(
            "<style>p{z-index:1.}</style><p>x",
            lambda css: css.replace("1.", "1"),
            "computed integer: invalid value became valid",
            id="invalid-became-valid",
        ),
        pytest.param(
            "<style>p{margin-top:1px}</style><p>x",
            lambda css: css.replace("1px", "1xx"),
            "computed length: valid value became invalid",
            id="valid-became-invalid",
        ),
        pytest.param(
            "<style>p{x-unknown:1}</style><p>x",
            lambda css: "" if "color:red" in css else css,
            "selector rule dropped",
            id="selector-dropped",
        ),
        pytest.param(
            "<style>p{x-unknown:1}</style><p>x",
            lambda css: css.replace("p{color", "p::{color"),
            "minified selector does not parse",
            id="selector-unparsable",
        ),
        pytest.param(
            "<style>div>b{x-unknown:1}</style><div><p><b>x</b></p></div>",
            lambda css: css.replace(">", " "),
            "selector match set differs",
            id="selector-rewritten",
        ),
    ],
)
def test_css_semantics_check_flags_a_broken_minifier(markup: str, minify: Callable[[str], str], expected: str) -> None:
    assert css_semantics_check(markup, minify) == expected


def test_css_semantics_check_skips_a_document_without_style() -> None:
    with pytest.raises(OutOfScopeError):
        css_semantics_check("<p>x")


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("//p", id="node-set"),
        pytest.param("count(//p) div 0", id="number"),
        pytest.param("0 div 0", id="nan"),
        pytest.param("string(//p)", id="string"),
        pytest.param("//p[", id="error"),
    ],
)
def test_xpath_entry_check_passes_the_entry_points(expression: str) -> None:
    assert xpath_entry_check(json.dumps({"html": "<p>a</p><p>b</p>", "xpath": expression})) is None


@pytest.mark.parametrize(
    ("expression", "check", "expected"),
    [
        pytest.param(
            "//p",
            partial(xpath_entry_check, iterate=lambda node, expression: list(node.xpath_iter(expression))[1:]),
            "list(xpath_iter()) != xpath()",
            id="iterator-drops-an-item",
        ),
        pytest.param(
            "//p",
            partial(xpath_entry_check, first=lambda node, expression: list(node.xpath_iter(expression))[-1]),
            "xpath_one() != first item",
            id="one-returns-the-last",
        ),
        pytest.param(
            "//p[",
            partial(xpath_entry_check, first=lambda _node, _expression: None),
            "entry points disagree on raising",
            id="one-swallows-the-error",
        ),
    ],
)
def test_xpath_entry_check_flags_a_broken_entry_point(
    expression: str, check: Callable[[str], str | None], expected: str
) -> None:
    assert check(json.dumps({"html": "<p>a</p><p>b</p>", "xpath": expression})) == expected


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("div > .a, p:first-child", id="translatable"),
        pytest.param(":dir(ltr)", id="untranslatable"),
    ],
)
def test_selector_entry_check_passes_the_entry_points(selector: str) -> None:
    case = {"html": "<!DOCTYPE html><div><p class=a dir=ltr>x</p><p>y</p></div>", "css": selector}
    assert selector_entry_check(json.dumps(case)) is None


def test_selector_entry_check_flags_a_broken_translation() -> None:
    case = json.dumps({"html": "<p>x</p>", "css": "p"})
    assert selector_entry_check(case, lambda selector: css_to_xpath(selector, prefix="child::")) == (
        "xpath(css_to_xpath()) != select()"
    )


def test_selector_entry_check_skips_an_invalid_selector() -> None:
    with pytest.raises(OutOfScopeError):
        selector_entry_check(json.dumps({"html": "<p>x</p>", "css": "p["}))


def test_span_check_passes_the_parser() -> None:
    assert span_check("<!DOCTYPE html><div class=a\r\nid=b>x</div>\r\n<p>y<img src=x><b>z</b>") is None


def _late_start(location: SourceLocation) -> SourceLocation:
    start = location.start_tag
    return location._replace(
        start_tag=start._replace(start_offset=start.start_offset + 1, start_col=start.start_col + 1)
    )


def _attribute_outside(location: SourceLocation) -> SourceLocation:
    return location._replace(attrs=dict.fromkeys(location.attrs, location.end_tag or location.start_tag))


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        pytest.param(_late_start, "start tag span does not slice <name ...>", id="start-one-late"),
        pytest.param(
            lambda location: location._replace(end_tag=location.start_tag),
            "end tag span does not slice </name> after the start tag",
            id="end-tag-at-the-start-tag",
        ),
        pytest.param(_attribute_outside, "attribute span outside its start tag or overlapping", id="attribute-outside"),
    ],
)
def test_span_check_flags_broken_spans(edit: Callable[[SourceLocation], SourceLocation], expected: str) -> None:
    def locate(element: Element) -> SourceLocation | None:
        return None if (location := element.source_location) is None else edit(location)

    assert span_check("<p class=a>x</p>", locate) == expected
