from __future__ import annotations

import html
from typing import Final

import pytest

import turbohtml
from turbohtml import escape


@pytest.mark.parametrize(
    ("text", "kwargs", "expected"),
    [
        pytest.param(
            "'<script>\"&foo;\"</script>'",
            {},
            "&#x27;&lt;script&gt;&quot;&amp;foo;&quot;&lt;/script&gt;&#x27;",
            id="default-quotes-on",
        ),
        pytest.param(
            "'<script>\"&foo;\"</script>'",
            {"quote": False},
            "'&lt;script&gt;\"&amp;foo;\"&lt;/script&gt;'",
            id="quote-false",
        ),
        pytest.param("\"'", {"quote": False}, "\"'", id="quote-false-bare-quotes"),
        pytest.param("\"'", {}, "&quot;&#x27;", id="default-escapes-quotes"),
    ],
)
def test_escape_quote_modes(text: str, kwargs: dict[str, bool], expected: str) -> None:
    assert turbohtml.escape(text, **kwargs) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("a", id="single-char"),
        pytest.param("plain text", id="words"),
        pytest.param("x" * 100, id="long-ascii"),
        pytest.param("caf\xe9 r\xe9sum\xe9", id="latin1"),
        pytest.param("☃ snowman", id="ucs2"),
        pytest.param("☃" * 100, id="long-ucs2"),
        pytest.param("\U0001f600 emoji", id="astral"),
        pytest.param("\U0001f600" * 100, id="long-astral"),
    ],
)
def test_escape_no_specials_returns_equal(text: str) -> None:
    assert turbohtml.escape(text) == text


@pytest.mark.parametrize(
    "marker",
    [
        pytest.param("", id="ascii"),
        pytest.param("☃", id="ucs2"),
        pytest.param("\U0001f600", id="ucs4"),
    ],
)
@pytest.mark.parametrize("pad", range(20))
@pytest.mark.parametrize(
    ("char", "rep"),
    [
        pytest.param("&", "&amp;", id="amp"),
        pytest.param("<", "&lt;", id="lt"),
        pytest.param(">", "&gt;", id="gt"),
        pytest.param('"', "&quot;", id="quot"),
        pytest.param("'", "&#x27;", id="apos"),
    ],
)
def test_escape_special_at_every_offset(marker: str, pad: int, char: str, rep: str) -> None:
    head, tail = f"{marker}{'a' * pad}", "b" * pad
    assert turbohtml.escape(f"{head}{char}{tail}") == f"{head}{rep}{tail}"


def test_escape_adjacent_specials() -> None:
    assert turbohtml.escape("&<>\"'" * 5) == "&amp;&lt;&gt;&quot;&#x27;" * 5


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("☃ <b> & </b>", "☃ &lt;b&gt; &amp; &lt;/b&gt;", id="ucs2-with-specials"),
        pytest.param("\U0001f600<&>\"'", "\U0001f600&lt;&amp;&gt;&quot;&#x27;", id="astral-with-specials"),
        pytest.param("\xe9\xff & \xe9", "\xe9\xff &amp; \xe9", id="latin1-high-bytes"),
    ],
)
def test_escape_multiple_kinds(text: str, expected: str) -> None:
    assert turbohtml.escape(text) == expected


def test_escape_wide_quote_false() -> None:
    # the UCS-2/UCS-4 path with quote=False: leaves quotes alone, still escapes & < >
    assert turbohtml.escape('☃ "x" & <b>', quote=False) == '☃ "x" &amp; &lt;b&gt;'


@pytest.mark.parametrize(
    "marker",
    [
        pytest.param("", id="ascii"),
        pytest.param("☃", id="ucs2"),
        pytest.param("\U0001f600", id="ucs4"),
    ],
)
def test_escape_quote_false_skips_full_blocks(marker: str) -> None:
    # quotes spread over whole blocks must be skipped with quote=False while & is still escaped
    body = "'a\"b" * 10
    assert turbohtml.escape(f"{marker}{body}&", quote=False) == f"{marker}{body}&amp;"


def test_escape_wide_lookalike_codepoints() -> None:
    # wide code points whose individual bytes match specials ('&' is 0x26, '<' is 0x3c) must stay untouched
    assert turbohtml.escape("☦㰼Ħ&") == "☦㰼Ħ&amp;"
    assert turbohtml.escape("\U0001f626\U0001f63c&") == "\U0001f626\U0001f63c&amp;"


def test_escape_str_subclass_returns_true_str() -> None:
    class StrSubclass(str):  # ruff:ignore[subclass-builtin]  # subclassing str is the behavior under test
        __slots__ = ()

    result = turbohtml.escape(StrSubclass("a & b"))
    assert result == "a &amp; b"
    assert type(result) is str
    clean = turbohtml.escape(StrSubclass("clean"))
    assert clean == "clean"
    assert type(clean) is str


def test_escape_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        turbohtml.escape(123)  # ty: ignore[invalid-argument-type]  # non-str on purpose to exercise the TypeError path


@pytest.mark.parametrize("quote", [False, True], ids=["text", "quoted"])
@pytest.mark.parametrize("offset", range(8), ids=lambda value: f"offset-{value}")
@pytest.mark.parametrize(
    "pair",
    [
        pytest.param("&'", id="amp-apostrophe"),
        pytest.param("'&", id="apostrophe-amp"),
        pytest.param("<=", id="less-equals"),
        pytest.param("=<", id="equals-less"),
        pytest.param(">?", id="greater-question"),
        pytest.param("?>", id="question-greater"),
        pytest.param('"#', id="quote-hash"),
        pytest.param('#"', id="hash-quote"),
        pytest.param("\xa6\xa7", id="high-bits"),
        pytest.param("&\x00", id="embedded-null"),
    ],
)
def test_escape_adjacent_byte_lanes(pair: str, offset: int, *, quote: bool) -> None:
    text: Final = f"{'x' * offset}{pair * 8}tail"
    assert escape(text, quote=quote) == html.escape(text, quote=quote)
