from __future__ import annotations

import time
from typing import Final

import pytest

from turbohtml import RdfaItem, parse

_OUTER: Final[str] = "http://outer.example/"
_INNER: Final[str] = "http://inner.example/"


@pytest.mark.parametrize(
    "declaring",
    [
        pytest.param(f'<span prefix="ex: {_INNER}" property="ex:a">1</span>', id="leaf"),
        pytest.param(f'<div prefix="ex: {_INNER}"><span property="ex:a">1</span></div>', id="walked-subtree"),
        pytest.param(
            f'<span prefix="ex: http://first.example/ ex: {_INNER}" property="ex:a">1</span>',
            id="declared-twice-in-one-attribute",
        ),
        pytest.param(
            f'<div prefix="ex: http://middle.example/"><span prefix="ex: {_INNER}" property="ex:a">1</span></div>',
            id="nested-redeclaration",
        ),
    ],
)
def test_rdfa_prefix_ends_with_its_element(declaring: str) -> None:
    html = f'<div typeof="schema:Thing">{declaring}<span property="ex:b">2</span></div>'
    assert parse(html).rdfa() == [_thing({f"{_INNER}a": ["1"], "ex:b": ["2"]})]


def _thing(properties: dict[str, list[str | RdfaItem]]) -> RdfaItem:
    return RdfaItem(vocab=None, type=["http://schema.org/Thing"], resource=None, properties=properties)


@pytest.mark.parametrize(
    "declaring",
    [
        pytest.param(f'<span prefix="ex: {_INNER}" property="ex:a">1</span>', id="leaf"),
        pytest.param(f'<div prefix="ex: {_INNER}"><span property="ex:a">1</span></div>', id="walked-subtree"),
        pytest.param(
            f'<span prefix="ex: http://first.example/ ex: {_INNER}" property="ex:a">1</span>',
            id="declared-twice-in-one-attribute",
        ),
    ],
)
def test_rdfa_prefix_redeclaration_ends_with_its_element(declaring: str) -> None:
    html = f'<div prefix="ex: {_OUTER}" typeof="schema:Thing">{declaring}<span property="ex:b">2</span></div>'
    assert parse(html).rdfa() == [_thing({f"{_INNER}a": ["1"], f"{_OUTER}b": ["2"]})]


def test_rdfa_initial_context_prefix_returns_after_redeclaring_element() -> None:
    html = (
        '<div typeof="schema:Thing">'
        f'<span prefix="schema: {_INNER}" property="schema:a">1</span><span property="schema:b">2</span></div>'
    )
    assert parse(html).rdfa() == [_thing({f"{_INNER}a": ["1"], "http://schema.org/b": ["2"]})]


def test_rdfa_relative_prefix_returns_after_redeclaring_element() -> None:
    html = (
        '<div prefix="ex: outer/" typeof="schema:Thing">'
        '<span prefix="ex: inner/" property="ex:a">1</span><span property="ex:b">2</span></div>'
    )
    assert parse(html).rdfa(base_url="http://base.example/") == [
        _thing({"http://base.example/inner/a": ["1"], "http://base.example/outer/b": ["2"]}),
    ]


def test_rdfa_prefix_of_nested_item_ends_with_it() -> None:
    html = (
        f'<div prefix="ex: {_OUTER}" typeof="schema:Thing">'
        f'<div prefix="ex: {_INNER} in: {_INNER}" property="ex:child" typeof="ex:Child">'
        '<span property="in:a">1</span></div>'
        '<span property="ex:b in:b">2</span></div>'
    )
    child: Final = RdfaItem(vocab=None, type=[f"{_INNER}Child"], resource=None, properties={f"{_INNER}a": ["1"]})
    assert parse(html).rdfa() == [_thing({f"{_INNER}child": [child], f"{_OUTER}b": ["2"], "in:b": ["2"]})]


def test_rdfa_prefix_of_top_level_item_ends_with_it() -> None:
    html = (
        f'<div prefix="ex: {_INNER}" typeof="ex:First"></div><div typeof="ex:Second"></div>'
        f'<section prefix="ex: {_OUTER}"><div typeof="ex:Third"></div></section><div typeof="ex:Fourth"></div>'
    )
    assert [item.type for item in parse(html).rdfa()] == [
        [f"{_INNER}First"],
        ["ex:Second"],
        [f"{_OUTER}Third"],
        ["ex:Fourth"],
    ]


def test_rdfa_time_is_linear_in_elements_that_declare_a_prefix() -> None:
    # Copying the inherited map once per declaring element would cost 1.4e10 pair copies here, several times the budget
    # even on an optimized build; merging and taking back one pair per element takes milliseconds.
    count: Final = 120000
    inherited: Final = " ".join(f"a{index:x}: u" for index in range(count))
    declaring: Final = "<p prefix='z: u'>" * count
    document: Final = parse(f"<div typeof=A prefix='{inherited}'>{declaring}")
    start = time.perf_counter()
    items = document.rdfa()
    assert time.perf_counter() - start < 2.0
    assert items == [RdfaItem(vocab=None, type=["A"], resource=None, properties={})]
