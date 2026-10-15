from __future__ import annotations

import pytest

from turbohtml import parse


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        pytest.param("Thing", ["https://example.org/#Thing"], id="vocab-term"),
        pytest.param("Good/Part", ["https://example.org/#Good/Part"], id="term-slash"),
        pytest.param("Événement", ["https://example.org/#Événement"], id="unicode-start"),
        pytest.param("état", ["https://example.org/#état"], id="unicode-lowercase"),
        pytest.param("1Bad", [], id="digit-start"),
        pytest.param("/Bad", [], id="slash-start"),
        pytest.param("Bad[Part]", [], id="non-name-character"),
        pytest.param("[schema:Thing]", [], id="safe-curie-disallowed"),
    ],
)
def test_rdfa_typeof_token(token: str, expected: list[str]) -> None:
    assert parse(f'<div vocab="https://example.org/#" typeof="{token}"></div>').rdfa()[0].type == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        pytest.param("name/part", {"https://example.org/#name/part": ["x"]}, id="term-slash"),
        pytest.param("état", {"https://example.org/#état": ["x"]}, id="unicode"),
        pytest.param("1name", {}, id="digit-start"),
        pytest.param("/name", {}, id="slash-start"),
        pytest.param("name[part]", {}, id="non-name-character"),
        pytest.param("[schema:name]", {}, id="safe-curie-disallowed"),
    ],
)
def test_rdfa_property_token(token: str, expected: dict[str, list[str]]) -> None:
    html = f'<div vocab="https://example.org/#" typeof="Thing"><span property="{token}">x</span></div>'
    assert parse(html).rdfa()[0].properties == expected


def test_rdfa_invalid_token_does_not_suppress_valid_property() -> None:
    html = '<div typeof="schema:Thing"><span property="1bad schema:name">x</span></div>'
    assert parse(html).rdfa()[0].properties == {"http://schema.org/name": ["x"]}


def test_rdfa_invalid_term_without_vocab_is_ignored() -> None:
    assert parse('<div typeof="1Bad"></div>').rdfa()[0].type == []
