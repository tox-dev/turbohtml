from __future__ import annotations

import pytest

from turbohtml import parse, parse_fragment


def test_unacknowledged_self_closing_start_tag_reports_the_token_position() -> None:
    assert [(error.code, error.line, error.col) for error in parse("<ul><li><div id='foo'/>A</li></ul>").errors] == [
        ("non-void-html-element-start-tag-with-trailing-solidus", 1, 8)
    ]


def test_document_ignores_the_slash_on_a_non_void_html_element() -> None:
    document = parse("<ul><li><div id='foo'/>A</li></ul>")
    element = document.find("div")
    assert element is not None
    assert element.text == "A"


def test_fragment_ignores_the_slash_on_a_non_void_html_element() -> None:
    fragment = parse_fragment("<div/>A", "body")
    element = fragment.find("div")
    assert element is not None
    assert element.text == "A"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<br/>", id="html-void"),
        pytest.param("<image/>", id="image-alias"),
        pytest.param("<svg/>", id="svg-root"),
        pytest.param("<math/>", id="mathml-root"),
        pytest.param("<svg><path/></svg>", id="svg-child"),
        pytest.param("<math><mrow/></math>", id="mathml-child"),
    ],
)
def test_acknowledged_self_closing_start_tag_reports_no_error(markup: str) -> None:
    assert parse(markup).errors == []


def test_html_integration_point_does_not_acknowledge_a_non_void_start_tag() -> None:
    assert [error.code for error in parse("<math><mtext><ms/>X</mtext></math>").errors] == [
        "non-void-html-element-start-tag-with-trailing-solidus"
    ]


def test_html_integration_point_ignores_the_slash() -> None:
    document = parse("<math><mtext><ms/>X</mtext></math>")
    element = document.find("ms")
    assert element is not None
    assert element.text == "X"
