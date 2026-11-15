from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse
from turbohtml.detect import detect

_PAST_THE_PRESCAN: Final[bytes] = b"<!--" + b"x" * 1100 + b"-->"
_CUT: Final[bytes] = b"windows-1251".ljust(63) + b"x"
_PADDED: Final[bytes] = b" " * 80 + b"windows-1251" + b" " * 80


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
    markup = b'<meta http-equiv=content-type content="text/html; a=' + (b"x" * 130) + b'; charset=utf-8">'
    assert parse(markup).encoding == "UTF-8"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(b'<meta charset="' + _CUT + b'">', id="charset-trailing"),
        pytest.param(b'<meta charset="' + b"windows-1251x".rjust(64) + b'">', id="charset-leading"),
        pytest.param(b"<meta http-equiv=content-type content=\"charset='" + _CUT + b"'\">", id="content"),
        pytest.param(
            _PAST_THE_PRESCAN + b"<meta http-equiv=content-type content=\"charset='" + _CUT + b"'\">",
            id="content-past-the-prescan",
        ),
    ],
)
def test_a_value_longer_than_any_label_is_not_cut_down_to_one(markup: bytes) -> None:
    # cut to 63 bytes and trimmed of whitespace the value would read "windows-1251"; whole, it names nothing
    assert parse(markup).encoding == "windows-1252"
    assert detect(markup).encoding == "windows-1252"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(b'<meta charset="' + _PADDED + b'">', id="charset"),
        pytest.param(b"<meta http-equiv=content-type content=\"charset='" + _PADDED + b"'\">", id="content"),
    ],
)
def test_whitespace_around_a_label_does_not_count_toward_its_length(markup: bytes) -> None:
    assert parse(markup).encoding == "windows-1251"
    assert detect(markup).encoding == "windows-1251"


def test_whitespace_around_a_label_past_the_prescan_does_not_count_toward_its_length() -> None:
    markup = _PAST_THE_PRESCAN + b"<meta http-equiv=content-type content=\"charset='" + _PADDED + b"'\">"
    assert parse(markup).encoding == "windows-1251"


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


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            b'<meta charset="" http-equiv=content-type content="text/html; charset=windows-1251">', id="empty-charset"
        ),
        pytest.param(
            b'<meta charset=foo http-equiv=content-type content="text/html; charset=windows-1251">',
            id="unknown-charset",
        ),
        pytest.param(
            b'<meta charset http-equiv=content-type content="text/html; charset=windows-1251">', id="valueless-charset"
        ),
    ],
)
def test_a_charset_naming_no_encoding_falls_through_to_content(markup: bytes) -> None:
    assert parse(markup).encoding == "windows-1251"
    assert detect(markup).encoding == "windows-1251"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            b'<meta http-equiv=content-type content="text/html; charset=windows-1251" charset="">', id="empty-charset"
        ),
        pytest.param(
            b'<meta http-equiv=content-type content="text/html; charset=windows-1251" charset=foo>',
            id="unknown-charset",
        ),
    ],
)
def test_a_failing_charset_after_content_leaves_the_content_declaration_in_force(markup: bytes) -> None:
    assert parse(markup).encoding == "windows-1251"
    assert detect(markup).encoding == "windows-1251"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(b'<meta charset=foo content="text/html; charset=windows-1251">', id="failing-charset-no-pragma"),
        pytest.param(
            b'<meta http-equiv=content-type content="charset=; charset=windows-1251">', id="first-token-empty"
        ),
        pytest.param(b'<meta http-equiv=content-type content="charset=bogus">', id="unknown-label"),
        pytest.param(b'<meta http-equiv=content-type content="charset=\'windows-1251">', id="unclosed-quote"),
        pytest.param(b'<meta http-equiv=content-type content="charset windows-1251">', id="charset-without-equals"),
    ],
)
def test_an_invalid_content_declaration_names_no_encoding(markup: bytes) -> None:
    assert parse(markup).encoding == "windows-1252"
    assert detect(markup).encoding == "windows-1252"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(b'<meta http-equiv content="charset=windows-1251">', id="valueless-http-equiv"),
        pytest.param(b"<meta http-equiv=content-type>", id="pragma-without-content"),
        pytest.param(b"<meta http-equiv=content-type content>", id="valueless-content"),
    ],
)
def test_a_meta_without_a_usable_pragma_or_content_names_no_encoding(markup: bytes) -> None:
    assert parse(markup).encoding == "windows-1252"
    assert detect(markup).encoding == "windows-1252"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            b'<meta charset=foo><meta http-equiv=content-type content="charset=windows-1251">', id="second-meta"
        ),
        pytest.param(
            b'<meta http-equiv=content-type content="charset=bogus"><meta charset=windows-1251>',
            id="content-then-charset",
        ),
    ],
)
def test_the_first_declaration_that_names_an_encoding_decides(markup: bytes) -> None:
    assert parse(markup).encoding == "windows-1251"
    assert detect(markup).encoding == "windows-1251"


def test_the_content_charset_token_is_matched_in_any_case_and_with_spaces() -> None:
    markup = b"<meta http-equiv=CONTENT-TYPE content=\"text/html;  CHARSET  =  'WINDOWS-1251'\">"
    assert parse(markup).encoding == "windows-1251"
    assert detect(markup).encoding == "windows-1251"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(b'<meta charset="big5\x00">', id="quoted-charset-value"),
        pytest.param(b"<meta charset=big5\x00q>", id="unquoted-charset-value"),
        pytest.param(b'<meta charset\x00q="big5">', id="charset-name"),
        pytest.param(b'<meta http-equiv="content-type" content="text/html; charset=big5\x00q">', id="content-label"),
        pytest.param(b'<meta http-equiv="content-type\x00q" content="text/html; charset=big5">', id="http-equiv-value"),
        pytest.param(b'<meta http-equiv\x00q="content-type" content="text/html; charset=big5">', id="http-equiv-name"),
        pytest.param(b'<meta content\x00q="text/html; charset=big5" http-equiv="content-type">', id="content-name"),
    ],
)
def test_a_nul_byte_does_not_end_a_name_or_a_value(markup: bytes) -> None:
    # the spec compares whole names and values, so a NUL must not cut them short
    assert parse(markup).encoding == "windows-1252"
    assert detect(markup).encoding == "windows-1252"


def test_a_nul_byte_does_not_hide_the_rest_of_a_content_value() -> None:
    # detect() is the prescan alone; parse() would find the same label again in the tree's own <meta>
    markup = b'<meta http-equiv="content-type" content="\x00text/html; charset=big5">'
    assert detect(markup).encoding == "Big5"
