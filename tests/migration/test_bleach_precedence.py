from __future__ import annotations

import gc
import weakref
from collections import UserDict, defaultdict
from typing import TYPE_CHECKING, Final, NoReturn, cast

import pytest

from turbohtml import parse_fragment
from turbohtml.clean import Policy, sanitize, sanitize_node
from turbohtml.migration.bleach import attribute_policy, clean

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


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
