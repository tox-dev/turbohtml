"""Peephole folding: exact rewrites plus a behavior differential under Node.

The folds run on the full-minify path (``minify_js``). They rewrite value-position
boolean and undefined literals into their shorter equivalents as real AST nodes, so
the printer re-parenthesises by precedence; property names, object keys and shadowed
bindings are left alone. ``test_folding_preserves_behavior`` executes each snippet and
its minified form under Node and asserts identical output.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404

import pytest

from turbohtml import JSMinify, minify_js

_NODE = shutil.which("node")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("x=true", "x=!0", id="true"),
        pytest.param("x=false", "x=!1", id="false"),
        pytest.param("x=a?true:false", "x=a?!0:!1", id="ternary"),
        pytest.param("x=true.toString()", "x=(!0).toString()", id="bool-member-parenthesized"),
        pytest.param("x={true:1,get false(){}}", "x={true:1,get false(){}}", id="bool-keys-kept"),
        pytest.param("x=a.true", "x=a.true", id="bool-property-kept"),
        pytest.param("x=undefined", "x=void 0", id="undefined"),
        pytest.param("x=undefined.y", "x=(void 0).y", id="undefined-member-parenthesized"),
        pytest.param("x=a.undefined", "x=a.undefined", id="undefined-property-kept"),
        pytest.param("var undefined=5;x=undefined", "var undefined=5;x=undefined", id="undefined-shadowed-kept"),
        # dead-code elimination drops a constant-condition branch, but only when the dropped
        # branch hoists nothing (a var or function declaration), checked across every control-flow
        # shape; an impure condition is left untouched
        pytest.param("null?1:2", "2", id="dce-null-falsy"),
        pytest.param("x?1:2", "x?1:2", id="dce-impure-cond-kept"),
        pytest.param("void 0?1:2", "2", id="dce-void-pure-falsy"),
        pytest.param("void x?1:2", "void x?1:2", id="dce-void-impure-kept"),
        pytest.param("if(0){class C{}}b", "b", id="dce-class-no-hoist-dropped"),
        # `undefined` folds to `void 0` only when nothing declares the name; a class named
        # `undefined` blocks it, while any other class lets the fold through
        pytest.param("class undefined{}x=undefined", "class undefined{}x=undefined", id="class-shadows-undefined"),
        pytest.param("class C extends D{}x=undefined", "class C extends D{}x=void 0", id="class-not-undefined-folds"),
        # conditional algebra (13.14): a repeated name test collapses to a logical, identical branches to
        # the branch, and two same-target assignments merge; an impure test or a non-ident target is kept
        pytest.param("x=a?a:b", "x=a||b", id="cond-self-or"),
        pytest.param("x=a?b:a", "x=a&&b", id="cond-self-and"),
        # a free-name test can throw a ReferenceError, so `a?b:b` may not drop to `b` here (#435); the
        # drop applies only to a resolvable binding, gated behind the mangler -- see test_mangle.py
        pytest.param("x=a?b:b", "x=a?b:b", id="cond-same-branches-free-test-kept"),
        pytest.param("x=cond?(t=1):(t=2)", "x=t=cond?1:2", id="cond-assign-merge"),
        pytest.param("x=g()?c:c", "x=g()?c:c", id="cond-impure-test-kept"),
        pytest.param("x=a?t+=1:t+=2", "x=a?t+=1:t+=2", id="cond-compound-assign-kept"),
        pytest.param("x=a?t=1:t+=2", "x=a?t=1:t+=2", id="cond-mixed-assign-kept"),
        pytest.param("x=a?o.p=1:o.p=2", "x=a?o.p=1:o.p=2", id="cond-member-target-kept"),
        pytest.param("x=a?t=1:u=2", "x=a?t=1:u=2", id="cond-diff-target-kept"),
        # same-op short-circuit chains left-rotate to drop the parens (13.13: same operands, order,
        # and result either way); a mixed-op pair keeps its grouping
        pytest.param("x=a&&(b&&c)", "x=a&&b&&c", id="and-chain-rotates"),
        pytest.param("x=a||(b||c)", "x=a||b||c", id="or-chain-rotates"),
        pytest.param("x=a??(b??c)", "x=a??b??c", id="nullish-chain-rotates"),
        pytest.param("x=a&&(b&&(c&&d))", "x=a&&b&&c&&d", id="and-chain-rotates-deep"),
        pytest.param("x=a&&((b&&c)&&d)", "x=a&&b&&c&&d", id="and-chain-rotates-left-nested"),
        pytest.param("x=a&&(b||c)", "x=a&&(b||c)", id="mixed-op-kept"),
        pytest.param("x=a??(b&&c)", "x=a??(b&&c)", id="nullish-and-mix-kept"),
        pytest.param("if(a)b&&c", "a&&b&&c", id="if-chain-rotates"),
        pytest.param("x=a?a:b||c", "x=a||b||c", id="cond-self-or-chain-rotates"),
        # same-type operands make loose equality strict (7.2.14 step 1), so === weakens to ==; an
        # operand of unknown static type (a name, member, or literal against a name) keeps ===
        pytest.param('x=typeof a==="string"', 'x=typeof a=="string"', id="eq-typeof-string-weakens"),
        pytest.param('x=typeof a!=="undefined"', 'x=typeof a!="undefined"', id="ne-typeof-string-weakens"),
        pytest.param("x=typeof a===typeof b", "x=typeof a==typeof b", id="eq-typeof-typeof-weakens"),
        pytest.param("x=!a===!b", "x=!a==!b", id="eq-bang-bang-weakens"),
        pytest.param("x=void a===void b", "x=void a==void b", id="eq-void-void-weakens"),
        pytest.param('x=a==="s"', 'x=a==="s"', id="eq-unknown-string-kept"),
        pytest.param("x=a===1", "x=a===1", id="eq-unknown-number-kept"),
        pytest.param("x=a===null", "x=a===null", id="eq-null-kept"),
        pytest.param("x=a===void 0", "x=a===void 0", id="eq-undefined-kept"),
        pytest.param("x=1===a", "x=1===a", id="eq-number-unknown-kept"),
        # -a coerces to an unknown numeric-or-bigint and delete yields its own boolean, but neither
        # is a shape static_type vouches for, so both keep ===
        pytest.param("x=-a===-b", "x=-a===-b", id="eq-minus-unary-kept"),
        pytest.param("x=delete a.b===c", "x=delete a.b===c", id="eq-delete-kept"),
        # negation is applied or removed wherever it nets bytes: !(a==b) is exactly a!=b (7.2.13/
        # 7.2.14, both booleans), a test position absorbs a ! by swapping branches or flipping the
        # connective, and && / || De-Morgan when both sides negate for free; !(a<b) stays (NaN)
        pytest.param("x=!(a==b)", "x=a!=b", id="not-eq-flips"),
        pytest.param("x=!(a!==b)", "x=a===b", id="not-strict-ne-flips"),
        pytest.param("x=!(a<b)", "x=!(a<b)", id="not-relational-kept"),
        pytest.param("if(!a&&!b)x()", "a||b||x()", id="demorgan-statement-test"),
        pytest.param("x=!a&&!b?1:2", "x=a||b?2:1", id="demorgan-cond-swaps"),
        pytest.param("if(!(a&&b))x()", "a&&b||x()", id="not-over-and-peels"),
        pytest.param("x=!(a!=b)", "x=a==b", id="not-ne-flips"),
        pytest.param("x=!(a===b)", "x=a!==b", id="not-strict-eq-flips"),
        pytest.param("if(!a&&!b&&c)x()", "a||b||!c||x()", id="demorgan-wraps-positive-leaf"),
        pytest.param("if(!(a??b))x()", "(a??b)||x()", id="not-over-nullish-peels"),
        pytest.param("if(!a&&b!==c)x()", "a||b===c||x()", id="demorgan-flips-strict-ne"),
        pytest.param("if(!a&&b===c)x()", "a||b!==c||x()", id="demorgan-flips-strict-eq"),
        pytest.param("if(!a&&b==c)x()", "a||b!=c||x()", id="demorgan-flips-eq"),
        pytest.param("if(!a&&b!=c)x()", "a||b==c||x()", id="demorgan-flips-ne"),
        pytest.param("if(!a&&!b&&void c)x()", "a||b||!void c||x()", id="demorgan-wraps-unary"),
        pytest.param("if(!a&&!b&&!c&&!d&&(e??f))x()", "a||b||c||d||!(e??f)||x()", id="demorgan-wraps-nullish"),
        pytest.param("if(!a&&!b&&!c&&!d&&e+f)x()", "a||b||c||d||!(e+f)||x()", id="demorgan-wraps-binary"),
        # past 16 fresh bangs the negation is skipped rather than half-applied (the wrap pool cap)
        pytest.param(
            "if(" + "&&".join(f"!n{i}" for i in range(18)) + "&&" + "&&".join(f"p{i}" for i in range(17)) + ")x()",
            "&&".join(f"!n{i}" for i in range(18)) + "&&" + "&&".join(f"p{i}" for i in range(17)) + "&&x()",
            id="demorgan-wrap-cap-skips",
        ),
        pytest.param(
            'function f(){if(typeof a!="object"&&!b(c))return;g();h()}',
            'function f(){(typeof a=="object"||b(c))&&(g(),h())}',
            id="guard-negation-distributes",
        ),
        # typeof X compared with "undefined" collapses to a strict undefined check (13.5.3), unless
        # X is a bare name whose ReferenceError the typeof suppresses
        pytest.param('x=typeof a.b!="undefined"', "x=void 0!==a.b", id="typeof-undefined-ne"),
        pytest.param('x=typeof a.b!=="undefined"', "x=void 0!==a.b", id="typeof-undefined-strict-ne"),
        pytest.param('x=typeof a.b=="undefined"', "x=void 0===a.b", id="typeof-undefined-eq"),
        pytest.param('x="undefined"===typeof a.b', "x=void 0===a.b", id="typeof-undefined-flipped"),
        pytest.param('x=typeof a[i]=="undefined"', "x=void 0===a[i]", id="typeof-undefined-index"),
        pytest.param('x=typeof f()=="undefined"', "x=void 0===f()", id="typeof-undefined-call"),
        pytest.param('x=typeof a!="undefined"', 'x=typeof a!="undefined"', id="typeof-undefined-bare-name-kept"),
        pytest.param("x=typeof a.b==c", "x=typeof a.b==c", id="typeof-nonstring-side-kept"),
        pytest.param('x=typeof a.b=="undef"', 'x=typeof a.b=="undef"', id="typeof-short-string-kept"),
        pytest.param('x=typeof a.b=="Undefined"', 'x=typeof a.b=="Undefined"', id="typeof-case-mismatch-kept"),
        # a consequent that always jumps makes the else keyword redundant (14.6.2: the alternate
        # runs exactly when the test was falsy either way), so the alternate joins the chain; a
        # falling-through consequent and Annex-B `else function` keep their else
        pytest.param("function f(){if(a)throw e;else g()}", "function f(){if(a)throw e;g()}", id="abrupt-else-spliced"),
        pytest.param("while(x){if(a)break;else g()}", "for(;x&&!a;)g()", id="abrupt-break-else-spliced"),
        pytest.param(
            "function f(){if(a)return 1;else if(b)return 2;else g();h()}",
            "function f(){if(a)return 1;if(b)return 2;g(),h()}",
            id="abrupt-else-chain-spliced",
        ),
        pytest.param(
            "function f(){if(a){g();return 1}else h()}",
            "function f(){if(a)return g(),1;h()}",
            id="abrupt-block-else-spliced",
        ),
        pytest.param(
            "function f(){if(a)return;else function q(){}q()}",
            "function f(){if(a)return;else function q(){}q()}",
            id="annex-b-else-function-kept",
        ),
        pytest.param("if(a)g();else{var x=1;h(x)}", "if(a)g();else{var x=1;h(x)}", id="multi-stmt-else-block-kept"),
        # a tail return of undefined is redundant (10.2.1.4: falling off the end returns undefined);
        # a valued or non-tail return, and `delete` (valued, not void), stay
        pytest.param("function f(){g();return}", "function f(){g()}", id="tail-bare-return-dropped"),
        pytest.param("function f(){return}", "function f(){}", id="tail-only-return-dropped"),
        pytest.param("function f(){return void g()}", "function f(){g()}", id="tail-return-void-unwrapped"),
        pytest.param("function f(){h();return void g()}", "function f(){h(),g()}", id="tail-return-void-seq"),
        pytest.param("function f(){return undefined}", "function f(){}", id="tail-return-undefined-dropped"),
        pytest.param("function f(){h();return void 0}", "function f(){h()}", id="tail-return-pure-seq-unwraps"),
        pytest.param("function f(){a();b();return void 0}", "function f(){a(),b()}", id="tail-return-pure-seq-keeps"),
        pytest.param("var f=()=>{return void g()}", "var f=()=>{g()}", id="tail-return-void-arrow"),
        pytest.param(
            "function f(){while(a)b();return void 0}",
            "function f(){for(;a;)b()}",
            id="tail-return-pure-after-loop-dropped",
        ),
        pytest.param("function f(){return g()}", "function f(){return g()}", id="tail-valued-return-kept"),
        pytest.param(
            "function f(){return delete o.p}", "function f(){return delete o.p}", id="tail-return-delete-kept"
        ),
        pytest.param(
            "function f(){return void g(),h()}", "function f(){return void g(),h()}", id="tail-seq-valued-kept"
        ),
        pytest.param(
            "function f(){for(;;){return void g()}}",
            "function f(){for(;;)return void g()}",
            id="loop-nested-return-void-kept",
        ),
    ],
)
def test_folds(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        # fold alone (mangle off) keeps unreachable code after a return when it hoists a var: the hoist
        # check descends every control-flow shape and short-circuits across both branch slots. The full
        # pipeline goes further and drops the binding (see test_compresses); here fold must not.
        pytest.param("function f(){return;if(a)var x}", id="unreach-if-then"),
        pytest.param("function f(){return;if(a){}else var y}", id="unreach-if-else"),
        pytest.param("function f(){return;for(var i=0;;){}}", id="unreach-for-init"),
        pytest.param("function f(){return;for(;;)var x}", id="unreach-for-body"),
        pytest.param("function f(){return;for(var k in o){}}", id="unreach-forin-bind"),
        pytest.param("function f(){return;for(k in o)var x}", id="unreach-forin-body"),
    ],
)
def test_fold_keeps_unreachable_that_hoists(source: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # adjacent same-kind declarations merge; a different kind or a gap does not
        pytest.param("var a=1;var b=2", "var a=1,b=2", id="var-merge"),
        pytest.param("let a=1;const b=2", "let a=1;const b=2", id="decl-kind-mismatch-kept"),
        pytest.param("var a=1;f();var b=2", "var a=1;f();var b=2", id="decl-not-adjacent"),
        # if -> logical / conditional (14.6, 13.13, 13.14); a !x test drops the negation
        pytest.param("if(a)b()", "a&&b()", id="if-and"),
        pytest.param("if(!a)b()", "a||b()", id="if-not-or"),
        pytest.param("if(a){b()}", "a&&b()", id="if-block-flatten"),
        pytest.param("if(a)b();else c()", "a?b():c()", id="if-else-cond"),
        pytest.param("if(!a)b();else c()", "a?c():b()", id="if-not-else-flip"),
        pytest.param("if(a){b()}else{c()}", "a?b():c()", id="if-else-blocks-flatten"),
        pytest.param("if(a){b();c()}", "a&&(b(),c())", id="if-multi-stmt-block-merges"),
        pytest.param("if(a){var b;c()}", "if(a){var b;c()}", id="if-multi-stmt-block-kept"),
        pytest.param("if(a){}else b()", "a||b()", id="if-empty-then-to-or"),
        pytest.param("if(a);else b()", "a||b()", id="if-empty-stmt-then-to-or"),
        pytest.param("if(a)b();else{}", "a&&b()", id="if-empty-else-dropped"),
        pytest.param("if(a)b();else{;}", "a&&b()", id="if-empty-stmt-else-dropped"),
        pytest.param("if(!a){}else b()", "a&&b()", id="if-empty-then-neg-to-and"),
        pytest.param("if(a){}else{}", "if(a){}", id="if-both-empty-guard-kept"),
        # an empty then with a non-expression else is left intact (nothing shorter to fold to)
        pytest.param("if(a){}else{var x=1}", "if(a){}else var x=1", id="if-empty-then-nonexpr-else-kept"),
        pytest.param("if(a){}else{for(;;);var z}", "if(a){}else{for(;;);var z}", id="if-empty-then-multi-else-kept"),
        pytest.param("function f(){if(a){return 1}return 2}", "function f(){return a?1:2}", id="guard-block-return"),
        # guard clause -> conditional return; cascades right
        pytest.param("function f(){if(a)return 1;return 2}", "function f(){return a?1:2}", id="guard-return"),
        pytest.param(
            "function f(){if(a)return 1;if(b)return 2;return 3}",
            "function f(){return a?1:b?2:3}",
            id="guard-cascade",
        ),
        # sequence merge (13.16); a directive prologue is never swept in; nested sequences flatten
        pytest.param("a();b();c()", "a(),b(),c()", id="seq-merge"),
        pytest.param("a,b;c,d;e", "a,b,c,d,e", id="seq-flatten"),
        pytest.param('function f(){"use strict";a();b()}', 'function f(){"use strict";a(),b()}', id="directive-kept"),
        pytest.param("function f(){a();b();return c}", "function f(){return a(),b(),c}", id="seq-into-return"),
        # double negation in a conditional test peels fully
        pytest.param("x=!!a?1:0", "x=a?1:0", id="double-negation"),
        # a["x"] -> a.x and {"x":1} -> {x:1} for a bare-identifier key; otherwise the quotes stay
        pytest.param("x=a['x']", "x=a.x", id="member-to-dot"),
        pytest.param("x=a['aB_$9']", "x=a.aB_$9", id="member-to-dot-charset"),
        pytest.param("x=a?.['x']", "x=a?.x", id="optional-member-to-dot"),
        pytest.param("x=a['1x']", "x=a['1x']", id="member-digit-start-kept"),
        pytest.param("x=a['a-b']", "x=a['a-b']", id="member-non-ident-kept"),
        pytest.param("x={'k':1}", "x={k:1}", id="key-unquote"),
        pytest.param("x={'__proto__':1}", "x={'__proto__':1}", id="key-proto-kept"),
        pytest.param("x={'abcdefghi':1}", "x={abcdefghi:1}", id="key-nine-char-non-proto"),
        pytest.param("x={'a-b':1}", "x={'a-b':1}", id="key-non-ident-kept"),
        # exact integer arithmetic and string concatenation fold (13.15); the cases that could
        # round, go negative, coerce or lose an escape are left untouched
        pytest.param("x=1+2", "x=3", id="int-add"),
        pytest.param("x=2+3*4", "x=14", id="int-precedence-cascade"),
        pytest.param("x=7-3", "x=4", id="int-sub"),
        pytest.param("x=3-5", "x=3-5", id="int-sub-negative-kept"),
        pytest.param("x=1.5+2", "x=1.5+2", id="float-add-kept"),
        pytest.param("x=1000+0", "x=1e3", id="int-add-then-canon"),
        pytest.param('x="a"+"b"', 'x="ab"', id="string-concat"),
        pytest.param("x='a'+'b'", "x='ab'", id="string-concat-single-quote"),
        pytest.param('x=1+"a"', 'x=1+"a"', id="mixed-add-kept"),
        pytest.param("x=1e3+2", "x=1e3+2", id="exponent-operand-kept"),
        pytest.param("x=9999999999999999+1", "x=9999999999999999+1", id="oversize-int-kept"),
        pytest.param("x=0*7", "x=0", id="int-mul-zero"),
        pytest.param("x=99999999*99999999", "x=99999999*99999999", id="int-mul-overflow-kept"),
        pytest.param('x="a"+5', 'x="a"+5', id="string-plus-number-kept"),
        pytest.param("x=\"a\"+'b'", "x=\"a\"+'b'", id="concat-mixed-quote-kept"),
        # more folded literals than the program's initial owned-buffer capacity, forcing it to grow
        pytest.param(
            "x=[" + ",".join(f"{index}+1" for index in range(18)) + "]",
            "x=[" + ",".join(str(index + 1) for index in range(18)) + "]",
            id="many-folded-literals",
        ),
        # a single-use local const with a literal initializer inlines and the declaration drops; a
        # const read twice, with a non-literal init, or at the (observable) top level is kept
        pytest.param("function f(){const x=1;return x}", "function f(){return 1}", id="const-inline"),
        pytest.param(
            "function f(){const x='a';return x.length}", "function f(){return'a'.length}", id="const-inline-string"
        ),
        pytest.param(
            "function f(){const x=/r/;return x.test(s)}", "function f(){return/r/.test(s)}", id="const-inline-regex"
        ),
        pytest.param("function f(){const x=true;return x}", "function f(){return!0}", id="const-inline-bool"),
        pytest.param("function f(){const x=1;return ()=>x}", "function f(){return()=>1}", id="const-inline-closure"),
        pytest.param("function f(){const x=42;return x*2}", "function f(){return 84}", id="const-inline-then-fold"),
        pytest.param("function f(){for(const x=5;c;)h(x)}", "function f(){for(;c;)h(5)}", id="const-inline-for-init"),
        pytest.param("function f(){const x=1;return x+x}", "function f(){return 2}", id="const-twice-inlines"),
        # every read of a short never-written value literal inlines when the copies cost less than
        # the binding (N*(len-1) < 3+len); long literals with several reads keep the name, a regex
        # read that could re-evaluate (loop or closure) keeps the shared object, and delete /
        # redeclaration count as writes that veto every inline
        pytest.param(
            "function f(){var a=1,b=2;return function(x){return x&a?a:b}}",
            "function f(){return function(a){return a&1?1:2}}",
            id="tiny-literals-propagate-all-reads",
        ),
        pytest.param(
            "function f(){var a=1,b=2;return function(x){return[a,a,b,b]}}",
            "function f(){return function(a){return[1,1,2,2]}}",
            id="two-bindings-propagate-in-one-pass",
        ),
        pytest.param(
            'function f(){var t="[object Foo]";return function(x){return q(x)==t||r(x)==t}}',
            'function f(){var a="[object Foo]";return function(b){return q(b)==a||r(b)==a}}',
            id="long-literal-twice-kept",
        ),
        pytest.param(
            "function f(){var r=/a/g;return function(s){return r.test(s)}}",
            "function f(){var a=/a/g;return function(b){return a.test(b)}}",
            id="regex-into-closure-kept",
        ),
        pytest.param(
            'const r=/a/g;for(var i=0;i<3;i++)q(r.test("aa"))',
            'const r=/a/g;for(var i=0;i<3;i++)q(r.test("aa"))',
            id="regex-into-loop-kept",
        ),
        pytest.param(
            "function f(){var x=1;var x=2;return x}",
            "function f(){var a=1,a=2;return a}",
            id="var-redeclare-kept",
        ),
        pytest.param(
            "function f(){var x=1;return delete x}",
            "function f(){var a=1;return delete a}",
            id="delete-operand-kept",
        ),
        pytest.param(
            "function f(){var x=1;return function(){return[{x},x]}}",
            "function f(){return function(){return[{x:1},1]}}",
            id="propagation-expands-shorthand",
        ),
        pytest.param(
            "function f(){var x=1,o=g();return function(){return[{o},x,x]}}",
            "function f(){var a=g();return function(){return[{o:a},1,1]}}",
            id="propagation-skips-other-shorthand",
        ),
        pytest.param(
            "function f(){var a=1,b=g();return function(){return a+a+b}}",
            "function f(){var a=g();return function(){return 2+a}}",
            id="propagation-unlinks-first-declarator",
        ),
        pytest.param(
            "function f(){var q=g(),a=1,z=h();return function(){return a+a+q+z}}",
            "function f(){var b=g(),a=h();return function(){return 2+b+a}}",
            id="propagation-unlinks-middle-declarator",
        ),
        pytest.param(
            "function f(){delete q;var x=1;return x}", "function f(){return delete q,1}", id="delete-free-name"
        ),
        pytest.param(
            "function f(){var r=/a/;g(q);while(c)r.test(s)}",
            "function f(){var a=/a/;for(g(q);c;)a.test(s)}",
            id="regex-not-in-next-expr-kept",
        ),
        pytest.param(
            "function f(){var r=/a/;throw g(w);function g(x){return r.test(x)}}",
            "function f(){var a=/a/;throw function(b){return a.test(b)}(w)}",
            id="regex-next-throw-without-read-kept",
        ),
        pytest.param(
            "function f(){q=r;var r=/a/;throw w}", "function f(){q=a;var a=/a/;throw w}", id="regex-pre-decl-read-kept"
        ),
        pytest.param("function f(){q=r;var r=/a/}", "function f(){q=a;var a=/a/}", id="regex-last-statement-kept"),
        pytest.param(
            "function f(){var r=/a/;switch(c){case 1:return r.test(s)}}",
            "function f(){var a=/a/;switch(c){case 1:return a.test(s)}}",
            id="regex-into-switch-kept",
        ),
        pytest.param(
            "function f(){var r=/a/;if(c){var q}g(r.test(s),q)}",
            "function f(){var b=/a/;if(c)var a;g(b.test(s),a)}",
            id="regex-past-if-kept",
        ),
        pytest.param(
            "function f(){var x=1;return function(){return{k:x,m:x}}}",
            "function f(){return function(){return{k:1,m:1}}}",
            id="propagation-into-plain-props",
        ),
        pytest.param(
            "function f(){var {q}=o,x=1;return function(){return x+x+q}}",
            "function f(){var {q:a}=o;return function(){return 2+a}}",
            id="propagation-skips-destructuring-sibling",
        ),
        pytest.param(
            "function f(){var x;return function(){return[x,x]}}",
            "function f(){var a;return function(){return[a,a]}}",
            id="propagation-bare-var-kept",
        ),
        pytest.param(
            "function f(){var a=x+x,x=1;return a}",
            "function f(){var b=a+a,a=1;return b}",
            id="propagation-earlier-declarator-reads-kept",
        ),
        pytest.param(
            "function f(){g();var x=1;return function(){return x+x}}",
            "function f(){g();var a=1;return function(){return a+a}}",
            id="propagation-later-statement-kept",
        ),
        pytest.param(
            "function f(){var x=!0;return function(){return[x,x]}}",
            "function f(){return function(){return[!0,!0]}}",
            id="propagation-bang-literal",
        ),
        pytest.param(
            "function f(){var x=undefined;return function(){return[x,x]}}",
            "function f(){var a=void 0;return function(){return[a,a]}}",
            id="propagation-void-too-long",
        ),
        # a regex moves only into the very next statement's expression (at most one evaluation per
        # pass over the declaration; an expression cannot loop) -- never past it or into a loop
        pytest.param("function f(){g();var x=!0;return h(),x}", "function f(){return g(),h(),!0}", id="seq-tail-bang"),
        pytest.param(
            "function f(){var r=/a/;q(r.test(s));h()}", "function f(){q(/a/.test(s)),h()}", id="regex-next-expr"
        ),
        pytest.param(
            "function f(){var r=/a/;throw r.test(s)}", "function f(){throw/a/.test(s)}", id="regex-next-throw"
        ),
        pytest.param(
            "function f(){var r=/a/;if(c)g();return r.test(s)}",
            "function f(){return c&&g(),/a/.test(s)}",
            id="regex-rides-merged-return",
        ),
        pytest.param(
            "function f(){var r=/a/;while(c)h();return r.test(s)}",
            "function f(){for(var a=/a/;c;)h();return a.test(s)}",
            id="regex-past-loop-kept",
        ),
        # a non-literal single use collapses only when its declaration immediately precedes the use as a
        # whole `return`/`throw` value (nothing runs between, no closure captures it); otherwise it stays
        pytest.param("function f(){const x=g();return x}", "function f(){return g()}", id="nonliteral-return-collapse"),
        pytest.param("function f(){var x=a.b;throw x}", "function f(){throw a.b}", id="nonliteral-throw-collapse"),
        pytest.param("function f(){const x=g();h(x)}", "function f(){const a=g();h(a)}", id="nonliteral-nonjump-kept"),
        # a binding that is written (reassigned, updated, or a destructuring / for-in assignment target) is
        # never inlined: the read/write split keeps the value off an illegal `[5]=arr` / `for(5 in o)` slot
        pytest.param(
            "function f(){let x=5;[x]=arr;return x}",
            "function f(){let a=5;return[a]=arr,a}",
            id="no-inline-destructure-write",
        ),
        pytest.param(
            "function f(){let x=5;for(x in o);}", "function f(){let a=5;for(a in o);}", id="no-inline-forin-write"
        ),
        pytest.param("function f(){var x=1;x=2;g(x)}", "function f(){var a=1;a=2,g(a)}", id="no-inline-reassigned"),
        # `x=EXPR` folded into the very next read of x -- the temporary the fold pass leaves as `(x=EXPR,x)`
        pytest.param("function f(){var r;r=g();return r}", "function f(){return g()}", id="collapse-assign-return"),
        pytest.param("function f(){var r=0;r=g();throw r}", "function f(){throw g()}", id="collapse-assign-throw"),
        pytest.param("function f(){var x=1;x=2;return x}", "function f(){return 2}", id="collapse-reassign-literal"),
        pytest.param(
            "function f(){var r;r=g();return r+1}", "function f(){var a;return a=g(),a+1}", id="collapse-not-sole-read"
        ),
        pytest.param(
            "function f(){var x;x=g(),y;return x}",
            "function f(){var a;return a=g(),y,a}",
            id="collapse-other-elem-between",
        ),
        pytest.param("function f(){a=1,b=2}", "function f(){a=1,b=2}", id="collapse-free-assign-kept"),
        pytest.param("function f(){var x;x=1;return x=2,x}", "function f(){return 2}", id="collapse-twice-written"),
        # `(t=V, t)` where t is read elsewhere drops just the redundant trailing read, keeping the assign
        pytest.param(
            "function f(a){return a=a||{},a}", "function f(a){return a=a||{}}", id="collapse-assign-value-read"
        ),
        pytest.param(
            "function f(){var x=1;x++;return x}", "function f(){var a=1;return a++,a}", id="no-inline-updated"
        ),
        pytest.param(
            "function f(){var x=1;x+=2;return x}", "function f(){var a=1;return a+=2,a}", id="no-inline-compound"
        ),
        pytest.param("function f(o){o.x+=1}", "function f(a){a.x+=1}", id="compound-assign-member"),
        pytest.param("function f(){g+=1}", "function f(){g+=1}", id="compound-assign-free"),
        pytest.param(
            "function f(){const a=1,b=2;return a+b}",
            "function f(){return 3}",
            id="const-multi-declarator-inlines",
        ),
        pytest.param(
            "function f(){const[x]=a;return x}", "function f(){const [b]=a;return b}", id="const-destructuring-kept"
        ),
        pytest.param("const x=1;f(x)", "const x=1;f(x)", id="const-global-kept"),
        # a non-`!` unary test keeps its operator; `!!` peels both negations (make_logical/make_cond)
        pytest.param("if(~a)b()", "~a&&b()", id="if-and-unary-non-not"),
        pytest.param("if(!!a)b()", "a&&b()", id="if-and-double-negation"),
        pytest.param("if(~a)b();else c()", "~a?b():c()", id="if-cond-unary-non-not"),
        # a dead branch whose alternate or loop body (not just its head) hoists is still kept;
        # one whose nested if / loop hoists nowhere is dropped (the body is scanned either way)
        pytest.param("if(0){if(q)g();else var v}f()", "if(0)if(q)g();else var v;f()", id="dead-if-alt-hoist-kept"),
        pytest.param("if(0){for(;c;)var v}f()", "if(0)for(;c;)var v;f()", id="dead-for-body-hoist-kept"),
        pytest.param("if(0){for(k in o)var v}f()", "if(0)for(k in o)var v;f()", id="dead-forin-body-hoist-kept"),
        pytest.param("if(0){if(q)while(c)g()}f()", "f()", id="dead-if-no-hoist-dropped"),
        pytest.param("if(0){for(;c;)g()}f()", "f()", id="dead-for-no-hoist-dropped"),
        pytest.param("if(0){for(k in o)g()}f()", "f()", id="dead-forin-no-hoist-dropped"),
        pytest.param("if(0){for(k of o)g()}f()", "f()", id="dead-forof-no-hoist-dropped"),
        # an object/member key with an ASCII code point above `z` is not an identifier, so quotes stay
        pytest.param("x=a['a{b']", "x=a['a{b']", id="member-above-z-kept"),
        # an unread local binding is dead code: a side-effect-free var/let/const and an unused function
        # declaration drop (ECMA-262 has no observable effect for an unread binding); a side-effecting
        # initializer, a used binding, a for-in/of loop binding and a top-level (observable) name stay
        pytest.param("function f(){var x=1;return 2}", "function f(){return 2}", id="drop-unused-var"),
        pytest.param("function f(){let x=function(){};return 2}", "function f(){return 2}", id="drop-unused-fn-expr"),
        pytest.param("function f(){const x=1;g()}", "function f(){g()}", id="drop-unused-const"),
        pytest.param("function f(){function h(){}return 2}", "function f(){return 2}", id="drop-unused-function"),
        # one dead declarator drops out of a multi-declarator statement; the statement empties only when
        # its last declarator goes, and a destructuring sibling is skipped over
        pytest.param(
            "function f(){var a=g(),x=1,b=2;return a+b}",
            "function f(){var a=g();return a+2}",
            id="drop-middle-declarator",
        ),
        pytest.param("function f(){var x=1,y=g();return y}", "function f(){return g()}", id="drop-first-declarator"),
        pytest.param(
            "function f(){var {a}=o,x=1;return a}", "function f(){var {a:a}=o;return a}", id="drop-past-destructure"
        ),
        pytest.param("function f(){var x=g();return 2}", "function f(){var a=g();return 2}", id="keep-sideeffect-init"),
        # the side-effect-free check accepts every literal kind, a function/arrow and a unary over a pure operand
        pytest.param("function f(){var x='s';return 1}", "function f(){return 1}", id="drop-unused-string"),
        pytest.param("function f(){var x=/r/;return 1}", "function f(){return 1}", id="drop-unused-regex"),
        pytest.param("function f(){var x=1n;return 1}", "function f(){return 1}", id="drop-unused-bigint"),
        pytest.param("function f(){var x=()=>1;return 2}", "function f(){return 2}", id="drop-unused-arrow"),
        pytest.param("function f(){var x=!0;return 1}", "function f(){return 1}", id="drop-unused-unary"),
        pytest.param("function f(){for(var[a]in o)g()}", "function f(){for(var [a] in o)g()}", id="keep-forin-pattern"),
        pytest.param(
            "function f(){function h(){}return h()+h()}",
            "function f(){function a(){}return a()+a()}",
            id="keep-twice-used-fn",
        ),
        # a function used once becomes an expression at the use; a use in a loop or a nested function
        # runs repeatedly and would build a fresh closure each time, so it is kept
        pytest.param(
            "function f(){function h(){return 1}return h}",
            "function f(){return function(){return 1}}",
            id="inline-single-use-fn",
        ),
        pytest.param(
            "function f(){function h(){}for(var i=0;i<3;i++)a.push(h)}",
            "function f(){function c(){}for(var b=0;b<3;b++)a.push(c)}",
            id="keep-fn-used-in-loop",
        ),
        pytest.param(
            "function f(){function h(){}g(function(){return h})}",
            "function f(){function a(){}g(function(){return a})}",
            id="keep-fn-used-in-closure",
        ),
        pytest.param("function f(){for(var k in o)g(k)}", "function f(){for(var a in o)g(a)}", id="keep-forin-binding"),
        pytest.param("function f(){return;for(;;){var x}}", "function f(){}", id="drop-unreachable-hoisted"),
        pytest.param("var g=1", "var g=1", id="keep-global-var"),
        pytest.param("with(o){var x=1;f()}", "with(o){var x=1;f()}", id="keep-under-with"),
        # a void guard-return at a function body's top level inverts to `cond||(rest)` when the rest is
        # all expressions; a valued/trailing return, a declaration in the rest, or a nested block is kept
        pytest.param("function f(a){if(a)return;g();h()}", "function f(a){a||(g(),h())}", id="guard-return-invert"),
        pytest.param("function f(a){if(a)return;g()}", "function f(a){a||g()}", id="guard-return-single"),
        pytest.param(
            "function f(a){g();if(a)return}", "function f(a){g();if(a)return}", id="guard-return-trailing-kept"
        ),
        pytest.param(
            "function f(a){if(a)return 1;g();h()}",
            "function f(a){if(a)return 1;g(),h()}",
            id="guard-return-valued-kept",
        ),
        pytest.param(
            "function f(a){if(a)return;g();var x=h();x()}",
            "function f(b){if(b)return;g();var a=h();a()}",
            id="guard-return-decl-kept",
        ),
        pytest.param(
            "function g(a){while(1){if(a)return;h()}}",
            "function g(a){for(;;){if(a)return;h()}}",
            id="guard-return-in-loop-kept",
        ),
        # a dead store whose value is impure keeps the value as an expression statement; a pure sequence
        # head is dropped; a twice-used function in a block keeps its name; a multi-declarator single use
        # is not inlined (the whole statement cannot be emptied without losing its siblings)
        pytest.param("function f(){var x;x=g()}", "function f(){g()}", id="dead-store-impure"),
        pytest.param("function f(){0,g()}", "function f(){g()}", id="seq-drop-pure-head"),
        pytest.param("function f(x){return x,g()}", "function f(a){return g()}", id="seq-drop-pure-local-head"),
        pytest.param("function f(y){return g(),y,h()}", "function f(a){return g(),h()}", id="seq-drop-pure-mid"),
        pytest.param(
            "function f(){var r;return h(),r=g(),r}", "function f(){return h(),g()}", id="seq-collapse-after-effect"
        ),
        pytest.param("function f(){var r;return r=1,r}", "function f(){return 1}", id="seq-collapse-literal"),
        # a single-use literal rides to the tail of the adjacent return/throw's comma sequence (the
        # earlier operands cannot change a never-written literal); an impure initializer would reorder
        # against them and stays, as does a read that is not the sequence's value
        pytest.param("function f(){var x=1;g();return x}", "function f(){return g(),1}", id="literal-seq-tail-inline"),
        pytest.param("function f(){var x=1;g();throw x}", "function f(){throw g(),1}", id="literal-seq-tail-throw"),
        pytest.param(
            "function f(){var x=h();g();return x}",
            "function f(){var a=h();return g(),a}",
            id="impure-seq-tail-kept",
        ),
        pytest.param(
            "function f(){var x=1;g();return x+1}",
            "function f(){return g(),2}",
            id="literal-first-statement-inlines",
        ),
        # the adjacent jump carries no value the read could be: a bare return (the closure read
        # may run before the declaration) and a non-sequence return value both keep the binding
        pytest.param(
            "function f(){if(c){q(()=>x);var x=1;return}g()}",
            "function f(){if(c){q(()=>a);var a=1;return}g()}",
            id="literal-bare-return-kept",
        ),
        pytest.param(
            "function f(){q(()=>x);var x=1;return 2}",
            "function f(){q(()=>a);var a=1;return 2}",
            id="literal-other-return-value-kept",
        ),
        pytest.param(
            "function f(){var x;x=g(),foo();return x}",
            "function f(){var a;return a=g(),foo(),a}",
            id="seq-collapse-gap-kept",
        ),
        pytest.param(
            "function f(){var x;return x=g(),x+x}",
            "function f(){var a;return a=g(),a+a}",
            id="seq-collapse-twice-read-kept",
        ),
        pytest.param(
            "function f(x){if(x){function h(){}h();h()}}",
            "function f(a){if(a){function h(){}h(),h()}}",
            id="block-function-kept",
        ),
        pytest.param(
            "function f(){var a=g(),x=1;return x}",
            "function f(){var a=g();return 1}",
            id="multi-declarator-single-use",
        ),
        # a single-use literal var in its function body's first statement dominates its one read
        # (nothing can execute before the statement), so it inlines even into a nested closure;
        # a later statement, a read in an earlier declarator's initializer, or a block-nested var
        # cannot prove domination and keep the binding
        pytest.param(
            'function f(){var t="[object Foo]";return function(x){return q(x)==t}}',
            'function f(){return function(a){return q(a)=="[object Foo]"}}',
            id="literal-first-statement-closure-inlines",
        ),
        pytest.param(
            'function f(){var a="x",b=a+"y";return function(){return b}}',
            'function f(){return function(){return"xy"}}',
            id="literal-declarator-chain-cascades",
        ),
        pytest.param(
            "function f(){g();var t=1;return function(){return t}}",
            "function f(){g();var a=1;return function(){return a}}",
            id="literal-later-statement-kept",
        ),
        pytest.param(
            "function f(){var a=(function(){return t})(),t=1;return a}",
            "function f(){var b=function(){return a}(),a=1;return b}",
            id="literal-earlier-declarator-read-kept",
        ),
        pytest.param(
            "function f(){while(c){var t=1;g(function(){return t})}}",
            "function f(){for(;c;){var a=1;g(function(){return a})}}",
            id="literal-block-nested-var-kept",
        ),
        pytest.param(
            "function f(){var a=t,t=1;return a}",
            "function f(){var b=a,a=1;return b}",
            id="literal-earlier-declarator-direct-read-kept",
        ),
        pytest.param(
            "function f(){var a=u?1:t,t=2;return a}",
            "function f(){var b=u?1:a,a=2;return b}",
            id="literal-earlier-declarator-cond-read-kept",
        ),
        pytest.param(
            "function f(){var a=function(){for(;;)g(t)},t=1;return a}",
            "function f(){var b=function(){for(;;)g(a)},a=1;return b}",
            id="literal-earlier-declarator-loop-read-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=t,t=2;return b}",
            "function f(){var c=g(),b=a,a=2;return b}",
            id="literal-second-earlier-declarator-read-kept",
        ),
        pytest.param(
            "function f(){g();var x=1;return x?u():0,h()}",
            "function f(){g();var a=1;return a?u():0,h()}",
            id="literal-seq-non-tail-read-kept",
        ),
        # while(c) canonicalizes to for(;c;) (14.7.3/14.7.4 run identically) so a preceding
        # expression statement can merge into the init slot; a truthy-constant test then drops
        # (an absent test always continues); a var init or a for-in head takes no expression
        pytest.param("while(c)b()", "for(;c;)b()", id="while-to-for"),
        pytest.param("a();while(c)b()", "for(a();c;)b()", id="expr-merges-into-for-init"),
        pytest.param("a();b();while(c)d()", "for(a(),b();c;)d()", id="seq-merges-into-for-init"),
        pytest.param("a();for(i=0;c;i++)b()", "for(a(),i=0;c;i++)b()", id="expr-joins-existing-init"),
        pytest.param("while(1)b()", "for(;;)b()", id="truthy-test-drops"),
        pytest.param("do b();while(c)", "do b();while(c)", id="do-while-kept"),
        pytest.param("a();for(var i=0;c;i++)b()", "a();for(var i=0;c;i++)b()", id="var-init-blocks-merge"),
        # a var statement becomes (or prepends) the for init -- it hoists to the function either
        # way and its initializers run in the same order right before the first test (14.7.4);
        # a let/const would be re-scoped into the head and stays put
        pytest.param("var a=1;for(;c;)b()", "for(var a=1;c;)b()", id="var-merges-into-for-init"),
        pytest.param("var a=g();for(var i=0;i<n;i++)b()", "for(var a=g(),i=0;i<n;i++)b()", id="var-joins-for-var-init"),
        pytest.param(
            "var a=g(),b=h();for(var i=0;i<n;i++)q(a,b)",
            "for(var a=g(),b=h(),i=0;i<n;i++)q(a,b)",
            id="multi-var-joins-for-var-init",
        ),
        pytest.param("var a=1;while(c)b()", "for(var a=1;c;)b()", id="var-merges-into-converted-while"),
        pytest.param("let a=1;for(;c;)b()", "let a=1;for(;c;)b()", id="let-stays-before-for"),
        pytest.param("var a=1;for(i=0;c;)b()", "var a=1;for(i=0;c;)b()", id="var-expr-init-kept"),
        pytest.param('var c="";for(let i=0;;);', 'for(var c="";;);', id="dropped-let-init-reopens-slot"),
        pytest.param("a();for(k in o)b()", "a();for(k in o)b()", id="forin-blocks-merge"),
        # a bare unlabeled break guarding a for body's top folds into the test (T then !C decide the
        # same exit at the same point; the break skipped the update exactly as a failed test does);
        # a labeled break, a later guard, or a do-while (whose body runs before the first test) stay
        pytest.param(
            "for(i=0;i<n;i++){if(f(i)===!1)break;g(i)}",
            "for(i=0;i<n&&f(i)!==!1;i++)g(i)",
            id="break-guard-into-test",
        ),
        pytest.param("for(i=0;i<n;i++)if(f(i)===!1)break", "for(i=0;i<n&&f(i)!==!1;i++);", id="break-guard-bare-body"),
        pytest.param("for(;;){if(d)break;g()}", "for(;!d;)g()", id="break-guard-no-test"),
        pytest.param("while(c){if(!d)break;g()}", "for(;c&&d;)g()", id="break-guard-not-peels"),
        pytest.param("l:for(;;){if(d)break l;g()}", "a:for(;;){if(d)break a;g()}", id="labeled-break-kept"),
        pytest.param("for(;c;){g();if(d)break}", "for(;c&&!(g(),d););", id="late-break-packs-into-test"),
        pytest.param("while(t){e();if(c)break;r()}", "for(;t&&!(e(),c);)r()", id="packed-break-guard-with-rest"),
        pytest.param("while(t){e();if(c)continue;r()}", "for(;t;)e(),c||r()", id="continue-guard-not-packed"),
        pytest.param("e();if(c){var x}else{var y}", "e();if(c)var x;else var y", id="if-else-not-packed"),
        pytest.param("do{if(d)break;g()}while(c)", "do{if(d)break;g()}while(c)", id="do-while-break-kept"),
        pytest.param("while(c){if(a==b)break;g()}", "for(;c&&a!=b;)g()", id="break-guard-eq-flips"),
        pytest.param("while(c){if(a!=b)break;g()}", "for(;c&&a==b;)g()", id="break-guard-ne-flips"),
        pytest.param("while(c){if(a!==b)break;g()}", "for(;c&&a===b;)g()", id="break-guard-strict-ne-flips"),
        pytest.param("while(c){if(-d)break;g()}", "for(;c&&!-d;)g()", id="break-guard-other-unary-wraps"),
        pytest.param("while(c){}", "for(;c;){}", id="while-empty-body"),
        pytest.param(
            "for(;c;){if(d){g();break}h()}", "for(;c;){if(d){g();break}h()}", id="break-multi-stmt-guard-kept"
        ),
        pytest.param("for(;c;){if(d)break}", "for(;c&&!d;);", id="break-only-body-empties"),
        pytest.param("while(c){if(a<b)break;g()}", "for(;c&&!(a<b);)g()", id="break-guard-relational-wraps"),
        pytest.param("for(;c;){if(d){var x}else break}", "for(;c;)if(d)var x;else break", id="break-in-else-kept"),
        pytest.param(
            "function f(){g();var x=1;h();return x}",
            "function f(){return g(),h(),1}",
            id="literal-seq-tail-later-statement",
        ),
        pytest.param(
            "function f(){var {x}=o,y=1;return x+y}",
            "function f(){var {x:a}=o;return a+1}",
            id="destructuring-sibling-skipped",
        ),
        # an expression's unread self-name drops (13.2.4/15.7.4: it binds only in its own body);
        # a self-referencing name stays, and a shorthand read gains an explicit key before an
        # inline lands in its value slot
        pytest.param("var f=function rec(n){return n*2}", "var f=function(a){return a*2}", id="nfe-unread-name-drops"),
        pytest.param("var C=class Self{m(){return 1}}", "var C=class{m(){return 1}}", id="class-unread-name-drops"),
        pytest.param("function f(){var x=1;return{x}}", "function f(){return{x:1}}", id="shorthand-inline-expands-key"),
        pytest.param(
            "function q(){function f(){}return{f}}",
            "function q(){return{f:function(){}}}",
            id="shorthand-function-inline-expands-key",
        ),
    ],
)
def test_compresses(source: str, expected: str) -> None:
    assert minify_js(source) == expected


def _run(code: str) -> str:
    assert _NODE is not None  # the callers are skipped when node is unavailable
    result = subprocess.run([_NODE, "-e", code], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    return result.stdout + result.stderr


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("console.log(true,false,!true,typeof undefined,void 0===undefined)", id="literals"),
        pytest.param("console.log([true,false,undefined].map(x=>x===void 0))", id="array"),
        pytest.param("var o={true:1,undefined:2};console.log(o.true,o.undefined)", id="keys-not-folded"),
        pytest.param("(function(undefined){console.log(undefined)})(7)", id="shadowed-undefined"),
        pytest.param("console.log(true.toString(),undefined?.x,undefined??'d')", id="member-and-operators"),
        # constant-condition dead-code elimination: short circuits, ternaries and ifs
        pytest.param("console.log(1&&'a',0&&'b',1||'c',0||'d')", id="dce-and-or"),
        pytest.param("console.log(null??'e',0??'f',1?'g':'h',0?'i':'j')", id="dce-nullish-ternary"),
        pytest.param("console.log(''&&1,'s'&&2,/r/&&3,(void 0)??4,undefined&&5)", id="dce-truthiness-kinds"),
        pytest.param("console.log(true&&false||'x',1&&2&&3)", id="dce-chained"),
        pytest.param("if(1)console.log('taken');else console.log('not')", id="dce-if-true"),
        pytest.param("if(0)console.log('not');else console.log('taken')", id="dce-if-false-else"),
        pytest.param("if(0)console.log('gone');console.log('after')", id="dce-if-false-no-else"),
        pytest.param(
            "(function(){function f(){return 7;console.log('dead')}console.log(f())})()", id="dce-unreachable-tail"
        ),
        # a dead branch that hoists a var or function must be kept, not dropped: node_hoists
        # has to descend each control-flow form to find the hoisted binding
        pytest.param("(function(){if(0){for(var i=0;i<1;i++)var a=1}console.log(typeof a)})()", id="hoist-for"),
        pytest.param("(function(){if(0){for(var k in {x:1})var b=1}console.log(typeof b)})()", id="hoist-forin"),
        pytest.param("(function(){if(0){for(var v of [1])var c=1}console.log(typeof c)})()", id="hoist-forof"),
        pytest.param("(function(){if(0){while(0)var d=1}console.log(typeof d)})()", id="hoist-while"),
        pytest.param("(function(){if(0){do var e=1;while(0)}console.log(typeof e)})()", id="hoist-dowhile"),
        pytest.param("(function(){if(0){lbl:{var g=1}}console.log(typeof g)})()", id="hoist-label"),
        pytest.param(
            "(function(){if(0){try{var h=1}catch(o){var j=1}finally{var k=1}}"
            "console.log(typeof h,typeof j,typeof k)})()",
            id="hoist-try",
        ),
        pytest.param(
            "(function(){if(0){switch(1){case 1:var p=1;default:var q=1}}console.log(typeof p,typeof q)})()",
            id="hoist-switch",
        ),
        pytest.param("(function(){if(0){with({}){var r=1}}console.log(typeof r)})()", id="hoist-with"),
        pytest.param("(function(){if(0){s();function s(){}}console.log(typeof s)})()", id="hoist-function"),
        pytest.param("(function(){if(0)var t=1;console.log(typeof t)})()", id="hoist-bare-var"),
        pytest.param(
            "(function(){if(0){if(1)var u=1;else var w=1}console.log(typeof u,typeof w)})()", id="hoist-nested-if"
        ),
        pytest.param(
            "(function(){if(0){switch(2){case 1:console.log('x')}}console.log('ok')})()", id="dead-switch-no-hoist"
        ),
        pytest.param("(function(){if(1)function af(){return 9}console.log(typeof af)})()", id="if-true-function-kept"),
        # non-`!`/`void` unary operands are not pure constants, so the short circuit stays
        pytest.param("console.log(-1&&'a',~0&&'b',typeof x&&'c')", id="dce-non-pure-unary"),
        # `undefined` must not fold to `void 0` when some binding shadows it; the shadow
        # scan has to recognize it in array, object, function, class and catch positions
        pytest.param("(function(){var [undefined]=[5];console.log(undefined)})()", id="undefined-array-bind"),
        pytest.param(
            "(function(){var {undefined}={undefined:6};console.log(undefined)})()", id="undefined-object-bind"
        ),
        pytest.param(
            "(function(){function undefined(){return 7}console.log(undefined())})()", id="undefined-function-bind"
        ),
        pytest.param("(function(){class undefined{}console.log(typeof undefined)})()", id="undefined-class-bind"),
        pytest.param("(function(){try{throw 8}catch(undefined){console.log(undefined)}})()", id="undefined-catch-bind"),
        # number truthiness across every literal form the zero-test inspects
        pytest.param("console.log(0xF&&1,0XF&&1,0o7&&1,0O7&&1,0b1&&1,0B1&&1)", id="radix-truthiness"),
        pytest.param("console.log(1e1&&1,1E1&&1,2n&&1,1.5&&1,1_0&&1,0.0&&1)", id="exponent-bigint-fraction"),
        pytest.param("console.log((void 0)&&1,(void 0)??2,void 0?1:2)", id="void-operand"),
        pytest.param("console.log((typeof x)??1,(!0)??2,null??3)", id="nullish-non-void-operands"),
        # dead branches that hoist on only one side of a control-flow node, so node_hoists
        # has to evaluate the second operand of its || rather than short-circuit
        pytest.param("(function(){if(0){if(x)g();else var y}console.log(typeof y)})()", id="hoist-if-else-only"),
        pytest.param("(function(){if(0){for(;;)var z}console.log(typeof z)})()", id="hoist-for-body-only"),
        pytest.param("(function(){if(0){for(k in {})var z}console.log(typeof z)})()", id="hoist-forin-body-only"),
        pytest.param("(function(){if(0){try{g()}catch(o){var x}}console.log(typeof x)})()", id="hoist-catch-only"),
        pytest.param(
            "(function(){if(0){try{g()}catch(o){h()}finally{var x}}console.log(typeof x)})()", id="hoist-finally-only"
        ),
        pytest.param("(function(){if(0){try{g()}catch(o){h()}}console.log('ok')})()", id="dead-try-no-hoist"),
        # `undefined` bound through an initializer, a class body and a try block, so the
        # shadow scan evaluates the later operands of its || chains
        pytest.param(
            "(function(){var q=function undefined(){};console.log(typeof q,undefined)})()", id="undefined-init-bind"
        ),
        pytest.param(
            "(function(){class X{m(){var undefined=1;return undefined}}console.log(new X().m())})()",
            id="undefined-class-body-bind",
        ),
        pytest.param(
            "(function(){class Y extends(class{m(){var undefined=1;return undefined}}){};console.log(new Y().m())})()",
            id="undefined-superclass-bind",
        ),
        pytest.param(
            "(function(){try{var undefined=5}catch(o){}console.log(undefined)})()", id="undefined-try-block-bind"
        ),
        pytest.param(
            "(function(){try{g()}catch(o){var undefined=1}console.log(undefined)})()", id="undefined-catch-block-bind"
        ),
        pytest.param(
            "(function(){try{}finally{var undefined=2}console.log(undefined)})()", id="undefined-finally-bind"
        ),
        pytest.param("(function(){if(1){var undefined=3}console.log(undefined)})()", id="undefined-block-bind"),
        pytest.param("(function(){if(0)g();else var undefined=4;console.log(undefined)})()", id="undefined-else-bind"),
        pytest.param(
            "(function(){for(var i=0;i<1;i++){var undefined=5}console.log(undefined)})()", id="undefined-for-body-bind"
        ),
        # unreachable-tail cut: a terminator that is the last statement, and a tail that hoists
        pytest.param("(function(){function f(){console.log('x');return 1}console.log(f())})()", id="terminator-last"),
        pytest.param("(function(){function f(){return 1;var x}console.log(f())})()", id="tail-hoists-kept"),
    ],
)
def test_folding_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))


_BS = chr(0x5C)  # backslash, kept out of the literals so every escape is unambiguous
_CR = chr(0x0D)
_LF = chr(0x0A)
_LS = chr(0x2028)
_PS = chr(0x2029)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # concatenation folds by the decoded value and re-encodes, so a trailing variable-length escape
        # (octal or hex) can no longer absorb the next operand's first character (#437); a control code
        # point takes a fixed-length \xHH that cannot grow
        pytest.param(r'"\1"+"2"', r'"\x012"', id="octal-then-digit"),
        pytest.param(r'"\0"+"0"', r'"\x000"', id="nul-then-digit"),
        pytest.param(r'"\101"+"b"', r'"Ab"', id="octal-three-digit"),
        pytest.param(r'"\41"+"1"', r'"!1"', id="octal-four-to-seven"),
        pytest.param(r'"\x41"+"1"', r'"A1"', id="hex-escape"),
        pytest.param(r'"\x1b"+"m"', r'"\x1bm"', id="hex-control-stays-hex"),
        pytest.param(r'"\x1A"+"z"', r'"\x1az"', id="hex-uppercase-digits"),
        # a non-octal code point right after an octal escape ends it: a char above '7' and one below '0'
        pytest.param(r'"\1a"+"z"', r'"\x01az"', id="octal-then-high-char"),
        pytest.param(r'"\1!"+"z"', r'"\x01!z"', id="octal-then-low-char"),
        pytest.param('"' + _BS + 'u0041"+"z"', r'"Az"', id="unicode-escape"),
        pytest.param('"' + _BS + 'u{1F600}"+"!"', '"\U0001f600!"', id="unicode-braced-astral"),
        pytest.param(r'"\n"+"a"', r'"\na"', id="newline"),
        pytest.param(r'"\r"+"a"', r'"\ra"', id="carriage-return"),
        pytest.param(r'"\t"+"a"', r'"\ta"', id="tab"),
        pytest.param(r'"\b"+"a"', r'"\ba"', id="backspace"),
        pytest.param(r'"\f"+"a"', r'"\fa"', id="form-feed"),
        pytest.param(r'"\v"+"a"', r'"\va"', id="vertical-tab"),
        pytest.param(r'"a\\b"+"c"', r'"a\\bc"', id="backslash"),
        pytest.param(r'"x"+"a\"b"', r'"xa\"b"', id="escaped-quote-reescaped"),
        pytest.param(r'"\q"+"z"', r'"qz"', id="unknown-escape-is-literal"),
        pytest.param('"' + _BS + 'u2028"+"a"', '"' + _BS + 'u2028a"', id="line-separator-value"),
        pytest.param('"' + _BS + 'u2029"+"a"', '"' + _BS + 'u2029a"', id="paragraph-separator-value"),
        # a LineContinuation contributes nothing to the value
        pytest.param('"a' + _BS + _LF + 'b"+"c"', r'"abc"', id="lf-continuation"),
        pytest.param('"a' + _BS + _CR + 'b"+"c"', r'"abc"', id="cr-continuation"),
        pytest.param('"a' + _BS + _CR + '"+"z"', r'"az"', id="cr-continuation-at-end"),
        pytest.param('"a' + _BS + _CR + _LF + 'b"+"c"', r'"abc"', id="crlf-continuation"),
        pytest.param('"a' + _BS + _LS + 'b"+"c"', r'"abc"', id="ls-continuation"),
        pytest.param('"a' + _BS + _PS + 'b"+"c"', r'"abc"', id="ps-continuation"),
        pytest.param(r'"ab"+"cd"', r'"abcd"', id="plain"),
    ],
)
def test_concat_reencodes_by_value(source: str, expected: str) -> None:
    assert minify_js(f"x={source}") == f"x={expected}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # truthiness reads the decoded value, so a line-continuation-only string (value "") is falsy (#436)
        pytest.param('"' + _BS + _LF + '"?1:2', "2", id="lf-only-empty-falsy"),
        pytest.param('"' + _BS + _CR + '"?1:2', "2", id="cr-only-empty-falsy"),
        pytest.param('"' + _BS + _CR + _LF + '"?1:2', "2", id="crlf-only-empty-falsy"),
        pytest.param('"' + _BS + _LS + '"?1:2', "2", id="ls-only-empty-falsy"),
        pytest.param('"' + _BS + _PS + '"?1:2', "2", id="ps-only-empty-falsy"),
        pytest.param('""?1:2', "2", id="empty-literal-falsy"),
        pytest.param(r'"a"?1:2', "1", id="nonempty-truthy"),
        pytest.param(r'"\n"?1:2', "1", id="escape-value-truthy"),
        pytest.param('"a' + _BS + _LF + '"?1:2', "1", id="continuation-plus-content-truthy"),
        pytest.param('"' + _BS + _CR + 'x"?1:2', "1", id="cr-continuation-then-content-truthy"),
    ],
)
def test_string_truthiness_reads_value(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(r'"\1"+"2"', id="octal-then-digit"),
        pytest.param(r'"\0"+"0"', id="nul-then-digit"),
        pytest.param(r'"\7"+"7"', id="octal-seven"),
        pytest.param(r'"\12"+"3"', id="octal-two-then-digit"),
        pytest.param(r'"\101"+"1"', id="octal-max-then-digit"),
        pytest.param(r'"\x0a"+"b"', id="hex-then-letter"),
        pytest.param('"' + _BS + 'u0041"+"1"', id="unicode-then-digit"),
        pytest.param('"a' + _BS + _CR + _LF + 'b"+"3"', id="crlf-then-digit"),
    ],
)
def test_concat_matches_node(source: str) -> None:
    minified = minify_js(f"x={source}").removeprefix("x=")
    assert _run(f"console.log(({source}))") == _run(f"console.log(({minified}))")
