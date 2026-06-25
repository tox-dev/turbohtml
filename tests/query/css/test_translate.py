"""Unit tests for the CSS-to-XPath translator: exact output, the public surface, and rejected input."""

from __future__ import annotations

import pytest

import turbohtml
from turbohtml._html import _css_to_xpath
from turbohtml.convert import HTMLTranslator, SelectorError, SelectorSyntaxError, Translator, css_to_xpath

_PREFIX = "descendant-or-self::"


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("div", "descendant-or-self::div", id="type"),
        pytest.param("DIV", "descendant-or-self::div", id="type-lowercased"),
        pytest.param("*", "descendant-or-self::*", id="universal"),
        pytest.param("#main", "descendant-or-self::*[@id = 'main']", id="id"),
        pytest.param(
            ".cls",
            "descendant-or-self::*[@class and contains(concat(' ', normalize-space(@class), ' '), ' cls ')]",
            id="class",
        ),
        pytest.param("[href]", "descendant-or-self::*[@href]", id="attr-exists"),
        pytest.param("[href=x]", "descendant-or-self::*[@href = 'x']", id="attr-eq"),
        pytest.param("[href='']", "descendant-or-self::*[@href = '']", id="attr-eq-empty"),
        pytest.param("[href^='']", "descendant-or-self::*[0]", id="prefix-empty"),
        pytest.param("[href$='']", "descendant-or-self::*[0]", id="suffix-empty"),
        pytest.param("[href*='']", "descendant-or-self::*[0]", id="substring-empty"),
        pytest.param("[href~='']", "descendant-or-self::*[0]", id="include-empty"),
        pytest.param("a + b", "descendant-or-self::a/following-sibling::*[1]/self::b", id="adjacent"),
        pytest.param("a ~ b", "descendant-or-self::a/following-sibling::b", id="general-sibling"),
        pytest.param("a > b", "descendant-or-self::a/b", id="child"),
        pytest.param("a b", "descendant-or-self::a/descendant-or-self::*/b", id="descendant"),
        pytest.param("a, b", "descendant-or-self::a | descendant-or-self::b", id="group"),
        pytest.param(":root", "descendant-or-self::*[not(parent::*)]", id="root"),
        pytest.param("p:empty", "descendant-or-self::p[not(*) and not(normalize-space())]", id="empty"),
        pytest.param("div:not(*)", "descendant-or-self::div[not(self::*)]", id="not-universal"),
        pytest.param(
            "div:not(p.note)",
            "descendant-or-self::div[not(self::p and @class and "
            "contains(concat(' ', normalize-space(@class), ' '), ' note '))]",
            id="not-compound",
        ),
        pytest.param(
            "div:not(.a, .b)",
            "descendant-or-self::div["
            "not(@class and contains(concat(' ', normalize-space(@class), ' '), ' a ')) and "
            "not(@class and contains(concat(' ', normalize-space(@class), ' '), ' b '))]",
            id="not-multi-arm",
        ),
    ],
)
def test_exact_translation(selector: str, expected: str) -> None:
    """The translator emits the documented XPath string for each construct."""
    assert css_to_xpath(selector) == expected


def test_concat_literal_for_value_with_both_quotes() -> None:
    r"""A value carrying both quote characters (via escapes) is emitted as an XPath concat()."""
    assert css_to_xpath(r"[title=a\'b\"c]") == "descendant-or-self::*[@title = concat('a', \"'\", 'b\"c')]"


def test_double_quote_literal_for_single_quote_value() -> None:
    r"""A value with a single quote but no double quote is wrapped in double quotes."""
    assert css_to_xpath(r"[title=a\'b]") == "descendant-or-self::*[@title = \"a'b\"]"


def test_case_insensitive_attribute_folds_both_sides() -> None:
    """An HTML case-insensitive attribute compares a translate()-lowered value to a lowered literal."""
    assert css_to_xpath("[type=TEXT]") == (
        "descendant-or-self::*[translate(@type, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz') = 'text']"
    )


@pytest.mark.parametrize(
    ("selector", "expected_predicate"),
    [
        pytest.param("li:nth-child(0)", "count(preceding-sibling::*) = -1", id="const-zero"),
        pytest.param("li:nth-child(2)", "count(preceding-sibling::*) = 1", id="const"),
        pytest.param(
            "li:nth-child(2n+3)",
            "(count(preceding-sibling::*) - 2) >= 0 and (count(preceding-sibling::*) - 2) mod 2 = 0",
            id="shift-negative",
        ),
        pytest.param(
            "li:nth-child(n)",
            "(count(preceding-sibling::*) + 1) >= 0 and (count(preceding-sibling::*) + 1) mod 1 = 0",
            id="shift-positive",
        ),
        pytest.param(
            "li:nth-child(2n+1)",
            "(count(preceding-sibling::*)) >= 0 and (count(preceding-sibling::*)) mod 2 = 0",
            id="shift-zero",
        ),
        pytest.param(
            "li:nth-child(-n+2)",
            "count(preceding-sibling::*) <= 1 and (1 - count(preceding-sibling::*)) mod 1 = 0",
            id="negative-a",
        ),
    ],
)
def test_nth_child_formula(selector: str, expected_predicate: str) -> None:
    """Each An+B shape produces its position-independent count() predicate."""
    assert css_to_xpath(selector) == f"descendant-or-self::li[{expected_predicate}]"


def test_custom_prefix() -> None:
    """The prefix argument replaces the default leading axis on every alternative."""
    assert css_to_xpath("a, b", prefix="child::") == "child::a | child::b"
    assert css_to_xpath("a", prefix="") == "a"


def test_translator_method_matches_function() -> None:
    """The cssselect-shaped Translator.css_to_xpath delegates to the module function."""
    assert Translator().css_to_xpath("div.x") == css_to_xpath("div.x")
    assert Translator().css_to_xpath("a", "child::") == css_to_xpath("a", prefix="child::")


def test_html_translator_is_the_translator() -> None:
    """The HTMLTranslator alias is the same class, for a mechanical cssselect port."""
    assert HTMLTranslator is Translator
    assert HTMLTranslator().css_to_xpath("p") == "descendant-or-self::p"


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("div:has(a)", id="has"),
        pytest.param("a:hover", id="interaction-state"),
        pytest.param(":checked", id="input-state"),
        pytest.param(":lang(en)", id="lang"),
        pytest.param("*:first-of-type", id="of-type-without-type"),
        pytest.param("*:nth-of-type(1)", id="nth-of-type-without-type"),
        pytest.param("p:nth-child(2n of .x)", id="nth-of-selector"),
        pytest.param(":not(div a)", id="not-with-combinator"),
        pytest.param("div:not(:has(a))", id="untranslatable-inside-not"),
        pytest.param("div:not(.a, :has(b))", id="untranslatable-later-not-arm"),
        pytest.param("div a:hover", id="untranslatable-later-compound"),
        pytest.param(":has(a), div", id="untranslatable-first-alternative"),
        pytest.param("div >", id="syntax-error"),
        pytest.param("", id="empty-selector"),
        pytest.param("::before", id="pseudo-element"),
    ],
)
def test_untranslatable_raises(selector: str) -> None:
    """An unsupported construct or a malformed selector raises SelectorSyntaxError."""
    with pytest.raises(SelectorSyntaxError):
        css_to_xpath(selector)


def test_selector_syntax_error_is_a_selector_error() -> None:
    """SelectorSyntaxError subclasses SelectorError, mirroring cssselect's hierarchy."""
    assert issubclass(SelectorSyntaxError, SelectorError)
    error = pytest.raises(SelectorSyntaxError, css_to_xpath, "div:has(a)").value
    assert isinstance(error, SelectorError)
    assert error.__cause__ is not None  # chained from the underlying ValueError


def test_binding_rejects_non_string_selector() -> None:
    """The C binding requires str arguments; a non-string raises TypeError before translation."""
    with pytest.raises(TypeError):
        _css_to_xpath(123, _PREFIX)  # ty: ignore[invalid-argument-type]  # a non-string selector is rejected in C


def test_round_trips_through_the_xpath_engine() -> None:
    """A translated expression runs unchanged through the XPath engine and finds the element."""
    doc = turbohtml.parse("<div class='card'><a href='/x'>x</a></div>")
    root = doc.root
    assert root is not None
    found = [node for node in root.xpath(css_to_xpath("div.card a[href]")) if isinstance(node, turbohtml.Element)]
    assert [node.attr("href") for node in found] == ["/x"]
