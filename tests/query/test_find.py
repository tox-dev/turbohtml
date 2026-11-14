from __future__ import annotations

import gc
import itertools
import re
import sys
from functools import partial
from typing import TYPE_CHECKING, Final
from weakref import finalize

import pytest

from turbohtml import Axis, Comment, Document, Element, Range, Text, parse, parse_xml
from turbohtml.clean import collapse_whitespace_node

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TypeAlias

    from work_steps import WorkSteps

    Filter: TypeAlias = "str | re.Pattern[str] | bool | Callable[[str | None], bool] | list[Filter]"


# document order of elements: html, head, body, section, h2, p, p, a
_DOC: Final[str] = (
    '<section><h2 id="t">T</h2><p class="lead big">one</p><p class="big">two</p><a href="/x">l</a></section>'
)
_LONG_NEEDLE: Final[str] = f"{'a' * 65}ba"


def _raise(_value: str | None) -> bool:
    raise ZeroDivisionError


@pytest.mark.parametrize(
    ("tag_filter", "tags"),
    [
        pytest.param("p", ["p", "p"], id="string-exact"),
        pytest.param(re.compile(r"^h"), ["html", "head", "h2"], id="regex-search"),
        pytest.param(lambda name: name in {"h2", "a"}, ["h2", "a"], id="callable-receives-name"),
        pytest.param(["h2", "a"], ["h2", "a"], id="list-any-member"),
        pytest.param("\ud800", [], id="lone-surrogate-unencodable"),  # falls back to a str compare
        pytest.param("", [], id="empty-name"),  # no element has an empty name
    ],
)
def test_tag_filter(tag_filter: Filter, tags: list[str]) -> None:
    assert _tags(parse(_DOC).find_all(tag_filter)) == tags


def _tags(elements: list[Element]) -> list[str]:
    return [element.tag for element in elements]


def test_tag_string_is_case_sensitive() -> None:
    doc = parse("<div>x</div>")
    assert _tags(doc.find_all("div")) == ["div"]
    assert doc.find_all("DIV") == []  # tag names are lowercased, so the match is exact


def test_tag_matches_a_custom_element() -> None:
    html = "<my-widget>a</my-widget><other-thing>b</other-thing><my-widget>c</my-widget>"
    assert _tags(parse(html).find_all("my-widget")) == ["my-widget", "my-widget"]


def test_tag_true_matches_every_element() -> None:
    # tag is positional-only, so the bool filter must be passed positionally
    assert len(parse(_DOC).find_all(True)) == 8  # ruff:ignore[boolean-positional-value-in-call]


def test_find_known_tag_absent_returns_none() -> None:
    assert parse(_DOC).find("table") is None  # fast path walks the subtree and finds nothing


def test_tag_with_attribute_filter_uses_the_general_path() -> None:
    # a tag plus any other filter leaves the tag-only fast path
    assert _tags(parse(_DOC).find_all("a", href="/x")) == ["a"]


@pytest.mark.parametrize(
    "class_filter", [pytest.param("big", id="string"), pytest.param(re.compile(r"big"), id="regex")]
)
def test_tag_with_class_filter_uses_the_general_path(class_filter: Filter) -> None:
    assert _tags(parse(_DOC).find_all("p", class_=class_filter)) == ["p", "p"]


@pytest.mark.parametrize(
    "id_filter", [pytest.param("t", id="exact-string"), pytest.param(re.compile(r"^t$"), id="regex")]
)
def test_attr_matches_h2(id_filter: Filter) -> None:
    assert _el(parse(_DOC).find(id=id_filter)).tag == "h2"


def _el(result: Element | None) -> Element:
    assert result is not None
    return result


@pytest.mark.parametrize(
    "id_value",
    [pytest.param("x", id="same-length-different-content"), pytest.param("tt", id="different-length")],
)
def test_attr_string_near_miss(id_value: str) -> None:
    assert parse(_DOC).find(id=id_value) is None  # only the <h2> carries id="t"


@pytest.mark.parametrize(
    "id_filter",
    [
        # an absent attribute compares as None against each string member of the list
        pytest.param(["t", "u"], id="list-skips-absent-attribute"),
        pytest.param(True, id="true-is-presence"),
    ],
)
def test_attr_selects_h2(id_filter: Filter) -> None:
    assert _tags(parse(_DOC).find_all(id=id_filter)) == ["h2"]


def test_attr_false_is_absence() -> None:
    assert "a" not in _tags(parse(_DOC).find_all(href=False))  # only <a> carries href
    assert parse(_DOC).find("a", href=False) is None


def test_attr_callable_on_absent_value_gets_none() -> None:
    seen: list[str | None] = []
    parse("<div id=x><span></span></div>").find_all(id=lambda value: bool(seen.append(value)))
    assert None in seen  # the <span> has no id, so the callable saw None


@pytest.mark.parametrize("class_filter", [pytest.param("big", id="token"), pytest.param(True, id="true-is-presence")])
def test_class_selects_paragraphs(class_filter: Filter) -> None:
    assert _tags(parse(_DOC).find_all(class_=class_filter)) == ["p", "p"]


@pytest.mark.parametrize(
    "class_filter",
    [
        pytest.param("lead big", id="whole-value"),
        # an anchored regex matches the leading "lead" token of "lead big"
        pytest.param(re.compile(r"^lea"), id="regex-token"),
    ],
)
def test_class_finds_the_lead(class_filter: Filter) -> None:
    assert _el(parse(_DOC).find(class_=class_filter)).text == "one"


_TEST_CLASS_REGEX_MATCHES_A_TOKEN_NOT_THE_WHOLE_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^big$")


def test_class_regex_matches_a_token_not_the_whole_value() -> None:
    # an anchored regex matches the "big" token of "lead big" but not the whole value
    doc = parse('<p class="lead big">x</p><p class="big">y</p>')
    assert len(doc.find_all("p", class_=_TEST_CLASS_REGEX_MATCHES_A_TOKEN_NOT_THE_WHOLE_VALUE_PATTERN)) == 2


def test_class_false_is_absence() -> None:
    assert "p" not in _tags(parse(_DOC).find_all(class_=False))


def test_class_on_valueless_attribute() -> None:
    doc = parse("<div class>")
    assert doc.find("div", class_=True) == doc.find("div")  # present
    assert doc.find("div", class_="x") is None  # no tokens to match


def test_class_via_attrs_dict_is_whole_value_not_member() -> None:
    # the attrs dict treats class as an ordinary whole-string attribute
    assert _tags(parse(_DOC).find_all("p", attrs={"class": "big"})) == ["p"]


def test_class_token_scan_handles_surrounding_whitespace() -> None:
    # leading and trailing spaces around the tokens still resolve to ["a", "b"]
    assert _el(parse('<p class=" a b ">').find("p", class_="a")).tag == "p"
    assert parse('<p class=" a b ">').find("p", class_="c") is None


@pytest.mark.parametrize(
    ("html", "class_filter"),
    [
        # a class value the same length as the filter but different content
        pytest.param('<p class="xyz">x</p>', "big", id="equal-length-mismatch"),
        # a non-matching regex walks every token of a whitespace-padded value,
        # including the trailing run that yields no token
        pytest.param('<p class=" a b ">x</p>', re.compile(r"zzz"), id="regex-no-token"),
    ],
)
def test_class_filter_no_match(html: str, class_filter: Filter) -> None:
    assert parse(html).find("p", class_=class_filter) is None


@pytest.mark.parametrize(
    "disabled_filter",
    [
        pytest.param("x", id="string"),
        pytest.param(re.compile(r"x"), id="regex"),
        # a valueless attribute compares as None against each list member
        pytest.param(["x", "y"], id="list"),
    ],
)
def test_filter_on_valueless_attribute_no_match(disabled_filter: Filter) -> None:
    assert parse("<input disabled>").find("input", disabled=disabled_filter) is None


def test_filter_on_present_valueless_attribute() -> None:
    assert _el(parse("<input disabled>").find("input", disabled=True)).tag == "input"


def test_axis_children() -> None:
    section = _el(parse(_DOC).find("section"))
    assert _tags(section.find_all("p", axis=Axis.CHILDREN)) == ["p", "p"]


@pytest.mark.parametrize(
    "target", [pytest.param("section", id="nearest"), pytest.param("body", id="walks-past-the-nearest")]
)
def test_axis_ancestors(target: str) -> None:
    h2 = _el(parse(_DOC).find("h2"))
    assert _el(h2.find(target, axis=Axis.ANCESTORS)).tag == target


def test_axis_next_siblings() -> None:
    h2 = _el(parse(_DOC).find("h2"))
    assert _tags(h2.find_all(axis=Axis.NEXT_SIBLINGS)) == ["p", "p", "a"]


def test_axis_previous_siblings() -> None:
    link = _el(parse(_DOC).find("a"))
    assert _tags(link.find_all(axis=Axis.PREVIOUS_SIBLINGS)) == ["p", "p", "h2"]


def test_axis_following_skips_text_nodes() -> None:
    h2 = _el(parse(_DOC).find("h2"))
    assert _el(h2.find("a", axis=Axis.FOLLOWING)).tag == "a"


def test_axis_preceding_excludes_ancestors() -> None:
    link = _el(parse(_DOC).find("a"))
    assert _el(link.find("h2", axis=Axis.PRECEDING)).tag == "h2"


@pytest.mark.parametrize(
    ("limit", "count"),
    [
        pytest.param(None, 2, id="none"),
        pytest.param(9, 2, id="larger-than-walk-threshold"),
        pytest.param(1, 1, id="one"),
        pytest.param(0, 0, id="zero"),
    ],
)
def test_limit(limit: int | None, count: int) -> None:
    assert len(parse(_DOC).find_all("p", limit=limit)) == count


def test_negative_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="limit must be non-negative"):
        parse(_DOC).find_all("p", limit=-1)


def test_limit_on_the_general_path() -> None:
    # a bool filter uses the general matcher, where the limit applies the same way
    assert len(parse(_DOC).find_all(True, limit=1)) == 1  # ruff:ignore[boolean-positional-value-in-call]


def test_dynamic_attr_name() -> None:
    assert _el(parse('<div data-x="v">').find("div", attrs={"data-x": "v"})).tag == "div"


@pytest.mark.parametrize(
    "attrs",
    [
        pytest.param({"data-missing": True}, id="name-unseen-in-tree"),
        pytest.param({"": True}, id="empty-name"),
    ],
)
def test_attrs_dict_matches_nothing(attrs: dict[str, Filter]) -> None:
    assert parse("<div>").find_all("div", attrs=attrs) == []


@pytest.mark.parametrize(
    "id_filter",
    [
        pytest.param(_raise, id="direct-callable"),
        pytest.param([re.compile(r"z"), _raise], id="callable-in-list"),
    ],
)
def test_callable_error_propagates(id_filter: Filter) -> None:
    with pytest.raises(ZeroDivisionError):
        parse("<p>").find(id=id_filter)


@pytest.mark.parametrize(
    "query",
    [
        pytest.param(lambda doc: doc.find("p", id=_raise), id="find"),
        pytest.param(lambda doc: doc.find_all("p", id=_raise), id="find-all"),
    ],
)
def test_indexed_tag_filter_error_propagates(query: Callable[[Document], object]) -> None:
    document = parse("<p>x</p>")
    document.find_all("p")
    with pytest.raises(ZeroDivisionError):
        query(document)


def test_find_filtered_on_warm_tree() -> None:
    document = parse("<p id='first'/><p id='target'/>")
    document.find_all("p")
    assert _el(document.find("p", id="target")).attrs["id"] == "target"


def test_find_filtered_on_warm_tree_miss() -> None:
    document = parse("<p id='first'/><p id='target'/>")
    document.find_all("p")
    assert document.find("p", id="missing") is None


def test_find_without_warm_index_bucket() -> None:
    document = parse("<custom id='target'/>")
    document.find_all("div")
    assert _el(document.find("custom")).attrs["id"] == "target"


def test_find_without_warm_index_empty_bucket() -> None:
    document = parse("<div/>")
    document.find_all("div")
    assert document.find("p") is None


def test_find_on_warm_subtree() -> None:
    document = parse("<section><p id='inside'/></section><p id='outside'/>")
    document.find_all("p")
    assert (section := document.find("section")) is not None
    assert (match := section.find("p")) is not None
    assert match.attrs["id"] == "inside"


@pytest.mark.parametrize(
    ("markup", "prime_tag", "tag"),
    [
        pytest.param("<p id='first'/><p id='second'/>", "p", "p", id="known-tag"),
        pytest.param("<custom id='first'/><custom id='second'/>", "div", "custom", id="unknown-tag"),
    ],
)
def test_find_all_limit_on_warm_tree(markup: str, prime_tag: str, tag: str) -> None:
    document = parse(markup)
    document.find_all(prime_tag)
    assert [element.attrs["id"] for element in document.find_all(tag, limit=1)] == ["first"]


def test_find_all_limit_on_warm_subtree() -> None:
    document = parse("<section><p id='inside'/></section><p id='outside'/>")
    document.find_all("p")
    assert (section := document.find("section")) is not None
    assert [element.attrs["id"] for element in section.find_all("p", limit=1)] == ["inside"]


def test_limited_tag_filter_error_propagates_on_cold_tree() -> None:
    with pytest.raises(ZeroDivisionError):
        parse("<p>x</p>").find_all("p", id=_raise, limit=1)


@pytest.mark.parametrize(
    "call",
    [
        # axis must be an Axis member; a str is rejected at runtime
        pytest.param(lambda: parse(_DOC).find("p", axis="descendants"), id="bad-axis-type"),  # ty: ignore[invalid-argument-type]
        # limit must be an int or None; a str is rejected at runtime
        pytest.param(lambda: parse(_DOC).find_all("p", limit="lots"), id="bad-limit-type"),  # ty: ignore[invalid-argument-type]
        # attrs must be a Mapping; a list is rejected at runtime
        pytest.param(lambda: parse(_DOC).find("p", attrs=["id"]), id="attrs-not-a-mapping"),  # ty: ignore[invalid-argument-type]
        # a non-str attribute name is rejected at runtime
        pytest.param(lambda: parse(_DOC).find("p", attrs={1: "x"}), id="attr-name-not-a-str"),  # ty: ignore[invalid-argument-type]
        # an int is not a valid filter type
        pytest.param(lambda: parse("<p>").find(id=123), id="unknown-filter-type"),  # ty: ignore[invalid-argument-type]
        # text= takes the same filter grammar; an int is rejected at runtime
        pytest.param(lambda: parse("<p>go</p>").find_all(text=123), id="bad-text-type"),  # ty: ignore[invalid-argument-type]
    ],
)
def test_type_errors(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        call()


# The text= filter runs against the element's collected subtree text (what .text returns):
# an exact str, a searched regex, or a callable. It composes with the structural filters,
# and -- because a regex/callable runs Python mid-walk -- the C side snapshots candidates
# and their text under the per-tree lock before running the predicate.

_TEXT_DOC: Final[str] = (
    "<section>"
    '<button class="buy">Add to cart</button>'
    "<p>Price: $19</p>"
    '<span data-sku="x">SKU-7788</span>'
    "<button>Cancel</button>"
    "</section>"
)


def _starts_with_sku(value: str | None) -> bool:
    return value is not None and value.startswith("SKU")


@pytest.mark.parametrize(
    ("text_filter", "tags"),
    [
        pytest.param("Add to cart", ["button"], id="string-is-exact-collected-text"),
        pytest.param(re.compile(r"\$\d+"), ["p"], id="regex-searches"),
        pytest.param(_starts_with_sku, ["span"], id="callable-receives-text"),
        pytest.param("Cancel", ["button"], id="string-matches-second-button"),
        pytest.param("nope", [], id="string-no-match"),
    ],
)
def test_text_predicate_kinds(text_filter: Filter, tags: list[str]) -> None:
    # search the section's descendants so the html/body/section wrappers (whose collected
    # text concatenates every child) do not also match
    section = _el(parse(_TEXT_DOC).find("section"))
    assert _tags(section.find_all(text=text_filter)) == tags


@pytest.mark.parametrize(
    ("html", "text_filter", "tags"),
    [
        # a plain str is the whole collected text, not a substring of it
        pytest.param(
            "<section><p>Buy now</p><p>Buy</p></section>", "Buy", ["p"], id="string-is-full-text-not-substring"
        ),
        pytest.param("<section><p>Buy now</p><p>Buy</p></section>", "Buy now", ["p"], id="string-matches-full-text"),
        # <p> collects "Buy " + "now"; only the <b> leaf collects exactly "now"
        pytest.param("<section><p>Buy <b>now</b></p></section>", "Buy now", ["p"], id="collects-nested-descendants"),
        pytest.param("<section><p>Buy <b>now</b></p></section>", "now", ["b"], id="nested-leaf-only"),
        # a loose text node is on the walk but never matches the element-only predicate
        pytest.param("<section>loose<p>go</p></section>", "go", ["p"], id="elements-only-not-text-nodes"),
        pytest.param(
            "<section><p>Buy <b>now</b></p></section>", re.compile(r"now"), ["p", "b"], id="literal-substring"
        ),
        pytest.param(
            "<section><p>Buy <b>now</b></p></section>", re.compile(r"Buynow"), [], id="literal-longer-than-text"
        ),
        pytest.param("<section><p>Buy now</p></section>", re.compile(r""), ["p"], id="literal-empty"),
        pytest.param(
            "<section><p>no not now</p></section>", re.compile(r"now"), ["p"], id="literal-partial-then-match"
        ),
        pytest.param("<section><p>at the café</p></section>", re.compile(r"café"), ["p"], id="literal-non-ascii"),
        pytest.param("<section><p>Buy now</p></section>", re.compile(r"buy", re.IGNORECASE), ["p"], id="ignorecase"),
        pytest.param("<section><p>ab</p></section>", re.compile(r"a b", re.VERBOSE), ["p"], id="verbose"),
    ],
)
def test_text_over_a_section(html: str, text_filter: Filter, tags: list[str]) -> None:
    section = _el(parse(html).find("section"))
    assert _tags(section.find_all(text=text_filter)) == tags


def test_text_matches_an_ancestor_whose_collected_text_equals_the_target() -> None:
    # collected text is the whole subtree, so a wrapper with one matching child matches too
    doc = parse("<div><p>solo</p></div>")
    assert _tags(_el(doc.find("body")).find_all(text="solo")) == ["div", "p"]


_TEST_TEXT_FIND_RETURNS_FIRST_MATCH_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$")


def test_text_find_returns_first_match() -> None:
    section = _el(parse(_TEXT_DOC).find("section"))
    assert _el(section.find(text=_TEST_TEXT_FIND_RETURNS_FIRST_MATCH_PATTERN)).tag == "p"


def _first_text(root: Element) -> Text:
    paragraph = root.children[0]
    assert isinstance(paragraph, Element)
    text = paragraph.children[0]
    assert isinstance(text, Text)
    return text


def _copied_empty_text() -> Element:
    return Element("div", children=[Element("p", children=[Text(""), Text("xy")])])


def _emptied_text() -> Element:
    root = Element("div", children=[Element("p", children=[Text("a"), Text("xy")])])
    _first_text(root).data = ""
    return root


def _split_off_empty_text() -> Element:
    root = Element("div", children=[Element("p", children=[Text("xy")])])
    Range(_first_text(root), 2).insert_node(Comment("c"))
    return root


def _collapsed_text() -> Element:
    root = Element("div", children=[Element("p", children=[Text("a "), Text(" "), Text("xy")])])
    collapse_whitespace_node(root)
    return root


_TEST_TEXT_SCAN_COPIES_AN_EMPTY_TEXT_NODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"x")


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_copied_empty_text, id="copied"),
        pytest.param(_emptied_text, id="emptied"),
        pytest.param(_split_off_empty_text, id="split-off"),
        pytest.param(_collapsed_text, id="collapsed"),
    ],
)
def test_text_scan_copies_an_empty_text_node(build: Callable[[], Element]) -> None:
    # the scan memcpys every Text node, so an empty one must not carry a NULL text pointer even at length 0
    assert _tags(build().find_all(text=_TEST_TEXT_SCAN_COPIES_AN_EMPTY_TEXT_NODE_PATTERN)) == ["p"]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [pytest.param(r"x", [], id="needle"), pytest.param(r"", ["p"], id="empty-needle")],
)
def test_text_scan_over_an_empty_subtree(pattern: str, expected: list[str]) -> None:
    # an all-empty subtree never allocates the scratch buffer, which memcpy may not receive as NULL
    root: Final = Element("div", children=[Element("p", children=[Text("")])])
    assert _tags(root.find_all(text=re.compile(pattern))) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        pytest.param(lambda doc: doc.find(text="missing"), None, id="find-none"),
        pytest.param(lambda doc: doc.find_all(text="missing"), [], id="find-all-empty"),
        # a known tag absent from the tree leaves zero candidates before any text is gathered
        pytest.param(lambda doc: doc.find("table", text="go"), None, id="no-candidate-find"),
        pytest.param(lambda doc: doc.find_all("table", text="go"), [], id="no-candidate-find-all"),
    ],
)
def test_text_no_match(
    query: Callable[[Document], Element | list[Element] | None], expected: list[Element] | None
) -> None:
    assert query(parse(_TEXT_DOC)) == expected


_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^b")

_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_2: Final[re.Pattern[str]] = re.compile(r"go\d")

_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_3: Final[re.Pattern[str]] = re.compile(r"go\d")

_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_4: Final[re.Pattern[str]] = re.compile(r"^button$")

_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_5: Final[re.Pattern[str]] = re.compile(r"go\d")

_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_6: Final[re.Pattern[str]] = re.compile(r"go\d")

_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_7: Final[re.Pattern[str]] = re.compile(r"go\d")


@pytest.mark.parametrize(
    ("html", "query", "tags"),
    [
        # only the <button> whose text matches, not the <p> that also carries it
        pytest.param(
            "<button>Add to cart</button><p>Add to cart</p>",
            lambda doc: doc.find_all("button", text="Add to cart"),
            ["button"],
            id="tag-string",
        ),
        pytest.param(
            "<button>go</button><bdo>go</bdo><div>go</div>",
            lambda doc: doc.find_all(_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN, text="go"),
            ["button", "bdo"],
            id="tag-regex",
        ),
        pytest.param(
            "<my-widget>go</my-widget><my-widget>stop</my-widget>",
            lambda doc: doc.find_all("my-widget", text="go"),
            ["my-widget"],
            id="custom-element-tag",
        ),
        pytest.param(
            '<button class="buy">go</button><button>go</button>',
            lambda doc: doc.find_all(class_="buy", text="go"),
            ["button"],
            id="class",
        ),
        pytest.param(
            '<span data-sku="x">go</span><span>go</span>',
            lambda doc: doc.find_all(text="go", attrs={"data-sku": "x"}),
            ["span"],
            id="attrs",
        ),
        pytest.param(
            "<button>go9</button><p>go9</p>",
            lambda doc: doc.find_all("button", text=_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_2),
            ["button"],
            id="plain-tag-prefilter",
        ),
        pytest.param(
            '<button class="buy">go9</button><button>go9</button>',
            lambda doc: doc.find_all(class_="buy", text=_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_3),
            ["button"],
            id="plain-class-prefilter",
        ),
        pytest.param(
            "<button>go9</button><p>go9</p>",
            lambda doc: doc.find_all(
                _TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_4,
                text=_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_5,
            ),
            ["button"],
            id="regex-tag-rechecked",
        ),
        pytest.param(
            "<my-widget>go9</my-widget><my-widget>stop</my-widget>",
            lambda doc: doc.find_all("my-widget", text=_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_6),
            ["my-widget"],
            id="custom-element-tag-prefilter",
        ),
        pytest.param(
            "<p>go9</p>",
            lambda doc: doc.find_all("table", text=_TEST_TEXT_COMPOSES_WITH_STRUCTURAL_FILTER_PATTERN_7),
            [],
            id="no-candidate",
        ),
    ],
)
def test_text_composes_with_structural_filter(
    html: str, query: Callable[[Document], list[Element]], tags: list[str]
) -> None:
    assert _tags(query(parse(html))) == tags


def test_text_attribute_is_reserved_for_the_predicate() -> None:
    # text= is the predicate, so a literal text attribute is matched through attrs= instead
    doc = parse('<span text="hi">go</span><span>go</span>')
    assert _tags(doc.find_all(text="go")) == ["span", "span"]  # both spans collect "go"
    assert _tags(doc.find_all(text="go", attrs={"text": "hi"})) == ["span"]


def test_text_on_children_axis() -> None:
    section = _el(parse("<section><p>go</p><div><span>x</span></div></section>").find("section"))
    assert _tags(section.find_all(text="go", axis=Axis.CHILDREN)) == ["p"]  # the <div> child collects "x"
    assert _tags(section.find_all(text="x")) == ["div", "span"]  # <div> and its <span> both collect "x"


@pytest.mark.parametrize(
    ("limit", "count"),
    [
        pytest.param(None, 3, id="none-is-unlimited"),
        pytest.param(0, 0, id="zero-yields-nothing"),
        pytest.param(2, 2, id="caps-results"),
    ],
)
def test_text_limit(limit: int | None, count: int) -> None:
    assert len(parse("<p>go</p><p>go</p><p>go</p>").find_all(text="go", limit=limit)) == count


_TEST_TEXT_CALLABLE_ERROR_PROPAGATES_PATTERN: Final[re.Pattern[str]] = re.compile(r"g\w")

_TEST_TEXT_CALLABLE_ERROR_PROPAGATES_PATTERN_2: Final[re.Pattern[str]] = re.compile(r"g\w")


@pytest.mark.parametrize(
    "query",
    [
        # the text predicate raises while it runs over the snapshot
        pytest.param(lambda doc: doc.find_all(text=_raise), id="text-predicate-find-all"),
        pytest.param(lambda doc: doc.find(text=_raise), id="text-predicate-find"),
        # a structural callable raises during the snapshot pass, before the text predicate runs
        pytest.param(lambda doc: doc.find_all(text="go", id=_raise), id="structural-find-all"),
        pytest.param(lambda doc: doc.find(text="go", id=_raise), id="structural-find"),
        pytest.param(
            lambda doc: doc.find_all(text=_TEST_TEXT_CALLABLE_ERROR_PROPAGATES_PATTERN, id=_raise),
            id="structural-find-all-regex",
        ),
        pytest.param(
            lambda doc: doc.find(text=_TEST_TEXT_CALLABLE_ERROR_PROPAGATES_PATTERN_2, id=_raise),
            id="structural-find-regex",
        ),
    ],
)
def test_text_callable_error_propagates(query: Callable[[Document], object]) -> None:
    with pytest.raises(ZeroDivisionError):
        query(parse(_TEXT_DOC))


@pytest.mark.parametrize(
    ("text", "pattern", "count"),
    [
        pytest.param("a" * 400, _LONG_NEEDLE, 0, id="overlapping-miss"),
        pytest.param("a" * 10 + _LONG_NEEDLE + "c" * 200, _LONG_NEEDLE, 1, id="direct-late-match"),
        pytest.param("a" * 200 + _LONG_NEEDLE, _LONG_NEEDLE, 1, id="fallback-late-match"),
        pytest.param(f"{'a' * 200}c{'a' * 199}", _LONG_NEEDLE, 0, id="late-miss"),
        pytest.param("a" * 400, f"{'a' * 65}b", 0, id="prefilter-miss"),
        pytest.param("a" * 100, _LONG_NEEDLE, 0, id="short-hay-miss"),
        pytest.param("a" * 65, _LONG_NEEDLE, 0, id="needle-longer"),
        pytest.param(_LONG_NEEDLE, _LONG_NEEDLE, 1, id="exact-match"),
    ],
)
def test_text_literal_regex_long_search(text: str, pattern: str, count: int) -> None:
    section = _el(parse(f"<section><p>{text}</p></section>").find("section"))
    assert len(section.find_all(text=re.compile(pattern))) == count


_TEST_TEXT_LITERAL_REGEX_FIND_RETURNS_FIRST_PATTERN: Final[re.Pattern[str]] = re.compile(r"now")

_TEST_TEXT_LITERAL_REGEX_FIND_RETURNS_FIRST_PATTERN_2: Final[re.Pattern[str]] = re.compile(r"absent")


def test_text_literal_regex_find_returns_first() -> None:
    section = _el(parse("<section><p>a</p><p>now</p></section>").find("section"))
    assert _el(section.find(text=_TEST_TEXT_LITERAL_REGEX_FIND_RETURNS_FIRST_PATTERN)).tag == "p"
    assert section.find(text=_TEST_TEXT_LITERAL_REGEX_FIND_RETURNS_FIRST_PATTERN_2) is None


def test_text_scan_descends_into_template_content() -> None:
    # a template's children live under a content fragment, which the text scan must descend into
    matches = _tags(parse("<template>inner</template>").find_all(text="inner"))
    assert "template" in matches


def test_text_scan_skips_non_text_children() -> None:
    # the gathered text concatenates only Text descendants, so a comment between them drops out
    matches = _tags(parse("<div>a<!--c-->b</div>").find_all(text="ab"))
    assert "div" in matches


_TEST_TEXT_BYTES_PATTERN_RAISES_PATTERN: Final[re.Pattern[bytes]] = re.compile(rb"test")


def test_text_bytes_pattern_raises() -> None:
    # a bytes pattern cannot search a str value; this matches the pre-existing behavior
    with pytest.raises(TypeError):
        parse("<p>test</p>").find_all(text=_TEST_TEXT_BYTES_PATTERN_RAISES_PATTERN)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_text_empty_string_matches_textless_element() -> None:
    section = _el(parse("<section><br></section>").find("section"))
    assert _tags(section.find_all(text="")) == ["br"]


def test_text_equal_length_different_content_does_not_match() -> None:
    # both are three code points, so the length gate passes and the content compare decides
    assert parse("<p>abc</p>").find_all(text="xyz") == []


def test_text_scan_handles_programmatic_tree_beyond_parser_depth_cap() -> None:
    root = Element("div")
    parent = root
    for _ in range(1_200):
        child = Element("div")
        parent.append(child)
        parent = child
    parent.append(Text("needle"))
    assert root.find(text="needle") is not None


_TEST_TEXT_PYTHON_PATH_LIMIT_CAPS_RESULTS_PATTERN: Final[re.Pattern[str]] = re.compile(r"go\d")


def test_text_python_path_limit_caps_results() -> None:
    # a non-literal regex exercises the snapshot path's own result cap
    matches = parse("<p>go9</p><p>go9</p><p>go9</p>").find_all(
        text=_TEST_TEXT_PYTHON_PATH_LIMIT_CAPS_RESULTS_PATTERN, limit=2
    )
    assert len(matches) == 2


@pytest.mark.parametrize("tag", ["div", "DIV", "custom", "Custom"])
def test_xml_find_all_matches_exact_tag_name(tag: str, xml_case_document: Document) -> None:
    assert [element.tag for element in xml_case_document.find_all(tag)] == [tag]


@pytest.mark.parametrize("tag", ["div", "DIV", "custom", "Custom"])
def test_xml_find_matches_exact_tag_name(tag: str, xml_case_document: Document) -> None:
    assert (match := xml_case_document.find(tag)) is not None
    assert match.tag == tag


def test_xml_find_all_matches_known_tag_below_subtree() -> None:
    root = parse_xml("<Root><section><div id='inside'/></section><div id='outside'/></Root>").root
    assert root is not None
    assert (section := root.find("section")) is not None
    assert [element.attrs["id"] for element in section.find_all("div")] == ["inside"]


@pytest.fixture
def xml_case_document() -> Document:
    return parse_xml("<Root><div/><DIV/><custom/><Custom/></Root>")


@pytest.mark.parametrize(
    ("present", "text"), [pytest.param(True, "present", id="present"), pytest.param(False, "absent", id="absent")]
)
@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param("custom", id="valueless"),
        pytest.param('custom=""', id="empty"),
        pytest.param('custom="café😀"', id="unicode"),
        pytest.param(f'custom="{"x" * 4096}"', id="long"),
    ],
)
def test_attribute_presence_ignores_value(attribute: str, *, present: bool, text: str) -> None:
    document: Final = parse(f"<p {attribute}>present</p><p>absent</p>")
    matches: Final = document.find_all("p", attrs={"custom": present})
    assert [node.text for node in matches] == [text]


@pytest.mark.parametrize(
    ("present", "text"), [pytest.param(True, "present", id="present"), pytest.param(False, "absent", id="absent")]
)
@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param("custom", id="valueless"),
        pytest.param('custom=""', id="empty"),
        pytest.param('custom="café😀"', id="unicode"),
        pytest.param(f'custom="{"x" * 4096}"', id="long"),
    ],
)
def test_attribute_presence_ignores_value_keyword(attribute: str, *, present: bool, text: str) -> None:
    document: Final = parse(f"<p {attribute}>present</p><p>absent</p>")
    matches: Final = document.find_all("p", custom=present)
    assert [node.text for node in matches] == [text]


@pytest.mark.parametrize(
    ("markup", "expected", "matched"),
    [
        pytest.param("a<b>café</b>😀", "acafé😀", True, id="unicode-chunks"),
        pytest.param("a<b>b</b>c", "ab", False, id="extra-text"),
        pytest.param("a<b>b</b>", "abc", False, id="short-text"),
        pytest.param("a<b>cd</b>", "ac", False, id="long-later-chunk"),
        pytest.param("a<b>c</b>", "ab", False, id="different-later-chunk"),
        pytest.param("a<!-- ignored --><b>&amp;</b>", "a&", True, id="comment-and-entity"),
        pytest.param("a<template>b</template>c", "abc", True, id="template"),
        pytest.param("<br>", "", True, id="textless"),
    ],
)
def test_text_exact_across_descendants(markup: str, expected: str, *, matched: bool) -> None:
    assert len(parse(f"<div>{markup}</div>").find_all("div", text=expected)) == int(matched)


def test_text_exact_with_empty_text_node() -> None:
    root: Final = Element("main")
    child: Final = Element("p")
    child.append(Text(""))
    child.append(Text("match"))
    root.append(child)
    assert root.find_all("p", text="match") == [child]


@pytest.mark.parametrize("count", [pytest.param(0, id="empty"), pytest.param(1, id="one"), pytest.param(33, id="many")])
@pytest.mark.parametrize(
    "limit",
    [
        pytest.param(None, id="unlimited"),
        pytest.param(0, id="zero"),
        pytest.param(1, id="first"),
        pytest.param(8, id="eight"),
        pytest.param(9, id="nine"),
        pytest.param(33, id="exact"),
        pytest.param(40, id="above-count"),
    ],
)
def test_find_all_result_order(count: int, limit: int | None) -> None:
    document: Final = parse("".join(f"<section><p id='{index}'></p><span></span></section>" for index in range(count)))
    assert [element.attrs["id"] for element in document.find_all("p", limit=limit)] == [
        str(index) for index in range(count)
    ][:limit]


@pytest.mark.parametrize("count", [pytest.param(0, id="empty"), pytest.param(1, id="one"), pytest.param(33, id="many")])
@pytest.mark.parametrize(
    "limit",
    [
        pytest.param(None, id="unlimited"),
        pytest.param(0, id="zero"),
        pytest.param(1, id="first"),
        pytest.param(8, id="eight"),
        pytest.param(9, id="nine"),
        pytest.param(33, id="exact"),
        pytest.param(40, id="above-count"),
    ],
)
def test_find_all_result_order_warm(count: int, limit: int | None) -> None:
    document: Final = parse("".join(f"<section><p id='{index}'></p><span></span></section>" for index in range(count)))
    document.find_all("span")
    assert [element.attrs["id"] for element in document.find_all("p", limit=limit)] == [
        str(index) for index in range(count)
    ][:limit]


def test_find_all_results_after_mutation() -> None:
    document: Final = parse("<main><p id='first'></p><p id='second'></p></main>")
    matches: Final = document.find_all("p")
    matches[0].tag = "span"
    matches[1].extract()
    main: Final = document.find("main")
    assert main is not None
    main.append(Element("p", attrs={"id": "third"}))
    assert [element.attrs["id"] for element in document.find_all("p")] == ["third"]


@pytest.mark.parametrize("limit", [0, 1, 2, 9])
@pytest.mark.parametrize(
    ("present", "expected"),
    [pytest.param(True, ["a", "c"], id="present"), pytest.param(False, ["b", "d"], id="absent")],
)
def test_find_all_filtered_result_limit(limit: int, *, present: bool, expected: list[str]) -> None:
    document: Final = parse("<section><p id='a' data-x></p><p id='b'></p><p id='c' data-x></p><p id='d'></p></section>")
    document.find_all("span")
    assert [element.attrs["id"] for element in document.find_all("p", attrs={"data-x": present}, limit=limit)] == (
        expected[:limit]
    )


@pytest.mark.parametrize("limit", [0, 1, 2, 9])
@pytest.mark.parametrize(
    ("present", "expected"),
    [pytest.param(True, ["a", "c"], id="present"), pytest.param(False, ["b", "d"], id="absent")],
)
def test_find_all_filtered_result_limit_subtree(limit: int, *, present: bool, expected: list[str]) -> None:
    document: Final = parse("<section><p id='a' data-x></p><p id='b'></p><p id='c' data-x></p><p id='d'></p></section>")
    document.find_all("span")
    section: Final = _el(document.find("section"))
    assert [element.attrs["id"] for element in section.find_all("p", attrs={"data-x": present}, limit=limit)] == (
        expected[:limit]
    )


def test_find_all_filtered_result_after_attribute_mutation() -> None:
    document: Final = parse("<p id='a' data-x></p><p id='b'></p><p id='c' data-x></p>")
    document.find_all("span")
    target: Final = document.find("p", id="b")
    assert target is not None
    target.attrs["data-x"] = None
    assert [element.attrs["id"] for element in document.find_all("p", attrs={"data-x": True})] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("class_filter", "attrs"),
    [
        pytest.param("hot", {"data-x": True}, id="plain-class"),
        pytest.param(re.compile(r"hot"), {"data-x": True}, id="regex-class"),
    ],
)
def test_find_all_indexed_filtered_limit(class_filter: str | re.Pattern[str], attrs: dict[str, str | bool]) -> None:
    document: Final = parse(
        "<p id='a' class='cold' data-x='one'></p>"
        "<p id='b' class='hot' data-x='two' data-y></p>"
        "<p id='c' class='hot' data-x='two' data-y></p>"
    )
    document.find_all("p")
    matches: Final = document.find_all("p", class_=class_filter, attrs=attrs, limit=1)
    assert [element.attrs["id"] for element in matches] == ["b"]


@pytest.mark.parametrize(
    "attrs",
    [
        pytest.param({"data-x": "two"}, id="attribute-value"),
        pytest.param({"data-x": True, "data-y": True}, id="two-attributes"),
    ],
)
def test_find_all_indexed_attribute_filtered_limit(attrs: dict[str, str | bool]) -> None:
    document: Final = parse(
        "<p id='a' class='cold' data-x='one'></p>"
        "<p id='b' class='hot' data-x='two' data-y></p>"
        "<p id='c' class='hot' data-x='two' data-y></p>"
    )
    document.find_all("p")
    matches: Final = document.find_all("p", attrs=attrs, limit=1)
    assert [element.attrs["id"] for element in matches] == ["b"]


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", [0, 1, 2])
@pytest.mark.parametrize(
    ("count", "expected"),
    [
        pytest.param(
            0,
            [],
            id="clear",
            marks=pytest.mark.skipif(sys.version_info >= (3, 12), reason="CPython 3.12 keeps results"),
        ),
        pytest.param(
            0,
            ["old"] * 32,
            id="clear-3.12",
            marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="CPython before 3.12 reads cleared results"),
        ),
        pytest.param(
            33,
            ["new"] * 32,
            id="replace",
            marks=pytest.mark.skipif(sys.version_info >= (3, 12), reason="CPython 3.12 keeps results"),
        ),
        pytest.param(
            33,
            ["old"] * 32,
            id="replace-3.12",
            marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="CPython before 3.12 reads cleared results"),
        ),
    ],
)
def test_find_all_results_during_collection(offset: int, count: int, expected: list[str]) -> None:
    thresholds: Final = gc.get_threshold()
    gc.disable()
    gc.collect()
    document: Final = parse(f"""<main>{"<p id='old'>x</p>" * 33}</main>""")
    document.find_all("p")
    main: Final = document.find("main")
    assert main is not None
    collect: Final = document.find_all
    changed = False

    def clear(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            main.set_inner_html("<p id='new'>x</p>" * count)

    # Empty lists exhaust CPython's freelist so result allocation can trigger collection.
    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(clear)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = collect("p", limit=32)
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(clear)
        gc.set_threshold(*thresholds)
        gc.enable()
        reserve.clear()
    assert (changed, [element.attrs["id"] for element in result], main.inner_html) == (
        True,
        expected,
        '<p id="new">x</p>' * count,
    )


if TYPE_CHECKING:
    from collections.abc import Callable


def test_find_all_attribute_filters_survive_mapping_changes() -> None:
    document: Final = parse('<p data-a="a" data-b="b">keep</p><p data-a="c" data-b="d">keep</p>')
    calls: Final[list[str | None]] = []
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}

    def first(value: str | None) -> bool:
        calls.append(value)
        attrs.clear()
        return True

    attrs.update({"data-a": first, "data-b": lambda value: calls.append(value) or True})
    finalize(attrs["data-b"], calls.append, "released")
    matches = document.find_all("p", attrs=attrs)
    gc.collect()
    assert ([node.text for node in matches], calls) == (["keep"] * 2, ["a", "b", "c", "d", "released"])


@pytest.mark.parametrize(
    ("text", "expected_calls"),
    [
        pytest.param("keep", ["a", "b", "c", "d"], id="literal-text"),
        pytest.param(lambda value: value == "keep", ["a", "b", "c", "d"], id="callable-text"),
    ],
)
def test_find_all_text_filter_survives_mapping_changes(
    text: str | Callable[[str | None], bool], expected_calls: list[str]
) -> None:
    document: Final = parse('<p data-a="a" data-b="b">keep</p><p data-a="c" data-b="d">keep</p>')
    calls: Final[list[str | None]] = []
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}

    def first(value: str | None) -> bool:
        calls.append(value)
        attrs.clear()
        return True

    attrs.update({"data-a": first, "data-b": lambda value: calls.append(value) or True})
    finalize(attrs["data-b"], calls.append, "released")
    matches = document.find_all("p", attrs=attrs, text=text)
    gc.collect()
    assert ([node.text for node in matches], calls) == (["keep"] * 2, [*expected_calls, "released"])


def test_find_attribute_filters_survive_mapping_changes() -> None:
    document: Final = parse('<p data-a="a" data-b="b">keep</p><p data-a="c" data-b="d">keep</p>')
    calls: Final[list[str | None]] = []
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}

    def first(value: str | None) -> bool:
        calls.append(value)
        attrs.clear()
        return True

    attrs.update({"data-a": first, "data-b": lambda value: calls.append(value) or True})
    finalize(attrs["data-b"], calls.append, "released")
    found: Final = _el(document.find("p", attrs=attrs))
    gc.collect()
    assert (found.text, calls) == ("keep", ["a", "b", "released"])


@pytest.mark.parametrize(
    ("text", "expected_calls"),
    [
        pytest.param("keep", ["a", "b"], id="literal-text"),
        pytest.param(lambda value: value == "keep", ["a", "b", "c", "d"], id="callable-text"),
    ],
)
def test_find_text_filter_survives_mapping_changes(
    text: str | Callable[[str | None], bool], expected_calls: list[str]
) -> None:
    document: Final = parse('<p data-a="a" data-b="b">keep</p><p data-a="c" data-b="d">keep</p>')
    calls: Final[list[str | None]] = []
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}

    def first(value: str | None) -> bool:
        calls.append(value)
        attrs.clear()
        return True

    attrs.update({"data-a": first, "data-b": lambda value: calls.append(value) or True})
    finalize(attrs["data-b"], calls.append, "released")
    found: Final = _el(document.find("p", attrs=attrs, text=text))
    gc.collect()
    assert (found.text, calls) == ("keep", [*expected_calls, "released"])


def test_find_all_filter_cleanup_preserves_result_storage() -> None:
    root: Final = _el(parse('<main><p data-x="yes">original</p></main>').find("main"))
    target: Final = Element("article")
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}
    attrs["data-x"] = lambda _: attrs.clear() or True
    finalize(attrs["data-x"], target.append, root)
    matches = root.find_all("p", attrs=attrs)
    gc.collect()
    assert ([node.text for node in matches], target.text) == (["original"], "original")


def test_find_filter_cleanup_preserves_result_storage() -> None:
    root: Final = _el(parse('<main><p data-x="yes">original</p></main>').find("main"))
    target: Final = Element("article")
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}
    attrs["data-x"] = lambda _: attrs.clear() or True
    finalize(attrs["data-x"], target.append, root)
    found: Final = _el(root.find("p", attrs=attrs))
    gc.collect()
    assert (found.text, target.text) == ("original", "original")


def test_attribute_filter_snapshot_releases_on_error() -> None:
    released: Final[list[str]] = []
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}

    def fail(_: str | None) -> bool:
        attrs.clear()
        raise LookupError

    attrs.update({"data-a": fail, "data-b": lambda _: True})
    finalize(attrs["data-b"], released.append, "released")
    with pytest.raises(LookupError):
        parse('<p data-a="a" data-b="b"></p>').find_all("p", attrs=attrs)
    gc.collect()
    assert released == ["released"]


# A candidate's text is a slice of each ancestor's, so reading every candidate from its own subtree reads a node once
# per candidate around it. Once those reads have cost several walks of the tree, a str or literal-regex search takes
# every element's verdict from one walk instead. _NEST nested elements are enough to get there.
_NEST: Final[int] = 5_000
_NEST_DOC: Final[str] = f"<r><s/>{'<li>' * _NEST}go{'</li>' * _NEST}<b>go</b><b>x<i/></b><e/></r>"
_NESTED: Final[list[str]] = ["li"] * _NEST


@pytest.mark.parametrize(
    ("axis", "text_filter", "tags"),
    [
        pytest.param(Axis.DESCENDANTS, "go", [*_NESTED, "b"], id="descendants-exact"),
        pytest.param(Axis.DESCENDANTS, re.compile(r"go"), ["r", *_NESTED, "b"], id="descendants-literal-regex"),
        pytest.param(Axis.DESCENDANTS, "", ["s", "i", "e"], id="descendants-empty-exact"),
        pytest.param(
            Axis.DESCENDANTS, re.compile(r""), ["r", "s", *_NESTED, "b", "b", "i", "e"], id="descendants-empty-regex"
        ),
        pytest.param(Axis.DESCENDANTS, "x", ["b"], id="descendants-exact-after-nest"),
        pytest.param(Axis.DESCENDANTS, "og", [], id="descendants-exact-same-length"),
        pytest.param(Axis.DESCENDANTS, re.compile(r"og"), ["r"], id="descendants-needle-across-elements"),
        pytest.param(Axis.DESCENDANTS, re.compile(r"gog"), ["r"], id="descendants-needle-overlapping-itself"),
        pytest.param(Axis.ANCESTORS, "go", _NESTED[1:], id="ancestors-exact"),
        pytest.param(Axis.ANCESTORS, re.compile(r"go"), [*_NESTED[1:], "r"], id="ancestors-literal-regex"),
        pytest.param(Axis.FOLLOWING, "go", [*_NESTED, "b"], id="following-exact"),
        pytest.param(Axis.FOLLOWING, re.compile(r"x"), ["b"], id="following-literal-regex"),
        pytest.param(Axis.PRECEDING, "go", ["b", *_NESTED], id="preceding-exact"),
        pytest.param(Axis.PRECEDING, re.compile(r"go"), ["b", *_NESTED], id="preceding-literal-regex"),
    ],
)
def test_text_of_nested_candidates(axis: Axis, text_filter: Filter, tags: list[str]) -> None:
    assert _tags(_nest_origin(axis).find_all(text=text_filter, axis=axis)) == tags


def _nest_origin(axis: Axis) -> Document | Element:
    document: Final = parse_xml(_NEST_DOC)
    _el(document.find("i")).append(Text(""))  # an empty Text adds nothing to any element's text
    if axis is Axis.ANCESTORS:
        return document.find_all("li")[-1]
    if axis is Axis.FOLLOWING:
        return _el(document.find("s"))
    if axis is Axis.PRECEDING:
        return _el(document.find("e"))
    return document


@pytest.mark.parametrize(
    "text_filter",
    [
        pytest.param("in stock", id="exact"),
        pytest.param(re.compile(r"in stock"), id="literal-regex"),
    ],
)
def test_text_of_nested_candidates_scales_linearly(text_filter: Filter, work_steps: WorkSteps) -> None:
    # each nested read walks the chain below it again, so the steps grow with its square until the text_index takes over
    work_steps.assert_linear(
        lambda depth: partial(parse_xml(_section_chain(depth)).find_all, text=text_filter), (2_000, 8_000)
    )


def _section_chain(depth: int) -> str:
    return f"<main>{'<section><hr/>' * depth}back order{'<hr/></section>' * depth}</main>"


def test_text_of_many_short_candidates_scale_linearly(work_steps: WorkSteps) -> None:
    # each candidate's first node is its text, so its read is one step and the reads never build a table
    work_steps.assert_linear(
        lambda n: partial(parse_xml(f"<main>{'<li>x</li>' * n}</main>").find_all, text="x"), (2_000, 8_000)
    )


_LATE_DOC: Final[str] = f"<r>{'<li>' * _NEST}go{'</li>' * _NEST}<b>x</b><b>x</b></r>"


@pytest.mark.parametrize(
    ("search", "tags"),
    [
        pytest.param(lambda document: [_el(document.find(text="x"))], ["b"], id="find"),
        pytest.param(lambda document: document.find_all(text="x", limit=1), ["b"], id="find-all-limit"),
        pytest.param(lambda document: document.find_all(text="x"), ["b", "b"], id="find-all"),
    ],
)
def test_text_past_nested_candidates(search: Callable[[Document], list[Element]], tags: list[str]) -> None:
    assert _tags(search(parse_xml(_LATE_DOC))) == tags


def test_text_search_past_nested_candidates_raises_from_filter() -> None:
    def fail_at_b(name: str | None) -> bool:
        if name == "b":
            raise LookupError
        return True

    with pytest.raises(LookupError):
        parse_xml(_LATE_DOC).find_all(fail_at_b, text="x")


def test_text_of_nested_candidates_moved_by_a_filter() -> None:
    document: Final = parse_xml(f"<r><m/>{'<li>' * _NEST}go{'</li>' * _NEST}<t/><u/></r>")
    present: Final = {element.tag for element in document.find_all(axis=Axis.DESCENDANTS)}
    moved: Final = _el(document.find("m"))
    target: Final = _el(document.find("t"))
    dropped: Final = _el(document.find("u"))

    def move_at_target(name: str | None) -> bool:
        if name == "t":
            target.append(moved)
            dropped.extract()
        return True

    # the result of a search whose callable edits the tree is unspecified; m is judged before the callable moves it, so
    # it stays in the result, and the search must still finish with elements the document held
    tags: Final = _tags(document.find_all(move_at_target, text=""))
    assert "m" in tags
    assert set(tags) <= present


def test_text_of_nested_ancestors_moved_by_a_filter() -> None:
    document: Final = parse_xml(_NEST_DOC)
    chain: Final = document.find_all("li")
    present: Final = {element.tag for element in document.find_all(axis=Axis.DESCENDANTS)}
    end: Final = _el(document.find("e"))
    calls: Final = itertools.count(1)

    def move_late(_name: str | None) -> bool:
        if next(calls) == _NEST - 1_000:
            end.append(chain[999])
        return True

    # the climb meets the moved element after the ancestors that were above it, so the result is unspecified here too;
    # the nearest ancestor is judged before any move, so it stays in the result
    tags: Final = _tags(chain[-1].find_all(move_late, text="go", axis=Axis.ANCESTORS))
    assert tags[0] == "li"
    assert set(tags) <= present


@pytest.mark.parametrize(
    "text_filter",
    [pytest.param("tail", id="exact"), pytest.param(re.compile(r"tail"), id="literal-regex")],
)
def test_text_of_chain_after_a_filter_adds_an_element_scales_linearly(
    text_filter: Filter, work_steps: WorkSteps
) -> None:
    # the element the filter adds is one the table does not hold, so the chain after it must be weighed again
    work_steps.assert_linear(lambda depth: _chain_after_an_added_element(depth, text_filter), (2_000, 8_000))


def _chain_after_an_added_element(depth: int, text_filter: Filter) -> Callable[[], object]:
    document: Final = parse_xml(f"<r>{'<d>' * depth}tail{'</d>' * depth}<t/>{'<e>' * depth}tail{'</e>' * depth}</r>")
    target: Final = _el(document.find("t"))

    def add(name: str | None) -> bool:
        if name == "t" and not target.children:
            target.set_inner_html("<b>tail</b>")
        return True

    return partial(document.find_all, add, text=text_filter)


@pytest.mark.parametrize(
    "text_filter",
    [pytest.param("tail", id="exact"), pytest.param(re.compile(r"tail"), id="literal-regex")],
)
def test_text_of_chain_detached_by_a_filter_scales_linearly(text_filter: Filter, work_steps: WorkSteps) -> None:
    # the callable detaches the element it adds while the search is judging it, so its own chain is read in a table
    work_steps.assert_linear(lambda depth: _chain_detached_by_a_filter(depth, text_filter), (2_000, 8_000))


def _chain_detached_by_a_filter(depth: int, text_filter: Filter) -> Callable[[], object]:
    document: Final = parse_xml(f"<r>{'<d>' * depth}tail{'</d>' * depth}<t/></r>")
    target: Final = _el(document.find("t"))

    def add_and_detach(name: str | None) -> bool:
        if name == "t" and not target.children:
            target.set_inner_html(f"<b>{'<f>' * depth}tail{'</f>' * depth}</b>")
        if name == "b":
            _el(document.find("b")).extract()
        return True

    return partial(document.find_all, add_and_detach, text=text_filter)


_ADDED_DOC: Final[str] = f"<r>{'<li>' * _NEST}go{'</li>' * _NEST}<t/></r>"


@pytest.mark.parametrize(
    ("text_filter", "markup", "limit", "tags"),
    [
        pytest.param("go", "<b>go</b><c>go</c>", None, ["r", *_NESTED, "b", "c"], id="exact"),
        pytest.param(re.compile(r"go"), "<b>go</b><c>go</c>", None, ["r", *_NESTED, "b", "c"], id="literal-regex"),
        pytest.param("go", "<b>no</b><c>go</c>", None, ["r", *_NESTED, "c"], id="exact-added-fails"),
        pytest.param("go", "<b>go</b><c>go</c>", _NEST + 2, ["r", *_NESTED, "b"], id="exact-limit-at-added"),
    ],
)
def test_text_of_candidates_added_by_a_filter_past_the_nest(
    text_filter: Filter, markup: str, limit: int | None, tags: list[str]
) -> None:
    document: Final = parse_xml(_ADDED_DOC)
    # the table holds no verdict for an element added after it was built, so that element is read from its subtree
    assert _tags(document.find_all(_add_at_target(document, markup), text=text_filter, limit=limit)) == tags


def _add_at_target(document: Document, markup: str) -> Callable[[str | None], bool]:
    target: Final = _el(document.find("t"))

    def add(name: str | None) -> bool:
        if name == "t" and not target.children:
            target.set_inner_html(markup)
        return name != "t"  # the table's verdict for t predates the edit, which leaves it unspecified

    return add


def test_first_text_match_added_by_a_filter_past_the_nest() -> None:
    document: Final = parse_xml(_ADDED_DOC)
    assert _el(document.find(_add_at_target(document, "<b>x</b>"), text="x")).tag == "b"
