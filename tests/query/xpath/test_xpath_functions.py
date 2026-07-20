"""XPath functions, operators, and value coercions evaluated through ``Node.xpath``.

A node-set expression returns a list; the three scalar-typed expressions return the
matching ``float`` / ``str`` / ``bool``, the same as lxml.
"""

from __future__ import annotations

import math
import re
from typing import Final
from xml.etree import ElementTree as ET  # ruff:ignore[suspicious-xml-etree-import]

import pytest

import turbohtml
from turbohtml import Element


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
        pytest.param("number('3.5')", 3.5, id="number-string"),
        pytest.param("number(true())", 1.0, id="number-bool"),
        pytest.param("number(false())", 0.0, id="number-false"),
        pytest.param("number(' -2.50 ')", -2.5, id="number-whitespace"),
        pytest.param("number('.5')", 0.5, id="number-leading-dot"),
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
    result = doc.xpath("(//div)/p")
    assert [node.tag for node in result if isinstance(node, Element)] == ["p", "p", "p"]


# An unknown function name is a static error (XPath 1.0 §3.2); nested inside any
# expression form the ValueError still propagates out, naming the offending function.
@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("bogus-fn(1)", id="unknown-function"),
        pytest.param("count(bogus-fn(1))", id="in-function-arg"),
        pytest.param("concat('x', bogus-fn(1))", id="in-later-function-arg"),
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
