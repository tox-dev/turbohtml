from __future__ import annotations

import pytest

from turbohtml import parse


@pytest.mark.parametrize(
    ("token", "iri"),
    [
        pytest.param("jsonld:Context", "http://www.w3.org/ns/json-ld#Context", id="jsonld"),
        pytest.param("odrl:permission", "http://www.w3.org/ns/odrl/2/permission", id="odrl"),
        pytest.param("sosa:observes", "http://www.w3.org/ns/sosa/observes", id="sosa"),
        pytest.param("SOSA:observes", "http://www.w3.org/ns/sosa/observes", id="sosa-uppercase"),
        pytest.param("ssn:hasProperty", "http://www.w3.org/ns/ssn/hasProperty", id="ssn"),
        pytest.param("time:hasBeginning", "http://www.w3.org/2006/time#hasBeginning", id="time"),
        pytest.param("describedby", "http://www.w3.org/2007/05/powder-s#describedby", id="describedby"),
        pytest.param("LICENSE", "http://www.w3.org/1999/xhtml/vocab#license", id="license-case-insensitive"),
        pytest.param("role", "http://www.w3.org/1999/xhtml/vocab#role", id="role"),
    ],
)
def test_rdfa_initial_context_property(token: str, iri: str) -> None:
    html = f'<div typeof="schema:Thing"><span property="{token}">x</span></div>'
    assert parse(html).rdfa()[0].properties == {iri: ["x"]}


def test_rdfa_initial_context_type_prefix() -> None:
    assert parse('<div typeof="sosa:Observation"></div>').rdfa()[0].type == ["http://www.w3.org/ns/sosa/Observation"]


def test_rdfa_vocab_overrides_initial_context_term() -> None:
    html = '<div vocab="http://schema.org/" typeof="Thing"><span property="license">L</span></div>'
    assert parse(html).rdfa()[0].properties == {"http://schema.org/license": ["L"]}


def test_rdfa_prefix_overrides_initial_context() -> None:
    html = (
        '<div prefix="sosa: https://example.org/ns#" typeof="schema:Thing">'
        '<span property="sosa:observes">x</span></div>'
    )
    assert parse(html).rdfa()[0].properties == {"https://example.org/ns#observes": ["x"]}


def test_rdfa_empty_vocab_restores_initial_context_term() -> None:
    html = (
        '<div vocab="http://schema.org/"><div vocab="" typeof="schema:Thing">'
        '<span property="license">L</span></div></div>'
    )
    assert parse(html).rdfa()[0].properties == {"http://www.w3.org/1999/xhtml/vocab#license": ["L"]}


def test_rdfa_unmapped_term_does_not_match_initial_context() -> None:
    html = '<div typeof="schema:Thing"><span property="description">x</span></div>'
    assert parse(html).rdfa()[0].properties == {"description": ["x"]}
