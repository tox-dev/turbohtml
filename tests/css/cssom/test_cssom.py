from __future__ import annotations

import subprocess  # ruff:ignore[suspicious-subprocess-import]  # runs a fixed argv in a child interpreter
import sys
from functools import partial
from typing import TYPE_CHECKING, Final

import pytest
from bench.operations import INPUTS, Case

from turbohtml import DocumentFragment, Element, Text, parse, parse_xml
from turbohtml.build import E
from turbohtml.cssom import ComputedStyle, RuleList, StyleDeclaration, StyleRule, StyleSheet, computed_style

if TYPE_CHECKING:
    from collections.abc import Callable

    from traced_bytes import PeakBytes, RetainedBytes
    from work_steps import WorkSteps

    from turbohtml import Document, Node


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


@pytest.mark.parametrize("name", ["color", "COLOR", "CoLoR"])
def test_declaration_standard_property_names_ignore_ascii_case(name: str) -> None:
    declaration = StyleDeclaration.parse("COLOR: red !important; color: blue")
    assert (declaration.get(name), declaration[name], declaration.important(name), name in declaration) == (
        "red",
        "red",
        True,
        True,
    )


def test_declaration_standard_property_name_is_lowercase() -> None:
    declaration = StyleDeclaration.parse("COLOR: red")
    assert (declaration.properties(), declaration.text) == (("color",), "color: red")


def test_declaration_single_dash_property_name_is_lowercase() -> None:
    declaration = StyleDeclaration.parse("-X: red")
    assert declaration.properties() == ("-x",)


def test_declaration_standard_property_name_folds_only_ascii() -> None:
    declaration = StyleDeclaration.parse("ÉX: red")
    assert (declaration.properties(), declaration.get("ÉX"), declaration.get("éx")) == (("Éx",), "red", None)


def test_declaration_custom_property_names_keep_case() -> None:
    declaration = StyleDeclaration.parse("--X: 1; --x: 2")
    assert (declaration.properties(), declaration.get("--X"), declaration.get("--x")) == (("--X", "--x"), "1", "2")


def test_declaration_custom_property_lookup_keeps_case() -> None:
    declaration = StyleDeclaration.parse("--X: 1")
    assert declaration.get("--x") is None


def test_declaration_escaped_uppercase_name_keeps_priority() -> None:
    declaration = StyleDeclaration.parse(r"\43 OLOR: red !important; color: blue")
    assert (declaration.get("COLOR"), declaration.important("color")) == ("red", True)


@pytest.mark.parametrize(
    ("source", "name", "expected"),
    [
        pytest.param("color: red !important; color: blue", "color", "red", id="earlier-important"),
        pytest.param("color: red; color: blue !important", "color", "blue", id="later-important"),
        pytest.param("color: red !important; color: blue !important", "color", "blue", id="later-important-tie"),
        pytest.param("color: red; color: blue; width: 1px !important", "color", "blue", id="unrelated-important"),
        pytest.param("--X: red !important; --X: blue", "--X", "red", id="custom-important"),
    ],
)
def test_declaration_duplicate_priority(source: str, name: str, expected: str) -> None:
    assert StyleDeclaration.parse(source)[name] == expected


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
    declaration = StyleDeclaration.parse(";".join(f"--v{index}: {index}" for index in range(20)))
    assert len(declaration) == 20


def test_parse_declarations_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        StyleDeclaration.parse(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the C guard


def test_comment_stripping_across_selector_value_and_string() -> None:
    rule = StyleSheet('di/**/v { colo/**/r: red; content: "/*keep*/" }').rules[0]
    assert rule.selector_text == "di v"
    assert rule.style.get("colo r") == "red"
    assert rule.style.get("content") == '"/*keep*/"'


def test_comment_stripping_tolerates_unterminated_comment_and_string() -> None:
    assert StyleSheet("a { color: red /* trailing").rules[0].style.get("color") == "red"
    assert StyleDeclaration.parse('content: "open').get("content") == '"open'


def test_stylesheet_skips_at_rules() -> None:
    sheet = StyleSheet('@charset "utf-8"; @import url(x.css); @media screen { p { color: red } } q { color: blue }')
    assert [rule.selector_text for rule in sheet.rules] == ["q"]


def _stylesheet_selectors(css: str) -> str:
    return " ".join(rule.selector_text for rule in StyleSheet(css).rules)


def _computed_color(css: str) -> str:
    target = parse(f"<style>{css}</style><p>x</p>").select_one("p")
    assert isinstance(target, Element)
    return computed_style(target)["color"]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        pytest.param(_stylesheet_selectors, "p", id="stylesheet"),
        pytest.param(_computed_color, "red", id="computed-style"),
    ],
)
def test_statement_at_rules_scale_linearly(entry: Callable[[str], str], expected: str, work_steps: WorkSteps) -> None:
    statement: Final[str] = "@import url(x);"
    work_steps.assert_linear(lambda n: partial(entry, f"{statement * n}p {{ color: red }}"), (500, 2000))
    assert entry(f"{statement * 2_000}p {{ color: red }}") == expected


def test_stylesheet_skips_unterminated_at_block_and_trailing_selector() -> None:
    assert len(StyleSheet("@media screen { p { color: red ").rules) == 0
    assert [rule.selector_text for rule in StyleSheet("a { color: red } b").rules] == ["a"]
    assert len(StyleSheet("@font-face").rules) == 0


def test_stylesheet_drops_empty_selector_and_unterminated_block() -> None:
    assert [rule.selector_text for rule in StyleSheet("{ color: red } p { color: blue").rules] == ["p"]


@pytest.mark.parametrize(
    ("css", "selector"),
    [
        pytest.param(r"\ {color:red}", r"\ ", id="escaped-space"),
        pytest.param(r"a\  {color:red}", r"a\ ", id="escaped-and-unescaped-space"),
        pytest.param(r"a\\ {color:red}", r"a\\", id="paired-backslashes-before-space"),
    ],
)
def test_stylesheet_selector_keeps_escaped_trailing_space(css: str, selector: str) -> None:
    assert [rule.selector_text for rule in StyleSheet(css).rules] == [selector]


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


def _style(element_html: str, *, css: str = "", tag: str = "div") -> ComputedStyle:
    # Compute the style of the first matching element in a document carrying one stylesheet.
    element = parse(f"<html><head><style>{css}</style></head><body>{element_html}</body></html>").select_one(tag)
    assert isinstance(element, Element)
    return computed_style(element)


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


def test_computed_style_has_rule_is_answered_for_each_element_of_one_tree() -> None:
    document = parse("<style>div:has(b) { color: red }</style><div><b>x</b></div><div>y</div><div><b>z</b></div>")
    first, middle, last = (computed_style(div)["color"] for div in document.select("div"))
    assert first == last == "red"
    assert middle != "red"


def test_computed_style_long_class_value_refreshes_after_change() -> None:
    # the rule's answer about the long class value is kept between elements and must not outlive a change to it
    pad: Final[str] = "w " * 160
    document: Final[Document] = parse(
        f'<style>li{{color:blue}}.hit li{{color:red}}</style><ol class="{pad}hit"><li><li></ol>'
    )
    items: Final[list[Element]] = document.select("li")
    before: Final[list[str]] = [computed_style(item)["color"] for item in items]
    document.select("ol")[0].attrs["class"] = pad
    assert (before, [computed_style(item)["color"] for item in items]) == (["red", "red"], ["blue", "blue"])


def test_computed_style_under_long_class_value_in_linear_time(work_steps: WorkSteps) -> None:
    def styles(size: int) -> Callable[[], object]:
        items = parse(f'<style>.hit li{{color:red}}</style><ol class="{"w " * size}">{"<li>" * size}').select("li")
        return lambda: [computed_style(item) for item in items]

    work_steps.assert_linear(styles, (250, 1000))


def test_computed_style_has_rule_after_detaching_a_subtree_of_the_same_tree() -> None:
    document = parse("<style>div:has(b) { color: red }</style><div><b>x</b></div><div><b>y</b></div>")
    detached = document.select("b")[1].extract()
    assert computed_style(detached)["color"] != "red"
    kept, emptied = document.select("div")
    assert computed_style(kept)["color"] == "red"
    assert computed_style(emptied)["color"] != "red"


@pytest.mark.parametrize(
    ("type_attribute", "expected"),
    [
        pytest.param("", "red", id="absent"),
        pytest.param(" type", "red", id="valueless"),
        pytest.param(' type=""', "red", id="empty"),
        pytest.param(' data-source="test" type="text/css"', "red", id="other-attribute"),
        pytest.param(' type="TEXT/CSS"', "red", id="ascii-case"),
        pytest.param(' type="text/plain"', "blue", id="different-type"),
        pytest.param(' type="text/css; charset=utf-8"', "blue", id="parameter"),
        pytest.param(' type=" text/css"', "blue", id="leading-space"),
        pytest.param(' type="text/csx"', "blue", id="same-length"),
    ],
)
def test_computed_style_skips_non_css_style_type(type_attribute: str, expected: str) -> None:
    document = parse(f"<style>div{{color:blue}}</style><style{type_attribute}>div{{color:red}}</style><div></div>")
    element = document.select_one("div")
    assert isinstance(element, Element)
    assert computed_style(element)["color"] == expected


@pytest.mark.parametrize(
    ("style_type", "expected"),
    [
        pytest.param("text/css", "red", id="svg-exact-type"),
        pytest.param("TEXT/CSS", "blue", id="svg-type-case"),
    ],
)
def test_computed_style_svg_style_type_is_case_sensitive(style_type: str, expected: str) -> None:
    document = parse(
        f"<style>div{{color:blue}}</style><svg><style type='{style_type}'>div{{color:red}}</style></svg><div></div>"
    )
    element = document.select_one("div")
    assert isinstance(element, Element)
    assert computed_style(element)["color"] == expected


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
    # color inherits, margin does not (initial), display is never set (initial)
    assert (style["color"], style["margin-top"], style["display"]) == ("green", "0", "inline")


def test_computed_style_unset_supported_property_falls_back_to_initial() -> None:
    style = _style("<div></div>")
    assert (style["display"], style["color"], style["opacity"], style["background-color"]) == (
        "inline",
        "canvastext",
        "1",
        "transparent",
    )


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
    assert (
        style["padding-top"],
        style["padding-right"],
        style["border-top-width"],
        style["border-top-style"],
        style["border-right-style"],
        style["border-left-color"],
    ) == ("1px", "2px", "3px", "solid", "dashed", "rgb(1, 2, 3)")


def test_computed_style_overflow_shorthand() -> None:
    assert _style("<div></div>", css="div { overflow: hidden }")["overflow-y"] == "hidden"
    style = _style("<div></div>", css="div { overflow: hidden scroll }")
    assert (style["overflow-x"], style["overflow-y"]) == ("hidden", "scroll")


def test_computed_style_border_shorthand_expands_all_twelve_longhands() -> None:
    style = _style("<div></div>", css="div { border: 2px dashed green }")
    for side in ("top", "right", "bottom", "left"):
        assert (style[f"border-{side}-width"], style[f"border-{side}-style"], style[f"border-{side}-color"]) == (
            "2px",
            "dashed",
            "green",
        )


@pytest.mark.parametrize(
    ("side", "other"),
    [
        pytest.param("top", "bottom", id="top"),
        pytest.param("right", "top", id="right"),
        pytest.param("bottom", "top", id="bottom"),
        pytest.param("left", "top", id="left"),
    ],
)
def test_computed_style_border_side_shorthand_sets_only_that_side(side: str, other: str) -> None:
    style = _style("<div></div>", css=f"div {{ border-{side}: 1px solid red }}")
    assert (style[f"border-{side}-width"], style[f"border-{side}-style"], style[f"border-{side}-color"]) == (
        "1px",
        "solid",
        "red",
    )
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
    assert (style["border-top-color"], style["border-right-color"]) == ("navy", "green")


def test_computed_style_border_shorthand_after_longhand_resets_it() -> None:
    style = _style("<div></div>", css="div { border-top-color: red; border-top: 2px solid }")
    # the omitted color component resets to its initial value
    assert (style["border-top-width"], style["border-top-style"], style["border-top-color"]) == (
        "2px",
        "solid",
        "currentcolor",
    )


def test_computed_style_border_shorthand_higher_priority_longhand_survives() -> None:
    style = _style("<div id='a' style='border: 5px solid red'></div>", css="#a { border-top-width: 9px !important }")
    assert (style["border-top-width"], style["border-right-width"]) == ("9px", "5px")


def test_computed_style_border_shorthand_repeated_component_is_invalid() -> None:
    # two style keywords cannot both bind: the whole declaration is invalid and sets nothing
    style = _style("<div></div>", css="div { border-top-color: lime; border-top: solid dashed }")
    assert (style["border-top-style"], style["border-top-color"]) == (
        "none",
        "lime",
    )  # the earlier longhand survives the dropped shorthand


def test_computed_style_shorthand_with_whitespace_separators() -> None:
    style = _style("<div style='margin:1px\t2px\n3px'></div>")
    assert (style["margin-top"], style["margin-right"], style["margin-bottom"]) == ("1px", "2px", "3px")


def test_computed_style_shorthand_keeps_parenthesised_group_whole() -> None:
    style = _style("<div></div>", css="div { padding: calc(1px + 2px) 3px }")
    assert (style["padding-top"], style["padding-right"]) == ("calc(1px + 2px)", "3px")


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
    root = parse("<html><head><style>html { color: inherit }</style></head><body></body></html>").root
    assert isinstance(root, Element)
    assert computed_style(root)["color"] == "canvastext"


def test_computed_style_detached_copy_ignores_root_rule() -> None:
    # extracting an element leaves it without ancestors, and the root rule must not keep matching it
    emphasis = parse(
        "<style>:root { color: navy } em { margin-top: 2px }</style><section><em>x</em></section>"
    ).select_one("em")
    assert isinstance(emphasis, Element)
    assert computed_style(emphasis)["color"] == "navy"
    detached = computed_style(emphasis.extract())
    assert (detached["color"], detached["margin-top"]) == ("canvastext", "2px")


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
    paragraph = parse("<style>p { color: red }</style><p></p>").select_one("p")
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


def _fragment_of(child: Node) -> DocumentFragment:
    fragment: Final[DocumentFragment] = DocumentFragment()
    fragment.append(child)
    return fragment


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        pytest.param(lambda doc: doc.select_one("i").decompose(), "red", id="decompose-plain"),
        pytest.param(lambda doc: doc.select_one("i").extract(), "red", id="extract-plain"),
        pytest.param(lambda doc: doc.select_one("i").unwrap(), "red", id="unwrap-plain"),
        pytest.param(lambda doc: doc.select_one("i").clear(), "red", id="clear-plain"),
        pytest.param(lambda doc: doc.select_one("i").replace_with(Element("u")), "red", id="replace-plain"),
        pytest.param(lambda doc: doc.select_one("b").append(Element("u")), "red", id="append-plain"),
        pytest.param(lambda doc: doc.select_one("b").extend([Element("u"), Text("x")]), "red", id="extend-plain"),
        pytest.param(lambda doc: doc.select_one("b").insert_before(doc.select_one("i")), "red", id="move-plain"),
        pytest.param(lambda doc: setattr(doc.select_one("b"), "text", "p { color: green }"), "red", id="text-plain"),
        pytest.param(lambda doc: setattr(doc.select_one("i").children[0], "data", "y"), "red", id="data-plain"),
        pytest.param(lambda doc: doc.select_one("i").wrap(Element("u")), "red", id="wrap-plain"),
        pytest.param(lambda doc: doc.select_one("b").set_inner_html("<u>x</u>"), "red", id="html-plain"),
        pytest.param(
            lambda doc: doc.select_one("b").insert_adjacent_html("afterend", "<u></u>"), "red", id="adjacent-plain"
        ),
        pytest.param(lambda doc: doc.select_one("span").decompose(), "blue", id="decompose-holder"),
        pytest.param(lambda doc: doc.select_one("span").extract(), "blue", id="extract-holder"),
        pytest.param(lambda doc: doc.select_one("span").clear(), "blue", id="clear-holder"),
        pytest.param(lambda doc: doc.select_one("#late").unwrap(), "blue", id="unwrap-style"),
        pytest.param(lambda doc: doc.select_one("#late").replace_with(Element("u")), "blue", id="replace-style"),
        pytest.param(lambda doc: doc.select_one("body").append(doc.select_one("#early")), "blue", id="move-style"),
        pytest.param(
            lambda doc: doc.select_one("#early").insert_before(doc.select_one("#late")), "blue", id="move-beside"
        ),
        pytest.param(lambda doc: doc.select_one("#late").children[0].decompose(), "blue", id="decompose-text"),
        pytest.param(lambda doc: doc.select_one("#late").append(Text("p { color: green }")), "green", id="append-text"),
        pytest.param(lambda doc: setattr(doc.select_one("#late"), "text", ""), "blue", id="text-style"),
        pytest.param(
            lambda doc: doc.select_one("body").append(E.div(E.style("p { color: green }"))), "green", id="append-holder"
        ),
        pytest.param(
            lambda doc: doc.select_one("body").extend([Element("u"), E.style("p { color: green }")]),
            "green",
            id="extend-style",
        ),
        pytest.param(
            lambda doc: doc.select_one("span").insert_after(_fragment_of(E.style("p { color: green }"))),
            "green",
            id="insert-fragment",
        ),
        pytest.param(
            lambda doc: doc.select_one("i").wrap(E.style("body p { color: green }")), "green", id="wrap-style"
        ),
        pytest.param(
            lambda doc: doc.select_one("i").wrap_siblings(E.style("body p { color: green }")), "green", id="wrap-run"
        ),
        pytest.param(
            lambda doc: doc.select_one("head").wrap_children(doc.select_one("span")), "blue", id="wrap-holder"
        ),
        pytest.param(
            lambda doc: doc.select_one("#late").wrap_children(E.g("body p { color: green }")), "blue", id="wrap-text"
        ),
        pytest.param(
            lambda doc: doc.select_one("b").set_inner_html("<style>body p { color: green }</style>"),
            "green",
            id="html-style",
        ),
        pytest.param(lambda doc: doc.select_one("#late").set_inner_html("p { color: green }"), "green", id="html-text"),
        pytest.param(
            lambda doc: doc.select_one("span").insert_adjacent_html("afterend", "<style>p { color: green }</style>"),
            "green",
            id="adjacent-style",
        ),
        pytest.param(
            lambda doc: doc.select_one("#late").insert_adjacent_html("beforeend", "p { color: green }"),
            "green",
            id="adjacent-text",
        ),
    ],
)
def test_computed_style_keeps_sheets_only_across_edits_without_style(
    edit: Callable[[Document], object], expected: str
) -> None:
    document: Final[Document] = parse(
        "<head><style id=early>p { color: blue }</style></head>"
        "<body><p></p><i>x</i><b></b><span><style id=late>p { color: red }</style></span></body>"
    )
    paragraph: Final[Element | None] = document.select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "red"
    edit(document)
    assert computed_style(paragraph)["color"] == expected


def test_computed_style_kept_sheets_match_later_elements() -> None:
    document: Final[Document] = parse("<style>late-tag, .late, #late { color: red }</style><p></p>")
    paragraph: Final[Element | None] = document.select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "canvastext"
    for element in (Element("late-tag"), Element("b", {"class": "late"}), Element("b", {"id": "late"})):
        paragraph.append(element)
        assert computed_style(element)["color"] == "red"


def test_computed_style_keeps_sheets_across_removals_in_linear_time(work_steps: WorkSteps) -> None:
    def hide_all(size: int) -> Callable[[], object]:
        document: Final[Document] = parse(
            f"<style>b {{ display: none }}{'em {margin: 0}' * size}</style><p>kept</p>{'<b></b>' * size}"
        )

        def hide() -> None:
            for element in document.select("body *"):
                if computed_style(element)["display"] == "none":
                    element.decompose()
            assert document.to_text() == "kept"

        return hide

    work_steps.assert_linear(hide_all, (250, 1000))


def test_computed_style_recompiles_after_new_attribute_name() -> None:
    paragraph = parse("<style>p[data-late] { color: red }</style><p></p>").select_one("p")
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
    paragraph = parse("<p>hi</p>").select_one("p")
    assert isinstance(paragraph, Element)
    with pytest.raises(TypeError):
        computed_style(paragraph.children[0])  # ty: ignore[invalid-argument-type]  # a text node is not an Element
    with pytest.raises(TypeError):
        computed_style("not a node")  # ty: ignore[invalid-argument-type]  # a non-node exercises the guard


def test_computed_style_with_no_stylesheet_is_all_initial() -> None:
    div = parse("<div></div>").select_one("div")
    assert isinstance(div, Element)
    assert computed_style(div)["visibility"] == "visible"


_CUSTOM_PROPERTY_BLOCKS: Final[list[pytest.ParameterSet]] = [
    pytest.param("--x: {a;b}", (("--x", "{a;b}"),), id="block-value"),
    pytest.param("--x: a{b;c}d; color: red", (("--x", "a{b;c}d"), ("color", "red")), id="block-between-tokens"),
    pytest.param("--x: {{a;b}}; color: red", (("--x", "{{a;b}}"), ("color", "red")), id="nested-blocks"),
    pytest.param("--x: {é;ü}; color: red", (("--x", "{é;ü}"), ("color", "red")), id="non-ascii-in-block"),
]


@pytest.mark.parametrize(("text", "expected"), _CUSTOM_PROPERTY_BLOCKS)
def test_parse_declarations_keeps_custom_property_block(text: str, expected: tuple[tuple[str, str], ...]) -> None:
    assert _pairs(StyleDeclaration.parse(text)) == expected


def _pairs(declaration: StyleDeclaration) -> tuple[tuple[str, str], ...]:
    return tuple((name, declaration[name]) for name in declaration)


@pytest.mark.parametrize(("text", "expected"), _CUSTOM_PROPERTY_BLOCKS)
def test_stylesheet_keeps_custom_property_block(text: str, expected: tuple[tuple[str, str], ...]) -> None:
    assert _pairs(StyleSheet(f"p {{ {text} }}").rules[0].style) == expected


def test_stylesheet_block_ends_only_at_its_own_closer() -> None:
    assert [rule.selector_text for rule in StyleSheet("p { --x: {a)} } q { color: red }").rules] == ["p", "q"]


def test_parse_declarations_keeps_deeply_nested_custom_property_block() -> None:
    value = f"{'{' * 70}a;b{'}' * 70}"
    assert _pairs(StyleDeclaration.parse(f"--x: {value}; color: red")) == (("--x", value), ("color", "red"))


_ESCAPED_DELIMITERS: Final[list[pytest.ParameterSet]] = [
    pytest.param(r"--x: \{; color: red", (("--x", r"\{"), ("color", "red")), id="escaped-opener"),
    pytest.param(r"--x: {a\};b}; color: red", (("--x", r"{a\};b}"), ("color", "red")), id="escaped-closer-in-block"),
    pytest.param(r"--x: a\;b; color: red", (("--x", r"a\;b"), ("color", "red")), id="escaped-semicolon"),
]


@pytest.mark.parametrize(("text", "expected"), _ESCAPED_DELIMITERS)
def test_parse_declarations_reads_escaped_delimiter_as_name(text: str, expected: tuple[tuple[str, str], ...]) -> None:
    assert _pairs(StyleDeclaration.parse(text)) == expected


@pytest.mark.parametrize(("text", "expected"), _ESCAPED_DELIMITERS)
def test_stylesheet_reads_escaped_delimiter_as_name(text: str, expected: tuple[tuple[str, str], ...]) -> None:
    assert _pairs(StyleSheet(f"p {{ {text} }}").rules[0].style) == expected


def test_stylesheet_at_rule_block_reads_escaped_closer_as_name() -> None:
    css = r"@media x { a\} { color: red } } q { color: blue }"
    assert [rule.selector_text for rule in StyleSheet(css).rules] == ["q"]


def test_computed_style_reads_declaration_after_custom_property_block() -> None:
    assert _style("<div></div>", css="div { --x: {a}; color: red }")["color"] == "red"


def test_stylesheet_attribute_selector_and_stray_bracket() -> None:
    sheet = StyleSheet('a[data-x="y"] { color: red } .c { width: 3) }')
    assert sheet.rules[0].selector_text == 'a[data-x="y"]'
    assert sheet.rules[1].style.get("width") == "3)"


def test_declaration_property_names_are_case_insensitive() -> None:
    assert _style("<div style='COLOR: RebeccaPurple'></div>")["color"] == "RebeccaPurple"


def test_computed_style_ignores_unknown_property() -> None:
    # "font" and "margink" are neither tracked longhands nor shorthands: both are dropped
    style = _style("<div></div>", css="div { font: 12px serif; margink: 9px; color: red }")
    assert (style["color"], style["margin-top"]) == ("red", "0")


@pytest.mark.parametrize(
    ("source", "name"),
    [
        pytest.param(r"co\lor: red", "color", id="simple-escape"),
        pytest.param(r"\63 olor: red", "color", id="hex-escape-with-space"),
        pytest.param(r"\000063olor: red", "color", id="six-digit-hex-escape"),
        pytest.param(r"\63: red", "c", id="hex-escape-at-name-end"),
        pytest.param(r"\63 1: red", "c1", id="hex-escape-consumes-one-space"),
        pytest.param(r"\0000631: red", "c1", id="hex-escape-stops-at-six-digits"),
        pytest.param(r"\4A: red", "j", id="uppercase-hex-escape"),
        pytest.param("\\63\r\nolor: red", "color", id="hex-escape-consumes-crlf"),
        pytest.param("\\63\rX: red", "cx", id="hex-escape-consumes-lone-cr"),
        pytest.param("co\\\x00lor: red", "co\ufffdlor", id="escaped-null"),
        pytest.param(r"\ : red", " ", id="escaped-space-name"),
        pytest.param(r" \63 olor  : red", "color", id="surrounding-whitespace"),
        pytest.param(r"--a\ : red", "--a ", id="escaped-trailing-space"),
        pytest.param(r"--a\  : red", "--a ", id="escaped-and-unescaped-trailing-space"),
        pytest.param(r"--a\\  : red", "--a\\", id="paired-backslashes-before-space"),
        pytest.param(r"\0x: red", "\ufffdx", id="null-escape"),
        pytest.param(r"\d800x: red", "\ufffdx", id="surrogate-escape"),
        pytest.param(r"\e000x: red", "\ue000x", id="non-surrogate-after-surrogate-range"),
        pytest.param(r"\110000x: red", "\ufffdx", id="out-of-range-escape"),
    ],
)
def test_parse_declarations_decodes_escaped_property_names(source: str, name: str) -> None:
    declaration = StyleDeclaration.parse(source)
    assert declaration.properties() == (name,)


def test_parse_declarations_serializes_escaped_property_name() -> None:
    declaration = StyleDeclaration.parse(r"--a\ : red")
    assert declaration.text == r"--a\ : red"


@pytest.mark.parametrize(
    "newline",
    [pytest.param("\n", id="lf"), pytest.param("\r", id="cr"), pytest.param("\f", id="ff")],
)
def test_parse_declarations_rejects_invalid_escape_in_property_name(newline: str) -> None:
    assert StyleDeclaration.parse(f"co\\{newline}lor: red").properties() == ()


def test_parse_declarations_rejects_invalid_escape_after_an_escape() -> None:
    assert StyleDeclaration.parse("co\\l\\\nor: red").properties() == ()


def test_parse_declarations_rejects_escape_after_invalid_escape() -> None:
    assert StyleDeclaration.parse("co\\\n\\lor: red").properties() == ()


def test_parse_declarations_rejects_trailing_escape_without_colon() -> None:
    assert StyleDeclaration.parse("co\\").properties() == ()


def test_parse_declarations_escaped_property_name_important() -> None:
    assert StyleDeclaration.parse(r"co\lor: red !important").important("color")


def test_parse_declarations_escaped_and_plain_duplicates() -> None:
    assert StyleDeclaration.parse(r"color: red; co\lor: blue").get("color") == "blue"


def test_stylesheet_decodes_escaped_property_name() -> None:
    assert StyleSheet(r"p { co\lor: red }").rules[0].style.get("color") == "red"


def test_computed_style_decodes_escaped_property_name_in_rule() -> None:
    assert _style("<div></div>", css=r"div { co\lor: red }")["color"] == "red"


def test_computed_style_decodes_escaped_property_name_inline() -> None:
    assert _style(r"<div style='co\lor: red'></div>")["color"] == "red"


def test_computed_style_shorthand_does_not_override_higher_priority_longhand() -> None:
    style = _style("<div id='a' style='margin: 5px'></div>", css="#a { margin-top: 9px !important }")
    assert (style["margin-top"], style["margin-right"]) == ("9px", "5px")


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        pytest.param("inherit", ("3px", "dashed", "blue"), id="inherit"),
        pytest.param("initial", ("medium", "none", "currentcolor"), id="initial"),
        pytest.param("unset", ("medium", "none", "currentcolor"), id="unset"),
        pytest.param("revert", ("medium", "none", "currentcolor"), id="revert"),
        pytest.param("revert-layer", ("medium", "none", "currentcolor"), id="revert-layer"),
    ],
)
def test_computed_style_border_css_wide_keyword_sets_every_longhand(keyword: str, expected: tuple[str, ...]) -> None:
    paragraph = parse(f"<div style='border: 3px dashed blue'><p style='border: {keyword}'></p></div>").select_one("p")
    assert isinstance(paragraph, Element)
    style = computed_style(paragraph)
    for side in ("top", "right", "bottom", "left"):
        assert (style[f"border-{side}-width"], style[f"border-{side}-style"], style[f"border-{side}-color"]) == expected


@pytest.mark.parametrize(
    ("shorthand", "longhand"),
    [
        pytest.param("border", "border-top", id="border"),
        pytest.param("border-top", "border-top", id="border-top"),
        pytest.param("outline", "outline", id="outline"),
    ],
)
def test_computed_style_css_wide_keyword_cannot_mix_with_components(shorthand: str, longhand: str) -> None:
    style = _style(f"<div style='{shorthand}: inherit solid'></div>")
    assert style[f"{longhand}-style"] == "none"


def test_computed_style_distributive_css_wide_keyword_cannot_mix_with_components() -> None:
    style = _style("<div style='margin: inherit 2px'></div>")
    assert (style["margin-top"], style["margin-right"]) == ("0", "0")


@pytest.mark.parametrize("keyword", ["inherit", "initial", "unset", "revert", "revert-layer"])
@pytest.mark.parametrize(
    "value",
    ["{keyword} blue", "blue {keyword}", "blue,{keyword}", "blue/{keyword}", "blue+{keyword}", "{keyword}(blue)"],
)
def test_computed_style_longhand_mixed_css_wide_keyword_does_not_override(keyword: str, value: str) -> None:
    style = _style("<p></p>", css=f"p {{ color: red; color: {value.format(keyword=keyword)} }}", tag="p")
    assert style["color"] == "red"


def test_computed_style_longhand_mixed_css_wide_keyword_keeps_earlier_declaration() -> None:
    assert _style("<div style='position: relative; position: initial static'></div>")["position"] == "relative"


def test_computed_style_longhand_mixed_important_css_wide_keyword_keeps_valid_declaration() -> None:
    assert _style("<div style='position: relative; position: static revert !important'></div>")["position"] == (
        "relative"
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("blue) initial", id="closing-paren-before"),
        pytest.param("binherit initial", id="letter-before"),
        pytest.param("Binherit initial", id="uppercase-before"),
        pytest.param("1inherit initial", id="digit-before"),
        pytest.param("-inherit initial", id="hyphen-before"),
        pytest.param("_inherit initial", id="underscore-before"),
        pytest.param("éinherit initial", id="non-ascii-before"),
        pytest.param("inherita initial", id="letter-after"),
        pytest.param("inheriTa initial", id="uppercase-after"),
        pytest.param("inherit2 initial", id="digit-after"),
        pytest.param("iNITIAL blue", id="uppercase-keyword"),
        pytest.param("i~ initial", id="tilde-after"),
        pytest.param("i_ initial", id="underscore-after"),
        pytest.param("i: initial", id="colon-after"),
    ],
)
def test_computed_style_longhand_css_wide_keyword_token_boundaries(value: str) -> None:
    assert _style("<p></p>", css=f"p {{ color: red; color: {value} }}", tag="p")["color"] == "red"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("inherit_foo", id="underscore-suffix"),
        pytest.param("inherité", id="non-ascii-suffix"),
        pytest.param("inherit-foo", id="hyphen-suffix"),
        pytest.param("indigo", id="unrelated-word"),
        pytest.param("unit", id="unit"),
        pytest.param("unite", id="unite"),
    ],
)
def test_computed_style_longhand_css_wide_keyword_prefix_is_identifier(value: str) -> None:
    assert _style("<p></p>", css=f"p {{ list-style-type: {value} }}", tag="p")["list-style-type"] == value


def test_computed_style_longhand_css_wide_keyword_inside_function_remains_value() -> None:
    assert _style("<div style='background-image: var(--image, initial)'></div>")["background-image"] == (
        "var(--image, initial)"
    )


@pytest.mark.parametrize("quote", ['"', "'"])
def test_computed_style_longhand_css_wide_keyword_inside_string_remains_value(quote: str) -> None:
    assert (
        _style("<div></div>", css=f"div {{ list-style-type: {quote}red inherit blue{quote} }}")["list-style-type"]
        == f"{quote}red inherit blue{quote}"
    )


def test_computed_style_longhand_css_wide_keyword_inside_escaped_string_remains_value() -> None:
    assert _style("<div></div>", css=r'div { list-style-type: "red \" inherit blue" }')["list-style-type"] == (
        '"red \\" inherit blue"'
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(r"\initial", "canvastext", id="initial"),
        pytest.param(r"\inherit", "blue", id="inherit"),
        pytest.param(r"\unset", "blue", id="unset"),
        pytest.param(r"\revert", "blue", id="revert"),
        pytest.param(r"revert\-layer", "blue", id="revert-layer"),
        pytest.param(r"in\69tial", "canvastext", id="hex-escape"),
        pytest.param(r"\000069nitial", "canvastext", id="six-digit-escape"),
        pytest.param(r"\69 nitial", "canvastext", id="escape-whitespace"),
    ],
)
def test_computed_style_escaped_css_wide_keyword(value: str, expected: str) -> None:
    style = _style(f'<div style="color:blue"><p style="color:red;color:{value}"></p></div>', tag="p")
    assert style["color"] == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(r"blue \initial", id="space"),
        pytest.param(r"blue,\initial", id="comma"),
        pytest.param(r"blue/\initial", id="slash"),
        pytest.param(r"blue+\initial", id="plus"),
        pytest.param(r"blue \69nitial", id="hex-escape"),
    ],
)
def test_computed_style_mixed_escaped_css_wide_keyword_keeps_earlier_value(value: str) -> None:
    assert _style(f'<p style="color:red;color:{value}"></p>', tag="p")["color"] == "red"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(r"var(--color, \initial)", id="fallback"),
        pytest.param(r"var(--color\), \initial)", id="escaped-paren"),
    ],
)
def test_computed_style_escaped_css_wide_keyword_inside_function_remains_value(value: str) -> None:
    assert _style(f'<p style="color:red;color:{value}"></p>', tag="p")["color"] == value


def test_computed_style_escaped_css_wide_keyword_inside_string_remains_value() -> None:
    value = r'"\initial"'
    assert _style(f"<p style='list-style-type:{value}'></p>", tag="p")["list-style-type"] == value


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(r"\blue", id="other-ident"),
        pytest.param(r"\000069", id="terminal-hex-escape"),
        pytest.param(r"foo\69", id="terminal-short-hex-escape"),
        pytest.param("foo\\69\\", id="terminal-backslash"),
        pytest.param(f"\\initial{'x' * 100}", id="long-ident"),
    ],
)
def test_computed_style_escaped_nonwide_ident_remains_value(value: str) -> None:
    assert _style(f'<p style="list-style-type:{value}"></p>', tag="p")["list-style-type"] == value


@pytest.mark.parametrize(
    ("declaration", "property_name", "expected"),
    [
        pytest.param(r"all:\initial", "color", "canvastext", id="all"),
        pytest.param(r"margin:\initial", "margin-top", "0", id="distributive"),
        pytest.param(r"border:\initial", "border-top-style", "none", id="box"),
    ],
)
def test_computed_style_escaped_css_wide_keyword_in_shorthand(
    declaration: str, property_name: str, expected: str
) -> None:
    assert _style(f'<p style="{declaration}"></p>', tag="p")[property_name] == expected


def test_computed_style_all_resets_supported_properties_except_direction() -> None:
    style = _style("<div style='color: red; margin: 9px; direction: rtl; all: initial'></div>")
    assert (style["color"], style["margin-top"], style["direction"]) == ("canvastext", "0", "rtl")


def test_computed_style_all_obeys_source_order_and_importance() -> None:
    style = _style("<div style='color: red !important; all: initial; margin: 9px'></div>")
    assert (style["color"], style["margin-top"]) == ("red", "9px")


def test_computed_style_all_inherit_and_unset() -> None:
    document = parse(
        "<div style='color: red; margin: 9px'><p style='all: inherit'></p><b style='all: unset'></b></div>"
    )
    paragraph = document.select_one("p")
    bold = document.select_one("b")
    assert isinstance(paragraph, Element)
    assert isinstance(bold, Element)
    assert (computed_style(paragraph)["color"], computed_style(paragraph)["margin-top"]) == ("red", "9px")
    assert (computed_style(bold)["color"], computed_style(bold)["margin-top"]) == ("red", "0")


def test_computed_style_all_rejects_non_keyword_value() -> None:
    style = _style("<div style='color: red; all: blue'></div>")
    assert style["color"] == "red"


def test_computed_style_important_keyword_is_a_value_when_not_flagged() -> None:
    assert _style("<div></div>", css="div { color: red important }")["color"] == "red important"


def test_computed_style_reads_five_style_sheets() -> None:
    sheets = "".join(f"<style>p {{ color: c{index} }}</style>" for index in range(5))
    paragraph = parse(f"<html><head>{sheets}</head><body><p></p></body></html>").select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "c4"


def test_computed_style_on_detached_element_uses_inline_and_initials() -> None:
    style = computed_style(E("div", {"style": "color: red"}))
    assert (style["color"], style["display"]) == ("red", "inline")


_ESCAPED_COMMENT_STARTS: Final[list[pytest.ParameterSet]] = [
    pytest.param(r"--x: a\/*b; color: red", (("--x", r"a\/*b"), ("color", "red")), id="unclosed"),
    pytest.param(r"--x: a\/*b*/c; color: red", (("--x", r"a\/*b*/c"), ("color", "red")), id="closed"),
    pytest.param("--x: a\\", (("--x", "a\\"),), id="backslash-at-end"),
]


@pytest.mark.parametrize(("text", "expected"), _ESCAPED_COMMENT_STARTS)
def test_parse_declarations_keeps_escaped_comment_start(text: str, expected: tuple[tuple[str, str], ...]) -> None:
    declaration = StyleDeclaration.parse(text)
    assert tuple((name, declaration[name]) for name in declaration) == expected


@pytest.mark.parametrize(("text", "expected"), _ESCAPED_COMMENT_STARTS)
def test_stylesheet_keeps_escaped_comment_start(text: str, expected: tuple[tuple[str, str], ...]) -> None:
    declaration = StyleSheet(f"p {{ {text}").rules[0].style
    assert tuple((name, declaration[name]) for name in declaration) == expected


def test_comment_and_escape_edge_cases() -> None:
    assert StyleDeclaration.parse('content: "a\\"b"').get("content") == '"a\\"b"'
    assert StyleDeclaration.parse('content: "a\\').get("content") == '"a\\'
    assert StyleSheet("a { b: c/d }").rules[0].style.get("b") == "c/d"
    assert StyleSheet("a { b: c }/").rules[0].style.get("b") == "c"
    assert StyleSheet("a { b: c }/*").rules[0].style.get("b") == "c"
    assert StyleSheet("a { /* x * y */ b: c }").rules[0].style.get("b") == "c"


@pytest.mark.parametrize(
    ("value", "important", "line_break"),
    [
        pytest.param("url\\", "", "\n", id="one-backslash"),
        pytest.param("url\\\\", "", "", id="two-backslashes"),
        pytest.param("url\\\\\\", "", "\n", id="three-backslashes"),
        pytest.param("url\\", " !important", "", id="important"),
    ],
)
def test_declaration_text_preserves_trailing_escape_before_separator(
    value: str, important: str, line_break: str
) -> None:
    first = StyleDeclaration.parse(f"background: {value}\n{important}; color: red")
    assert first.text == f"background: {value}{line_break}{important}; color: red"
    second = StyleDeclaration.parse(first.text)
    assert second.get("background") == value
    assert second.get("color") == "red"
    assert second.text == first.text


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
    root = parse("<html><head><style>html { color: unset }</style></head><body></body></html>").root
    assert isinstance(root, Element)
    assert computed_style(root)["color"] == "canvastext"


def test_computed_style_alternative_with_lower_id_specificity_loses() -> None:
    # the id alternative sets the best specificity; the class alternative then compares below it
    style = _style("<div class='x' id='a'></div>", css="#a, .x { color: teal }")
    assert style["color"] == "teal"


def test_the_text_keeps_an_empty_constructed_value() -> None:
    assert StyleDeclaration((("a", "", False), ("b", "2", False))).text == "a: ; b: 2"


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
    first: Final[ComputedStyle] = computed_style(document.select("div")[0])
    second: Final[ComputedStyle] = computed_style(document.select("div")[1])
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
    document: Final[Document] = parse(f"""<style>{css}</style>{'<div class="hit" id="target"></div>' * 3}""")
    assert [computed_style(node)["color"] for node in document.select("div")] == [expected] * 3


def test_computed_style_keeps_specificity_within_each_sheet() -> None:
    document: Final[Document] = parse(
        '<style>#target,#missing{color:red}</style><style>.hit{color:blue}</style><div class="hit" id="target"></div>'
    )
    assert computed_style(document.select("div")[0])["color"] == "red"


def test_computed_style_rebuilds_specificity_after_selector_change() -> None:
    document: Final[Document] = parse(
        '<style>.hit,#missing{color:red}div.hit{color:blue}</style><div class="hit" id="target"></div>'
    )
    node: Final[Element] = document.select("div")[0]
    before: Final[str] = computed_style(node)["color"]
    text: Final[Node] = document.select("style")[0].children[0]
    assert isinstance(text, Text)
    text.data = "#target{color:red}div.hit{color:blue}"
    assert (before, computed_style(node)["color"]) == ("blue", "red")


def test_computed_style_rereads_dir_auto_after_text_change() -> None:
    # both rules resolve the paragraph through the div; the answer kept for the first must not outlive the edit
    document: Final[Document] = parse(
        "<style>:dir(ltr){color:blue}p:dir(rtl){color:red}</style><div dir=auto>x<p></p></div>"
    )
    node: Final[Element] = document.select("p")[0]
    before: Final[str] = computed_style(node)["color"]
    text: Final[Node] = document.select("div")[0].children[0]
    assert isinstance(text, Text)
    text.data = "א"
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
    document: Final[Document] = parse(
        f"<!doctype html><style>{selector}{{color:red}}div{{color:blue}}</style>"
        '<div class="hit" id="target"><span></span></div>'
    )
    assert computed_style(document.select("div")[0])["color"] == expected


@pytest.mark.parametrize(
    ("doctype", "expected"),
    [pytest.param("", "red", id="quirks"), pytest.param("<!doctype html>", "blue", id="standards")],
)
def test_computed_style_predicate_order_preserves_quirks(doctype: str, expected: str) -> None:
    document: Final[Document] = parse(
        f'{doctype}<style>:has(span).hit{{color:red}}div{{color:blue}}</style><div class="HIT"><span></span></div>'
    )
    assert computed_style(document.select("div")[0])["color"] == expected


def test_computed_style_predicate_order_preserves_foreign_elements() -> None:
    document: Final[Document] = parse(
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
    document: Final[Document] = parse(
        f'<style>div{{color:blue}}div{selector}{{color:red}}</style>{"<div>" * 64}<span class="hit"></span>'
        f"{'</div>' * 64}"
    )
    nodes: Final[tuple[Element, ...]] = tuple(document.select("div"))
    for node in reversed(nodes):
        computed_style(node)
    assert computed_style(nodes[0])["color"] == expected


def test_computed_style_has_refreshes_after_attribute_change() -> None:
    document: Final[Document] = parse(
        f"<style>div{{color:blue}}div:has(.hit){{color:red}}</style>{'<div>' * 64}<span></span>{'</div>' * 64}"
    )
    outer: Final[Element] = document.select("div")[0]
    target: Final[Element] = document.select("span")[0]
    before: Final[str] = computed_style(outer)["color"]
    target.attrs["class"] = "hit"
    assert (before, computed_style(outer)["color"]) == ("blue", "red")


def test_computed_style_has_refreshes_after_insertion() -> None:
    document: Final[Document] = parse(
        f"<style>div{{color:blue}}div:has(.hit){{color:red}}</style>{'<div>' * 64}<span></span>{'</div>' * 64}"
    )
    outer: Final[Element] = document.select("div")[0]
    target: Final[Element] = document.select("span")[0]
    before: Final[str] = computed_style(outer)["color"]
    target.append(Element("b", {"class": "hit"}))
    assert (before, computed_style(outer)["color"]) == ("blue", "red")


def test_computed_style_has_refreshes_after_removal() -> None:
    document: Final[Document] = parse(
        f'<style>div{{color:blue}}div:has(.hit){{color:red}}</style>{"<div>" * 64}<span class="hit"></span>'
        f"{'</div>' * 64}"
    )
    outer: Final[Element] = document.select("div")[0]
    target: Final[Element] = document.select("span")[0]
    before: Final[str] = computed_style(outer)["color"]
    target.extract()
    assert (before, computed_style(outer)["color"]) == ("red", "blue")


def test_computed_style_has_refreshes_after_text_change() -> None:
    document: Final[Document] = parse(
        f"<style>div{{color:blue}}div:has(span:empty){{color:red}}</style>{'<div>' * 64}<span></span>{'</div>' * 64}"
    )
    outer: Final[Element] = document.select("div")[0]
    target: Final[Element] = document.select("span")[0]
    before: Final[str] = computed_style(outer)["color"]
    target.append(Text("filled"))
    assert (before, computed_style(outer)["color"]) == ("red", "blue")


def test_computed_style_has_refreshes_after_stylesheet_change() -> None:
    document: Final[Document] = parse(
        f"<style>div{{color:blue}}div:has(.hit){{color:red}}</style>{'<div>' * 64}<span></span>{'</div>' * 64}"
    )
    outer: Final[Element] = document.select("div")[0]
    before: Final[str] = computed_style(outer)["color"]
    style_text: Final[Node] = document.select("style")[0].children[0]
    assert isinstance(style_text, Text)
    style_text.data = "div{color:red}"
    assert (before, computed_style(outer)["color"]) == ("blue", "red")


def test_computed_style_first_legend_refreshes_after_insert() -> None:
    # both rules ask whether the control's legend is the fieldset's first; the answer is kept between them and must
    # not outlive a legend inserted in front
    document: Final[Document] = parse(
        "<style>input:enabled{color:red}input:disabled{color:blue}</style>"
        "<fieldset disabled><legend><input></legend></fieldset>"
    )
    control: Final[Element] = document.select("input")[0]
    before: Final[str] = computed_style(control)["color"]
    document.select("legend")[0].insert_before(Element("legend"))
    assert (before, computed_style(control)["color"]) == ("red", "blue")


@pytest.mark.parametrize(
    ("case_index", "expected"),
    [
        pytest.param(0, ("blue",) * 512, id="deep-missing"),
        # past the depth cap the parser attaches deeper nodes beside their deepest allowed ancestor, so the last three
        # divs hold no match
        pytest.param(1, ("red",) * 509 + ("blue",) * 3, id="deep-matching"),
        pytest.param(2, ("red", "blue") * 2_048, id="wide-positional"),
        pytest.param(3, ("red",) * 8, id="shallow-has"),
    ],
)
def test_computed_style_selector_shared_inputs(case_index: int, expected: tuple[str, ...]) -> None:
    source: Final[Case] = INPUTS["computed-style-selectors"]()[case_index][1]
    assert isinstance(source, str)
    document: Final[Document] = parse(source)
    assert tuple(computed_style(node)["color"] for node in document.select("div")) == expected


def test_computed_style_repeated_missing_has_keeps_memory_flat(retained_bytes: RetainedBytes) -> None:
    document: Final[Document] = parse(
        f"<style>div{{color:blue}}div:has(.absent){{color:red}}</style>{'<div>' * 64}<span></span>{'</div>' * 64}"
    )
    nodes: Final[tuple[Element, ...]] = tuple(document.select("div"))[:3]
    _compute_style_rounds(nodes, 1)
    before: Final[int] = retained_bytes()
    _compute_style_rounds(nodes, 4_096)
    assert retained_bytes() - before < 32_768
    assert tuple(computed_style(node)["color"] for node in nodes) == ("blue",) * 3


def _compute_style_rounds(nodes: tuple[Element, ...], rounds: int) -> None:
    for _ in range(rounds):
        for node in nodes:
            computed_style(node)


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param("rules", id="rules"),
        pytest.param("selector-list", id="selector-list"),
        pytest.param("compound", id="compound"),
        pytest.param("arguments", id="arguments"),
    ],
)
def test_computed_style_has_memo_is_bounded(shape: str, retained_bytes: RetainedBytes) -> None:
    # every :has() argument keys one answer per element, so the memo grows with arguments times elements
    arguments: Final[int] = 13
    sheet: Final[str] = {
        "rules": ":has(x){}" * arguments,
        "selector-list": f"{','.join([':has(x)'] * arguments)}{{}}",
        "compound": f"{':not(:has(x))' * arguments}{{}}",
        "arguments": f":has({','.join(['x'] * arguments)}){{}}",
    }[shape]
    elements: Final[int] = (1 << 21) // arguments + 8_192
    document: Final[Document] = parse(f"<style>{sheet}:has(p){{color:red}}</style>{'<div>' * 28}{'<p>' * elements}")
    root: Final[Element | None] = document.select_one("html")
    assert isinstance(root, Element)
    before: Final[int] = retained_bytes()
    assert computed_style(root)["color"] == "red"
    assert retained_bytes() - before < 1 << 20


@pytest.mark.parametrize(
    ("rule", "target", "body"),
    [
        pytest.param(":has(~ s ~ em)", "#t", "<h1 id=t></h1>{}", id="later-chain"),
        pytest.param(":has(~ em:has(~ s ~ em))", "#t", "<h1 id=t></h1>{}", id="nested-has"),
        pytest.param(":has(s ~ em)", "#t", "<section id=t>{}</section>", id="descendant-lead"),
        pytest.param(":has(> s ~ em)", "#t", "<section id=t>{}</section>", id="child-lead"),
        pytest.param(":has(~ s:not(:scope) ~ em)", "#t", "<h1 id=t></h1>{}", id="scope-in-compound"),
        pytest.param(":has(~ s:is(:scope ~ *) ~ em)", "#t", "<h1 id=t></h1>{}", id="scope-in-walk"),
        pytest.param(":has(:default[hidden])", "form", "<form>{0}{1}</form>", id="default-button"),
    ],
)
def test_computed_style_sibling_rule_in_linear_time(rule: str, target: str, body: str, work_steps: WorkSteps) -> None:
    def style(size: int) -> Callable[[], object]:
        runs = ("<em></em>" * size, "<button></button>" * size)
        node = parse(f"<style>{f'{rule}{{color:red}}' * 3}</style>{body.format(*runs)}").select_one(target)
        assert isinstance(node, Element)
        return lambda: computed_style(node)

    work_steps.assert_linear(style, (600, 2400))


@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("em:has(~ s)", id="later-sibling"),
        pytest.param("em:has(~ s ~ em)", id="later-chain"),
        pytest.param("em:has(~ s:not(:scope))", id="scope-in-compound"),
        pytest.param("s ~ em", id="earlier-sibling"),
        pytest.param(":scope ~ em", id="scope-earlier-sibling"),
        pytest.param("em:has(~ button:is(:scope ~ *))", id="scope-offset"),
        pytest.param("em:has(~ button:not(:scope + *))", id="scope-offset-negated"),
        pytest.param("em:has(~ :is(:scope ~ * ~ *) ~ s)", id="scope-offset-left"),
        pytest.param("em:has(~ button:is(:scope + em + *))", id="scope-fixed"),
        pytest.param("em:has(~ button:is(:scope ~ em ~ *))", id="scope-between"),
        pytest.param("em:has(~ button:nth-child(2 of :scope ~ *))", id="scope-of-index"),
        pytest.param("em:has(~ button:nth-child(odd of :scope ~ *))", id="scope-of-step"),
        pytest.param("em:has(~ button:nth-last-child(1 of :scope ~ *))", id="scope-of-from-end"),
        pytest.param("em:has(~ button:is(:not(:scope) ~ *))", id="not-scope-chain"),
        pytest.param("em:has(+ em + button:is(:scope ~ em ~ *))", id="scope-between-adjacent-lead"),
        pytest.param("em:has(~ button:is(:scope + em ~ *))", id="scope-adjacent-between"),
        pytest.param("em:has(:scope:not(.k) ~ button:is(em ~ :scope ~ em ~ *))", id="scope-pinned-in-chain"),
        pytest.param(
            "em:has(~ button:not(:nth-child(3n+1 of :scope ~ *)):not(:nth-child(2n of :scope + * ~ *)))",
            id="scope-of-steps-negated",
        ),
        pytest.param("button:default", id="default"),
    ],
)
def test_computed_style_every_sibling_in_linear_time(rule: str, work_steps: WorkSteps) -> None:
    def style(size: int) -> Callable[[], object]:
        nodes = parse(
            f"<style>{rule}{{color:red}}</style><form>{'<em></em>' * size}{'<button></button>' * size}"
        ).select("form *")
        return lambda: [computed_style(node) for node in nodes]

    work_steps.assert_linear(style, (350, 1400))


@pytest.mark.parametrize("order", [pytest.param(1, id="forward"), pytest.param(-1, id="reverse")])
@pytest.mark.parametrize(
    "climbing",
    [pytest.param("", id="shallow"), pytest.param(":is(q u) b{color:green}", id="deep-matcher-for-every-rule")],
)
@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("em:has(~ s ~ em)", id="later-chain"),
        pytest.param("em:has(~ em:has(~ s))", id="nested-has"),
        pytest.param("ol:has(s ~ em)", id="descendant-lead"),
        pytest.param("s ~ em", id="earlier-sibling"),
        pytest.param("em:has(~ s:not(:scope))", id="scope-in-compound"),
        pytest.param("em:has(~ em:is(:scope + *))", id="scope-in-walk"),
        pytest.param("em:has(~ em:nth-child(1 of :scope ~ s))", id="scope-in-of"),
        pytest.param("em:has(~ em:nth-child(2 of em) ~ s)", id="of-without-scope"),
        pytest.param("em:has(~ s ~ em:nth-last-child(2 of em))", id="last-of-without-scope"),
        pytest.param("em:has(~ em:is(:not(:scope + *)) ~ s)", id="scope-in-nested-walk"),
        pytest.param("em:has(~ em:is(s + em) ~ s)", id="nested-walk-without-scope"),
        pytest.param(":not(:scope) ~ em", id="scope-earlier-sibling"),
        pytest.param("em:has(~ em:is(:scope ~ * ~ *))", id="scope-offset"),
        pytest.param("em:has(~ s:not(:scope + *))", id="scope-offset-negated"),
        pytest.param("em:has(~ button:is(:scope + * ~ button))", id="scope-offset-typed"),
        pytest.param("em:has(~ :is(:scope + *) ~ s)", id="scope-offset-left"),
        pytest.param("em:has(~ em:is(:scope + em + s))", id="scope-fixed"),
        pytest.param("em:has(~ em:where(:scope ~ *, s))", id="scope-offset-or"),
        pytest.param("em:has(+ em:is(:scope + *) ~ s)", id="scope-offset-adjacent-lead"),
        pytest.param("em:has(~ s ~ button:is(:scope ~ s ~ *))", id="scope-between"),
        pytest.param("ol:has(> em:nth-child(2 of :scope ~ *))", id="scope-in-of-under-child"),
        pytest.param("ol:has(> em:nth-last-child(2 of s, button))", id="of-under-child"),
        pytest.param("ol:has(> em:is(em, s ~ em))", id="list-under-child"),
        pytest.param("ol:has(> em:is(:not(:scope ~ s)))", id="scope-walk-under-child"),
        pytest.param("em:has(~ em ~ s:is(:scope ~ * ~ * ~ * ~ *))", id="scope-offset-chain-of-two"),
        pytest.param("button:default", id="default"),
    ],
)
def test_computed_style_sibling_rule_agrees_with_matches(rule: str, climbing: str, order: int) -> None:
    # runs longer than the 128 siblings a walk passes before it keeps a record of the run, on both sides of the one <s>
    document: Final[Document] = parse(
        f"<style>*{{color:blue}}{climbing}{rule}{{color:red}}</style>"
        f"<form><ol>{'<em></em>' * 140}<s></s>{'<em></em>' * 140}<button></button><button></button></ol></form>"
    )
    nodes: Final[list[Element]] = document.select("form, form *")[::order]
    assert [computed_style(node)["color"] for node in nodes] == [
        "red" if node.matches(rule) else "blue" for node in nodes
    ]


@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("em:has(> b:has(> s):is(:scope > *))", id="nested-has-of-scope-child"),
        pytest.param("em:has(> x:nth-last-child(1 of :scope > *) > b)", id="of-scope-child-from-end"),
        pytest.param("em:has(> x:nth-child(1 of :scope > *) > b)", id="of-scope-child-first"),
    ],
)
def test_computed_style_has_argument_with_nested_has_agrees_with_matches(rule: str) -> None:
    document: Final[Document] = parse(
        f"<style>*{{color:blue}}{rule}{{color:red}}</style><div><em><b><s></s></b></em><em><x><b></b></x></em></div>"
    )
    nodes: Final[list[Element]] = document.select("em")
    colors: Final[list[str]] = [computed_style(node)["color"] for node in nodes]
    assert colors == ["red" if node.matches(rule) else "blue" for node in nodes]
    assert "red" in colors


# one run of 216 siblings, i b x s by letter and an upper-case letter for class=k
_SWEPT_RUN: Final[str] = f"{'ibxIsxBixibIsbxxsiBxbiIxsbxixs' * 7}ibxIsx"
_SWEPT_TAGS: Final[dict[str, str]] = {letter: f"<{letter.lower()}></{letter.lower()}>" for letter in "ibxs"} | {
    letter: f"<{letter.lower()} class=k></{letter.lower()}>" for letter in "IB"
}


def _alternatives(head: str, step: str, counts: range, tail: str = "") -> str:
    return ", ".join(f"{head}{step * count}{tail}" for count in counts)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        pytest.param(
            "i:has(~ x:is(:scope ~ b ~ *))",
            lambda r, a, y: _between(r, a, y, "b"),
            id="scope-between",
        ),
        pytest.param("i:has(~ x:nth-child(2 of :scope ~ *))", lambda _r, a, y: y == a + 2, id="of-index"),
        pytest.param(
            "i:has(~ x:nth-child(odd of :scope ~ *))",
            lambda _r, a, y: (y - a) % 2,
            id="of-step",
        ),
        pytest.param("i:has(~ x:nth-last-child(1 of :scope ~ *))", lambda r, _a, y: y == len(r) - 1, id="of-from-end"),
        pytest.param(
            "i:has(~ x:nth-last-child(2 of :scope + *, :scope ~ * ~ * ~ *))",
            lambda r, a, y: _of_from_end(r, a, y, lambda d: d == 1 or d >= 3) == 2,
            id="of-from-end-exact",
        ),
        pytest.param("i:has(~ x:is(:not(:scope) ~ *))", lambda _r, _a, y: y >= 2, id="not-scope"),
        pytest.param(
            "i:has(~ x:is(:not(:scope) + b ~ *))",
            lambda r, a, y: any(r[p] == "b" and p - 1 != a for p in range(1, y)),
            id="not-scope-adjacent",
        ),
        pytest.param(
            "i:has(+ b + x:is(:scope ~ b ~ *))",
            lambda r, a, y: y == a + 2 and r[a + 1] == "b",
            id="scope-between-adjacent-lead",
        ),
        pytest.param(
            "i:has(~ x:is(:scope + b ~ *))",
            lambda r, a, y: r[a + 1] == "b" and y > a + 1,
            id="scope-adjacent-between",
        ),
        pytest.param(
            "i:has(:scope.k ~ x:is(b ~ :scope ~ s ~ *))",
            lambda r, a, y: _SWEPT_RUN[a].isupper() and "b" in r[:a] and _between(r, a, y, "s"),
            id="scope-pinned-in-chain",
        ),
        pytest.param(
            "i:has(~ x:is(i ~ :scope + b + x ~ *))",
            lambda r, a, y: "i" in r[:a] and r[a + 1 : a + 3] == "bx" and y > a + 2,
            id="scope-in-chain-adjacent",
        ),
        pytest.param(
            "i:has(~ s ~ x:is(:scope ~ b + i ~ x ~ *))",
            lambda r, a, y: _between(r, a, y, "s") and -1 < r.find("bi", a + 1) <= r.rfind("x", 0, y) - 2,
            id="scope-chain-past-chain",
        ),
        pytest.param(
            "i:has(~ x:is(:scope ~ b ~ *) ~ s)",
            lambda r, a, y: _between(r, a, y, "b") and "s" in r[y + 1 :],
            id="scope-between-then-later",
        ),
        pytest.param(
            "i:has(~ x:is(:scope, :scope ~ s ~ *))",
            lambda r, a, y: _between(r, a, y, "s"),
            id="scope-or-chain",
        ),
        pytest.param(
            "i:has(~ x:not(:nth-child(3n+1 of :scope ~ *)):not(:nth-child(2n of :scope ~ *)))",
            lambda _r, a, y: (y - a) % 3 != 1 and (y - a) % 2,
            id="of-steps-negated",
        ),
        pytest.param(
            "i:has(~ x:not(:nth-child(2n of :scope ~ *)):not(:nth-child(2n of :scope ~ *)))",
            lambda _r, a, y: (y - a) % 2,
            id="of-step-negated-twice",
        ),
        pytest.param(
            "i:has(~ x:not(:nth-child(2n of :scope ~ *)):not(:nth-child(70n of :scope ~ *)))",
            lambda _r, a, y: (y - a) % 2 and (y - a) % 70,
            id="of-steps-negated-past-the-spans",
        ),
        pytest.param(
            "i:has(~ x:not(:nth-child(2n of :scope ~ *)):not(:nth-child(2n+1 of :scope ~ *)))",
            lambda _r, _a, _y: False,
            id="of-steps-negated-apart",
        ),
        pytest.param(
            "i:has(~ x:nth-child(2n of :scope ~ *):not(:nth-child(2n of :scope ~ *)))",
            lambda _r, _a, _y: False,
            id="of-step-and-not",
        ),
        pytest.param(
            "i:has(~ x:nth-child(2n of :scope ~ *):not(:nth-child(2n+1 of :scope ~ *)))",
            lambda _r, a, y: (y - a) % 2 == 0,
            id="of-step-and-not-apart",
        ),
        pytest.param(
            "i:has(~ x:nth-child(2n of :scope ~ *):nth-child(3n+1 of :scope ~ *))",
            lambda _r, a, y: (y - a) % 6 == 4,
            id="of-steps-together",
        ),
        pytest.param(
            "i:has(~ x:nth-child(-n+3 of :scope ~ *))",
            lambda _r, a, y: y - a <= 3,
            id="of-countdown",
        ),
        pytest.param(
            "i:has(~ x:nth-child(2 of :scope + * + *, :scope + *, :scope + *, :scope ~ * ~ * ~ *,"
            " :scope ~ * ~ * ~ * ~ *, :scope + * + * + * + *))",
            lambda _r, a, y: y == a + 2,
            id="of-index-past-exact",
        ),
        pytest.param(
            "i:has(~ x:nth-last-child(1 of :scope + *, :scope + * + * + * + *))",
            lambda r, a, y: _of_from_end(r, a, y, lambda d: d in {1, 4}) == 1,
            id="of-from-end-exact-only",
        ),
        pytest.param(
            "i:has(~ x:nth-last-child(3 of :scope + *, :scope ~ * ~ * ~ * ~ * ~ *))",
            lambda r, a, y: _of_from_end(r, a, y, lambda d: d == 1 or d >= 5) == 3,
            id="of-from-end-exact-before-far",
        ),
        pytest.param(
            "i:has(~ x:is(:scope ~ b ~ *, s ~ *))",
            lambda r, a, y: _between(r, a, y, "b") or "s" in r[:y],
            id="scope-between-or-static",
        ),
        pytest.param(
            "i:has(~ x:nth-child(2n of :scope ~ *):nth-child(2n+1 of :scope ~ *).k)",
            lambda _r, _a, _y: False,
            id="of-apart-typed",
        ),
        pytest.param(
            "i:has(~ x:not(:scope.k ~ b ~ *))",
            lambda r, a, y: not (_SWEPT_RUN[a].isupper() and _between(r, a, y, "b")),
            id="scope-pinned-negated",
        ),
        pytest.param(
            "i:has(~ x:is(:scope.k ~ b ~ *, i:scope ~ s ~ *))",
            lambda r, a, y: (_SWEPT_RUN[a].isupper() and _between(r, a, y, "b")) or _between(r, a, y, "s"),
            id="scope-pinned-twice",
        ),
        pytest.param(
            "i:has(~ x:is(:not(:scope).k ~ *))",
            lambda _r, a, y: any(_SWEPT_RUN[p].isupper() and p != a for p in range(y)),
            id="not-scope-typed",
        ),
        pytest.param(
            "i:has(~ x:is(:scope + b + *, :scope ~ s ~ *))",
            lambda r, a, y: (y == a + 2 and r[a + 1] == "b") or _between(r, a, y, "s"),
            id="scope-fixed-or-between",
        ),
        pytest.param(
            "i:has(~ s ~ x:is(:scope ~ *))",
            lambda r, a, y: _between(r, a, y, "s"),
            id="scope-offset-past-chain",
        ),
        pytest.param(
            "i:has(~ s:is(:scope ~ *) ~ x:is(:scope ~ b ~ *))",
            lambda r, a, y: _between(r, a, y, "s") and _between(r, a, y, "b"),
            id="unswept-scope-twice",
        ),
        pytest.param(
            "i:has(~ x:is(:scope ~ b:not(:scope) ~ *))",
            lambda r, a, y: _between(r, a, y, "b"),
            id="unswept-scope-in-two-compounds",
        ),
        pytest.param("i:has(~ x:is(:scope ~ b > *))", lambda _r, _a, _y: False, id="unswept-scope-then-child"),
        pytest.param(
            "i:has(~ x:is(:scope:is(.k, :scope) ~ b ~ *))",
            lambda r, a, y: _between(r, a, y, "b"),
            id="unswept-scope-beside-list",
        ),
        pytest.param(
            "i:has(~ x:is(:scope:not(:scope) ~ *))", lambda _r, _a, _y: False, id="unswept-scope-beside-not-scope"
        ),
        pytest.param(
            "i:has(~ x:is(b ~ :not(:scope) ~ *))",
            lambda r, a, y: any(p != a and "b" in r[:p] for p in range(y)),
            id="unswept-not-scope-in-chain",
        ),
        pytest.param(
            "i:has(~ x:is(:not(:scope, s) ~ *))",
            lambda r, a, y: any(p != a and r[p] != "s" for p in range(y)),
            id="unswept-not-scope-or",
        ),
        pytest.param(
            "i:has(~ x:is(:scope:not(.k, b) ~ s ~ *))",
            lambda r, a, y: not _SWEPT_RUN[a].isupper() and _between(r, a, y, "s"),
            id="scope-beside-not-list",
        ),
        pytest.param(
            "i:has(~ x:is(:scope:not(i ~ b) ~ s ~ *))",
            lambda r, a, y: _between(r, a, y, "s"),
            id="scope-beside-not-chain",
        ),
        pytest.param(
            "i:has(~ x:is(:scope:not(i.k) ~ s ~ *))",
            lambda r, a, y: not _SWEPT_RUN[a].isupper() and _between(r, a, y, "s"),
            id="scope-beside-not-compound",
        ),
        pytest.param(
            "i:has(~ x:is(:scope:not(:first-child) ~ s ~ *))",
            lambda r, a, y: a > 0 and _between(r, a, y, "s"),
            id="scope-beside-not-pseudo",
        ),
        pytest.param(
            "i:has(~ x:is(:not(:scope):not(.k) ~ b ~ *))",
            lambda r, a, y: any(p != a and not _SWEPT_RUN[p].isupper() and _between(r, p, y, "b") for p in range(y)),
            id="not-scope-beside-not",
        ),
        pytest.param(
            "i:has(~ x:nth-child(1 of :scope.k ~ *))",
            lambda _r, a, y: _SWEPT_RUN[a].isupper() and y == a + 1,
            id="unswept-of-scope-typed",
        ),
        pytest.param(
            "i:has(~ x:nth-child(1 of :scope ~ *, .k))",
            lambda _r, a, y: y == min(a + 1, _SWEPT_RUN.find("I")),
            id="unswept-of-scope-or-class",
        ),
        pytest.param(
            "i:has(~ x:nth-child(1 of :scope ~ b.k))", lambda _r, _a, _y: False, id="unswept-of-scope-typed-end"
        ),
        pytest.param(
            "i:has(~ x:nth-child(2 of :scope, :scope ~ *))", lambda _r, a, y: y == a + 1, id="of-scope-itself"
        ),
        pytest.param(
            "i:has(~ x:nth-child(1 of :scope.k, :scope ~ *))",
            lambda _r, a, y: y == a + 1 and not _SWEPT_RUN[a].isupper(),
            id="unswept-of-scope-itself-typed",
        ),
        pytest.param("i:has(+ x:is(:scope ~ * > *))", lambda _r, _a, _y: False, id="unswept-adjacent-scope-climb"),
        pytest.param(
            "i:has(~ x:nth-child(3n of :scope ~ *):nth-child(-2n+4 of :scope ~ *))",
            lambda _r, _a, _y: False,
            id="of-steps-apart-in-range",
        ),
        pytest.param(
            "i:has(~ b:is(:scope ~ *) ~ x:is(:scope ~ * ~ *))",
            lambda r, a, y: _between(r, a, y, "b"),
            id="scope-offsets-twice",
        ),
        pytest.param(
            "i:has(~ x:nth-child(1 of .k))",
            lambda _r, _a, y: _SWEPT_RUN[y].isupper(),
            id="of-without-scope",
        ),
        pytest.param(
            "i:has(~ x:nth-child(2 of :scope ~ *, :first-child))",
            lambda _r, a, y: len([p for p in range(y + 1) if p > a or p == 0]) == 2,
            id="unswept-of-scope-or-pseudo",
        ),
        pytest.param(
            "i:has(~ x:nth-child(1 of :scope ~ b))",
            lambda r, a, y: r.find("b", a + 1) == y,
            id="unswept-of-scope-between",
        ),
        pytest.param(
            "i:has(~ x:nth-child(1 of b ~ *, :scope ~ *))",
            lambda r, a, y: min(a + 1, r.find("b") + 1) == y,
            id="unswept-of-chain-or-scope",
        ),
        pytest.param(
            "i:has(~ x:is(:is(:scope ~ b > i)))", lambda _r, _a, _y: False, id="unswept-nested-scope-then-child"
        ),
        pytest.param(
            "i:has(:scope:not(:scope ~ b) ~ x:is(:scope ~ b ~ *))",
            lambda r, a, y: _between(r, a, y, "b"),
            id="unswept-pinned-beside-chain",
        ),
        pytest.param(
            "i:has(~ x:nth-child(2n of :scope ~ *):nth-child(2n+1 of :scope ~ *))",
            lambda _r, _a, _y: False,
            id="of-apart",
        ),
        pytest.param(
            "i:has(~ x:not(:not(:nth-child(3n of :scope ~ *))))",
            lambda _r, a, y: (y - a) % 3 == 0,
            id="of-step-double-negated",
        ),
        pytest.param(
            "i:has(~ x:nth-child(-n+1 of :scope ~ * ~ *, :scope + *))",
            lambda _r, a, y: y == a + 1,
            id="of-countdown-past-exact",
        ),
        pytest.param(
            "i:has(~ x:not(:not(:scope.k ~ b ~ *)))",
            lambda r, a, y: _SWEPT_RUN[a].isupper() and _between(r, a, y, "b"),
            id="scope-pinned-double-negated",
        ),
        pytest.param(
            "i:has(~ x:is(:scope.k ~ b ~ *):not(:scope.k ~ b ~ *))", lambda _r, _a, _y: False, id="scope-pinned-and-not"
        ),
        pytest.param(
            f"i:has(~ x:is({_alternatives(':scope ~ b', ' ~ *', range(1, 66))}))",
            lambda r, a, y: _between(r, a, y, "b"),
            id="past-the-spans",
        ),
        pytest.param(
            f"i:has(~ x:nth-child(1 of {_alternatives(':scope', ' + *', range(1, 66))}))",
            lambda _r, a, y: y == a + 1,
            id="of-past-the-spans",
        ),
        pytest.param(
            f"i:has(~ x:is({_alternatives(':nth-child(1 of :scope', ' + *', range(1, 66), ')')}))",
            lambda _r, a, y: y - a <= 65,
            id="list-past-the-spans",
        ),
        pytest.param(
            f"i:has(~ x:is({', '.join(f':nth-child({step}n of :scope ~ *)' for step in range(2, 11))}))",
            lambda _r, a, y: any((y - a) % step == 0 for step in range(2, 11)),
            id="past-the-counts",
        ),
        pytest.param(
            f"i:has(~ x:is({_alternatives(':scope.k ~ b', ' ~ *', range(1, 34))}))",
            lambda r, a, y: _SWEPT_RUN[a].isupper() and _between(r, a, y, "b"),
            id="past-the-predicates",
        ),
    ],
)
def test_computed_style_sweep_agrees_with_brute_force(rule: str, expected: Callable[[str, int, int], bool]) -> None:
    html: Final[str] = "".join(_SWEPT_TAGS[letter] for letter in _SWEPT_RUN)
    nodes: Final[list[Element]] = parse(f"<style>*{{color:blue}}{rule}{{color:red}}</style><p>{html}</p>").select(
        "p > *"
    )
    run: Final[str] = _SWEPT_RUN.lower()
    assert [computed_style(node)["color"] for node in nodes] == [
        "red" if letter == "i" and any(expected(run, anchor, x) for x in _later(run, anchor, "x")) else "blue"
        for anchor, letter in enumerate(run)
    ]


def _later(run: str, anchor: int, tag: str) -> list[int]:
    return [index for index in range(anchor + 1, len(run)) if run[index] == tag]


def _between(run: str, low: int, high: int, tag: str) -> bool:
    return tag in run[low + 1 : high]


def _of_from_end(run: str, anchor: int, element: int, offsets: Callable[[int], bool]) -> int:
    return sum(offsets(index - anchor) for index in range(element, len(run))) if offsets(element - anchor) else 0


@pytest.mark.parametrize(
    ("step", "offset"),
    [
        pytest.param(2**32 + 2, -(2**32 - 1), id="step-past-int-max"),
        pytest.param(-(2**32 + 2), 5, id="negative-step-past-int-max"),
        pytest.param(3, -(2**31), id="offset-past-int-min"),
        pytest.param(2**32 + 2, 2**31 + 5, id="offset-past-int-max"),
        pytest.param(3, 2**31 + 5, id="small-step-offset-past-int-max"),
        pytest.param(0, -(2**31), id="zero-step-int-min-offset"),
    ],
)
def test_computed_style_sweep_wide_nth_agrees_with_formula(step: int, offset: int) -> None:
    count: Final[int] = 48
    nodes: Final[list[Element]] = parse(
        f"<style>*{{color:blue}}x:has(~ x:nth-child({step}n{offset:+d} of :scope ~ *)){{color:red}}</style>"
        f"<p>{'<x></x>' * count}</p>"
    ).select("p > x")
    assert [computed_style(node)["color"] for node in nodes] == [
        "red" if any(_nth_matches(step, offset, later - index) for later in range(index + 1, count + 1)) else "blue"
        for index in range(1, count + 1)
    ]


def _nth_matches(step: int, offset: int, distance: int) -> bool:
    # browsers saturate every An+B coefficient to the 32-bit int range before matching
    step, offset = max(-(2**31), min(2**31 - 1, step)), max(-(2**31), min(2**31 - 1, offset))
    if step == 0:
        return distance == offset
    difference = distance - offset
    return difference % step == 0 and difference // step >= 0


@pytest.mark.parametrize(
    ("rule", "color"),
    [
        pytest.param("i:has(~ p > x:is(:scope ~ b ~ *))", "blue", id="scope-chain-below-sibling"),
        pytest.param("i:has(~ p > x:not(:scope ~ b ~ *))", "red", id="scope-chain-negated-below-sibling"),
        pytest.param("i:has(~ p x:nth-child(1 of :not(:scope)))", "red", id="scope-of-below-sibling"),
        pytest.param("i:has(~ p > x:is(b > i, :scope ~ b ~ *))", "blue", id="scope-chain-or-child-below-sibling"),
        pytest.param("i:has(~ p > x:is(:is(:scope ~ b) ~ *))", "blue", id="nested-scope-chain-below-sibling"),
        pytest.param("i:has(~ p > x:is(:scope ~ * > *))", "red", id="scope-climb-below-sibling"),
        pytest.param("i:has(~ x:is(:scope ~ b ~ *) > i)", "blue", id="scope-chain-above-child"),
        pytest.param("i:has(~ s:is(:scope ~ b ~ *) ~ p > x:not(:scope ~ b ~ *))", "red", id="swept-then-below"),
        pytest.param("i:has(~ s:is(:scope ~ b ~ *) ~ p > x:is(:scope ~ * > *))", "red", id="swept-then-climb"),
        pytest.param("i:has(~ p > x:nth-child(1 of :scope ~ * > *))", "red", id="scope-climb-of-below-sibling"),
        pytest.param("i:has(~ p > x:nth-child(1 of b):is(:scope ~ * > *))", "blue", id="of-beside-scope-climb"),
        pytest.param("i:has(~ p > x:is(b > i, :scope ~ * > *))", "red", id="child-or-scope-climb"),
        pytest.param("i:has(~ p > x:is(:is(:scope ~ * > *)))", "red", id="nested-scope-climb"),
    ],
)
def test_computed_style_scope_below_a_sibling(rule: str, color: str) -> None:
    document: Final[Document] = parse(
        f"<style>*{{color:blue}}{rule}{{color:red}}</style>"
        f"<div>{'<i></i>' * 40}<b></b><p><b></b><x><i></i></x></p><s></s><p><x></x></p></div>"
    )
    assert {computed_style(node)["color"] for node in document.select("div > i")} == {color}


@pytest.mark.parametrize(
    ("rule", "red"),
    [
        pytest.param("b:scope:nth-last-child(1 of b)", ["b", "b"], id="last-of-beside-scope"),
        pytest.param("i:scope:has(~ x:has(b))", ["i"], id="has-in-sibling-argument"),
        pytest.param("i:scope:has(~ x:has(b) y)", ["i"], id="has-left-of-descendant"),
    ],
)
def test_computed_style_scope_selector_with_nested_lists(rule: str, red: list[str]) -> None:
    nodes: Final[list[Element]] = parse(
        f"<style>{rule}{{background-color:red}}</style><div><i></i><x><b></b><y></y></x><b></b></div>"
    ).select("div *")
    assert [node.tag for node in nodes if computed_style(node)["background-color"] == "red"] == red


@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("i:has(~ p > x:is(:scope ~ b ~ *))", id="scope-chain-below-sibling"),
        pytest.param("i:has(~ p x:nth-child(1 of :not(:scope)))", id="scope-of-below-sibling"),
        pytest.param("i:has(~ s:is(:scope ~ b ~ *) ~ p > x:not(:scope ~ b ~ *))", id="swept-then-below"),
    ],
)
def test_computed_style_scope_below_a_sibling_in_linear_time(rule: str, work_steps: WorkSteps) -> None:
    def style(size: int) -> Callable[[], object]:
        nodes = parse(
            f"<style>{rule}{{color:red}}</style>"
            f"<div>{'<i></i>' * size}<b></b><s></s>{'<p><b></b><x></x></p>' * size}</div>"
        ).select("div > *")
        return lambda: [computed_style(node) for node in nodes]

    work_steps.assert_linear(style, (350, 1400))


def test_computed_style_scope_climb_past_the_ancestor_memo() -> None:
    # the em sits 40 levels below the section, past the 32 ancestors a walk passes before it keeps a table; the
    # :is(q u) rule puts every rule on the deep matcher
    document: Final[Document] = parse(
        f"<style>*{{color:blue}}:is(q u) b{{color:green}}section:has(:is(:scope > *) em){{color:red}}</style>"
        f"<section>{'<div>' * 40}<em></em>{'</div>' * 40}</section>"
    )
    section: Final[Element | None] = document.select_one("section")
    assert isinstance(section, Element)
    assert computed_style(section)["color"] == "red"


_UNSWEPT_WALKS: Final[list[pytest.ParameterSet]] = [
    pytest.param("i:has(~ s:is(:scope ~ *) ~ x:is(:scope ~ b ~ *))", id="scope-in-two-compounds"),
    pytest.param("i:has(~ x:is(:scope ~ * *))", id="climbing-chain"),
    pytest.param("i:has(~ x:nth-child(odd of :scope ~ *, b))", id="of-depends-on-content"),
    pytest.param("i:has(~ x:is(b ~ :not(:scope) ~ *))", id="not-scope-not-leading"),
    pytest.param("i:has(~ x:is(:is(:scope ~ b) + *))", id="scope-beside-scope-writer"),
    pytest.param(f"i:has(~ x:is({_alternatives(':scope.k ~ b', ' ~ *', range(1, 34))}))", id="past-the-predicates"),
    pytest.param("i:has(+ b ~ :is(:scope ~ * *))", id="adjacent-lead"),
]


@pytest.mark.parametrize("rule", _UNSWEPT_WALKS)
def test_computed_style_has_walks_past_the_allowance_raise(rule: str) -> None:
    nodes: Final[list[Element]] = parse(
        f"<style>{rule}{{color:red}}</style><div>{'<i></i><b></b>' * 12_500}<x></x></div>"
    ).select("div > *")
    with pytest.raises(ValueError, match=r"^the :has\(\) walks of the page's style rules exceed the allowance"):
        list(map(computed_style, nodes))


@pytest.mark.parametrize("rule", _UNSWEPT_WALKS[:1])
def test_computed_style_has_walks_raise_in_linear_time(rule: str, work_steps: WorkSteps) -> None:
    def style(size: int) -> Callable[[], object]:
        nodes = parse(f"<style>{rule}{{color:red}}</style><div>{'<i></i><b></b>' * size}<x></x></div>").select(
            "div > *"
        )

        def until_raised() -> None:
            with pytest.raises(ValueError, match="exceed the allowance"):
                list(map(computed_style, nodes))

        return until_raised

    work_steps.assert_linear(style, (16_000, 64_000))


def test_computed_style_has_walks_raise_until_the_document_changes() -> None:
    document: Final[Document] = parse(
        f"<style>{_UNSWEPT_WALKS[0].values[0]}{{color:red}}</style>"
        f"<div><i></i><s></s><b></b><x></x>{'<i></i><b></b>' * 12_500}</div>"
    )
    nodes: Final[list[Element]] = document.select("div > *")
    with pytest.raises(ValueError, match="exceed the allowance"):
        list(map(computed_style, nodes))
    with pytest.raises(ValueError, match="exceed the allowance"):
        computed_style(nodes[0])
    nodes[-1].attrs["class"] = "k"
    assert computed_style(nodes[0])["color"] == "red"


def test_computed_style_many_has_rules_on_a_large_page_stay_within_the_allowance() -> None:
    templates: Final[tuple[str, ...]] = (
        "li:has(> a.{0})",
        "nav:has(ul li.{0})",
        "tr:has(td.{0}:empty)",
        "section:has(h2.{0} + p)",
        "article:has(> img.{0}, > figure)",
        "label:has(+ input.{0}:checked)",
        "div:has(~ footer.{0})",
        "li:has(~ li.{0})",
        "td:has(~ td.{0})",
        "section:has(:scope > h2.{0})",
        "li:has(+ li:is(:scope + .{0}))",
        "p:has(~ p:nth-child(2n of .{0}))",
    )
    sheet: Final[str] = "".join(
        f"{template.format(f'k{index}')}{{background-color:red}}" for template in templates for index in range(8)
    )
    row: Final[str] = f"<tr>{''.join(f'<td class=k{index}></td>' for index in range(6))}</tr>"
    items: Final[str] = "".join(f"<li class=k{index % 8}><a class=k{index % 5}></a></li>" for index in range(40))
    section: Final[str] = (
        f"<section><h2 class=k1></h2><p></p><table>{row * 20}</table><ul>{items}</ul>"
        f"<label></label><input type=checkbox class=k2 checked>{'<p class=k3></p>' * 20}</section>"
    )
    document: Final[Document] = parse(f"<style>{sheet}</style><div>{section * 60}<footer class=k4></footer></div>")
    red: Final[list[Element]] = document.select(
        ", ".join(template.format(f"k{index}") for template in templates for index in range(8))
    )
    assert [node for node in document.select("*") if computed_style(node)["background-color"] == "red"] == red


def test_computed_style_sibling_records_past_the_limit() -> None:
    # 399 alternatives over 80 runs keep more records of their walks than the smallest deep memo holds, so the cascade
    # drops them and walks on; the first run holds only the inner walk's record, which puts the one that overflows
    # inside an outer walk whose own record goes with the rest
    rule: Final[str] = f":is({', '.join(f'.c{index} ~ i ~ em' for index in range(399))})"
    run: Final[str] = f"{'<i></i>' * 140}{'<b></b>' * 140}<em></em>"
    document: Final[Document] = parse(
        f"<style>*{{color:blue}}{rule}{{color:red}}</style><p>{'<i></i>' * 140}<em></em></p>"
        f"{f'<p>{run}</p>' * 78}<p><s class=c7></s>{run}</p>"
    )
    assert [computed_style(node)["color"] for node in document.select("em")] == ["blue"] * 79 + ["red"]


def test_computed_style_nth_of_counts_grow_past_every_table_size() -> None:
    # each clause keeps its own counts, so the table regrows through every size up to 512 slots with hundreds of owners
    rules: Final[str] = "".join(f"x ~ p:nth-child(1 of .c{index}){{color:red}}" for index in range(300))
    node: Final[Element | None] = parse(f"<style>{rules}</style><div><x></x><p class=c0></p></div>").select_one("p")
    assert isinstance(node, Element)
    assert computed_style(node)["color"] == "red"


def test_computed_style_nth_of_clauses_eight_apart_share_a_home() -> None:
    # the first and ninth clause of one selector number eight apart, so in the eight-slot table the second one counted
    # probes past the first; the seven between sit on q, which no element is, and are never counted
    between: Final[str] = ", ".join(f"q:nth-child(1 of .c{index})" for index in range(7))
    rule: Final[str] = f"x ~ :is(p:nth-child(1 of .a), {between}, p:nth-child(2 of .b))"
    nodes: Final[list[Element]] = parse(
        f"<style>{rule}{{color:red}}</style><div><x></x><p class=a></p><p class=b></p><p class=b></p></div>"
    ).select("p")
    assert [computed_style(node)["color"] for node in nodes] == ["red", "canvastext", "red"]


@pytest.mark.parametrize(
    ("rule", "added"),
    [
        pytest.param("em:has(~ s ~ em)", ("s", "em"), id="later-chain"),
        pytest.param("s ~ em", ("s",), id="earlier-sibling"),
        pytest.param("em:has(~ s:not(:scope))", ("s",), id="scope-in-compound"),
        pytest.param("button:default", ("button",), id="default"),
    ],
)
def test_computed_style_sibling_rule_refreshes_after_insertion(rule: str, added: tuple[str, ...]) -> None:
    # the answers a sibling rule keeps for every element it styles must not outlive an edit to the run
    document: Final[Document] = parse(f"<style>{rule}{{color:red}}</style><form>{'<em></em>' * 40}<button></button>")
    nodes: Final[list[Element]] = document.select("form > *")
    before: Final[list[tuple[str, bool]]] = [(computed_style(node)["color"], node.matches(rule)) for node in nodes]
    for tag in reversed(added):
        nodes[20].insert_before(Element(tag))
    after: Final[list[tuple[str, bool]]] = [
        (computed_style(node)["color"], node.matches(rule)) for node in document.select("form > *")
    ]
    assert {(color == "red") == matched for color, matched in before + after} == {True}
    assert [color for color, _ in before] != [computed_style(node)["color"] for node in nodes]


@pytest.mark.parametrize(
    "order", [pytest.param(slice(None), id="forward"), pytest.param(slice(None, None, -1), id="reverse")]
)
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
    selector: str, expected: tuple[str, ...], separator: str, *, order: slice
) -> None:
    document: Final[Document] = parse(
        f"""<style>i{{color:blue}}i{selector}{{color:red}}</style>{
            separator.join(('<i class="hit"></i>', "<i></i>", '<i class="hit"></i>', "<i></i>"))
        }"""
    )
    nodes: Final[list[Element]] = document.select("i")
    assert tuple(computed_style(node)["color"] for node in nodes[order]) == expected[order]


def test_computed_style_nth_offset_near_int_min_matches_nothing() -> None:
    # a = -1 with b = -(INT_MAX - 2): for the third child, index - b is one past INT_MAX
    document: Final[Document] = parse(
        f"<style>p{{color:blue}}p:nth-child(-n-{2**31 - 3}){{color:red}}</style><i></i><i></i><p>x"
    )
    paragraph: Final[Element | None] = document.select_one("p")
    assert isinstance(paragraph, Element)
    assert computed_style(paragraph)["color"] == "blue"


def test_computed_style_type_positions_in_long_run() -> None:
    # past 32 siblings the cascade indexes the run once for all its elements, and an edit drops that index
    run: Final[str] = "".join(f"<x-{index % 2}></x-{index % 2}>" for index in range(80))
    document: Final[Document] = parse(
        "<style>#p > *{color:blue}#p > :last-of-type{color:red}#p > :nth-of-type(2){color:green}</style>"
        f"<div id=p>{run}</div>"
    )
    nodes: Final[list[Element]] = document.select("#p > *")
    before: Final[list[str]] = [computed_style(node)["color"] for node in nodes]
    nodes[-1].insert_after(Element("x-0"))
    after: Final[list[str]] = [computed_style(node)["color"] for node in nodes]
    assert (before, after) == (
        ["blue"] * 2 + ["green"] * 2 + ["blue"] * 74 + ["red"] * 2,
        ["blue"] * 2 + ["green"] * 2 + ["blue"] * 75 + ["red"],
    )


def test_computed_style_type_position_looked_up_backwards() -> None:
    # the cascade remembers the last position it computed; a later call for an earlier sibling past a long run of
    # other types reads the position memo instead of walking the run
    document: Final[Document] = parse(
        f"<style>#p > :nth-of-type(1){{color:red}}</style><div id=p><b></b>{'<i></i>' * 40}<b></b></div>"
    )
    nodes: Final[list[Element]] = document.select("#p > b")
    assert [computed_style(node)["color"] for node in nodes[::-1]] == ["canvastext", "red"]


def test_computed_style_sibling_positions_after_insertion() -> None:
    document: Final[Document] = parse("<style>i{color:blue}i:nth-child(odd){color:red}</style><i></i><i></i><i></i>")
    nodes: Final[list[Element]] = document.select("i")
    before: Final[tuple[str, ...]] = tuple(computed_style(node)["color"] for node in reversed(nodes))
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
    document: Final[Document] = parse(
        f"<style>i{{color:blue}}i{selector}{{color:red}}</style>{'<i></i> <!--gap--> <b></b>' * 4}"
    )
    nodes: Final[list[Element]] = document.select("i")
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
    document: Final[Document] = parse(
        '<div style="color:red"><i></i><i></i></div><div style="color:blue"><i></i><i></i></div>'
    )
    nodes: Final[list[Element]] = document.select("div,i")
    assert tuple(computed_style(nodes[index])["color"] for index in order) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<math><style>p{color:<mi>teal</mi>}</style></math><p></p>",
            {"color": "canvastext"},
            id="child-element-text-not-sheet",
        ),
        pytest.param(
            "<math><style><style>p</style>{font-style:italic}</style></math><p></p>",
            {"font-style": "normal"},
            id="inner-style-text-not-outer-declaration",
        ),
    ],
)
def test_computed_style_foreign_style_sheet_is_its_own_text(html: str, expected: dict[str, str]) -> None:
    # a nested style's text is compiled as that style's own sheet, never appended to the enclosing one
    style: Final[ComputedStyle] = computed_style(parse(html).select("p")[0])
    assert {name: style[name] for name in expected} == expected


def test_computed_style_style_staircase_stays_within_memory_budget(
    peak_bytes: PeakBytes,
) -> None:
    peak: Final[int] = peak_bytes(
        "import tracemalloc\n"
        "from turbohtml import parse\n"
        "from turbohtml.cssom import computed_style\n"
        "staircase = ''.join('<style>p{z-index:%d}' % index for index in range(270))\n"
        "document = parse('<p></p><math>' + staircase + '</math>')\n"
        "tracemalloc.start()\n"
        "computed_style(document.select('p')[0])\n"
        "print(tracemalloc.get_traced_memory()[1])\n",
        (),
    )
    assert peak < 12 * 1024 * 1024, peak


def test_computed_style_html_style_drops_text_of_appended_element() -> None:
    document: Final[Document] = parse("<style>p{color:</style><p></p>")
    style: Final[Element | None] = document.select_one("style")
    paragraph: Final[Element | None] = document.select_one("p")
    assert isinstance(style, Element)
    assert isinstance(paragraph, Element)
    style.append(Element("b", children=[Text("red}")]))
    assert computed_style(paragraph)["color"] == "canvastext"


@pytest.mark.parametrize(
    "order",
    [
        pytest.param((0, 1, 2, 3, 4, 5, 6, 7, 8), id="document-order"),
        pytest.param((8, 7, 6, 5, 4, 3, 2, 1, 0), id="reverse-order"),
        pytest.param((3, 5, 8, 3), id="cousins"),
        pytest.param((3, 7, 4, 1, 8), id="shallower-branch"),
    ],
)
def test_computed_style_returns_to_an_ancestor(order: tuple[int, ...]) -> None:
    document: Final[Document] = parse(
        '<div style="color:red"><p><b><i></i></b><b><i></i></b></p><u style="color:blue"><s><i></i></s></u></div>'
    )
    nodes: Final[list[Element]] = document.select("div,p,b,i,u,s")
    expected: Final[tuple[str, ...]] = ("red",) * 6 + ("blue",) * 3
    assert tuple(computed_style(nodes[index])["color"] for index in order) == tuple(expected[index] for index in order)


def _leaves_in_document_order(depth: int) -> list[Element]:
    return list(parse_xml("<d><b/>" * depth + "</d>" * depth).select("b"))


def _leaves_in_reverse_order(depth: int) -> list[Element]:
    return _leaves_in_document_order(depth)[::-1]


def _leaves_in_scattered_order(depth: int) -> list[Element]:
    leaves: Final[list[Element]] = _leaves_in_document_order(depth)
    return [leaves[index * 7919 % depth] for index in range(depth)]


def _alternating_branches(depth: int) -> list[Element]:
    branch: Final[str] = f"{'<d>' * depth}{{}}{'</d>' * depth}"
    return list(parse_xml(f"<r>{branch.format('<b/>')}{branch.format('<i/>')}</r>").select("b, i")) * (depth // 2)


@pytest.mark.parametrize(
    "calls",
    [
        pytest.param(_leaves_in_document_order, id="document-order"),
        pytest.param(_leaves_in_reverse_order, id="reverse-order"),
        pytest.param(_leaves_in_scattered_order, id="scattered-order"),
        pytest.param(_alternating_branches, id="alternating-branches"),
    ],
)
def test_computed_style_below_a_deep_chain_is_linear(
    calls: Callable[[int], list[Element]], work_steps: WorkSteps
) -> None:
    def walk(depth: int) -> Callable[[], object]:
        nodes: Final[list[Element]] = calls(depth)
        return lambda: [computed_style(node) for node in nodes]

    work_steps.assert_linear(walk, (1500, 5000))


def test_computed_style_in_scattered_order_matches_document_order() -> None:
    def styled(level: int) -> str:
        return f' style="color:c{level};width:{level}px"' if level % 7 == 0 else ""

    def leaf(level: int) -> str:
        return ' style="color:initial;width:inherit"' if level % 5 == 0 else ' style="width:inherit"'

    levels: Final[int] = 1300
    source: Final[str] = "".join(f"<d{styled(level)}><b{leaf(level)}/>" for level in range(levels)) + "</d>" * levels
    walked: Final[list[list[tuple[str, str]]]] = [_style_items(node) for node in parse_xml(source).select("d, b")]
    nodes: Final[list[Element]] = parse_xml(source).select("d, b")
    scattered: Final[dict[int, list[tuple[str, str]]]] = {
        index: _style_items(nodes[index]) for index in (step * 7919 % len(nodes) for step in range(len(nodes)))
    }
    assert [scattered[index] for index in range(len(nodes))] == walked


def _style_items(node: Element) -> list[tuple[str, str]]:
    style: Final[ComputedStyle] = computed_style(node)
    return [(name, style[name]) for name in style]


def test_computed_style_returns_to_an_element_whose_style_escapes_a_backslash() -> None:
    document: Final[Document] = parse_xml(r'<r><s style="\5c 63olor:red"><b/></s><i/></r>')
    nodes: Final[list[Element]] = document.select("b, i")
    assert tuple(computed_style(nodes[index])["color"] for index in (0, 1, 0)) == ("canvastext",) * 3


def test_computed_style_shares_an_inherited_value_down_a_deep_chain(retained_bytes: RetainedBytes) -> None:
    depth: Final[int] = 1200
    color: Final[str] = "x" * 100_000
    nodes: Final[list[Element]] = parse_xml(
        f'<d style="color:{color}">{"<d><b/>" * depth}{"</d>" * (depth + 1)}'
    ).select("b")
    before: Final[int] = retained_bytes()
    for index in range(depth):
        computed_style(nodes[index * 7919 % depth])
    assert retained_bytes() - before < 8 * len(color) * 4


def test_computed_style_detached_subtree_then_document() -> None:
    document: Final[Document] = parse('<div style="color:red"><p style="color:blue"><b></b></p><i></i></div>')
    inner: Final[list[Element]] = document.select("b, i")
    document.select("p")[0].extract()
    assert tuple(computed_style(inner[index])["color"] for index in (0, 1, 0)) == ("blue", "red", "blue")


@pytest.mark.parametrize(
    "order",
    [
        pytest.param((1699, 300, 299, 1000, 1699), id="dropped-levels"),
        pytest.param((1699, 512, 513, 1698, 0), id="kept-level"),
        pytest.param((1100, 200, 1699, 700, 1699), id="half-dropped"),
    ],
)
def test_computed_style_chain_deeper_than_the_cached_levels(order: tuple[int, ...]) -> None:
    colors: Final[dict[int, str]] = {0: "red", 250: "blue", 600: "green", 1300: "teal"}
    opened: Final[str] = "".join(
        f'<d style="color:{colors[level]}">' if level in colors else "<d>" for level in range(1700)
    )
    nodes: Final[list[Element]] = parse_xml(opened + "</d>" * 1700).select("d")
    expected: Final[tuple[str, ...]] = tuple(
        colors[max(styled for styled in colors if styled <= level)] for level in order
    )
    assert tuple(computed_style(nodes[level])["color"] for level in order) == expected


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        pytest.param(lambda style: style.attrs.__setitem__("type", "text/plain"), "canvastext", id="type-set"),
        pytest.param(lambda style: setattr(style.children[0], "data", "p{color:blue}"), "blue", id="data-set"),
        pytest.param(lambda style: style.rewrite_links(lambda _url: None), "red", id="rewrite-links-unchanged"),
    ],
)
def test_computed_style_follows_a_style_element_edited_in_place(
    edit: Callable[[Element], object], expected: str
) -> None:
    # none of these links or unlinks a node
    paragraph, style = _styled_paragraph("<style>p{color:red}</style><p></p><i></i>")
    edit(style)
    assert computed_style(paragraph)["color"] == expected
    sibling = paragraph.next_sibling
    assert sibling is not None
    sibling.decompose()  # an unrelated edit keeps the recompiled sheets
    assert computed_style(paragraph)["color"] == expected


def _styled_paragraph(markup: str) -> tuple[Element, Element]:
    document = parse(markup)
    paragraph = document.select_one("p")
    style = document.select_one("style")
    assert paragraph is not None
    assert style is not None
    computed_style(paragraph)  # compile and cache the sheets
    return paragraph, style


def test_computed_style_follows_a_style_type_removed_again() -> None:
    paragraph, style = _styled_paragraph('<style type="text/plain">p{color:red}</style><p></p>')
    assert computed_style(paragraph)["color"] == "canvastext"
    del style.attrs["type"]
    assert computed_style(paragraph)["color"] == "red"


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda style: style.rewrite_links(lambda _url: "https://example.org/b.png"), id="rewrite"),
        pytest.param(lambda style: style.resolve_links("https://example.org/"), id="resolve"),
    ],
)
def test_computed_style_follows_links_rewritten_inside_a_style_element(edit: Callable[[Element], object]) -> None:
    paragraph, style = _styled_paragraph("<style>p{background-image:url(b.png)}</style><p></p><i></i>")
    edit(style)
    assert computed_style(paragraph)["background-image"] == "url(https://example.org/b.png)"
    sibling = paragraph.next_sibling
    assert sibling is not None
    sibling.decompose()
    assert computed_style(paragraph)["background-image"] == "url(https://example.org/b.png)"


def test_computed_style_keeps_sheets_across_attribute_writes_that_keep_style_types() -> None:
    paragraph, style = _styled_paragraph(
        '<style>p{color:red}</style><style type="text/plain">p{color:green}</style><style>.x{color:blue}</style><p></p>'
    )
    style.attrs["media"] = "screen"
    paragraph.attrs["class"] = "x"
    assert computed_style(paragraph)["color"] == "blue"


def test_long_rule_chain_raises_recursion_error_on_a_small_stack() -> None:
    code = (
        "import threading\n"
        "from turbohtml import parse\n"
        "from turbohtml.cssom import computed_style\n"
        "n = 6_144\n"
        'document = parse("<style>" + "wbr ~ " * n + "b{color:red}</style>" + "<wbr>" * n + "<b id=x></b>")\n'
        "outcome = []\n"
        "def run():\n"
        "    try:\n"
        '        outcome.append(computed_style(document.select_one("#x"))["color"])\n'
        "    except RecursionError as error:\n"
        "        outcome.append(str(error))\n"
        "threading.stack_size(256 * 1024)\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\n"
        "worker.join()\n"
        "print(outcome[0])\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert (result.returncode, "C stack" in result.stdout) == (0, True), result.stderr


def test_long_rule_chain_applies_when_the_stack_holds_it() -> None:
    document = parse(f"<style>{'wbr ~ ' * 199}b{{color:red}}</style>{'<wbr>' * 199}<b id=x></b>")
    assert computed_style(document.select("#x")[0])["color"] == "red"


def test_computed_style_of_clauses_of_two_rules_count_apart() -> None:
    # each rule numbers its 'of S' clauses from 1, and the climbing rule puts both on the deep matcher's one memo
    document: Final[Document] = parse(
        "<style>*{color:blue}:is(:is(q u) v) w{color:green}* a:nth-child(1 of .k){color:red}"
        "* b:nth-child(2 of [d]){color:lime}</style><p><b d></b><a class=k></a><b d></b></p>"
    )
    assert [computed_style(node)["color"] for node in document.select("p *")] == ["blue", "red", "lime"]


def test_computed_style_of_clauses_of_many_rules_count_apart() -> None:
    rules: Final[list[str]] = [f"* b:nth-child({index % 3 + 1} of .c{index % 4})" for index in range(16)]
    classes: Final[str] = "".join(f"<b class=c{index % 4}></b>" for index in range(10))
    document: Final[Document] = parse(
        f"<style>*{{color:blue}}:is(:is(q u) v) w{{color:green}}{''.join(f'{rule}{{color:red}}' for rule in rules)}"
        f"</style><p>{classes}</p>"
    )
    nodes: Final[list[Element]] = document.select("p *")
    assert [computed_style(node)["color"] for node in nodes] == [
        "red" if any(node.matches(rule) for rule in rules) else "blue" for node in nodes
    ]
