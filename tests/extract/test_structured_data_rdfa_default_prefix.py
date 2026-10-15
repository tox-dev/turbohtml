from __future__ import annotations

from turbohtml import parse


def test_rdfa_default_prefix_property() -> None:
    html = '<div typeof="schema:Thing"><span property=":custom">x</span></div>'
    assert parse(html).rdfa()[0].properties == {"http://www.w3.org/1999/xhtml/vocab#custom": ["x"]}


def test_rdfa_default_prefix_type() -> None:
    assert parse('<div typeof=":Thing"></div>').rdfa()[0].type == ["http://www.w3.org/1999/xhtml/vocab#Thing"]


def test_rdfa_default_prefix_empty_type_reference() -> None:
    assert parse('<div typeof=":"></div>').rdfa()[0].type == ["http://www.w3.org/1999/xhtml/vocab#"]


def test_rdfa_default_prefix_empty_property_reference() -> None:
    html = '<div typeof="schema:Thing"><span property=":">x</span></div>'
    assert parse(html).rdfa()[0].properties == {"http://www.w3.org/1999/xhtml/vocab#": ["x"]}


def test_rdfa_vocab_does_not_change_default_prefix() -> None:
    html = '<div vocab="https://example.org/#" typeof=":Thing"></div>'
    assert parse(html).rdfa()[0].type == ["http://www.w3.org/1999/xhtml/vocab#Thing"]


def test_rdfa_default_prefix_cannot_be_redeclared() -> None:
    html = '<div prefix=": https://wrong.example/" typeof=":Thing"></div>'
    assert parse(html).rdfa()[0].type == ["http://www.w3.org/1999/xhtml/vocab#Thing"]


def test_rdfa_prefix_parser_continues_after_rejected_default() -> None:
    html = '<div prefix=": https://wrong.example/ p: https://example.org/" typeof="p:Item"></div>'
    assert parse(html).rdfa()[0].type == ["https://example.org/Item"]
