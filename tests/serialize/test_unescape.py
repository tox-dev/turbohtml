from __future__ import annotations

import html
import random

import pytest

import turbohtml

CASES = [
    pytest.param("no character references", "no character references", id="no-refs"),
    pytest.param("&\n&\t& &&", "&\n&\t& &&", id="bare-amp"),
    pytest.param("&0 &9 &a &0; &9; &a;", "&0 &9 &a &0; &9; &a;", id="amp-then-alnum"),
    pytest.param("&amp;&lt;&gt;&quot;&#x27;", "&<>\"'", id="common-named"),
    pytest.param("&#62;&#x3e;&#X3E;", ">>>", id="numeric-dec-hex"),
    pytest.param("&#0;", "�", id="zero-to-replacement"),
    pytest.param("&#13;", "\r", id="carriage-return"),
    pytest.param("&#128;", "€", id="windows-1252-remap"),
    pytest.param("&#xD800;", "�", id="surrogate"),
    pytest.param("&#x110000;", "�", id="out-of-range"),
    pytest.param("&#1;", "", id="invalid-codepoint-empty"),
    pytest.param("&#99999999999999999999;", "�", id="overflow"),
    pytest.param("&quot;;", '";', id="trailing-semicolon"),
    pytest.param("&notit", "\xacit", id="longest-match-no-semi"),
    pytest.param("&notit;", "\xacit;", id="longest-match-semi"),
    pytest.param("&notin;", "∉", id="full-named-match"),
    pytest.param("&CounterClockwiseContourIntegral;", "∳", id="longest-valid-name"),
    pytest.param("&acE;", "∾̳", id="two-codepoint-entity"),
    pytest.param("&acE", "&acE", id="two-codepoint-needs-semi"),
    pytest.param("&svadilfari;", "&svadilfari;", id="unknown-named"),
    pytest.param("&amp", "&", id="legacy-no-semi"),
    pytest.param("&AMP;", "&", id="legacy-uppercase"),
    pytest.param("&Amp", "&Amp", id="case-sensitive-miss"),
    pytest.param("&co;", "&co;", id="too-short-miss"),
    pytest.param(f"&{'a' * 40}", f"&{'a' * 40}", id="name-cap-32-chars"),
    pytest.param("&<b>", "&<b>", id="amp-then-lt"),
    pytest.param("&\x0cx", "&\x0cx", id="amp-then-formfeed"),
    pytest.param("&amp#", "&#", id="name-then-hash"),
    pytest.param("&#62x", ">x", id="numeric-no-semicolon"),
    pytest.param("&#62", ">", id="numeric-digits-to-end"),
    pytest.param("&#x!", "&#x!", id="hex-non-digit-char"),
    pytest.param("&#x1F600;", "\U0001f600", id="numeric-astral-non-surrogate"),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_unescape_cases(text: str, expected: str) -> None:
    assert turbohtml.unescape(text) == expected
    assert turbohtml.unescape(text) == html.unescape(text)


@pytest.mark.parametrize("prefix", ["&", "&#", "&#x", "&#X", "&#y", "&#xy", "&#Xy"])
def test_unescape_incomplete_at_end(prefix: str) -> None:
    assert turbohtml.unescape(prefix) == prefix
    assert turbohtml.unescape(f"{prefix};") == f"{prefix};"


def test_unescape_no_reference_returns_input() -> None:
    text = "plain text without references"
    assert turbohtml.unescape(text) is text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("☃ &amp; &#62; &copy; x", "☃ & > \xa9 x", id="ucs2"),
        pytest.param("\U0001f600&amp;&#x41;&notin;", "\U0001f600&A∉", id="astral"),
        pytest.param("\U0001f600 &amp; tail", "\U0001f600 & tail", id="astral-trailing-text"),
        pytest.param("☃ snowman waits patiently &amp; melts", "☃ snowman waits patiently & melts", id="ucs2-late-ref"),
        pytest.param(
            "\U0001f600 emoji party &gt; goes on late", "\U0001f600 emoji party > goes on late", id="ucs4-late-ref"
        ),
    ],
)
def test_unescape_multiple_kinds(text: str, expected: str) -> None:
    assert turbohtml.unescape(text) == expected


def test_unescape_long_text_with_sparse_refs() -> None:
    head, tail = "x" * 5000, "y" * 5000
    assert turbohtml.unescape(f"{head}&amp;{tail}") == f"{head}&{tail}"
    assert turbohtml.unescape("a" * 5000) == "a" * 5000


def test_unescape_str_subclass() -> None:
    class StrSubclass(str):  # ruff:ignore[subclass-builtin]  # subclassing str is the behavior under test
        __slots__ = ()

    assert turbohtml.unescape(StrSubclass("a &amp; b")) == "a & b"


def test_unescape_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        turbohtml.unescape(123)  # ty: ignore[invalid-argument-type]  # non-str on purpose to exercise the TypeError path


def test_unescape_matches_stdlib_fuzz() -> None:
    rng = random.Random(1234)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # fuzz corpus, not for security
    alphabet = [*"&<>#;xX0123456789abcAF gtltampcopyfjlignotin\t\né☃", "&amp;", "&#62;", "&notit", "&#0;", "&copy;"]
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert turbohtml.unescape(text) == html.unescape(text)
