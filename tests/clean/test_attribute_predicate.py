from __future__ import annotations

from typing import Final, cast

import pytest

from turbohtml import Element, parse_fragment
from turbohtml.clean import Policy, Removed, Transform, sanitize, sanitize_node, sanitize_report


@pytest.mark.parametrize("keep", [pytest.param(False, id="reject"), pytest.param(True, id="keep")])
def test_predicate_selects_attributes(*, keep: bool) -> None:
    policy: Final = Policy(attribute_predicate=lambda _tag, name, _value: keep or name != "title")
    assert sanitize('<a title="first" href="https://example.com">x</a>', policy) == (
        '<a title="first" href="https://example.com">x</a>' if keep else '<a href="https://example.com">x</a>'
    )


def test_predicate_reads_original_attributes_once_in_source_order() -> None:
    seen: Final[list[tuple[str, str, str]]] = []

    def predicate(tag: str, name: str, value: str) -> bool:
        seen.append((tag, name, value))
        return True

    sanitize(
        '<a title="first" href="javascript:bad()" onclick="bad()" style="color: red; position: fixed" data-extra>x</a>',
        Policy(attributes={"a": frozenset({"*"})}, attribute_predicate=predicate),
    )
    assert seen == [
        ("a", "title", "first"),
        ("a", "href", "javascript:bad()"),
        ("a", "onclick", "bad()"),
        ("a", "style", "color: red; position: fixed"),
        ("a", "data-extra", ""),
    ]


@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param('href="javascript:bad()"', id="url"),
        pytest.param('onclick="bad()"', id="event"),
        pytest.param('style="position: fixed"', id="style"),
        pytest.param('data-extra="x"', id="unlisted"),
    ],
)
def test_predicate_cannot_bypass_safety_or_allowlists(attribute: str) -> None:
    policy: Final = Policy(
        attributes={"a": frozenset({"href", "onclick", "style"})}, attribute_predicate=lambda *_: True
    )
    assert sanitize(f"<a {attribute}>x</a>", policy) == "<a>x</a>"


@pytest.mark.parametrize("attribute", [pytest.param("title", id="safe"), pytest.param("onclick", id="unsafe")])
def test_predicate_exception_propagates(attribute: str) -> None:
    def predicate(_tag: str, name: str, _value: str) -> bool:
        raise ValueError(name)

    with pytest.raises(ValueError, match=attribute):
        sanitize(f'<a {attribute}="bad()">x</a>', Policy(attribute_predicate=predicate))


def test_predicate_truth_exception_propagates() -> None:
    verdict: Final = memoryview(b"value")
    verdict.release()
    with pytest.raises(ValueError, match="released memoryview"):
        sanitize('<a title="x">x</a>', Policy(attribute_predicate=lambda *_: cast("bool", verdict)))


@pytest.mark.parametrize("keep", [pytest.param(False, id="reject"), pytest.param(True, id="keep")])
def test_predicate_controls_which_attributes_reach_rewrite_filter(*, keep: bool) -> None:
    seen: Final[list[str]] = []

    def rewrite(_tag: str, name: str, value: str) -> str:
        seen.append(name)
        return value

    assert (
        sanitize('<a title="x">x</a>', Policy(attribute_predicate=lambda *_: keep, attribute_filter=rewrite)),
        seen,
    ) == (('<a title="x">x</a>', ["title"]) if keep else ("<a>x</a>", []))


def test_rewrite_filter_still_receives_scrubbed_values() -> None:
    seen: Final[list[tuple[str, str]]] = []

    def predicate(_tag: str, _name: str, value: str) -> bool:
        seen.append(("predicate", value))
        return True

    def rewrite(_tag: str, _name: str, value: str) -> str:
        seen.append(("filter", value))
        return value

    sanitize(
        '<a style="color: red; position: fixed">x</a>',
        Policy(attributes={"a": frozenset({"style"})}, attribute_predicate=predicate, attribute_filter=rewrite),
    )
    assert seen == [("predicate", "color: red; position: fixed"), ("filter", "color: red")]


def test_predicate_does_not_bypass_rewritten_value_safety() -> None:
    assert (
        sanitize(
            '<a href="https://example.com">x</a>',
            Policy(attribute_predicate=lambda *_: True, attribute_filter=lambda *_: "javascript:bad()"),
        )
        == "<a>x</a>"
    )


def test_predicate_rejections_are_reported() -> None:
    assert sanitize_report(
        '<a title="x" href="https://example.com">x</a>', Policy(attribute_predicate=lambda *_: False)
    ) == (
        "<a>x</a>",
        [Removed("a", "title"), Removed("a", "href")],
    )


@pytest.mark.parametrize("count", [pytest.param(31, id="small"), pytest.param(64, id="compacted")])
def test_predicate_keeps_order_across_attribute_compaction(count: int) -> None:
    seen: Final[list[str]] = []

    def predicate(_tag: str, name: str, _value: str) -> bool:
        seen.append(name)
        return name != "onclick"

    source: Final = " ".join(f'data-{index}="{index}"' for index in range(count))
    policy: Final = Policy(attributes={"a": frozenset({"*"})}, attribute_predicate=predicate)
    assert (sanitize(f'<a onclick="bad()" {source}>x</a>', policy), seen) == (
        f"<a {source}>x</a>",
        ["onclick", *(f"data-{index}" for index in range(count))],
    )


def test_predicate_does_not_filter_forced_attributes() -> None:
    assert (
        sanitize(
            '<a title="source">x</a>',
            Policy(attribute_predicate=lambda *_: False, set_attributes={"a": {"title": "forced"}}),
        )
        == '<a title="forced">x</a>'
    )


def test_predicate_filters_transform_attributes() -> None:
    assert (
        sanitize(
            "<span>x</span>",
            Policy(transform_tags={"span": Transform("a", {"title": "added"})}, attribute_predicate=lambda *_: False),
        )
        == "<a>x</a>"
    )


def test_predicate_source_mutations_do_not_change_the_copy() -> None:
    source: Final = parse_fragment('<a title="first" href="https://example.com">x</a>')
    destination: Final = Element("div")

    def predicate(_tag: str, _name: str, _value: str) -> bool:
        source.clear()
        destination.append(source)
        return True

    assert sanitize_node(source, Policy(attribute_predicate=predicate)).inner_html == (
        '<a title="first" href="https://example.com">x</a>'
    )


def test_predicate_rejections_do_not_change_reconstructed_elements() -> None:
    seen: Final[list[tuple[str, str]]] = []

    def predicate(_tag: str, name: str, value: str) -> bool:
        seen.append((name, value))
        return len(seen) > 1

    policy: Final = Policy(
        tags=frozenset({"p", "b"}), attributes={"b": frozenset({"*"})}, attribute_predicate=predicate
    )
    assert (sanitize('<p><b title="x" lang="en">one</p>two', policy), seen) == (
        '<p><b lang="en">one</b></p><b title="x" lang="en">two</b>',
        [("title", "x"), ("lang", "en"), ("title", "x"), ("lang", "en")],
    )
