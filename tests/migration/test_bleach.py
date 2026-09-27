from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import pytest

from turbohtml._html import _bleach_attributes
from turbohtml.clean import Policy, sanitize
from turbohtml.migration.bleach import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS, attribute_policy, clean

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        pytest.param({"a": ["href"], "*": lambda *_: False}, '<a href="/x">text</a>', id="listed-tag"),
        pytest.param({"a": lambda *_: False, "*": ["title"]}, '<a title="t">text</a>', id="listed-wildcard"),
        pytest.param(
            {"a": lambda _tag, name, _value: name == "href", "*": lambda _tag, name, _value: name == "title"},
            '<a href="/x" title="t">text</a>',
            id="combined-predicates",
        ),
        pytest.param(
            {"a": [], "*": lambda _tag, name, _value: name == "title"}, '<a title="t">text</a>', id="empty-tag"
        ),
        pytest.param({"a": lambda *_: False}, "<a>text</a>", id="no-wildcard"),
        pytest.param({"*": lambda _tag, name, _value: name == "href"}, '<a href="/x">text</a>', id="no-tag"),
        pytest.param({"a": ["href", "title"]}, '<a href="/x" title="t">text</a>', id="no-predicates"),
    ],
)
def test_attribute_policy_combines_tag_and_wildcard_rules(
    rules: Mapping[str, Iterable[str] | Callable[[str, str, str], bool]], expected: str
) -> None:
    names, attribute_filter = attribute_policy(rules)
    assert (
        sanitize('<a href="/x" title="t" rel="r">text</a>', Policy(attributes=names, attribute_filter=attribute_filter))
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
    names, judge = _bleach_attributes(lambda _tag, name, _value: name == "href", Mapping)
    assert names == {"*": frozenset({"*"})}
    assert judge is not None
    assert (judge("a", "href", "/x"), judge("a", "title", "t")) == ("/x", None)


def test_a_mapping_lists_names_per_tag_and_binds_its_callables() -> None:
    names, judge = _bleach_attributes({"a": lambda _tag, name, _value: name == "href", "b": ["data-z"]}, Mapping)
    assert names == {"a": frozenset({"*"}), "b": frozenset({"data-z"})}
    assert judge is not None
    assert (judge("a", "href", "/x"), judge("a", "rel", "r"), judge("b", "data-z", "1")) == ("/x", None, "1")


def test_a_wildcard_callable_is_the_fallback_for_other_tags() -> None:
    _, judge = _bleach_attributes({"*": lambda _tag, name, _value: name == "title", "a": lambda *_: True}, Mapping)
    assert judge is not None
    assert (judge("a", "x", "1"), judge("p", "title", "t"), judge("p", "x", "1")) == ("1", "t", None)


def test_a_mapping_without_callables_binds_no_filter() -> None:
    assert _bleach_attributes({"a": ["href"]}, Mapping)[1] is None


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


def test_the_filter_takes_three_arguments() -> None:
    _, bound = _bleach_attributes(lambda *_: True, Mapping)
    assert bound is not None
    with pytest.raises(TypeError):
        bound("a", "href")  # ty: ignore[missing-argument]  # the arity check is the point


@pytest.mark.parametrize(
    "attributes",
    [pytest.param(5, id="not-iterable"), pytest.param({"a": 5}, id="a-tag-listing-a-non-iterable")],
)
def test_a_shape_that_lists_nothing_is_rejected(attributes: object) -> None:
    with pytest.raises(TypeError):
        _bleach_attributes(attributes, Mapping)


def test_the_entry_takes_two_arguments() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes(["href"])  # ty: ignore[missing-argument]  # the arity check is the point


def test_the_shape_test_must_be_a_type() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes({"a": ["href"]}, 5)  # ty: ignore[invalid-argument-type]  # the argument check is the point
