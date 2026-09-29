"""The compiled Matcher's soupsieve-shaped bound methods over the native engine."""

from __future__ import annotations

from typing import Final, cast

import pytest

from turbohtml import Element, parse
from turbohtml.query import (  # the soupsieve names
    DEBUG,
    Matching,
    SelectorSyntaxError,
    closest,
    compile,  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name
    css,
    escape_identifier,
    filter,  # ruff:ignore[builtin-import-shadowing]  # the soupsieve name
    iselect,
    match,
    select,
    select_one,
)

_DOC = "<div><a href=x>one</a><span><a href=y>two</a></span><a>bare</a></div>"


def _root() -> Element:
    root = parse(_DOC).root
    assert root is not None
    return root


def test_compile_returns_a_reusable_matcher() -> None:
    matcher = compile("a[href]")
    first, second = matcher.select(_root()), matcher.select(_root())
    assert [node.attr("href") for node in first] == [node.attr("href") for node in second] == ["x", "y"]


def test_css_is_a_compile_alias() -> None:
    assert [node.tag for node in css("a[href]").select(_root())] == ["a", "a"]


def test_select_returns_descendants_in_document_order() -> None:
    assert [node.attr("href") for node in compile("a[href]").select(_root())] == ["x", "y"]


def test_select_limit_caps_the_result() -> None:
    assert [node.attr("href") for node in compile("a[href]").select(_root(), limit=1)] == ["x"]


def test_select_zero_limit_returns_all() -> None:
    assert len(compile("a[href]").select(_root(), limit=0)) == 2


def test_select_one_returns_the_first_match() -> None:
    found = compile("a[href]").select_one(_root())
    assert found is not None
    assert found.attr("href") == "x"


def test_select_one_returns_none_when_absent() -> None:
    assert compile("table").select_one(_root()) is None


def test_iselect_yields_lazily() -> None:
    assert [node.attr("href") for node in compile("a[href]").iselect(_root())] == ["x", "y"]


def test_iselect_limit_caps_the_stream() -> None:
    assert [node.attr("href") for node in compile("a[href]").iselect(_root(), limit=1)] == ["x"]


def test_match_tests_a_single_element() -> None:
    anchor = compile("a[href]").select_one(_root())
    assert anchor is not None
    assert compile("a[href]").match(anchor) is True


def test_match_is_false_for_a_non_match() -> None:
    assert compile("a[href]").match(_root()) is False


def test_filter_keeps_matching_members_of_an_iterable() -> None:
    anchors = _root().select("a")
    assert [node.attr("href") for node in compile("[href]").filter(anchors)] == ["x", "y"]


def test_filter_accepts_a_generator() -> None:
    anchors = _root().select("a")
    assert [node.attr("href") for node in compile("[href]").filter(node for node in anchors)] == ["x", "y"]


def test_filter_preserves_order_and_duplicates_across_trees() -> None:
    first = parse("<a id=first class=hit></a><a id=miss></a>").select("a")
    second = parse("<a id=second class=hit></a>").select_one("a")
    assert second is not None
    assert [node.attr("id") for node in compile(".hit").filter([second, first[1], first[0], second])] == [
        "second",
        "first",
        "second",
    ]


@pytest.mark.parametrize(
    "candidates",
    [
        pytest.param([1], id="first"),
        pytest.param([_root(), 1], id="later"),
    ],
)
def test_filter_rejects_non_elements(candidates: list[object]) -> None:
    with pytest.raises(TypeError, match="filter candidates must be Element instances"):
        compile("*").filter(cast("list[Element]", candidates))


def test_filter_on_an_element_tests_its_direct_children() -> None:
    parent = parse("<div>text<a href=x>k</a><span><a href=deep>d</a></span><b>n</b></div>").select_one("div")
    assert parent is not None
    assert [node.tag for node in compile("a, span").filter(parent)] == ["a", "span"]


def test_closest_walks_up_to_the_nearest_match() -> None:
    anchor = compile("a[href=y]").select_one(_root())
    assert anchor is not None
    found = compile("div").closest(anchor)
    assert found is not None
    assert found.tag == "div"


def test_closest_returns_none_without_a_match() -> None:
    anchor = compile("a[href=x]").select_one(_root())
    assert anchor is not None
    assert compile("table").closest(anchor) is None


def test_compile_rejects_a_malformed_selector() -> None:
    with pytest.raises(SelectorSyntaxError):
        compile("a[")


def test_selector_syntax_error_is_a_value_error() -> None:
    assert issubclass(SelectorSyntaxError, ValueError)


def test_pattern_exposes_the_selector() -> None:
    assert compile("div a").pattern == "div a"


def test_namespaces_and_flags_default_to_soupsieves() -> None:
    matcher = compile("a")
    assert matcher.namespaces is None
    assert matcher.flags == 0


def test_options_are_carried_on_the_matcher() -> None:
    matcher = compile("a", Matching(namespaces={"svg": "http://www.w3.org/2000/svg"}, flags=1))
    assert matcher.namespaces == {"svg": "http://www.w3.org/2000/svg"}
    assert matcher.flags == 1


def test_default_is_soupsieves_html_mode() -> None:
    assert Matching() == Matching(namespaces=None, flags=0)


def test_config_is_frozen() -> None:
    with pytest.raises(AttributeError):
        Matching().flags = 1  # ty: ignore[invalid-assignment]  # asserting the frozen dataclass rejects it


def test_soupsieve_preset_maps_the_call_convention() -> None:
    namespaces = {"svg": "http://www.w3.org/2000/svg"}
    config = Matching.soupsieve(namespaces=namespaces, flags=DEBUG)
    assert config == Matching(namespaces=namespaces, flags=DEBUG)


def test_soupsieve_preset_defaults_match_the_plain_config() -> None:
    assert Matching.soupsieve() == Matching()


def test_debug_flag_value() -> None:
    assert DEBUG == 0x1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("foo", "foo", id="plain"),
        pytest.param("ABC", "ABC", id="uppercase-kept"),
        pytest.param("_x", "_x", id="underscore-kept"),
        pytest.param("a-", "a-", id="trailing-dash-kept"),
        pytest.param("café", "café", id="non-ascii-kept"),
        pytest.param("foo bar", "foo\\ bar", id="space-backslashed"),
        pytest.param("a#b.c", "a\\#b\\.c", id="punctuation-backslashed"),
        pytest.param("-", "\\-", id="lone-dash"),
        pytest.param("--", "--", id="double-dash-kept"),
        pytest.param("-1", "-\\31 ", id="dash-then-digit"),
        pytest.param("12ab", "\\31 2ab", id="leading-digit"),
        pytest.param("0", "\\30 ", id="lone-digit"),
        pytest.param("a\tb", "a\\9 b", id="interior-control"),
        pytest.param("\x7f", "\\7f ", id="delete-char"),
        pytest.param("\x00abc", "�abc", id="null-to-replacement"),
        pytest.param("a1b", "a1b", id="digit-after-letter-stays"),
        pytest.param("1-", "\\31 -", id="leading-digit-then-dash"),
        pytest.param("-a1", "-a1", id="dash-letter-digit"),
    ],
)
def test_escape_matches_cssom(raw: str, expected: str) -> None:
    assert escape_identifier(raw) == expected


def test_non_str_identifier_raises_type_error() -> None:
    with pytest.raises(TypeError, match="must be str"):
        escape_identifier(cast("str", b"raw-bytes"))


_MODULE_DOC = "<div><a href=x>one</a><span><a href=y>two</a></span></div>"


def _module_root() -> Element:
    root = parse(_MODULE_DOC).root
    assert root is not None
    return root


def test_select_collects_matches() -> None:
    assert [node.attr("href") for node in select("a[href]", _module_root())] == ["x", "y"]


def test_select_honors_limit() -> None:
    assert [node.attr("href") for node in select("a[href]", _module_root(), limit=1)] == ["x"]


def test_select_one_returns_first() -> None:
    found = select_one("a[href]", _module_root())
    assert found is not None
    assert found.attr("href") == "x"


def test_iselect_iterates_matches() -> None:
    assert [node.attr("href") for node in iselect("a[href]", _module_root(), limit=2)] == ["x", "y"]


def test_match_tests_an_element() -> None:
    anchor = select_one("a[href]", _module_root())
    assert anchor is not None
    assert match("a[href]", anchor) is True


def test_filter_keeps_matching_members() -> None:
    anchors = _module_root().select("a")
    assert [node.attr("href") for node in filter("[href=y]", anchors)] == ["y"]


def test_closest_walks_up() -> None:
    anchor = select_one("a[href=y]", _module_root())
    assert anchor is not None
    found = closest("div", anchor)
    assert found is not None
    assert found.tag == "div"


_ORACLE_DOCS: Final = {
    "article": (
        '<!doctype html><html lang="en"><head><title>T</title></head><body>'
        '<div id="div" class="foo bar">'
        '<p id="0" class="test1 test2 test3">Some text <span id="1" class="foo"> in a paragraph</span>.</p>'
        '<a id="2" href="http://google.com">Link</a>'
        '<span id="3" class="foo bar">Direct child</span>'
        '<pre id="pre" class="test-a test-b">'
        '<span id="4">Child 1</span>'
        '<span id="5" class="test2">Child 2</span>'
        '<span id="6">Child 3</span>'
        "</pre>"
        "</div>"
        '<main><article><h2>One</h2><p lang="en-US">p1</p><p class="lead">p2</p><p></p></article>'
        '<article dir="rtl"><h2>Two</h2><p>p3</p><em>em</em><strong>st</strong></article></main>'
        '<nav><ul><li><a href="/x">x</a></li><li class="on"><a href="/y">y</a></li><li>plain</li></ul></nav>'
        "</body></html>"
    ),
    "form": (
        "<!doctype html><html><head><title>F</title></head><body>"
        '<form action="/s"><fieldset><legend>L</legend>'
        '<input type="text" name="q" value="v" required>'
        '<input type="checkbox" name="c" checked>'
        '<input type="radio" name="r" disabled>'
        '<input type="number" min="0" max="9" value="5">'
        '<select><option value="a" selected>a</option><option value="b">b</option></select>'
        '<optgroup label="g"><option>c</option></optgroup>'
        '<textarea name="t" readonly>text</textarea>'
        '<button type="submit" disabled>go</button>'
        "</fieldset></form>"
        "<table><thead><tr><th>H1</th><th>H2</th></tr></thead><tbody>"
        "<tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr>"
        "</tbody></table>"
        "<div><span>only</span> tail</div>"
        "</body></html>"
    ),
}

_ORACLE_SELECTORS: Final = (
    "a:active",
    ".foo",
    "a.bar",
    "a.\\0 bar",
    "span.foo\\",
    "a.foo.bar",
    "div span",
    ".foo\\:bar\\3a foobar",
    "#\\31",
    "a#\\32",
    "span, a",
    "span",
    "tag",
    "Tag",
    "TAG",
    "a:visited",
    "[href]",
    "[   href   ]",
    "span[id].test[data-test=test]",
    "[id=\\35]",
    "[id='5']",
    '[id="5"]',
    '[  id  =  "5"  ]',
    '[ID="5"]',
    '[id="\x00pre"]',
    '[id="\\0 pre"]',
    '[type="test"]',
    "[lang|=en]",
    "[class~=test2]",
    "[class~=test-a]",
    "[class~=test-b]",
    '[class~="test1 test2"]',
    '[class~=""]',
    '[class~="test1\\ test2"]',
    "div > span",
    "div>span",
    "span:first-child",
    "input:focus",
    "input:not(:focus)",
    "a:hover",
    "p:lang(de)",
    "p:lang(en)",
    "span + span",
    "span+span",
    "span#\\34 + span#\\35",
    "body *",
    "[class^=here]",
    "[class$=words]",
    "[class*=words]",
    "span[title*='bar']",
    "span[title^='foo']",
    "span[title$='bar']",
    "span[title|='fo']",
    "span[title~='baz']",
    ":checked",
    ":disabled",
    "body :empty",
    ":enabled",
    "p:first-of-type",
    "span:first-of-type",
    "body :first-of-type",
    "span:last-child",
    "span:LAST-CHILD",
    "p:last-of-type",
    "span:last-of-type",
    "body :last-of-type",
    "ns1|el, ns2|el",
    'div :not([id="1"])',
    'span:not([id="1"])',
    'div :NOT([id="1"])',
    "p:nth-child(-2)",
    "p:nth-child(2)",
    "p:NTH-CHILD(2)",
    "p:NT\\H-CH\\ILD(2)",
    "p:nth-child(odd)",
    "p:nth-child(ODD)",
    "p:nth-child(even)",
    "p:nth-child(EVEN)",
    "p:nth-child(2n-5)",
    "p:nth-child(2N-5)",
    "p:nth-child(-2n+20)",
    "p:nth-child(50n-20)",
    "p:nth-child(-2n-2)",
    "p:nth-child(9n - 1)",
    "p:nth-child(2n + 1)",
    "p:nth-child(-n+3)",
    "span:nth-child(-n+3)",
    "p:nth-last-child(2)",
    "p:nth-last-child(2n + 1)",
    "p:nth-last-of-type(3)",
    "p:nth-last-of-type(2n + 1)",
    "p:nth-of-type(3)",
    "p:nth-of-type(2n + 1)",
    "span:nth-of-type(2n + 1)",
    "span:only-child",
    "p:only-of-type",
    ":root",
    ":root > body > div",
    ":root div",
    "p ~ span",
    "#head-2:target",
    "#head-2:not(:target)",
    "[class*=WORDS]",
    "[class*=WORDS i]",
    "[class*=WORDSi]",
    "[class*='WORDS'i]",
    '[type="test" i]',
    '[type="test" s]',
    ":default",
    ":default:default",
    "div:dir(rtl)",
    "div:dir(ltr)",
    "div:dir(ltr):dir(rtl)",
    "span:dir(rtl)",
    "span:dir(ltr)",
    ":is(input, textarea):dir(ltr)",
    "html:dir(ltr)",
    "math:dir(rtl)",
    "form:focus-visible",
    "form:not(:focus-visible)",
    "form:focus-within",
    "form:not(:focus-within)",
    "div:not(.aaaa):has(.kkkk > p.llll)",
    "p:has(+ .dddd:has(+ div .jjjj))",
    "p:has(~ .jjjj)",
    "div:has(> .bbbb)",
    "div:NOT(.aaaa):HAS(.kkkk > p.llll)",
    "div:has(> .bbbb, .ffff, .jjjj)",
    "div:has(.ffff, > .bbbb, .jjjj)",
    "div:has(> :not(.bbbb, .ffff, .jjjj))",
    "div:not(:has(> .bbbb, .ffff, .jjjj))",
    ":is(span, a)",
    ":is(span, , a)",
    ":is(, span, a)",
    ":is(span, a, )",
    ":is()",
    ":is(span, a:is(#\\32))",
    ":is(span):not(span)",
    ":is(span):is(div)",
    ":is(a):is(#\\32)",
    "p:lang(de-DE)",
    "p:lang(de--DE)",
    "p:lang(de-\\*-DE)",
    "p:lang('*-de-DE')",
    "p:lang('*-*-*-DE')",
    "p:lang(\\*-DE)",
    "p:lang('de-DE')",
    "p:lang('de-\\\nDE')",
    "p:lang('*-DE')",
    "p[lang]:lang(de-DE)",
    "div:lang('')",
    "mtext:lang(en)",
    "div :not(p, :not([id=\\35]))",
    ":nth-child(-n+3 of p)",
    ":nth-child(2n + 1 of :is(p, span).test)",
    ":nth-child(2n + 1 OF :is(p, span).test)",
    ":nth-last-child(2n + 1 of p[id], span[id])",
    ":optional",
    "input:optional",
    "body :read-only",
    ":read-write",
    ":required",
    "input:required",
    "article:target-within",
    "article:not(:target-within)",
    ":where(span, a)",
    ":where(span, a:where(#\\32))",
    ":any-link",
    ":link",
    "a:any-link",
    ":not(a:any-link)",
    ":lang('*')",
    ":lang('*-US')",
    "p:lang('*-US')",
    ":lang(\\*-DE)",
    ":scope",
    ":scope > body",
    ":scope > body > div",
    ":scope a",
)


@pytest.mark.parametrize("selector", _ORACLE_SELECTORS, ids=repr)
@pytest.mark.parametrize("doc_name", list(_ORACLE_DOCS), ids=list(_ORACLE_DOCS))
@pytest.mark.oracle
def test_matches_soupsieve(doc_name: str, selector: str) -> None:
    ours, theirs = _oracle_hits(_ORACLE_DOCS[doc_name], selector)
    assert ours == theirs


@pytest.mark.oracle
def test_corpus_exercises_the_fixtures() -> None:
    matching = sum(
        1 for selector in _ORACLE_SELECTORS if any(_oracle_hits(html, selector)[0] for html in _ORACLE_DOCS.values())
    )
    assert matching >= 90


@pytest.mark.oracle
def _oracle_hits(document_html: str, selector: str) -> tuple[list[int], list[int]]:
    bs4: Final = pytest.importorskip("bs4")
    soupsieve: Final = pytest.importorskip("soupsieve")
    turbo_doc = parse(document_html)
    soup = bs4.BeautifulSoup(document_html, "html.parser")
    turbo_index = {
        element: position
        for position, element in enumerate(node for node in turbo_doc.descendants if isinstance(node, Element))
    }
    soup_index = {id(element): position for position, element in enumerate(soup.find_all(name=True))}
    ours = sorted(turbo_index[element] for element in select(selector, turbo_doc))
    theirs = sorted(soup_index[id(element)] for element in soupsieve.select(selector, soup))
    return ours, theirs
