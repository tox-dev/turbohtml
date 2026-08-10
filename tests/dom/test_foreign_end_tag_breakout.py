"""Cover the foreign-content ``</p>`` and ``</br>`` breakout rules."""

from __future__ import annotations

import pytest

from turbohtml import _html

_DOCUMENT_CASES = [
    pytest.param(
        "<svg></p><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <svg svg>\n|     <p>\n|     <foo>",
        id="svg-end-p",
    ),
    pytest.param(
        "<svg></br><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <svg svg>\n|     <br>\n|     <foo>",
        id="svg-end-br",
    ),
    pytest.param(
        "<math></p><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <math math>\n|     <p>\n|     <foo>",
        id="math-end-p",
    ),
    pytest.param(
        "<math></br><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <math math>\n|     <br>\n|     <foo>",
        id="math-end-br",
    ),
]

_FRAGMENT_CASES = [
    pytest.param("<svg></p><foo>", "div", "| <svg svg>\n| <p>\n| <foo>", id="div-svg-end-p"),
    pytest.param("<svg></br><foo>", "div", "| <svg svg>\n| <br>\n| <foo>", id="div-svg-end-br"),
    pytest.param("</p><foo>", "svg svg", "| <p>\n| <svg foo>", id="svg-root-end-p"),
    pytest.param("</br><foo>", "svg svg", "| <br>\n| <svg foo>", id="svg-root-end-br"),
]


@pytest.mark.parametrize(("data", "expected"), _DOCUMENT_CASES)
def test_foreign_end_tag_breaks_out(data: str, expected: str) -> None:
    assert _html._parse_tree(data).rstrip("\n") == expected


@pytest.mark.parametrize(("data", "context", "expected"), _FRAGMENT_CASES)
def test_foreign_end_tag_in_fragment(data: str, context: str, expected: str) -> None:
    assert _html._parse_fragment(data, context).rstrip("\n") == expected
