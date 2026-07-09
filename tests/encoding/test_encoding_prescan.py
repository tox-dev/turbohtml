"""The WHATWG "prescan a byte stream to determine its encoding" algorithm, byte for byte."""

from __future__ import annotations

import pytest

from turbohtml import parse

_FILLER = b"x" * 130


@pytest.mark.parametrize(
    ("markup", "encoding"),
    [
        pytest.param(b"<meta charset=windows-1251 charset=utf-8>", "windows-1251", id="duplicate-charset"),
        pytest.param(
            b'<meta http-equiv=content-type http-equiv=refresh content="text/html;charset=utf-8">',
            "UTF-8",
            id="duplicate-http-equiv",
        ),
    ],
)
def test_a_repeated_attribute_name_is_ignored(markup: bytes, encoding: str) -> None:
    # the prescan collects attributes into a list and skips a name already in it, so the first occurrence decides
    assert parse(markup).encoding == encoding


def test_charset_beyond_the_first_bytes_of_a_content_value_is_still_found() -> None:
    # the spec caps only the 1024-byte prescan window, never a single attribute value
    markup = b'<meta http-equiv=content-type content="text/html; a=' + _FILLER + b'; charset=utf-8">'
    assert parse(markup).encoding == "UTF-8"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(b"<!--><meta charset=windows-1251>", id="no-dashes-between"),
        pytest.param(b"<!---><meta charset=windows-1251>", id="one-dash-between"),
        pytest.param(b"<!-- --><meta charset=windows-1251>", id="spaced"),
    ],
)
def test_a_comment_closes_at_the_first_gt_preceded_by_two_dashes(markup: bytes) -> None:
    # the two dashes of "<!--" may double as the "--" of "-->", so "<!-->" is a complete comment
    assert parse(markup).encoding == "windows-1251"


def test_a_slash_not_followed_by_a_letter_skips_to_the_next_gt() -> None:
    # "</" plus a non-letter is a bogus comment: everything up to the next ">" is swallowed, meta included
    markup = b"</ <meta charset=utf-8> <meta charset=windows-1251>"
    assert parse(markup).encoding == "windows-1251"


@pytest.mark.parametrize(
    ("markup", "encoding"),
    [
        pytest.param(b'<meta charset=a content="text/html;charset=utf-8">', "windows-1252", id="unknown-charset-attr"),
        pytest.param(
            b'<meta content="text/html;charset=utf-8" charset=windows-1251>',
            "windows-1251",
            id="charset-attr-overrides-content",
        ),
        pytest.param(b'<meta content="text/html;charset=utf-8">', "windows-1252", id="content-without-pragma"),
        pytest.param(b'<meta http-equiv=content-type content="text/html;charset=utf-8">', "UTF-8", id="pragma"),
    ],
)
def test_charset_and_content_attributes_resolve_in_the_spec_order(markup: bytes, encoding: str) -> None:
    assert parse(markup).encoding == encoding


def test_a_repeated_content_attribute_is_ignored() -> None:
    # the first content wins, as it does for charset and http-equiv; the second declares a different encoding
    markup = (
        b'<meta http-equiv=content-type content="text/html;charset=utf-8" content="text/html;charset=windows-1251">'
    )
    assert parse(markup).encoding == "UTF-8"
