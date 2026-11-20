from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Token, Tokenizer, TokenType, tokenize

if TYPE_CHECKING:
    from collections.abc import Iterable


_RUN_CASES: Final[list[pytest.ParameterSet]] = [
    pytest.param("<svg>x</svg>y", ["<svg>", "TEXT 'x'", "</svg>", "TEXT 'y'"], id="enter-and-leave"),
    pytest.param("<svg/><style><i>", ["<svg/>", "<style>", "TEXT '<i>'"], id="self-closing-svg"),
    pytest.param("<div><svg/><style><i>", ["<div>", "<svg/>", "<style>", "TEXT '<i>'"], id="self-closing-svg-in-html"),
    pytest.param(
        "<p></p><svg><g></g></svg><i>",
        ["<p>", "</p>", "<svg>", "<g>", "</g>", "</svg>", "<i>"],
        id="svg-at-document-depth-zero",
    ),
    pytest.param(
        "<svg><g></g></svg><style><i>",
        ["<svg>", "<g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="close-after-run",
    ),
    pytest.param("<svg><svg></svg></svg><i>", ["<svg>", "<svg>", "</svg>", "</svg>", "<i>"], id="nested-svg"),
    pytest.param(
        "<svg><g><rect/></g></svg><style><i>",
        ["<svg>", "<g>", "<rect/>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="self-closing-in-run",
    ),
    pytest.param(
        "<svg><g/><g></g></svg><style><i>",
        ["<svg>", "<g/>", "<g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="self-closing-g",
    ),
    pytest.param(
        '<svg><g a=1 b="x&amp;y"><i c=2></i></g></svg><style d=3><i>',
        ["<svg>", "<g a='1' b='x&y'>", "<i c='2'>", "</i>", "</g>", "</svg>", "<style d='3'>", "TEXT '<i>'"],
        id="attributes-in-run",
    ),
    pytest.param(
        "<svg><title></title><g></g></svg><i>",
        ["<svg>", "<title>", "</title>", "<g>", "</g>", "</svg>", "<i>"],
        id="point-then-run",
    ),
    pytest.param(
        "<div><svg><g></g></svg><style><i>",
        ["<div>", "<svg>", "<g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="html-parent",
    ),
    pytest.param("<table><svg><g></g></svg><i>", ["<table>", "<svg>", "<g>", "</g>", "</svg>", "<i>"], id="in-table"),
    pytest.param(
        "<math><mi><svg><g></g></svg><style><i>",
        ["<math>", "<mi>", "<svg>", "<g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="foreign-parent-stays-open",
    ),
    pytest.param(
        "<math><mi><svg><g>x</g></svg>y<style><i>",
        ["<math>", "<mi>", "<svg>", "<g>", "TEXT 'x'", "</g>", "</svg>", "TEXT 'y'", "<style>", "TEXT '<i>'"],
        id="foreign-parent-text",
    ),
    pytest.param(
        "<svg><g><svg><g></g></svg></g></svg><style><i>",
        ["<svg>", "<g>", "<svg>", "<g>", "</g>", "</svg>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="nested-runs",
    ),
    pytest.param("<svg><g><p><style><i>", ["<svg>", "<g>", "<p>", "<style>", "TEXT '<i>'"], id="break-p"),
    pytest.param("<svg><g><b>x</b></g>", ["<svg>", "<g>", "<b>", "TEXT 'x'", "</b>", "</g>"], id="break-b"),
    pytest.param("<svg><g><div><i>", ["<svg>", "<g>", "<div>", "<i>"], id="break-div"),
    pytest.param("<svg><g><br><i>", ["<svg>", "<g>", "<br>", "<i>"], id="break-br"),
    pytest.param(
        "<svg><g><font>x</font><i>", ["<svg>", "<g>", "<font>", "TEXT 'x'", "</font>", "<i>"], id="font-plain"
    ),
    pytest.param("<svg><g><font color=red><i>", ["<svg>", "<g>", "<font color='red'>", "<i>"], id="font-color"),
    pytest.param("<svg><g><font size=1><i>", ["<svg>", "<g>", "<font size='1'>", "<i>"], id="font-size"),
    pytest.param("<svg><g><font FACE=a><i>", ["<svg>", "<g>", "<font face='a'>", "<i>"], id="font-face"),
    pytest.param(
        "<svg><g><foreignObject><style><i>",
        ["<svg>", "<g>", "<foreignobject>", "<style>", "TEXT '<i>'"],
        id="foreign-object",
    ),
    pytest.param("<svg><g><desc><b><i>", ["<svg>", "<g>", "<desc>", "<b>", "<i>"], id="desc"),
    pytest.param(
        "<svg><g><title><textarea>v</textarea></title><i>",
        ["<svg>", "<g>", "<title>", "<textarea>", "TEXT 'v'", "</textarea>", "</title>", "<i>"],
        id="title",
    ),
    pytest.param(
        "<svg><g><annotation-xml encoding=text/html><style><i>",
        ["<svg>", "<g>", "<annotation-xml encoding='text/html'>", "<style>", "<i>"],
        id="annotation-xml-long-name",
    ),
    pytest.param(
        "<math><annotation-xml encoding=text/html><svg><g><style><i>",
        ["<math>", "<annotation-xml encoding='text/html'>", "<svg>", "<g>", "<style>", "<i>"],
        id="svg-in-html-annotation",
    ),
    pytest.param(
        "<math><mi><svg><g><annotation-xml><i></annotation-xml></g><style><i>",
        [
            "<math>",
            "<mi>",
            "<svg>",
            "<g>",
            "<annotation-xml>",
            "<i>",
            "</annotation-xml>",
            "</g>",
            "<style>",
            "TEXT '<i>'",
        ],
        id="annotation-in-run",
    ),
    pytest.param(
        "<svg><g><math><mi><style><i>", ["<svg>", "<g>", "<math>", "<mi>", "<style>", "<i>"], id="math-in-run"
    ),
    pytest.param("<svg><g><mi><i>", ["<svg>", "<g>", "<mi>", "<i>"], id="mathml-text-name-in-run"),
    pytest.param(
        "<table><svg><g><desc><td><i>",
        ["<table>", "<svg>", "<g>", "<desc>", "<td>", "<i>"],
        id="table-part-in-table-point",
    ),
    pytest.param("<svg><g><desc><td><i>", ["<svg>", "<g>", "<desc>", "<td>", "<i>"], id="table-part-outside-table"),
    pytest.param("<svg><g></x><i>", ["<svg>", "<g>", "</x>", "<i>"], id="mismatched-end"),
    pytest.param("<svg><g></svg><i>", ["<svg>", "<g>", "</svg>", "<i>"], id="end-of-outer"),
    pytest.param("<svg><g><h></g></svg><i>", ["<svg>", "<g>", "<h>", "</g>", "</svg>", "<i>"], id="end-skips-inner"),
    pytest.param("<svg></x><i>", ["<svg>", "</x>", "<i>"], id="stray-end-empty-run"),
    pytest.param("<svg><g></p></g><i>", ["<svg>", "<g>", "</p>", "</g>", "<i>"], id="end-p-in-run"),
    pytest.param("<svg><g></br><i>", ["<svg>", "<g>", "</br>", "<i>"], id="end-br-in-run"),
    pytest.param("<svg><g></g></g><i>", ["<svg>", "<g>", "</g>", "</g>", "<i>"], id="extra-end"),
    pytest.param("<svg></abcdefghijklmnop><i>", ["<svg>", "</abcdefghijklmnop>", "<i>"], id="long-end-empty-run"),
    pytest.param(
        "<svg><g></ABCDEFGHIJ></g><i>", ["<svg>", "<g>", "</abcdefghij>", "</g>", "<i>"], id="long-end-in-run"
    ),
    pytest.param(
        "<svg><linearGradient><stop/></linearGradient><i>",
        ["<svg>", "<lineargradient>", "<stop/>", "</lineargradient>", "<i>"],
        id="long-name",
    ),
    pytest.param(
        "<svg><abcdefgh><i></abcdefgh><i>", ["<svg>", "<abcdefgh>", "<i>", "</abcdefgh>", "<i>"], id="eight-byte-name"
    ),
    pytest.param("<svg><abcdefghi/><i>", ["<svg>", "<abcdefghi/>", "<i>"], id="nine-byte-self-closing"),
    pytest.param("<svg><ő><i></ő><b>", ["<svg>", "TEXT '<ő>'", "<i>", "COMMENT 'ő'", "<b>"], id="non-ascii-name"),
    pytest.param(
        "<svg><g><ő>x</ő></g><b>", ["<svg>", "<g>", "TEXT '<ő>x'", "COMMENT 'ő'", "</g>", "<b>"], id="non-ascii-in-run"
    ),
]


@pytest.mark.parametrize(("document", "expected"), _RUN_CASES)
def test_svg_run_tokens_match_main(document: str, expected: list[str]) -> None:
    assert _rendered(tokenize(document)) == expected


def _rendered(tokens: Iterable[Token]) -> list[str]:
    return [_render(token) for token in tokens]


def _render(token: Token) -> str:
    if token.type is TokenType.START_TAG:
        attrs = "".join(f" {name}={value!r}" for name, value in token.attrs or [])
        return f"<{token.tag}{attrs}{'/' if token.self_closing else ''}>"
    if token.type is TokenType.END_TAG:
        return f"</{token.tag}>"
    return f"{token.type.name} {token.data!r}"


@pytest.mark.parametrize(("document", "expected"), _RUN_CASES)
def test_svg_run_streamed_one_character_at_a_time(document: str, expected: list[str]) -> None:
    tokenizer: Final[Tokenizer] = Tokenizer()
    tokens: Final[list[Token]] = [token for char in document for token in tokenizer.feed(char)]
    assert _rendered([*tokens, *tokenizer.close()]) == expected


@pytest.mark.parametrize(("document", "expected"), _RUN_CASES)
def test_svg_run_split_at_every_feed_boundary(document: str, expected: list[str]) -> None:
    for split in range(len(document) + 1):
        tokenizer: Final[Tokenizer] = Tokenizer()
        tokens: Final[list[Token]] = [
            *tokenizer.feed(document[:split]),
            *tokenizer.feed(document[split:]),
            *tokenizer.close(),
        ]
        assert _rendered(tokens) == expected, split


_BARE_CASES: Final[list[pytest.ParameterSet]] = [
    pytest.param(
        '<svg><g a=1><b c="2"></b></g></svg><style d=3><i>',
        ["<svg>", "<g>", "<b>", "</b>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="attributes-in-run",
    ),
    pytest.param(
        "<svg><font color=red a=1><style><i>", ["<svg>", "<font>", "<style>", "TEXT '<i>'"], id="font-breakout"
    ),
    pytest.param(
        "<math><mi><svg><g x=1></g></svg><style y=2><i>",
        ["<math>", "<mi>", "<svg>", "<g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
        id="foreign-parent",
    ),
    pytest.param("<svg><title><i a=1></title><i b=2>", ["<svg>", "<title>", "<i>", "</title>", "<i>"], id="point"),
    pytest.param("<svg/><style><i>", ["<svg/>", "<style>", "TEXT '<i>'"], id="self-closing"),
    pytest.param(
        "<svg><g></g></svg><style a=1><i>", ["<svg>", "<g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"], id="close"
    ),
    pytest.param(
        "<svg><g><linearGradient a=1></linearGradient></g><i>",
        ["<svg>", "<g>", "<lineargradient>", "</lineargradient>", "</g>", "<i>"],
        id="long-name",
    ),
]


@pytest.mark.parametrize(("document", "expected"), _BARE_CASES)
def test_svg_run_without_captured_attributes(document: str, expected: list[str]) -> None:
    tokens: Final[list[Token]] = list(tokenize(document, capture_attributes=False))
    assert _rendered(tokens) == expected
    assert not any(token.attrs for token in tokens)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            f"{'<p>' * 32}<svg><g></g></svg><style><i>",
            ["<p>"] * 32 + ["<svg>", "<g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
            id="svg-at-capacity",
        ),
        pytest.param(
            f"{'<p>' * 31}<svg><g><g></g></g></svg><style><i>",
            ["<p>"] * 31 + ["<svg>", "<g>", "<g>", "</g>", "</g>", "</svg>", "<style>", "TEXT '<i>'"],
            id="run-at-capacity",
        ),
        pytest.param(
            f"<svg>{'<g>' * 1200}{'</g>' * 1200}</svg><style><i>",
            ["<svg>", *["<g>"] * 1200, *["</g>"] * 1200, "</svg>", "<style>", "TEXT '<i>'"],
            id="past-the-depth-limit",
        ),
    ],
)
def test_svg_run_stops_at_the_depth_limit(document: str, expected: list[str]) -> None:
    assert _rendered(tokenize(document)) == expected


def test_reset_inside_svg_run_returns_to_html_content() -> None:
    tokenizer: Final[Tokenizer] = Tokenizer(capture_attributes=False)
    before: Final[list[str]] = _rendered(tokenizer.feed("<svg><g><g>"))
    tokenizer.reset()
    after: Final[list[str]] = _rendered([*tokenizer.feed("<style><i></style></g><svg></svg><b>"), *tokenizer.close()])
    assert before == ["<svg>", "<g>", "<g>"]
    assert after == [
        "<style>",
        "TEXT '<i>'",
        "</style>",
        "</g>",
        "<svg>",
        "</svg>",
        "<b>",
    ]
