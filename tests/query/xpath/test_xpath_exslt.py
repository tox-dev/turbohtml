"""The EXSLT namespaces built into the XPath engine.

parsel and scrapy enable the EXSLT regexp namespace by default and lean on
``re:test`` in predicates, so turbohtml dispatches the EXSLT prefixes the way
``libexslt`` gives them to lxml -- without the caller registering a namespace:

* ``re:`` (``test``/``replace``) dispatches to Python's :mod:`re`;
* ``set:`` operates on node-sets (``difference``, ``intersection``, ``distinct``,
  ``has-same-node``, ``leading``, ``trailing``);
* ``str:`` builds strings (``replace`` literal-not-regex, ``concat``, ``padding``,
  ``align``) -- the node-synthesizing ``tokenize``/``split`` stay out of scope;
* ``math:`` reduces node-sets and numbers (``min``, ``max``, ``highest``,
  ``lowest``, ``abs``, ``power``);
* ``date:`` reads fields from an explicit ISO ``YYYY-MM-DD`` string (``year``,
  ``month-in-year``, ``day-in-month``, ``day-in-week``, ``leap-year``) -- the
  implicit current-date form stays out of scope so the engine is deterministic.

lxml has to register each namespace; the prefixes are built in here.
"""

from __future__ import annotations

import math
import re

import pytest

import turbohtml
from turbohtml import Element

HTML = (
    "<html><body>"
    "<a href='/path/123'>a</a><a href='HTTP://x'>b</a><a href='/y'>c</a>"
    "<ul><li id='a' class='x'>1</li><li id='b' class='y'>2</li>"
    "<li id='c' class='x'>3</li><li id='d'>1</li></ul>"
    "<div id='nums'><n>3</n><n>1</n><n>5</n><n>5</n></div>"
    "<div id='mixed'><m>3</m><m>nope</m></div>"
    "</body></html>"
)


@pytest.fixture
def doc() -> turbohtml.Node:
    return turbohtml.parse(HTML)


def tags(result: list[Element | str]) -> list[str]:
    return [node.tag if isinstance(node, Element) else node for node in result]


def ids(result: list[Element | str]) -> list[str]:
    collected: list[str] = []
    for node in result:
        assert isinstance(node, Element)
        value = node.attrs["id"]
        assert isinstance(value, str)
        collected.append(value)
    return collected


def texts(result: list[Element | str]) -> list[str]:
    return [node.text for node in result if isinstance(node, Element)]


# --- re: regular-expression functions -------------------------------------- #


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
def test_re_test(doc: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert tags(doc.xpath(expr)) == expected


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
def test_re_replace(doc: turbohtml.Node, expr: str, expected: str) -> None:
    assert doc.xpath(expr) == expected


def test_re_test_global_flag_is_accepted(doc: turbohtml.Node) -> None:
    # 'g' is meaningless for a boolean test, but must be tolerated, not rejected.
    assert doc.xpath("re:test('a', 'a', 'g')") is True


def test_re_test_malformed_pattern_propagates_python_error(doc: turbohtml.Node) -> None:
    with pytest.raises(re.error):
        doc.xpath("//a[re:test(@href, '(')]")


def test_re_replace_malformed_pattern_propagates_python_error(doc: turbohtml.Node) -> None:
    with pytest.raises(re.error):
        doc.xpath("re:replace('x', '(', '', 'y')")


# --- set: node-set functions ----------------------------------------------- #


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
def test_set_nodeset_results(doc: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert ids(doc.xpath(expr)) == expected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("set:has-same-node(//li[@class='x'], //li[@id='a'])", True, id="set-shares-first"),
        pytest.param("set:has-same-node(//li, //li[@id='c'])", True, id="set-shares-later"),
        pytest.param("set:has-same-node(//li[@id='a'], //li[@id='b'])", False, id="set-disjoint"),
    ],
)
def test_set_has_same_node(doc: turbohtml.Node, expr: str, *, expected: bool) -> None:
    assert doc.xpath(expr) is expected


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("set:difference('x', //li)", id="set-difference-non-nodeset"),
        pytest.param("set:has-same-node(//li, 'x')", id="set-has-same-node-non-nodeset"),
        pytest.param("set:distinct('x')", id="set-distinct-non-nodeset"),
    ],
)
def test_set_non_nodeset_argument_raises(doc: turbohtml.Node, expr: str) -> None:
    with pytest.raises(TypeError, match="non-node-set"):
        doc.xpath(expr)


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
    doc = turbohtml.parse("<ul><li id='a'>1</li><li id='b'>22</li><li id='c'>1</li><li id='d'>22</li></ul>")
    assert ids(doc.xpath("set:distinct(//li)")) == ["a", "b"]


# --- str: string functions ------------------------------------------------- #


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
def test_str_functions(doc: turbohtml.Node, expr: str, expected: str) -> None:
    assert doc.xpath(expr) == expected


def test_str_concat_non_nodeset_argument_raises(doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="non-node-set"):
        doc.xpath("str:concat('x')")


# --- math: numeric and node-set functions ---------------------------------- #


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
def test_math_numbers(doc: turbohtml.Node, expr: str, expected: float) -> None:
    assert doc.xpath(expr) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("math:max(//m)", id="math-max-non-numeric"),
        pytest.param("math:min(//m)", id="math-min-non-numeric"),
        pytest.param("math:max(//absent)", id="math-max-empty"),
        pytest.param("math:min(//absent)", id="math-min-empty"),
    ],
)
def test_math_extreme_is_nan(doc: turbohtml.Node, expr: str) -> None:
    result = doc.xpath(expr)
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
def test_math_select(doc: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert texts(doc.xpath(expr)) == expected


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("math:max('x')", id="math-max-non-nodeset"),
        pytest.param("math:highest('x')", id="math-highest-non-nodeset"),
    ],
)
def test_math_non_nodeset_argument_raises(doc: turbohtml.Node, expr: str) -> None:
    with pytest.raises(TypeError, match="non-node-set"):
        doc.xpath(expr)


# --- date: field-extraction functions -------------------------------------- #


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
def test_date_numbers(doc: turbohtml.Node, expr: str, expected: float) -> None:
    assert doc.xpath(expr) == pytest.approx(expected)


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
def test_date_invalid_is_nan(doc: turbohtml.Node, expr: str) -> None:
    result = doc.xpath(expr)
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
def test_date_leap_year(doc: turbohtml.Node, expr: str, *, expected: bool) -> None:
    assert doc.xpath(expr) is expected
