"""Identifier mangling: exact renames plus a behavior differential under Node.

The string cases pin specific renames (parameters, locals, closures, block scope,
shorthand) and the safety rules (``with`` / ``eval`` poison a scope, globals and
property names are never renamed). ``test_mangling_preserves_behavior`` is the real
correctness gate: it runs each snippet and its minified form under Node and asserts
identical output, so any capture or mis-resolution is caught. It is skipped when Node
is unavailable (e.g. the CI unit environment) - the string cases still run there.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404

import pytest

from turbohtml.clean import minify_js

_NODE = shutil.which("node")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("function f(longName, other){return longName+other}", "function f(b,a){return b+a}", id="params"),
        pytest.param(
            "function f(){var localVar=g();return localVar*localVar}",
            "function f(){var a=g();return a*a}",
            id="local",
        ),
        pytest.param("var g=(alpha,beta)=>alpha+beta", "var g=(b,a)=>b+a", id="arrow-params"),
        pytest.param(
            "function outer(x){function inner(y){return x+y}return inner}",
            "function outer(a){return function(b){return a+b}}",
            id="closure-capture",
        ),
        pytest.param("function f(p,q){return p+p+q}", "function f(a,b){return a+a+b}", id="frequency-shortest-name"),
        pytest.param("function f(p){return p.toString()}", "function f(a){return a.toString()}", id="property-kept"),
        pytest.param("function f(){}", "function f(){}", id="nothing-renamable"),
        # a renamed shorthand binding must expand to key:value or it reads the wrong property
        pytest.param("function f({x}){return x}", "function f({x:a}){return a}", id="shorthand-pattern-expanded"),
        pytest.param(
            "function f(){var x=g();return{x}}", "function f(){var a=g();return{x:a}}", id="shorthand-literal-expanded"
        ),
        # a global shorthand binding is never renamed, so it stays a shorthand (not expanded)
        pytest.param("let x=1;o={x}", "let x=1;o={x}", id="shorthand-global-kept"),
        pytest.param("function f(){var x=1;with(o){x}}", "function f(){var x=1;with(o)x}", id="with-poisons"),
        pytest.param(
            "function f(){eval('x');var y=1;return y}", "function f(){eval('x');var y=1;return y}", id="eval-poisons"
        ),
        pytest.param("let top=1;function f(){return top}", "let top=1;function f(){return top}", id="global-kept"),
        # a named function/class expression binds its name only inside its own body, so the name
        # is renamed like any local (its references go with it); a declaration name stays kept
        pytest.param("var f=function rec(n){return rec}", "var f=function b(a){return b}", id="nfe-name-renamed"),
        pytest.param(
            "var C=class Self{m(){return Self}}", "var C=class a{m(){return a}}", id="class-expr-name-renamed"
        ),
        pytest.param("function decl(){return decl}", "function decl(){return decl}", id="func-decl-name-kept"),
        # labels are their own namespace: the label renames (with its break/continue) while a
        # same-named variable renames independently, and a global variable still stays put
        pytest.param("outer:for(;;){break outer}", "a:for(;;)break a", id="label-renamed"),
        pytest.param("x:for(var x=0;x<1;x++)break x", "a:for(var x=0;x<1;x++)break a", id="label-and-var-share-name"),
        # four-character callees that differ from `eval` at each position must not poison
        pytest.param("oval(1)", "oval(1)", id="eval-lookalike-char0"),
        pytest.param("exit(1)", "exit(1)", id="eval-lookalike-char1"),
        pytest.param("even(1)", "even(1)", id="eval-lookalike-char2"),
        pytest.param("evan(1)", "evan(1)", id="eval-lookalike-char3"),
    ],
)
def test_renames(source: str, expected: str) -> None:
    assert minify_js(source) == expected


def _run(code: str) -> str:
    assert _NODE is not None  # the callers are skipped when node is unavailable
    result = subprocess.run([_NODE, "-e", code], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    return result.stdout + result.stderr


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("(function(){var a=1,b=2;function s(x){return x+a+b}console.log(s(10))})()", id="closure"),
        pytest.param(
            "(function(){let r=[];for(let i=0;i<3;i++)r.push(()=>i);console.log(r.map(f=>f()).join(','))})()",
            id="let-per-iteration-closure",
        ),
        pytest.param(
            "(function(){function outer(n){function inner(){return n*2}return inner()}console.log(outer(21))})()",
            id="nested",
        ),
        pytest.param(
            "(function(){var x=1;{let x=2;console.log(x)}console.log(x)})()",
            id="block-shadowing",
        ),
        pytest.param(
            "(function(){var o={get v(){return this._v},set v(n){this._v=n}};o.v=5;console.log(o.v)})()",
            id="accessors",
        ),
        # enough distinct locals to spill the name tables past several resizes, push the
        # base-54 counter into two-character names, and make it skip the reserved word it
        # lands on (`if` is base-54 index 332, so 340 bindings reach and skip it)
        pytest.param(
            "(function(){var "
            + ",".join(f'v{index}="literal long enough to stay {index}"' for index in range(340))
            + ";console.log("
            # each read twice, of a long literal: a short or single-read one would inline instead
            + "+".join(f"v{index}+v{index}" for index in range(340))
            + ")})()",
            id="many-locals",
        ),
        pytest.param(
            "(function(){function f([,x]){return x}console.log(f([1,2]))})()",
            id="array-elision-pattern",
        ),
        # more block scopes than the scope array's initial capacity, forcing it to grow
        pytest.param("(function(){" + "{let a=1}" * 20 + "console.log('ok')})()", id="many-scopes"),
        # a renamed named-function-expression name must keep resolving to itself (recursion)
        pytest.param(
            "(function(){var f=function fact(n){return n<=1?1:n*fact(n-1)};console.log(f(5))})()",
            id="nfe-recursion",
        ),
        # a renamed class-expression name must keep resolving to itself from the body
        pytest.param(
            "(function(){var C=class Self{static make(){return new Self}};console.log(C.make()instanceof C)})()",
            id="class-expr-self-reference",
        ),
        # the expression name and a shadowing parameter of the same source name stay distinct
        pytest.param(
            "(function(){var f=function dup(dup){return dup};console.log(f(7))})()",
            id="nfe-name-shadowed-by-param",
        ),
        # a labeled continue/break must still target its (renamed) label, even when a variable
        # of the same name is renamed independently in the same scope
        pytest.param(
            "(function(){var out=[];loop:for(var i=0;i<3;i++){if(i===1)continue loop;out.push(i)}"
            "console.log(out.join(''))})()",
            id="labeled-continue",
        ),
        pytest.param(
            "(function(){x:for(var x=0;x<2;x++)for(var y=0;y<2;y++){if(y)continue x;console.log(x,y)}})()",
            id="label-and-var-same-name-nested",
        ),
        # past 52 nesting levels the label is kept verbatim rather than risk a multi-character name;
        # break to the outermost (renamed) and innermost (kept) labels both still resolve
        pytest.param(
            "(function(){" + "".join(f"L{depth}:" for depth in range(53)) + "for(;;){break L0}})();console.log('ok')",
            id="label-depth-cap",
        ),
    ],
)
def test_mangling_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))


_BS = chr(0x5C)  # backslash, kept out of the literals so the \u escapes are unambiguous


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # a Unicode-escaped IdentifierName is the same binding as its plain spelling (ECMA-262 §12.7), so
        # the declaration and the use link and rename together instead of the use being dropped as free (#438)
        pytest.param(
            "function f(){var " + _BS + "u0061bc=1;return abc+abc}",
            "function f(){return 2}",
            id="escaped-declaration-plain-use",
        ),
        pytest.param(
            "function f(){var abc=1;return " + _BS + "u0061bc+abc}",
            "function f(){return 2}",
            id="plain-declaration-escaped-use",
        ),
        # a braced \u{...} escape in an identifier decodes to the same StringValue
        pytest.param(
            "function f(){var " + _BS + "u{61}bc=1;return abc+abc}",
            "function f(){return 2}",
            id="braced-escape-declaration",
        ),
        # a local shadow of `undefined` spelled with an escape still shadows, so `undefined` is not the
        # global and must not fold to `void 0`
        pytest.param(
            "function f(){var " + _BS + "u0075ndefined=1;return undefined}",
            "function f(){return 1}",
            id="escaped-undefined-shadow-kept",
        ),
    ],
)
def test_escaped_identifier_binds_as_stringvalue(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param(
            "(function(){var " + _BS + "u0061bc=5;return abc})()",
            id="escaped-declaration",
        ),
        pytest.param(
            "(function(){var abc=5;return " + _BS + "u0061bc})()",
            id="escaped-use",
        ),
    ],
)
def test_escaped_identifier_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))
