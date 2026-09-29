from __future__ import annotations

import gc
import weakref
from collections import UserDict, defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, NoReturn, cast

import pytest

from turbohtml import parse_fragment
from turbohtml._html import _bleach_attributes
from turbohtml.clean import Policy, sanitize, sanitize_node
from turbohtml.migration.bleach import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS, attribute_policy, clean

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        pytest.param({"a": ["href"], "*": lambda *_: False}, '<a href="/x">text</a>', id="listed-tag"),
        pytest.param({"a": lambda *_: False, "*": ["title"]}, "<a>text</a>", id="predicate-before-wildcard"),
        pytest.param(
            {"a": lambda _tag, name, _value: name == "href", "*": lambda _tag, name, _value: name == "title"},
            '<a href="/x">text</a>',
            id="tag-predicate",
        ),
        pytest.param(
            {"a": [], "*": lambda _tag, name, _value: name == "title"}, '<a title="t">text</a>', id="empty-tag"
        ),
        pytest.param({"a": lambda *_: False}, "<a>text</a>", id="no-wildcard"),
        pytest.param({"*": lambda _tag, name, _value: name == "href"}, '<a href="/x">text</a>', id="no-tag"),
        pytest.param({"a": ["href", "title"]}, '<a href="/x" title="t">text</a>', id="no-predicates"),
    ],
)
def test_attribute_policy_preserves_tag_and_wildcard_precedence(
    rules: Mapping[str, Iterable[str] | Callable[[str, str, str], bool]], expected: str
) -> None:
    names, attribute_predicate = attribute_policy(rules)
    assert (
        sanitize(
            '<a href="/x" title="t" rel="r">text</a>', Policy(attributes=names, attribute_predicate=attribute_predicate)
        )
        == expected
    )


def test_attribute_policy_skips_wildcard_after_tag_match() -> None:
    def unexpected(_tag: str, _name: str, _value: str) -> bool:
        raise AssertionError

    assert clean('<a href="/x">text</a>', attributes={"a": ["href"], "*": unexpected}) == '<a href="/x">text</a>'


@pytest.mark.parametrize(
    "rules",
    [pytest.param(["*"], id="flat"), pytest.param({"a": ["*"]}, id="tag"), pytest.param({"*": ["*"]}, id="wildcard")],
)
def test_attribute_star_is_literal(rules: list[str] | dict[str, list[str]]) -> None:
    assert clean('<a href="/x" title="t" *="literal">text</a>', attributes=rules) == '<a *="literal">text</a>'


def test_clean_defaults_match_bleach() -> None:
    assert clean("<a href='http://x'>ok</a> <script>bad()</script>") == (
        '<a href="http://x">ok</a> &lt;script&gt;bad()&lt;/script&gt;'
    )


def test_clean_drops_javascript_url() -> None:
    assert clean("<a href='javascript:alert(1)'>x</a>") == "<a>x</a>"


def test_clean_custom_tags() -> None:
    assert clean("<b>keep</b><i>drop</i>", tags=["b"]) == "<b>keep</b>&lt;i&gt;drop&lt;/i&gt;"


def test_clean_attributes_as_list() -> None:
    assert clean('<b class="c" id="i">x</b>', tags=["b"], attributes=["class"]) == '<b class="c">x</b>'


def test_clean_attributes_as_dict() -> None:
    out = clean('<a href="http://x" title="t" rel="r">y</a>', tags=["a"], attributes={"a": ["href", "title"]})
    assert out == '<a href="http://x" title="t">y</a>'


def test_clean_attributes_as_callable() -> None:
    keep_data = clean(
        '<b data-x="1" id="i">y</b>', tags=["b"], attributes=lambda _tag, name, _value: name.startswith("data-")
    )
    assert keep_data == '<b data-x="1">y</b>'


def test_clean_attributes_as_per_tag_callable() -> None:
    out = clean(
        '<a href="http://x" data-z="1">y</a><b data-z="1">z</b>',
        tags=["a", "b"],
        attributes={"a": lambda _tag, name, _value: name == "href", "b": ["data-z"]},
    )
    assert out == '<a href="http://x">y</a><b data-z="1">z</b>'


def test_clean_attributes_as_wildcard_callable() -> None:
    # a "*" callable applies to every tag; it must drop the disallowed attribute, not fail open
    out = clean(
        '<a href="/foo" title="t">x</a>', tags=["a"], attributes={"*": lambda _tag, name, _val: name == "title"}
    )
    assert out == '<a title="t">x</a>'


def test_clean_protocols() -> None:
    assert clean('<a href="ftp://x">y</a>', tags=["a"], attributes={"a": ["href"]}, protocols=["ftp"]) == (
        '<a href="ftp://x">y</a>'
    )


def test_clean_protocols_cannot_admit_javascript() -> None:
    html = '<a href="javascript:alert(1)">y</a>'
    assert clean(html, tags=["a"], attributes={"a": ["href"]}, protocols=["javascript"]) == "<a>y</a>"


def test_clean_strip_true_unwraps() -> None:
    assert clean("<div><b>x</b></div>", strip=True) == "<b>x</b>"


def test_clean_strip_false_escapes() -> None:
    assert clean("<div><b>x</b></div>", strip=False) == "&lt;div&gt;<b>x</b>&lt;/div&gt;"


def test_clean_strip_comments_default() -> None:
    assert clean("a<!-- c -->b") == "ab"


def test_clean_keep_comments() -> None:
    assert clean("a<!-- c -->b", strip_comments=False) == "a<!-- c -->b"


def test_clean_css_sanitizer_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="css_sanitizer"):
        clean("<p>x</p>", css_sanitizer=object())


def test_exported_constants() -> None:
    assert "a" in ALLOWED_TAGS
    assert ALLOWED_ATTRIBUTES["a"] == frozenset({"href", "title"})
    assert frozenset({"http", "https", "mailto"}) == ALLOWED_PROTOCOLS


def test_a_flat_list_admits_its_names_on_every_tag() -> None:
    assert _bleach_attributes(["href", "title"], Mapping) == ({"*": frozenset({"href", "title"})}, None)


def test_a_callable_admits_every_name_and_judges_each_value() -> None:
    names, judge = attribute_policy(lambda _tag, name, _value: name == "href")
    assert names == {"*": frozenset({"*"})}
    assert judge is not None
    assert (judge("a", "href", "/x"), judge("a", "title", "t")) == (True, False)


def test_per_tag_callable_leaves_other_tag_lists_intact() -> None:
    assert (
        clean(
            '<a href="/x" rel="r">x</a><b data-z="1">y</b>',
            attributes={"a": lambda _tag, name, _value: name == "href", "b": ["data-z"]},
        )
        == '<a href="/x">x</a><b data-z="1">y</b>'
    )


def test_a_wildcard_callable_is_the_fallback_for_other_tags() -> None:
    _, judge = attribute_policy({"*": lambda _tag, name, _value: name == "title", "a": lambda *_: True})
    assert judge is not None
    assert (judge("a", "x", "1"), judge("p", "title", "t"), judge("p", "x", "1")) == (True, True, False)


def test_a_mapping_without_callables_binds_no_predicate() -> None:
    assert attribute_policy({"a": ["href"]})[1] is None


def test_a_predicate_error_propagates() -> None:
    def judge(_tag: str, _name: str, _value: str) -> bool:
        msg = "boom"
        raise RuntimeError(msg)

    _, bound = _bleach_attributes(judge, Mapping)
    assert bound is not None
    with pytest.raises(RuntimeError, match="boom"):
        bound("a", "href", "/x")


class _Undecided:
    """A verdict whose truth test raises, the way a lazy proxy might."""

    def __bool__(self) -> bool:
        msg = "undecided"
        raise RuntimeError(msg)


def test_a_verdict_that_cannot_be_judged_propagates() -> None:
    _, bound = _bleach_attributes(lambda *_: _Undecided(), Mapping)
    assert bound is not None
    with pytest.raises(RuntimeError, match="undecided"):
        bound("a", "href", "/x")


def test_the_predicate_takes_three_arguments() -> None:
    _, bound = _bleach_attributes(lambda *_: True, Mapping)
    assert bound is not None
    with pytest.raises(TypeError):
        bound("a", "href")  # ty: ignore[missing-argument]  # the arity check is the point


@pytest.mark.parametrize(
    "attributes",
    [
        pytest.param(5, id="not-iterable"),
        pytest.param({"a": 5}, id="a-tag-listing-a-non-iterable"),
        pytest.param({"a": [["href"]]}, id="unhashable-attribute-name"),
    ],
)
def test_a_shape_that_lists_nothing_is_rejected(attributes: object) -> None:
    with pytest.raises(TypeError):
        clean('<a href="/x">x</a>', attributes=cast("list[str]", attributes))


def test_the_entry_takes_two_arguments() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes(["href"])  # ty: ignore[missing-argument]  # the arity check is the point


def test_the_shape_test_must_be_a_type() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes({"a": ["href"]}, 5)  # ty: ignore[invalid-argument-type]  # the argument check is the point


@pytest.mark.parametrize("callable_fallback", [pytest.param(False, id="listed"), pytest.param(True, id="callable")])
@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        pytest.param(False, "<a>x</a>", id="false"),
        pytest.param(True, '<a title="t">x</a>', id="true"),
        pytest.param(0, "<a>x</a>", id="zero"),
        pytest.param(1, '<a title="t">x</a>', id="one"),
        pytest.param(None, "<a>x</a>", id="none"),
        pytest.param("", "<a>x</a>", id="empty-string"),
        pytest.param("keep", '<a title="t">x</a>', id="nonempty-string"),
    ],
)
def test_tag_predicate_overrides_wildcard(
    expected: str, *, verdict: bool | int | str | None, callable_fallback: bool
) -> None:
    assert (
        clean(
            '<a title="t">x</a><b title="t">y</b>',
            attributes={
                "a": lambda *_: cast("bool", verdict),
                "*": (lambda *_: True) if callable_fallback else ["title"],
            },
        )
        == expected + '<b title="t">y</b>'
    )


def test_tag_predicate_overrides_literal_star() -> None:
    assert clean('<a *="literal">x</a><b *="literal">y</b>', attributes={"a": lambda *_: False, "*": ["*"]}) == (
        '<a>x</a><b *="literal">y</b>'
    )


@pytest.mark.parametrize("tag", [pytest.param("a", id="named"), pytest.param("*", id="star")])
def test_tag_predicate_runs_once(tag: str) -> None:
    calls: Final[list[tuple[str, str, str]]] = []

    def reject(tag: str, name: str, value: str) -> bool:
        calls.append((tag, name, value))
        return False

    _, predicate = attribute_policy({tag: reject})
    assert predicate is not None
    assert (predicate(tag, "title", "t"), calls) == (False, [(tag, "title", "t")])


def test_tag_predicate_error_precedes_wildcard() -> None:
    def reject(_tag: str, _name: str, _value: str) -> bool:
        msg = "tag rejected"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="tag rejected"):
        clean('<a title="t">x</a>', attributes={"a": reject, "*": ["title"]})


def test_tag_predicate_truth_error_precedes_wildcard() -> None:
    verdict: Final = weakref.proxy(set())
    gc.collect()
    with pytest.raises(ReferenceError, match="no longer exists"):
        clean('<a title="t">x</a>', attributes={"a": lambda *_: cast("bool", verdict), "*": ["title"]})


def test_tag_predicate_can_sanitize_with_another_policy() -> None:
    def keep_href(_tag: str, name: str, _value: str) -> bool:
        return name == "href" and clean('<b title="t">x</b>', attributes={"b": lambda *_: False, "*": ["title"]}) == (
            "<b>x</b>"
        )

    assert clean('<a href="/x" title="t">x</a>', attributes={"a": keep_href, "*": ["title"]}) == '<a href="/x">x</a>'


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        pytest.param("href", "javascript:bad()", '<a title="t">x</a>', id="unsafe-url"),
        pytest.param("onclick", "bad()", '<a title="t">x</a>', id="event-handler"),
        pytest.param("style", "color: red; position: fixed", '<a style="color: red" title="t">x</a>', id="css"),
    ],
)
def test_predicate_sees_source_values_before_safety(name: str, value: str, expected: str) -> None:
    calls: Final[list[tuple[str, str, str]]] = []

    def keep(tag: str, attribute: str, source: str) -> bool:
        calls.append((tag, attribute, source))
        return True

    assert (clean(f'<a {name}="{value}" title="t">x</a>', attributes=keep), calls) == (
        expected,
        [("a", name, value), ("a", "title", "t")],
    )


def test_predicate_observes_unfiltered_css() -> None:
    names, predicate = attribute_policy(
        lambda _tag, name, value: name == "style" and value == "color: red; position: fixed"
    )
    assert (
        sanitize(
            '<a style="color: red; position: fixed">x</a>',
            Policy(attributes=names, attribute_predicate=predicate, css_properties=frozenset({"color"})),
        )
        == '<a style="color: red">x</a>'
    )


@pytest.mark.parametrize(
    ("markup", "value", "expected", "calls"),
    [
        pytest.param('<a title="&amp;">x</a>', "&amp;", '<a title="&amp;">x</a>', 1, id="named-reference"),
        pytest.param("<a title=&amp;>x</a>", "&amp;", '<a title="&amp;">x</a>', 1, id="unquoted-reference"),
        pytest.param('<a title="a&#x20;b">x</a>', "a&#x20;b", '<a title="a b">x</a>', 1, id="numeric-reference"),
        pytest.param('<a title="&#0;">x</a>', "&#0;", '<a title="�">x</a>', 1, id="null-reference"),
        pytest.param('<a title="x\r\ny">x</a>', "x\ny", '<a title="x\ny">x</a>', 1, id="newline"),
        pytest.param('<a title="x\0y">x</a>', "x�y", '<a title="x�y">x</a>', 1, id="null-character"),
        pytest.param('<a title="first" title="&amp;">x</a>', "first", '<a title="first">x</a>', 1, id="duplicate"),
        pytest.param("<a title>x</a>", "", '<a title="">x</a>', 1, id="valueless"),
        pytest.param("<a title=>x</a>", "", '<a title="">x</a>', 1, id="empty-unquoted"),
        pytest.param("<a title = >x</a>", "", '<a title="">x</a>', 1, id="empty-spaced"),
        pytest.param('<a =foo="bar">x</a>', "bar", '<a =foo="bar">x</a>', 1, id="equals-name"),
        pytest.param("<a =x>x</a>", "", '<a =x="">x</a>', 1, id="equals-name-valueless"),
        pytest.param('<a title="">x</a>', "", '<a title="">x</a>', 1, id="empty-quoted"),
        pytest.param('<a title =  "&amp;">x</a>', "&amp;", '<a title="&amp;">x</a>', 1, id="spaced-equals"),
        pytest.param(
            '<b><i title="&amp;">a</b>b</i>',
            "&amp;",
            '<b><i title="&amp;">a</i></b><i title="&amp;">b</i>',
            2,
            id="reconstructed-element",
        ),
    ],
)
def test_bleach_predicate_receives_source_references(markup: str, value: str, expected: str, calls: int) -> None:
    seen: Final[list[str]] = []

    def keep(_tag: str, _name: str, source: str) -> bool:
        seen.append(source)
        return source == value

    assert clean(markup, attributes=keep) == expected
    assert seen == [value] * calls


def test_bleach_predicate_cannot_keep_encoded_script_url() -> None:
    seen: Final[list[str]] = []

    def keep(_tag: str, _name: str, source: str) -> bool:
        seen.append(source)
        return True

    assert clean('<a href="jav&#x61;script:alert(1)">x</a>', attributes=keep) == "<a>x</a>"
    assert seen == ["jav&#x61;script:alert(1)"]


def test_bleach_predicate_retains_reconstructed_attribute_origins() -> None:
    seen: Final[list[tuple[str, str]]] = []

    def keep(_tag: str, name: str, value: str) -> bool:
        seen.append((name, value))
        return True

    assert clean('<b><i id="x" title="&amp;">a</b>b</i>', attributes=keep) == (
        '<b><i id="x" title="&amp;">a</i></b><i id="x" title="&amp;">b</i>'
    )
    assert seen == [("id", "x"), ("title", "&amp;"), ("id", "x"), ("title", "&amp;")]


def test_bleach_predicate_on_existing_tree_sees_parsed_value() -> None:
    seen: Final[list[str]] = []

    def keep(_tag: str, _name: str, value: str) -> bool:
        seen.append(value)
        return value == "&"

    names, predicate = attribute_policy(keep)
    assert sanitize_node(
        parse_fragment('<a title="&amp;">x</a>'), Policy(attributes=names, attribute_predicate=predicate)
    ).inner_html == ('<a title="&amp;">x</a>')
    assert seen == ["&"]


def test_rejected_url_predicate_changes_later_rules() -> None:
    def keep(_tag: str, _name: str, _value: str) -> bool:
        rules["b"] = ["title"]
        return True

    rules: Final[dict[str, list[str] | Callable[[str, str, str], bool]]] = {"a": keep}
    assert clean('<a href="javascript:bad()">a</a><b title="B">b</b>', attributes=rules) == '<a>a</a><b title="B">b</b>'


@pytest.mark.parametrize("replace", [pytest.param(False, id="mutate-list"), pytest.param(True, id="replace-rule")])
def test_predicate_changes_later_tag_rules(*, replace: bool) -> None:
    listed: Final[list[str]] = []

    def keep(_tag: str, _name: str, _value: str) -> bool:
        if replace:
            rules["b"] = ["title"]
        else:
            listed.append("title")
        return True

    rules: Final[dict[str, list[str] | Callable[[str, str, str], bool]]] = {"a": keep, "b": listed}
    assert clean('<a title="A">a</a><b title="B">b</b>', attributes=rules) == '<a title="A">a</a><b title="B">b</b>'


def test_predicate_adds_a_later_tag_rule() -> None:
    def keep(_tag: str, _name: str, _value: str) -> bool:
        rules["b"] = ["title"]
        return True

    rules: Final[dict[str, list[str] | Callable[[str, str, str], bool]]] = {"a": keep}
    assert clean('<a title="A">a</a><b title="B">b</b>', attributes=rules) == '<a title="A">a</a><b title="B">b</b>'


def test_predicate_removes_its_own_rule() -> None:
    def reject(_tag: str, _name: str, _value: str) -> bool:
        del rules["a"]
        return False

    rules: Final[dict[str, list[str] | Callable[[str, str, str], bool]]] = {"a": reject, "*": ["title"]}
    assert clean('<a title="A">a</a><a title="B">b</a>', attributes=rules) == '<a>a</a><a title="B">b</a>'


def test_predicate_invalidates_a_later_tag_rule() -> None:
    def keep(_tag: str, _name: str, _value: str) -> bool:
        rules["b"] = cast("list[str]", None)
        return True

    rules: Final[dict[str, list[str] | Callable[[str, str, str], bool]]] = {"a": keep}
    with pytest.raises(TypeError, match="NoneType"):
        clean('<a title="A">a</a><b title="B">b</b>', attributes=rules)


def test_string_rule_uses_substring_membership() -> None:
    assert clean('<a title="A">a</a><b title="B">b</b>', attributes={"a": "data-title", "b": "title"}) == (
        '<a title="A">a</a><b title="B">b</b>'
    )


@pytest.mark.parametrize(
    "position", [pytest.param(0, id="absent"), pytest.param(1, id="first"), pytest.param(2, id="last")]
)
def test_policy_keeps_iterable_rule_state(position: int) -> None:
    rules: dict[str, Iterable[str] | Callable[[str, str, str], bool]] = {"a": iter(["title", "href"])}
    if position == 1:
        rules = {"b": lambda *_: True, **rules}
    elif position == 2:
        rules["b"] = lambda *_: True
    assert (
        clean(
            '<a title="one" href="/x">first</a><a title="two" href="/y">second</a>',
            attributes=rules,
        )
        == '<a title="one" href="/x">first</a><a>second</a>'
    )


def test_attribute_predicate_rejects_an_unhashable_tag() -> None:
    _, predicate = attribute_policy(lambda *_: True)
    assert predicate is not None
    with pytest.raises(TypeError, match="unhashable"):
        predicate(cast("str", []), "title", "t")


def test_attribute_predicate_propagates_mapping_lookup_errors() -> None:
    with pytest.raises(LookupError, match="unavailable rule"):
        clean('<a title="t">x</a>', attributes=_UnavailableRules(a=lambda *_: True))


def test_attribute_rules_do_not_create_missing_defaults() -> None:
    rules: Final[defaultdict[str, list[str]]] = defaultdict(list, {"a": ["title"]})
    result: Final = clean('<a title="one">first</a><b title="two">second</b>', attributes=rules)
    assert (result, dict(rules)) == ('<a title="one">first</a><b>second</b>', {"a": ["title"]})


def test_attribute_rules_do_not_enumerate_custom_items() -> None:
    assert clean('<a title="one">first</a>', attributes=_UnenumerableRules(a=["title"])) == '<a title="one">first</a>'


def test_attribute_rules_use_custom_membership() -> None:
    assert clean('<a title="one">first</a><b title="two">second</b>', attributes=_VirtualRules()) == (
        '<a title="one">first</a><b>second</b>'
    )


@pytest.mark.parametrize(
    "names",
    [
        pytest.param(["title"], id="list"),
        pytest.param(("title",), id="tuple"),
        pytest.param({"title"}, id="set"),
        pytest.param(frozenset({"title"}), id="frozenset"),
    ],
)
def test_static_attribute_rule_containers(names: Iterable[str]) -> None:
    assert clean('<a title="one" href="/x">first</a>', attributes={"a": names}) == '<a title="one">first</a>'


def test_flat_attribute_iterator_keeps_membership_state() -> None:
    assert clean('<a title="one">first</a><a title="two">second</a>', attributes=iter(["title"])) == (
        '<a title="one">first</a><a>second</a>'
    )


def test_flat_attribute_string_keeps_membership_semantics() -> None:
    assert clean('<a title="one" href="/x">first</a>', attributes="data-title") == '<a title="one">first</a>'


def test_flat_attribute_sequence_uses_index_iteration() -> None:
    assert (
        clean(
            '<a title="one" href="/x">first</a><a title="two">second</a>',
            attributes=cast("Iterable[str]", _AttributeSequence()),
        )
        == '<a title="one">first</a><a title="two">second</a>'
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("java\0script:bad()", "<a>x</a>", id="nul-replacement"),
        pytest.param("java\ufffdscript:bad()", "<a>x</a>", id="replacement-character"),
        pytest.param("java\u2603script:bad()", "<a>x</a>", id="non-ascii-scheme"),
        pytest.param("http\u2603://example.org/x", '<a href="http\u2603://example.org/x">x</a>', id="allowed-scheme"),
        pytest.param("/caf\u00e9:menu", '<a href="/caf\u00e9:menu">x</a>', id="relative-path"),
        pytest.param("https[://example.org/x", '<a href="https[://example.org/x">x</a>', id="ascii-relative"),
    ],
)
def test_url_scheme_normalization_matches_bleach(value: str, expected: str) -> None:
    assert clean(f'<a href="{value}">x</a>') == expected


@pytest.mark.parametrize(
    ("protocols", "value", "keep"),
    [
        pytest.param(None, "/relative", True, id="default-relative"),
        pytest.param([], "/relative", False, id="empty-relative"),
        pytest.param([], "", False, id="empty-url"),
        pytest.param([], "#fragment", True, id="fragment"),
        pytest.param([], "#javascript:bad()", True, id="fragment-scheme-text"),
        pytest.param([], "#%zz", True, id="fragment-malformed-percent"),
        pytest.param([], "  #fragment", True, id="fragment-leading-space"),
        pytest.param([], "%zz", False, id="relative-malformed-percent"),
        pytest.param(["ftp"], "relative", False, id="ftp-relative"),
        pytest.param(["ftp"], "ftp://example.org/x", True, id="ftp-scheme"),
        pytest.param(["http"], "/relative", True, id="http-relative"),
        pytest.param(["https"], "//example.org/x", True, id="https-protocol-relative"),
        pytest.param(["https"], "http://example.org/x", False, id="http-disallowed"),
    ],
)
@pytest.mark.parametrize(
    ("tag", "attribute"), [pytest.param("a", "href", id="link"), pytest.param("img", "src", id="image")]
)
def test_protocols_control_relative_urls(
    protocols: list[str] | None, value: str, *, keep: bool, tag: str, attribute: str
) -> None:
    end = "</a>" if tag == "a" else ""
    html = f'<{tag} {attribute}="{value}">x{end}'
    assert clean(html, tags=[tag], attributes={tag: [attribute]}, protocols=protocols) == (
        html if keep else f"<{tag}>x{end}"
    )


@pytest.mark.parametrize(
    ("protocols", "value", "expected"),
    [
        pytest.param([], "&#0;#:x", None, id="invalid-numeric-before-fragment"),
        pytest.param(["ftp"], "&Tab;ftp:x", None, id="unresolved-named-before-scheme"),
        pytest.param(None, "&Tab;ftp:x", None, id="default-decoded-tab-before-scheme"),
        pytest.param(None, "&Tab;ja:x", None, id="default-decoded-tab-before-custom-scheme"),
        pytest.param(None, "&#100ata:text/html,x", None, id="default-semicolonless-numeric-scheme"),
        pytest.param(None, "&#118bscript:x", None, id="default-semicolonless-numeric-script-scheme"),
        pytest.param(None, "ft&#112:x", None, id="default-semicolonless-numeric-mid-scheme"),
        pytest.param(None, "&#104ttp://example.org/", "http://example.org/", id="default-semicolonless-allowed"),
        pytest.param(None, "&Tab;javascript:x", None, id="decoded-script-baseline"),
        pytest.param(None, "&#0;javascript:x", None, id="decoded-script-after-invalid-numeric"),
        pytest.param(None, "1é:x", None, id="digit-before-non-ascii"),
        pytest.param(None, "1http:x", None, id="digit-leading-scheme"),
        pytest.param(["1http"], "1http:x", "1http:x", id="allowed-digit-leading-scheme"),
        pytest.param(["ftp"], "&#x66;tp:x", "ftp:x", id="valid-numeric-in-scheme"),
        pytest.param(["ftp"], "&#X66;tp:x", "ftp:x", id="uppercase-hex-reference"),
        pytest.param(["ftp"], "&#102;tp:x", "ftp:x", id="decimal-reference"),
        pytest.param(["ftp"], "&copy;ftp:x", "©ftp:x", id="named-non-ascii-reference"),
        pytest.param(["ftp"], "&#x80;ftp:x", "€ftp:x", id="numeric-control-before-scheme"),
        pytest.param(["ftp"], "&#0;ftp:x", None, id="invalid-numeric-before-scheme"),
        pytest.param(["ftp"], "&#x110000;ftp:x", None, id="numeric-out-of-range"),
        pytest.param(["ftp"], "&#;ftp:x", None, id="numeric-missing-digits"),
        pytest.param(["ftp"], "&#xG;ftp:x", None, id="numeric-invalid-digit"),
        pytest.param(["ftp"], "&#102ftp:x", None, id="numeric-missing-semicolon"),
        pytest.param(["ftp"], "&#102", None, id="numeric-at-end"),
        pytest.param(["ftp"], "&#99999999999999999;ftp:x", None, id="numeric-overflow"),
        pytest.param(["ftp"], "&#xD800;ftp:x", "�ftp:x", id="numeric-surrogate"),
        pytest.param(["ftp"], "&;ftp:x", None, id="named-empty"),
        pytest.param(["ftp"], "&amp", None, id="named-at-end"),
        pytest.param(["ftp"], "&amp:ftp:x", None, id="named-missing-semicolon"),
        pytest.param(["ftp"], "&", None, id="ampersand-at-end"),
        pytest.param(["ftp"], "&x", None, id="short-unresolved-name"),
        pytest.param(["ftp"], "&#", None, id="numeric-prefix-at-end"),
        pytest.param(["ftp"], "&#1", None, id="short-numeric-reference"),
        pytest.param(["ftp"], "&1;ftp:x", None, id="numeric-named-reference"),
        pytest.param(["ftp"], "&A1;ftp:x", None, id="uppercase-digit-name"),
        pytest.param(["ftp"], "&a1;ftp:x", None, id="lowercase-digit-name"),
        pytest.param(["ftp"], "&frac12;ftp:x", "½ftp:x", id="named-reference-with-digit"),
        pytest.param(["ftp"], "&:ftp:x", None, id="punctuation-after-ampersand"),
        pytest.param(["ftp"], "&@;ftp:x", None, id="punctuation-after-digit-range"),
        pytest.param(["ftp"], "&[;ftp:x", None, id="punctuation-after-uppercase-range"),
        pytest.param(["ftp"], "&" + "a" * 35 + ";ftp:x", None, id="named-overlong"),
        pytest.param(["ftp"], "&NotEqualTilde;ftp:x", None, id="named-unresolved-reference"),
        pytest.param(None, "&#0;ftp:x", None, id="default-decoded-replacement-before-scheme"),
        pytest.param(["ftp"], "&amp;ftp:x", None, id="named-ampersand-before-scheme"),
        pytest.param(None, "&amp;ftp:x", "&amp;ftp:x", id="default-named-ampersand-relative"),
        pytest.param(None, "`//bad[host", None, id="backtick-before-invalid-authority"),
        pytest.param(None, "é//bad[host", None, id="unicode-before-invalid-authority"),
        pytest.param(None, "&#x80;//bad[host", None, id="reference-before-invalid-authority"),
        pytest.param(None, ":x", ":x", id="colon-before-scheme"),
        pytest.param(["f1tp"], "f1tp:x", "f1tp:x", id="scheme-with-digit"),
        pytest.param(["ftp2"], "éftp2:x", "éftp2:x", id="non-ascii-before-scheme-digit"),
        pytest.param(["abcdefghijkl"], "éabcdefghijkl:x", "éabcdefghijkl:x", id="long-obfuscated-scheme"),
        pytest.param(["abcdefghijkl"], "abcdefghijkl:x", "abcdefghijkl:x", id="long-scheme"),
        pytest.param(None, "é?x:y", "é?x:y", id="non-ascii-before-query"),
        pytest.param([], "é#frag", "é#frag", id="non-ascii-before-fragment"),
        pytest.param(None, "é", "é", id="only-non-ascii-relative"),
        pytest.param(["foo/bar"], "foo/bar:x", "foo/bar:x", id="unrecognized-allowed-scheme"),
    ],
)
@pytest.mark.parametrize(
    ("tag", "attribute"), [pytest.param("a", "href", id="link"), pytest.param("img", "src", id="image")]
)
def test_bleach_url_normalization_uses_source_entities(
    protocols: list[str] | None, value: str, expected: str | None, tag: str, attribute: str
) -> None:
    end = "</a>" if tag == "a" else ""
    output = f'<{tag} {attribute}="{expected}">x{end}' if expected is not None else f"<{tag}>x{end}"
    assert (
        clean(f'<{tag} {attribute}="{value}">x{end}', tags=[tag], attributes={tag: [attribute]}, protocols=protocols)
        == output
    )


def test_bleach_url_normalization_handles_long_relative_url() -> None:
    value = "/" + "x" * 140
    assert clean(f'<a href="{value}">x</a>') == f'<a href="{value}">x</a>'


def test_bleach_url_normalization_handles_long_unicode_relative_url() -> None:
    value = "é" + "x" * 140
    assert clean(f'<a href="{value}">x</a>') == f'<a href="{value}">x</a>'


def test_bleach_url_normalization_checks_each_href_source() -> None:
    assert clean(
        '<p>&amp;</p><a title="first" href="ftp:x">safe</a><a title="second" href="&Tab;ftp:x">unsafe</a>',
        tags=["p", "a"],
        attributes={"a": ["title", "href"]},
        protocols=["ftp"],
    ) == ('<p>&amp;</p><a title="first" href="ftp:x">safe</a><a title="second">unsafe</a>')


def test_bleach_url_normalization_rejects_long_invalid_authority() -> None:
    value = "é//" + "x" * 140 + "[bad"
    assert clean(f'<a href="{value}">x</a>') == "<a>x</a>"


@pytest.mark.parametrize(
    ("protocols", "value"),
    [
        pytest.param([], "&#0;#:x", id="invalid-numeric-fragment"),
        pytest.param(["ftp"], "&Tab;ftp:x", id="unresolved-named-scheme"),
    ],
)
def test_bleach_url_normalization_preserves_reconstructed_source(protocols: list[str], value: str) -> None:
    assert (
        clean(f'<b><a href="{value}">a</b>b</a>', tags=["a", "b"], attributes={"a": ["href"]}, protocols=protocols)
        == "<b><a>a</a></b><a>b</a>"
    )


def test_custom_rule_changes_later_tag_rules() -> None:
    rules: Final[dict[str, Iterable[str]]] = {}
    rules["a"] = cast("Iterable[str]", _MutatingRule(rules))
    assert clean('<a title="one">first</a><b title="two">second</b>', attributes=rules) == (
        '<a title="one">first</a><b title="two">second</b>'
    )


class _UnavailableRules(UserDict[str, "Callable[[str, str, str], bool]"]):
    def __getitem__(self, key: str) -> Callable[[str, str, str], bool]:
        msg = "unavailable rule"
        raise LookupError(msg)


class _UnenumerableRules(UserDict[str, list[str]]):
    def items(self) -> NoReturn:
        raise AssertionError(self)


class _VirtualRules(UserDict[str, list[str]]):
    def __contains__(self, key: object) -> bool:
        return key == "a"

    def __getitem__(self, key: str) -> list[str]:
        return ["title"]


class _MutatingRule:
    def __init__(self, rules: dict[str, Iterable[str]]) -> None:
        self.rules = rules

    def __contains__(self, name: str) -> bool:
        self.rules["b"] = ["title"]
        return name == "title"


class _AttributeSequence:
    def __getitem__(self, index: int) -> str:
        return ("title",)[index]
