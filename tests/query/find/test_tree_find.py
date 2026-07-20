"""find()/find_all(): the filter grammar (str/regex/bool/callable/list), class_, text, axes, and limit."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Axis, Element, parse

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TypeAlias

    from turbohtml import Document

    Filter: TypeAlias = "str | re.Pattern[str] | bool | Callable[[str | None], bool] | list[Filter]"

# document order of elements: html, head, body, section, h2, p, p, a
_DOC = '<section><h2 id="t">T</h2><p class="lead big">one</p><p class="big">two</p><a href="/x">l</a></section>'
_LONG_NEEDLE: Final[str] = "a" * 65 + "ba"


def _tags(elements: list[Element]) -> list[str]:
    return [element.tag for element in elements]


def _el(result: Element | None) -> Element:
    assert result is not None
    return result


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


def test_class_regex_matches_a_token_not_the_whole_value() -> None:
    # an anchored regex matches the "big" token of "lead big" but not the whole value
    doc = parse('<p class="lead big">x</p><p class="big">y</p>')
    assert len(doc.find_all("p", class_=re.compile(r"^big$"))) == 2


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


# a known-tag + attribute query rooted at the document reaches the matcher through the
# atom-index bucket, so its error path is distinct from the general descendant walk's
@pytest.mark.parametrize("query", [lambda doc: doc.find("p", id=_raise), lambda doc: doc.find_all("p", id=_raise)])
def test_indexed_tag_filter_error_propagates(query: Callable[[Document], object]) -> None:
    with pytest.raises(ZeroDivisionError):
        query(parse("<p>x</p>"))


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

_TEXT_DOC = (
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
    ],
)
def test_text_over_a_section(html: str, text_filter: Filter, tags: list[str]) -> None:
    section = _el(parse(html).find("section"))
    assert _tags(section.find_all(text=text_filter)) == tags


def test_text_matches_an_ancestor_whose_collected_text_equals_the_target() -> None:
    # collected text is the whole subtree, so a wrapper with one matching child matches too
    doc = parse("<div><p>solo</p></div>")
    assert _tags(_el(doc.find("body")).find_all(text="solo")) == ["div", "p"]


def test_text_find_returns_first_match() -> None:
    section = _el(parse(_TEXT_DOC).find("section"))
    assert _el(section.find(text=re.compile(r"\$"))).tag == "p"


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
def test_text_no_match(query: Callable[[Document], object], expected: object) -> None:
    assert query(parse(_TEXT_DOC)) == expected


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
            lambda doc: doc.find_all(re.compile(r"^b"), text="go"),
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
    doc = parse("<section><p>go</p><div><span>x</span></div></section>")
    section = _el(doc.find("section"))
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


@pytest.mark.parametrize(
    "query",
    [
        # the text predicate raises while it runs over the snapshot
        pytest.param(lambda doc: doc.find_all(text=_raise), id="text-predicate-find-all"),
        pytest.param(lambda doc: doc.find(text=_raise), id="text-predicate-find"),
        # a structural callable raises during the snapshot pass, before the text predicate runs
        pytest.param(lambda doc: doc.find_all(text="go", id=_raise), id="structural-find-all"),
        pytest.param(lambda doc: doc.find(text="go", id=_raise), id="structural-find"),
    ],
)
def test_text_callable_error_propagates(query: Callable[[Document], object]) -> None:
    with pytest.raises(ZeroDivisionError):
        query(parse(_TEXT_DOC))


# A metacharacter-free, case-sensitive regex is a plain literal: the C side searches each
# candidate's collected text for the substring directly, never building a str or calling
# back into Python. A literal regex matches a substring (unlike a plain str, which is the
# whole collected text), so "now" matches both the <p> wrapper and the <b> leaf.
@pytest.mark.parametrize(
    ("html", "text_filter", "tags"),
    [
        pytest.param(
            "<section><p>Buy <b>now</b></p></section>", re.compile(r"now"), ["p", "b"], id="literal-substring"
        ),
        # the needle is longer than every candidate's collected text, so nothing matches
        pytest.param(
            "<section><p>Buy <b>now</b></p></section>", re.compile(r"Buynow"), [], id="literal-longer-than-text"
        ),
        pytest.param("<section><p>Buy now</p></section>", re.compile(r""), ["p"], id="literal-empty"),
        # a literal can mismatch partway in before matching at a later offset
        pytest.param(
            "<section><p>no not now</p></section>", re.compile(r"now"), ["p"], id="literal-partial-then-match"
        ),
        # a non-ASCII literal stays literal (no byte is a metacharacter)
        pytest.param("<section><p>at the café</p></section>", re.compile(r"café"), ["p"], id="literal-non-ascii"),
    ],
)
def test_text_literal_regex_searches_substring(html: str, text_filter: re.Pattern[str], tags: list[str]) -> None:
    section = _el(parse(html).find("section"))
    assert _tags(section.find_all(text=text_filter)) == tags


@pytest.mark.parametrize(
    ("text", "pattern", "count"),
    [
        pytest.param("a" * 400, _LONG_NEEDLE, 0, id="overlapping-miss"),
        pytest.param("a" * 10 + _LONG_NEEDLE + "c" * 200, _LONG_NEEDLE, 1, id="direct-late-match"),
        pytest.param("a" * 200 + _LONG_NEEDLE, _LONG_NEEDLE, 1, id="fallback-late-match"),
        pytest.param("a" * 200 + "c" + "a" * 199, _LONG_NEEDLE, 0, id="late-miss"),
        pytest.param("a" * 400, "a" * 65 + "b", 0, id="prefilter-miss"),
        pytest.param("a" * 100, _LONG_NEEDLE, 0, id="short-hay-miss"),
        pytest.param("a" * 65, _LONG_NEEDLE, 0, id="needle-longer"),
        pytest.param(_LONG_NEEDLE, _LONG_NEEDLE, 1, id="exact-match"),
    ],
)
def test_text_literal_regex_long_search(text: str, pattern: str, count: int) -> None:
    section = _el(parse(f"<section><p>{text}</p></section>").find("section"))
    assert len(section.find_all(text=re.compile(pattern))) == count


def test_text_literal_regex_find_returns_first() -> None:
    section = _el(parse("<section><p>a</p><p>now</p></section>").find("section"))
    assert _el(section.find(text=re.compile(r"now"))).tag == "p"
    assert section.find(text=re.compile(r"absent")) is None


# A case-insensitive or verbose pattern is not a literal even with no metacharacters, so it
# keeps the Python search path: IGNORECASE folds case and VERBOSE drops whitespace, both of
# which a byte-for-byte C scan would get wrong.
@pytest.mark.parametrize(
    ("text_filter", "html", "tags"),
    [
        pytest.param(re.compile(r"buy", re.IGNORECASE), "<section><p>Buy now</p></section>", ["p"], id="ignorecase"),
        pytest.param(re.compile(r"a b", re.VERBOSE), "<section><p>ab</p></section>", ["p"], id="verbose"),
    ],
)
def test_text_regex_flags_keep_python_path(text_filter: re.Pattern[str], html: str, tags: list[str]) -> None:
    section = _el(parse(html).find("section"))
    assert _tags(section.find_all(text=text_filter)) == tags


def test_text_scan_descends_into_template_content() -> None:
    # a template's children live under a content fragment, which the text scan must descend into
    matches = _tags(parse("<template>inner</template>").find_all(text="inner"))
    assert "template" in matches


def test_text_scan_skips_non_text_children() -> None:
    # the gathered text concatenates only Text descendants, so a comment between them drops out
    matches = _tags(parse("<div>a<!--c-->b</div>").find_all(text="ab"))
    assert "div" in matches


def test_text_bytes_pattern_raises() -> None:
    # a bytes pattern cannot search a str value; this matches the pre-existing behavior
    with pytest.raises(TypeError):
        parse("<p>test</p>").find_all(text=re.compile(rb"test"))  # ty: ignore[invalid-argument-type]


def test_text_empty_string_matches_textless_element() -> None:
    section = _el(parse("<section><br></section>").find("section"))
    assert _tags(section.find_all(text="")) == ["br"]


def test_text_equal_length_different_content_does_not_match() -> None:
    # both are three code points, so the length gate passes and the content compare decides
    assert parse("<p>abc</p>").find_all(text="xyz") == []


# A non-literal regex keeps the Python path that snapshots candidates under the lock, where the
# structural filters compose the same way: the C prefilter skips by plain tag/class, then
# node_matches re-checks (and can reject) before the regex runs over the gathered text.
@pytest.mark.parametrize(
    ("html", "query", "tags"),
    [
        pytest.param(
            "<button>go9</button><p>go9</p>",
            lambda doc: doc.find_all("button", text=re.compile(r"go\d")),
            ["button"],
            id="plain-tag-prefilter",
        ),
        pytest.param(
            '<button class="buy">go9</button><button>go9</button>',
            lambda doc: doc.find_all(class_="buy", text=re.compile(r"go\d")),
            ["button"],
            id="plain-class-prefilter",
        ),
        pytest.param(
            "<button>go9</button><p>go9</p>",
            lambda doc: doc.find_all(re.compile(r"^button$"), text=re.compile(r"go\d")),
            ["button"],
            id="regex-tag-rechecked",
        ),
        # a custom-element tag has no known atom, so the prefilter defers to node_matches
        pytest.param(
            "<my-widget>go9</my-widget><my-widget>stop</my-widget>",
            lambda doc: doc.find_all("my-widget", text=re.compile(r"go\d")),
            ["my-widget"],
            id="custom-element-tag-prefilter",
        ),
        # a known tag absent from the tree leaves zero candidates before any text is gathered
        pytest.param(
            "<p>go9</p>",
            lambda doc: doc.find_all("table", text=re.compile(r"go\d")),
            [],
            id="no-candidate",
        ),
    ],
)
def test_text_python_path_composes_with_structural_filter(
    html: str, query: Callable[[Document], list[Element]], tags: list[str]
) -> None:
    assert _tags(query(parse(html))) == tags


def test_text_python_path_limit_caps_results() -> None:
    # a non-literal regex exercises the snapshot path's own result cap
    matches = parse("<p>go9</p><p>go9</p><p>go9</p>").find_all(text=re.compile(r"go\d"), limit=2)
    assert len(matches) == 2


@pytest.mark.parametrize(
    "query",
    [
        # a structural callable raises during the snapshot pass, alongside a Python-path regex
        pytest.param(lambda doc: doc.find_all(text=re.compile(r"g\w"), id=_raise), id="structural-find-all-regex"),
        pytest.param(lambda doc: doc.find(text=re.compile(r"g\w"), id=_raise), id="structural-find-regex"),
    ],
)
def test_text_python_path_structural_error_propagates(query: Callable[[Document], object]) -> None:
    with pytest.raises(ZeroDivisionError):
        query(parse(_TEXT_DOC))
