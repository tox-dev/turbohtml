"""Parser error and backtrack coverage.

``minify_js`` fails loudly on a script it cannot parse, raising ``ValueError`` rather
than echoing the source back, so a malformed or unsupported script never passes
unnoticed. Each ``malformed`` case drives one parse-error path; each ``backtrack`` case
is valid syntax that forces the parser to rewind a speculative read (``get``/``set``/
``async`` used as ordinary names, the arrow-parameter lookahead) and still parse right.
"""

from __future__ import annotations

import pytest

from turbohtml import JSMinify, minify_js


def minify(source: str) -> str:
    # the parser tests pin un-mangled output, so names stay stable
    return minify_js(source, JSMinify(mangle=False, fold=False))


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("if(x", id="if-unclosed-cond"),
        pytest.param("if(x)a;else", id="else-no-body"),
        pytest.param("for", id="for-no-paren"),
        pytest.param("for(;;", id="for-unclosed"),
        pytest.param("for(var x of", id="forof-no-rhs"),
        pytest.param("for(a in", id="forin-no-rhs"),
        pytest.param("while", id="while-no-paren"),
        pytest.param("while(x", id="while-unclosed"),
        pytest.param("do a;", id="do-without-while"),
        pytest.param("do a;while", id="do-while-no-paren"),
        pytest.param("switch", id="switch-no-paren"),
        pytest.param("switch(x){a()}", id="switch-no-case"),
        pytest.param("switch(x){case 1}", id="case-no-colon"),
        pytest.param("try a", id="try-no-block"),
        pytest.param("function", id="function-no-name"),
        pytest.param("function f", id="function-no-params"),
        pytest.param("function f(a,", id="function-params-trailing"),
        pytest.param("function f(){", id="function-unclosed-body"),
        pytest.param("class", id="class-no-body"),
        pytest.param("class C{", id="class-unclosed"),
        pytest.param("class C extends", id="class-extends-nothing"),
        pytest.param("return*", id="return-bad-expr"),
        pytest.param("throw", id="throw-newline"),
        pytest.param("var", id="var-no-binding"),
        pytest.param("var x=", id="var-no-init"),
        pytest.param("let{a", id="destructure-unclosed-object"),
        pytest.param("let[a", id="destructure-unclosed-array"),
        pytest.param("x=(", id="paren-unclosed"),
        pytest.param("x=(1", id="paren-no-close"),
        pytest.param("x=[1,2", id="array-unclosed"),
        pytest.param("x={a:1", id="object-unclosed"),
        pytest.param("x={a:}", id="object-no-value"),
        pytest.param("x=a?b", id="ternary-no-colon"),
        pytest.param("x=a?b:", id="ternary-no-alt"),
        pytest.param("x=`${", id="template-unclosed-subst"),
        pytest.param("x=`${a", id="template-subst-no-close"),
        pytest.param("x=`${a}", id="template-no-backtick"),
        pytest.param("x=new", id="new-no-callee"),
        pytest.param("x=f(1,", id="call-args-trailing"),
        pytest.param("x=function(", id="func-expr-unclosed-params"),
        pytest.param("x=(a,b)=>", id="arrow-no-body"),
        pytest.param("x=/", id="regex-unterminated"),
        pytest.param("x=/[/", id="regex-unterminated-class"),
        pytest.param("import x from 'y'", id="import-unsupported"),
        pytest.param("export const x=1", id="export-unsupported"),
        pytest.param("@", id="stray-at"),
        pytest.param("x=a**", id="pow-no-rhs"),
        pytest.param("x=a||", id="logical-no-rhs"),
        # an error inside a specific production, so its propagation path is taken
        pytest.param("for(1*;;)x", id="for-init-error"),
        pytest.param("do{@}while(1)", id="do-body-error"),
        pytest.param("switch(x){case 1:1*}", id="case-body-error"),
        pytest.param("try{1*}catch(e){}", id="try-block-error"),
        pytest.param("function f(){1*}", id="function-body-error"),
        pytest.param("with(@)x", id="with-head-error"),
        pytest.param("x=(a=1*)=>a", id="arrow-param-error"),
        pytest.param("x=(1,@)", id="sequence-error"),
        pytest.param("x=new a[1*]", id="new-member-error"),
        pytest.param("x=a[1*]", id="computed-member-error"),
        pytest.param("x={m(a=1*){}}", id="method-param-error"),
        pytest.param("x=[@]", id="array-element-error"),
        pytest.param("function g(@){}", id="param-error"),
        pytest.param("class C{m(a=1*){}}", id="class-method-param-error"),
        pytest.param("class C{x=1*}", id="class-field-error"),
        pytest.param("class D{[@](){}}", id="class-computed-key-error"),
        pytest.param("try{}catch(e){1*}", id="catch-block-error"),
        pytest.param("x=f(1*)", id="call-argument-error"),
        pytest.param("x=new new", id="new-new-callee-error"),
        pytest.param("x={m(){1*}}", id="object-method-body-error"),
        pytest.param("x={[1*]:1}", id="computed-key-error"),
        pytest.param("x=(a b)=>1", id="arrow-params-no-close"),
        # an error in the final operation of a statement, so its `p->err ? -1` tail is taken
        pytest.param("for(a in b)1*", id="forin-body-error"),
        pytest.param("do a;while(1*", id="do-while-cond-error"),
        pytest.param("while(x)1*", id="while-body-error"),
        pytest.param("try{}finally{1*}", id="finally-block-error"),
        pytest.param("with(x)1*", id="with-body-error"),
        pytest.param("void 1*", id="void-operand-error"),
        pytest.param("delete 1*", id="delete-operand-error"),
        pytest.param("class C{m(){1*}}", id="class-method-body-error"),
        pytest.param("a:1*", id="labeled-statement-error"),
        pytest.param("for(;;)1*", id="for-body-error"),
        pytest.param("void", id="void-no-operand"),
        pytest.param("++", id="prefix-no-operand"),
        pytest.param("typeof", id="typeof-no-operand"),
        pytest.param("async function f(){await}", id="await-no-operand"),
        pytest.param("switch(x){case 1:a()", id="unterminated-switch"),
        pytest.param("break\\x", id="break-lexer-error"),
        pytest.param("function*g(){yield 1*}", id="yield-operand-error"),
        pytest.param("x=async function(){}", id="async-function-expression-unsupported"),
        pytest.param("function*g(){yield", id="yield-at-eof"),
        # a trailing comma then EOF, so the comma-separated loop exits on its end-of-input guard
        pytest.param("x={a:1,", id="object-trailing-comma-eof"),
        pytest.param("x=[1,2,", id="array-trailing-comma-eof"),
        pytest.param("(a,b,", id="arrow-params-trailing-comma-eof"),
    ],
)
def test_malformed_raises(source: str) -> None:
    with pytest.raises(ValueError, match="at offset"):
        minify(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("function f(){return}", "function f(){return}", id="bare-return"),
        pytest.param("function f(){return\nx}", "function f(){return;x}", id="return-asi"),
        pytest.param("function f(){return x}", "function f(){return x}", id="return-value"),
        pytest.param("a:while(1)break a", "a:while(1)break a", id="labeled-break"),
        pytest.param("a:while(1)continue a", "a:while(1)continue a", id="labeled-continue"),
        pytest.param("while(1)break", "while(1)break", id="unlabeled-break"),
        pytest.param("while(1){continue}", "while(1)continue", id="unlabeled-continue"),
        # async-function declaration vs the cases that are not one (an arrow, or a newline-broken async)
        pytest.param("async function f(){}", "async function f(){}", id="async-function-decl"),
        pytest.param("async x=>x", "async x=>x", id="async-arrow"),
        pytest.param("async\nfunction f(){}", "async;function f(){}", id="async-newline-not-decl"),
        # postfix ++/-- only when no line break precedes them, else ASI splits the statement
        pytest.param("x++", "x++", id="postfix-inc"),
        pytest.param("y--", "y--", id="postfix-dec"),
        pytest.param("x\n++y", "x;++y", id="newline-before-prefix-not-postfix"),
        # a non-ASCII identifier, so the printer's id-char guard takes its >= 0x80 arm
        pytest.param("var café=1;café in o", "var café=1;café in o", id="unicode-identifier"),
        pytest.param("void x", "void x", id="void"),
        pytest.param("delete a.b", "delete a.b", id="delete"),
        pytest.param("new a", "new a()", id="new-no-args"),
        pytest.param("new a()", "new a()", id="new-args"),
        pytest.param("switch(x){case 1:f();default:g()}", "switch(x){case 1:f();default:g()}", id="case-then-default"),
        pytest.param("class C{x}", "class C{x}", id="class-field-bare"),
        pytest.param("class C{x=1}", "class C{x=1}", id="class-field-init"),
        pytest.param("class C{m(){}}", "class C{m(){}}", id="class-method"),
        pytest.param("class C{*m(){}}", "class C{*m(){}}", id="class-generator"),
        pytest.param("class C{async m(){}}", "class C{async m(){}}", id="class-async"),
        pytest.param("class C{get x(){}}", "class C{get x(){}}", id="class-getter"),
        pytest.param("class C{set x(v){}}", "class C{set x(v){}}", id="class-setter"),
        pytest.param("class C{static y(){}}", "class C{static y(){}}", id="class-static"),
        pytest.param("class C{'s'(){}}", "class C{s(){}}", id="class-string-key"),
        pytest.param("class C{1(){}}", "class C{1(){}}", id="class-number-key"),
        pytest.param("class C{[c](){}}", "class C{[c](){}}", id="class-computed-key"),
        pytest.param("class C{#p=1}", "class C{#p=1}", id="class-private-field"),
        pytest.param("class C extends D{}", "class C extends D{}", id="class-extends"),
        pytest.param("o={get x(){}}", "o={get x(){}}", id="object-getter"),
        pytest.param("o={'s':1}", "o={s:1}", id="object-string-key"),
        pytest.param("o={1:2}", "o={1:2}", id="object-number-key"),
        pytest.param("o={[c]:1}", "o={[c]:1}", id="object-computed-key"),
        pytest.param("o={*g(){}}", "o={*g(){}}", id="object-generator"),
        pytest.param("o={a,b}", "o={a,b}", id="object-shorthand-pair"),
        pytest.param("new a", "new a()", id="new-bare-member"),
        pytest.param("function f(){return;x}", "function f(){return;x}", id="return-semicolon"),
        pytest.param("while(1){break;f()}", "while(1){break;f()}", id="break-semicolon"),
        pytest.param("x=class{}", "x=class{}", id="anonymous-class-expression"),
        pytest.param("class C{static(){}}", "class C{static(){}}", id="static-as-method-name"),
        pytest.param("class C{static=1}", "class C{static=1}", id="static-as-field-name"),
        pytest.param("class C{async(){}}", "class C{async(){}}", id="async-as-method-name"),
        pytest.param("class C{get(){}}", "class C{get(){}}", id="get-as-method-name"),
        pytest.param("class C{set=1}", "class C{set=1}", id="set-as-field-name"),
        pytest.param("class C{static async*m(){}}", "class C{static async *m(){}}", id="static-async-generator"),
        pytest.param("class C{static get x(){}}", "class C{static get x(){}}", id="static-getter"),
        pytest.param("throw x", "throw x", id="throw-value"),
        pytest.param("a:while(1){break\nf()}", "a:while(1){break;f()}", id="break-then-newline"),
        pytest.param("new a.b", "new a.b()", id="new-member-callee"),
        pytest.param("new a(1)", "new a(1)", id="new-with-arg"),
        pytest.param("({a=1}=x)", "({a=1}=x)", id="object-shorthand-default-pattern"),
        pytest.param(
            "function*g(){f(yield);[yield];g(yield,1);({k:yield});yield;yield x}",
            "function*g(){f(yield);[yield];g(yield,1);({k:yield});yield;yield x}",
            id="yield-operand-boundaries",
        ),
        pytest.param("function*g(){yield\nx}", "function*g(){yield;x}", id="yield-newline"),
        pytest.param("function*g(){a?yield:b}", "function*g(){a?yield:b}", id="yield-before-colon"),
        pytest.param("o={get,set,async}", "o={get,set,async}", id="accessor-words-shorthand"),
        pytest.param("x=class extends D{}", "x=class extends D{}", id="anonymous-class-extends"),
        pytest.param("switch(x){case 1:f();case 2:g()}", "switch(x){case 1:f();case 2:g()}", id="case-then-case"),
        pytest.param("return", "return", id="bare-return-at-eof"),
        pytest.param("class C{static}", "class C{static}", id="static-as-field-bare"),
        pytest.param("class C{async=1}", "class C{async=1}", id="async-as-field-init"),
        pytest.param("class C{get}", "class C{get}", id="get-as-field-bare"),
        pytest.param("class C{set}", "class C{set}", id="set-as-field-bare"),
        pytest.param("class C{static;m(){}}", "class C{static;m(){}}", id="static-field-semicolon"),
        pytest.param("class C{async\nm(){}}", "class C{async;m(){}}", id="async-name-then-newline"),
        pytest.param("class C{async;m(){}}", "class C{async;m(){}}", id="async-field-semicolon"),
        pytest.param("new new C()", "new new C()()", id="nested-new-with-args"),
        pytest.param("x=new new C(1)", "x=new new C(1)()", id="nested-new-inner-args"),
        pytest.param("x=new new C", "x=new new C()()", id="nested-new-inner-no-args"),
        pytest.param("x=async\nfunction f(){}", "x=async;function f(){}", id="async-newline-then-function"),
        pytest.param("class C{async get(){}}", "class C{async get(){}}", id="async-method-named-get"),
        pytest.param("class C{*get(){}}", "class C{*get(){}}", id="generator-method-named-get"),
        pytest.param("class C{get\nx(){}}", "class C{get x(){}}", id="get-name-then-newline"),
        pytest.param("class C{get;m(){}}", "class C{get;m(){}}", id="get-name-then-semicolon"),
    ],
)
def test_constructs_minify_to(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("x={get:1,set:2,async:3}", "x={get:1,set:2,async:3}", id="accessor-words-as-keys"),
        pytest.param("x={get(){},set(){},async(){}}", "x={get(){},set(){},async(){}}", id="accessor-words-as-methods"),
        pytest.param("x={get a(){},set a(v){}}", "x={get a(){},set a(v){}}", id="real-accessors"),
        pytest.param("x={async a(){},*b(){},async*c(){}}", "x={async a(){},*b(){},async *c(){}}", id="async-gen"),
        pytest.param("class C{get(){}set(){}async(){}}", "class C{get(){}set(){}async(){}}", id="class-accessor-words"),
        pytest.param("class C{get a(){}set a(v){}}", "class C{get a(){}set a(v){}}", id="class-real-accessors"),
        pytest.param("class C{static get(){}}", "class C{static get(){}}", id="class-static-get-name"),
        pytest.param("x=(a)", "x=a", id="paren-not-arrow"),
        pytest.param("x=(a,b)", "x=(a,b)", id="paren-sequence-not-arrow"),
        pytest.param("x=(a)=>a", "x=a=>a", id="paren-is-arrow"),
        pytest.param("x=async(a)=>a", "x=async a=>a", id="async-arrow"),
        pytest.param("x=async(a)", "x=async(a)", id="async-call-not-arrow"),
        pytest.param("x=10n", "x=10n", id="bigint-literal"),
        pytest.param("x=a?.[b]", "x=a?.[b]", id="optional-computed-member"),
        pytest.param("f=async\nx=>x", "f=async;x=>x", id="async-newline-not-arrow"),
        pytest.param("class C{#a;m(){return #a in this}}", "class C{#a;m(){return#a in this}}", id="private-in-check"),
    ],
)
def test_backtrack_parses(source: str, expected: str) -> None:
    assert minify(source) == expected
