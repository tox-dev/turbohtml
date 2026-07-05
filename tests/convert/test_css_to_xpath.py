"""css_to_xpath(): the emitted XPath 1.0 text, the prefix, and the typed errors."""

from __future__ import annotations

import pytest

import turbohtml
from turbohtml import match
from turbohtml.convert import (
    ExpressionError,
    GenericTranslator,
    HTMLTranslator,
    SelectorError,
    SelectorSyntaxError,
    css_to_xpath,
)


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("div", "descendant-or-self::div", id="type"),
        pytest.param("DIV", "descendant-or-self::div", id="type-lowercased"),
        pytest.param("*", "descendant-or-self::*", id="universal"),
        pytest.param("div p", "descendant-or-self::div/descendant::p", id="descendant"),
        pytest.param("div > p", "descendant-or-self::div/p", id="child"),
        pytest.param("a + b", "descendant-or-self::a/following-sibling::*[1]/self::b", id="adjacent"),
        pytest.param("a ~ b", "descendant-or-self::a/following-sibling::b", id="sibling"),
        pytest.param("#id", "descendant-or-self::*[@id = 'id']", id="id"),
        pytest.param(
            ".cls",
            "descendant-or-self::*[@class and contains(concat(' ', normalize-space(@class), ' '), ' cls ')]",
            id="class",
        ),
        pytest.param("a[href]", "descendant-or-self::a[@href]", id="attr-exists"),
        pytest.param("a[HREF]", "descendant-or-self::a[@href]", id="attr-name-lowercased"),
        pytest.param("[data-x='a']", "descendant-or-self::*[@data-x = 'a']", id="attr-eq"),
        pytest.param('[data-x="it\'s"]', 'descendant-or-self::*[@data-x = "it\'s"]', id="literal-squote"),
        pytest.param("[data-x='say \"hi\"']", "descendant-or-self::*[@data-x = 'say \"hi\"']", id="literal-dquote"),
        pytest.param("div, p", "descendant-or-self::div | descendant-or-self::p", id="group-union"),
        pytest.param(":scope > div", "descendant-or-self::*[1]/div", id="scope-leading"),
        pytest.param("li:nth-child(n)", "descendant-or-self::li", id="nth-trivial"),
        pytest.param("li:nth-child(-n)", "descendant-or-self::li[false()]", id="nth-impossible"),
        pytest.param("li:nth-child(3)", "descendant-or-self::li[count(preceding-sibling::*) = 2]", id="nth-fixed"),
        pytest.param(
            "li:nth-child(2n+4)",
            "descendant-or-self::li[count(preceding-sibling::*) >= 3 and (count(preceding-sibling::*) + 1) mod 2 = 0]",
            id="nth-shifted",
        ),
        pytest.param(
            "li:nth-child(3n+1)",
            "descendant-or-self::li[count(preceding-sibling::*) mod 3 = 0]",
            id="nth-unshifted",
        ),
        pytest.param(
            "li:nth-last-child(-2n+4)",
            "descendant-or-self::li[count(following-sibling::*) <= 3 and (count(following-sibling::*) + 1) mod 2 = 0]",
            id="nth-last-negative-step",
        ),
        pytest.param(r"di\a0 v", "descendant-or-self::*[name() = 'di\xa0v']", id="unsafe-type-name"),
        pytest.param(r"[h\]ref]", "descendant-or-self::*[attribute::*[name() = 'h]ref']]", id="unsafe-attr-name"),
        pytest.param(":hover", "descendant-or-self::*[false()]", id="never-pseudo"),
        pytest.param(":dir(sideways)", "descendant-or-self::*[false()]", id="dir-unknown-direction"),
        pytest.param(":is(:unknown-pseudo)", "descendant-or-self::*[false()]", id="is-forgiving-empty"),
    ],
)
def test_translation(selector: str, expected: str) -> None:
    assert css_to_xpath(selector) == expected


def test_class_name_with_both_quote_kinds_uses_concat() -> None:
    assert css_to_xpath(r".it\27 s\22 x") == (
        "descendant-or-self::*[@class and contains(concat(' ', normalize-space(@class), ' '),"
        " concat(' it',\"'\",'s\"x '))]"
    )


def test_prefix_replaces_the_default_scope() -> None:
    assert css_to_xpath("div p", prefix="descendant::") == "descendant::div/descendant::p"


def test_prefix_applies_to_every_union_arm() -> None:
    assert css_to_xpath("a, b", prefix="//") == "//a | //b"


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("", id="empty"),
        pytest.param("div >", id="dangling-combinator"),
        pytest.param(":unknown-pseudo", id="unknown-pseudo"),
        pytest.param("[href=", id="unclosed-attribute"),
        pytest.param("p::before", id="pseudo-element"),
        pytest.param(":not()", id="empty-not"),
    ],
)
def test_syntax_error(selector: str) -> None:
    with pytest.raises(SelectorSyntaxError, match="invalid CSS selector"):
        css_to_xpath(selector)


@pytest.mark.parametrize(
    ("selector", "match"),
    [
        pytest.param(":dir(ltr)", ":dir", id="dir"),
        pytest.param(":default", ":default", id="default"),
        pytest.param("*:first-of-type", "type selector", id="first-of-type-universal"),
        pytest.param(".c:last-of-type", "type selector", id="last-of-type-untyped"),
        pytest.param("*:only-of-type", "type selector", id="only-of-type-universal"),
        pytest.param("*:nth-of-type(2)", "type selector", id="nth-of-type-universal"),
        pytest.param("div:scope", ":scope", id="scope-compounded"),
        pytest.param("div :scope", ":scope", id="scope-not-leading"),
        pytest.param(":is(:scope)", ":scope", id="scope-nested"),
        pytest.param("a, :dir(rtl)", ":dir", id="error-in-later-arm"),
        pytest.param(":dir(rtl), a", ":dir", id="error-in-earlier-arm"),
    ],
)
def test_expression_error(selector: str, match: str) -> None:
    with pytest.raises(ExpressionError, match=match):
        css_to_xpath(selector)


def test_syntax_error_is_the_one_unified_selector_error() -> None:
    assert SelectorSyntaxError is turbohtml.SelectorSyntaxError is match.SelectorSyntaxError
    assert issubclass(SelectorSyntaxError, ValueError)


def test_expression_error_keeps_the_cssselect_shape() -> None:
    assert issubclass(ExpressionError, SelectorError)
    assert issubclass(ExpressionError, RuntimeError)


def test_non_str_selector_raises_type_error() -> None:
    with pytest.raises(TypeError):
        css_to_xpath(123)  # ty: ignore[invalid-argument-type]  # intentional non-str exercises the C argument guard


def test_generic_translator_is_a_method_like_cssselect() -> None:
    assert GenericTranslator().css_to_xpath("div p") == css_to_xpath("div p")


def test_generic_translator_passes_the_prefix_positionally() -> None:
    assert GenericTranslator().css_to_xpath("div", "//") == "//div"


def test_html_translator_records_the_xhtml_flag() -> None:
    assert HTMLTranslator().xhtml is False
    assert HTMLTranslator(xhtml=True).xhtml is True


def test_html_translator_translates_like_the_function() -> None:
    assert HTMLTranslator().css_to_xpath("A[HREF]") == css_to_xpath("a[href]")
