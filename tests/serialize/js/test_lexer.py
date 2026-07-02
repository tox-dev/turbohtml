"""JavaScript lexer conformance via the ``_minify_js_tokens`` hook.

The hook renders the token stream as a canonical dump: _TOKENS space-separated, a
value-bearing token as ``KIND:lexeme`` and a punctuator as ``KIND``, with a ``*``
prefix on any token a line terminator preceded (the automatic-semicolon-insertion
signal). The dump drives the lexer through a minimal operand/operator position
tracker, so it also covers the regex- and template-rescan paths the real parser
will trigger. Phase 1 lands the lexer only; the parser and printer follow.
"""

from __future__ import annotations

import pytest

from turbohtml import _html

_TOKENS = _html._minify_js_tokens


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        pytest.param("", "EOF", id="empty"),
        pytest.param("   \t  ", "EOF", id="whitespace-only"),
        pytest.param("x", "IDENT:x EOF", id="bare-identifier"),
        pytest.param("$_a0", "IDENT:$_a0 EOF", id="identifier-symbols"),
        pytest.param("café", "IDENT:café EOF", id="identifier-non-ascii"),
        pytest.param("\\u0061bc", "IDENT:\\u0061bc EOF", id="identifier-unicode-escape"),
        pytest.param("\\u{61}bc", "IDENT:\\u{61}bc EOF", id="identifier-unicode-braced-escape"),
        pytest.param("#field", "PRIVATE:#field EOF", id="private-name"),
    ],
)
def test_identifiers(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        pytest.param("0", "NUM:0 EOF", id="zero"),
        pytest.param("42", "NUM:42 EOF", id="integer"),
        pytest.param("3.14", "NUM:3.14 EOF", id="float"),
        pytest.param(".5", "NUM:.5 EOF", id="leading-dot-float"),
        pytest.param("1e10", "NUM:1e10 EOF", id="exponent"),
        pytest.param("1.5E-3", "NUM:1.5E-3 EOF", id="signed-exponent"),
        pytest.param("2e+8", "NUM:2e+8 EOF", id="positive-exponent"),
        pytest.param("0xFF", "NUM:0xFF EOF", id="hex"),
        pytest.param("0o17", "NUM:0o17 EOF", id="octal"),
        pytest.param("0b1010", "NUM:0b1010 EOF", id="binary"),
        pytest.param("1_000_000", "NUM:1_000_000 EOF", id="separators"),
        pytest.param("0xDE_AD", "NUM:0xDE_AD EOF", id="hex-separators"),
        pytest.param("42n", "BIGINT:42n EOF", id="bigint"),
        pytest.param("0x1Fn", "BIGINT:0x1Fn EOF", id="bigint-hex"),
        pytest.param("007", "NUM:007 EOF", id="legacy-octal"),
    ],
)
def test_numbers(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        pytest.param("'abc'", "STRING:'abc' EOF", id="single-quoted"),
        pytest.param('"abc"', 'STRING:"abc" EOF', id="double-quoted"),
        pytest.param(r"'a\'b'", r"STRING:'a\'b' EOF", id="escaped-quote"),
        pytest.param("'a\\\nb'", "STRING:'a\\\nb' EOF", id="line-continuation"),
        pytest.param('""', 'STRING:"" EOF', id="empty-string"),
    ],
)
def test_strings(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        pytest.param("`abc`", "TEMPLATE:`abc` EOF", id="no-substitution"),
        pytest.param("``", "TEMPLATE:`` EOF", id="empty-template"),
        pytest.param(r"`a\`b`", r"TEMPLATE:`a\`b` EOF", id="escaped-backtick"),
        pytest.param(
            "`a${x}b`",
            "TEMPLATE_HEAD:`a${ IDENT:x TEMPLATE_TAIL:}b` EOF",
            id="one-substitution",
        ),
        pytest.param(
            "`${x}${y}`",
            "TEMPLATE_HEAD:`${ IDENT:x TEMPLATE_MIDDLE:}${ IDENT:y TEMPLATE_TAIL:}` EOF",
            id="two-substitutions",
        ),
        pytest.param(
            "`${ {a:1} }`",
            "TEMPLATE_HEAD:`${ LBRACE IDENT:a COLON NUM:1 RBRACE TEMPLATE_TAIL:}` EOF",
            id="object-in-substitution",
        ),
        pytest.param(
            "`${`${x}`}`",
            "TEMPLATE_HEAD:`${ TEMPLATE_HEAD:`${ IDENT:x TEMPLATE_TAIL:}` TEMPLATE_TAIL:}` EOF",
            id="nested-template",
        ),
    ],
)
def test_templates(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        # a value is expected, so the slash opens a regular-expression literal
        pytest.param("/ab+/g", "REGEX:/ab+/g EOF", id="regex-at-start"),
        pytest.param("x = /re/", "IDENT:x ASSIGN REGEX:/re/ EOF", id="regex-after-assign"),
        pytest.param("return /re/", "IDENT:return REGEX:/re/ EOF", id="regex-after-keyword"),
        pytest.param(r"/a\/b/", r"REGEX:/a\/b/ EOF", id="regex-escaped-slash"),
        pytest.param("/[/]/", "REGEX:/[/]/ EOF", id="regex-class-slash"),
        pytest.param("/=/", "REGEX:/=/ EOF", id="regex-looks-like-diveq"),
        # an operand just ended, so the slash is division
        pytest.param("a / b", "IDENT:a DIV IDENT:b EOF", id="division"),
        pytest.param("a /= b", "IDENT:a DIV_ASSIGN IDENT:b EOF", id="divide-assign"),
        pytest.param("4 / 2 / 1", "NUM:4 DIV NUM:2 DIV NUM:1 EOF", id="chained-division"),
        pytest.param(") / x", "RPAREN DIV IDENT:x EOF", id="division-after-paren"),
    ],
)
def test_regex_vs_division(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        pytest.param(
            "{}()[];,.:?", "LBRACE RBRACE LPAREN RPAREN LBRACK RBRACK SEMI COMMA DOT COLON QUESTION EOF", id="brackets"
        ),
        pytest.param("...", "ELLIPSIS EOF", id="ellipsis"),
        pytest.param("=>", "ARROW EOF", id="arrow"),
        pytest.param("a?.b", "IDENT:a OPT_CHAIN IDENT:b EOF", id="optional-chain"),
        pytest.param("a?.5", "IDENT:a QUESTION NUM:.5 EOF", id="ternary-not-optional-chain"),
        pytest.param("< > <= >=", "LT GT LE GE EOF", id="relational"),
        pytest.param("== != === !==", "EQ_EQ NE EQ_EQ_EQ NE_EQ EOF", id="equality"),
        pytest.param("+ - * %", "PLUS MINUS STAR MOD EOF", id="arithmetic"),
        pytest.param("**", "POW EOF", id="power"),
        pytest.param("++ --", "INC DEC EOF", id="inc-dec"),
        pytest.param("<< >> >>>", "SHL SHR USHR EOF", id="shifts"),
        pytest.param("& | ^ ! ~", "BIT_AND BIT_OR BIT_XOR NOT BIT_NOT EOF", id="bitwise"),
        pytest.param("&& || ??", "AND OR NULLISH EOF", id="logical"),
        pytest.param(
            "= += -= *= %= **=",
            "ASSIGN PLUS_ASSIGN MINUS_ASSIGN STAR_ASSIGN MOD_ASSIGN POW_ASSIGN EOF",
            id="arithmetic-assign",
        ),
        pytest.param(
            "<<= >>= >>>=",
            "SHL_ASSIGN SHR_ASSIGN USHR_ASSIGN EOF",
            id="shift-assign",
        ),
        pytest.param(
            "&= |= ^= &&= ||= ??=",
            "AND_ASSIGN OR_ASSIGN XOR_ASSIGN LAND_ASSIGN LOR_ASSIGN NULLISH_ASSIGN EOF",
            id="logical-assign",
        ),
    ],
)
def test_punctuators(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        pytest.param("a // comment\nb", "IDENT:a *IDENT:b EOF", id="line-comment"),
        pytest.param("a /* c */ b", "IDENT:a IDENT:b EOF", id="block-comment-inline"),
        pytest.param("a /* x\ny */ b", "IDENT:a *IDENT:b EOF", id="block-comment-newline"),
        pytest.param("a\nb", "IDENT:a *IDENT:b EOF", id="newline-flag"),
        pytest.param("a\r\nb", "IDENT:a *IDENT:b EOF", id="crlf"),
        pytest.param("a" + chr(0x2029) + "b", "IDENT:a *IDENT:b EOF", id="paragraph-separator"),
        pytest.param("a" + chr(0x0B) + "b", "IDENT:a IDENT:b EOF", id="vertical-tab-ws"),
        pytest.param("a" + chr(0x0C) + "b", "IDENT:a IDENT:b EOF", id="form-feed-ws"),
        pytest.param("a" + chr(0xA0) + "b", "IDENT:a IDENT:b EOF", id="no-break-space-ws"),
        pytest.param(chr(0xFEFF) + "x", "IDENT:x EOF", id="byte-order-mark"),
        pytest.param("a" + chr(0x2028) + "b", "IDENT:a *IDENT:b EOF", id="line-separator"),
        pytest.param("#!/usr/bin/env node\nx", "*IDENT:x EOF", id="hashbang"),
        pytest.param("// only a comment", "EOF", id="comment-to-eof"),
    ],
)
def test_trivia_and_newlines(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        pytest.param("'unterminated", "ERROR:'unterminated", id="unterminated-string"),
        pytest.param("'newline\nhere'", "ERROR:'newline", id="string-newline"),
        pytest.param("`unterminated", "ERROR:`unterminated", id="unterminated-template"),
        pytest.param("/* never closed", "ERROR:/* never closed", id="unterminated-block-comment"),
        pytest.param("/unterminated", "ERROR:/unterminated", id="unterminated-regex"),
        pytest.param("/re\nx/", "ERROR:/re", id="regex-newline"),
        pytest.param("@", "ERROR:@", id="stray-at"),
        pytest.param("\\x", "ERROR:\\", id="lone-backslash"),
    ],
)
def test_errors(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


@pytest.mark.parametrize(
    ("source", "dump"),
    [
        # every Unicode Space_Separator the lexer treats as token-separating white space
        pytest.param("a" + chr(0xA0) + "b", "IDENT:a IDENT:b EOF", id="ws-no-break-space"),
        pytest.param("a" + chr(0xFEFF) + "b", "IDENT:a IDENT:b EOF", id="ws-byte-order-mark"),
        pytest.param("a" + chr(0x1680) + "b", "IDENT:a IDENT:b EOF", id="ws-ogham"),
        pytest.param("a" + chr(0x2000) + "b", "IDENT:a IDENT:b EOF", id="ws-en-quad"),
        pytest.param("a" + chr(0x202F) + "b", "IDENT:a IDENT:b EOF", id="ws-narrow-no-break"),
        pytest.param("a" + chr(0x205F) + "b", "IDENT:a IDENT:b EOF", id="ws-medium-math"),
        pytest.param("a" + chr(0x3000) + "b", "IDENT:a IDENT:b EOF", id="ws-ideographic"),
        # Unicode line terminators set the newline-before flag (the leading *)
        pytest.param("a" + chr(0x2028) + "b", "IDENT:a *IDENT:b EOF", id="line-separator"),
        pytest.param("a" + chr(0x2029) + "b", "IDENT:a *IDENT:b EOF", id="paragraph-separator"),
        pytest.param("/* a * b */x", "IDENT:x EOF", id="block-comment-inner-star"),
        pytest.param("#!/usr/bin/node\nx", "*IDENT:x EOF", id="hashbang"),
        pytest.param("1e+5", "NUM:1e+5 EOF", id="exponent-plus"),
        pytest.param("1e-5", "NUM:1e-5 EOF", id="exponent-minus"),
        pytest.param("f(...a)", "IDENT:f LPAREN ELLIPSIS IDENT:a RPAREN EOF", id="spread"),
        pytest.param("x=/a\\/b/", "IDENT:x ASSIGN REGEX:/a\\/b/ EOF", id="regex-escaped-slash"),
        pytest.param("x=/abc/", "IDENT:x ASSIGN REGEX:/abc/ EOF", id="regex-no-escape"),
        pytest.param("/* a *", "ERROR:/* a *", id="unterminated-block-comment"),
        pytest.param("a.#b", "IDENT:a DOT PRIVATE:#b EOF", id="private-not-at-start"),
        pytest.param("a\\x", "IDENT:a ERROR:\\", id="identifier-backslash-not-u"),
        pytest.param("a\\", "IDENT:a ERROR:\\", id="identifier-backslash-at-end"),
        pytest.param("x=\\u{61", "IDENT:x ASSIGN IDENT:\\u{61 EOF", id="unterminated-braced-escape"),
        pytest.param("1e5", "NUM:1e5 EOF", id="exponent-no-sign"),
        pytest.param("`a$b`", "TEMPLATE:`a$b` EOF", id="template-dollar-not-brace"),
        pytest.param("a.b", "IDENT:a DOT IDENT:b EOF", id="single-dot-member"),
        pytest.param("\\u0061", "IDENT:\\u0061 EOF", id="escape-started-identifier"),
        pytest.param(".5", "NUM:.5 EOF", id="leading-dot-number"),
        # tokens that run right up to end-of-input (the pos+1<len boundary checks)
        pytest.param("#", "PRIVATE:# EOF", id="hash-at-eof"),
        pytest.param("a\\u", "IDENT:a\\u EOF", id="escape-u-at-eof"),
        pytest.param("1e", "NUM:1e EOF", id="exponent-marker-at-eof"),
        pytest.param("`a$", "ERROR:`a$", id="template-dollar-at-eof"),
        pytest.param("a..b", "IDENT:a DOT DOT IDENT:b EOF", id="two-dots-not-spread"),
        pytest.param("a.", "IDENT:a DOT EOF", id="dot-at-eof"),
        pytest.param("x=/a\\", "IDENT:x ASSIGN ERROR:/a\\", id="regex-backslash-at-eof"),
        pytest.param("a&&=b", "IDENT:a LAND_ASSIGN IDENT:b EOF", id="logical-and-assign"),
        pytest.param("a||=b", "IDENT:a LOR_ASSIGN IDENT:b EOF", id="logical-or-assign"),
    ],
)
def test_unicode_and_edge_trivia(source: str, dump: str) -> None:
    assert _TOKENS(source) == dump


def test_non_string_argument_rejected() -> None:
    with pytest.raises(TypeError, match="source must be a str"):
        _TOKENS(123)  # ty: ignore[invalid-argument-type]  # wrong type on purpose, to test the guard


_PARSE = _html._minify_js_parse


def test_parse_dump_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="source must be a str"):
        _PARSE(123)  # ty: ignore[invalid-argument-type]  # wrong type on purpose, to test the guard


def test_parse_dump_reports_parse_error() -> None:
    with pytest.raises(ValueError, match="module syntax"):
        _PARSE("import x from 'y'")


def test_token_dump_grows_buffer() -> None:
    # a long source drives the token dump buffer past its initial capacity (doubling loop);
    # every one of the 400 statements must still tokenize correctly, not just produce output
    assert _TOKENS("a;" * 400) == " ".join(["IDENT:a SEMI"] * 400) + " EOF"


def test_token_dump_deep_template_nesting() -> None:
    # 65 nested template substitutions exceed the dumper's fixed nesting tracker (the cap);
    # all 65 opens must still tokenize as template heads despite the tracker giving up
    assert _TOKENS("`${" * 65 + "0" + "}`" * 65).count("TEMPLATE_HEAD:") == 65


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        pytest.param("tag`a${b}c`", "tagged", id="tagged-template"),
        pytest.param("debugger", "debugger", id="debugger"),
        pytest.param("with(o){x}", "with", id="with"),
        pytest.param("o:for(;;)break o", "label", id="labeled-break"),
        pytest.param("a=[1,,2]", "hole", id="array-hole"),
    ],
)
def test_parse_dump_names_rare_kinds(source: str, kind: str) -> None:
    assert kind in _PARSE(source)
