"""``turbohtml.cssom``: the CSS Object Model cascade and computed style (issue #546)."""

from __future__ import annotations

import pytest

from turbohtml import Element, parse
from turbohtml.build import E
from turbohtml.cssom import ComputedStyle, RuleList, StyleDeclaration, StyleRule, StyleSheet, computed_style


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
def test_declaration_important_flag(text: str, expected: bool) -> None:  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
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


def test_computed_style_important_normal_order_does_not_regress() -> None:
    # a later normal declaration must not override an earlier important one
    style = _style("<p></p>", css="p { color: blue !important } p { color: red }", tag="p")
    assert style["color"] == "blue"


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
