from __future__ import annotations

from turbohtml import RdfaItem, parse


def test_rdfa_custom_prefix_matches_without_case() -> None:
    html = '<div prefix="Ex: https://example.org/ns/" typeof="eX:Thing"><span property="EX:name">Ada</span></div>'
    assert parse(html).rdfa() == [
        RdfaItem(
            vocab=None,
            type=["https://example.org/ns/Thing"],
            resource=None,
            properties={"https://example.org/ns/name": ["Ada"]},
        ),
    ]


def test_rdfa_initial_prefix_matches_without_case() -> None:
    html = '<div typeof="SCHEMA:Person"><span property="DC:title">Ada</span></div>'
    assert parse(html).rdfa() == [
        RdfaItem(
            vocab=None,
            type=["http://schema.org/Person"],
            resource=None,
            properties={"http://purl.org/dc/terms/title": ["Ada"]},
        ),
    ]


def test_rdfa_mixed_case_declaration_overrides_initial_prefix() -> None:
    html = (
        '<div prefix="SCHEMA: https://example.org/custom/" typeof="schema:Thing">'
        '<span property="ScHeMa:name">Ada</span></div>'
    )
    assert parse(html).rdfa() == [
        RdfaItem(
            vocab=None,
            type=["https://example.org/custom/Thing"],
            resource=None,
            properties={"https://example.org/custom/name": ["Ada"]},
        ),
    ]


def test_rdfa_descendant_prefix_overrides_parent_without_case() -> None:
    html = (
        '<div prefix="Ex: https://example.org/one/" typeof="ex:Thing">'
        '<span prefix="eX: https://example.org/two/" property="EX:name">Ada</span></div>'
    )
    assert parse(html).rdfa() == [
        RdfaItem(
            vocab=None,
            type=["https://example.org/one/Thing"],
            resource=None,
            properties={"https://example.org/two/name": ["Ada"]},
        ),
    ]


def test_rdfa_undeclared_uppercase_iri_keeps_original_case() -> None:
    html = '<div typeof="HTTPS://example.org/Thing"></div>'
    assert parse(html).rdfa() == [
        RdfaItem(vocab=None, type=["HTTPS://example.org/Thing"], resource=None, properties={}),
    ]
