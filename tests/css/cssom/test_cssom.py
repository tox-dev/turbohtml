"""``turbohtml.cssom``: the CSS Object Model cascade and computed style (issue #546)."""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING, Final

import pytest
from bench.operations import INPUTS

from turbohtml import Element, Text, parse
from turbohtml._html import _css_declaration_index, _css_declaration_text
from turbohtml.build import E
from turbohtml.cssom import ComputedStyle, RuleList, StyleDeclaration, StyleRule, StyleSheet, computed_style

if TYPE_CHECKING:
    from types import ModuleType

    from turbohtml import Document


def _style(element_html: str, *, css: str = "", tag: str = "div") -> ComputedStyle:
    """Compute the style of the first matching element in a document carrying one stylesheet."""
    document = parse(f"<html><head><style>{css}</style></head><body>{element_html}</body></html>")
    element = document.select_one(tag)
    assert isinstance(element, Element)
    return computed_style(element)


def test_parse_declarations_keeps_source_order_value_and_important() -> None:
    declaration = StyleDeclaration.parse("color: red; margin: 0 auto !important")
    assert declaration.get("color") == "red"
    assert declaration.get("margin") == "0 auto"
    assert declaration.important("margin") is True
    assert declaration.important("color") is False


def test_parse_declarations_last_duplicate_wins() -> None:
    declaration = StyleDeclaration.parse("color: red; color: blue")
    assert declaration["color"] == "blue"
    assert declaration.properties() == ("color",)
    assert len(declaration) == 1


def test_declaration_missing_property() -> None:
    declaration = StyleDeclaration.parse("color: red")
    assert declaration.get("width") is None
    assert declaration.get("width", "auto") == "auto"
    assert declaration.important("width") is False
    assert "width" not in declaration
    assert "color" in declaration
    with pytest.raises(KeyError):
        _ = declaration["width"]


def test_declaration_iteration_and_text_and_repr() -> None:
    declaration = StyleDeclaration.parse("color: red; margin: 0 !important")
    assert list(declaration) == ["color", "margin"]
    assert declaration.text == "color: red; margin: 0 !important"
    assert repr(declaration) == "StyleDeclaration('color: red; margin: 0 !important')"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("color:red!important", True, id="no-space"),
        pytest.param("color: red ! important", True, id="spaces-around-bang"),
        pytest.param("font: important", False, id="bare-important-is-a-value"),
        pytest.param("color: red !", False, id="trailing-bang-only"),
        pytest.param('content: "important"', False, id="important-inside-string"),
    ],
)
def test_declaration_important_flag(text: str, expected: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    name = text.split(":", 1)[0].strip()
    assert StyleDeclaration.parse(text).important(name) is expected


def test_parse_declarations_skips_empty_and_colonless_pieces() -> None:
    declaration = StyleDeclaration.parse(";; color: red ; : orphan ; width: ; nonsense ; height: 3px")
    assert declaration.properties() == ("color", "height")


def test_parse_declarations_grows_past_initial_capacity() -> None:
    text = ";".join(f"--v{index}: {index}" for index in range(20))
    declaration = StyleDeclaration.parse(text)
    assert len(declaration) == 20


def test_parse_declarations_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        StyleDeclaration.parse(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the C guard


def test_comment_stripping_across_selector_value_and_string() -> None:
    sheet = StyleSheet('di/**/v { colo/**/r: red; content: "/*keep*/" }')
    rule = sheet.rules[0]
    assert rule.selector_text == "di v"
    assert rule.style.get("colo r") == "red"
    assert rule.style.get("content") == '"/*keep*/"'


def test_comment_stripping_tolerates_unterminated_comment_and_string() -> None:
    assert StyleSheet("a { color: red /* trailing").rules[0].style.get("color") == "red"
    assert StyleDeclaration.parse('content: "open').get("content") == '"open'


def test_stylesheet_skips_at_rules() -> None:
    css = '@charset "utf-8"; @import url(x.css); @media screen { p { color: red } } q { color: blue }'
    sheet = StyleSheet(css)
    assert [rule.selector_text for rule in sheet.rules] == ["q"]


def test_stylesheet_skips_unterminated_at_block_and_trailing_selector() -> None:
    assert len(StyleSheet("@media screen { p { color: red ").rules) == 0
    assert [rule.selector_text for rule in StyleSheet("a { color: red } b").rules] == ["a"]
    assert len(StyleSheet("@font-face").rules) == 0


def test_stylesheet_drops_empty_selector_and_unterminated_block() -> None:
    assert [rule.selector_text for rule in StyleSheet("{ color: red } p { color: blue").rules] == ["p"]


def test_stylesheet_grows_past_initial_capacity() -> None:
    css = " ".join(f".c{index} {{ color: red }}" for index in range(12))
    assert len(StyleSheet(css).rules) == 12


def test_stylesheet_empty_and_whitespace() -> None:
    assert len(StyleSheet("").rules) == 0
    assert len(StyleSheet("   \n  ").rules) == 0


def test_stylesheet_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        StyleSheet(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the C guard


def test_rulelist_and_rule_surface() -> None:
    sheet = StyleSheet("a { color: red } b { color: blue }")
    rules = sheet.rules
    assert isinstance(rules, RuleList)
    assert len(rules) == 2
    assert [rule.selector_text for rule in rules] == ["a", "b"]
    first: StyleRule = rules[0]
    assert isinstance(first.style, StyleDeclaration)
    assert repr(first) == "StyleRule('a' { color: red })"
    assert repr(sheet) == "StyleSheet(2 rules)"
    assert repr(rules).startswith("RuleList([")


def test_computed_style_specificity_orders_id_over_class_over_type() -> None:
    style = _style(
        "<div class='x' id='a'>hi</div>",
        css="div { color: red } .x { color: green } #a { color: blue }",
    )
    assert style["color"] == "blue"


def test_computed_style_specificity_class_beats_type() -> None:
    assert _style("<div class='x'></div>", css="div { color: red } .x { color: green }")["color"] == "green"


def test_computed_style_specificity_counts_type_depth() -> None:
    style = _style(
        "<div></div>",
        css="div { color: red } body div { color: green }",
    )
    assert style["color"] == "green"


def test_computed_style_picks_most_specific_matching_alternative() -> None:
    # every alternative of the one rule matches; branch coverage of the specificity max
    style = _style(
        "<div class='x y'></div>",
        css=".x, .x.y, div.x, div.x.y, .x.y { color: teal }",
    )
    assert style["color"] == "teal"


def test_computed_style_ignores_names_bordering_known_properties() -> None:
    # names the property binary search must reject: one a char longer than "color"
    # (a real name is its prefix) and a bare "c" (a prefix of a real name). Neither
    # matches, so the real color declaration still wins.
    style = _style("<div></div>", css="div { colorz: red; c: green; color: blue }")
    assert style["color"] == "blue"


def test_computed_style_later_source_order_wins_on_a_tie() -> None:
    assert _style("<p></p>", css="p { color: red } p { color: blue }", tag="p")["color"] == "blue"


def test_computed_style_important_beats_normal() -> None:
    style = _style("<p></p>", css="p { color: blue !important } p { color: red }", tag="p")
    assert style["color"] == "blue"


def test_computed_style_inline_beats_rule() -> None:
    assert _style("<div style='color: teal'></div>", css="div { color: red }")["color"] == "teal"


def test_computed_style_important_rule_beats_normal_inline() -> None:
    assert _style("<div style='color: teal'></div>", css="div { color: red !important }")["color"] == "red"


def test_computed_style_important_inline_beats_important_rule() -> None:
    style = _style("<div style='color: teal !important'></div>", css="div { color: red !important }")
    assert style["color"] == "teal"


def test_computed_style_inline_valueless_style_attribute_is_ignored() -> None:
    assert _style("<div style></div>", css="div { color: red }")["color"] == "red"


def test_computed_style_inline_alongside_other_attribute() -> None:
    assert _style("<div data-x='1'></div>", css="div { color: red }")["color"] == "red"


def test_computed_style_inheritance_and_initial() -> None:
    document = parse(
        "<html><head><style>div { color: green; margin: 5px }</style></head>"
        "<body><div><span>hi</span></div></body></html>"
    )
    span = document.select_one("span")
    assert isinstance(span, Element)
    style = computed_style(span)
    assert style["color"] == "green"  # inherited
    assert style["margin-top"] == "0"  # margin does not inherit -> initial
    assert style["display"] == "inline"  # never set -> initial


def test_computed_style_unset_supported_property_falls_back_to_initial() -> None:
    style = _style("<div></div>")
    assert style["display"] == "inline"
    assert style["color"] == "canvastext"
    assert style["opacity"] == "1"
    assert style["background-color"] == "transparent"


@pytest.mark.parametrize(
    ("value", "top", "right", "bottom", "left"),
    [
        pytest.param("5px", "5px", "5px", "5px", "5px", id="one-value"),
        pytest.param("1px 2px", "1px", "2px", "1px", "2px", id="two-values"),
        pytest.param("1px 2px 3px", "1px", "2px", "3px", "2px", id="three-values"),
        pytest.param("1px 2px 3px 4px", "1px", "2px", "3px", "4px", id="four-values"),
    ],
)
def test_computed_style_margin_shorthand_distributes(value: str, top: str, right: str, bottom: str, left: str) -> None:
    style = _style("<div></div>", css=f"div {{ margin: {value} }}")
    assert (style["margin-top"], style["margin-right"], style["margin-bottom"], style["margin-left"]) == (
        top,
        right,
        bottom,
        left,
    )


def test_computed_style_padding_and_border_family_shorthands() -> None:
    style = _style(
        "<div></div>",
        css="div { padding: 1px 2px; border-width: 3px; border-style: solid dashed; border-color: rgb(1, 2, 3) }",
    )
    assert style["padding-top"] == "1px"
    assert style["padding-right"] == "2px"
    assert style["border-top-width"] == "3px"
    assert style["border-top-style"] == "solid"
    assert style["border-right-style"] == "dashed"
    assert style["border-left-color"] == "rgb(1, 2, 3)"


def test_computed_style_overflow_shorthand() -> None:
    assert _style("<div></div>", css="div { overflow: hidden }")["overflow-y"] == "hidden"
    style = _style("<div></div>", css="div { overflow: hidden scroll }")
    assert (style["overflow-x"], style["overflow-y"]) == ("hidden", "scroll")


def test_computed_style_border_shorthand_expands_all_twelve_longhands() -> None:
    style = _style("<div></div>", css="div { border: 2px dashed green }")
    for side in ("top", "right", "bottom", "left"):
        assert style[f"border-{side}-width"] == "2px"
        assert style[f"border-{side}-style"] == "dashed"
        assert style[f"border-{side}-color"] == "green"


@pytest.mark.parametrize("side", ["top", "right", "bottom", "left"])
def test_computed_style_border_side_shorthand_sets_only_that_side(side: str) -> None:
    style = _style("<div></div>", css=f"div {{ border-{side}: 1px solid red }}")
    assert (style[f"border-{side}-width"], style[f"border-{side}-style"], style[f"border-{side}-color"]) == (
        "1px",
        "solid",
        "red",
    )
    other = "bottom" if side == "top" else "top"
    assert style[f"border-{other}-width"] == "medium"  # a sibling side stays at its initial


@pytest.mark.parametrize(
    ("value", "width", "style_", "color"),
    [
        pytest.param("thick dotted blue", "thick", "dotted", "blue", id="width-style-color"),
        pytest.param("blue thick dotted", "thick", "dotted", "blue", id="color-width-style-any-order"),
        pytest.param("auto", "medium", "auto", "currentcolor", id="style-only-omits-reset-to-initial"),
        pytest.param(".5px solid rgb(1, 2, 3)", ".5px", "solid", "rgb(1, 2, 3)", id="length-lead-and-color-function"),
        pytest.param("thin", "thin", "none", "currentcolor", id="width-keyword-only"),
    ],
)
def test_computed_style_outline_shorthand_classifies_components(
    value: str, width: str, style_: str, color: str
) -> None:
    style = _style("<div></div>", css=f"div {{ outline: {value} }}")
    assert (style["outline-width"], style["outline-style"], style["outline-color"]) == (width, style_, color)


def test_computed_style_border_longhand_after_shorthand_wins() -> None:
    style = _style("<div></div>", css="div { border: 2px solid green; border-top-color: navy }")
    assert style["border-top-color"] == "navy"
    assert style["border-right-color"] == "green"


def test_computed_style_border_shorthand_after_longhand_resets_it() -> None:
    style = _style("<div></div>", css="div { border-top-color: red; border-top: 2px solid }")
    assert style["border-top-width"] == "2px"
    assert style["border-top-style"] == "solid"
    assert style["border-top-color"] == "currentcolor"  # the omitted component resets to the initial


def test_computed_style_border_shorthand_higher_priority_longhand_survives() -> None:
    style = _style("<div id='a' style='border: 5px solid red'></div>", css="#a { border-top-width: 9px !important }")
    assert style["border-top-width"] == "9px"
    assert style["border-right-width"] == "5px"


def test_computed_style_border_shorthand_repeated_component_is_invalid() -> None:
    # two style keywords cannot both bind: the whole declaration is invalid and sets nothing
    style = _style("<div></div>", css="div { border-top-color: lime; border-top: solid dashed }")
    assert style["border-top-style"] == "none"
    assert style["border-top-color"] == "lime"  # the earlier longhand survives the dropped shorthand


def test_computed_style_shorthand_with_whitespace_separators() -> None:
    style = _style("<div style='margin:1px\t2px\n3px'></div>")
    assert (style["margin-top"], style["margin-right"], style["margin-bottom"]) == ("1px", "2px", "3px")


def test_computed_style_shorthand_keeps_parenthesised_group_whole() -> None:
    style = _style("<div></div>", css="div { padding: calc(1px + 2px) 3px }")
    assert style["padding-top"] == "calc(1px + 2px)"
    assert style["padding-right"] == "3px"


def test_computed_style_shorthand_tolerates_stray_close_paren() -> None:
    style = _style("<div></div>", css="div { overflow: hidden) scroll }")
    assert (style["overflow-x"], style["overflow-y"]) == ("hidden)", "scroll")


def test_computed_style_over_long_shorthand_is_invalid() -> None:
    assert _style("<div></div>", css="div { margin: 1px 2px 3px 4px 5px }")["margin-top"] == "0"
    assert _style("<div></div>", css="div { overflow: a b c }")["overflow-x"] == "visible"


def test_computed_style_empty_shorthand_is_ignored() -> None:
    assert _style("<div style='margin:'></div>")["margin-top"] == "0"


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        pytest.param("inherit", "rebeccapurple", id="inherit"),
        pytest.param("unset", "rebeccapurple", id="unset-inherited"),
        pytest.param("revert", "rebeccapurple", id="revert-inherited"),
        pytest.param("initial", "canvastext", id="initial"),
    ],
)
def test_computed_style_cascade_keywords_on_inherited_property(keyword: str, expected: str) -> None:
    document = parse(
        f"<html><head><style>body {{ color: rebeccapurple }} span {{ color: {keyword} }}</style></head>"
        "<body><span>hi</span></body></html>"
    )
    span = document.select_one("span")
    assert isinstance(span, Element)
    assert computed_style(span)["color"] == expected


def test_computed_style_unset_on_non_inherited_property_is_initial() -> None:
    document = parse(
        "<html><head><style>body { display: block } div { display: unset }</style></head>"
        "<body><div></div></body></html>"
    )
    div = document.select_one("div")
    assert isinstance(div, Element)
    assert computed_style(div)["display"] == "inline"


def test_computed_style_inherit_at_root_falls_back_to_initial() -> None:
    document = parse("<html><head><style>html { color: inherit }</style></head><body></body></html>")
    root = document.root
    assert isinstance(root, Element)
    assert computed_style(root)["color"] == "canvastext"


def test_computed_style_ignores_unsupported_selector_rules() -> None:
    style = _style("<div></div>", css="div::before { color: red } div { color: green }")
    assert style["color"] == "green"


def test_computed_style_ignores_non_matching_rules() -> None:
    assert _style("<div></div>", css="p { color: red } div { color: green }")["color"] == "green"


def test_computed_style_reads_multiple_style_sheets_in_document_order() -> None:
    document = parse(
        "<html><head><style>p { color: red }</style><style>p { color: blue }</style></head><body><p></p></body></html>"
    )
    paragraph = document.select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "blue"


def test_computed_style_repeated_call_keeps_value() -> None:
    document = parse("<style>p { color: red }</style><p></p>")
    paragraph = document.select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == computed_style(paragraph)["color"] == "red"


def test_computed_style_tracks_stylesheet_text_mutation() -> None:
    document = parse("<style>p { color: red }</style><p></p>")
    style = document.select_one("style")
    paragraph = document.select_one("p")
    assert isinstance(style, Element)
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "red"
    text = style.children[0]
    assert isinstance(text, Text)
    text.data = "p { color: blue }"
    assert computed_style(paragraph)["color"] == "blue"


def test_computed_style_tracks_stylesheet_insertion() -> None:
    document = parse("<head><style>p { color: red }</style></head><body><p></p></body>")
    head = document.select_one("head")
    paragraph = document.select_one("p")
    assert isinstance(head, Element)
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "red"
    head.append(Element("style", children=[Text("p { color: blue }")]))
    assert computed_style(paragraph)["color"] == "blue"


def test_computed_style_recompiles_after_new_attribute_name() -> None:
    document = parse("<style>p[data-late] { color: red }</style><p></p>")
    paragraph = document.select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "canvastext"
    paragraph.attrs["data-late"] = "yes"
    assert computed_style(paragraph)["color"] == "red"


def test_computed_style_surface() -> None:
    style = _style("<div></div>", css="div { color: red }")
    assert style.get("color") == "red"
    assert style.get("nonesuch") is None
    assert style.get("nonesuch", "fallback") == "fallback"
    assert "color" in style
    assert "nonesuch" not in style
    assert style["color"] == "red"
    with pytest.raises(KeyError):
        _ = style["nonesuch"]
    names = style.properties()
    assert names[0] == "color"
    assert "display" in names
    assert len(style) == len(names)
    assert list(style) == list(names)
    assert repr(style) == f"ComputedStyle({len(style)} properties)"


def test_computed_style_rejects_non_element() -> None:
    document = parse("<p>hi</p>")
    paragraph = document.select_one("p")
    assert isinstance(paragraph, Element)
    with pytest.raises(TypeError):
        computed_style(paragraph.children[0])  # ty: ignore[invalid-argument-type]  # a text node is not an Element
    with pytest.raises(TypeError):
        computed_style("not a node")  # ty: ignore[invalid-argument-type]  # a non-node exercises the guard


def test_computed_style_with_no_stylesheet_is_all_initial() -> None:
    document = parse("<div></div>")
    div = document.select_one("div")
    assert isinstance(div, Element)
    assert computed_style(div)["visibility"] == "visible"


def test_stylesheet_attribute_selector_and_stray_bracket() -> None:
    sheet = StyleSheet('a[data-x="y"] { color: red } .c { width: 3) }')
    assert sheet.rules[0].selector_text == 'a[data-x="y"]'
    assert sheet.rules[1].style.get("width") == "3)"


def test_declaration_property_names_are_case_insensitive() -> None:
    assert _style("<div style='COLOR: RebeccaPurple'></div>")["color"] == "RebeccaPurple"


def test_computed_style_ignores_unknown_property() -> None:
    # "font" and "margink" are neither tracked longhands nor shorthands: both are dropped
    style = _style("<div></div>", css="div { font: 12px serif; margink: 9px; color: red }")
    assert style["color"] == "red"
    assert style["margin-top"] == "0"


def test_computed_style_shorthand_does_not_override_higher_priority_longhand() -> None:
    style = _style("<div id='a' style='margin: 5px'></div>", css="#a { margin-top: 9px !important }")
    assert style["margin-top"] == "9px"
    assert style["margin-right"] == "5px"


def test_computed_style_important_keyword_is_a_value_when_not_flagged() -> None:
    assert _style("<div></div>", css="div { color: red important }")["color"] == "red important"


def test_computed_style_reads_five_style_sheets() -> None:
    sheets = "".join(f"<style>p {{ color: c{index} }}</style>" for index in range(5))
    document = parse(f"<html><head>{sheets}</head><body><p></p></body></html>")
    paragraph = document.select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "c4"


def test_computed_style_on_detached_element_uses_inline_and_initials() -> None:
    element = E("div", {"style": "color: red"})
    style = computed_style(element)
    assert style["color"] == "red"
    assert style["display"] == "inline"


def test_comment_and_escape_edge_cases() -> None:
    assert StyleDeclaration.parse('content: "a\\"b"').get("content") == '"a\\"b"'
    assert StyleDeclaration.parse('content: "a\\').get("content") == '"a\\'
    assert StyleSheet("a { b: c/d }").rules[0].style.get("b") == "c/d"
    assert StyleSheet("a { b: c }/").rules[0].style.get("b") == "c"
    assert StyleSheet("a { b: c }/*").rules[0].style.get("b") == "c"
    assert StyleSheet("a { /* x * y */ b: c }").rules[0].style.get("b") == "c"


def test_at_rule_block_ignores_braces_inside_selectors_and_strings() -> None:
    css = "@media x { a[t='}'] { color: red } b[u=\"\\}\"] { color: red } } p { color: blue }"
    assert [rule.selector_text for rule in StyleSheet(css).rules] == ["p"]


def test_at_rule_block_tolerates_unterminated_string() -> None:
    assert len(StyleSheet('@media x { a { content: "oops').rules) == 0
    assert len(StyleSheet("@media x { a { content: 'x\\").rules) == 0


def test_single_quoted_strings_and_semicolon_in_parentheses() -> None:
    declaration = StyleDeclaration.parse("content: 'a;b'; color: red")
    assert declaration.get("content") == "'a;b'"
    assert declaration.get("color") == "red"
    assert StyleDeclaration.parse("width: calc(1 ; 2); color: red").get("color") == "red"


def test_computed_style_unset_inherited_property_at_root_is_initial() -> None:
    document = parse("<html><head><style>html { color: unset }</style></head><body></body></html>")
    root = document.root
    assert isinstance(root, Element)
    assert computed_style(root)["color"] == "canvastext"


def test_computed_style_alternative_with_lower_id_specificity_loses() -> None:
    # the id alternative sets the best specificity; the class alternative then compares below it
    style = _style("<div class='x' id='a'></div>", css="#a, .x { color: teal }")
    assert style["color"] == "teal"


_ITEMS = (("color", "red", False), ("margin", "0", True), ("color", "blue", False))


def test_the_index_keeps_first_seen_order_and_the_last_value() -> None:
    assert _css_declaration_index(_ITEMS) == {"color": 2, "margin": 1}
    assert list(_css_declaration_index(_ITEMS)) == ["color", "margin"]


def test_the_index_of_nothing_is_empty() -> None:
    assert _css_declaration_index(()) == {}


def test_the_text_serializes_in_source_order_with_the_flag() -> None:
    assert _css_declaration_text(_ITEMS) == "color: red; margin: 0 !important; color: blue"


class _Undecided:
    """A flag whose truth test raises, the way a lazy proxy might."""

    def __bool__(self) -> bool:
        msg = "undecided"
        raise RuntimeError(msg)


def test_the_text_propagates_a_flag_whose_truth_test_raises() -> None:
    with pytest.raises(RuntimeError, match="undecided"):
        _css_declaration_text((("color", "red", _Undecided()),))  # ty: ignore[invalid-argument-type]


def test_the_text_of_nothing_is_empty() -> None:
    assert not _css_declaration_text(())


@pytest.mark.parametrize(
    "items",
    [
        pytest.param([("color", "red", False)], id="a-list"),
        pytest.param((("color", "red"),), id="a-pair"),
        pytest.param(((1, "red", False),), id="a-non-str-name"),
        pytest.param((("color", 2, False),), id="a-non-str-value"),
        pytest.param(("color",), id="a-bare-str"),
    ],
)
def test_the_entries_reject_malformed_items(items: object) -> None:
    with pytest.raises(TypeError):
        _css_declaration_index(items)  # ty: ignore[invalid-argument-type]  # the argument check is the point
    with pytest.raises(TypeError):
        _css_declaration_text(items)  # ty: ignore[invalid-argument-type]  # the argument check is the point


@pytest.mark.parametrize(
    ("attribute", "value"),
    [pytest.param("style", "color:blue", id="inline"), pytest.param("class", "blue", id="class")],
)
def test_computed_style_after_parent_attribute_change(attribute: str, value: str) -> None:
    document: Final[Document] = parse(
        "<style>.blue {color:blue!important}</style><div style='color:red'><span>x</span></div>"
    )
    parent: Final[Element] = document.select("div")[0]
    child: Final[Element] = document.select("span")[0]
    before: Final[tuple[str, str]] = (computed_style(parent)["color"], computed_style(child)["color"])
    parent.attrs[attribute] = value
    assert (before, computed_style(parent)["color"], computed_style(child)["color"]) == (("red", "red"), "blue", "blue")


def test_computed_style_after_parent_attribute_removal() -> None:
    document: Final[Document] = parse("<div style='color:red'><span>x</span></div>")
    parent: Final[Element] = document.select("div")[0]
    child: Final[Element] = document.select("span")[0]
    before: Final[str] = computed_style(child)["color"]
    del parent.attrs["style"]
    assert (before, computed_style(child)["color"]) == ("red", "canvastext")


def test_computed_style_after_sibling_attribute_change() -> None:
    document: Final[Document] = parse("<style>.blue + div {color:blue}</style><aside></aside><div>x</div>")
    child: Final[Element] = document.select("div")[0]
    before: Final[str] = computed_style(child)["color"]
    document.select("aside")[0].attrs["class"] = "blue"
    assert (before, computed_style(child)["color"]) == ("canvastext", "blue")


def test_computed_style_reuses_parent_across_children() -> None:
    document: Final[Document] = parse("<div style='color:red'><span>a</span><b>b</b><i>c</i></div>")
    assert [computed_style(element)["color"] for element in document.select("div, span, b, i")] == ["red"] * 4


def test_computed_style_snapshot_survives_later_resolution() -> None:
    document: Final[Document] = parse("<div style='color:red'>a</div><div style='color:blue'>b</div>")
    first: Final = computed_style(document.select("div")[0])
    second: Final = computed_style(document.select("div")[1])
    assert (first["color"], second["color"], computed_style(document.select("div")[0])["color"]) == (
        "red",
        "blue",
        "red",
    )


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        pytest.param(".hit,#missing{color:red}.hit{color:blue}", "blue", id="unmatched-list-alternative"),
        pytest.param(":is(.hit,#missing){color:red}.hit{color:blue}", "red", id="is-maximum-alternative"),
        pytest.param(":where(#target,.hit){color:red}div{color:blue}", "blue", id="where-zero-specificity"),
        pytest.param(".hit,#target{color:red}.hit{color:blue}", "red", id="matching-list-maximum"),
        pytest.param(".hit,#missing{color:red}.hit,#other{color:blue}", "blue", id="source-order"),
        pytest.param(":invalid-pseudo{color:red}.hit{color:blue}", "blue", id="invalid-rule-before-valid"),
        pytest.param(":invalid-pseudo{color:red}", "canvastext", id="invalid-only-sheet"),
        pytest.param("", "canvastext", id="empty-sheet"),
    ],
)
def test_computed_style_preserves_alternative_specificity(css: str, expected: str) -> None:
    document: Final = parse(f"<style>{css}</style>" + '<div class="hit" id="target"></div>' * 3)
    assert [computed_style(node)["color"] for node in document.select("div")] == [expected] * 3


def test_computed_style_keeps_specificity_within_each_sheet() -> None:
    document: Final = parse(
        '<style>#target,#missing{color:red}</style><style>.hit{color:blue}</style><div class="hit" id="target"></div>'
    )
    assert computed_style(document.select("div")[0])["color"] == "red"


def test_computed_style_rebuilds_specificity_after_selector_change() -> None:
    document: Final = parse(
        '<style>.hit,#missing{color:red}div.hit{color:blue}</style><div class="hit" id="target"></div>'
    )
    node: Final = document.select("div")[0]
    before: Final = computed_style(node)["color"]
    text: Final = document.select("style")[0].children[0]
    assert isinstance(text, Text)
    text.data = "#target{color:red}div.hit{color:blue}"
    assert (before, computed_style(node)["color"]) == ("blue", "red")


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(":has(span).hit", "red", id="matching-class"),
        pytest.param(":has(span).missing", "blue", id="missing-class"),
        pytest.param(":has(span)#target", "red", id="matching-id"),
        pytest.param(":has(span)#missing", "blue", id="missing-id"),
        pytest.param(":has(b).hit", "blue", id="missing-descendant"),
        pytest.param("*:has(span).hit", "red", id="universal-first"),
        pytest.param("div:has(span).hit", "red", id="type-first"),
        pytest.param(".hit:has(span)", "red", id="class-first"),
        pytest.param(":not(.other).hit", "red", id="negation-first"),
        pytest.param(":has(b).missing,:has(span).hit", "red", id="selector-list"),
    ],
)
def test_computed_style_preserves_compound_predicates(selector: str, expected: str) -> None:
    document: Final = parse(
        f"<!doctype html><style>{selector}{{color:red}}div{{color:blue}}</style>"
        '<div class="hit" id="target"><span></span></div>'
    )
    assert computed_style(document.select("div")[0])["color"] == expected


@pytest.mark.parametrize(
    ("doctype", "expected"),
    [pytest.param("", "red", id="quirks"), pytest.param("<!doctype html>", "blue", id="standards")],
)
def test_computed_style_predicate_order_preserves_quirks(doctype: str, expected: str) -> None:
    document: Final = parse(
        f'{doctype}<style>:has(span).hit{{color:red}}div{{color:blue}}</style><div class="HIT"><span></span></div>'
    )
    assert computed_style(document.select("div")[0])["color"] == expected


def test_computed_style_predicate_order_preserves_foreign_elements() -> None:
    document: Final = parse(
        '<!doctype html><style>:has(circle).hit{color:red}g{color:blue}</style><svg><g class="hit"><circle/></g></svg>'
    )
    assert computed_style(document.select("g")[0])["color"] == "red"


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(":has(.hit)", "red", id="descendant-match"),
        pytest.param(":has(.absent)", "blue", id="descendant-miss"),
        pytest.param(":has(:scope > .hit)", "blue", id="scope-relative-child"),
        pytest.param(":has(:is(:scope > .hit))", "blue", id="nested-scope"),
    ],
)
def test_computed_style_deep_has_scope(selector: str, expected: str) -> None:
    document: Final = parse(
        f"<style>div{{color:blue}}div{selector}{{color:red}}</style>"
        + "<div>" * 64
        + '<span class="hit"></span>'
        + "</div>" * 64
    )
    nodes: Final = tuple(document.select("div"))
    for node in reversed(nodes):
        computed_style(node)
    assert computed_style(nodes[0])["color"] == expected


@pytest.mark.parametrize("mutation", ["attribute", "insert", "remove", "text", "stylesheet"])
def test_computed_style_has_refreshes_after_mutation(mutation: str) -> None:
    selector: Final = ":has(span:empty)" if mutation == "text" else ":has(.hit)"
    leaf: Final = '<span class="hit"></span>' if mutation == "remove" else "<span></span>"
    document: Final = parse(
        f"<style>div{{color:blue}}div{selector}{{color:red}}</style>" + "<div>" * 64 + leaf + "</div>" * 64
    )
    outer: Final = document.select("div")[0]
    target: Final = document.select("span")[0]
    before: Final = computed_style(outer)["color"]
    if mutation == "attribute":
        target.attrs["class"] = "hit"
    elif mutation == "insert":
        target.append(Element("b", {"class": "hit"}))
    elif mutation == "remove":
        target.extract()
    elif mutation == "text":
        target.append(Text("filled"))
    else:
        style_text: Final = document.select("style")[0].children[0]
        assert isinstance(style_text, Text)
        style_text.data = "div{color:red}"
    expected: Final = ("red", "blue") if mutation in {"remove", "text"} else ("blue", "red")
    assert (before, computed_style(outer)["color"]) == expected


@pytest.mark.parametrize(
    ("case_index", "expected"),
    [
        pytest.param(0, ("blue",) * 512, id="deep-missing"),
        pytest.param(1, ("red",) * 510 + ("blue",) * 2, id="deep-matching"),
        pytest.param(2, ("red", "blue") * 2_048, id="wide-positional"),
        pytest.param(3, ("red",) * 8, id="shallow-has"),
    ],
)
def test_computed_style_selector_shared_inputs(case_index: int, expected: tuple[str, ...]) -> None:
    source: Final = INPUTS["computed-style-selectors"]()[case_index][1]
    assert isinstance(source, str)
    document: Final = parse(source)
    assert tuple(computed_style(node)["color"] for node in document.select("div")) == expected


def test_computed_style_repeated_missing_has_bounds_retained_memory() -> None:
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    document: Final = parse(
        "<style>div{color:blue}div:has(.absent){color:red}</style>" + "<div>" * 64 + "<span></span>" + "</div>" * 64
    )
    nodes: Final = tuple(document.select("div"))[:3]
    tracing.start()
    try:
        for node in nodes:
            computed_style(node)
        gc.collect()
        before: Final = tracing.get_traced_memory()[0]
        for _ in range(1_024):
            for node in nodes:
                computed_style(node)
        gc.collect()
        retained: Final = tracing.get_traced_memory()[0] - before
    finally:
        tracing.stop()
    assert (tuple(computed_style(node)["color"] for node in nodes), retained < 32_768) == (("blue",) * 3, True), (
        retained
    )


@pytest.mark.parametrize("reverse", [pytest.param(False, id="forward"), pytest.param(True, id="reverse")])
@pytest.mark.parametrize("separator", [pytest.param("", id="adjacent"), pytest.param(" \n<!--gap-->", id="spaced")])
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(":nth-child(odd)", ("red", "blue", "red", "blue"), id="child"),
        pytest.param(":nth-last-child(odd)", ("blue", "red", "blue", "red"), id="last-child"),
        pytest.param(":nth-of-type(odd)", ("red", "blue", "red", "blue"), id="type"),
        pytest.param(":nth-last-of-type(odd)", ("blue", "red", "blue", "red"), id="last-type"),
        pytest.param(":nth-child(1 of :scope)", ("red",) * 4, id="scope"),
        pytest.param(":nth-child(2 of :scope)", ("blue",) * 4, id="scope-second"),
        pytest.param(":nth-child(2 of .hit)", ("blue", "blue", "red", "blue"), id="filtered"),
        pytest.param(":nth-child(2 of :nth-child(odd))", ("blue", "blue", "red", "blue"), id="nested"),
        pytest.param(":nth-last-child(2 of :nth-child(odd))", ("red", "blue", "blue", "blue"), id="nested-last"),
        pytest.param(":nth-child(2 of :nth-of-type(odd))", ("blue", "blue", "red", "blue"), id="nested-type"),
        pytest.param(
            ":nth-child(1 of :is(:scope, :nth-child(even)))", ("red", "red", "blue", "blue"), id="nested-scope"
        ),
    ],
)
def test_computed_style_sibling_positions(
    selector: str, expected: tuple[str, ...], separator: str, *, reverse: bool
) -> None:
    document: Final = parse(
        f"<style>i{{color:blue}}i{selector}{{color:red}}</style>"
        + separator.join(('<i class="hit"></i>', "<i></i>", '<i class="hit"></i>', "<i></i>"))
    )
    nodes: Final = document.select("i")
    assert tuple(computed_style(node)["color"] for node in (reversed(nodes) if reverse else nodes)) == (
        tuple(reversed(expected)) if reverse else expected
    )


def test_computed_style_sibling_positions_after_insertion() -> None:
    document: Final = parse("<style>i{color:blue}i:nth-child(odd){color:red}</style><i></i><i></i><i></i>")
    nodes: Final = document.select("i")
    before: Final = tuple(computed_style(node)["color"] for node in reversed(nodes))
    nodes[0].insert_before(Element("i"))
    assert (before, tuple(computed_style(node)["color"] for node in reversed(nodes))) == (
        ("red", "blue", "red"),
        ("blue", "red", "blue"),
    )


@pytest.mark.parametrize(
    "order",
    [
        pytest.param((0, 1, 2, 3), id="forward"),
        pytest.param((3, 2, 1, 0), id="reverse"),
        pytest.param((0, 3, 1, 2), id="shuffled"),
    ],
)
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(":nth-of-type(odd)", ("red", "blue", "red", "blue"), id="type"),
        pytest.param(":nth-last-of-type(odd)", ("blue", "red", "blue", "red"), id="last-type"),
    ],
)
def test_computed_style_sibling_types(selector: str, expected: tuple[str, ...], order: tuple[int, ...]) -> None:
    document: Final = parse(
        f"<style>i{{color:blue}}i{selector}{{color:red}}</style>" + "<i></i> <!--gap--> <b></b>" * 4
    )
    nodes: Final = document.select("i")
    assert tuple(computed_style(nodes[index])["color"] for index in order) == tuple(expected[index] for index in order)


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        pytest.param((1, 2, 0), ("red", "red", "red"), id="siblings-parent"),
        pytest.param((1, 4, 2, 5), ("red", "blue", "red", "blue"), id="different-parents"),
        pytest.param((1, 2, 4, 5, 3), ("red", "red", "blue", "blue", "blue"), id="replace-parent"),
    ],
)
def test_computed_style_inherited_sibling_values(order: tuple[int, ...], expected: tuple[str, ...]) -> None:
    document: Final = parse('<div style="color:red"><i></i><i></i></div><div style="color:blue"><i></i><i></i></div>')
    nodes: Final = document.select("div,i")
    assert tuple(computed_style(nodes[index])["color"] for index in order) == expected
