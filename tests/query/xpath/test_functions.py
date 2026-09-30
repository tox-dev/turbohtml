"""XPath functions, operators, and value coercions evaluated through ``Node.xpath``.

A node-set expression returns a list; the three scalar-typed expressions return the
matching ``float`` / ``str`` / ``bool``, the same as lxml.
"""

from __future__ import annotations

import math
import re
import string
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys
from string import ascii_lowercase, ascii_uppercase
from typing import TYPE_CHECKING, Final, cast
from xml.etree import ElementTree as ET  # ruff:ignore[suspicious-xml-etree-import]

import pytest
from bench.ci import benchmarks
from bench.core import OPERATIONS
from bench.operations import INPUTS

import turbohtml
from turbohtml import Document, Element, Text, XPath, parse, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


def number(node: turbohtml.Node, expr: str) -> float:
    result = node.xpath(expr)
    assert isinstance(result, float)
    return result


HTML = (
    "<html><body><div class='a b'><p>one</p><p class='x'>two</p><p>three</p></div>"
    "<ul><li>1</li><li>2</li><li>3</li></ul>"
    "<a href='/y'>L</a><input disabled><!--note--></body></html>"
)
_LONG_NEEDLE: Final = "a" * 65 + "ba"
_LATE_HAY: Final = "a" * 200 + _LONG_NEEDLE


@pytest.fixture
def doc() -> turbohtml.Node:
    return turbohtml.parse(HTML)


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
def test_scalar_and_boolean(doc: turbohtml.Node, expr: str, expected: object) -> None:
    assert doc.xpath(expr) == expected


@pytest.mark.parametrize(
    ("expression", "hay", "needle", "expected"),
    [
        pytest.param("contains($hay, $needle)", "a" * 400, "a" * 65 + "b", False, id="prefilter-miss"),
        pytest.param(
            "contains($hay, $needle)",
            _LATE_HAY,
            _LONG_NEEDLE,
            True,
            id="late-match",
        ),
        pytest.param("contains($hay, $needle)", "a" * 200 + "c" + "a" * 199, _LONG_NEEDLE, False, id="kmp-miss"),
        pytest.param(
            "substring-before($hay, $needle)",
            _LATE_HAY,
            _LONG_NEEDLE,
            "a" * 200,
            id="substring-before",
        ),
        pytest.param(
            "substring-after($hay, $needle)",
            _LATE_HAY + "z",
            _LONG_NEEDLE,
            "z",
            id="substring-after",
        ),
        pytest.param("contains($hay, $needle)", "a" * 65, _LONG_NEEDLE, False, id="needle-longer"),
        pytest.param("contains($hay, $needle)", _LONG_NEEDLE, _LONG_NEEDLE, True, id="short-hay"),
        pytest.param("contains($hay, $needle)", "x" + "a" * 65 + "ca", _LONG_NEEDLE, False, id="short-hay-miss"),
    ],
)
def test_long_string_search(doc: turbohtml.Node, expression: str, hay: str, needle: str, expected: object) -> None:
    assert doc.xpath(expression, hay=hay, needle=needle) == expected


def test_number_parse_edge_cases(doc: turbohtml.Node) -> None:
    assert math.isnan(number(doc, "number('1.2.3')"))
    assert math.isnan(number(doc, "number('12x')"))
    assert math.isnan(number(doc, "number('.')"))


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
        pytest.param(1 / 3, id="one-third"),
        pytest.param(0.1, id="tenth"),
        pytest.param(2 / 3, id="two-thirds"),
        pytest.param(math.pi, id="pi"),
        pytest.param(math.e, id="e"),
        pytest.param(1e-7, id="small"),
        pytest.param(1e21, id="large"),
        pytest.param(123.456, id="mixed"),
        pytest.param(9.999999999999999e-5, id="seventeen-digits"),
        pytest.param(5e-324, id="smallest-subnormal"),
    ],
)
def test_number_to_string_round_trips(value: float) -> None:
    doc = turbohtml.parse("<r/>")
    rendered = doc.xpath("string($v)", v=value)
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
    elementpath = pytest.importorskip("elementpath")
    markup = "<div><p>One</p><p>Two</p><p>Three</p></div>"
    root = ET.fromstring(markup)  # ruff:ignore[suspicious-xml-element-tree-usage]
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
            "yz" * 64 + "a", "abcdefghijklmnopaa", "ABCDEFGHIJKLMNOPXY", "yz" * 64 + "A", id="duplicate-after-index"
        ),
        pytest.param(
            "yz" * 64 + "界😀", "abcdefghijklmnop界😀", "ABCDEFGHIJKLMNOPé", "yz" * 64 + "é", id="unicode-after-index"
        ),
        pytest.param("a" * 128 + "z", ascii_lowercase, "A", "A" * 128, id="late-removal"),
        pytest.param("yz" * 64, "", "X", "yz" * 64, id="empty-map"),
        pytest.param("", ascii_lowercase, "A", "", id="empty-input"),
    ],
)
def test_translate_deferred_index(text: str, source: str, target: str, expected: str) -> None:
    document: Final = parse("<p></p>")
    assert document.xpath(f"translate('{text}', '{source}', '{target}')") == expected


def test_translate_nul_mapping() -> None:
    document: Final = parse("<p></p>")
    assert (
        document.xpath(
            "translate($text, $source, $target)",
            text="yz" * 64 + "\x00a",
            source="abcdefghijklmnop\x00",
            target="ABCDEFGHIJKLMNOP!",
        )
        == "yz" * 64 + "!A"
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
    document: Final = parse_xml(f"<root>{text}</root>")
    assert document.xpath(f"translate(string(/root), '{source}', '{target}')") == expected


ID_HTML = (
    "<html><body><p id='a'>one</p><p id='b'>two</p><div class='x' id='c'>three</div>"
    "<span>no id</span><i id=''>empty</i><!--note--></body></html>"
)

NS_HTML = "<html><body><p id='p'>html</p><svg id='s'><circle/></svg><math><mi>x</mi></math></body></html>"

LANG_HTML = (
    "<html><body><p id='nolang'>x</p>"
    "<div lang='en-US'><p id='inherit'>a</p></div>"
    "<div lang='en'><p id='outer'>o</p>"
    "<div lang='FR'><span><p id='inner'>i</p></span></div></div>"
    "</body></html>"
)


def node_list(result: object) -> list[turbohtml.Node | str]:
    """The node-set list an expression is expected to return."""
    assert isinstance(result, list)
    return result


def tags(result: object) -> list[str]:
    return [node.tag if isinstance(node, Element) else str(node) for node in node_list(result)]


@pytest.fixture
def ids() -> turbohtml.Node:
    return turbohtml.parse(ID_HTML)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("id('a')", ["p"], id="single"),
        pytest.param("id('a c')", ["p", "div"], id="multiple-tokens"),
        pytest.param("id('  a   c  ')", ["p", "div"], id="surrounding-and-inner-space"),
        pytest.param("id('xx a')", ["p"], id="token-length-mismatch-then-hit"),
        pytest.param("id('missing')", [], id="no-such-id"),
        pytest.param("id('')", [], id="empty-string"),
        pytest.param("id(//div/@id)", ["div"], id="nodeset-argument"),
        pytest.param("id(//div/@class)", [], id="nodeset-argument-no-match"),
        pytest.param("id(//p)", [], id="nodeset-string-values-are-text"),
        pytest.param("id(//zzz)", [], id="empty-nodeset-argument"),
    ],
)
def test_id(ids: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert tags(ids.xpath(expr)) == expected


def test_id_string_value(ids: turbohtml.Node) -> None:
    assert ids.xpath("string(id('b'))") == "two"


def test_id_hash_collisions_keep_document_order() -> None:
    document = turbohtml.parse("<p id=a></p><div id=ba></div><span id=q></span><i id=x></i>")
    assert tags(document.xpath("id('a ba q a missing')")) == ["p", "div", "span"]


@pytest.fixture
def foreign() -> turbohtml.Node:
    return turbohtml.parse(NS_HTML)


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
    return turbohtml.parse(LANG_HTML)


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


@pytest.mark.parametrize("count", [pytest.param(1, id="one"), pytest.param(1000, id="many")])
def test_id_node_values_preserve_order(count: int) -> None:
    document: Final = parse(
        '<main><b id="first"></b><b id="second"></b>' + "<i> second  first second </i>" * count + "</main>"
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
    expression, source = cast("tuple[str, str]", INPUTS["xpath-translate"]()[case][1])
    document: Final = parse(source)
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
    load: Final = next(load for identity, _, load in benchmarks() if identity == name)
    expression, source = cast("tuple[str, str]", load())
    assert parse(source).xpath(expression) == expected


EXSLT_HTML = (
    "<html><body>"
    "<a href='/path/123'>a</a><a href='HTTP://x'>b</a><a href='/y'>c</a>"
    "<ul><li id='a' class='x'>1</li><li id='b' class='y'>2</li>"
    "<li id='c' class='x'>3</li><li id='d'>1</li></ul>"
    "<div id='nums'><n>3</n><n>1</n><n>5</n><n>5</n></div>"
    "<div id='mixed'><m>3</m><m>nope</m></div>"
    "</body></html>"
)


@pytest.fixture
def exslt_doc() -> turbohtml.Node:
    return turbohtml.parse(EXSLT_HTML)


def exslt_tags(result: object) -> list[str]:
    assert isinstance(result, list)
    return [node.tag if isinstance(node, Element) else node for node in result]


def exslt_ids(result: object) -> list[str]:
    assert isinstance(result, list)
    collected: list[str] = []
    for node in result:
        assert isinstance(node, Element)
        value = node.attrs["id"]
        assert isinstance(value, str)
        collected.append(value)
    return collected


def exslt_texts(result: object) -> list[str]:
    assert isinstance(result, list)
    return [node.text for node in result if isinstance(node, Element)]


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
        "<root>" + "".join(f'<item id="{index}">value-{index % unique}</item>' for index in range(256)) + "</root>"
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


@pytest.mark.parametrize(
    "count", [pytest.param(0, id="empty"), pytest.param(1, id="one"), pytest.param(1000, id="many")]
)
@pytest.mark.parametrize(
    ("markup", "text"),
    [pytest.param("", "", id="empty-values"), pytest.param("é<b>界</b>😀", "é界😀", id="descendant-text")],
)
def test_concat_node_strings(count: int, markup: str, text: str) -> None:
    document: Final = parse("<main>" + f"<i>{markup}</i>" * count + "</main>")
    assert document.xpath("str:concat(//i)") == text * count


def test_concat_valueless_attribute() -> None:
    assert parse('<i a b="value"></i>').xpath("str:concat(//i/@*)") == "value"


@pytest.mark.parametrize("text", [pytest.param("", id="empty"), pytest.param("é界😀", id="nonempty")])
def test_concat_constructed_text_child(text: str) -> None:
    element: Final = Element("i", None, [Text(text)])
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


@pytest.mark.parametrize(
    ("text", "search", "replacement", "expected"),
    [
        pytest.param("a" * 32768, "a" * 64 + "b" + "a" * 64, "X", "a" * 32768, id="kmp-miss"),
        pytest.param(
            ("a" * 4096 + "b" + "a" * 64 + "x") * 8,
            "a" * 64 + "b" + "a" * 64,
            "X",
            ("a" * 4032 + "Xx") * 8,
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
    document: Final = parse("<p></p>")
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
    case = cast("tuple[str, str, str, str]", load())
    operation = cast("Callable[[tuple[str, str, str, str]], str]", OPERATIONS["xpath-replace"][0])
    assert operation(case) == case[3]


@pytest.mark.parametrize("function", [pytest.param("re:test", id="exslt"), pytest.param("matches", id="xpath2")])
def test_regex_dynamic_patterns(function: str) -> None:
    document: Final = parse_xml(
        "<root>"
        + "".join(f'<n pattern="^{index}$">{index}</n><n pattern="^{index}$">miss</n>' for index in range(160))
        + "</root>"
    )
    assert document.xpath(f"count(//n[{function}(., @pattern)])") == 160


@pytest.mark.parametrize("flags", [pytest.param("", id="case-sensitive"), pytest.param("i", id="ignore-case")])
def test_regex_evaluations_keep_flags_separate(flags: str) -> None:
    expression: Final = XPath("count(//n[re:test(., $pattern, $flags)])")
    document: Final = parse_xml("<root><n>CAFÉ</n><n>café</n><n>tea</n></root>")
    assert [expression(document, pattern=pattern, flags=flags) for pattern in ("café", "tea", "café")] == (
        [2, 1, 2] if flags else [1, 1, 1]
    )


def test_regex_dynamic_flags_keep_patterns_separate() -> None:
    document: Final = parse_xml('<root><n flags="i">CAFÉ</n><n flags="">CAFÉ</n><n flags="i">CAFÉ</n></root>')
    assert document.xpath("count(//n[re:test(., 'café', @flags)])") == 2


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("re:test('10', 0)", True, id="numeric-pattern"),
        pytest.param("re:test('x', 'x', 0)", True, id="numeric-flags"),
        pytest.param("re:test('x', 'X', '')", False, id="empty-flags"),
    ],
)
def test_regex_coerces_arguments(expression: str, *, expected: bool) -> None:
    assert parse_xml("<root/>").xpath(expression) is expected


_REGEX_TEST: Final = XPath("re:test($text, $pattern, $flags)")
_REGEX_REPLACE: Final = XPath("re:replace($text, $pattern, $flags, $replacement)")
_FN_REPLACE: Final = XPath("replace($text, $pattern, $replacement)")
_REGEX_TEXTS: Final = (
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
_PYTHON_FLAGS: Final = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


def _python_regex(pattern: str, flags: str) -> re.Pattern[str]:
    python_flags = 0
    for letter in flags:
        python_flags |= _PYTHON_FLAGS[letter]
    return re.compile(pattern, python_flags)


def _forbid_python_re(mocker: MockerFixture) -> None:
    # the regex functions must never hand a pattern to Python's backtracking re engine
    mocker.patch.object(re, "compile", autospec=True, side_effect=AssertionError("re.compile called"))
    mocker.patch.object(re, "sub", autospec=True, side_effect=AssertionError("re.sub called"))


@pytest.fixture
def native_regex(mocker: MockerFixture) -> None:
    _forbid_python_re(mocker)


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
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert (result.returncode, result.stdout) == (0, f"{expected}\n"), result.stderr


def test_regex_backreference_backtracking_is_bounded() -> None:
    code = (
        "import turbohtml\ntry:\n"
        "    turbohtml.parse('<p>' + 'a' * 60 + '!</p>').xpath(\"re:test(//p, '^(a|a)+\\\\1$')\")\n"
        "except ValueError as error:\n    print(error)\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    expected: Final = (
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
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
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
            "a" * 700_000,
            "re:test(/r, '(x)?a*\\1')",
            r"regular expression back-references need more than 655360 saved backtracking positions \(10 MiB\); "
            r"search a shorter string or drop the back-reference$",
            id="trail",
        ),
    ],
)
def test_regex_backreference_backtracking_limit_raises(
    mocker: MockerFixture, body: str, expression: str, message: str
) -> None:
    document: Final = parse_xml(f"<r>{body}</r>")
    expected: Final = re.compile(message)
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
    compiled: Final = _python_regex(pattern, flags)
    template: Final = "<" + "".join(f"\\g<{index}>|" for index in range(1, compiled.groups + 1)) + "\\g<0>>"
    expected: Final = [(compiled.search(text) is not None, compiled.sub(template, text)) for text in _REGEX_TEXTS]
    _forbid_python_re(mocker)
    document: Final = parse_xml("<r/>")
    assert [
        (
            _REGEX_TEST(document, text=text, pattern=pattern, flags=flags),
            _REGEX_REPLACE(document, text=text, pattern=pattern, flags=f"{flags}g", replacement=template),
        )
        for text in _REGEX_TEXTS
    ] == expected


_FOLD_TEXTS: Final = tuple(
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
    compiled: Final = re.compile(pattern, re.IGNORECASE)
    expected: Final = [compiled.search(char) is not None for char in _FOLD_TEXTS]
    _forbid_python_re(mocker)
    document: Final = parse_xml("<r/>")
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
    expected: Final = re.compile(r"unsupported regular expression syntax: ")
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
    expected: Final = re.compile(re.escape(message))
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
    expected: Final = re.compile(
        re.escape(
            "regular expression compiles to more than 655360 instructions (10 MiB); lower its repetition counts or"
            " split it into several patterns"
        )
    )
    _forbid_python_re(mocker)
    with pytest.raises(ValueError, match=expected):
        _REGEX_TEST(parse_xml("<r/>"), text="ab", pattern=pattern, flags="")


def test_regex_replace_with_too_many_groups_raises_value_error(mocker: MockerFixture) -> None:
    expected: Final = re.compile(
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
        pytest.param("a", "\\0\\07\\101\\1011\\08", "a", id="octal-escapes"),
        pytest.param("a", "\\n\\t\\r\\f\\v\\a\\b\\\\", "a", id="control-escapes"),
        pytest.param("a", "\\-\\.\\ \\_\\~\\é", "a", id="kept-escapes"),
        pytest.param("(a)", "\\1!\\1a\\1", "a", id="group-then-text"),
        pytest.param("((((((((((((a))))))))))))", "\\12|\\123|\\128", "a", id="two-digit-group"),
        pytest.param("(a)", "\\g<01>", "a", id="zero-padded-number"),
        pytest.param("(a)|b", "[\\1]", "ab", id="unmatched-group"),
        pytest.param("x*", "-", "abc", id="empty-matches"),
        pytest.param("", "$", "ab", id="dollar-literal"),
    ],
)
def test_re_replace_template_matches_python_re(
    mocker: MockerFixture, pattern: str, replacement: str, text: str
) -> None:
    expected: Final = re.sub(pattern, replacement, text)
    _forbid_python_re(mocker)
    assert _REGEX_REPLACE(parse_xml("<r/>"), text=text, pattern=pattern, flags="g", replacement=replacement) == expected


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
    expected: Final = re.compile(re.escape(message))
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
