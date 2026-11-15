from __future__ import annotations

from typing import Final

import pytest

from turbohtml import RdfaItem, parse

_LIMIT: Final[int] = 256
_BASE: Final[str] = "https://example.org/dir/page"
_HEAD: Final[str] = "http://vocab.example/"
_CDN: Final[str] = "https://cdn.example/"


def test_rdfa_vocab_at_the_length_limit_expands_terms() -> None:
    vocab = _iri(_LIMIT, _HEAD)
    html = f'<div vocab="{vocab}" typeof="Person"><span property="name">Ada</span></div>'
    assert parse(html).rdfa() == [
        RdfaItem(vocab=vocab, type=[f"{vocab}Person"], resource=None, properties={f"{vocab}name": ["Ada"]}),
    ]


def _iri(length: int, head: str) -> str:
    return f"{head}{'a' * (length - len(head) - 1)}/"


def test_rdfa_vocab_past_the_length_limit_clears_the_vocabulary() -> None:
    html = (
        f'<div vocab="http://schema.org/"><div vocab="{_iri(_LIMIT + 1, _HEAD)}" typeof="Person">'
        '<span property="name license">Ada</span></div></div>'
    )
    assert parse(html).rdfa() == [
        RdfaItem(
            vocab=None,
            type=["Person"],
            resource=None,
            properties={"name": ["Ada"], "http://www.w3.org/1999/xhtml/vocab#license": ["Ada"]},
        ),
    ]


def test_rdfa_prefix_iri_at_the_length_limit_expands_curies() -> None:
    iri = _iri(_LIMIT, _HEAD)
    html = f'<div prefix="ex: {iri}" typeof="ex:Person" resource="ex:ada"><span property="ex:name">Ada</span></div>'
    assert parse(html).rdfa() == [
        RdfaItem(vocab=None, type=[f"{iri}Person"], resource=f"{iri}ada", properties={f"{iri}name": ["Ada"]}),
    ]


def test_rdfa_prefix_iri_past_the_length_limit_declares_nothing() -> None:
    html = (
        f'<div prefix="ex: {_iri(_LIMIT + 1, _HEAD)} schema: {_iri(_LIMIT + 1, _HEAD)} ok: http://ok.example/" '
        'typeof="ex:Person"><span property="ex:name schema:name ok:name">Ada</span></div>'
    )
    assert parse(html).rdfa() == [
        RdfaItem(
            vocab=None,
            type=["ex:Person"],
            resource=None,
            properties={"ex:name": ["Ada"], "http://schema.org/name": ["Ada"], "http://ok.example/name": ["Ada"]},
        ),
    ]


def test_rdfa_long_vocab_does_not_multiply_into_every_term() -> None:
    terms = [f"p{index}" for index in range(2000)]
    html = f'<div vocab="{_iri(20_000, _HEAD)}" typeof="T"><span property="{" ".join(terms)}">x</span></div>'
    assert list(parse(html).rdfa()[0].properties) == terms


def test_rdfa_relative_vocab_resolved_past_the_length_limit_clears_the_vocabulary() -> None:
    html = (
        f'<base href="{_iri(_LIMIT, _CDN)}"><div vocab="terms/" typeof="Person"><span property="name">Ada</span></div>'
    )
    assert parse(html).rdfa(base_url=_BASE) == [
        RdfaItem(vocab=None, type=["Person"], resource=None, properties={"name": ["Ada"]}),
    ]


@pytest.mark.parametrize(
    "prefix_iri",
    [
        pytest.param("terms/", id="resolved-at-declaration"),
        pytest.param("terms#", id="resolved-with-each-reference"),
        pytest.param("//", id="resolves-to-the-base-itself"),
    ],
)
def test_rdfa_relative_prefix_iri_resolved_past_the_length_limit_expands_nothing(prefix_iri: str) -> None:
    html = (
        f'<base href="{_iri(_LIMIT + 1, _CDN)}">'
        f'<div prefix="ex: {prefix_iri}" typeof="ex:Person"><span property="ex:name ex:a.b">Ada</span></div>'
    )
    assert parse(html).rdfa(base_url=_BASE) == [
        RdfaItem(vocab=None, type=["ex:Person"], resource=None, properties={"ex:name": ["Ada"], "ex:a.b": ["Ada"]}),
    ]


def test_rdfa_relative_prefix_iri_lengthened_to_the_limit_expands_the_curie() -> None:
    base = _iri(_LIMIT - len("terms#") + len("ex:"), _CDN)
    assert parse(_curie_markup_under(base)).rdfa(base_url=_BASE)[0].properties == {f"{base}terms#name": ["Ada"]}


def _curie_markup_under(base: str) -> str:
    return f'<base href="{base}"><div prefix="ex: terms#" typeof="T"><span property="ex:name">Ada</span></div>'


def test_rdfa_relative_prefix_iri_lengthened_past_the_limit_keeps_the_curie() -> None:
    base = _iri(_LIMIT + 1 - len("terms#") + len("ex:"), _CDN)
    assert parse(_curie_markup_under(base)).rdfa(base_url=_BASE)[0].properties == {"ex:name": ["Ada"]}


def test_rdfa_relative_prefix_iri_closing_on_a_dot_segment_does_not_bring_the_base_back() -> None:
    html = (
        f'<base href="{_iri(_LIMIT + 40, _CDN)}page">'
        '<div prefix="ex: .." typeof="T"><span property="ex:name">Ada</span></div>'
    )
    assert parse(html).rdfa(base_url=_BASE)[0].properties == {"ex:name": ["Ada"]}


def test_rdfa_relative_prefix_iri_percent_encoded_past_the_length_limit_expands_nothing() -> None:
    html = f'<div prefix="ex: {"é" * 50}" typeof="ex:Person"><span property="ex:name">Ada</span></div>'
    assert parse(html).rdfa(base_url=_BASE)[0].properties == {"ex:name": ["Ada"]}


def test_rdfa_relative_prefix_iri_past_the_limit_expands_without_a_base_url() -> None:
    html = f'<div prefix="ex: {"é" * 50}" typeof="ex:Person"><span property="ex:name">Ada</span></div>'
    assert parse(html).rdfa()[0].properties == {f"{'é' * 50}name": ["Ada"]}


@pytest.mark.parametrize(
    ("character", "encoded"),
    [
        pytest.param("é", "%C3%A9", id="two-utf8-bytes"),
        pytest.param("中", "%E4%B8%AD", id="three-utf8-bytes"),
        pytest.param("😀", "%F0%9F%98%80", id="four-utf8-bytes"),
    ],
)
def test_rdfa_relative_prefix_iri_limit_ignores_the_reference_percent_encoding(character: str, encoded: str) -> None:
    # the reference's own characters grow when resolved; that growth is the term's, so a short mapping still expands
    html = f'<div prefix="ex: terms#" typeof="T"><span property="ex:{character * 60}">Ada</span></div>'
    assert parse(html).rdfa(base_url=_BASE)[0].properties == {
        f"https://example.org/dir/terms#{encoded * 60}": ["Ada"],
    }
