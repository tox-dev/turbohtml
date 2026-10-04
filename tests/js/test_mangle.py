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
import subprocess  # ruff:ignore[suspicious-subprocess-import]

import pytest

from turbohtml.clean import JSMinify, minify_js

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
        pytest.param(
            "function transform(value){return value+a+b+c}",
            "function transform(d){return d+a+b+c}",
            id="single-character-free-names",
        ),
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


@pytest.mark.parametrize(
    ("source", "options", "expected"),
    [
        pytest.param(
            "!function(){var x=i;const y=5;x,y}",
            JSMinify(mangle=True, fold=False),
            "!function(){var a=i;5}",
            id="sequence-drops-earlier-read",
        ),
        pytest.param(
            "function f(){var x=i;const y=5;return x,y}",
            JSMinify(mangle=True, fold=False),
            "function f(){var a=i;return 5}",
            id="return-sequence",
        ),
        pytest.param(
            "function f(){var p=1;const y=5;p,y}",
            JSMinify(mangle=True, fold=False),
            "function f(){5}",
            id="dropped-local-read",
        ),
        # fold first merges `g();$` into the sequence `g(),$`, which the mangler then collapses
        pytest.param(
            "{let $=1;g();$}", JSMinify(mangle=True, fold=True), "g(),1", id="pure-statement-merged-then-collapsed"
        ),
        pytest.param("{let $=1;/a/;x=$}", JSMinify(mangle=True, fold=True), "x=1", id="regex-statement-dropped"),
        pytest.param(
            "function f(){const y=5;var x;return 0,x=y}",
            JSMinify(mangle=True, fold=False),
            "function f(){return 5}",
            id="dead-store-value",
        ),
        pytest.param(
            "function f(){const y=5;var t;return t=y,t}",
            JSMinify(mangle=True, fold=False),
            "function f(){return 5}",
            id="assign-then-read",
        ),
        pytest.param(
            "function f(){const t=5;var s=t;return s}",
            JSMinify(mangle=True, fold=False),
            "function f(){return 5}",
            id="inlined-initializer",
        ),
        pytest.param(
            "function g(){function f(){return 1}return 0,f}",
            JSMinify(mangle=True, fold=False),
            "function g(){return function(){return 1}}",
            id="function-declaration",
        ),
    ],
)
def test_binding_read_that_moved_survives(source: str, options: JSMinify, expected: str) -> None:
    # a rewrite that moves a read must not leave the mangler inlining a binding at the read's old node (#1035)
    assert minify_js(source, options) == expected


def _run(code: str) -> str:
    assert _NODE is not None  # the callers are skipped when node is unavailable
    # the first node start on a cold Windows runner has taken over a minute on its own; the timeout
    # is only here to bound a snippet that never returns, so it sits well clear of a slow launch
    result = subprocess.run([_NODE, "-e", code], capture_output=True, text=True, timeout=300, check=False)  # ruff:ignore[subprocess-without-shell-equals-true]
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
        # a binding read after a dropped pure sequence element keeps its value, not an outer name (#1035)
        pytest.param(
            "var i=7;console.log((function(){var x=i;const y=5;return x,y})())",
            id="read-survives-sequence-collapse",
        ),
        pytest.param(
            "var t=7;console.log((function(){const t=5;var s=t;return s})())",
            id="read-survives-inlined-initializer",
        ),
    ],
)
def test_mangling_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("{function g(){return 1}}g()", "{function g(){return 1}}g()", id="top-level-block-kept"),
        pytest.param(
            "function t(){{function g(){return 1}}return typeof g}",
            "function t(){{function g(){return 1}}return typeof g}",
            id="unread-in-block-kept",
        ),
        pytest.param(
            "function t(){{function g(){return 1}g()}return typeof g}",
            "function t(){{function g(){return 1}g()}return typeof g}",
            id="single-use-in-block-not-inlined",
        ),
        pytest.param(
            "function t(){var g=1;{function g(){}}return g}",
            "function t(){var g=1;{function g(){}}return g}",
            id="outer-var-not-propagated",
        ),
        pytest.param(
            "function t(){var g=1,h=g;{function g(){}}return[h,g]}",
            "function t(){var g=1,a=g;{function g(){}}return[a,g]}",
            id="outer-var-keeps-name",
        ),
        pytest.param(
            "function t(){function g(){}return[g,g]}",
            "function t(){function a(){}return[a,a]}",
            id="body-level-renamed",
        ),
        pytest.param(
            "function t(){class C{}return[C,C]}", "function t(){class a{}return[a,a]}", id="body-level-class-renamed"
        ),
        pytest.param(
            "function t(){var g=1;try{throw 0}catch(e){function g(){}}return typeof g}",
            "function t(){var g=1;try{throw 0}catch(a){function g(){}}return typeof g}",
            id="catch-block-outer-var-kept",
        ),
        pytest.param(
            "function t(){var g=1;try{throw 0}catch{function g(){}}return typeof g}",
            "function t(){var g=1;try{throw 0}catch{function g(){}}return typeof g}",
            id="catch-block-without-binding",
        ),
        pytest.param(
            "function t(){var g=1;if(1)function g(){}return typeof g}",
            "function t(){var g=1;if(1)function g(){}return typeof g}",
            id="if-clause-outer-var-kept",
        ),
        pytest.param(
            "function t(){var g=1;if(0);else function g(){}return typeof g}",
            "function t(){var g=1;if(0);else function g(){}return typeof g}",
            id="else-clause-outer-var-kept",
        ),
        # a catch block scopes its own let like any block, so the binding renames instead of resolving outward
        pytest.param(
            "function t(){try{throw 0}catch(e){let q=h();return[q,q]}}",
            "function t(){try{throw 0}catch(b){let a=h();return[a,a]}}",
            id="catch-block-let-renamed",
        ),
    ],
)
def test_annex_b_block_function(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("{function g(){return 1}}console.log(typeof g)", id="top-level"),
        pytest.param("function t(){if(1){function g(){return 2}}return g()}console.log(t())", id="if-block"),
        pytest.param("function t(){{function g(){return 1}g()}return typeof g}console.log(t())", id="used-in-block"),
        pytest.param(
            "function t(){{function g(){return 1}}{function g(){return 2}}return g()}console.log(t())",
            id="later-block-wins",
        ),
        pytest.param("function t(){var g=1;{function g(){}}return typeof g}console.log(t())", id="outer-var"),
        pytest.param(
            "function o(){var g=5;function t(){{function g(){}}return g}return typeof t()}console.log(o())",
            id="outer-function-binding",
        ),
        pytest.param(
            "function f(){var g=1;try{throw 0}catch(e){function g(){}}return typeof g}console.log(f())",
            id="catch-block-outer-var",
        ),
        pytest.param(
            "function f(){var g=1;try{throw 0}catch(e){function g(){return 2}}return g()}console.log(f())",
            id="catch-block-outer-var-called",
        ),
        pytest.param(
            "function o(){var g=5;function t(){try{throw 0}catch(e){function g(){}}return g}return typeof t()}"
            "console.log(o())",
            id="catch-block-outer-function-binding",
        ),
        pytest.param("function f(){var g=1;if(1)function g(){}return typeof g}console.log(f())", id="if-clause"),
        pytest.param(
            "function f(){var g=1;if(0);else function g(){}return typeof g}console.log(f())", id="else-clause"
        ),
    ],
)
def test_annex_b_block_function_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("function f(param){function param(){}}", "function f(a){function a(){}}", id="param-unread"),
        pytest.param(
            "function f(param){function param(){}return param}",
            "function f(a){function a(){}return a}",
            id="param-read-once",
        ),
        pytest.param(
            "function f(param){return param;function param(){}}",
            "function f(a){return a;function a(){}}",
            id="param-read-before-declaration",
        ),
        pytest.param("function f(...rest){function rest(){}}", "function f(...a){function a(){}}", id="rest-param"),
        pytest.param(
            "function f({key}){function key(){}}", "function f({key:a}){function a(){}}", id="destructured-param"
        ),
        pytest.param("var g=param=>{function param(){}}", "var g=a=>{function a(){}}", id="arrow-param"),
        pytest.param("class C{m(param){function param(){}}}", "class C{m(a){function a(){}}}", id="method-param"),
        pytest.param(
            "function f(){var [item]=[];function item(){}}",
            "function f(){var [a]=[];function a(){}}",
            id="destructured-var-unread",
        ),
        pytest.param(
            "function f(){var [item]=[];function item(){}return[item,item]}",
            "function f(){var [a]=[];function a(){}return[a,a]}",
            id="destructured-var-read-twice",
        ),
        pytest.param(
            "function f(){for(var [item] of list);function item(){}}",
            "function f(){for(var [a] of list);function a(){}}",
            id="for-of-destructured-var",
        ),
    ],
)
def test_function_declaration_shares_binding(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("function f(a){function a(){}return typeof a}console.log(f(1))", id="param"),
        pytest.param("function f(a){'use strict';function a(){}return typeof a}console.log(f(1))", id="strict"),
        pytest.param(
            "function f(a=1,g=()=>a){function a(){}return[typeof a,g()]}console.log(f())", id="default-param-scope"
        ),
        pytest.param("function f(a){function a(){}return typeof arguments[0]}console.log(f(1))", id="arguments-alias"),
        pytest.param("function f(a){function a(){}a=5;return a}console.log(f(1))", id="reassigned"),
        pytest.param("function f(){var [a]=[2];function a(){}return[a,a]}console.log(f())", id="destructured-var"),
        pytest.param("function f(){var [a]=[];function a(){}return typeof a}console.log(f())", id="var-hole"),
    ],
)
def test_function_declaration_shares_binding_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("var a;function f(x){a++}", "var a;function f(b){a++}", id="top-level-var-written-inside"),
        pytest.param(
            "var a=1;function f(x,y){return a+x+y}", "var a=1;function f(c,b){return a+c+b}", id="top-level-var-read"
        ),
        pytest.param(
            "var a=1;function f(x){return function(y){return a+x+y}}",
            "var a=1;function f(b){return function(c){return a+b+c}}",
            id="read-two-scopes-down",
        ),
        pytest.param("let a=1;{let x=g();h(a,x,x)}", "let a=1;{let b=g();h(a,b,b)}", id="top-level-block"),
        pytest.param("const a=1;function f(x){return[a,x]}", "const a=1;function f(b){return[a,b]}", id="const"),
        pytest.param("var a=1;var f=x=>a+x", "var a=1,f=b=>a+b", id="arrow-param"),
        # a label is its own namespace, so its name stays free for a binding
        pytest.param(
            "function f(x){a:for(;;)break a;return x}", "function f(a){a:for(;;)break a;return a}", id="label"
        ),
        pytest.param(
            "function f(x){try{throw 1}catch(a){var a=2;return x}}",
            "function f(b){try{throw 1}catch(a){var a=2;return b}}",
            id="pinned-catch-parameter",
        ),
        # a var in a catch body that names something else is an ordinary local
        pytest.param(
            "function f(){try{throw 1}catch(e){var longName=g();return[longName,e,longName]}}",
            "function f(){try{throw 1}catch(b){var a=g();return[a,b,a]}}",
            id="catch-body-var-renamed",
        ),
    ],
)
def test_rename_avoids_kept_names(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("try{}catch(a){var a}console.log(a)", id="top-level"),
        pytest.param("function f(){try{throw 1}catch(a){var a=2}return a}", id="initializer"),
        pytest.param("function f(){try{throw 1}catch(a){var [,a]=[1,2]}return a}", id="array-hole"),
        pytest.param("function f(){try{throw 1}catch(a){var [a=2]=[]}return a}", id="array-default"),
        pytest.param("function f(){try{throw 1}catch(a){var [...a]=[]}return a}", id="array-rest"),
        pytest.param("function f(){try{throw 1}catch(a){var {a}={a:2}}return a}", id="object-shorthand"),
        pytest.param("function f(){try{throw 1}catch(a){var {k:a}={k:2}}return a}", id="object-key"),
        pytest.param("function f(){try{throw 1}catch(a){var {...a}={}}return a}", id="object-rest"),
        pytest.param("function f(){try{throw 1}catch(a){try{throw 2}catch(b){var a=3}}return a}", id="outer-catch"),
    ],
)
def test_catch_parameter_redeclared_by_var_kept(source: str) -> None:
    assert minify_js(source) == source


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "options", [pytest.param(JSMinify(), id="fold"), pytest.param(JSMinify(fold=False), id="no-fold")]
)
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("var a;function f(x){a++}f();console.log(a)", id="top-level-var-written-inside"),
        pytest.param("var a=1;function f(x,y){return a+x+y}console.log(f(2,3))", id="top-level-var-read"),
        pytest.param("let a=1;{let x=g();console.log(a,x,x)}function g(){return 2}", id="top-level-block"),
        pytest.param("try{}catch(a){var a}console.log(a)", id="catch-var-top-level"),
        pytest.param(
            "function f(){var r=[];try{throw 1}catch(a){var a=2;r.push(a)}r.push(a);return r.join()}console.log(f())",
            id="catch-var-initializer",
        ),
        pytest.param(
            "function f(p){try{throw 1}catch(a){var a=2}return[a,p].join()}console.log(f(9))", id="catch-var-with-param"
        ),
        pytest.param("function f(){try{throw 1}catch(a){var [a]=[2]}return a}console.log(f())", id="catch-var-pattern"),
        pytest.param("function f(){try{throw 1}catch(e){const q=[e];return q}}console.log(f())", id="catch-const"),
    ],
)
def test_kept_names_preserve_behavior(snippet: str, options: JSMinify) -> None:
    assert _run(snippet) == _run(minify_js(snippet, options))


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
