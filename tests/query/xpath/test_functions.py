from __future__ import annotations

import math
import operator
import random
import re
import string
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # the test runs a fixed command in a subprocess
import sys
from functools import partial
from string import ascii_lowercase, ascii_uppercase
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias, cast
from xml.etree import ElementTree as ET  # ruff:ignore[suspicious-xml-etree-import]  # parses fixed markup from the test corpus, not untrusted input

import pytest
from bench.ci import benchmarks
from bench.operations import INPUTS

import turbohtml
from turbohtml import Document, Element, Range, Text, XPath, parse, parse_xml
from turbohtml.clean import LinkCandidate, Linkify, linkify_node

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from types import ModuleType, SimpleNamespace

    from bench.operations import Case
    from pytest_mock import MockerFixture
    from work_steps import WorkSteps


_LONG_NEEDLE: Final[str] = f"{'a' * 65}ba"


_LATE_HAY: Final[str] = "a" * 200 + _LONG_NEEDLE


@pytest.fixture
def doc() -> turbohtml.Node:
    html: Final[str] = (
        "<html><body><div class='a b'><p>one</p><p class='x'>two</p><p>three</p></div>"
        "<ul><li>1</li><li>2</li><li>3</li></ul>"
        "<a href='/y'>L</a><input disabled><!--note--></body></html>"
    )
    return turbohtml.parse(html)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        # arithmetic
        pytest.param("1 + 2 * 3", 7.0, id="precedence"),
        pytest.param("(1 + 2) * 3", 9.0, id="grouping"),
        pytest.param("7 div 2", 3.5, id="div"),
        pytest.param("7 mod 3", 1.0, id="mod"),
        pytest.param("-5 + 1", -4.0, id="unary-minus"),
        # numeric functions
        pytest.param("count(//p)", 3.0, id="count"),
        pytest.param("count(//li)", 3.0, id="count-li"),
        pytest.param("sum(//li)", 6.0, id="sum"),
        pytest.param("string-length('hello')", 5.0, id="string-length"),
        pytest.param("floor(3.7)", 3.0, id="floor"),
        pytest.param("ceiling(3.2)", 4.0, id="ceiling"),
        pytest.param("round(3.5)", 4.0, id="round"),
        # round() resolves ties toward positive infinity (XPath 1.0 §4.4), so a
        # negative .5 rounds up, not away from zero as C round() would
        pytest.param("round(-2.5)", -2.0, id="round-neg-half-up"),
        pytest.param("round(-0.5)", 0.0, id="round-neg-half-to-zero"),
        pytest.param("round(2.5)", 3.0, id="round-pos-half-up"),
        # the largest double below 0.5 rounds down; adding 0.5 first would round the sum up to 1
        pytest.param("round(0.5 - 1 div 18014398509481984)", 0.0, id="round-just-below-half"),
        # 2^52 + 1 is already an integer; adding 0.5 first would land on 2^52 + 2
        pytest.param("round(4503599627370497)", 4503599627370497.0, id="round-odd-integer-past-2-pow-52"),
        # [-0.5, 0) rounds to negative zero, whose reciprocal is negative infinity
        pytest.param("1 div round(-0.4)", float("-inf"), id="round-small-negative-to-negative-zero"),
        pytest.param("1 div round(-0.5)", float("-inf"), id="round-negative-half-to-negative-zero"),
        pytest.param("1 div round(-0)", float("-inf"), id="round-keeps-negative-zero"),
        pytest.param("1 div round(0)", float("inf"), id="round-keeps-positive-zero"),
        pytest.param("round(-1 div 0)", float("-inf"), id="round-negative-infinity"),
        pytest.param("string(round(0 div 0))", "NaN", id="round-nan"),
        pytest.param("number('3.5')", 3.5, id="number-string"),
        pytest.param("number(true())", 1.0, id="number-bool"),
        pytest.param("number(false())", 0.0, id="number-false"),
        pytest.param("number(' -2.50 ')", -2.5, id="number-whitespace"),
        pytest.param("number('.5')", 0.5, id="number-leading-dot"),
        # numbers convert to the nearest double, as Python's float() does
        pytest.param("0.49999999999999994 < 0.5", True, id="literal-just-below-half"),
        pytest.param("number('0.49999999999999994') < 0.5", True, id="number-just-below-half"),
        pytest.param("number('0.009221885624698875')", 0.009221885624698875, id="number-seventeen-digits"),
        pytest.param("1.7976931348623157", 1.7976931348623157, id="literal-seventeen-digits"),
        pytest.param("number('9007199254740993')", 9007199254740992.0, id="number-past-2-pow-53-ties-to-even"),
        pytest.param("12345678901234567890", 12345678901234567890.0, id="literal-past-2-pow-53"),
        pytest.param("number('0.0000000000000000000000000000001')", 1e-31, id="number-past-22-fraction-digits"),
        pytest.param("0.0000000000000000000000000000001", 1e-31, id="literal-past-22-fraction-digits"),
        pytest.param("string(number(string(0.1 + 0.2))) = string(0.1 + 0.2)", True, id="string-number-round-trip"),
        pytest.param("number(//li)", 1.0, id="number-nodeset"),
        pytest.param("5 - 2", 3.0, id="subtraction"),
        # string functions
        pytest.param("string(42)", "42", id="string-number"),
        pytest.param("string(3.5)", "3.5", id="string-decimal"),
        pytest.param("string(true())", "true", id="string-bool"),
        pytest.param("string(//p)", "one", id="string-nodeset"),
        pytest.param("string(//zzz)", "", id="string-empty-nodeset"),
        pytest.param("string(false())", "false", id="string-false"),
        pytest.param("string(1 div 0)", "Infinity", id="string-infinity"),
        pytest.param("string(-1 div 0)", "-Infinity", id="string-neg-infinity"),
        pytest.param("string(0 div 0)", "NaN", id="string-nan"),
        pytest.param("string(1000000000000000)", "1000000000000000", id="string-huge"),
        pytest.param("string(//input/@disabled)", "", id="string-valueless-attr"),
        pytest.param("string(//comment())", "note", id="string-comment"),
        pytest.param("concat('a', 'b', 'c')", "abc", id="concat"),
        pytest.param("normalize-space('  a   b ')", "a b", id="normalize-space"),
        pytest.param("normalize-space('')", "", id="normalize-space-empty"),
        pytest.param("normalize-space('   ')", "", id="normalize-space-blank"),
        pytest.param("substring('hello', 2)", "ello", id="substring-2"),
        pytest.param("substring('hello', 2, 3)", "ell", id="substring-3"),
        pytest.param("substring('hello', 0, 2)", "h", id="substring-clamp-low"),
        pytest.param("substring('hello', 10)", "", id="substring-past-end"),
        pytest.param("substring('hello', 2, 100)", "ello", id="substring-clamp-high"),
        # substring rounds its numeric arguments with the XPath round (ties toward
        # positive infinity): round(1.5)=2 and round(2.6)=3 select positions 2..4
        pytest.param("substring('12345', 1.5, 2.6)", "234", id="substring-rounded-args"),
        pytest.param("substring('abcdef', 0.5 - 1 div 18014398509481984, 2)", "a", id="substring-start-below-half"),
        pytest.param("substring('hello', 3, -1)", "", id="substring-empty"),
        pytest.param("substring('', 1, 1)", "", id="substring-of-empty"),
        pytest.param("substring-before('a/b/c', '/')", "a", id="substring-before"),
        pytest.param("substring-after('a/b/c', '/')", "b/c", id="substring-after"),
        pytest.param("substring-before('abc', '/')", "", id="substring-before-miss"),
        pytest.param("translate('bar', 'abc', 'ABC')", "BAr", id="translate"),
        pytest.param("translate('abcd', 'bc', 'x')", "axd", id="translate-delete"),
        pytest.param("contains('abc', '')", True, id="contains-empty"),
        pytest.param("string('')", "", id="string-empty-literal"),
        pytest.param("concat('', '')", "", id="concat-empty"),
        pytest.param("substring('hello', 0, 0)", "", id="substring-zero-length"),
        pytest.param("starts-with('hello', 'he')", True, id="starts-with-true"),
        pytest.param("starts-with('he', 'hello')", False, id="starts-with-longer-needle"),
        pytest.param("starts-with('hello', 'xx')", False, id="starts-with-miss"),
        # XPath 2.0 string convenience functions
        pytest.param("ends-with('hello', 'lo')", True, id="ends-with-true"),
        pytest.param("ends-with('hello', 'he')", False, id="ends-with-miss"),
        pytest.param("ends-with('hello', '')", True, id="ends-with-empty-suffix"),
        pytest.param("ends-with('lo', 'hello')", False, id="ends-with-longer-suffix"),
        pytest.param("string-join(//p, ',')", "one,two,three", id="string-join-nodeset"),
        pytest.param("string-join(//div, '|')", "onetwothree", id="string-join-single-node"),
        pytest.param("string-join(//zzz, ',')", "", id="string-join-empty-nodeset"),
        pytest.param("string-join('abc', '-')", "abc", id="string-join-string"),
        pytest.param("lower-case('Héllo WÖRLD')", "héllo wörld", id="lower-case"),
        pytest.param("upper-case('Héllo wörld')", "HÉLLO WÖRLD", id="upper-case"),
        pytest.param("matches('abc123', '[0-9]+')", True, id="matches-true"),
        pytest.param("matches('abcdef', '[0-9]+')", False, id="matches-miss"),
        pytest.param("matches('ABC', 'abc', 'i')", True, id="matches-flags"),
        pytest.param("matches('ABC', 'abc', 'iiiiimmmmmsssssxxxxx')", True, id="matches-repeated-flags"),
        # a number's string is an exact-length buffer, so `$` must stop at its end without reading past it
        pytest.param("matches(12, '2$')", True, id="matches-end-of-number"),
        pytest.param("matches(12, '$', 'm')", True, id="matches-multiline-end-of-number"),
        pytest.param("matches(11, '(.)\\1$')", True, id="matches-backref-end-of-number"),
        pytest.param("matches('x\n', 'x$')", True, id="matches-end-before-final-newline"),
        pytest.param("replace(12, '$', 'X', 'm')", "12X", id="replace-multiline-end-of-number"),
        pytest.param("replace('a1b2c3', '[0-9]', '#')", "a#b#c#", id="replace-all"),
        pytest.param("replace('2024-05-06', '(\\d+)-(\\d+)-(\\d+)', '$3/$2/$1')", "06/05/2024", id="replace-groups"),
        pytest.param("replace('a', '(a)', '$1z')", "az", id="replace-group-then-letter"),
        pytest.param("replace('ABCabc', 'b', 'X', 'i')", "AXCaXc", id="replace-flags"),
        pytest.param("replace('a', 'a', 'x$')", "x$", id="replace-trailing-dollar"),
        pytest.param("replace('a', 'a', '$x')", "$x", id="replace-dollar-above-digit"),
        pytest.param("replace('a', 'a', '$.')", "$.", id="replace-dollar-below-digit"),
        pytest.param("replace('a', 'a', '\\$')", "$", id="replace-escaped-dollar"),
        pytest.param("replace('a', 'a', '\\\\')", "\\", id="replace-escaped-backslash"),
        pytest.param("replace('a', 'a', '\\x')", "\\x", id="replace-lone-backslash"),
        pytest.param("replace('a', 'a', 'x\\')", "x\\", id="replace-trailing-backslash"),
        pytest.param("name(//comment())", "", id="name-of-comment"),
        pytest.param("'a' = 1", False, id="string-eq-number"),
        pytest.param("local-name(//p)", "p", id="local-name"),
        pytest.param("name(//li)", "li", id="name"),
        pytest.param("name(//p/@class)", "class", id="name-attribute"),
        pytest.param("name(//a/@href)", "href", id="name-href-attribute"),
        pytest.param("local-name(//zzz)", "", id="local-name-empty"),
        pytest.param("local-name(1)", "", id="local-name-non-nodeset"),
        # boolean functions and operators
        pytest.param("true()", True, id="true"),
        pytest.param("false()", False, id="false"),
        pytest.param("not(1 = 1)", False, id="not"),
        pytest.param("boolean(1)", True, id="boolean-number"),
        pytest.param("boolean(0)", False, id="boolean-zero"),
        pytest.param("boolean('')", False, id="boolean-empty"),
        pytest.param("boolean(//p)", True, id="boolean-nodeset"),
        pytest.param("boolean(//zzz)", False, id="boolean-empty-nodeset"),
        pytest.param("boolean(0 div 0)", False, id="boolean-nan"),
        pytest.param("1 = 1", True, id="eq"),
        pytest.param("1 != 2", True, id="ne"),
        pytest.param("1 < 2", True, id="lt"),
        pytest.param("2 <= 2", True, id="le"),
        pytest.param("3 > 2", True, id="gt"),
        pytest.param("2 >= 3", False, id="ge"),
        pytest.param("'a' = 'a'", True, id="string-eq"),
        pytest.param("'a' = 'b'", False, id="string-ne"),
        pytest.param("'a' = 'bb'", False, id="string-ne-length"),
        pytest.param("true() = false()", False, id="bool-eq-bool"),
        pytest.param("1 = true()", True, id="number-eq-bool"),
        pytest.param("1 = 1 and 2 = 2", True, id="and"),
        pytest.param("1 = 2 or 2 = 2", True, id="or"),
        pytest.param("1 = 2 and 2 = 2", False, id="and-short-circuit"),
        pytest.param("1 = 1 or 2 = 3", True, id="or-short-circuit"),
        # node-set comparisons (existential)
        pytest.param("//p/text() = 'two'", True, id="nodeset-eq-string"),
        pytest.param("'two' = //p/text()", True, id="string-eq-nodeset"),
        pytest.param("//p/text() = 'nope'", False, id="nodeset-eq-string-miss"),
        pytest.param("//li > 2", True, id="nodeset-gt-number"),
        pytest.param("//p = true()", True, id="nodeset-eq-bool"),
        pytest.param("false() = //zzz", True, id="bool-eq-empty-nodeset"),
        pytest.param("//zzz = false()", True, id="empty-nodeset-eq-bool"),
        pytest.param("//li = //li", True, id="nodeset-eq-nodeset"),
        pytest.param("//p/text() = //li/text()", False, id="nodeset-eq-nodeset-miss"),
        # 0-argument functions operate on the context node
        pytest.param("count(//p[normalize-space()='one'])", 1.0, id="context-normalize-space"),
        pytest.param("count(//p[string()='one'])", 1.0, id="context-string"),
        pytest.param("count(//p[string-length()=3])", 2.0, id="context-string-length"),
        pytest.param("count(//p[name()='p'])", 3.0, id="context-name"),
        pytest.param("count(//li[number()=2])", 1.0, id="context-number"),
    ],
)
def test_scalar_and_boolean(doc: turbohtml.Node, expr: str, *, expected: float | str | bool) -> None:
    assert doc.xpath(expr) == expected


_XPATH_NUMBER: Final[re.Pattern[str]] = re.compile(
    r"""
    [ \t\r\n]*                           # XML whitespace around the number
    -?                                     # an optional sign
    (?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)       # digits with an optional fraction, or a bare fraction
    [ \t\r\n]*                           # XML whitespace around the number
    """,
    re.VERBOSE,
)
_RELATIONAL: Final[Mapping[str, Callable[[float, float], bool]]] = MappingProxyType({
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
})
_NODE_SET_SIZES: Final[tuple[int, ...]] = (0, 1, 2, 15, 16, 17)


@pytest.mark.parametrize("right_size", _NODE_SET_SIZES)
@pytest.mark.parametrize("left_size", _NODE_SET_SIZES)
@pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
def test_node_set_relational_comparison_is_existential(left_size: int, right_size: int, op: str) -> None:
    texts: Final[tuple[str, ...]] = ("1", "2", "-1", "0.5", " 3 ", "x", "", "NaN")
    rng = random.Random(f"{op}-{left_size}-{right_size}")  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # a fixed corpus
    left: Final[list[str]] = [rng.choice(texts) for _ in range(left_size)]
    right: Final[list[str]] = [rng.choice(texts) for _ in range(right_size)]
    expected: Final[bool] = any(_RELATIONAL[op](_xpath_number(a), _xpath_number(b)) for a in left for b in right)
    assert _node_set_document(left, right).xpath(f"//li {op} //b") is expected


def _xpath_number(text: str) -> float:
    return float(text) if _XPATH_NUMBER.fullmatch(text) else math.nan


def _node_set_document(left: list[str], right: list[str]) -> turbohtml.Node:
    left_items: Final[str] = "".join(f"<li>{text}</li>" for text in left)
    right_items: Final[str] = "".join(f"<b>{text}</b>" for text in right)
    return parse(f"<main><ul>{left_items}</ul><section>{right_items}</section></main>")


@pytest.mark.parametrize("size", [1, 2, 15, 16, 17])
@pytest.mark.parametrize(
    ("op", "fillers", "lasts", "expected"),
    [
        pytest.param("<", ("9", "0"), ("1", "2"), True, id="lt-match"),
        pytest.param("<", ("9", "0"), ("2", "1"), False, id="lt-miss"),
        pytest.param("<=", ("9", "0"), ("1", "2"), True, id="le-match"),
        pytest.param("<=", ("9", "0"), ("2", "1"), False, id="le-miss"),
        pytest.param(">", ("0", "9"), ("2", "1"), True, id="gt-match"),
        pytest.param(">", ("0", "9"), ("1", "2"), False, id="gt-miss"),
        pytest.param(">=", ("0", "9"), ("2", "1"), True, id="ge-match"),
        pytest.param(">=", ("0", "9"), ("1", "2"), False, id="ge-miss"),
    ],
)
def test_node_set_relational_comparison_checks_past_the_first_pair(
    size: int, op: str, fillers: tuple[str, str], lasts: tuple[str, str], *, expected: bool
) -> None:
    left: Final[list[str]] = [fillers[0]] * (size - 1) + [lasts[0]]
    right: Final[list[str]] = [fillers[1]] * (size - 1) + [lasts[1]]
    assert _node_set_document(left, right).xpath(f"//li {op} //b") is expected


@pytest.mark.parametrize("function", ["name", "local-name"])
@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param("class", id="ascii"),
        pytest.param("été", id="latin-1"),
        pytest.param("ж", id="cyrillic"),
        pytest.param("a\ud800b", id="lone-surrogate"),
    ],
)
def test_name_of_attribute_keeps_its_characters(function: str, attribute: str) -> None:
    assert turbohtml.parse(f"<p {attribute}='1'>x</p>").xpath(f"{function}(//p/@*)") == attribute


@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param("class", id="ascii"),
        pytest.param("été", id="latin-1"),
        pytest.param("a\ud800b", id="lone-surrogate"),
    ],
)
def test_smart_string_attrname_keeps_its_characters(attribute: str) -> None:
    results = turbohtml.parse(f"<p {attribute}='1'>x</p>").xpath("//p/@*", smart_strings=True)
    assert isinstance(results, list)
    (value,) = results
    assert isinstance(value, turbohtml.XPathString)
    assert value.attrname == attribute


@pytest.mark.parametrize(
    ("expression", "hay", "needle", "expected"),
    [
        pytest.param("contains($hay, $needle)", "a" * 400, f"{'a' * 65}b", False, id="prefilter-miss"),
        pytest.param(
            "contains($hay, $needle)",
            _LATE_HAY,
            _LONG_NEEDLE,
            True,
            id="late-match",
        ),
        pytest.param("contains($hay, $needle)", f"{'a' * 200}c{'a' * 199}", _LONG_NEEDLE, False, id="kmp-miss"),
        pytest.param(
            "substring-before($hay, $needle)",
            _LATE_HAY,
            _LONG_NEEDLE,
            "a" * 200,
            id="substring-before",
        ),
        pytest.param(
            "substring-after($hay, $needle)",
            f"{_LATE_HAY}z",
            _LONG_NEEDLE,
            "z",
            id="substring-after",
        ),
        pytest.param("contains($hay, $needle)", "a" * 65, _LONG_NEEDLE, False, id="needle-longer"),
        pytest.param("contains($hay, $needle)", _LONG_NEEDLE, _LONG_NEEDLE, True, id="short-hay"),
        pytest.param("contains($hay, $needle)", f"x{'a' * 65}ca", _LONG_NEEDLE, False, id="short-hay-miss"),
    ],
)
def test_long_string_search(
    doc: turbohtml.Node, expression: str, hay: str, needle: str, *, expected: str | bool
) -> None:
    assert doc.xpath(expression, hay=hay, needle=needle) == expected


def test_number_parse_edge_cases(doc: turbohtml.Node) -> None:
    assert math.isnan(number(doc, "number('1.2.3')"))
    assert math.isnan(number(doc, "number('12x')"))
    assert math.isnan(number(doc, "number('.')"))


def number(node: turbohtml.Node, expr: str) -> float:
    result = node.xpath(expr)
    assert isinstance(result, float)
    return result


def test_number_of_non_numeric_string_is_nan(doc: turbohtml.Node) -> None:
    assert math.isnan(number(doc, "number('hello')"))
    assert math.isnan(number(doc, "number('')"))


def test_scalar_through_xpath_one(doc: turbohtml.Node) -> None:
    assert doc.xpath_one("count(//p)") == pytest.approx(3.0)
    assert doc.xpath_one("string(//p)") == "one"


def test_scalar_through_xpath_iter(doc: turbohtml.Node) -> None:
    assert list(doc.xpath_iter("count(//p)")) == [3.0]


def test_filter_base_node_set_continues_as_path(doc: turbohtml.Node) -> None:
    # a parenthesised node-set followed by a step
    assert tags(doc.xpath("(//div)/p")) == ["p", "p", "p"]


def tags(result: XPathResult) -> list[str]:
    return [node.tag if isinstance(node, Element) else str(node) for node in node_list(result)]


def node_list(result: XPathResult) -> Sequence[turbohtml.Node | str | float]:
    # The node-set list an expression is expected to return.
    assert isinstance(result, list)
    return result


XPathResult: TypeAlias = "Sequence[turbohtml.Node | str | float] | float | bool | str"


# An unknown function name is a static error (XPath 1.0 §3.2); nested inside any
# expression form the ValueError still propagates out, naming the offending function.
@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("bogus-fn(1)", id="unknown-function"),
        pytest.param("count(bogus-fn(1))", id="in-function-arg"),
        pytest.param("concat('x', bogus-fn(1))", id="in-later-function-arg"),
        pytest.param("concat('a', 'b', 'c', bogus-fn(1))", id="in-heap-function-arg"),
        pytest.param("//p[bogus-fn(1)]", id="in-predicate"),
        pytest.param("(bogus-fn(1))[1]", id="in-filter-primary"),
        pytest.param("(//p)[bogus-fn(1)]", id="in-filter-predicate"),
        pytest.param("(bogus-fn(1))/p", id="in-filter-base"),
        pytest.param("bogus-fn(1) | //p", id="in-union-left"),
        pytest.param("//p | bogus-fn(1)", id="in-union-right"),
        pytest.param("bogus-fn(1) or 1", id="in-or-left"),
        pytest.param("false() or bogus-fn(1)", id="in-or-right"),
        pytest.param("bogus-fn(1) and 1", id="in-and-left"),
        pytest.param("true() and bogus-fn(1)", id="in-and-right"),
        pytest.param("- bogus-fn(1)", id="in-negation"),
        pytest.param("bogus-fn(1) = 1", id="in-compare-left"),
        pytest.param("1 = bogus-fn(1)", id="in-compare-right"),
    ],
)
def test_unknown_function_raises(doc: turbohtml.Node, expr: str) -> None:
    with pytest.raises(ValueError, match=r"xpath: unknown function 'bogus-fn'"):
        doc.xpath(expr)


# A scalar where the grammar requires a node-set is a type error, not an unimplemented
# feature: the function arguments, the path base, a predicate base, and a union operand.
@pytest.mark.parametrize(
    ("expr", "message"),
    [
        pytest.param("count('x')", "count.. of a non-node-set", id="count-non-nodeset"),
        pytest.param("sum('x')", "sum.. of a non-node-set", id="sum-non-nodeset"),
        pytest.param("(1)/p", "path step on a non-node-set", id="path-on-non-nodeset"),
        pytest.param("(1)[1]", "predicate on a non-node-set", id="predicate-on-non-nodeset"),
        pytest.param("//a | 1", "union of non-node-sets", id="union-of-non-nodesets"),
        pytest.param("1 | //a", "union of non-node-sets", id="union-non-nodeset-left"),
    ],
)
def test_non_nodeset_raises_type_error(doc: turbohtml.Node, expr: str, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        doc.xpath(expr)


def test_namespace_axis(doc: turbohtml.Node) -> None:
    # every element exposes the implicit xml namespace node; it marshals to its URI
    uri = "http://www.w3.org/XML/1998/namespace"
    assert doc.xpath("//p/namespace::*") == [uri, uri, uri]  # three <p> elements
    assert doc.xpath("//p/namespace::xml") == [uri, uri, uri]
    assert doc.xpath("//p/namespace::node()") == [uri, uri, uri]
    assert doc.xpath("//p/namespace::other") == []  # wrong length
    assert doc.xpath("//p/namespace::aml") == []  # wrong first character
    assert doc.xpath("//p/namespace::xyz") == []  # wrong second character
    assert doc.xpath("//p/namespace::xmz") == []  # wrong third character
    assert doc.xpath("//p/namespace::text()") == []
    assert doc.xpath("name(//p/namespace::*)") == "xml"
    assert doc.xpath("//p/namespace::*/a") == []  # a namespace node has no axes
    # an attribute and the namespace node of the same element sort node-then-namespace
    assert doc.xpath("count(//p/@class | //p/namespace::*)") == pytest.approx(4.0)


# A core function has a fixed arity (XPath 1.0 §4). Too few arguments used to read an
# uninitialized args[] slot and fault; too many were silently ignored. Both now raise.
@pytest.mark.parametrize(
    ("expr", "message"),
    [
        pytest.param("count()", "count() takes 1 argument, got 0", id="count-too-few"),
        pytest.param("count(//a, //a)", "count() takes 1 argument, got 2", id="count-too-many"),
        pytest.param("true(1)", "true() takes 0 arguments, got 1", id="niladic-too-many"),
        pytest.param("starts-with()", "starts-with() takes 2 arguments, got 0", id="starts-with-too-few"),
        pytest.param("sum()", "sum() takes 1 argument, got 0", id="sum-too-few"),
        pytest.param("floor()", "floor() takes 1 argument, got 0", id="floor-too-few"),
        pytest.param("id()", "id() takes 1 argument, got 0", id="id-too-few"),
        pytest.param("translate('a')", "translate() takes 3 arguments, got 1", id="fixed-three-too-few"),
        pytest.param("substring('a')", "substring() takes 2 to 3 arguments, got 1", id="range-too-few"),
        pytest.param("substring('a', 1, 2, 3)", "substring() takes 2 to 3 arguments, got 4", id="range-too-many"),
        pytest.param("concat('a')", "concat() takes at least 2 arguments, got 1", id="variadic-too-few"),
        pytest.param("ends-with('a')", "ends-with() takes 2 arguments, got 1", id="ends-with-too-few"),
        pytest.param("string-join(//p)", "string-join() takes 2 arguments, got 1", id="string-join-too-few"),
        pytest.param("lower-case()", "lower-case() takes 1 argument, got 0", id="lower-case-too-few"),
        pytest.param("upper-case('a', 'b')", "upper-case() takes 1 argument, got 2", id="upper-case-too-many"),
        pytest.param("matches('a')", "matches() takes 2 to 3 arguments, got 1", id="matches-too-few"),
        pytest.param("replace('a', 'b')", "replace() takes 3 to 4 arguments, got 2", id="replace-too-few"),
    ],
)
def test_wrong_arity_raises_value_error(doc: turbohtml.Node, expr: str, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        doc.xpath(expr)


# matches/replace surface Python's re.error for a malformed pattern, the same way the
# EXSLT re: functions do.
@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("matches('a', '[')", id="matches"),
        pytest.param("replace('a', '[', 'x')", id="replace"),
    ],
)
def test_regex_functions_reject_a_malformed_pattern(doc: turbohtml.Node, expr: str) -> None:
    with pytest.raises(re.error):
        doc.xpath(expr)


def test_count_of_a_nodeset_is_the_length_not_uninitialized_memory(doc: turbohtml.Node) -> None:
    # count() with no argument used to return an uninitialized stack double
    assert doc.xpath("count(//zzz)") == pytest.approx(0.0)
    assert doc.xpath("count(//p)") == pytest.approx(3.0)


def test_concat_grows_past_the_old_eight_argument_buffer(doc: turbohtml.Node) -> None:
    assert doc.xpath("concat(1,2,3,4,5,6,7,8,9,0)") == "1234567890"


# XPath 1.0 §4.2: a number stringifies to the fewest digits that round-trip the double,
# always in positional notation (no exponent).
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(1 / 3, "0.3333333333333333", id="one-third"),
        pytest.param(0.1, "0.1", id="tenth"),
        pytest.param(0.2, "0.2", id="fifth"),
        pytest.param(2 / 3, "0.6666666666666666", id="two-thirds"),
        pytest.param(1e-7, "0.0000001", id="small-decimal"),
        pytest.param(1.5e-7, "0.00000015", id="small-decimal-mantissa"),
        pytest.param(1e21, "1000000000000000000000", id="large-integer"),
        pytest.param(1.25e21, "1250000000000000000000", id="large-integer-mantissa"),
        pytest.param(42.0, "42", id="integer-fast-path"),
        pytest.param(-0.5, "-0.5", id="negative-fraction"),
        pytest.param(1234567890123456.7, "1234567890123456.8", id="seventeen-significant-digits"),
    ],
)
def test_number_to_string_is_shortest_decimal(value: float, expected: str) -> None:
    doc = turbohtml.parse("<r/>")
    assert doc.xpath("string($v)", v=value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(math.pi, id="pi"),
        pytest.param(math.e, id="e"),
        pytest.param(123.456, id="mixed"),
        pytest.param(9.999999999999999e-5, id="seventeen-digits"),
        pytest.param(5e-324, id="smallest-subnormal"),
    ],
)
def test_number_to_string_round_trips(value: float) -> None:
    rendered = turbohtml.parse("<r/>").xpath("string($v)", v=value)
    assert isinstance(rendered, str)
    # the rendered decimal parses back to the bit-identical double it came from
    assert float(rendered).hex() == float(value).hex()


# elementpath is the reference XPath 2.0 processor for ElementTree; the string
# convenience subset must agree with it over the same document and literal arguments.
@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("ends-with('hello', 'lo')", id="ends-with-true"),
        pytest.param("ends-with('hello', 'x')", id="ends-with-false"),
        pytest.param("string-join(//p, ', ')", id="string-join-nodeset"),
        pytest.param("lower-case(//p[1])", id="lower-case-node"),
        pytest.param("upper-case(//p[2])", id="upper-case-node"),
        pytest.param("matches('abc123', '[0-9]+')", id="matches"),
        pytest.param("matches('ABC', 'abc', 'i')", id="matches-flags"),
        pytest.param("replace('a1b2c3', '[0-9]', '#')", id="replace-all"),
        pytest.param(r"replace('2024-05-06', '(\d+)-(\d+)-(\d+)', '$3/$2/$1')", id="replace-groups"),
    ],
)
def test_string_functions_agree_with_elementpath(expr: str) -> None:
    elementpath: Final[ModuleType] = pytest.importorskip("elementpath")
    markup = "<div><p>One</p><p>Two</p><p>Three</p></div>"
    root = ET.fromstring(markup)  # ruff:ignore[suspicious-xml-element-tree-usage]  # parses fixed markup from the test corpus, not untrusted input
    want = elementpath.select(root, expr, parser=elementpath.XPath2Parser)
    assert turbohtml.parse(markup).xpath(expr) == want


@pytest.mark.parametrize(
    ("text", "source", "target", "expected"),
    [
        pytest.param("a" * 32768, ascii_lowercase, ascii_uppercase, "A" * 32768, id="first-map-entry"),
        pytest.param("b" * 32768, ascii_lowercase, ascii_uppercase, "B" * 32768, id="second-map-entry"),
        pytest.param("a" * 64, "a" * 65536, "X", "X" * 64, id="duplicate-first-entry"),
        pytest.param("a" * 64, "a" * 65536, "", "", id="duplicate-first-removal"),
        pytest.param(
            f"{'yz' * 64}a", "abcdefghijklmnopaa", "ABCDEFGHIJKLMNOPXY", f"{'yz' * 64}A", id="duplicate-after-index"
        ),
        pytest.param(
            f"{'yz' * 64}界😀", "abcdefghijklmnop界😀", "ABCDEFGHIJKLMNOPé", f"{'yz' * 64}é", id="unicode-after-index"
        ),
        pytest.param(f"{'a' * 128}z", ascii_lowercase, "A", "A" * 128, id="late-removal"),
        pytest.param("yz" * 64, "", "X", "yz" * 64, id="empty-map"),
        pytest.param("", ascii_lowercase, "A", "", id="empty-input"),
    ],
)
def test_translate_deferred_index(text: str, source: str, target: str, expected: str) -> None:
    document: Final[Document] = parse("<p></p>")
    assert document.xpath(f"translate('{text}', '{source}', '{target}')") == expected


def test_translate_nul_mapping() -> None:
    document: Final[Document] = parse("<p></p>")
    assert (
        document.xpath(
            "translate($text, $source, $target)",
            text=f"{'yz' * 64}\x00a",
            source="abcdefghijklmnop\x00",
            target="ABCDEFGHIJKLMNOP!",
        )
        == f"{'yz' * 64}!A"
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("translate(102, '0', 'x')", "1x2", id="number-text"),
        pytest.param("translate('120', 2, 'x')", "1x0", id="number-source"),
        pytest.param("translate('120', '2', 9)", "190", id="number-target"),
        pytest.param("translate(true(), 'tr', 'TR')", "TRue", id="boolean-text"),
    ],
)
def test_translate_coerced_arguments(expression: str, expected: str) -> None:
    assert parse("<p/>").xpath(expression) == expected


@pytest.mark.parametrize(
    ("source", "target", "text", "expected"),
    [
        pytest.param("abcdefghijklmnop", "ABCDEFGHIJKLMNOP", "apz" * 64, "APz" * 64, id="map-and-miss"),
        pytest.param("abcdefghijklmnop", "A", "apz" * 64, "Az" * 64, id="delete"),
        pytest.param("a" * 16, "ABCDEFGHIJKLMNOP", "a" * 64, "A" * 64, id="first-duplicate"),
        pytest.param("雪" * 16, "𐀀", "雪z" * 64, "𐀀z" * 64, id="wide-unicode"),
        pytest.param("abcdefghijklmnop", "", "a" * 64, "", id="delete-all"),
        pytest.param("abcdefghijklmno", "A", "az" * 64, "Az" * 64, id="short-map"),
        pytest.param("abcdefghijklmnop", "A", "a" * 63, "A" * 63, id="short-text"),
        pytest.param("abcdefghijklmnop", "A", "a" * 64, "A" * 64, id="threshold"),
        pytest.param("", "A", "a" * 64, "a" * 64, id="empty-map"),
        pytest.param("abcdefghijklmnop", "A", "", "", id="empty-text"),
        pytest.param(
            "".join(chr(256 + index * 128) for index in range(16)),
            "ABCDEFGHIJKLMNOP",
            "".join(chr(256 + index * 128) for index in range(16)) * 4,
            "ABCDEFGHIJKLMNOP" * 4,
            id="hash-collisions",
        ),
    ],
)
def test_translate_character_map(source: str, target: str, text: str, expected: str) -> None:
    document: Final[Document] = parse_xml(f"<root>{text}</root>")
    assert document.xpath(f"translate(string(/root), '{source}', '{target}')") == expected


@pytest.fixture
def ids() -> turbohtml.Node:
    return turbohtml.parse(
        "<html><body><p id='a'>one</p><p id='b'>two</p><div class='x' id='c'>three</div>"
        f"<span>no id</span><i id=''>empty</i><!--note-->{_ID_PADDING}</body></html>"
    )


# the first id() of an evaluation walks a tree of more than 256 nodes (XP_ID_INDEX_MIN_NODES in functions.c) and the
# next one reads an index of it, so a case runs both ways on a document padded past that size
_ID_PADDING: Final[str] = "<br>" * 256
_ID_LOOKUPS: Final[list[pytest.ParameterSet]] = [
    pytest.param("{}", id="walk"),
    pytest.param("id('-') | {}", id="index"),
]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("id('a')", ["p"], id="single"),
        pytest.param("id('a c')", ["p", "div"], id="multiple-tokens"),
        pytest.param("id('  a   c  ')", ["p", "div"], id="surrounding-and-inner-space"),
        pytest.param("id('xx a')", ["p"], id="token-length-mismatch-then-hit"),
        pytest.param("id('missing')", [], id="no-such-id"),
        pytest.param("id('')", [], id="empty-string"),
        pytest.param("id('   ')", [], id="whitespace-only"),
        pytest.param("id(//div/@id)", ["div"], id="nodeset-argument"),
        pytest.param("id(//div/@class)", [], id="nodeset-argument-no-match"),
        pytest.param("id(//p)", [], id="nodeset-string-values-are-text"),
        pytest.param("id(//zzz)", [], id="empty-nodeset-argument"),
    ],
)
@pytest.mark.parametrize("lookup", _ID_LOOKUPS)
def test_id(ids: turbohtml.Node, lookup: str, expr: str, expected: list[str]) -> None:
    assert tags(ids.xpath(lookup.format(expr))) == expected


def test_id_string_value(ids: turbohtml.Node) -> None:
    assert ids.xpath("string(id('b'))") == "two"


@pytest.mark.parametrize("lookup", _ID_LOOKUPS)
def test_id_hash_collisions_keep_document_order(lookup: str) -> None:
    document = turbohtml.parse(f"<p id=a></p><div id=ba></div><span id=q></span><i id=x></i>{_ID_PADDING}")
    assert tags(document.xpath(lookup.format("id('a ba q a missing')"))) == ["p", "div", "span"]


@pytest.mark.parametrize("lookup", _ID_LOOKUPS)
@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("id('a')", ["p"], id="one-id"),
        pytest.param("id('c a')", ["p", "i"], id="interleaved-ids"),
        pytest.param("id('a c a b c')", ["p", "div", "i"], id="repeated-tokens"),
        pytest.param("id(//@id)", ["p", "div", "i"], id="every-id"),
    ],
)
def test_id_selects_the_first_element_sharing_an_id(lookup: str, expr: str, expected: list[str]) -> None:
    # XPath 1.0 section 5.2.1: a second element with the same ID is treated as having none
    document = turbohtml.parse(f"<p id=a></p><div id=b></div><span id=a></span><i id=c></i><b id=a></b>{_ID_PADDING}")
    assert tags(document.xpath(lookup.format(expr))) == expected


def test_id_rekeyed_walk_keeps_the_first_element_sharing_an_id() -> None:
    by_home: Final[dict[int, str]] = {}
    for index in range(20_000):
        by_home.setdefault(_fnv_home(f"k{index}"), f"k{index}")
    # core/hash.h: the longest run of slots an FNV-1a table walks before it rehashes under the keyed hash
    th_fast_probe_limit: int = 64
    run: Final[list[str]] = [by_home[home] for home in range(th_fast_probe_limit + 1)]
    early: Final[list[str]] = [by_home[150], by_home[160]]
    stray: Final[str] = next(f"m{index}" for index in range(20_000) if _fnv_home(f"m{index}") == 0)
    # the run tokens fill homes 0 to 64, as long a run as the fill allows, and the stray id walks all of it, so the walk
    # starts over keyed after it has claimed the two early tokens; their second elements must stay unclaimed
    elements: Final[str] = "".join(f"<i id={name}></i>" for name in run)
    document: Final[Document] = parse(
        f"<p id={early[0]}></p><b id={early[1]}></b><q id={stray}></q><s id={early[0]}></s><u id={early[1]}></u>"
        f"{elements}"
    )
    assert tags(document.xpath(f"id('{' '.join(early + run)}')")) == ["p", "b", *["i"] * len(run)]


def _fnv_home(name: str) -> int:
    # the slot FNV-1a gives name in the 256-slot token set that 67 tokens take
    value = 14695981039346656037
    for char in name:
        value = ((value ^ ord(char)) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return value & 0xFF


@pytest.mark.parametrize("lookup", _ID_LOOKUPS)
def test_id_in_a_document_without_ids(lookup: str) -> None:
    assert turbohtml.parse(f"<p id>x</p>{_ID_PADDING}").xpath(lookup.format("id('a')")) == []


def test_id_resolves_a_reference_per_node() -> None:
    names: Final[list[str]] = [f"n{index}" * (index % 3 + 1) for index in range(200)]
    rows: Final[str] = "".join(f'<sec id="{name}"/><xref to="none {name}"/><xref to="{name}none"/>' for name in names)
    document: Final[Document] = parse_xml(f"<book>{rows}</book>")
    assert len(node_list(document.xpath("//xref[id(@to)]"))) == len(names)
    assert document.xpath("(id('') | id(//xref/@to))/@id") == names


def test_id_follows_the_root_of_each_context_node() -> None:
    document: Final[Document] = parse(f"<p id=x></p><i id=y></i><div><b id=x></b></div>{_ID_PADDING}")
    detached: Final[turbohtml.Node | str | float | None] = document.xpath_one("//div")
    assert isinstance(detached, Element)
    detached.extract()
    # each root is walked by its first lookup and indexed by its second, the detached one in place of the document's
    expression: Final[str] = (
        "count(id('x')) + count(id('y')) + count($other[id('x')]/b) + count($other[id('y')]) + count($other[id('x')])"
        " + count(id('y'))"
    )
    assert document.xpath(expression, other=[detached]) == pytest.approx(5.0)


def _xrefs(size: int) -> str:
    rows = "".join(f'<sec id="s{index}" k="v"/><p><xref to="s{index}"/></p>' for index in range(size))
    return f"<book>{rows}</book>"


def _shared_xrefs(size: int) -> str:
    rows = '<p><xref id="s" to="s t"/></p>' * size
    return f"<book>{rows}</book>"


@pytest.mark.parametrize(
    ("markup", "expression"),
    [
        pytest.param(_xrefs, "count(//xref[id(@to)])", id="per-node"),
        pytest.param(_xrefs, "count(//xref[id(@to) and seen()])", id="beside-an-extension"),
        pytest.param(_xrefs, "count(//xref[id(@to)/@k = 'v'])", id="compared"),
        pytest.param(_xrefs, "count(//xref[upper-case(id(@to)/@k) = 'V'])", id="case-function"),
        pytest.param(_xrefs, "count(//xref[count(set:distinct(id(@to))) = 1])", id="set-distinct"),
        pytest.param(_shared_xrefs, "count(//xref[id(@to)])", id="one-id"),
    ],
)
def test_id_per_node_grows_linearly(markup: Callable[[int], str], expression: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda size: _query(parse_xml(markup(size)), expression), (500, 2000))


def _query(document: Document, expression: str) -> Callable[[], object]:
    return lambda: document.xpath(expression, extensions={(None, "seen"): lambda _context: True})


def _node(document: turbohtml.Node, path: str) -> Element:
    found = document.xpath_one(path)
    assert isinstance(found, Element)
    return found


def _first(document: turbohtml.Node) -> Element:
    return _node(document, "//p[@n='1']")


def _box(document: turbohtml.Node) -> Element:
    return _node(document, "//div")


def _section(document: turbohtml.Node) -> Element:
    return _node(document, "//section")


def _fresh(number: str) -> Element:
    return Element("i", {"id": "t", "n": number})


def _delete_first(document: turbohtml.Node) -> None:
    span = Range(_box(document), 0)
    span.set_end(_box(document), 1)
    span.delete_contents()


def _link_with_id(candidate: LinkCandidate) -> LinkCandidate:
    candidate.attrs = {"id": "t", "n": "6" if candidate.existing else "8"}
    return candidate


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        pytest.param(lambda document: _first(document).attrs.__setitem__("id", "u"), "2", id="attr-set"),
        pytest.param(lambda document: _first(document).attrs.__delitem__("id"), "2", id="attr-delete"),
        pytest.param(lambda document: _first(document).attrs.clear(), "2", id="attrs-clear"),
        pytest.param(lambda document: _first(document).attrs.__ior__({"id": "u"}), "2", id="attrs-merge"),
        pytest.param(lambda document: _first(document).decompose(), "2", id="decompose"),
        pytest.param(lambda document: _first(document).extract(), "2", id="extract"),
        pytest.param(lambda document: _first(document).unwrap(), "2", id="unwrap"),
        pytest.param(lambda document: _first(document).replace_with(_fresh("9")), "9", id="replace-with"),
        pytest.param(lambda document: _first(document).insert_before(_fresh("0")), "0", id="insert-before"),
        pytest.param(lambda document: _first(document).wrap(_fresh("7")), "7", id="wrap"),
        pytest.param(lambda document: _box(document).wrap_children(_fresh("7")), "7", id="wrap-children"),
        pytest.param(lambda document: _box(document).insert(0, _fresh("0")), "0", id="insert"),
        pytest.param(lambda document: _box(document).clear(), "", id="clear"),
        pytest.param(lambda document: _box(document).set_text("x"), "", id="set-text"),
        pytest.param(lambda document: setattr(_box(document), "text", "x"), "", id="text"),
        pytest.param(lambda document: _box(document).set_inner_html("<b id=t n=5></b>"), "5", id="set-inner-html"),
        pytest.param(
            lambda document: _box(document).insert_adjacent_html("afterbegin", "<b id=t n=5></b>"),
            "5",
            id="insert-adjacent-html",
        ),
        pytest.param(
            lambda document: _section(document).append(_node(document, "//p[@n='2']")), "2", id="move-earlier"
        ),
        pytest.param(lambda document: _section(document).append(_fresh("3")), "3", id="append"),
        pytest.param(lambda document: _section(document).extend([_fresh("3")]), "3", id="extend"),
        pytest.param(lambda document: document.remove("p[n='1']"), "2", id="remove"),
        pytest.param(lambda document: document.strip_tags("p"), "", id="strip-tags"),
        pytest.param(lambda document: Range(_box(document), 0).insert_node(_fresh("4")), "4", id="range-insert"),
        pytest.param(_delete_first, "2", id="range-delete"),
        pytest.param(
            lambda document: linkify_node(_section(document), Linkify(callbacks=[_link_with_id])), "8", id="linkify"
        ),
        pytest.param(
            lambda document: linkify_node(
                _section(document), Linkify(callbacks=[_link_with_id], process_existing=True)
            ),
            "6",
            id="linkify-existing",
        ),
    ],
)
def test_id_sees_an_edit_an_extension_makes(edit: Callable[[turbohtml.Node], object], expected: str) -> None:
    document: Final[Document] = parse(
        '<section><a href="http://example.com/">x</a> see http://example.org</section>'
        f"<div><p id=t n=1>one</p><p id=t n=2>two</p></div>{_ID_PADDING}"
    )

    def change(_context: SimpleNamespace) -> str:
        edit(document)
        return ""

    # the first lookup walks the tree and the second indexes it, which the edit between the second and third outdates
    lookup: Final[str] = "string(id('t')/@n)"
    found: Final[list[turbohtml.Node | str] | float | str] = document.xpath(
        f"concat({lookup}, {lookup}, change(), '>', {lookup})", extensions={(None, "change"): change}
    )
    assert (found, document.xpath(lookup)) == (f"11>{expected}", expected)


def test_id_sees_an_anchor_linkify_unwraps() -> None:
    document: Final[Document] = parse(f'<a id=t n=1 href="http://example.com/">x</a><p id=t n=2></p>{_ID_PADDING}')
    veto: Final[Linkify] = Linkify(callbacks=[lambda _candidate: None], process_existing=True)

    def change(_context: SimpleNamespace) -> str:
        linkify_node(document, veto)
        return ""

    lookup: Final[str] = "string(id('t')/@n)"
    found: Final[list[turbohtml.Node | str] | float | str] = document.xpath(
        f"concat({lookup}, {lookup}, change(), '>', {lookup})", extensions={(None, "change"): change}
    )
    assert found == "11>2"


@pytest.fixture
def foreign() -> turbohtml.Node:
    return turbohtml.parse(
        "<html><body><p id='p'>html</p><svg id='s'><circle/></svg><math><mi>x</mi></math></body></html>"
    )


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("namespace-uri(//p)", "", id="html-element"),
        pytest.param("namespace-uri(//*[local-name()='svg'])", "http://www.w3.org/2000/svg", id="svg-element"),
        pytest.param("namespace-uri(//*[local-name()='math'])", "http://www.w3.org/1998/Math/MathML", id="mathml"),
        pytest.param("namespace-uri(//p/@id)", "", id="attribute"),
        pytest.param("namespace-uri(//p/text())", "", id="text-node"),
        pytest.param("namespace-uri('literal')", "", id="non-nodeset-argument"),
        pytest.param("namespace-uri(//zzz)", "", id="empty-nodeset-argument"),
    ],
)
def test_namespace_uri(foreign: turbohtml.Node, expr: str, expected: str) -> None:
    assert foreign.xpath(expr) == expected


def test_namespace_uri_context_node_counts_svg_subtree(foreign: turbohtml.Node) -> None:
    # The <svg> and its <circle> child are both in the SVG namespace.
    assert foreign.xpath("count(//*[namespace-uri()='http://www.w3.org/2000/svg'])") == pytest.approx(2.0)


@pytest.fixture
def langs() -> turbohtml.Node:
    return turbohtml.parse(
        "<html><body><p id='nolang'>x</p>"
        "<div lang='en-US'><p id='inherit'>a</p></div>"
        "<div lang='en'><p id='outer'>o</p>"
        "<div lang='FR'><span><p id='inner'>i</p></span></div></div>"
        "</body></html>"
    )


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//p[@id='inherit'][lang('en')]", ["p"], id="subtag-prefix"),
        pytest.param("//p[@id='inherit'][lang('en-US')]", ["p"], id="exact"),
        pytest.param("//p[@id='inherit'][lang('EN')]", ["p"], id="case-insensitive-want"),
        pytest.param("//p[@id='outer'][lang('en')]", ["p"], id="direct-attribute"),
        pytest.param("//p[@id='inner'][lang('fr')]", ["p"], id="case-insensitive-tag-via-ancestor"),
        pytest.param("//p[@id='inner'][lang('en')]", [], id="nearest-wins-no-fallthrough"),
        pytest.param("//p[@id='nolang'][lang('en')]", [], id="no-lang-in-any-ancestor"),
        pytest.param("//p[@id='inherit'][lang('e')]", [], id="not-a-subtag-boundary"),
        pytest.param("//p[@id='inherit'][lang('en-US-extra')]", [], id="want-longer-than-tag"),
    ],
)
def test_lang(langs: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert tags(langs.xpath(expr)) == expected


def test_lang_reads_html_lang_attribute_where_lxml_reads_xml_lang(langs: turbohtml.Node) -> None:
    # lxml's lang() returns nothing here (no xml:lang); turbohtml matches the
    # 'inherit' (lang='en-US') and 'outer' (lang='en') paragraphs.
    assert len(node_list(langs.xpath("//p[lang('en')]"))) == 2


# XPath 1.0 §4.3 reads xml:lang; an HTML tree follows HTML "the lang and xml:lang
# attributes", where xml:lang is in the XML namespace only on a foreign element
@pytest.mark.parametrize(
    ("markup", "expr", "expected"),
    [
        pytest.param('<svg xml:lang="it"><g/></svg>', "//g[lang('it')]", ["g"], id="foreign-xml-lang"),
        pytest.param('<svg xml:lang="it" lang="fr"><g/></svg>', "//g[lang('fr')]", [], id="xml-lang-wins-over-lang"),
        pytest.param('<p xml:lang="de"><b>x</b></p>', "//b[lang('de')]", [], id="html-xml-lang-in-no-namespace"),
        pytest.param('<svg lang="es"><g/></svg>', "//g[lang('es')]", ["g"], id="svg-lang"),
        pytest.param('<math lang="fr"><mi>x</mi></math>', "//mi[lang('fr')]", [], id="mathml-lang-ignored"),
    ],
)
def test_lang_html_tree_reads_xml_lang_on_foreign_elements(markup: str, expr: str, expected: list[str]) -> None:
    assert tags(turbohtml.parse(markup).xpath(expr)) == expected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//e[lang('en')]", ["e"], id="xml-lang-inherited"),
        pytest.param("//f[lang('fr')]", [], id="lang-without-namespace-ignored"),
    ],
)
def test_lang_xml_tree_reads_xml_lang(expr: str, expected: list[str]) -> None:
    document = turbohtml.parse_xml('<r><d xml:lang="en-US"><e/></d><f lang="fr"/></r>')
    assert tags(document.xpath(expr)) == expected


def test_lang_on_a_text_node_context_reads_ancestor_lang() -> None:
    # lang() walks self-or-ancestor elements from the context node; on a text-node
    # context it used to loop over a text-node span's attr_count with attrs == NULL and
    # dereference a null pointer (issue #422)
    doc = turbohtml.parse("<p lang=en>hi<b>x</b></p>")
    assert doc.xpath('//text()[lang("en")]') == ["hi", "x"]


def test_lang_is_false_with_no_lang_bearing_ancestor() -> None:
    doc = turbohtml.parse("<div><p>hi</p></div>")
    assert doc.xpath("//text()[lang('en')]") == []


@pytest.mark.parametrize(
    ("declared", "expr", "expected"),
    [
        pytest.param({}, "//*[lang('en')]", [], id="undeclared"),
        pytest.param({0: " xml:lang='en'"}, "//*[lang('en')]", list(range(150)), id="declared-at-the-top"),
        pytest.param({0: " xml:lang='en'", 100: " xml:lang='fr'"}, "//*[lang('en')]", list(range(100)), id="outer"),
        pytest.param(
            {0: " xml:lang='en'", 100: " xml:lang='fr'"}, "//*[lang('fr')]", list(range(100, 150)), id="inner"
        ),
        pytest.param({20: " xml:lang='en'"}, "//*[lang('en')]", list(range(20, 150)), id="declared-below-the-top"),
        pytest.param({20: " xml:lang='en'"}, "//*[not(lang('en'))]", list(range(20)), id="above-the-declaration"),
        pytest.param(
            {0: " xml:lang='en'", 110: " xml:lang='fr'"},
            "//*[lang('fr') or lang('en')][lang('en')]",
            list(range(110)),
            id="asked-twice",
        ),
        pytest.param(
            {0: " xml:lang='en'", 110: " xml:lang='fr'"},
            "//d149/ancestor-or-self::*[lang('fr')]",
            list(range(110, 150)),
            id="deepest-first",
        ),
        pytest.param({90: " xml:lang='en-GB' id='x'"}, "//@id[lang('en')]/..", [90], id="attribute-context"),
        pytest.param(
            {0: " xml:lang='en'", 50: " xml:lang='fr'"},
            "//d149[lang('fr')] | //d130[lang('fr')] | //d100[lang('en')] | //d20[lang('en')]",
            [20, 130, 149],
            id="a-few-long-walks",
        ),
    ],
)
def test_lang_on_a_deeply_nested_tree(declared: Mapping[int, str], expr: str, expected: list[int]) -> None:
    assert _levels(_lang_chain(declared).xpath(expr)) == expected


def _levels(result: XPathResult) -> list[int]:
    return [int(tag[1:]) for tag in tags(result)]


def _lang_chain(declared: Mapping[int, str], depth: int = 150) -> turbohtml.Node:
    # deeper than the nodes and attributes lang() reads before it climbs through the evaluation's memo
    opened: Final[str] = "".join(f"<d{level}{declared.get(level, '')}>" for level in range(depth))
    return parse_xml(opened + "".join(f"</d{level}>" for level in reversed(range(depth))))


@pytest.mark.parametrize(
    ("lang", "expr", "expected"),
    [
        pytest.param(" lang=de", "//li[lang('de')]", ["li", "li", "li"], id="inherited"),
        pytest.param(" lang=nl", "//li[lang('de')]", [], id="inherited-other"),
        pytest.param("", "//li[lang('de')]", [], id="undeclared"),
        pytest.param(" lang=de", "//ul[lang('de') and lang('DE')]", ["ul"], id="own"),
        pytest.param(" lang=nl", "//em[lang('de')]", ["em"], id="nearer-declaration"),
        pytest.param(" lang=nl", "//*[lang('nl')]", ["ul", "li", "li", "li"], id="declaring-element"),
    ],
)
def test_lang_below_an_element_with_many_attributes(lang: str, expr: str, expected: list[str]) -> None:
    names: Final[str] = " ".join(f"data-n{index}" for index in range(90))
    document: Final[Document] = parse(f"<ul {names}{lang}><li>a<li>b<li><em lang=de>c</em></ul>")
    assert tags(document.xpath(expr)) == expected


class _Predicate(Protocol):
    def __call__(self, context: SimpleNamespace, /) -> str | float | Element | Iterable[Element]: ...


def _noop(_context: SimpleNamespace) -> bool:
    return True


# a registered extension runs the evaluation as a snapshot, which hands control to Python before every comparison and
# extension call, as it does for every comparison on Python 3.11
_SNAPSHOT: Final[dict[tuple[str | None, str], _Predicate]] = {(None, "noop"): _noop}


def _deep_lang(size: int) -> turbohtml.Node:
    return parse_xml(f"<doc xml:lang='de'>{'<s>' * size}{'</s>' * size}</doc>")


def _wide_lang(size: int) -> turbohtml.Node:
    names: Final[str] = " ".join(f"data-w{index}" for index in range(size))
    return parse(f"<div lang=de {names}>{'<i></i>' * size}</div>")


@pytest.mark.parametrize(
    ("build", "expression", "extensions"),
    [
        pytest.param(_deep_lang, "count(//*[lang('de')])", None, id="deep"),
        pytest.param(_deep_lang, "count(//*[lang('de') and name() != 'q'])", None, id="deep-comparison"),
        pytest.param(_deep_lang, "count(//*[lang('de') and name() != 'q'])", _SNAPSHOT, id="deep-comparison-snapshot"),
        pytest.param(_deep_lang, "count(//*[noop() and lang('de')])", _SNAPSHOT, id="deep-extension"),
        pytest.param(_wide_lang, "count(//i[lang('de')])", None, id="wide"),
        pytest.param(_wide_lang, "count(//i[noop() and lang('de')])", _SNAPSHOT, id="wide-extension"),
    ],
)
def test_lang_grows_linearly(
    build: Callable[[int], turbohtml.Node],
    expression: str,
    extensions: dict[tuple[str | None, str], _Predicate] | None,
    work_steps: WorkSteps,
) -> None:
    work_steps.assert_linear(lambda size: partial(build(size).xpath, expression, extensions=extensions), (500, 2000))


@pytest.mark.parametrize(
    ("expr", "flips", "expected"),
    [
        pytest.param("//*[flip() and lang('en')]", 150, list(range(1, 150, 2)), id="by-turns"),
        pytest.param("(//*[lang('en')])[position() < 150][flip()][lang('en')]", 149, [], id="asked-before-and-after"),
        pytest.param("(//*)[position() < 150][flip()][lang('fr')]", 149, list(range(149)), id="asked-after"),
    ],
)
def test_lang_reads_a_declaration_an_extension_changed(expr: str, flips: int, expected: list[int]) -> None:
    document: Final[turbohtml.Node] = _lang_chain({0: " xml:lang='en'"})
    top: Final[Element] = cast("Element", document.xpath_one("/*"))
    calls: Final[list[str]] = []

    def flip(_context: SimpleNamespace) -> bool:
        # the declaration every element inherits changes, and its attribute array moves, between two lang() calls
        calls.append(str(top.attrs["xml:lang"]))
        top.attrs["xml:lang"] = "fr" if calls[-1] == "en" else "en"
        top.attrs[f"data-{len(calls)}"] = ""
        return True

    assert _levels(document.xpath(expr, extensions={(None, "flip"): flip})) == expected
    assert calls == (["en", "fr"] * 75)[:flips]


def test_lang_reads_a_subtree_an_extension_moved() -> None:
    document: Final[Document] = parse_xml(f"<r><a xml:lang='en'>{'<x>' * 100}{'</x>' * 100}</a><b xml:lang='fr'/></r>")
    moved: Final[Element] = cast("Element", document.xpath_one("//a/x"))
    target: Final[Element] = cast("Element", document.xpath_one("//b"))

    def move(_context: SimpleNamespace) -> str:
        target.append(moved)
        return "/"

    expr: Final[str] = "concat(count(//x[lang('en')]), move(), count(//x[lang('en')]), '/', count(//x[lang('fr')]))"
    assert document.xpath(expr, extensions={(None, "move"): move}) == "100/0/100"


def test_lang_reads_a_detached_subtree_an_extension_attached() -> None:
    document: Final[Document] = parse_xml(f"<r><a xml:lang='en'/><o>{'<x>' * 100}{'</x>' * 100}</o></r>")
    detached: Final[Element] = cast("Element", document.xpath_one("//o")).extract()
    target: Final[Element] = cast("Element", document.xpath_one("//a"))

    def attach(_context: SimpleNamespace) -> str:
        # the subtree's top had no parent when lang() climbed to it, and gains a declaring one without leaving a parent
        target.append(detached)
        return "/"

    expr: Final[str] = "concat(count($o//x[lang('en')]), attach(), count($o//x[lang('en')]))"
    assert document.xpath(expr, o=detached, extensions={(None, "attach"): attach}) == "0/100"


def test_lang_reads_an_anchor_linkify_cleared() -> None:
    document: Final[Document] = parse(f"<a href='http://example.com' lang=en>{'<span>' * 80}s{'</span>' * 80}</a>")

    def strip(link: LinkCandidate) -> LinkCandidate:
        link.url = ""
        link.attrs = {}
        return link

    def relink(_context: SimpleNamespace) -> str:
        linkify_node(document, Linkify(callbacks=[strip], process_existing=True))
        return "/"

    expr: Final[str] = "concat(count(//span[lang('en')]), relink(), count(//span[lang('en')]))"
    assert document.xpath(expr, extensions={(None, "relink"): relink}) == "80/0"


@pytest.mark.parametrize("count", [pytest.param(1, id="one"), pytest.param(1000, id="many")])
def test_id_node_values_preserve_order(count: int) -> None:
    document: Final[Document] = parse(
        f'<main><b id="first"></b><b id="second"></b>{"<i> second  first second </i>" * count}</main>'
    )
    assert document.xpath("id(//i)/@id") == ["first", "second"]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("xpath-id-nodes", id="many-short-nodes"),
        pytest.param("xpath-id-nodes-long", id="few-long-nodes"),
    ],
)
def test_id_nodes_benchmark_output(name: str) -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    expression, source = cast("tuple[str, str]", load())
    assert parse(source).xpath(expression) == ["first", "second"]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(0, "z" * 32768, id="map-32"),
        pytest.param(1, "z" * 32768, id="map-128"),
        pytest.param(2, "z" * 32768, id="map-512"),
        pytest.param(3, "a short title", id="short"),
        pytest.param(4, "A" * 32768, id="first-entry"),
        pytest.param(5, "b" * 32768, id="duplicates"),
        pytest.param(6, "b" * 64, id="long-map"),
        pytest.param(7, "B" * 32768, id="second-entry"),
        pytest.param(8, string.ascii_uppercase * 1260, id="ascii-cycle"),
        pytest.param(9, "".join(chr(384 + index) for index in range(128)) * 128, id="unicode-cycle"),
    ],
)
def test_translate_benchmark_result(case: int, expected: str) -> None:
    expression, source = cast("tuple[str, ...]", INPUTS["xpath-translate"]()[case][1])[:2]
    document: Final[Document] = parse(source)
    assert document.xpath(expression) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("xpath-translate-repeated", "B" * 32768, id="repeated"),
        pytest.param("xpath-translate-long-map", "b" * 64, id="long-map"),
        pytest.param(
            "xpath-translate-varied",
            "".join(chr(384 + index) for index in range(128)) * 128,
            id="varied",
        ),
    ],
)
def test_codspeed_translate_result(name: str, expected: str) -> None:
    load: Final[Callable[[], Case]] = next(load for identity, _, load in benchmarks() if identity == name)
    expression, source = cast("tuple[str, str]", load())
    assert parse(source).xpath(expression) == expected


@pytest.fixture
def exslt_doc() -> turbohtml.Node:
    return turbohtml.parse(
        "<html><body>"
        "<a href='/path/123'>a</a><a href='HTTP://x'>b</a><a href='/y'>c</a>"
        "<ul><li id='a' class='x'>1</li><li id='b' class='y'>2</li>"
        "<li id='c' class='x'>3</li><li id='d'>1</li></ul>"
        "<div id='nums'><n>3</n><n>1</n><n>5</n><n>5</n></div>"
        "<div id='mixed'><m>3</m><m>nope</m></div>"
        "</body></html>"
    )


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//a[re:test(@href, '[0-9]+')]", ["a"], id="re-test-digits"),
        pytest.param("//a[re:test(@href, '^/')]", ["a", "a"], id="re-test-anchored"),
        pytest.param("//a[re:test(@href, 'NOPE')]", [], id="re-test-no-match"),
        pytest.param("//a[re:test(@href, 'http', 'i')]", ["a"], id="re-test-case-insensitive-flag"),
        pytest.param("//a[re:test(@href, 'http')]", [], id="re-test-case-sensitive-default"),
    ],
)
def test_re_test(exslt_doc: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert exslt_tags(exslt_doc.xpath(expr)) == expected


def exslt_tags(result: XPathResult) -> list[str | turbohtml.Node | float]:
    assert isinstance(result, list)
    return [node.tag if isinstance(node, Element) else node for node in result]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("re:replace('hello world', 'o', 'g', '0')", "hell0 w0rld", id="re-replace-global"),
        pytest.param("re:replace('aaa', 'a', '', 'b')", "baa", id="re-replace-first-only-default"),
        pytest.param("re:replace('Hello', 'hello', 'i', 'hi')", "hi", id="re-replace-case-insensitive"),
        pytest.param("re:replace('abc', 'X', 'imsxz', 'Y')", "abc", id="re-replace-all-flag-letters-no-match"),
        pytest.param("re:replace('a.b', 'a.b', 's', 'Z')", "Z", id="re-replace-dotall-flag"),
    ],
)
def test_re_replace(exslt_doc: turbohtml.Node, expr: str, expected: str) -> None:
    assert exslt_doc.xpath(expr) == expected


def test_re_test_global_flag_is_accepted(exslt_doc: turbohtml.Node) -> None:
    # 'g' is meaningless for a boolean test, but must be tolerated, not rejected.
    assert exslt_doc.xpath("re:test('a', 'a', 'g')") is True


def test_re_test_malformed_pattern_propagates_python_error(exslt_doc: turbohtml.Node) -> None:
    with pytest.raises(re.error):
        exslt_doc.xpath("//a[re:test(@href, '(')]")


def test_re_replace_malformed_pattern_propagates_python_error(exslt_doc: turbohtml.Node) -> None:
    with pytest.raises(re.error):
        exslt_doc.xpath("re:replace('x', '(', '', 'y')")


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("set:difference(//li, //li[@class='x'])", ["b", "d"], id="set-difference"),
        pytest.param("set:intersection(//li, //li[@class='x'])", ["a", "c"], id="set-intersection"),
        pytest.param("set:distinct(//li)", ["a", "b", "c"], id="set-distinct-by-string-value"),
        pytest.param("set:leading(//li, //li[@id='c'])", ["a", "b"], id="set-leading"),
        pytest.param("set:trailing(//li, //li[@id='c'])", ["d"], id="set-trailing"),
        pytest.param("set:leading(//li, //li[@id='gone'])", ["a", "b", "c", "d"], id="set-leading-empty-second"),
        pytest.param("set:trailing(//li, //li[@id='gone'])", ["a", "b", "c", "d"], id="set-trailing-empty-second"),
        pytest.param("set:leading(//li[@id='b'], //li[@id='a'])", [], id="set-leading-pivot-absent"),
        pytest.param("set:trailing(//li[@id='b'], //li[@id='a'])", [], id="set-trailing-pivot-absent"),
    ],
)
def test_set_nodeset_results(exslt_doc: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert exslt_ids(exslt_doc.xpath(expr)) == expected


def exslt_ids(result: XPathResult) -> list[str]:
    assert isinstance(result, list)
    collected: list[str] = []
    for node in result:
        assert isinstance(node, Element)
        value = node.attrs["id"]
        assert isinstance(value, str)
        collected.append(value)
    return collected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("set:has-same-node(//li[@class='x'], //li[@id='a'])", True, id="set-shares-first"),
        pytest.param("set:has-same-node(//li, //li[@id='c'])", True, id="set-shares-later"),
        pytest.param("set:has-same-node(//li[@id='a'], //li[@id='b'])", False, id="set-disjoint"),
    ],
)
def test_set_has_same_node(exslt_doc: turbohtml.Node, expr: str, *, expected: bool) -> None:
    assert exslt_doc.xpath(expr) is expected


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("set:difference('x', //li)", id="set-difference-non-nodeset"),
        pytest.param("set:has-same-node(//li, 'x')", id="set-has-same-node-non-nodeset"),
        pytest.param("set:distinct('x')", id="set-distinct-non-nodeset"),
    ],
)
def test_set_non_nodeset_argument_raises(exslt_doc: turbohtml.Node, expr: str) -> None:
    with pytest.raises(TypeError, match="non-node-set"):
        exslt_doc.xpath(expr)


@pytest.fixture
def attr_doc() -> turbohtml.Node:
    # Several attributes on one element share a node but differ by attribute index, so
    # set operations over an attribute node-set exercise the same-node/different-attr path.
    return turbohtml.parse("<a id='1' class='c' data-x='y'>t</a>")


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("set:difference(//a/@*, //a/@class)", ["1", "y"], id="set-difference-attributes"),
        pytest.param("set:intersection(//a/@*, //a/@class)", ["c"], id="set-intersection-attributes"),
        pytest.param("set:leading(//a/@*, //a/@class)", ["1"], id="set-leading-attributes"),
        pytest.param("set:trailing(//a/@*, //a/@class)", ["y"], id="set-trailing-attributes"),
    ],
)
def test_set_over_attribute_nodesets(attr_doc: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert attr_doc.xpath(expr) == expected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("set:has-same-node(//a/@id, //a/@*)", True, id="set-attr-member-present"),
        pytest.param("set:has-same-node(//a/@id, //a/@class)", False, id="set-attr-same-node-different-index"),
    ],
)
def test_set_has_same_node_attributes(attr_doc: turbohtml.Node, expr: str, *, expected: bool) -> None:
    assert attr_doc.xpath(expr) is expected


def test_set_distinct_mixed_lengths() -> None:
    # Values of differing lengths exercise the length comparison before the byte compare.
    exslt_doc = turbohtml.parse("<ul><li id='a'>1</li><li id='b'>22</li><li id='c'>1</li><li id='d'>22</li></ul>")
    assert exslt_ids(exslt_doc.xpath("set:distinct(//li)")) == ["a", "b"]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("str:replace('abcabc', 'b', 'X')", "aXcaXc", id="str-replace-each"),
        pytest.param("str:replace('abc', 'z', 'y')", "abc", id="str-replace-no-match"),
        pytest.param("str:replace('abx', 'xy', 'Z')", "abx", id="str-replace-multichar-runs-off-tail"),
        pytest.param("str:replace('aaa', 'a', 'bb')", "bbbbbb", id="str-replace-grow"),
        pytest.param("str:replace('abcabc', 'bc', '')", "aa", id="str-replace-shrink"),
        pytest.param("str:replace('abc', '', 'X')", "abc", id="str-replace-empty-search"),
        pytest.param("str:replace('', 'x', 'y')", "", id="str-replace-empty-input"),
        pytest.param("str:concat(//n)", "3155", id="str-concat-nodeset"),
        pytest.param("str:concat(//absent)", "", id="str-concat-empty"),
        pytest.param("str:padding(5)", "     ", id="str-padding-default-space"),
        pytest.param("str:padding(3, 'ab')", "aba", id="str-padding-cycles-pattern"),
        pytest.param("str:padding(3, '')", "   ", id="str-padding-empty-pattern-is-spaces"),
        pytest.param("str:padding(0)", "", id="str-padding-zero"),
        pytest.param("str:padding(-2)", "", id="str-padding-negative"),
        pytest.param("str:padding(number('x'))", "", id="str-padding-nan-is-empty"),
        pytest.param("str:align('ab', 'XXXXX')", "abXXX", id="str-align-left-default"),
        pytest.param("str:align('ab', 'XXXXX', 'right')", "XXXab", id="str-align-right"),
        pytest.param("str:align('ab', 'XXXXX', 'center')", "XabXX", id="str-align-center"),
        pytest.param("str:align('ab', 'XXXXX', 'bogus')", "abXXX", id="str-align-unknown-is-left"),
        pytest.param("str:align('abcdef', 'XXX')", "abc", id="str-align-truncate-left"),
        pytest.param("str:align('abcdef', 'XXX', 'right')", "def", id="str-align-truncate-right"),
    ],
)
def test_str_functions(exslt_doc: turbohtml.Node, expr: str, expected: str) -> None:
    assert exslt_doc.xpath(expr) == expected


def test_str_padding_at_ceiling_is_allowed(exslt_doc: turbohtml.Node) -> None:
    assert exslt_doc.xpath("string-length(str:padding(100000))") == pytest.approx(100000)


@pytest.mark.parametrize(
    ("expr", "shown"),
    [
        pytest.param("str:padding(100001)", "100001", id="just-over-ceiling"),
        pytest.param("str:padding(2000000000)", "2000000000", id="dos-length"),
        pytest.param("str:padding(4611686018427387904)", "4611686018427387904", id="past-ssize-quarter"),
        pytest.param("str:padding(1 div 0)", "inf", id="infinite"),
    ],
)
def test_str_padding_length_past_ceiling_raises(exslt_doc: turbohtml.Node, expr: str, shown: str) -> None:
    message = rf"str:padding length {re.escape(shown)} exceeds the maximum of 100000; pass a length of 100000 or fewer"
    with pytest.raises(ValueError, match=message):
        exslt_doc.xpath(expr)


@pytest.mark.parametrize("width", [pytest.param("100001", id="just-over"), pytest.param("2000000000", id="gigabytes")])
def test_str_padding_length_from_source_data_past_ceiling_raises(width: str) -> None:
    with pytest.raises(ValueError, match=rf"str:padding length {width} exceeds the maximum of 100000"):
        parse_xml(f'<row width="{width}"/>').xpath("str:padding(/row/@width, '-')")


def test_str_concat_non_nodeset_argument_raises(exslt_doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="non-node-set"):
        exslt_doc.xpath("str:concat('x')")


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("math:max(//n)", 5.0, id="math-max"),
        pytest.param("math:min(//n)", 1.0, id="math-min"),
        pytest.param("math:abs(-4.5)", 4.5, id="math-abs-negative"),
        pytest.param("math:abs(4.5)", 4.5, id="math-abs-positive"),
        pytest.param("math:power(2, 10)", 1024.0, id="math-power"),
    ],
)
def test_math_numbers(exslt_doc: turbohtml.Node, expr: str, expected: float) -> None:
    assert exslt_doc.xpath(expr) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("math:max(//m)", id="math-max-non-numeric"),
        pytest.param("math:min(//m)", id="math-min-non-numeric"),
        pytest.param("math:max(//absent)", id="math-max-empty"),
        pytest.param("math:min(//absent)", id="math-min-empty"),
    ],
)
def test_math_extreme_is_nan(exslt_doc: turbohtml.Node, expr: str) -> None:
    result = exslt_doc.xpath(expr)
    assert isinstance(result, float)
    assert math.isnan(result)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("math:highest(//n)", ["5", "5"], id="math-highest-ties"),
        pytest.param("math:lowest(//n)", ["1"], id="math-lowest"),
        pytest.param("math:highest(//m)", [], id="math-highest-non-numeric"),
        pytest.param("math:lowest(//absent)", [], id="math-lowest-empty"),
    ],
)
def test_math_select(exslt_doc: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert exslt_texts(exslt_doc.xpath(expr)) == expected


def exslt_texts(result: XPathResult) -> list[str]:
    assert isinstance(result, list)
    return [node.text for node in result if isinstance(node, Element)]


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("math:max('x')", id="math-max-non-nodeset"),
        pytest.param("math:highest('x')", id="math-highest-non-nodeset"),
    ],
)
def test_math_non_nodeset_argument_raises(exslt_doc: turbohtml.Node, expr: str) -> None:
    with pytest.raises(TypeError, match="non-node-set"):
        exslt_doc.xpath(expr)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("date:year('2024-06-22')", 2024.0, id="date-year"),
        pytest.param("date:month-in-year('2024-06-22')", 6.0, id="date-month"),
        pytest.param("date:day-in-month('2024-06-22')", 22.0, id="date-day"),
        pytest.param("date:day-in-week('2024-06-22')", 7.0, id="date-day-in-week-saturday"),
        pytest.param("date:day-in-week('2024-01-15')", 2.0, id="date-day-in-week-monday-pre-march"),
        pytest.param("date:year('2024-06-22T10:30:00')", 2024.0, id="date-year-with-time-t"),
        pytest.param("date:year('2024-06-22 10:30:00')", 2024.0, id="date-year-with-time-space"),
    ],
)
def test_date_numbers(exslt_doc: turbohtml.Node, expr: str, expected: float) -> None:
    assert exslt_doc.xpath(expr) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("date:year('not-a-date')", id="date-non-numeric"),
        pytest.param("date:year('2024-06')", id="date-too-short"),
        pytest.param("date:year('2024/06/22')", id="date-wrong-first-separator"),
        pytest.param("date:year('2024-06.22')", id="date-wrong-second-separator"),
        pytest.param("date:year('XXXX-06-22')", id="date-bad-year-digits"),
        pytest.param("date:year('2024-XX-22')", id="date-bad-month-digits-above-nine"),
        pytest.param("date:year('202/-06-22')", id="date-bad-year-digit-below-zero"),
        pytest.param("date:year('2024-06-XX')", id="date-bad-day-digits"),
        pytest.param("date:year('2024-13-01')", id="date-month-too-large"),
        pytest.param("date:year('2024-00-01')", id="date-month-too-small"),
        pytest.param("date:year('2024-06-32')", id="date-day-too-large"),
        pytest.param("date:year('2024-06-00')", id="date-day-too-small"),
        pytest.param("date:year('2024-06-22Z')", id="date-trailing-junk"),
    ],
)
def test_date_invalid_is_nan(exslt_doc: turbohtml.Node, expr: str) -> None:
    result = exslt_doc.xpath(expr)
    assert isinstance(result, float)
    assert math.isnan(result)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("date:leap-year('2024-01-01')", True, id="date-leap-divisible-by-four"),
        pytest.param("date:leap-year('2023-01-01')", False, id="date-leap-not-divisible-by-four"),
        pytest.param("date:leap-year('2000-01-01')", True, id="date-leap-divisible-by-four-hundred"),
        pytest.param("date:leap-year('1900-01-01')", False, id="date-leap-century-not-leap"),
        pytest.param("date:leap-year('bad')", False, id="date-leap-invalid-is-false"),
    ],
)
def test_date_leap_year(exslt_doc: turbohtml.Node, expr: str, *, expected: bool) -> None:
    assert exslt_doc.xpath(expr) is expected


@pytest.mark.parametrize("unique", [pytest.param(1, id="identical"), pytest.param(64, id="many-values")])
def test_distinct_retains_first_occurrences(unique: int) -> None:
    document: Final[Document] = parse_xml(
        f"""<root>{"".join(f'<item id="{index}">value-{index % unique}</item>' for index in range(256))}</root>"""
    )
    assert document.xpath("set:distinct(//item)/@id") == [str(index) for index in range(unique)]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param('<item id="a"/><item id="b"/>', ["a"], id="empty"),
        pytest.param('<item id="a">é😀</item><item id="b">é😀</item>', ["a"], id="unicode"),
        pytest.param('<item id="a">a<b>b</b></item><item id="b">ab</item>', ["a"], id="descendants"),
        pytest.param("", [], id="empty-set"),
        pytest.param('<item id="a">one</item>', ["a"], id="singleton"),
    ],
)
def test_distinct_string_values(content: str, expected: list[str]) -> None:
    assert parse_xml(f"<root>{content}</root>").xpath("set:distinct(//item)/@id") == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param('<item a="é" b="😀"/><item a="é" b=""/>', ["é", "😀", ""], id="duplicates"),
        pytest.param('<item a="é"/>', ["é"], id="singleton"),
    ],
)
def test_distinct_attribute_values(content: str, expected: list[str]) -> None:
    assert parse_xml(f"<root>{content}</root>").xpath("set:distinct(//item/@*)") == expected


@pytest.mark.parametrize("count", [pytest.param(3, id="few"), pytest.param(300, id="many")])
def test_distinct_keeps_the_first_node_of_each_value(count: int) -> None:
    pool: Final[tuple[str, str, str, str, str, str, str, str, *tuple[str, ...]]] = (
        "",
        "é",
        "界",
        "😀",
        "😀😀",
        "a",
        "aa",
        "界界",
        *(f"{'x' * (number % 5)}{number}" for number in range(40)),
    )
    rng = random.Random(count)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # a fixed corpus
    values: Final[list[str]] = [rng.choice(pool) for _ in range(count)]
    items: Final[str] = "".join(f'<item id="{index}">{value}</item>' for index, value in enumerate(values))
    expected: Final[list[str]] = [str(index) for index, value in enumerate(values) if values.index(value) == index]
    assert parse_xml(f"<root>{items}</root>").xpath("set:distinct(//item)/@id") == expected


@pytest.mark.parametrize(
    "count", [pytest.param(0, id="empty"), pytest.param(1, id="one"), pytest.param(1000, id="many")]
)
@pytest.mark.parametrize(
    ("markup", "text"),
    [pytest.param("", "", id="empty-values"), pytest.param("é<b>界</b>😀", "é界😀", id="descendant-text")],
)
def test_concat_node_strings(count: int, markup: str, text: str) -> None:
    document: Final[Document] = parse(f"<main>{f'<i>{markup}</i>' * count}</main>")
    assert document.xpath("str:concat(//i)") == text * count


def test_concat_valueless_attribute() -> None:
    assert parse('<i a b="value"></i>').xpath("str:concat(//i/@*)") == "value"


@pytest.mark.parametrize("text", [pytest.param("", id="empty"), pytest.param("é界😀", id="nonempty")])
def test_concat_constructed_text_child(text: str) -> None:
    element: Final[Element] = Element("i", None, [Text(text)])
    assert element.xpath("str:concat(.)") == text


@pytest.mark.parametrize(
    ("markup", "expression", "expected"),
    [
        pytest.param("<root><i>é界😀</i><i>second</i></root>", "str:concat(//i)", "é界😀second", id="single-text"),
        pytest.param('<root><i a="é" b="界"/></root>', "str:concat(//i/@*)", "é界", id="attributes"),
        pytest.param('<root><i a="" b="界"/></root>', "str:concat(//i/@*)", "界", id="empty-attribute"),
        pytest.param("<root><!----><!--界--></root>", "str:concat(//comment())", "界", id="empty-comment"),
        pytest.param("<root>é<i>界</i>😀</root>", "str:concat(//text())", "é界😀", id="text-nodes"),
        pytest.param("<root><!--é--><!--界--></root>", "str:concat(//comment())", "é界", id="comments"),
        pytest.param("<root>é<i>界</i>😀</root>", "str:concat(/)", "é界😀", id="document"),
        pytest.param("<root><i>é<b>界</b>😀</i></root>", "str:concat(//i)", "é界😀", id="descendants"),
        pytest.param("<root><i><b>界</b></i></root>", "str:concat(//i)", "界", id="single-element-child"),
        pytest.param("<root><i><!--ignored-->é</i></root>", "str:concat(//i)", "é", id="comment-before-text"),
        pytest.param("<root><i>é<!--ignored--></i></root>", "str:concat(//i)", "é", id="comment-after-text"),
        pytest.param("<root><i/><i/></root>", "str:concat(//i)", "", id="empty-elements"),
        pytest.param("<root/>", "str:concat(//absent)", "", id="empty-set"),
        pytest.param(
            "<root/>", "str:concat(/*/namespace::xml)", "http://www.w3.org/XML/1998/namespace", id="xml-namespace"
        ),
    ],
)
def test_concat_item_string_values(markup: str, expression: str, expected: str) -> None:
    assert parse_xml(markup).xpath(expression) == expected


@pytest.mark.parametrize("depth", [pytest.param(depth, id=f"depth-{depth}") for depth in range(4)])
def test_concat_follows_single_child_chains_to_their_text(depth: int) -> None:
    chain: Final[str] = "<s>" * depth
    close: Final[str] = "</s>" * depth
    document: Final[Document] = parse_xml(f"<root><i>{chain}é界😀{close}</i><i>{chain}second{close}</i></root>")
    assert document.xpath("str:concat(//i)") == "é界😀second"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<i><s>é</s><b>界</b></i>", "é界", id="chain-ends-in-several-children"),
        pytest.param("<i><s><!--c-->界</s></i>", "界", id="chain-ends-in-comment-and-text"),
        pytest.param("<i><s><!--c--></s></i>", "", id="chain-ends-in-comment"),
        pytest.param("<i><s><t>é</t></s><!--c--></i>", "é", id="descendant-then-comment"),
        pytest.param("<i><s/><b/></i>", "", id="several-empty-children"),
        pytest.param("<i><!--c--><s/></i>", "", id="comment-then-empty-element"),
        pytest.param("<i><s>é</s><b/></i><i>z</i>", "éz", id="built-then-borrowed"),
        pytest.param("<i><s>é</s><b/></i><i><b>y</b><!--c--></i>", "éy", id="built-then-built"),
        pytest.param(
            f"<i><s>{'x' * 300}</s><!--c--></i><i><b>y</b><!--c--></i>", f"{'x' * 300}y", id="built-past-scratch"
        ),
        pytest.param("<i>abc</i><i>é😀</i><i>def</i>", "abcé😀def", id="wide-in-one-member"),
        pytest.param("<i>x</i><i><s>😀</s></i><i>y</i>", "x😀y", id="wide-in-one-built-member"),
        pytest.param("<i/><i></i><i><s/></i>", "", id="empty-members"),
        pytest.param("<i></i><i>é</i><i/>", "é", id="empty-members-around-text"),
    ],
)
def test_concat_member_values(markup: str, expected: str) -> None:
    assert parse_xml(f"<root>{markup}</root>").xpath("str:concat(//i)") == expected


@pytest.mark.parametrize(
    ("markup", "expression", "expected"),
    [
        pytest.param("<root><i>x<b></b></i><!----></root>", "str:concat(//i | //comment())", "x", id="empty-comment"),
        pytest.param('<root><i a b="value">x<b></b></i></root>', "str:concat(//i | //i/@*)", "xvalue", id="valueless"),
    ],
)
def test_concat_builds_beside_empty_and_valueless_members(markup: str, expression: str, expected: str) -> None:
    assert parse(markup).xpath(expression) == expected


def test_concat_of_a_chain_emptied_by_set_text() -> None:
    document = parse("<root><i><s>x</s></i><i><s>é</s></i></root>")
    cast("Element", document.xpath_one("//s")).set_text("")
    assert document.xpath("str:concat(//i)") == "é"


@pytest.mark.parametrize(
    ("markup", "expression", "expected"),
    [
        pytest.param("<i>abc</i><i>def</i>", "str:concat(//i)", "abcdef", id="ascii-input"),
        pytest.param(f"<i>ab</i><p>{'x' * 400}</p>", "str:concat(//i)", "ab", id="short-read-of-ascii-input"),
        pytest.param("<i>é</i><i>a</i>", "str:concat(//i)", "éa", id="latin1-input"),
        pytest.param("<i>é</i><p>界</p>", "str:concat(//i)", "é", id="latin1-text-of-bmp-input"),
        pytest.param("<i>界</i><i>a</i>", "str:concat(//i)", "界a", id="bmp-text"),
        pytest.param("<i>😀</i><i>界</i><i>a</i>", "str:concat(//i)", "😀界a", id="astral-first"),
        pytest.param('<i a="é">b</i>', "str:concat(//i | //i/@a)", "bé", id="latin1-attribute"),
        pytest.param('<i a="界">b</i>', "str:concat(//i | //i/@a)", "b界", id="bmp-attribute"),
        pytest.param("<i>a</i><!--c-->", "str:concat(//i | //comment())", "ac", id="comment"),
        pytest.param("<i>a</i>", "str:concat(/)", "a", id="document-is-built"),
        pytest.param("<i>a<b>c</b></i>", "str:concat(//i)", "ac", id="element-is-built"),
    ],
)
def test_concat_at_the_root_takes_the_narrowest_width(markup: str, expression: str, expected: str) -> None:
    document: Final[Document] = parse(markup)
    result: Final[str] = cast("str", document.xpath(expression))
    assert (result, XPath(expression)(document)) == (expected, expected)
    # a str stored wider than its characters need compares unequal to the same characters
    assert result == "".join(list(result))


@pytest.mark.parametrize(
    ("markup", "text", "expected"),
    [
        pytest.param("<i>a</i><b>x</b>", "界", "a界", id="ascii-span-beside-bmp-text"),
        pytest.param("<i>a</i><b>x</b>", "😀", "a😀", id="ascii-span-beside-astral-text"),
        pytest.param("<i>界</i><b>x</b>", "😀", "界😀", id="bmp-span-beside-astral-text"),
        pytest.param("<i>a</i><b>x</b>", "y", "ay", id="ascii-span-beside-set-text"),
    ],
)
def test_concat_at_the_root_widens_spans_beside_set_text(markup: str, text: str, expected: str) -> None:
    document: Final[Document] = parse(markup)
    cast("Element", document.xpath_one("//b")).set_text(text)
    result: Final[str] = cast("str", document.xpath("str:concat(//i | //b)"))
    assert (result, "".join(list(result))) == (expected, expected)


def _reversed(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return nodes[::-1]


def _rotated(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [*nodes[1:], nodes[0]]


def _repeated(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return nodes[:1] * 4


def _swapped(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [nodes[1], nodes[0], *nodes[2:]]


def _turned(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [*nodes[:2], *nodes[:1:-1]]


def _doubled(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [*nodes[:2], *nodes[1:]]


class _Extension(Protocol):
    def __call__(
        self, context: SimpleNamespace, nodes: list[Element], /
    ) -> str | float | Element | Iterable[Element]: ...


_OUT_OF_ORDER: Final[dict[tuple[str | None, str], _Extension]] = {
    (None, "reversed"): _reversed,
    (None, "rotated"): _rotated,
    (None, "repeated"): _repeated,
    (None, "swapped"): _swapped,
    (None, "turned"): _turned,
    (None, "doubled"): _doubled,
}


@pytest.mark.parametrize(
    ("expression", "numbered"),
    [
        pytest.param("str:concat(//a)", False, id="str-concat"),
        pytest.param("string-join(//a, '')", False, id="string-join"),
        pytest.param("id(//a)", False, id="id"),
        pytest.param("set:distinct(//a)", True, id="set-distinct"),
        pytest.param("str:concat(//a[not(a)]/ancestor-or-self::a)", False, id="reverse-axis"),
        pytest.param("str:concat(/ | (//a)[position() > 1])", False, id="inside-the-root-node"),
        pytest.param("str:concat((//a)[position() < 4] | //text())", False, id="text-node-inside"),
        pytest.param("str:concat(reversed(//a))", False, id="out-of-order"),
        pytest.param("str:concat(rotated(//a))", False, id="out-of-order-later"),
        pytest.param("str:concat(reversed(//a | //b))", False, id="out-of-order-from-the-last-node"),
        pytest.param("string-join(repeated(//a), '')", False, id="same-member-again"),
    ],
)
def test_gathering_nested_members_is_bounded_per_call(expression: str, *, numbered: bool) -> None:
    expected: Final[re.Pattern[str]] = re.compile(
        re.escape(
            "xpath: node-set members inside one another repeat more than 2621440 characters (10 MiB) of text;"
            " select nodes that do not contain one another"
        )
    )
    with pytest.raises(ValueError, match=expected):
        _nested(4, numbered=numbered).xpath(expression, extensions=_OUT_OF_ORDER)


def _nested(depth: int, *, numbered: bool = False) -> Document:
    # every <a> has the million characters as its string-value, so each one below the first repeats them
    opened: Final[str] = "".join(f"<a>{index}" if numbered else "<a>" for index in range(depth))
    return parse_xml(f"<r>{opened}{'x' * 1_000_000}{'</a>' * depth}<b/></r>")


def test_concat_at_the_root_bounds_nested_members() -> None:
    with pytest.raises(ValueError, match="repeat more than 2621440 characters"):
        _nested(4).xpath("str:concat(//a)")


@pytest.mark.parametrize(
    ("expression", "numbered", "expected"),
    [
        pytest.param("string-length(str:concat(//a))", False, 3_000_000.0, id="str-concat"),
        pytest.param("string-length(string-join(//a, ', '))", False, 3_000_004.0, id="string-join"),
        pytest.param("count(id(//a))", False, 0.0, id="id"),
        pytest.param("count(set:distinct(//a))", True, 3.0, id="set-distinct"),
        pytest.param("string-length(str:concat(reversed(//a)))", False, 3_000_000.0, id="out-of-order"),
    ],
)
def test_gathering_nested_members_within_the_allowance(expression: str, *, numbered: bool, expected: float) -> None:
    assert _nested(3, numbered=numbered).xpath(expression, extensions=_OUT_OF_ORDER) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param(
            "string-length(concat(str:concat((//a)[position() > 1]), str:concat((//a)[1]/a//a)))",
            5_000_000.0,
            id="two-calls",
        ),
        pytest.param("count(//a[str:concat(.//a) != ''])", 3.0, id="predicate-contexts"),
    ],
)
def test_gathering_calls_each_have_their_own_allowance(expression: str, expected: float) -> None:
    # no call here repeats more than two million characters, and none of them holds what another gathered
    assert _nested(4).xpath(expression) == expected


@pytest.mark.parametrize(
    ("markup", "expression", "expected"),
    [
        pytest.param("<a>{text}</a>" * 4, "string-length(str:concat(//a))", 4_000_000.0, id="str-concat"),
        pytest.param("<a>{text}</a>" * 4, "string-length(string-join(//a, ','))", 4_000_003.0, id="string-join"),
        pytest.param("<a>{text}</a>" * 4, "count(id(//a))", 0.0, id="id"),
        pytest.param("<a>1{text}</a><a>2{text}</a><a>3{text}</a>", "count(set:distinct(//a))", 3.0, id="set-distinct"),
        pytest.param("<a><a><a><a>{text}</a></a></a></a>", "count(set:distinct(//a))", 1.0, id="set-distinct-same"),
        pytest.param(
            "<a><a>{text}</a></a><c>1{text}</c><c>1{text}</c><c>2{text}</c>",
            "count(set:distinct(//a | //c))",
            3.0,
            id="set-distinct-keeps-one-of-equal-values",
        ),
        pytest.param(
            "<a><a><a><a>{text}</a></a></a></a><c>1{text}</c><c>2{text}</c>",
            "count(set:distinct(//a | //c))",
            3.0,
            id="set-distinct-dropped-values-repeat-nothing",
        ),
        pytest.param(
            "<a><a><a><a><!--{text}--></a></a></a></a>",
            "string-length(str:concat(//a | //comment()))",
            1_000_000.0,
            id="comment-inside",
        ),
        pytest.param(
            "<a k='{text}'><a k='{text}'><a k='{text}'><a k='{text}'/></a></a></a>",
            "string-length(str:concat(//a | //@k))",
            4_000_000.0,
            id="attributes-inside",
        ),
        pytest.param(
            "<a><a>{text}</a><a>{text}</a></a><a>{text}</a><a>{text}</a>",
            "string-length(str:concat(//a))",
            6_000_000.0,
            id="siblings-after-a-nested-run",
        ),
        pytest.param(
            "<b><a>{text}</a></b><a>{text}</a><b><a>{text}</a><a>{text}</a></b><c/><c/><c/><c/><c/><a>{text}</a>",
            "string-length(str:concat(//a))",
            5_000_000.0,
            id="members-apart",
        ),
    ],
)
def test_gathering_members_that_repeat_nothing_is_not_limited(markup: str, expression: str, expected: float) -> None:
    assert parse_xml(f"<r>{markup.format(text='x' * 1_000_000)}</r>").xpath(expression) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("string-length(str:concat(//a))", 6_000_000.0, id="str-concat"),
        pytest.param("string-length(string-join(//a, ''))", 6_000_000.0, id="string-join"),
        pytest.param("count(id(//a))", 0.0, id="id"),
        pytest.param("string-length(str:concat(//a | //@k))", 6_000_006.0, id="attributes-between"),
        pytest.param("string-length(str:concat(doubled(//a)))", 7_000_000.0, id="same-member-again"),
    ],
)
def test_gathering_members_ranked_in_order_is_not_limited(expression: str, expected: float) -> None:
    assert _apart(6).xpath(expression, extensions=_OUT_OF_ORDER) == expected


def _apart(count: int, *, depth: int = 1000, text: str = "x" * 1_000_000) -> Document:
    # every <a> ends its own chain, so placing one after another climbs further than a sort of the set may walk
    chain: Final[str] = f"{'<c>' * depth}<a k='v'>{text}</a>{'</c>' * depth}"
    return parse_xml(f"<r>{chain * count}</r>")


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("str:concat(swapped(//a))", id="out-of-order-first"),
        pytest.param("str:concat(turned(//a))", id="out-of-order-after-ranking"),
    ],
)
def test_gathering_members_ranked_out_of_order_is_bounded(expression: str) -> None:
    with pytest.raises(ValueError, match="repeat more than 2621440 characters"):
        _apart(6).xpath(expression, extensions=_OUT_OF_ORDER)


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(lambda size: f"{'<c>' * 32}<a>x</a>{'</c>' * 32}" * size, id="chains"),
        pytest.param(lambda size: f"{'<c><a>x</a>' * size}{'</c>' * size}", id="one-chain"),
        pytest.param(lambda size: "<a>x</a><b/><b/><b/><b/>" * size, id="siblings"),
        pytest.param(lambda size: "".join(f"<a>{index}</a><b/>" for index in range(size)), id="distinct-values"),
    ],
)
@pytest.mark.parametrize("expression", ["str:concat(//a)", "string-join(//a, '')", "set:distinct(//a)", "id(//a)"])
def test_gathering_members_grows_linearly(markup: Callable[[int], str], expression: str, work_steps: WorkSteps) -> None:
    # the first member holds more text than the allowance, so the call reads where every member after it lies
    work_steps.assert_linear(
        lambda size: partial(parse_xml(f"<r><a>{'x' * 3_000_000}</a>{markup(size)}</r>").xpath, expression),
        (1000, 4000),
    )


@pytest.mark.parametrize(
    ("text", "search", "replacement", "expected"),
    [
        pytest.param("a" * 32768, f"{'a' * 64}b{'a' * 64}", "X", "a" * 32768, id="kmp-miss"),
        pytest.param(
            (f"{'a' * 4096}b{'a' * 64}x") * 8,
            f"{'a' * 64}b{'a' * 64}",
            "X",
            (f"{'a' * 4032}Xx") * 8,
            id="kmp-sparse",
        ),
        pytest.param("a" * 32768, "a", "bb", "bb" * 32768, id="dense-one-character"),
        pytest.param("A short paragraph", "a", "A", "A short pArAgrAph", id="short-ascii"),
        pytest.param("abababa", "aba", "X", "XbX", id="non-overlapping"),
        pytest.param("abcabc", "abc", "", "", id="delete-all"),
        pytest.param("abc", "", "X", "abc", id="empty-search"),
        pytest.param("", "a", "X", "", id="empty-input"),
        pytest.param("", "", "X", "", id="both-empty"),
        pytest.param("abc", "abcd", "X", "abc", id="longer-search"),
        pytest.param("a\x00ba\x00b", "\x00b", "X", "aXaX", id="nul-search"),
        pytest.param("abab", "b", "\x00", "a\x00a\x00", id="nul-replacement"),
        pytest.param("a\x00a", "a", "\x00", "\x00\x00\x00", id="nul-retained-gap"),
        pytest.param("a\x00b", "", "X", "a\x00b", id="nul-empty-search"),
        pytest.param("é界😀é界😀", "界😀", "水", "é水é水", id="unicode-widths"),
        pytest.param("e\u0301", "é", "X", "e\u0301", id="no-normalization"),
        pytest.param("a'b\"a", "'", '"', 'a"b"a', id="quoted-bindings"),
    ],
)
def test_replace_bound_literals(text: str, search: str, replacement: str, expected: str) -> None:
    document: Final[Document] = parse("<p></p>")
    assert (
        document.xpath("str:replace($text, $search, $replacement)", text=text, search=search, replacement=replacement)
        == expected
    )


@pytest.mark.parametrize(
    ("name", "length"),
    [
        pytest.param("xpath-concat", 320_000, id="many-short-nodes"),
        pytest.param("xpath-concat-long", 327_680, id="few-long-nodes"),
    ],
)
def test_concat_benchmark_output(name: str, length: int) -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    expression, source = cast("tuple[str, str]", load())
    assert parse(source).xpath(expression) == "a" * length


@pytest.mark.parametrize("name", ["xpath-replace", "xpath-replace-short"])
def test_replace_benchmark_output(name: str) -> None:
    _, _, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == name)
    text, search, replacement, expected = cast("tuple[str, ...]", load())[:4]
    assert (
        parse("<p></p>").xpath(
            "str:replace($text, $search, $replacement)", text=text, search=search, replacement=replacement
        )
        == expected
    )


@pytest.mark.parametrize("function", [pytest.param("re:test", id="exslt"), pytest.param("matches", id="xpath2")])
def test_regex_dynamic_patterns(function: str) -> None:
    document: Final[Document] = parse_xml(
        "<root>"
        f"""{"".join(f'<n pattern="^{index}$">{index}</n><n pattern="^{index}$">miss</n>' for index in range(160))}"""
        "</root>"
    )
    assert document.xpath(f"count(//n[{function}(., @pattern)])") == 160


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        pytest.param("", [1, 1, 1], id="case-sensitive"),
        pytest.param("i", [2, 1, 2], id="ignore-case"),
    ],
)
def test_regex_evaluations_keep_flags_separate(flags: str, expected: list[int]) -> None:
    expression: Final[XPath] = XPath("count(//n[re:test(., $pattern, $flags)])")
    document: Final[Document] = parse_xml("<root><n>CAFÉ</n><n>café</n><n>tea</n></root>")
    assert [expression(document, pattern=pattern, flags=flags) for pattern in ("café", "tea", "café")] == expected


def test_regex_dynamic_flags_keep_patterns_separate() -> None:
    document: Final[Document] = parse_xml('<root><n flags="i">CAFÉ</n><n flags="">CAFÉ</n><n flags="i">CAFÉ</n></root>')
    assert document.xpath("count(//n[re:test(., 'café', @flags)])") == 2


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("re:test('10', 0)", True, id="numeric-pattern"),
        pytest.param("re:test('x', 'x', 0)", True, id="numeric-flags"),
        pytest.param("re:test('x', 'X', '')", False, id="empty-flags"),
        pytest.param("re:test('x', str:concat(//missing))", True, id="computed-empty-pattern"),
        pytest.param("re:test('x', 'X', str:concat(//missing))", False, id="computed-empty-flags"),
    ],
)
def test_regex_coerces_arguments(expression: str, *, expected: bool) -> None:
    assert parse_xml("<root/>").xpath(expression) is expected


_REGEX_TEST: Final[XPath] = XPath("re:test($text, $pattern, $flags)")


_REGEX_REPLACE: Final[XPath] = XPath("re:replace($text, $pattern, $flags, $replacement)")


_FN_REPLACE: Final[XPath] = XPath("replace($text, $pattern, $replacement)")


_PYTHON_FLAGS: Final[MappingProxyType[str, re.RegexFlag]] = MappingProxyType({
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
})


@pytest.fixture
def native_regex(mocker: MockerFixture) -> None:
    _forbid_python_re(mocker)


def _forbid_python_re(mocker: MockerFixture) -> None:
    # the regex functions must never hand a pattern to Python's backtracking re engine
    mocker.patch.object(re, "compile", autospec=True, side_effect=AssertionError("re.compile called"))
    mocker.patch.object(re, "sub", autospec=True, side_effect=AssertionError("re.sub called"))


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("boolean(//p[matches(., '^(a+)+$')])", "False", id="nested-plus"),
        pytest.param("re:test(//p, '(a|aa)*b')", "False", id="overlapping-alternation"),
        pytest.param("re:test(//p, '(a*)*b', 'i')", "False", id="nested-star"),
        pytest.param("string-length(replace(//p, '(a|a)*$', 'x'))", "5002.0", id="replace"),
        pytest.param("string-length(re:replace(//p, '(\\w+)+x', 'g', '\\1'))", "5001.0", id="re-replace-captures"),
    ],
)
def test_regex_catastrophic_pattern_finishes_quickly(expression: str, expected: str) -> None:
    # run in a subprocess: a backtracking engine would hang on this input
    code = f"import turbohtml\nprint(turbohtml.parse('<p>' + 'a' * 5000 + '!</p>').xpath({expression!r}))"
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert (result.returncode, result.stdout) == (0, f"{expected}\n"), result.stderr


def test_regex_backreference_backtracking_is_bounded() -> None:
    code = (
        "import turbohtml\ntry:\n"
        "    turbohtml.parse('<p>' + 'a' * 60 + '!</p>').xpath(\"re:test(//p, '^(a|a)+\\\\1$')\")\n"
        "except ValueError as error:\n    print(error)\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    expected: Final[str] = (
        "regular expression back-references need more than 10006100 backtracking steps (10000000 plus 100 per"
        " character searched in this XPath evaluation); simplify the pattern or drop the back-reference\n"
    )
    assert (result.returncode, result.stdout) == (0, expected), result.stderr


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        pytest.param("(?:{inner})+|b", "True", id="nested-loops"),
        pytest.param("({inner})", "True", id="nested-captures"),
        pytest.param("(?:{inner})*|b", "ValueError", id="nested-nullable-loops"),
    ],
)
def test_regex_nesting_needs_no_c_stack(shape: str, expected: str) -> None:
    # a thread as small as musl's default: recursion per nesting level would overflow it long before 10000 levels
    code = (
        "import threading, turbohtml\n"
        "pattern = 'a'\n"
        f"for _ in range(10000):\n    pattern = {shape!r}.replace('{{inner}}', pattern)\n"
        "result = []\n"
        "def run():\n"
        "    try:\n"
        "        result.append(turbohtml.parse_xml('<r/>').xpath(\"re:test('ab', $p)\", p=pattern))\n"
        "    except ValueError as error:\n"
        "        result.append(type(error).__name__)\n"
        "threading.stack_size(128 * 1024)\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\n"
        "worker.join()\n"
        "print(result[0])\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert (result.returncode, result.stdout) == (0, f"{expected}\n"), result.stderr


@pytest.mark.parametrize(
    ("body", "expression", "message"),
    [
        pytest.param(
            "<p>aaaaaaaaaaaaaaaaaa!</p>" * 400,
            "count(//p[re:test(., '^(a|a)+\\1$')])",
            r"regular expression back-references need more than \d+ backtracking steps \(10000000 plus 100 per "
            r"character searched in this XPath evaluation\); simplify the pattern or drop the back-reference$",
            id="shared-budget",
        ),
        pytest.param(
            "x" * 24_000,
            "re:test(/r, '^(x+)\\1y')",
            r"regular expression back-references need more than 12400000 backtracking steps \(10000000 plus 100 per "
            r"character searched in this XPath evaluation\); simplify the pattern or drop the back-reference$",
            id="long-group",
        ),
        pytest.param(
            "x" * 24_000,
            "re:test(/r, '^(x+)\\1y', 'i')",
            r"regular expression back-references need more than 12400000 backtracking steps \(10000000 plus 100 per "
            r"character searched in this XPath evaluation\); simplify the pattern or drop the back-reference$",
            id="long-group-ignore-case",
        ),
        pytest.param(
            "a" * 700_000,
            "re:test(/r, '(x)?a*\\1')",
            r"regular expression back-references need more than 655360 saved backtracking positions \(10 MiB\); "
            r"search a shorter string or drop the back-reference$",
            id="trail",
        ),
        pytest.param(
            "x" * 2000,
            # a group under {0} emits no instruction, so only unsetting its slots for each search spends steps
            "replace(/r, concat('(x)\\1', str:padding(40000, '(){0}')), '')",
            r"regular expression back-references need more than 10200000 backtracking steps \(10000000 plus 100 per "
            r"character searched in this XPath evaluation\); simplify the pattern or drop the back-reference$",
            id="group-slots",
        ),
    ],
)
def test_regex_backreference_backtracking_limit_raises(
    mocker: MockerFixture, body: str, expression: str, message: str
) -> None:
    document: Final[Document] = parse_xml(f"<r>{body}</r>")
    expected: Final[re.Pattern[str]] = re.compile(message)
    _forbid_python_re(mocker)
    with pytest.raises(ValueError, match=expected):
        document.xpath(expression)


@pytest.mark.parametrize(
    ("pattern", "flags"),
    [
        pytest.param("[0-9]+", "", id="digit-range"),
        pytest.param("\\d+", "", id="digits"),
        pytest.param("\\d{4}-\\d\\d-\\d\\d", "", id="date"),
        pytest.param("(\\d+)-(\\d+)-(\\d+)", "", id="date-groups"),
        pytest.param("^\\w+", "", id="leading-word"),
        pytest.param("\\w+$", "", id="trailing-word"),
        pytest.param("\\bfoo\\b", "", id="word-boundary"),
        pytest.param("\\Bo", "", id="not-word-boundary"),
        pytest.param("foo|bar|baz", "", id="word-alternation"),
        pytest.param("(foo|bar)(?: \\w+)?", "", id="alternation-group"),
        pytest.param("[A-Z][a-z]+", "", id="capitalized"),
        pytest.param("[^a-z ]+", "", id="negated-class"),
        pytest.param("hello", "i", id="ignore-case-literal"),
        pytest.param("CAFÉ", "i", id="ignore-case-accent"),
        pytest.param("[a-z]+", "i", id="ignore-case-class"),
        pytest.param("^[a-c]$", "m", id="multiline-anchors"),
        pytest.param("^", "m", id="multiline-start"),
        pytest.param("$", "", id="end"),
        pytest.param("$", "m", id="multiline-end"),
        pytest.param("a.c", "", id="dot"),
        pytest.param("b.c", "s", id="dot-all"),
        pytest.param(".+", "", id="dot-plus"),
        pytest.param(".+", "s", id="dot-all-plus"),
        pytest.param("\\s+", "", id="space"),
        pytest.param("\\S+", "", id="not-space"),
        pytest.param("\\W+", "", id="not-word"),
        pytest.param("\\D+", "", id="not-digit"),
        pytest.param("a*", "", id="star-empty-matches"),
        pytest.param("a+?", "", id="lazy-plus"),
        pytest.param("a*?b", "", id="lazy-star"),
        pytest.param("(a|ab)(c|bcd)(d*)", "", id="leftmost-first"),
        pytest.param("(?P<word>\\w+) (?P=word)", "", id="named-backreference"),
        pytest.param("(?P<one>a)(?P<two>b)?(?P=two)", "", id="same-length-names"),
        pytest.param("(\\w)\\1", "", id="backreference"),
        pytest.param("(\\w+)\\s+\\1", "i", id="ignore-case-backreference"),
        pytest.param("^(\\w)\\w*\\1$", "m", id="anchored-backreference"),
        pytest.param("b(\\w)?\\1", "", id="backreference-literal-start"),
        pytest.param("(x?)\\1", "", id="empty-backreference"),
        pytest.param("(a)|b", "", id="unmatched-group"),
        pytest.param("(a)?b", "", id="optional-group"),
        pytest.param("x{2,3}", "", id="bounded"),
        pytest.param("o{,2}", "", id="upper-bound-only"),
        pytest.param("[ab]{2}", "", id="exact-count"),
        pytest.param("(?:ab){1,2}?", "", id="lazy-bounded"),
        pytest.param("a{1,}b{0,}", "", id="unbounded-counts"),
        pytest.param("(?:ab|a){2,}", "", id="min-then-loop"),
        pytest.param("\\Aab", "", id="text-start"),
        pytest.param("c\\Z", "", id="text-end"),
        pytest.param(" a  b # comment\n c", "x", id="verbose"),
        pytest.param("[ ]", "x", id="verbose-class-space"),
        pytest.param("\x07 \x0e", "x", id="verbose-control-characters"),
        pytest.param("a\tb\n c", "x", id="verbose-tab-and-newline"),
        pytest.param("a # trailing comment", "x", id="verbose-unterminated-comment"),
        pytest.param("(?i)hello", "", id="global-flag"),
        pytest.param("(?#note)(?m)^b", "", id="global-flag-after-comment"),
        pytest.param("(?s:.)b", "", id="scoped-flag"),
        pytest.param("(?-i:a)b", "i", id="scoped-flag-off"),
        pytest.param("(?x) a b", "", id="global-verbose"),
        pytest.param("(?u)\\w", "", id="unicode-flag"),
        pytest.param("[\\d.,]+", "", id="class-escape"),
        pytest.param("[\\w-]+@[\\w-]+\\.\\w+", "", id="email"),
        pytest.param("https?://[^/\\s]+", "", id="url"),
        pytest.param("[]a]+", "", id="leading-bracket"),
        pytest.param("[^]]+", "", id="negated-leading-bracket"),
        pytest.param("[a\\-z]+", "", id="escaped-dash"),
        pytest.param("[-a]+[a-]", "", id="edge-dashes"),
        pytest.param("[a-cb-e]+", "", id="overlapping-ranges"),
        pytest.param("[a-ec]+", "", id="contained-range"),
        pytest.param("[\\s\\d]", "", id="class-categories"),
        pytest.param("[^\\W\\d_]+", "", id="letters"),
        pytest.param("\\x41|\\u00e9|\\U0001F600", "", id="hex-escapes"),
        pytest.param("\\101\\0", "", id="octal-escapes"),
        pytest.param("\\0.|\\08", "", id="octal-escape-ends"),
        pytest.param("[\\101-\\103\\0]+", "", id="class-octal"),
        pytest.param("\\t|\\n|\\r|\\f|\\v|\\a", "", id="control-escapes"),
        pytest.param("[\\b\\t]", "", id="class-backspace"),
        pytest.param("\\.\\*\\+\\?\\{\\}\\(\\)\\[\\]\\|\\^\\$\\\\", "", id="escaped-metacharacters"),
        pytest.param("\\-\\/\\ ", "", id="escaped-punctuation"),
        pytest.param("[\\]\\-\\\\]", "", id="class-escaped-punctuation"),
        pytest.param("{", "", id="lone-brace"),
        pytest.param("a{b", "", id="brace-not-quantifier"),
        pytest.param("a{}", "", id="empty-braces"),
        pytest.param("a{", "", id="trailing-brace"),
        pytest.param("x{,}", "", id="open-braces"),
        pytest.param("(a*)*", "", id="nullable-star"),
        pytest.param("(a|)+", "", id="nullable-plus"),
        pytest.param("(?:a??)*b", "", id="lazy-nullable-body"),
        pytest.param("(|a)*", "", id="empty-first-alternative"),
        pytest.param("(|a){0,3}", "", id="nullable-bounded"),
        pytest.param("(|a){0,3}?b", "", id="nullable-lazy-bounded"),
        pytest.param("(a?){2,}", "", id="nullable-min-then-loop"),
        pytest.param("(?:(a*)*)*b", "", id="nested-nullable"),
        pytest.param("(?:)*a", "", id="empty-body-loop"),
        pytest.param("(?:){3}a{0}", "", id="empty-body-counts"),
        pytest.param("(?:){0,5}", "", id="empty-body-bounded"),
        pytest.param("((a)|b)+", "", id="group-in-loop"),
        pytest.param("(?:(\\w)\\1)+", "", id="backreference-in-loop"),
        pytest.param("(a|)(?:\\1)*b", "", id="empty-backreference-loop"),
        pytest.param("(a)(?:\\1|b)+", "", id="backreference-alternative"),
        pytest.param("(?#comment)ab", "", id="comment"),
        pytest.param("é+", "i", id="ignore-case-accent-repeat"),
        pytest.param("(?:)", "", id="empty-group"),
        pytest.param("", "", id="empty-pattern"),
        pytest.param("|a", "", id="empty-alternative"),
    ],
)
def test_regex_matches_like_python_re(mocker: MockerFixture, pattern: str, flags: str) -> None:
    compiled: Final[re.Pattern[str]] = _python_regex(pattern, flags)
    template: Final[str] = "<" + "".join(f"\\g<{index}>|" for index in range(1, compiled.groups + 1)) + "\\g<0>>"
    regex_texts = (
        "",
        "abc",
        "aab",
        "abcd",
        "foo bar foo baz",
        "Hello hello HELLO",
        "2024-05-06 / 1999-12-31",
        "a\nb\nc\n",
        "  spaced\t out \n",
        "Café CAFÉ café ÉÉ",
        "mail me@example.com or see https://x.org/p",
        "x xx xxx xxxx",
        "x² ½",
        "A]b[c-d.e,f",
        "\x07\t\n\x0b\x0c\r\b",
        ".*+?{}()[]|^$\\",
        "ABC\x00",
        "\U0001f600 é A",
        "ooooo",
        "a{b} a{} {",
        "the the quick quick fox",
        "12,345.67 and 8",
    )
    expected: Final[list[tuple[bool, str]]] = [
        (compiled.search(text) is not None, compiled.sub(template, text)) for text in regex_texts
    ]
    _forbid_python_re(mocker)
    document: Final[Document] = parse_xml("<r/>")
    assert [
        (
            _REGEX_TEST(document, text=text, pattern=pattern, flags=flags),
            _REGEX_REPLACE(document, text=text, pattern=pattern, flags=f"{flags}g", replacement=template),
        )
        for text in regex_texts
    ] == expected


def _python_regex(pattern: str, flags: str) -> re.Pattern[str]:
    python_flags = 0
    for letter in flags:
        python_flags |= _PYTHON_FLAGS[letter]
    return re.compile(pattern, python_flags)


_FOLD_TEXTS: Final[tuple[str, ...]] = tuple(
    map(
        chr,
        (
            *b"kKsSiI_1 ",
            0x212A,
            0x017F,
            0x0130,
            0x0131,
            0x00DF,
            0x1E9E,
            0x03C3,
            0x03A3,
            0x03C2,
            0x0432,
            0x0412,
            0x1C80,
            0xFB05,
            0xFB06,
            0x00B5,
            0x039C,
            0x03BC,
            0x00E5,
            0x212B,
            0x01C5,
            0x01C6,
            0x01C4,
            0x1F80,
            0x1F88,
            0x0390,
            0x1FD3,
            0x2126,
            0x03C9,
            0xD7FF,
            0x10400,
            0x10428,
            0x4E00,
        ),
    )
)


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param("k", id="literal-k"),
        pytest.param("[k]", id="class-k"),
        pytest.param("[\\u212a]", id="class-kelvin"),
        pytest.param("[\\u017f]", id="class-long-s"),
        pytest.param("[\\u0130]", id="class-dotted-capital-i"),
        pytest.param("[\\u0131]", id="class-dotless-i"),
        pytest.param("\\u00df", id="literal-sharp-s"),
        pytest.param("[\\u00df]", id="class-sharp-s"),
        pytest.param("[\\u1e9e]", id="class-capital-sharp-s"),
        pytest.param("[\\u03c2]", id="class-final-sigma"),
        pytest.param("\\u03a3", id="literal-sigma"),
        pytest.param("[\\u1c80]", id="class-rounded-ve"),
        pytest.param("\\ufb05", id="literal-ligature"),
        pytest.param("\\ufb06", id="literal-ligature-alias"),
        pytest.param("[\\u00b5]", id="class-micro"),
        pytest.param("[\\u01c5]", id="class-titlecase-digraph"),
        pytest.param("[\\u1f88]", id="class-greek-titlecase"),
        pytest.param("[\\u2126]", id="class-ohm"),
        pytest.param("[\\u2100-\\u2130]", id="class-letterlike-range"),
        pytest.param("[a-z]", id="class-ascii-range"),
        pytest.param("[^k]", id="negated-class"),
        pytest.param("[\\U00010400]", id="class-deseret"),
        pytest.param("\\U00010400", id="literal-deseret"),
        pytest.param("[\\W]", id="class-not-word"),
        pytest.param("[\\d_]", id="class-uncased"),
        pytest.param("1", id="literal-uncased"),
    ],
)
def test_regex_ignore_case_matches_like_python_re(mocker: MockerFixture, pattern: str) -> None:
    compiled: Final[re.Pattern[str]] = re.compile(pattern, re.IGNORECASE)
    expected: Final[list[bool]] = [compiled.search(char) is not None for char in _FOLD_TEXTS]
    _forbid_python_re(mocker)
    document: Final[Document] = parse_xml("<r/>")
    assert [_REGEX_TEST(document, text=char, pattern=pattern, flags="i") for char in _FOLD_TEXTS] == expected


@pytest.mark.parametrize(
    ("text", "pattern", "expected"),
    [
        pytest.param("", "\\B", True, id="not-boundary-in-empty"),
        pytest.param("", "\\b", False, id="boundary-in-empty"),
        pytest.param("a", "\\B", False, id="not-boundary-at-word"),
    ],
)
@pytest.mark.usefixtures("native_regex")
def test_regex_word_boundary_on_empty_text(text: str, pattern: str, *, expected: bool) -> None:
    assert _REGEX_TEST(parse_xml("<r/>"), text=text, pattern=pattern, flags="") is expected


_EXPECTED_PATTERN: Final[re.Pattern[str]] = re.compile(r"unsupported regular expression syntax: ")


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param("a(?=b)", id="lookahead"),
        pytest.param("a(?!b)", id="negative-lookahead"),
        pytest.param("(?<=a)b", id="lookbehind"),
        pytest.param("(?<!a)b", id="negative-lookbehind"),
        pytest.param("(a)?(?(1)b|c)", id="conditional"),
        pytest.param("(?>a+)", id="atomic-group"),
        pytest.param("a*+", id="possessive-star"),
        pytest.param("a{1,2}+", id="possessive-bounded"),
        pytest.param("\\N{DIGIT ONE}", id="named-character"),
        pytest.param("[\\N{DIGIT ONE}]", id="named-character-in-class"),
        pytest.param("(?a)\\w", id="ascii-flag"),
    ],
)
def test_regex_syntax_beyond_the_linear_engine_raises_value_error(mocker: MockerFixture, pattern: str) -> None:
    expected: Final[re.Pattern[str]] = _EXPECTED_PATTERN
    _forbid_python_re(mocker)
    with pytest.raises(ValueError, match=expected):
        _REGEX_TEST(parse_xml("<r/>"), text="ab", pattern=pattern, flags="")


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        pytest.param("(", "missing ), unterminated subpattern", id="unterminated-group"),
        pytest.param("(?i:a", "missing ), unterminated subpattern", id="unterminated-scoped-group"),
        pytest.param("a)", "unbalanced parenthesis", id="unbalanced"),
        pytest.param("[a", "unterminated character set", id="unterminated-class"),
        pytest.param("[a\\", "unterminated character set", id="class-trailing-backslash"),
        pytest.param("\\", "bad escape (end of pattern)", id="trailing-backslash"),
        pytest.param("\\q", "bad escape", id="unknown-escape"),
        pytest.param("[\\q]", "bad escape", id="class-unknown-escape"),
        pytest.param("[\\8]", "bad escape", id="class-decimal-escape"),
        pytest.param("\\x4", "incomplete escape", id="short-hex"),
        pytest.param("\\u12G4", "incomplete escape", id="bad-hex-digit"),
        pytest.param("\\x4g", "incomplete escape", id="bad-lowercase-hex-digit"),
        pytest.param("\\U00110000", "bad escape", id="beyond-unicode"),
        pytest.param("\\400", "octal escape value outside of range", id="octal-range"),
        pytest.param("[\\400]", "octal escape value outside of range", id="class-octal-range"),
        pytest.param("\\2", "invalid group reference 2", id="missing-group"),
        pytest.param("(a)\\11", "invalid group reference 11", id="two-digit-group"),
        pytest.param("(a)\\18", "invalid group reference 18", id="second-digit-not-octal"),
        pytest.param("(a)\\81", "invalid group reference 81", id="first-digit-not-octal"),
        pytest.param("(a\\1)", "cannot refer to an open group", id="open-group"),
        pytest.param("[z-a]", "bad character range", id="reversed-range"),
        pytest.param("[\\d-z]", "bad character range", id="category-range-start"),
        pytest.param("[a-\\d]", "bad character range", id="category-range-end"),
        pytest.param("[a-\\q]", "bad escape", id="range-end-escape"),
        pytest.param("*", "nothing to repeat", id="leading-star"),
        pytest.param("^*", "nothing to repeat", id="repeated-anchor"),
        pytest.param("a|{2}", "nothing to repeat", id="count-after-bar"),
        pytest.param("a**", "multiple repeat", id="multiple-repeat"),
        pytest.param("a{3,2}", "min repeat greater than max repeat", id="reversed-bounds"),
        pytest.param("(?", "unexpected end of pattern", id="bare-extension"),
        pytest.param("(?Px)", "unknown extension ?P", id="unknown-p-extension"),
        pytest.param("(?P", "unknown extension ?P", id="truncated-p-extension"),
        pytest.param("(?P<>a)", "missing group name", id="empty-name"),
        pytest.param("(?P<a", "missing >, unterminated name", id="unterminated-name"),
        pytest.param("(?P<a>a)(?P=a", "missing ), unterminated name", id="unterminated-reference"),
        pytest.param("(?P<1a>x)", "bad character in group name", id="bad-name"),
        pytest.param("(?P<a>x)(?P<a>y)", "redefinition of group name", id="duplicate-name"),
        pytest.param("(?P=b)", "unknown group name", id="unknown-name"),
        pytest.param("(?P<a>(?P=a))", "cannot refer to an open group", id="open-named-group"),
        pytest.param("(?#x", "missing ), unterminated comment", id="unterminated-comment"),
        pytest.param("(?Q)", "unknown extension", id="unknown-extension"),
        pytest.param("(?~)", "unknown extension", id="unknown-extension-symbol"),
        pytest.param("(?z)", "unknown flag", id="unknown-flag"),
        pytest.param("(?i", "missing -, : or )", id="unterminated-flags"),
        pytest.param("(?-u:a)", "cannot turn off flags", id="unicode-off"),
        pytest.param("(?i-i:a)", "flag turned on and off", id="flag-on-and-off"),
        pytest.param("(?-)", "unknown flag", id="empty-removal"),
        pytest.param("(?i-m-s:a)", "unknown flag", id="second-removal"),
        pytest.param("(?i-:a)", "unknown flag", id="removal-without-flag"),
        pytest.param("a(?i)", "global flags not at the start", id="late-global-flag"),
        pytest.param("(?:(?i))", "global flags not at the start", id="nested-global-flag"),
    ],
)
def test_regex_malformed_pattern_raises_re_error(mocker: MockerFixture, pattern: str, message: str) -> None:
    expected: Final[re.Pattern[str]] = re.compile(re.escape(message))
    _forbid_python_re(mocker)
    with pytest.raises(re.error, match=expected):
        _REGEX_TEST(parse_xml("<r/>"), text="ab", pattern=pattern, flags="")


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param("a{2147483647}", id="huge-count"),
        pytest.param("a{99999999999}", id="saturated-count"),
        pytest.param("(?:a?){0,100000}", id="nullable-copies"),
        pytest.param("(?:a|b){200000}", id="alternation-copies"),
    ],
)
def test_regex_oversized_pattern_raises_value_error(mocker: MockerFixture, pattern: str) -> None:
    expected: Final[re.Pattern[str]] = re.compile(
        re.escape(
            "regular expression compiles to more than 655360 instructions (10 MiB); lower its repetition counts or"
            " split it into several patterns"
        )
    )
    _forbid_python_re(mocker)
    with pytest.raises(ValueError, match=expected):
        _REGEX_TEST(parse_xml("<r/>"), text="ab", pattern=pattern, flags="")


@pytest.mark.parametrize(
    ("expression", "text", "pattern", "allowance"),
    [
        pytest.param(_REGEX_TEST, "a" * 4000, "(?:(?:a?){540}){540}c", 10_400_000, id="states"),
        pytest.param(_REGEX_TEST, "a" * 40_000, f"{'a' * 20_000}b", 14_000_000, id="literal-prefix"),
        pytest.param(_FN_REPLACE, "x" * 90_000, f"(?:x|y)*z{'(){0}' * 20_000}", 19_000_000, id="capture-slots"),
        pytest.param(_FN_REPLACE, "x" * 90_000, f"(?:y|z){'(){0}' * 20_000}", 19_000_000, id="idle-capture-slots"),
        pytest.param(_FN_REPLACE, "a" * 7000, "a(?:a|x)*c|a", 10_700_000, id="replace-rescans"),
    ],
)
def test_regex_matching_is_bounded(
    mocker: MockerFixture, expression: XPath, text: str, pattern: str, allowance: int
) -> None:
    expected: Final[re.Pattern[str]] = re.compile(
        re.escape(
            f"regular expression needs more than {allowance} matching steps (10000000 plus 100 per character searched"
            " in this XPath evaluation); simplify the pattern or search a shorter string"
        )
    )
    _forbid_python_re(mocker)
    with pytest.raises(ValueError, match=expected):
        expression(parse_xml("<r/>"), text=text, pattern=pattern, flags="", replacement="")


# 260 words, some starting with each letter: every letter of the text tries them all
_REGEX_WORD_LIST: Final[tuple[str, ...]] = tuple(
    f"{letter}{digit}" for digit in range(10) for letter in ascii_lowercase
)


@pytest.mark.parametrize(
    ("text", "pattern", "expected"),
    [
        pytest.param("a" * 90_000, "(?:(?:a?){5}){5}b", False, id="small-program"),
        pytest.param("a" * 18_000, "(?:(?:a?){20}){20}b", False, id="hundreds-of-states"),
        pytest.param(f"{'lorem ipsum ' * 18_000}z9", "|".join(_REGEX_WORD_LIST), True, id="word-list"),
        pytest.param(f"{'ab' * 50_000}{'a' * 40}c", f"{'a' * 40}c", True, id="long-literal"),
        pytest.param(f"{'ab' * 50_000}{'a' * 40}d", f"{'a' * 40}c", False, id="long-literal-miss"),
        pytest.param("a" * 39, "a" * 40, False, id="long-literal-short-text"),
    ],
)
def test_regex_matching_within_the_budget(mocker: MockerFixture, text: str, pattern: str, *, expected: bool) -> None:
    _forbid_python_re(mocker)
    assert _REGEX_TEST(parse_xml("<r/>"), text=text, pattern=pattern, flags="") is expected


def test_regex_matching_budget_is_shared_by_the_evaluation(mocker: MockerFixture) -> None:
    expression: Final[str] = "count(//p[replace(., 'a(?:a|x)*c|a', '') != ''])"
    paragraph: Final[str] = f"<p>{'a' * 4000}</p>"
    expected: Final[re.Pattern[str]] = re.compile(
        re.escape("regular expression needs more than 10800000 matching steps")
    )
    _forbid_python_re(mocker)
    assert parse_xml(f"<r>{paragraph}</r>").xpath(expression) == 0
    with pytest.raises(ValueError, match=expected):
        parse_xml(f"<r>{paragraph * 2}</r>").xpath(expression)


_NEAR_CAP: Final[str] = "(?:(?:a?){500}){640}"  # 640,000 of the 655,360 instructions one program may have
_MANY_GROUPS: Final[str] = "(a?)" * 260  # about 8 MiB of capture slots once a replace runs it
_TAILS: Final[str] = string.ascii_letters  # each ends a pattern in a cache slot of its own


@pytest.mark.parametrize(
    ("expression", "text", "expected", "limit"),
    [
        pytest.param(
            f"""string({" and ".join(f"matches('a', '{_NEAR_CAP}{tail}?')" for tail in _TAILS[:16])})""",
            "",
            "true",
            400,
            id="instructions",
        ),
        pytest.param(
            f"""string({" and ".join(f"matches('a', '{_NEAR_CAP}{tail}?')" for tail in _TAILS[:12] * 2)})""",
            "",
            "true",
            400,
            id="instructions-compiled-again",
        ),
        pytest.param(
            f"""concat({", ".join(f"replace('a', '{_MANY_GROUPS}{tail}?', '$1')" for tail in _TAILS[:48])})""",
            "",
            "a" * 48,
            320,
            id="capture-slots",
        ),
        pytest.param(
            f"""concat({", ".join(f"matches('a', '{_MANY_GROUPS}{tail}?')" for tail in _TAILS[:48])}, """
            f"""{", ".join(f"replace('a', '{_MANY_GROUPS}{tail}?', '$1')" for tail in _TAILS[:48])})""",
            "",
            f"{'true' * 48}{'a' * 48}",
            320,
            id="capture-slots-on-reuse",
        ),
        pytest.param(
            f"""string({" and ".join(f"matches($text, '(?P<b>b?)a*(?P=b){tail}?')" for tail in _TAILS[:48])})""",
            "a" * 500_000,
            "true",
            320,
            id="saved-positions",
        ),
    ],
)
def test_regex_cache_bounds_what_its_programs_hold_together(
    mocker: MockerFixture, expression: str, text: str, expected: str, limit: int
) -> None:
    # each pattern keeps to the per-program caps and lands in a slot of its own, so without a bound on the cache a
    # case keeps all its programs until the evaluation ends, well past the four programs at every cap the bound allows
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")  # PyPy has no tracemalloc
    _forbid_python_re(mocker)
    tracing.start()
    try:
        assert parse_xml("<r/>").xpath(expression, text=text) == expected
        peak: Final[int] = tracing.get_traced_memory()[1]
    finally:
        tracing.stop()
    assert peak < limit << 20


def test_regex_backreference_search_grows_linearly(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(_padded_backreference, (250, 1000))


def _padded_backreference(size: int) -> Callable[[], object]:
    expression: Final[str] = f"matches(str:padding({size}, 'x'), concat('x(y)\\1', str:padding({size}, '(){{0}}')))"
    return partial(parse_xml("<r/>").xpath, expression)


def test_regex_replace_with_too_many_groups_raises_value_error(mocker: MockerFixture) -> None:
    expected: Final[re.Pattern[str]] = re.compile(
        re.escape(
            "replacing needs more than 10 MiB of capture slots for this regular expression (3303 instructions times"
            " 1100 groups); make groups the replacement does not use non-capturing with (?:...)"
        )
    )
    _forbid_python_re(mocker)
    with pytest.raises(ValueError, match=expected):
        _FN_REPLACE(parse_xml("<r/>"), text="a", pattern="(a)" * 1100, replacement="")


@pytest.mark.parametrize(
    ("pattern", "replacement", "text"),
    [
        pytest.param("(a)(b)?", "[\\1\\2\\g<1>\\g<0>]", "ab a", id="group-references"),
        pytest.param("(?P<first>\\w)(?P<rest>\\w*)", "\\g<rest>\\g<first>", "hello world", id="named-references"),
        pytest.param("(x)(?P<a>(?P<b>y)(z))(?P<c>w)(?P=b)", "\\g<c>\\g<b>\\g<a>", "xyzwy", id="named-among-numbered"),
        pytest.param("a", "\\0\\07\\101\\1011\\08", "a", id="octal-escapes"),
        pytest.param("a", "\\n\\t\\r\\f\\v\\a\\b\\\\", "a", id="control-escapes"),
        pytest.param("a", "\\-\\.\\ \\_\\~\\é", "a", id="kept-escapes"),
        pytest.param("(a)", "\\1!\\1a\\1", "a", id="group-then-text"),
        pytest.param("((((((((((((a))))))))))))", "\\12|\\123|\\128", "a", id="two-digit-group"),
        pytest.param("(a)", "\\g<01>", "a", id="zero-padded-number"),
        pytest.param("(a)|b", "[\\1]", "ab", id="unmatched-group"),
        pytest.param("(a)\\1|b", "[\\1]", "aab", id="unmatched-group-after-backreference-match"),
        pytest.param("(a)\\1c|b", "[\\1]", "aab", id="unmatched-group-after-failed-start"),
        pytest.param("x*", "-", "abc", id="empty-matches"),
        pytest.param("", "$", "ab", id="dollar-literal"),
    ],
)
def test_re_replace_template_matches_python_re(
    mocker: MockerFixture, pattern: str, replacement: str, text: str
) -> None:
    expected: Final[str] = re.sub(pattern, replacement, text)
    _forbid_python_re(mocker)
    assert _REGEX_REPLACE(parse_xml("<r/>"), text=text, pattern=pattern, flags="g", replacement=replacement) == expected


@pytest.mark.usefixtures("native_regex")
def test_regex_resolves_each_of_many_group_names() -> None:
    digits: Final[str] = "".join(str(index % 10) for index in range(150))
    groups: Final[str] = "".join(f"(?P<n{index}>{index % 10})" for index in range(150))
    pattern: Final[str] = f"{groups}(?P=n7)(?P=n148)"
    replaced = _REGEX_REPLACE(
        parse_xml("<r/>"), text=f"{digits}78", pattern=pattern, flags="", replacement="\\g<n149>\\g<n3>\\g<n42>"
    )
    assert replaced == "932"


@pytest.mark.usefixtures("native_regex")
def test_regex_compiles_many_named_groups_in_linear_time(work_steps: WorkSteps) -> None:
    document: Final[Document] = parse_xml("<r/>")
    work_steps.assert_linear(
        lambda count: partial(_REGEX_TEST, document, text="x", pattern=_named_groups(count), flags=""),
        (1000, 4000),
    )


def _named_groups(count: int) -> str:
    return "".join(f"(?P<n{index}>{index % 10})" for index in range(count))


@pytest.mark.parametrize(
    ("replacement", "error", "message"),
    [
        pytest.param("\\", re.error, "bad escape (end of pattern)", id="trailing-backslash"),
        pytest.param("\\q", re.error, "bad escape", id="unknown-escape"),
        pytest.param("\\g", re.error, "missing <", id="bare-g"),
        pytest.param("\\g(1)", re.error, "missing <", id="g-without-angle"),
        pytest.param("\\g<1", re.error, "missing >, unterminated name", id="unterminated-name"),
        pytest.param("\\g<>", re.error, "missing group name", id="empty-name"),
        pytest.param("\\g<9>", re.error, "invalid group reference 9", id="missing-numbered-group"),
        pytest.param("\\g<99999999999>", re.error, "invalid group reference", id="huge-group-number"),
        pytest.param("\\g<a b>", re.error, "bad character in group name", id="bad-name"),
        pytest.param("\\g<-1>", re.error, "bad character in group name", id="negative-number"),
        pytest.param("\\g<nope>", IndexError, "unknown group name 'nope'", id="unknown-name"),
        pytest.param("\\5", re.error, "invalid group reference 5", id="missing-group"),
        pytest.param("\\18", re.error, "invalid group reference 18", id="missing-two-digit-group"),
        pytest.param("\\183", re.error, "invalid group reference 18", id="third-digit-not-octal"),
        pytest.param("\\912", re.error, "invalid group reference 91", id="first-digit-not-octal"),
        pytest.param("\\777", re.error, "octal escape value outside of range", id="octal-range"),
    ],
)
def test_re_replace_malformed_template_raises(
    mocker: MockerFixture, replacement: str, error: type[Exception], message: str
) -> None:
    expected: Final[re.Pattern[str]] = re.compile(re.escape(message))
    _forbid_python_re(mocker)
    with pytest.raises(error, match=expected):
        _REGEX_REPLACE(parse_xml("<r/>"), text="ab", pattern="(?P<name>a)", flags="", replacement=replacement)


@pytest.mark.parametrize(
    ("pattern", "replacement", "expected"),
    [
        pytest.param("(a)", "$15", "a5bc", id="digits-past-the-groups-are-literal"),
        pytest.param("(a)(b)", "[$5]", "[]c", id="single-digit-past-the-groups-is-empty"),
        pytest.param("(a)(b)", "[$52]", "[2]c", id="leading-digit-past-the-groups"),
        pytest.param("(a)", "$01", "abc", id="leading-zero"),
        pytest.param("b", "$0$0", "abbc", id="whole-match"),
        pytest.param("(x)?b", "[$1]", "a[]c", id="unmatched-group"),
        pytest.param("b", "\\$\\\\", "a$\\c", id="escaped-dollar-and-backslash"),
    ],
)
@pytest.mark.usefixtures("native_regex")
def test_fn_replace_dollar_references(pattern: str, replacement: str, expected: str) -> None:
    assert _FN_REPLACE(parse_xml("<r/>"), text="abc", pattern=pattern, replacement=replacement) == expected


@pytest.mark.usefixtures("native_regex")
def test_regex_flags_ignore_unknown_letters() -> None:
    assert _REGEX_REPLACE(parse_xml("<r/>"), text="A.B", pattern="a.b", flags="gimsxzq", replacement="!") == "!"


@pytest.mark.parametrize("repetitions", [pytest.param(1, id="single-search"), pytest.param(2, id="reused-pattern")])
@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        pytest.param("article|post", "grid post-body", True, id="later-start"),
        pytest.param("article|post", "grid content", False, id="miss"),
        pytest.param("article|post", "", False, id="empty-input"),
        pytest.param("é|post", "grid é", True, id="unicode-start"),
        pytest.param("é|post", "grid", False, id="ascii-before-unicode"),
        pytest.param("\\x7f|post", "grid\x7f", True, id="last-ascii-bit"),
        pytest.param("(?i:İ|post)", "i", True, id="unicode-fold-to-ascii"),
        pytest.param("^article|post$", "article body", True, id="start-assertion"),
        pytest.param("^article|post$", "xarticle body", False, id="failed-start-assertion"),
        pytest.param("^article|post$", "grid post", True, id="end-assertion"),
        pytest.param("^article|post$", "grid posts", False, id="failed-end-assertion"),
        pytest.param("(?:a|.)z", "éz", True, id="wildcard-unicode"),
        pytest.param("(?:a|.)z", "\nz", False, id="wildcard-newline"),
        pytest.param("(?:a|(?s:.))z", "\nz", True, id="dotall-newline"),
        pytest.param("(?:a?)*|post", "", True, id="nullable-loop"),
        pytest.param("(?:a|b)*z", "abz", True, id="loop-before-literal"),
        pytest.param("(?:a|b)*z", "abc", False, id="loop-miss"),
        pytest.param("(a|b)\\1", "bb", True, id="backreference"),
        pytest.param("(?:a?)?c", f"{'x' * 64}c", True, id="shared-optional-exit"),
        pytest.param("(?:a?)?c", "x" * 64, False, id="long-miss"),
    ],
)
def test_alternation_start(pattern: str, text: str, repetitions: int, *, expected: bool) -> None:
    assert (
        parse_xml(f"<r>{'<item/>' * repetitions}</r>").xpath(
            "count(//item[re:test($text, $pattern)])", text=text, pattern=pattern
        )
        == repetitions * expected
    )


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        pytest.param("(art|article)", "x <art>icle <art>", id="first-shorter"),
        pytest.param("(article|art)", "x <article> <art>", id="first-longer"),
        pytest.param("(article|art)+", "x <article> <art>", id="repeated-capture"),
    ],
)
def test_alternation_capture_priority(pattern: str, expected: str) -> None:
    assert parse_xml("<r/>").xpath("re:replace('x article art', $pattern, 'g', '<\\1>')", pattern=pattern) == expected


@pytest.mark.parametrize(
    ("patterns", "flags", "expected"),
    [
        pytest.param(("a", "b", "a", "b"), ("i", "i", "i", "i"), ["0", "2"], id="changing-pattern"),
        pytest.param(("a", "a", "a", "a"), ("i", "", "i", ""), ["0", "2"], id="changing-flags"),
        pytest.param(("a", "aa", "aaa", "a"), ("i", "i", "i", "i"), ["0", "3"], id="changing-length"),
    ],
)
def test_regex_patterns_within_query(patterns: tuple[str, ...], flags: tuple[str, ...], expected: list[str]) -> None:
    assert (
        parse_xml(
            "<r>"
            f"""{
                "".join(
                    f'<item id="{index}" pattern="{pattern}" flags="{flag}"/>'
                    for index, (pattern, flag) in enumerate(zip(patterns, flags, strict=True))
                )
            }"""
            "</r>"
        ).xpath("//item[re:test('A', @pattern, @flags)]/@id")
        == expected
    )


_PADDING: Final[str] = "str:padding(99000, 'x')"
_GROWTH_REFUSED: Final[re.Pattern[str]] = re.compile(
    re.escape(
        "replacing, joining and padding add more than 16777216 characters (64 MiB) to their inputs in this XPath"
        " evaluation; shorten the replacement, separator or padding, or replace fewer matches or join fewer items"
    )
)


@pytest.mark.parametrize(
    ("markup", "expression"),
    [
        pytest.param("", "replace(str:padding(1000, 'a'), 'a', str:padding(16800, 'b'))", id="regex-matches"),
        pytest.param("", "re:replace(str:padding(1000, 'a'), 'a', 'g', str:padding(16800, 'b'))", id="exslt-regex"),
        pytest.param("", "str:replace(str:padding(1000, 'a'), 'a', str:padding(16800, 'b'))", id="exslt-literal"),
        pytest.param("", "replace(str:padding(1000, 'a'), '.*', str:padding(99000, '$0'))", id="regex-references"),
        pytest.param("<p/>" * 16800, "string-join(//p, str:padding(1000, 'x'))", id="string-join-members"),
        pytest.param(
            "<p/>" * 300,
            "string-join(//p, string-join(//p, str:padding(1000, 'x')))",
            id="string-join-nested",
        ),
        pytest.param("", f"concat({', '.join([_PADDING] * 170)})", id="concat-of-padding"),
        pytest.param(
            "",
            "concat(replace(str:padding(1000, 'a'), 'a', str:padding(8400, 'b')),"
            " str:replace(str:padding(1000, 'a'), 'a', str:padding(8400, 'b')))",
            id="shared-by-sibling-calls",
        ),
        pytest.param("<p/>" * 200, f"count(//p[string-join(//p, {_PADDING}) != ''])", id="inside-a-node"),
    ],
)
def test_growth_past_the_allowance_raises(markup: str, expression: str) -> None:
    with pytest.raises(ValueError, match=_GROWTH_REFUSED):
        parse_xml(f"<r>{markup}</r>").xpath(expression)


@pytest.mark.parametrize(
    ("markup", "expression", "expected"),
    [
        pytest.param("<p/>" * 169, "string-length(string-join(//p, str:padding(99000, 'x')))", 16_632_000.0, id="join"),
        pytest.param(
            "", "string-length(replace(str:padding(1000, 'a'), 'a', str:padding(16700, 'b')))", 16_700_000.0, id="regex"
        ),
        pytest.param(
            "",
            "string-length(str:replace(str:padding(1000, 'a'), 'a', str:padding(16700, 'b')))",
            16_700_000.0,
            id="literal",
        ),
        pytest.param(
            "",
            "string-length(re:replace(str:padding(1000, 'a'), 'a', 'g', str:padding(16700, 'b')))",
            16_700_000.0,
            id="exslt-regex",
        ),
        pytest.param(
            "",
            f"string-length(concat({', '.join([_PADDING] * 169)}))",
            16_731_000.0,
            id="padding",
        ),
    ],
)
def test_growth_within_the_allowance(markup: str, expression: str, expected: float) -> None:
    assert parse_xml(f"<r>{markup}</r>").xpath(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("string-length(replace($text, 'a', $replacement))", id="regex"),
        pytest.param("string-length(str:replace($text, 'a', $replacement))", id="literal"),
    ],
)
def test_replace_growth_at_the_allowance_is_allowed(expression: str) -> None:
    assert parse_xml("<r/>").xpath(expression, text="a" * 4_096, replacement="b" * 4_097) == 16_781_312


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("replace($text, 'a', $replacement)", id="regex"),
        pytest.param("str:replace($text, 'a', $replacement)", id="literal"),
    ],
)
def test_replace_growth_past_the_allowance_raises(expression: str) -> None:
    with pytest.raises(ValueError, match=_GROWTH_REFUSED):
        parse_xml("<r/>").xpath(expression, text="a" * 4_096, replacement="b" * 4_098)


def test_replace_refuses_the_text_between_matches_once_the_allowance_is_spent() -> None:
    # the first replacement fits in the buffer, and the text after it does not
    with pytest.raises(ValueError, match=_GROWTH_REFUSED):
        parse_xml("<r/>").xpath("replace($text, 'a', $replacement)", text=f"a{'x' * 10}a", replacement="b" * 16_777_219)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("string-length(replace($text, 'a', 'b'))", 16_777_217.0, id="regex-same-length"),
        pytest.param("string-length(str:replace($text, 'a', 'b'))", 16_777_217.0, id="literal-same-length"),
        pytest.param("string-length(re:replace($text, 'a', 'g', 'b'))", 16_777_217.0, id="exslt-regex-same-length"),
        pytest.param("string-length(replace($text, 'aa', 'b'))", 8_388_609.0, id="regex-shorter"),
    ],
)
def test_replace_that_does_not_lengthen_its_input_is_not_limited(expression: str, expected: float) -> None:
    assert parse_xml("<r/>").xpath(expression, text="a" * 16_777_217) == expected


@pytest.mark.parametrize(
    ("markup", "expression", "expected"),
    [
        pytest.param(
            '<a href="/x"/>' * 200,
            f"count(//a[replace(@href, '^', {_PADDING}) != ''])",
            200.0,
            id="regex",
        ),
        pytest.param(
            "<p/>" * 300, "count(//p[string-join(//p, str:padding(1000, 'x')) != ''])", 300.0, id="string-join"
        ),
        pytest.param(
            "<p/>" * 200,
            "count(//p[str:replace(str:padding(1000, 'a'), 'a', str:padding(10000, 'b')) != ''])",
            200.0,
            id="literal",
        ),
    ],
)
def test_predicate_gets_back_the_growth_it_spent_on_each_node(markup: str, expression: str, expected: float) -> None:
    assert parse_xml(f"<r>{markup}</r>").xpath(expression) == expected


def test_each_evaluation_has_its_own_allowance() -> None:
    document: Final[Document] = parse_xml("<r/>")
    expression: Final[str] = "string-length(replace(str:padding(1000, 'a'), 'a', str:padding(16700, 'b')))"
    assert document.xpath(expression) == document.xpath(expression) == 16_700_000
