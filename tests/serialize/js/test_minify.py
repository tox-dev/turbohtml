"""Per-rule output tests for the JavaScript minifier's structural transforms.

Each case pins the exact output of one transform: whitespace and comment removal,
semicolon insertion between statements, precedence-driven parenthesisation, and the
adjacency guard that keeps tokens from merging. These run on the *un-mangled* output
so identifier names stay stable; mangling has its own tests in ``test_mangle.py``.
Correctness across a large corpus is gated in ``test_corpus.py``.
"""

from __future__ import annotations

import pytest

from turbohtml import JSMinify, minify_js


def minify(source: str) -> str:
    # the structural tests pin un-mangled output, so names stay stable
    return minify_js(source, JSMinify(mangle=False, fold=False))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("var  x  =  1 ;", "var x=1", id="whitespace"),
        pytest.param("a = 1 ; b = 2 ;", "a=1;b=2", id="statement-semicolons"),
        pytest.param("// line\nx = 1", "x=1", id="line-comment"),
        pytest.param("/* block */ x = 1", "x=1", id="block-comment"),
        # a `//!` line comment is not a banner: only block bang comments are kept (see test_bang_comments_kept)
        pytest.param("//! not a banner\nx = 1", "x=1", id="bang-line-comment-stripped"),
        pytest.param("function f ( a , b ) { return a + b }", "function f(a,b){return a+b}", id="function"),
        pytest.param("x = 1 + 2 * 3", "x=1+2*3", id="precedence-no-parens"),
        pytest.param("x = ( 1 + 2 ) * 3", "x=(1+2)*3", id="precedence-needs-parens"),
        pytest.param("x = a ** b ** c", "x=a**b**c", id="pow-right-assoc"),
        pytest.param("x = ( a ** b ) ** c", "x=(a**b)**c", id="pow-left-needs-parens"),
        # ECMA-262 §13.6: the left of ** must be an UpdateExpression, so an unparenthesised
        # UnaryExpression there (`-2**2`) is a SyntaxError; the parens are load-bearing and kept.
        # A UnaryExpression is allowed on the right, so those parens are dropped.
        pytest.param("x = ( - 2 ) ** 2", "x=(-2)**2", id="pow-left-unary-keeps-parens"),
        pytest.param("x = ( typeof a ) ** 2", "x=(typeof a)**2", id="pow-left-typeof-keeps-parens"),
        pytest.param("x = ( ! a ) ** 2", "x=(!a)**2", id="pow-left-not-keeps-parens"),
        pytest.param("x = 2 ** - 3", "x=2**-3", id="pow-right-unary-drops-parens"),
        pytest.param("x = ( a ++ ) ** 2", "x=a++**2", id="pow-left-postfix-update-no-parens"),
        # ECMA-262 §13.13: ?? may not sit next to an unparenthesised || or &&, so the parens that
        # separate the two families are load-bearing and must survive on either side.
        pytest.param("x = ( a || b ) ?? c", "x=(a||b)??c", id="nullish-left-or-keeps-parens"),
        pytest.param("x = ( a && b ) ?? c", "x=(a&&b)??c", id="nullish-left-and-keeps-parens"),
        pytest.param("x = a ?? ( b || c )", "x=a??(b||c)", id="nullish-right-or-keeps-parens"),
        pytest.param("x = a ?? ( b && c )", "x=a??(b&&c)", id="nullish-right-and-keeps-parens"),
        pytest.param("x = ( a ?? b ) || c", "x=(a??b)||c", id="or-left-nullish-keeps-parens"),
        pytest.param("x = ( a ?? b ) && c", "x=(a??b)&&c", id="and-left-nullish-keeps-parens"),
        pytest.param("x = a ?? b ?? c", "x=a??b??c", id="nullish-chain-no-parens"),
        pytest.param("x = a && b && c", "x=a&&b&&c", id="and-chain-no-parens"),
        pytest.param("x = a && b || c", "x=a&&b||c", id="and-or-no-parens"),
        # a logical operand of an arithmetic operator keeps only the precedence paren (no mix)
        pytest.param("x = ( a || b ) + c", "x=(a||b)+c", id="logical-in-arithmetic"),
        # §13.3.1 / §13.3.5: an optional chain may not be a template tag or `new` callee, so the
        # parens that terminate the chain are load-bearing.
        pytest.param("( a ?. b ) `t`", "(a?.b)`t`", id="optional-chain-tag-keeps-parens"),
        pytest.param("new ( a ?. b ) ( )", "new (a?.b)()", id="optional-chain-new-callee-keeps-parens"),
        pytest.param("y = ( 1 )", "y=1", id="redundant-parens"),
    ],
)
def test_whitespace_and_precedence(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("return - 1", "return-1", id="return-minus"),
        pytest.param("return  .5", "return.5", id="return-dot-number"),
        pytest.param("a + + b", "a+ +b", id="plus-plus-not-increment"),
        pytest.param("a - - b", "a- -b", id="minus-minus-not-decrement"),
        pytest.param("a < ! -- b", "a<! --b", id="html-comment-open"),
        pytest.param("a -- > b", "a-- >b", id="html-comment-close"),
        pytest.param("x = /re/g . test ( s )", "x=/re/g.test(s)", id="regex-method"),
        pytest.param("/a/ instanceof b", "/a/ instanceof b", id="regex-then-keyword"),
        pytest.param("1 . toString ( )", "(1).toString()", id="number-member"),
        pytest.param("a instanceof B", "a instanceof B", id="instanceof-spacing"),
    ],
)
def test_adjacency_guard(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # a computed member with an identifier-name key lowers to dot access, but the object must be
        # parenthesized when it is a numeric literal so `25.toString` does not re-lex `25.` as a float
        # (#419): an integer object would otherwise produce the invalid `25.toString`
        pytest.param('25["toString"]()', "(25).toString()", id="int-computed-to-dot-parenthesizes"),
        pytest.param('0["x"]', "(0).x", id="zero-computed-to-dot-parenthesizes"),
        pytest.param('(3)["valueOf"]()', "(3).valueOf()", id="paren-int-computed-to-dot"),
        # a non-integer numeric object takes dot access unambiguously; the parens are the printer's
        # existing conservative choice, matching a plain `.` after a number
        pytest.param('3.5["x"]', "(3.5).x", id="float-computed-to-dot"),
        pytest.param('0x25["x"]', "(0x25).x", id="hex-computed-to-dot"),
        # a non-numeric object keeps lowering to bare dot access
        pytest.param('a["b"]', "a.b", id="ident-computed-to-dot"),
        # a non-identifier key keeps the brackets
        pytest.param('a["b c"]', 'a["b c"]', id="non-ident-key-keeps-brackets"),
    ],
)
def test_computed_member_to_dot(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("body", "value"),
    [
        # ECMA-262 §12.3 LineContinuation: a backslash then a LineTerminatorSequence (LF, CR, or the CR-LF
        # pair) is consumed as one unit and drops from the value, so a CRLF-authored source lexes instead
        # of the lexer rejecting the trailing LF as an unterminated string (#407)
        pytest.param("a" + chr(0x5C) + chr(0x0A) + "b", "ab", id="lf"),
        pytest.param("a" + chr(0x5C) + chr(0x0D) + "b", "ab", id="cr"),
        pytest.param("a" + chr(0x5C) + chr(0x0D) + chr(0x0A) + "b", "ab", id="crlf"),
        pytest.param(
            "a" + chr(0x5C) + chr(0x0D) + chr(0x0A) + "b" + chr(0x5C) + chr(0x0D) + chr(0x0A) + "c",
            "abc",
            id="multi-segment-crlf",
        ),
    ],
)
def test_string_line_continuation_lexes(body: str, value: str) -> None:
    # the standalone literal keeps its lexeme, so lexing it proves the fix; concatenating "" folds by value
    assert minify_js(f'x="{body}"') == f'x="{body}"'
    assert minify_js(f'"{body}"+""') == f'"{value}"'


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("if ( x ) { a ( ) } else { b ( ) }", "if(x)a();else b()", id="if-else"),
        pytest.param("for ( let i = 0 ; i < n ; i ++ ) { f ( i ) }", "for(let i=0;i<n;i++)f(i)", id="for"),
        pytest.param("for ( const k in o ) { }", "for(const k in o){}", id="for-in"),
        pytest.param("for ( const v of a ) { }", "for(const v of a){}", id="for-of"),
        pytest.param("do { f ( ) } while ( c )", "do f();while(c)", id="do-while"),
        pytest.param(
            "switch ( x ) { case 1 : a ( ) ; break ; default : b ( ) }",
            "switch(x){case 1:a();break;default:b()}",
            id="switch",
        ),
        pytest.param(
            "try { a ( ) } catch ( e ) { b ( ) } finally { c ( ) }", "try{a()}catch(e){b()}finally{c()}", id="try"
        ),
        pytest.param("label : for ( ; ; ) { break label }", "label:for(;;)break label", id="labeled"),
        # a single scope-free statement drops the loop-body braces; a lexical declaration, an empty
        # body, or an already-braceless body keeps its form
        pytest.param("for ( ; ; ) g ( )", "for(;;)g()", id="loop-body-braceless-kept"),
        pytest.param("for ( ; ; ) { class C { } }", "for(;;){class C{}}", id="loop-body-class-kept"),
        pytest.param("for ( ; ; ) { function h ( ) { } }", "for(;;){function h(){}}", id="loop-body-function-kept"),
        pytest.param("do { } while ( c )", "do{}while(c)", id="do-while-empty-kept"),
        pytest.param("do a ( ) ; while ( b )", "do a();while(b)", id="do-while-braceless-kept"),
        pytest.param("for ( ; ; ) { ; g ( ) }", "for(;;)g()", id="loop-body-empty-then-stmt"),
        pytest.param("for ( var a = ( b in c ) ; ; ) ;", "for(var a=(b in c);;);", id="for-init-in-parenthesised"),
        # an if branch, label body or with body flattens like a loop body; a consequent that would
        # end in an open `if` keeps (or gains) braces so the `else` stays attached to the outer if
        pytest.param("while ( x ) { if ( a ) { break } }", "while(x)if(a)break", id="if-branch-braces-drop"),
        pytest.param("l : { g ( ) }", "l:g()", id="label-body-braces-drop"),
        pytest.param("with ( o ) { g ( ) }", "with(o)g()", id="with-body-braces-drop"),
        pytest.param(
            "while ( x ) { if ( a ) { if ( b ) break } else d ( ) }",
            "while(x)if(a){if(b)break}else d()",
            id="dangling-else-braces-kept",
        ),
        pytest.param(
            "while ( x ) { if ( a ) { while ( y ) if ( b ) break } else d ( ) }",
            "while(x)if(a){while(y)if(b)break}else d()",
            id="dangling-else-loop-chain-kept",
        ),
        pytest.param(
            "while ( x ) { if ( a ) { if ( b ) break ; else e ( ) } else d ( ) }",
            "while(x)if(a)if(b)break;else e();else d()",
            id="closed-inner-else-flattens",
        ),
        pytest.param(
            "if ( a ) b : { if ( c ) break b } else d ( )",
            "if(a){b:if(c)break b}else d()",
            id="dangling-else-label-braced",
        ),
    ],
)
def test_statements(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("x = { a : 1 , b ( ) { } , get c ( ) { } }", "x={a:1,b(){},get c(){}}", id="object"),
        pytest.param("x = [ 1 , , 3 , , ]", "x=[1,,3,,]", id="array-holes"),
        pytest.param("x = a => a + 1", "x=a=>a+1", id="arrow-single-param"),
        pytest.param("x = ( a , b ) => a", "x=(a,b)=>a", id="arrow-multi-param"),
        pytest.param("f = ( ) => ( { a : 1 } )", "f=()=>({a:1})", id="arrow-object-body"),
        pytest.param("class C extends B { #p = 1 ; m ( ) { } }", "class C extends B{#p=1;m(){}}", id="class"),
        pytest.param("s = `a ${ x + 1 } b`", "s=`a ${x+1} b`", id="template"),
        pytest.param("new Foo ( 1 )", "new Foo(1)", id="new-with-args"),
        pytest.param("new a . b . C ( )", "new a.b.C()", id="new-member-callee"),
        pytest.param("x = a ??= b", "x=a??=b", id="nullish-assign"),
    ],
)
def test_expressions(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # a regex with a `\` escape inside a parenthesised function/arrow expression:
        # the arrow-lookahead used to lex the `/` as division, hit the `\`, and leak the
        # speculative lexical error into the real parse (the jQuery/lodash desync). The
        # expected output pins that the regex survives intact rather than raising.
        pytest.param(r"(function(){var a = /\d/})", r"(function(){var a=/\d/})", id="func-expr"),
        pytest.param(r"(()=>{var a = /\d/})", r"()=>{var a=/\d/}", id="arrow-expr"),
        pytest.param(
            r"(function(){var a = /[\d]/, b = /\w+/g})",
            r"(function(){var a=/[\d]/,b=/\w+/g})",
            id="two-escaped-regexes",
        ),
    ],
)
def test_speculative_backtrack_does_not_leak_error(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("x = 1.0", "x=1", id="trailing-zero"),
        pytest.param("x = 0.5", "x=.5", id="leading-zero"),
        pytest.param("x = 1.50", "x=1.5", id="trailing-fraction-zero"),
        pytest.param("x = 5.", "x=5", id="trailing-dot"),
        pytest.param("x = 1_000_000", "x=1e6", id="separators-and-exponent"),
        pytest.param("x = 1000", "x=1e3", id="round-integer-exponent"),
        pytest.param("x = 100", "x=100", id="exponent-not-shorter"),
        pytest.param("x = 0xFF", "x=0xFF", id="hex-kept"),
        pytest.param("x = 007", "x=007", id="legacy-octal-kept"),
        pytest.param("x = 0.0", "x=0", id="zero-point-zero"),
        # a literal too long for the stack buffer takes the heap path; separators still go
        pytest.param("x = " + "1" + "_000" * 23, "x=" + "1" + "000" * 23, id="overflow-strips-separators"),
        # BigInt has no dot or exponent, so separator removal is its only safe shortening
        pytest.param("x = 1_000n", "x=1000n", id="bigint-strips-separators"),
        pytest.param("x = 0x1_0n", "x=0x10n", id="bigint-hex-strips-separators"),
        pytest.param("x = 123n", "x=123n", id="bigint-no-separators-verbatim"),
    ],
)
def test_number_canonicalization(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a^b", "a^b", id="bit-xor"),
        pytest.param("a&b", "a&b", id="bit-and"),
        pytest.param("a|b", "a|b", id="bit-or"),
        pytest.param("a<<b", "a<<b", id="shl"),
        pytest.param("a>>b", "a>>b", id="shr"),
        pytest.param("a>>>b", "a>>>b", id="ushr"),
        pytest.param("a%b", "a%b", id="mod"),
        pytest.param("a-=b", "a-=b", id="sub-assign"),
        pytest.param("a*=b", "a*=b", id="mul-assign"),
        pytest.param("a/=b", "a/=b", id="div-assign"),
        pytest.param("a%=b", "a%=b", id="mod-assign"),
        pytest.param("a**=b", "a**=b", id="pow-assign"),
        pytest.param("a<<=b", "a<<=b", id="shl-assign"),
        pytest.param("a>>=b", "a>>=b", id="shr-assign"),
        pytest.param("a>>>=b", "a>>>=b", id="ushr-assign"),
        pytest.param("a&=b", "a&=b", id="and-assign"),
        pytest.param("a|=b", "a|=b", id="or-assign"),
        pytest.param("a^=b", "a^=b", id="xor-assign"),
        pytest.param(
            "x={async f(){},*g(){},async*h(){}}", "x={async f(){},*g(){},async *h(){}}", id="async-gen-methods"
        ),
        pytest.param("(a?.b)()", "(a?.b)()", id="parenthesised-optional-callee"),
        pytest.param("for(var x=(a in b);;)c()", "for(var x=(a in b);;)c()", id="in-operator-in-for-init"),
        pytest.param("for(a instanceof b;;)c()", "for(a instanceof b;;)c()", id="instanceof-in-for-init"),
        pytest.param(";", "", id="empty-statement"),
        pytest.param(";;", "", id="empty-statements"),
        pytest.param("a>b", "a>b", id="greater-than"),
        pytest.param("o={x}", "o={x}", id="unmangled-shorthand-kept"),
        pytest.param("x=0o17", "x=0o17", id="octal-radix"),
        pytest.param("x=0O17", "x=0O17", id="octal-radix-upper"),
        pytest.param("x=0b101", "x=0b101", id="binary-radix"),
        pytest.param("x=0B101", "x=0B101", id="binary-radix-upper"),
        pytest.param("x=0XFF", "x=0XFF", id="hex-radix-upper"),
        pytest.param("foo[0]", "foo[0]", id="three-char-computed-member"),
        pytest.param("leg[0]", "leg[0]", id="three-char-member-not-let"),
        pytest.param("lot[0]", "lot[0]", id="three-char-member-l-not-le"),
        pytest.param("let[0]", "let [0]", id="let-computed-member-guard"),
        pytest.param("x=0", "x=0", id="single-zero-not-octal"),
        pytest.param("function*g(){yield;yield x}", "function*g(){yield;yield x}", id="yield-with-and-without-arg"),
    ],
)
def test_operators_and_structural(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # the leading `(` must survive: it keeps a statement-position function/object from
        # being read as a declaration / block instead of an expression
        pytest.param("( function ( ) { } ) ( )", "(function(){}())", id="iife-statement-start"),
        pytest.param("( { a : 1 } )", "({a:1})", id="object-in-statement-position"),
    ],
)
def test_statement_position_parens_preserved(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # a bang block comment is a license/banner header kept byte-exact, the way the CSS minifier keeps
        # `/*! ... */`; every other comment is still stripped
        pytest.param("/*! (c) 2026 Me */\nvar x = 1", "/*! (c) 2026 Me */var x=1", id="bang-kept"),
        pytest.param("/* @license MIT */\na = 1", "/* @license MIT */a=1", id="license-kept"),
        pytest.param("/* @preserve keep */ a = 1", "/* @preserve keep */a=1", id="preserve-kept"),
        # the body survives verbatim: internal stars, newlines and alignment are not touched
        pytest.param("/*!\n * line\n */\na = 1", "/*!\n * line\n */a=1", id="bang-body-verbatim"),
        pytest.param("/* plain */ a = 1", "a=1", id="plain-block-stripped"),
        # a bang comment anywhere is hoisted into a leading banner, in source order
        pytest.param("a = 1;/*! trailing */", "/*! trailing */a=1", id="trailing-bang-hoisted"),
        pytest.param("/*! one */b = 1;/*! two */", "/*! one */ /*! two */b=1", id="two-bangs-source-order"),
        # a program that is nothing but a banner still emits it
        pytest.param("/*! banner only */", "/*! banner only */", id="banner-only"),
        # more banners than the initial capacity, to exercise the growth of the kept-comment list
        pytest.param(
            "/*!1*//*!2*//*!3*//*!4*//*!5*/x = 1",
            "/*!1*/ /*!2*/ /*!3*/ /*!4*/ /*!5*/x=1",
            id="many-bangs-grow",
        ),
    ],
)
def test_bang_comments_kept(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("/*! keep */\nfunction f(longName){return longName*2}", id="mangle-fold"),
        pytest.param("/* @license MIT */ const value = 1 + 2; use(value)", id="license-through-compress"),
    ],
)
def test_bang_comment_survives_full_minify_and_is_idempotent(source: str) -> None:
    once = minify_js(source)
    assert once.startswith("/*")  # the banner leads the output even with mangle and fold on
    assert minify_js(once) == once


def test_unparseable_input_raises() -> None:
    # module syntax is not handled; minify_js fails loudly rather than silently
    # echoing the source back, so an unminifiable script never passes unnoticed
    with pytest.raises(ValueError, match="module syntax"):
        minify("import x from 'y'; x()")


def test_non_string_argument_rejected() -> None:
    with pytest.raises(TypeError, match="source must be a str"):
        minify(123)  # ty: ignore[invalid-argument-type]  # wrong type on purpose, to test the guard
