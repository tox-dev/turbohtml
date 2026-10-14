from __future__ import annotations

from typing import Final

import pytest

from turbohtml import RdfaItem, parse

_BASE: Final = "https://example.org/dir/page"


@pytest.mark.parametrize(
    ("vocab", "expected"),
    [
        pytest.param("/terms/", "https://example.org/terms/", id="root-relative"),
        pytest.param("../terms/", "https://example.org/terms/", id="parent-relative"),
        pytest.param("https://schema.org/", "https://schema.org/", id="absolute"),
    ],
)
def test_rdfa_resolves_vocab_before_expanding_terms(vocab: str, expected: str) -> None:
    html = f'<div vocab="{vocab}" typeof="Person"><span property="name">Ada</span></div>'
    assert parse(html).rdfa(base_url=_BASE) == [
        RdfaItem(vocab=expected, type=[f"{expected}Person"], resource=None, properties={f"{expected}name": ["Ada"]}),
    ]


def test_rdfa_resolves_vocab_against_html_base() -> None:
    html = (
        '<base href="https://cdn.example/dir/">'
        '<div vocab="../terms/" typeof="Person"><span property="name">Ada</span></div>'
    )
    assert parse(html).rdfa(base_url=_BASE) == [
        RdfaItem(
            vocab="https://cdn.example/terms/",
            type=["https://cdn.example/terms/Person"],
            resource=None,
            properties={"https://cdn.example/terms/name": ["Ada"]},
        ),
    ]


def test_rdfa_without_base_keeps_relative_vocab() -> None:
    html = '<div vocab="/terms/" typeof="Person"><span property="name">Ada</span></div>'
    assert parse(html).rdfa() == [
        RdfaItem(vocab="/terms/", type=["/terms/Person"], resource=None, properties={"/terms/name": ["Ada"]}),
    ]


@pytest.mark.parametrize("attribute", ["about", "resource", "href", "src"])
@pytest.mark.parametrize("value", ['=""', ""], ids=["empty", "valueless"])
def test_rdfa_present_empty_subject_resolves_to_base(attribute: str, value: str) -> None:
    html = f'<div typeof="https://schema.org/Thing" {attribute}{value}></div>'
    assert parse(html).rdfa(base_url=_BASE)[0].resource == _BASE


@pytest.mark.parametrize("attribute", ["resource", "href", "src"])
def test_rdfa_present_empty_property_iri_resolves_to_base(attribute: str) -> None:
    html = f'<div typeof="https://schema.org/Thing"><a property="https://schema.org/url" {attribute}="">x</a></div>'
    assert parse(html).rdfa(base_url=_BASE)[0].properties["https://schema.org/url"] == [_BASE]


def test_rdfa_empty_subject_uses_html_base() -> None:
    html = '<base href="https://cdn.example/doc"><div typeof="https://schema.org/Thing" about=""></div>'
    assert parse(html).rdfa(base_url=_BASE)[0].resource == "https://cdn.example/doc"


def test_rdfa_absent_subject_remains_blank() -> None:
    assert parse('<div typeof="https://schema.org/Thing"></div>').rdfa(base_url=_BASE)[0].resource is None


def test_rdfa_absent_property_iri_uses_text() -> None:
    html = '<div typeof="https://schema.org/Thing"><a property="https://schema.org/url">x</a></div>'
    assert parse(html).rdfa(base_url=_BASE)[0].properties["https://schema.org/url"] == ["x"]


def test_rdfa_empty_content_is_literal() -> None:
    html = '<div typeof="https://schema.org/Thing"><a property="https://schema.org/url" content="" href="">x</a></div>'
    assert parse(html).rdfa(base_url=_BASE)[0].properties["https://schema.org/url"] == [""]
