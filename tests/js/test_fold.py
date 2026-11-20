from __future__ import annotations

import json
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # the test runs a fixed command in a subprocess
from functools import partial
from typing import TYPE_CHECKING, Final

import pytest
from bench.operations import INPUTS, Case

from turbohtml.clean import JSMinify, minify_js

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType

    from work_steps import WorkSteps

_NODE: Final[str | None] = shutil.which("node")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("x=!0", "x=!0", id="canonical-true"),
        pytest.param("x=!1", "x=!1", id="canonical-false"),
        pytest.param("x=!2", "x=!1", id="noncanonical-number"),
        pytest.param("x=!10", "x=!1", id="multidigit-number"),
        pytest.param("x=!!0", "x=!1", id="nested-not"),
        pytest.param("x=true", "x=!0", id="true"),
        pytest.param("x=false", "x=!1", id="false"),
        pytest.param("x=a?true:false", "x=!!a", id="ternary"),
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
        pytest.param("x=null?1:2", "x=2", id="dce-null-falsy"),
        pytest.param("x?1:2", "x?1:2", id="dce-impure-cond-kept"),
        pytest.param("x=void 0?1:2", "x=2", id="dce-void-pure-falsy"),
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
        # a boolean literal branch turns the conditional into a short circuit of the same value; the test is negated
        # where its truthy side must give the literal, and kept bare only when it is itself a boolean
        pytest.param("x=a?b:!0", "x=!a||b", id="cond-true-alt-or"),
        pytest.param("x=a?!1:b", "x=!a&&b", id="cond-false-cons-and"),
        pytest.param("x=a==1?b:!1", "x=a==1&&b", id="cond-boolean-test-false-alt-and"),
        pytest.param("x=a<1?!0:b", "x=a<1||b", id="cond-boolean-test-true-cons-or"),
        pytest.param("x=a===1?b:!0", "x=a!==1||b", id="cond-equality-test-flips"),
        pytest.param("x=a.b?(g(),!1):!0", "x=!a.b||(g(),!1)", id="cond-sequence-branch"),
        pytest.param("x=a.b?c&&d:!0", "x=!a.b||c&&d", id="cond-and-branch-under-or"),
        pytest.param("x=a?b:!1", "x=a?b:!1", id="cond-non-boolean-test-false-alt-kept"),
        pytest.param("x=a?!0:b", "x=a?!0:b", id="cond-non-boolean-test-true-cons-kept"),
        pytest.param("x=a?b=1:!0", "x=a?b=1:!0", id="cond-assign-branch-kept"),
        pytest.param("x=a?b||c:!1", "x=a?b||c:!1", id="cond-or-branch-under-and-kept"),
        pytest.param("x=a?b??c:!0", "x=a?b??c:!0", id="cond-nullish-branch-kept"),
        pytest.param("x=a+b?c:!0", "x=a+b?c:!0", id="cond-arithmetic-test-kept"),
        pytest.param("x=a.b?c||d:!0", "x=!a.b||c||d", id="cond-or-branch-under-or"),
        pytest.param("x=g()?b:!0", "x=!g()||b", id="cond-call-test-negated"),
        pytest.param("x=-a?b:!0", "x=!-a||b", id="cond-unary-test-negated"),
        pytest.param("x=a?!b:!0", "x=!a||!b", id="cond-negated-branch-is-not-literal"),
        pytest.param("x=a>b?c:!1", "x=a>b&&c", id="cond-greater-test"),
        pytest.param("x=a<=b?c:!1", "x=a<=b&&c", id="cond-less-equal-test"),
        pytest.param("x=a>=b?c:!1", "x=a>=b&&c", id="cond-greater-equal-test"),
        pytest.param("x=a!=b?c:!1", "x=a!=b&&c", id="cond-inequality-test"),
        pytest.param("x=a!==b?c:!1", "x=a!==b&&c", id="cond-strict-inequality-test"),
        pytest.param("x=a===b?c:!1", "x=a===b&&c", id="cond-strict-equality-test"),
        pytest.param("x=-a?b:!1", "x=-a?b:!1", id="cond-unary-test-not-boolean-kept"),
        pytest.param("x=a+b?c:!1", "x=a+b?c:!1", id="cond-arithmetic-test-not-boolean-kept"),
        pytest.param("x=a?b:!1e3", "x=a?b:!1e3", id="cond-exponent-literal-kept"),
        pytest.param("x=a?()=>1:!0", "x=a?()=>1:!0", id="cond-arrow-branch-kept"),
        pytest.param("function*g(){x=a?yield:!0}", "function*g(){x=a?yield:!0}", id="cond-yield-branch-kept"),
        pytest.param("x=a?!0:!0", "x=a?!0:!0", id="cond-same-boolean-branches-kept"),
        pytest.param("x=a?!0:!1", "x=!!a", id="cond-boolean-branches-to-boolean"),
        pytest.param("x=a<b?!0:!1", "x=a<b", id="cond-boolean-branches-boolean-test"),
        pytest.param("x=a?!1:!0", "x=!a", id="cond-boolean-branches-negated"),
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
        pytest.param("y=(p??q)??(r??s)", "y=p??q??r??s", id="nullish-chain-rotates-onto-left-chain"),
        pytest.param("x=a&&(b||c)", "x=a&&(b||c)", id="mixed-op-kept"),
        pytest.param("x=a??(b&&c)", "x=a??(b&&c)", id="nullish-and-mix-kept"),
        pytest.param("if(a)b&&c", "a&&b&&c", id="if-chain-rotates"),
        pytest.param("if(!p&&!(q||r&&s))t()", "p||q||r&&s||t()", id="if-negated-test-chain-rotates"),
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
        # -a and ~a may be a BigInt, and a-b too when neither side is known to be a number, so they keep ===;
        # unary +, >>>, and arithmetic with a number operand are numbers (a BigInt throws instead), a concatenation
        # with a string is a string, and delete, relational and worded operators are booleans
        pytest.param("x=-a===-b", "x=-a===-b", id="eq-minus-unary-kept"),
        pytest.param("x=~a===~b", "x=~a===~b", id="eq-bitnot-unary-kept"),
        pytest.param("x=a-b===c-d", "x=a-b===c-d", id="eq-unknown-arithmetic-kept"),
        pytest.param("x=a+1===b+1", "x=a+1===b+1", id="eq-number-plus-unknown-kept"),
        pytest.param("x=1+a===b+1", "x=1+a===b+1", id="eq-number-plus-unknown-right-kept"),
        pytest.param("x=delete a.b===c", "x=delete a.b===c", id="eq-delete-unknown-kept"),
        pytest.param("x=+a!==+a", "x=+a!=+a", id="ne-unary-plus-weakens"),
        pytest.param("x=1/+a===1/b", "x=1/+a==1/b", id="eq-division-weakens"),
        pytest.param("x=a-1===b*2", "x=a-1==b*2", id="eq-arithmetic-weakens"),
        pytest.param("x=a>>>0===b>>>0", "x=a>>>0==b>>>0", id="eq-unsigned-shift-weakens"),
        pytest.param("x=(a|0)===(b<<1)", "x=(a|0)==b<<1", id="eq-bitwise-weakens"),
        pytest.param("x=1+2*a===3", "x=1+2*a==3", id="eq-number-plus-weakens"),
        pytest.param('x=""+a===b+""', 'x=""+a==b+""', id="eq-concat-weakens"),
        pytest.param('x="a"+b+c===d', 'x="a"+b+c===d', id="eq-concat-unknown-kept"),
        pytest.param('x="a"+b+c+d+e+f===""+g', 'x="a"+b+c+d+e+f===""+g', id="eq-concat-past-depth-kept"),
        pytest.param("x=delete a.b===!c", "x=delete a.b==!c", id="eq-delete-weakens"),
        pytest.param("x=(a<b)!==(c in d)", "x=a<b!=c in d", id="ne-relational-in-weakens"),
        pytest.param("x=a instanceof b===!c", "x=a instanceof b==!c", id="eq-instanceof-weakens"),
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
            f"if({'&&'.join(f'!n{i}' for i in range(18))}&&{'&&'.join(f'p{i}' for i in range(17))})x()",
            f"{'&&'.join(f'!n{i}' for i in range(18))}&&{'&&'.join(f'p{i}' for i in range(17))}&&x()",
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
        # a return of undefined is a bare return anywhere in a body, which a guard then folds; a tail conditional
        # with an undefined branch becomes an if, since falling off the end returns undefined too
        pytest.param(
            "function f(){for(;;){if(a)return void 0;g()}}",
            "function f(){for(;;){if(a)return;g()}}",
            id="return-undefined-bare",
        ),
        pytest.param(
            "function f(){if(a)return void 0;g();h()}", "function f(){a||(g(),h())}", id="return-undefined-guard"
        ),
        pytest.param(
            "function f(){for(;;){if(a)return void g();h()}}",
            "function f(){for(;;){if(a)return void g();h()}}",
            id="return-void-call-kept",
        ),
        pytest.param("function f(){if(a)return;return b}", "function f(){if(!a)return b}", id="bare-then-return"),
        pytest.param("function f(){if(a)return b;return}", "function f(){if(a)return b}", id="bare-next-return"),
        pytest.param(
            "function f(){for(;;){if(a)return;return b}}",
            "function f(){for(;;)return a?void 0:b}",
            id="bare-return-merged",
        ),
        pytest.param(
            "function f(){for(;;){if(a)return;return}}",
            "function f(){for(;;){if(a)return;return}}",
            id="both-bare-returns-kept",
        ),
        pytest.param(
            "function f(){return a==null?void 0:a.b}",
            "function f(){if(a!=null)return a.b}",
            id="tail-cond-undefined-then",
        ),
        pytest.param(
            "function f(){return a?a.b:void 0}", "function f(){if(a)return a.b}", id="tail-cond-undefined-else"
        ),
        pytest.param(
            "function f(){g();return a?a.b:void 0}", "function f(){if(g(),a)return a.b}", id="tail-cond-undefined-seq"
        ),
        pytest.param("function f(){return a?b:c}", "function f(){return a?b:c}", id="tail-cond-valued-kept"),
        pytest.param(
            "function f(){return a?b:void g()}", "function f(){return a?b:void g()}", id="tail-cond-void-call-kept"
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
        pytest.param("function f(){return;if(a);else var y}", id="unreach-if-else"),
        pytest.param("function f(){return;for(var i=0;;);}", id="unreach-for-init"),
        pytest.param("function f(){return;for(;;)var x}", id="unreach-for-body"),
        pytest.param("function f(){return;for(var k in o);}", id="unreach-forin-bind"),
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
        pytest.param("if(a){}else{}", "if(a);", id="if-both-empty-guard-kept"),
        # an empty then with a non-expression else is left intact (nothing shorter to fold to)
        pytest.param("if(a){}else{var x=1}", "if(a);else var x=1", id="if-empty-then-nonexpr-else-kept"),
        pytest.param("if(a){}else{for(;;);var z}", "if(a);else{for(;;);var z}", id="if-empty-then-multi-else-kept"),
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
        # only a string the source wrote in the prologue is a directive (11.2.1); any other string statement is
        # a constant without effect, so it goes, and never lands in the prologue
        pytest.param('function f(){"use "+"strict";a()}', "function f(){a()}", id="folded-directive"),
        pytest.param('"use "+"strict";a()', "a()", id="folded-script-directive"),
        pytest.param('function f(){"a";"use "+"strict"}', 'function f(){"a"}', id="folded-after-directive"),
        pytest.param('("use strict");a()', "a()", id="parenthesized-string"),
        pytest.param('function f(){"a"+"b";"use strict"}', "function f(){}", id="folded-ends-prologue"),
        pytest.param('function f(){if(0){}"use strict"}', "function f(){}", id="dropped-statement"),
        pytest.param('function f(){;"use strict"}', "function f(){}", id="dropped-empty-statement"),
        pytest.param('()=>{"use "+"strict";a()}', "()=>{a()}", id="folded-arrow-directive"),
        pytest.param('o={m(){"use "+"strict"}}', "o={m(){}}", id="folded-method-directive"),
        pytest.param('function f(){"use strict";"a";b()}', 'function f(){"use strict";"a";b()}', id="directives-kept"),
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
        # a radix operand folds like the decimal value the printer writes for it
        pytest.param("x=4*0x40", "x=256", id="hex-operand"),
        pytest.param("x=0b11+0o7", "x=10", id="binary-octal-operands"),
        pytest.param("x=0x1_0*2", "x=32", id="separated-hex-operand"),
        pytest.param("x=0x38D7EA4C68000+0", "x=0x38D7EA4C68000+0", id="oversize-hex-operand-kept"),
        # a legacy octal is not its decimal digits: 010 is 8
        pytest.param("x=010*2", "x=010*2", id="legacy-octal-operand-kept"),
        pytest.param("x=08+1", "x=08+1", id="leading-zero-decimal-operand-kept"),
        pytest.param("x=0+1", "x=1", id="zero-operand"),
        pytest.param("x=9999999999999999+1", "x=9999999999999999+1", id="oversize-int-kept"),
        # separators fold like the digits the printer keeps, so a second minification has nothing left to fold
        pytest.param("x=1_0+1", "x=11", id="separated-operand"),
        pytest.param("x=999_999_999_999_999+0", "x=999999999999999", id="separated-largest-operand"),
        pytest.param("x=1_000_000_000_000_000+0", "x=1e15+0", id="separated-oversize-operand-kept"),
        pytest.param("x=0*7", "x=0", id="int-mul-zero"),
        pytest.param("x=99999999*99999999", "x=99999999*99999999", id="int-mul-overflow-kept"),
        pytest.param('x="a"+5', 'x="a"+5', id="string-plus-number-kept"),
        pytest.param("x=\"a\"+'b'", 'x="ab"', id="concat-mixed-quote-joined"),
        # more folded literals than the program's initial owned-buffer capacity, forcing it to grow
        pytest.param(
            f"x=[{','.join(f'{index}+1' for index in range(18))}]",
            f"x=[{','.join(str(index + 1) for index in range(18))}]",
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
        # a number weighs as it prints, so 3600000 costs its four-character 36e5 and propagates in the first pass
        # rather than in a re-minification
        pytest.param(
            "function f(a){const h=3600000;return[a/h,a*h]}",
            "function f(a){return[a/36e5,a*36e5]}",
            id="number-weighs-printed-spelling",
        ),
        pytest.param(
            "function f(a){const h=1.50;return[a/h,a*h,a-h,a+h]}",
            "function f(a){const b=1.5;return[a/b,a*b,a-b,a+b]}",
            id="number-printed-spelling-kept",
        ),
        pytest.param(
            f"function f(a){{const h=1.{'5' * 70};return[a/h,a*h]}}",
            f"function f(a){{const b=1.{'5' * 70};return[a/b,a*b]}}",
            id="number-past-spelling-buffer-kept",
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
            "function f(){var x=1;return[{external},x,x]}",
            "function f(){return[{external},1,1]}",
            id="propagation-keeps-unbound-shorthand",
        ),
        pytest.param(
            "function f(){var a=1,b=g();return function(){return a+a+b}}",
            "function f(){var a=g();return function(){return 2+a}}",
            id="propagation-unlinks-first-declarator",
        ),
        pytest.param(
            "function f(){var q=g(),a=1,z=h();return function(){return a+a+q+z}}",
            "function f(){var b=g(),a=1,c=h();return function(){return a+a+b+c}}",
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
            "function f(){var r=/a/;return c?r.test(s):g()}",
            "function f(){return c?/a/.test(s):g()}",
            id="regex-into-ternary-consquent",
        ),
        pytest.param(
            "function f(){var r=/a/;return c?g():r.test(s)}",
            "function f(){return c?g():/a/.test(s)}",
            id="regex-into-ternary-alt",
        ),
        pytest.param(
            "function f(){var re=/[0-9]+/;k(1,function(n){return n},re)}",
            "function f(){k(1,function(a){return a},/[0-9]+/)}",
            id="regex-after-nested-function-in-next-statement",
        ),
        pytest.param(
            "function f(){var re=/[0-9]+/;k.call(o,()=>re)}",
            "function f(){var a=/[0-9]+/;k.call(o,()=>a)}",
            id="regex-into-arrow-in-next-statement-kept",
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
            "function f(){var r=/a/;g();label:h();return r.test(s)}",
            "function f(){var a=/a/;g();a:h();return a.test(s)}",
            id="regex-past-labeled-statement-kept",
        ),
        pytest.param(
            "function f(){var r=/a/;switch(c){case 1:return r.test(s)}}",
            "function f(){var a=/a/;switch(c){case 1:return a.test(s)}}",
            id="regex-into-switch-kept",
        ),
        pytest.param(
            "function f(){var r=/a/;if(c){var q}g(r.test(s),q)}",
            "function f(){var a=/a/;if(c)var b;g(a.test(s),b)}",
            id="regex-past-if-kept",
        ),
        pytest.param(
            "function f(){var r=/a/;for(;c;)g(r.test(s));}",
            "function f(){for(var a=/a/;c;)g(a.test(s))}",
            id="regex-into-for-loop-body",
        ),
        pytest.param(
            "function f(){var r=/a/;for(;c;h(r.test(s)));}",
            "function f(){for(var a=/a/;c;h(a.test(s)));}",
            id="regex-into-for-loop-update",
        ),
        pytest.param(
            "function f(){var x=1;return function(){return{k:x,m:x}}}",
            "function f(){return function(){return{k:1,m:1}}}",
            id="propagation-into-plain-props",
        ),
        pytest.param(
            "function f(){var {q}=o,x=1;return function(){return x+x+q}}",
            "function f(){var {q:b}=o,a=1;return function(){return a+a+b}}",
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
        pytest.param(
            "function f(c){var r=/a/;return c?1:r}",
            "function f(a){return a?1:/a/}",
            id="regex-conditional-alternate",
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
            "function f(){return g()+2}",
            id="drop-middle-declarator",
        ),
        pytest.param("function f(){var x=1,y=g();return y}", "function f(){return g()}", id="drop-first-declarator"),
        pytest.param(
            "function f(){var {a}=o,x=1;return a}", "function f(){var {a:a}=o;return a}", id="drop-past-destructure"
        ),
        pytest.param("function f(){var x=g();return 2}", "function f(){return g(),2}", id="keep-sideeffect-init"),
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
        # the rest of the body moves into the guard's block unless it declares a binding the block would rescope
        pytest.param(
            "function f(a){if(a)return;g();var x=h();x()}",
            "function f(a){if(!a){g();var b=h();b()}}",
            id="guard-return-var-wrapped",
        ),
        pytest.param(
            "function f(a,b){if(a)return;if(b)return 1;return 2}",
            "function f(a,b){if(!a)return b?1:2}",
            id="guard-return-before-return",
        ),
        pytest.param(
            "function f(a){if(a==null)return;for(;;)g()}",
            "function f(a){if(a!=null)for(;;)g()}",
            id="guard-return-equality-flips",
        ),
        pytest.param(
            "function f(a){if(a)return;l:for(;;){g();break l}}",
            "function f(a){if(!a)a:for(;;){g();break a}}",
            id="guard-return-labeled-loop-wrapped",
        ),
        pytest.param(
            "function f(a){if(a)return;g();let x=h();x();x()}",
            "function f(b){if(b)return;g();let a=h();a(),a()}",
            id="guard-return-let-kept",
        ),
        pytest.param(
            "function f(a){if(a)return;g();class C{}h(C,C)}",
            "function f(a){if(a)return;g();class b{}h(b,b)}",
            id="guard-return-class-kept",
        ),
        pytest.param(
            "function f(a){if(a)return;g();function h(){}h(h)}",
            "function f(a){if(a)return;g();function b(){}b(b)}",
            id="guard-return-function-kept",
        ),
        pytest.param(
            "function f(a){if(a)return;g();l:function h(){}h(h)}",
            "function f(a){if(a)return;g();a:function h(){}h(h)}",
            id="guard-return-labeled-function-kept",
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
        # a let, const or class reached before its declaration throws, so the access is never dead or pure
        pytest.param("function f(){x=2;let x=1}", "function f(){a=2;let a=1}", id="dead-store-before-let-kept"),
        pytest.param("function f(){C=1;class C{}}", "function f(){a=1;class a{}}", id="dead-store-before-class-kept"),
        pytest.param(
            "function f(){var y;y=x;let x=1;return x}",
            "function f(){a;let a=1;return a}",
            id="early-read-in-dead-store-kept",
        ),
        pytest.param(
            "function f(){(x,g());let x=1;return x}",
            "function f(){a,g();let a=1;return a}",
            id="early-read-in-sequence-kept",
        ),
        pytest.param(
            "function f(){g();function g(){x=1}let x}",
            "function f(){(function(){a=1}());let a}",
            id="hoisted-store-kept",
        ),
        pytest.param(
            "function f(){switch(1){case 0:let x=1;case 1:x=2}}",
            "function f(){switch(1){case 0:let a=1;case 1:a=2}}",
            id="store-past-skipped-case-kept",
        ),
        pytest.param(
            "function f(){let x=1;var y;y=x;x=3;return g()}", "function f(){return g()}", id="late-store-dropped"
        ),
        # a top-level binding is a global another script reads, so a store to it is never dead
        pytest.param("var x;x=g()", "var x;x=g()", id="global-store-kept"),
        pytest.param("let x=1;x=2", "let x=1;x=2", id="global-lexical-store-kept"),
        pytest.param(
            "var x=1;function f(){x=2}f()", "var x=1;function f(){x=2}f()", id="global-store-in-function-kept"
        ),
        pytest.param("var a=1,b;b=a;a=3", "var a=1,b;b=a,a=3", id="global-store-in-sequence-kept"),
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
            "function f(){return g(),1}",
            id="multi-declarator-single-use",
        ),
        # a declaration's trailing unread declarators keep only their initializers' effects, and its trailing
        # single-use declarators move into the next statement when their reads come first and in order
        pytest.param(
            "function f(){var a=g(),b=h();return a}",
            "function f(){var a=g();return h(),a}",
            id="unread-tail-after-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=h(),c=1;return[a,c]}",
            "function f(){var a=g();return h(),[a,1]}",
            id="unread-tail-pure-and-effect",
        ),
        pytest.param(
            "function f(){let a=g(),b=h()}",
            "function f(){g(),h()}",
            id="unread-every-declarator",
        ),
        pytest.param(
            "function f(){var a=g();var a}",
            "function f(){var a=g(),a}",
            id="unread-redeclared-var-kept",
        ),
        pytest.param(
            "function f(){try{}catch(e){var e=g()}}",
            "function f(){try{}catch(e){var e=g()}}",
            id="unread-catch-redeclaration-kept",
        ),
        pytest.param("var a=g(),b=h();a+b", "var a=g(),b=h();a+b", id="top-level-inline-kept"),
        pytest.param("let a=g(),b=h();a+b", "let a=g(),b=h();a+b", id="top-level-let-inline-kept"),
        pytest.param(
            "function f(){var a=g(),b=h(a);return a+b}",
            "function f(){var a=g(),b=h(a);return a+b}",
            id="inline-twice-read-kept",
        ),
        pytest.param(
            "var a=g(),b=h();k([a,b])",
            "var a=g(),b=h();k([a,b])",
            id="unread-top-level-kept",
        ),
        pytest.param(
            "function f(p){var a=g(),b=h();return[1,p,()=>0,a,b]}",
            "function f(a){return[1,a,()=>0,g(),h()]}",
            id="inline-in-declaration-order",
        ),
        pytest.param(
            "function f(p){var a=g(),b=h();return a+b}",
            "function f(a){return g()+h()}",
            id="inline-binary-operands",
        ),
        pytest.param(
            "function f(p){var a=g(),b=h();return(a,p)+b}",
            "function f(a){return g(),a+h()}",
            id="inline-sequence-operand",
        ),
        pytest.param(
            "function f(p){var a=g(),b=h();return[b,a]}",
            "function f(b){var a=g();return[h(),a]}",
            id="inline-trailing-declarator-only",
        ),
        pytest.param(
            "function f(p){var a=g();return p(a)}",
            "function f(a){return a(g())}",
            id="inline-parameter-call",
        ),
        pytest.param(
            "function f(p){var a=g();return new p(a)}",
            "function f(a){return new a(g())}",
            id="inline-parameter-new",
        ),
        pytest.param(
            "function f(p){var a=g(),b=h();return[,1n,a,b]}",
            "function f(a){return[,1n,g(),h()]}",
            id="inline-bigint-hole",
        ),
        pytest.param(
            "function f(p){var a=g();p(a)}",
            "function f(a){a(g())}",
            id="inline-into-expression-statement",
        ),
        pytest.param(
            "function f(){var a=g(),b=h();g(b);return a}",
            "function f(){var a=g(),b=h();return g(b),a}",
            id="inline-out-of-order-kept",
        ),
        pytest.param(
            "function f(){var a=g(g(g(g(g(g(g(g(g(g(g(g(g(g(g(g(g(1)))))))))))))))));return[a]}",
            "function f(){var a=g(g(g(g(g(g(g(g(g(g(g(g(g(g(g(g(g(1)))))))))))))))));return[a]}",
            id="inline-tall-initializer-kept",
        ),
        pytest.param(
            "function f(){var a=g(),{x}=o;return[a,x]}",
            "function f(){var a=g(),{x:b}=o;return[a,b]}",
            id="inline-before-destructure-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b;return[a,b]}",
            "function f(){var a=g(),b;return[a,b]}",
            id="inline-before-uninitialized-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=h();return[a,b,b]}",
            "function f(){var b=g(),a=h();return[b,a,a]}",
            id="inline-before-twice-read-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=h();return[a,b=1]}",
            "function f(){var a=g(),b=h();return[a,b=1]}",
            id="inline-before-written-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=h();return[a,b,b=1]}",
            "function f(){var b=g(),a=h();return[b,a,a=1]}",
            id="inline-before-read-and-written-kept",
        ),
        pytest.param(
            "function f(){var b;var a=g(),b=h();return[a,b]}",
            "function f(){var a,b=g(),a=h();return[b,a]}",
            id="inline-before-redeclared-kept",
        ),
        pytest.param(
            "function f(){let c=k(),a=g();return c(a)}",
            "function f(){let a=k(),b=g();return a(b)}",
            id="inline-into-local-callee-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=h();return[a,++b]}",
            "function f(){var a=g(),b=h();return[a,++b]}",
            id="inline-before-updated-kept",
        ),
        pytest.param(
            "function f(){try{}catch(e){var a=g(),e=h();return[a,e]}}",
            "function f(){try{}catch(e){var a=g(),e=h();return[a,e]}}",
            id="inline-catch-redeclaration-kept",
        ),
        pytest.param(
            "function f(){var a=g();return arguments(a)}",
            "function f(){var a=g();return arguments(a)}",
            id="inline-into-arguments-callee-kept",
        ),
        pytest.param("var a=g(),b=h()", "var a=g(),b=h()", id="unread-top-level-declaration-kept"),
        pytest.param(
            "function f(p){h(()=>p=1);var a=g();return[p,a]}",
            "function f(a){h(()=>a=1);var b=g();return[a,b]}",
            id="inline-past-written-parameter-kept",
        ),
        pytest.param(
            "function f(p){var a=g();return p(o.x,a)}",
            "function f(a){var b=g();return a(o.x,b)}",
            id="inline-past-member-argument-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=h();return[a,c,b]}",
            "function f(){var a=g(),b=h();return[a,c,b]}",
            id="inline-past-free-name-kept",
        ),
        pytest.param(
            "function f(){var a=g();return[[[[[[a]]]]]]}",
            "function f(){var a=g();return[[[[[[a]]]]]]}",
            id="inline-deep-read-kept",
        ),
        pytest.param(
            "function f(){var a=g();return[...x,a]}",
            "function f(){var a=g();return[...x,a]}",
            id="inline-past-spread-kept",
        ),
        pytest.param(
            "function f(p){var a=g();return p(...x,a)}",
            "function f(a){var b=g();return a(...x,b)}",
            id="inline-past-spread-argument-kept",
        ),
        pytest.param(
            "function f(){var a=g();return h(a)}",
            "function f(){var a=g();return h(a)}",
            id="inline-into-free-callee-kept",
        ),
        pytest.param(
            "function f(){var a=g();return o.f(a)}",
            "function f(){var a=g();return o.f(a)}",
            id="inline-into-method-call-kept",
        ),
        # an access evaluates its object, then a computed key, before the property get that may run a getter
        pytest.param(
            "function f(){var a=g();return a.x}", "function f(){return g().x}", id="inline-into-member-object"
        ),
        pytest.param(
            "function f(){var a=g();return a.f(1)}", "function f(){return g().f(1)}", id="inline-into-method-object"
        ),
        pytest.param(
            "function f(p){var a=g();return p[a]}", "function f(a){return a[g()]}", id="inline-into-computed-key"
        ),
        pytest.param(
            "function f(){var a=g();return o.x[a]}",
            "function f(){var a=g();return o.x[a]}",
            id="inline-past-getter-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=h();return a.x(b)}",
            "function f(){var a=g(),b=h();return a.x(b)}",
            id="inline-into-method-argument-kept",
        ),
        pytest.param(
            "function f(p){var a=g();return p?.(a)}",
            "function f(a){var b=g();return a?.(b)}",
            id="inline-into-optional-call-kept",
        ),
        pytest.param(
            "function f(p){h(()=>p=1);var a=g();return p(a)}",
            "function f(a){h(()=>a=1);var b=g();return a(b)}",
            id="inline-into-written-parameter-callee-kept",
        ),
        pytest.param(
            "function f(){var c=k(),a=g();return[c,c,a]}",
            "function f(){var a=k(),b=g();return[a,a,b]}",
            id="inline-past-local-read-kept",
        ),
        pytest.param(
            "function f(){var a=g();return-a}",
            "function f(){var a=g();return-a}",
            id="inline-under-unary-kept",
        ),
        pytest.param(
            "function f(){var a=g();throw a}",
            "function f(){throw g()}",
            id="inline-before-throw",
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
            "function f(){var a=function(){return b}(),b=1;return a}",
            id="literal-earlier-declarator-read-kept",
        ),
        pytest.param(
            "function f(){while(c){var t=1;g(function(){return t})}}",
            "function f(){for(;c;){var a=1;g(function(){return a})}}",
            id="literal-block-nested-var-kept",
        ),
        pytest.param(
            "function f(){var a=t,t=1;return a}",
            "function f(){var a=b,b=1;return a}",
            id="literal-earlier-declarator-direct-read-kept",
        ),
        pytest.param(
            "function f(){var a=u?1:t,t=2;return a}",
            "function f(){var a=u?1:b,b=2;return a}",
            id="literal-earlier-declarator-cond-read-kept",
        ),
        pytest.param(
            "function f(){var a=function(){for(;;)g(t)},t=1;return a}",
            "function f(){var a=function(){for(;;)g(b)},b=1;return a}",
            id="literal-earlier-declarator-loop-read-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=t,t=2;return b}",
            "function f(){var c=g(),a=b,b=2;return a}",
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
        pytest.param("while(c){}", "for(;c;);", id="while-empty-body"),
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
        # a shorthand that binds the name is not its read: the inline leaves the pattern alone
        pytest.param(
            "function f(a,{x}){var x=g(a);return x}",
            "function f(a,{x:b}){return g(a)}",
            id="shorthand-pattern-not-an-inline-slot",
        ),
        pytest.param(
            "function f(){var y=2;g({y});if(c)var {y}=o}",
            "function f(){var a=2;g({y:a});if(c)var {y:a}=o}",
            id="shorthand-read-before-pattern-expands-key",
        ),
        pytest.param(
            "function p(o){function g(){}var {g}=o;if(c)q();else k(g)}",
            "function p(a){function b(){}var {g:b}=a;c?q():k(b)}",
            id="shorthand-pattern-not-a-function-inline-slot",
        ),
        pytest.param(
            "function f(o){var x=5;var {x}=o;return x+x}",
            "function f(b){var a=5,{x:a}=b;return a+a}",
            id="shorthand-pattern-redeclares-literal",
        ),
        pytest.param(
            "function g(){var k=function([{k}]?1:0){};return k}",
            "function g(){return function([{k}]?1:0){}}",
            id="shorthand-in-parameter-not-an-inline-slot",
        ),
        pytest.param(
            "y=function({x}){var x=function({x}){var x=m();return x}();return x}",
            "y=function({x:a}){return function({x:a}){return m()}()}",
            id="nested-shorthand-pattern-keeps-initializer-once",
        ),
    ],
)
def test_compresses(source: str, expected: str) -> None:
    assert minify_js(source) == expected


# a literal binding a closure reads takes its value there when nothing before it in the function's first statement can
# call that closure first
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "function f(p){var b,a=1;return function(){return[a,a,b]}}",
            "function f(b){var a;return function(){return[1,1,a]}}",
            id="none",
        ),
        pytest.param(
            "function f(p){var b='s',a=1;return function(){return[a,a,b]}}",
            "function f(a){return function(){return[1,1,'s']}}",
            id="string",
        ),
        pytest.param(
            "function f(p){var b=1n,a=1;return function(){return[a,a,b]}}",
            "function f(b){var a=1n;return function(){return[1,1,a]}}",
            id="bigint",
        ),
        pytest.param(
            "function f(p){var b=/r/,a=1;return function(){return[a,a,b]}}",
            "function f(b){var a=/r/;return function(){return[1,1,a]}}",
            id="regex",
        ),
        pytest.param(
            "function f(p){var b=function(){},a=1;return function(){return[a,a,b]}}",
            "function f(b){var a=function(){};return function(){return[1,1,a]}}",
            id="function",
        ),
        pytest.param(
            "function f(p){var b=()=>0,a=1;return function(){return[a,a,b]}}",
            "function f(b){var a=()=>0;return function(){return[1,1,a]}}",
            id="arrow",
        ),
        pytest.param(
            "function f(p){var b=[,1],a=1;return function(){return[a,a,b]}}",
            "function f(b){var a=[,1];return function(){return[1,1,a]}}",
            id="hole",
        ),
        pytest.param(
            "function f(p){var b=`t`,a=1;return function(){return[a,a,b]}}",
            "function f(b){var a=`t`;return function(){return[1,1,a]}}",
            id="template",
        ),
        pytest.param(
            "function f(p){var b=`t${p}`,a=1;return function(){return[a,a,b]}}",
            "function f(b){var c=`t${b}`,a=1;return function(){return[a,a,c]}}",
            id="template-substitution",
        ),
        pytest.param(
            "function f(p){var b=p,a=1;return function(){return[a,a,b]}}",
            "function f(a){var b=a;return function(){return[1,1,b]}}",
            id="param",
        ),
        pytest.param(
            "function f(p){var b=G,a=1;return function(){return[a,a,b]}}",
            "function f(c){var b=G,a=1;return function(){return[a,a,b]}}",
            id="free-name",
        ),
        pytest.param(
            "var T=h();function f(p){var b=T,a=1;return function(){return[a,a,b]}}",
            "var T=h();function f(c){var b=T,a=1;return function(){return[a,a,b]}}",
            id="top-level-name",
        ),
        pytest.param(
            "function f(p){var b=!p,a=1;return function(){return[a,a,b]}}",
            "function f(a){var b=!a;return function(){return[1,1,b]}}",
            id="not",
        ),
        pytest.param(
            "function f(p){var b=typeof p,a=1;return function(){return[a,a,b]}}",
            "function f(a){var b=typeof a;return function(){return[1,1,b]}}",
            id="typeof",
        ),
        pytest.param(
            "function f(p){var b=void g(),a=1;return function(){return[a,a,b]}}",
            "function f(c){var b=void g(),a=1;return function(){return[a,a,b]}}",
            id="void-call",
        ),
        pytest.param(
            "function f(p){var b=-1,a=1;return function(){return[a,a,b]}}",
            "function f(a){return function(){return[1,1,-1]}}",
            id="negative",
        ),
        pytest.param(
            "function f(p){var b=-p,a=1;return function(){return[a,a,b]}}",
            "function f(b){var c=-b,a=1;return function(){return[a,a,c]}}",
            id="negate-name",
        ),
        pytest.param(
            "function f(p){var b=1/3,a=1;return function(){return[a,a,b]}}",
            "function f(b){var a=1/3;return function(){return[1,1,a]}}",
            id="arithmetic",
        ),
        pytest.param(
            "function f(p){var b=p/3,a=1;return function(){return[a,a,b]}}",
            "function f(b){var c=b/3,a=1;return function(){return[a,a,c]}}",
            id="arithmetic-name",
        ),
        pytest.param(
            "function f(p){var b=3/p,a=1;return function(){return[a,a,b]}}",
            "function f(b){var c=3/b,a=1;return function(){return[a,a,c]}}",
            id="name-divides",
        ),
        pytest.param(
            "function f(p){var b=[p,[1]],a=1;return function(){return[a,a,b]}}",
            "function f(a){var b=[a,[1]];return function(){return[1,1,b]}}",
            id="array",
        ),
        pytest.param(
            "function f(p){var b=[...p],a=1;return function(){return[a,a,b]}}",
            "function f(b){var c=[...b],a=1;return function(){return[a,a,c]}}",
            id="spread",
        ),
        pytest.param(
            "function f(p){var b={a:p,c(){}},a=1;return function(){return[a,a,b]}}",
            "function f(a){var b={a:a,c(){}};return function(){return[1,1,b]}}",
            id="object",
        ),
        pytest.param(
            "function f(p){var b={[p]:1},a=1;return function(){return[a,a,b]}}",
            "function f(b){var c={[b]:1},a=1;return function(){return[a,a,c]}}",
            id="computed-key",
        ),
        pytest.param(
            "function f(p){var b={...p},a=1;return function(){return[a,a,b]}}",
            "function f(b){var c={...b},a=1;return function(){return[a,a,c]}}",
            id="object-spread",
        ),
        pytest.param(
            "function f(p){var c=p,b=c,a=1;return function(){return[a,a,b,c]}}",
            "function f(b){var a=b,c=a;return function(){return[1,1,c,a]}}",
            id="var-name",
        ),
        pytest.param(
            "function f(p){var b=k,a=1;return function(){return[a,a,b]};function k(){}}",
            "function f(b){var a=function(){};return function(){return[1,1,a]}}",
            id="function-name",
        ),
        pytest.param(
            "try{}catch(e){(function(){var b=e,a=1;return function(){return[a,a,b]}})()}",
            "try{}catch(a){(function(){var b=a;return function(){return[1,1,b]}}())}",
            id="catch-name",
        ),
        pytest.param(
            "function f(p){var b={a:g()},a=1;return function(){return[a,a,b]}}",
            "function f(c){var b={a:g()},a=1;return function(){return[a,a,b]}}",
            id="object-call",
        ),
        pytest.param(
            "function f(p){var b=g(),a=1;return function(){return[a,a,b]}}",
            "function f(c){var b=g(),a=1;return function(){return[a,a,b]}}",
            id="call",
        ),
        pytest.param(
            "function f(p){var b=p.x,a=1;return function(){return[a,a,b]}}",
            "function f(b){var c=b.x,a=1;return function(){return[a,a,c]}}",
            id="member",
        ),
        pytest.param(
            "function f(p){var b=[[[[[[[[1]]]]]]]],a=1;return function(){return[a,a,b]}}",
            "function f(c){var b=[[[[[[[[1]]]]]]]],a=1;return function(){return[a,a,b]}}",
            id="deep",
        ),
        pytest.param(
            "function f(p){var b=c,a=1;let c=p;return function(){return[a,a,b,c]}}",
            "function f(c){var d=b,a=1;let b=c;return function(){return[a,a,d,b]}}",
            id="let-name",
        ),
        pytest.param(
            "function f(p){var b=g();var a=1;return function(){return[a,a,b]}}",
            "function f(c){var b=g(),a=1;return function(){return[a,a,b]}}",
            id="second-statement",
        ),
        pytest.param(
            "function f(p){var b=1,a=1;return function(){return a}}",
            "function f(a){return function(){return 1}}",
            id="single-read",
        ),
        pytest.param(
            "function f(p){var b=g(),a=1;return function(){return a}}",
            "function f(b){var c=g(),a=1;return function(){return a}}",
            id="single-read-after-call",
        ),
        pytest.param(
            "function f(p){let b=1,a=1;return function(){return[a,b]}}",
            "function f(a){return function(){return[1,1]}}",
            id="let-single-read",
        ),
        pytest.param(
            "function f(p){let b=g(),a=1;return function(){return[a,b]}}",
            "function f(c){let a=g(),b=1;return function(){return[b,a]}}",
            id="let-single-read-after-call",
        ),
    ],
)
def test_propagates_past_inert_initializers(source: str, expected: str) -> None:
    assert minify_js(source) == expected


_DEEP_TEST: Final[str] = f"{'a===(' * 15}a===1{')' * 15}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("function f(a){return a?g(1):g(2)}", "function f(a){return g(a?1:2)}", id="one-argument"),
        pytest.param("function f(a,b){return a?g(b,1):g(b,2)}", "function f(a,b){return g(b,a?1:2)}", id="shared-name"),
        pytest.param(
            "function f(a,b){return!a&&b!==1?g('x',2):g('x',3)}",
            "function f(a,b){return g('x',a||b===1?3:2)}",
            id="logical-test",
        ),
        pytest.param(
            "function f(a){function g(){}return a?g(1):g(2)}",
            "function f(a){return function(){}(a?1:2)}",
            id="local-callee",
        ),
        pytest.param(
            "function f(a){try{}catch(e){return e?g(1):g(2)}}",
            "function f(a){try{}catch(a){return g(a?1:2)}}",
            id="catch-test",
        ),
        pytest.param(
            "function f(){var b=h();h();return b?g(1):g(2)}",
            "function f(){var a=h();return h(),g(a?1:2)}",
            id="var-test",
        ),
        pytest.param("function f(a){return G&&a?g(1):g(2)}", "function f(a){return G&&a?g(1):g(2)}", id="global-and"),
        pytest.param("function f(a){return a?g(1):g(22)}", "function f(a){return g(a?1:22)}", id="literal-length"),
        pytest.param(
            "function f(){function k(){}return k?g(1):g(2)}",
            "function f(){return g(function(){}?1:2)}",
            id="function-test",
        ),
        pytest.param(
            "function f(a){return a!==1?g(1):g(2)}", "function f(a){return g(a!==1?1:2)}", id="strict-inequality"
        ),
        pytest.param(
            "function f(a){return a==='s'?g(1):g(2)}", "function f(a){return g(a==='s'?1:2)}", id="string-test"
        ),
        pytest.param("function f(a){return a.b?g(1):g(2)}", "function f(a){return a.b?g(1):g(2)}", id="member-test"),
        pytest.param("var a=h();a?g(1):g(2)", "var a=h();a?g(1):g(2)", id="top-level-test"),
        pytest.param(
            "function f(a){return!G===a?g(1):g(2)}", "function f(a){return!G===a?g(1):g(2)}", id="not-global-test"
        ),
        pytest.param(
            "function f(a){return a&&G?g(1):g(2)}", "function f(a){return a&&G?g(1):g(2)}", id="logical-global"
        ),
        pytest.param("function f(a){return a?g(h(1)):g(h(2))}", "function f(a){return g(h(a?1:2))}", id="nested-calls"),
        pytest.param("function f(a){if(a?g(1):g(2))x()}", "function f(a){g(a?1:2)&&x()}", id="if-test"),
        pytest.param(
            "function f(a){if(!(a?g(1):g(2)))x();else y()}", "function f(a){g(a?1:2)?y():x()}", id="negated-if-test"
        ),
        pytest.param("function f(a){while(a?g(1):g(2))x()}", "function f(a){for(;g(a?1:2);)x()}", id="while-test"),
        pytest.param(
            "function f(a){do x();while(a?g(1):g(2))}", "function f(a){do x();while(g(a?1:2))}", id="do-while-test"
        ),
        pytest.param("function f(a){for(;a?g(1):g(2);)x()}", "function f(a){for(;g(a?1:2);)x()}", id="for-test"),
        pytest.param(
            "function f(a,b){return b&&(a?g(1):g(2))}", "function f(a,b){return b&&g(a?1:2)}", id="logical-right"
        ),
        pytest.param(
            "function f(a){return(a?g(1):g(2))?x():y()}",
            "function f(a){return g(a?1:2)?x():y()}",
            id="conditional-test",
        ),
        pytest.param(
            "function f(a){if(a)return function(b){return b?g(1):g(2)}}",
            "function f(a){if(a)return function(a){return g(a?1:2)}}",
            id="in-function-under-if",
        ),
        pytest.param("function f(a,b){return a?g(a):g(b)}", "function f(a,b){return g(a||b)}", id="test-is-arm"),
        pytest.param("function f(a,b){return a?g(b):g(a)}", "function f(a,b){return g(a&&b)}", id="test-is-other-arm"),
        pytest.param(
            "function f(a){var b;return a?g(b=1):g(b=2)}",
            "function f(a){var b;return g(b=a?1:2)}",
            id="assignment-arms",
        ),
        pytest.param(
            "function f(a){var b;return a?g(b=1):g(2)}",
            "function f(a){var b;return g(a?b=1:2)}",
            id="one-assignment-arm",
        ),
        pytest.param(
            "function f(a,b){return a?b?g(1):g(2):h()}",
            "function f(a,b){return a?g(b?1:2):h()}",
            id="conditional-in-arm",
        ),
        pytest.param(
            "function f(a){return a===1?g(x(),1):g(x(),2)}",
            "function f(a){return a===1?g(x(),1):g(x(),2)}",
            id="call-argument",
        ),
        pytest.param("function f(a){return a?g(1,2):g(3,4)}", "function f(a){return a?g(1,2):g(3,4)}", id="two-differ"),
        pytest.param("function f(a){return a?g(1):g(1)}", "function f(a){return a?g(1):g(1)}", id="none-differ"),
        pytest.param("function f(a){return a?g(1):g(1,2)}", "function f(a){return a?g(1):g(1,2)}", id="argument-count"),
        pytest.param(
            "function f(a){return a?g(1,2):g(1)}", "function f(a){return a?g(1,2):g(1)}", id="argument-count-shorter"
        ),
        pytest.param("function f(a){return a?g(...x):g(1)}", "function f(a){return a?g(...x):g(1)}", id="spread-first"),
        pytest.param(
            "function f(a){return a?g(1):g(...x)}", "function f(a){return a?g(1):g(...x)}", id="spread-second"
        ),
        pytest.param("function f(a){return a?g?.(1):g?.(2)}", "function f(a){return a?g?.(1):g?.(2)}", id="optional"),
        pytest.param(
            "function f(a){return a?o.g(1):o.g(2)}", "function f(a){return a?o.g(1):o.g(2)}", id="member-callee"
        ),
        pytest.param("function f(a){return a?g(1):h(2)}", "function f(a){return a?g(1):h(2)}", id="other-callee"),
        pytest.param("function f(a){return a?g(1):new g(2)}", "function f(a){return a?g(1):new g(2)}", id="new"),
        pytest.param("function f(a){return a?g(1):2}", "function f(a){return a?g(1):2}", id="one-call"),
        pytest.param("function f(a){return G?g(1):g(2)}", "function f(a){return G?g(1):g(2)}", id="global-test"),
        pytest.param(
            "function f(a){let b=a;return h(),b?g(1):g(2)}",
            "function f(a){let b=a;return h(),b?g(1):g(2)}",
            id="let-test",
        ),
        pytest.param(
            "function f(a){return a==1?g(1):g(2)}", "function f(a){return a==1?g(1):g(2)}", id="loose-equality"
        ),
        pytest.param("function f(a){return-a?g(1):g(2)}", "function f(a){return-a?g(1):g(2)}", id="unary-test"),
        pytest.param(
            f"function f(a){{return {_DEEP_TEST}?g(1):g(2)}}",
            f"function f(a){{return {_DEEP_TEST}?g(1):g(2)}}",
            id="deep-test",
        ),
    ],
)
def test_hoists_call_out_of_conditional(source: str, expected: str) -> None:
    assert minify_js(source) == expected


_TALL: Final[str] = f"{'g(' * 17}1{')' * 17}"


@pytest.mark.parametrize(
    ("source", "options", "expected"),
    [
        # the analysis resolves a block the printer drops the braces of the way the output parses
        pytest.param(
            "function f(x){function g(){}if(x){h(g)}else{var y=1}return y}",
            None,
            "function f(a){if(a)h(function(){});else var b=1;return b}",
            id="braceless-block-resolves-outside",
        ),
        # a loop body runs once per iteration, so a function read there keeps its one closure
        pytest.param(
            "function k(x){function f(){}do g(f);while(x--)}",
            None,
            "function k(a){function b(){}do g(b);while(a--)}",
            id="do-while-body-keeps-function",
        ),
        pytest.param(
            "function k(x){function f(){}while(x--)g(f)}",
            JSMinify(fold=False),
            "function k(a){function b(){}while(a--)g(b)}",
            id="while-body-keeps-function",
        ),
        # the declarator rewrites settle the tree without another round when nothing else follows from them
        pytest.param(
            "function f(){const a=g(0),b=g(1);return[a,b]}", None, "function f(){return[g(0),g(1)]}", id="settled"
        ),
        pytest.param("function f(x){g();var a=h();return a}", None, "function f(a){return g(),h()}", id="then-folded"),
        # a rewrite that moves a read another pass decides by position leaves the tree to a fresh analysis
        pytest.param("function f(){var r=/x/g,a=g(r);return a}", None, "function f(){return g(/x/g)}", id="regex"),
        pytest.param(
            "function f(){function k(){}var a=g(()=>k);return a}",
            None,
            "function f(){function a(){}return g(()=>a)}",
            id="function-declaration",
        ),
        pytest.param(
            "function f(){h();var c=1,a=g(()=>c);return a}",
            None,
            "function f(){h();var a=1;return g(()=>a)}",
            id="literal",
        ),
        pytest.param(
            "function f(){h();var s=(h(),x),a=g(()=>s);return a}",
            None,
            "function f(){h();var a=(h(),x);return g(()=>a)}",
            id="sequence",
        ),
        pytest.param(
            f"function f(){{h();var t={_TALL},a=g(()=>t);return a}}",
            None,
            f"function f(){{h();var a={_TALL};return g(()=>a)}}",
            id="tall",
        ),
        pytest.param(
            "function f(x){if(x){var a=g();return a}return 1}",
            None,
            "function f(a){return a?g():1}",
            id="block-emptied",
        ),
        pytest.param(
            "function f(x){if(x){let a=g()}return 1}", None, "function f(a){return a&&g(),1}", id="block-let-fused"
        ),
        pytest.param("function f(){var z;let a=(z=g());return 1}", None, "function f(){return g(),1}", id="dead-store"),
        pytest.param("function f(){var z,a=g();z=a,1}", None, "function f(){g()}", id="dead-store-after-inline"),
        pytest.param(
            "function f(){function k(){}h();var b=g(),a=h(()=>k);return[b,a]}",
            None,
            "function f(){function a(){}return h(),[g(),h(()=>a)]}",
            id="moves-function-declaration-read",
        ),
        pytest.param(
            "var G=1;function f(){h();var b=g(),a=h(()=>G);return[b,a]}",
            None,
            "var G=1;function f(){return h(),[g(),h(()=>G)]}",
            id="moves-global-read",
        ),
        pytest.param(
            "function f(x){var k=m();if(x){function k(){}}h();var b=g(),a=h(()=>k);return[b,a]}",
            None,
            "function f(a){var k=m();if(a){function k(){}}return h(),[g(),h(()=>k)]}",
            id="moves-pinned-read",
        ),
        pytest.param(
            "function f(p){h();var b=g(),a=h(()=>p);return[b,a]}",
            None,
            "function f(a){return h(),[g(),h(()=>a)]}",
            id="moves-parameter-read",
        ),
        pytest.param(
            "function f(){var c;h();var b=g(),a=h(()=>c);return[b,a]}",
            None,
            "function f(){var a;return h(),[g(),h(()=>a)]}",
            id="moves-uninitialized-read",
        ),
        pytest.param(
            "function f(){var w=m();w=n();h();var b=g(),a=h(()=>w);return[b,a]}",
            None,
            "function f(){var a=m();return a=n(),h(),[g(),h(()=>a)]}",
            id="moves-written-read",
        ),
        pytest.param(
            "function f(){var w=m(),v=w;h();var b=g(),a=h(()=>w);return[b,a,w,v]}",
            None,
            "function f(){var a=m(),b=a;return h(),[g(),h(()=>a),a,b]}",
            id="moves-twice-read",
        ),
        pytest.param(
            "function f(x){if(x){h();let a=g(),b=k();return[a,b]}}",
            None,
            "function f(a){if(a)return h(),[g(),k()]}",
            id="block-declaration-replaced",
        ),
        pytest.param(
            "function f(){var r=/x/;h();var a=g(),b=k();return[a,b,r]}",
            None,
            "function f(){return h(),[g(),k(),/x/]}",
            id="replaced-by-regex-read",
        ),
        pytest.param(
            "function f(p){h();var a=g(),b=p;return[a,b]}", None, "function f(a){return h(),[g(),a]}", id="moves-name"
        ),
        pytest.param(
            "function f(){var q=m();h();var a=g(),b=q;return[a,b]}",
            None,
            "function f(){var a=m();return h(),[g(),a]}",
            id="moves-local-name",
        ),
        pytest.param(
            "function f(p){h(p);var a=g(),b=p;return[a,b]}",
            None,
            "function f(a){return h(a),[g(),a]}",
            id="moves-name-read-before",
        ),
        pytest.param(
            "function f(p){h();var a=g(),b=p;return[a,b,p]}",
            None,
            "function f(a){return h(),[g(),a,a]}",
            id="moves-name-read-after",
        ),
        pytest.param(
            "function f(p){h();var a=g(),b=G;return[a,b]}", None, "function f(a){return h(),[g(),G]}", id="moves-global"
        ),
        pytest.param(
            "function f(){var a=g(),b=(h(),k());return a}",
            None,
            "function f(){var a=g(),b=(h(),k());return a}",
            id="unread-sequence-kept",
        ),
        pytest.param(
            "function f(){var a=g(),b=(h(),k());return[a,b]}",
            None,
            "function f(){var a=g(),b=(h(),k());return[a,b]}",
            id="sequence-not-inlined",
        ),
    ],
)
def test_compresses_to_a_fixpoint(source: str, options: JSMinify | None, expected: str) -> None:
    assert minify_js(source, options) == expected
    assert minify_js(expected, options) == expected


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(
            "function f(o){var x=5;var {x}=o;return{x}}console.log(JSON.stringify(f({x:7})))",
            '{"x":7}\n',
            id="object-literal-read",
        ),
        pytest.param(
            "function f(o){var x=5;var {x}=o;return x}console.log(f({x:7}))", "7\n", id="object-pattern-one-read"
        ),
        pytest.param(
            "function f(o){var x=5;var {x}=o;return x+x}console.log(f({x:7}))", "14\n", id="object-pattern-two-reads"
        ),
        pytest.param(
            "function f(o){var {x}=o;var x=5;return x+x}console.log(f({x:7}))", "10\n", id="object-pattern-before-var"
        ),
        pytest.param(
            "function f(o){var x=5;console.log(x);var {x}=o;return x}console.log(f({x:7}))",
            "5\n7\n",
            id="object-pattern-after-read",
        ),
        pytest.param(
            "function f(o){var x=5;var {x=1}=o;return x+x}console.log(f({}),f({x:7}))",
            "2 14\n",
            id="object-pattern-default",
        ),
        pytest.param(
            "function f(o){var x=5;var {a:{x}}=o;return x+x}console.log(f({a:{x:7}}))",
            "14\n",
            id="object-pattern-nested",
        ),
        pytest.param(
            "function f(o){var x=5;var {a:{x=1}={}}=o;return x+x}console.log(f({}),f({a:{x:7}}))",
            "2 14\n",
            id="object-pattern-nested-default",
        ),
        pytest.param(
            "function f(o){var x=5;var {...x}=o;return JSON.stringify(x)}console.log(f({y:7}))",
            '{"y":7}\n',
            id="object-pattern-rest",
        ),
        pytest.param(
            "function f(o){var x=5;var {x}=o;return()=>x}console.log(f({x:7})())",
            "7\n",
            id="object-pattern-closure-read",
        ),
        pytest.param(
            "function f(){var x=5;for(var {x} of [{x:7}])console.log(x);return x}console.log(f())",
            "7\n7\n",
            id="for-of-object-pattern",
        ),
        pytest.param(
            "function f(){var x=5;for(var [x] in {ab:1});return x}console.log(f())", "a\n", id="for-in-array-pattern"
        ),
        pytest.param(
            "function f(o){var x=5;var [x]=o;return x+x}console.log(f([7]))", "14\n", id="array-pattern-two-reads"
        ),
        pytest.param(
            "function f(o){var x=5;var [...x]=o;return JSON.stringify([x,x])}console.log(f([7,8]))",
            "[[7,8],[7,8]]\n",
            id="array-pattern-rest",
        ),
        pytest.param(
            "function f({x},o){var {x}=o;return x+x}console.log(f({x:1},{x:7}))",
            "14\n",
            id="parameter-pattern-redeclared",
        ),
        pytest.param(
            "function f({x}){var x=5;var {x}={x:7};return x+x}console.log(f({x:1}))",
            "14\n",
            id="parameter-pattern-redeclared-by-literal-pattern",
        ),
        pytest.param(
            "function f({x=1},o){var {x}=o;return x}console.log(f({},{x:7}))", "7\n", id="parameter-default-redeclared"
        ),
        pytest.param(
            "function f({a:{x}},o){var {x}=o;return x}console.log(f({a:{x:1}},{x:7}))",
            "7\n",
            id="parameter-nested-redeclared",
        ),
        pytest.param("function f(o){var x=5;({x}=o);return x+x}console.log(f({x:7}))", "14\n", id="assignment-pattern"),
        pytest.param(
            "function f(o){var x=5;({x=1}=o);return x+x}console.log(f({}),f({x:7}))",
            "2 14\n",
            id="assignment-pattern-default",
        ),
        pytest.param(
            "function f(o){var x=5;({a:{x}}=o);return x+x}console.log(f({a:{x:7}}))",
            "14\n",
            id="assignment-pattern-nested",
        ),
        pytest.param(
            "function f(o){var x=5;({a:{x=1}={}}=o);return x+x}console.log(f({}),f({a:{x:7}}))",
            "2 14\n",
            id="assignment-pattern-nested-default",
        ),
        pytest.param(
            "function p(o){function g(){return 1}var {g}=o;console.log(g())}p({g:function(){return 2}})",
            "2\n",
            id="function-declaration-pattern",
        ),
    ],
)
def test_binding_shorthand_preserves_behavior(snippet: str, expected: str) -> None:
    assert (_run(snippet), _run(minify_js(snippet))) == (expected, expected)


def _run(code: str) -> str:
    assert _NODE is not None  # the callers are skipped when node is unavailable
    # the first node start on a cold Windows runner has taken over a minute on its own; the timeout
    # is only here to bound a snippet that never returns, so it sits well clear of a slow launch
    result = subprocess.run([_NODE, "-e", code], capture_output=True, text=True, timeout=300, check=False)  # ruff:ignore[subprocess-without-shell-equals-true]
    return result.stdout + result.stderr


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param("function f(){{q({names});{declaration};h([{padding}])}}", id="read-before"),
        pytest.param("function f(){{{declaration};h([{padding}],{names})}}", id="read-in-next"),
    ],
)
def test_regex_initializers_cost_no_more_than_string_ones(shape: str, work_steps: WorkSteps) -> None:
    def source(initializer: str) -> str:
        names = [f"r{index:x}" for index in range(300)]
        return shape.format(
            names=",".join(names),
            declaration=f"var {','.join(f'{name}={initializer}' for name in names)}",
            padding="1," * 3_000,
        )

    # a string initializer never reaches the regex branch, so it times the same script without that check
    work_steps.assert_within(partial(minify_js, source("/a/")), partial(minify_js, source('"a"')), factor=1.5)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("if(a){{b()}}", "a&&b()", id="if-body-to-logical"),
        pytest.param("for(;;){{{}}}", "for(;;);", id="loop-body-empties"),
        pytest.param("{{b()}}", "b()", id="bare-block"),
        pytest.param("{{{x()}}}", "x()", id="three-deep"),
        pytest.param("if(a){{b()}}else{{c()}}", "a?b():c()", id="both-branches"),
        pytest.param("while(x){{y()}}", "for(;x;)y()", id="while-body"),
        pytest.param("{{var v=1}}", "var v=1", id="transparent-var"),
        pytest.param("{{function f(){}}}", "{function f(){}}", id="function-keeps-its-block"),
        pytest.param("{{class C{}f(C)}}", "{class C{}f(C)}", id="class-keeps-its-block"),
        pytest.param("a();{{b()}}", "a(),b()", id="block-after-a-statement"),
        pytest.param("{{a();b()}}", "a(),b()", id="multi-statement-inner-block"),
        # a string a block hid is not a directive, so flattening it to the prologue drops it
        pytest.param('function f(){{"use strict"}}', "function f(){}", id="no-directive-from-flatten"),
    ],
)
def test_nested_blocks_collapse_in_one_call(source: str, expected: str) -> None:
    # the fold reaches a fixpoint per call, so a redundant block never survives to need a second (#1066)
    assert minify_js(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("if(a){{b()}}", "if(a)b()", id="if-body-braceless"),
        pytest.param("for(;;){{{}}}", "for(;;);", id="loop-body-empties"),
        pytest.param("while(x){{y()}}", "while(x)y()", id="while-body"),
    ],
)
def test_nested_blocks_collapse_without_folding(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False, fold=False)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('function f(){{"use strict";x()}}', "function f(){x()}", id="flattened-string"),
        pytest.param("function f(){1}", "function f(){}", id="number"),
        pytest.param('a();"x"', "a()", id="trailing-string"),
        pytest.param("a();1;/re/;null;!0;void 0", "a()", id="each-constant"),
        pytest.param('for(;;)"x"', "for(;;);", id="loop-body"),
        pytest.param('if(a)"x";else b()', "a||b()", id="if-branch"),
        pytest.param('x="a"+"b";"a"+"b"', 'x="ab"', id="folded-concat"),
        pytest.param('"use strict";"x";f()', '"use strict";"x";f()', id="script-directives-kept"),
        pytest.param('function f(){"use strict";"x";g()}', 'function f(){"use strict";"x";g()}', id="directives-kept"),
        pytest.param("a();x;b()", "a(),x,b()", id="name-kept"),
    ],
)
def test_fold_drops_constant_statement(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == expected


@pytest.mark.parametrize(
    ("source", "options", "expected"),
    [
        pytest.param(
            'function f(){{"use strict";x()}}',
            JSMinify(mangle=False, fold=False),
            'function f(){{"use strict";x()}}',
            id="block-string",
        ),
        # a string that is no directive stays parenthesized when it lands at the start of a body
        pytest.param(
            '("use strict");a()', JSMinify(mangle=False, fold=False), '("use strict");a()', id="parenthesized"
        ),
        pytest.param(
            'function f(){var a;"use strict"}',
            JSMinify(fold=False),
            'function f(){("use strict")}',
            id="moved-by-mangle",
        ),
    ],
)
def test_constant_statement_kept_without_folding(source: str, options: JSMinify, expected: str) -> None:
    assert minify_js(source, options) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('function f(){{"use strict";x()}}', "function f(){x()}", id="constant-statement"),
        pytest.param("for(;;){{{}}}", "for(;;);", id="empty-loop-body"),
        pytest.param("do{}while(x)", "do;while(x)", id="empty-do-while-body"),
    ],
)
def test_constant_and_empty_body_reach_fixpoint(source: str, expected: str) -> None:
    once: Final[str] = minify_js(source)
    assert (once, minify_js(once)) == (expected, expected)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("(0,o.f)()", "(0,o.f)()", id="sequence-callee"),
        pytest.param("(0,0,o.f)()", "(0,o.f)()", id="long-sequence-callee"),
        pytest.param("((0,o.f))()", "(0,o.f)()", id="parenthesized-sequence-callee"),
        pytest.param("(1&&o.f)()", "(0,o.f)()", id="and-callee"),
        pytest.param("(0||o[k])()", "(0,o[k])()", id="or-computed-callee"),
        pytest.param("(null??o.f)()", "(0,o.f)()", id="nullish-callee"),
        pytest.param("(!0?o.f:0)()", "(0,o.f)()", id="conditional-callee"),
        pytest.param("(0,o?.f)()", "(0,o?.f)()", id="optional-member-callee"),
        pytest.param("(0,o.f)?.()", "(0,o.f)?.()", id="optional-call-callee"),
        pytest.param("(0,o.f)`x`", "(0,o.f)`x`", id="tag"),
        pytest.param("(0,eval)(s)", "(0,eval)(s)", id="indirect-eval"),
        pytest.param("(1&&eval)`s`", "(0,eval)`s`", id="eval-tag"),
        pytest.param("delete(0,o.f)", "delete(0,o.f)", id="delete-member"),
        pytest.param("delete(1&&x)", "delete(0,x)", id="delete-name"),
        pytest.param("typeof(0,x)", "typeof(0,x)", id="typeof-name"),
        pytest.param("typeof(0,o.f)", "typeof(0,o.f)", id="typeof-member"),
        pytest.param("(0,f)()", "f()", id="plain-name-callee-unwraps"),
        pytest.param("(0,o).f()", "o.f()", id="object-unwraps"),
        pytest.param("new(0,o.f)", "new o.f", id="new-callee-unwraps"),
        pytest.param("void(0,o.f)", "void o.f", id="void-operand-unwraps"),
        pytest.param("x=(0,o.f)", "x=o.f", id="assigned-value-unwraps"),
        pytest.param("o.f()", "o.f()", id="member-callee"),
        pytest.param("delete o.f", "delete o.f", id="delete-member-reference"),
    ],
)
def test_value_slot_keeps_value(source: str, expected: str) -> None:
    assert minify_js(source) == expected


def test_indirect_eval_leaves_locals_renamable() -> None:
    assert minify_js("function f(){var x=g();return(1&&eval)(x)}") == "function f(){var a=g();return(0,eval)(a)}"


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("console.log(true,false,!true,typeof undefined,void 0===undefined)", id="literals"),
        pytest.param(
            "function f(){const x=1,z=2;const inner=x=>[x,z,z];return[x,x,{x},inner(5),z]}"
            "console.log(JSON.stringify(f()))",
            id="propagation-plan-shadowing",
        ),
        pytest.param(
            "const trace=[];function f(g){const x=1;g(x);let y=2;y++;return[x,x,y]}"
            "console.log(JSON.stringify([f(x=>trace.push(x)),trace]))",
            id="propagation-plan-assignment-order",
        ),
        pytest.param(
            "const external=9;function f(){const x=1;return[{x,external},x,x]}console.log(JSON.stringify(f()))",
            id="propagation-plan-shorthand",
        ),
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
        pytest.param(
            "var o={f(){return this===o}};console.log((0,o.f)(),(1&&o.f)(),(!0?o.f:0)(),(0,o.f)`x`)",
            id="value-callee-this",
        ),
        pytest.param("var o={f:1};console.log(delete(0,o.f),delete(1&&o.f),o.f)", id="value-delete-operand"),
        pytest.param(
            "var x=1;function t(){var x=2;return[(0,eval)('x'),(1&&eval)('x')]}console.log(t())", id="indirect-eval"
        ),
        pytest.param('function f(){"use "+"strict";return typeof this}console.log(f())', id="folded-no-strict"),
        pytest.param('function f(){if(0){}"use strict";return typeof this}console.log(f())', id="dropped-no-strict"),
        pytest.param('function f(){"use strict";"a";return typeof this}console.log(f())', id="directive-strict"),
        pytest.param('function f(){{"use strict";return typeof this}}console.log(f())', id="block-string-no-strict"),
        pytest.param("var n=0;do{}while(n++<3);for(;n++<9;){}console.log(n)", id="empty-loop-bodies"),
        pytest.param(
            "function f(g,x){if(g)if(x)return'r';else console.log('h');return'e'}console.log(f(1,0),f(1,1),f(0,0))",
            id="lone-else-splice",
        ),
        pytest.param(
            "function f(x,c){var n=0;do if(x)break;else n++;while(n<c);return n}console.log(f(0,3),f(1,3))",
            id="lone-else-splice-do-while",
        ),
        pytest.param(
            "var log=[];class C{static a=log.push(1);static{};static b=log.push(2)}console.log(log.join())",
            id="empty-static-block",
        ),
        pytest.param(
            "function X(a){if(!arguments.length)return function(b){this.b=b};this.a=a}"
            "console.log(JSON.stringify([new(new X)(1),new X(2).a,typeof new X]))",
            id="new-arguments",
        ),
        pytest.param("var f=async()=>1;f().then(console.log)", id="async-arrow"),
        pytest.param(
            "function k(x){function f(){}var s=new Set;do s.add(f);while(x--);return s.size}console.log(k(3))",
            id="do-while-closure-identity",
        ),
        pytest.param(
            "var log=[];function g(...v){log.push(v);return v.length}"
            "function f(a,b){return[a===1?g(b,1):g(b,2),!a||b?g(3):g(4)]}"
            "console.log(JSON.stringify([f(1,0),f(2,5),f(0,0),log]))",
            id="hoisted-call",
        ),
        pytest.param(
            "var log=[];function g(v){log.push(v);return v}"
            "function f(a){if(a===1)return void 0;if(a===2)return;g(a);return a>3?g(a+1):void 0}"
            "function*h(a){if(a)return void 0;yield 1}"
            "console.log(JSON.stringify([f(1),f(2),f(3),f(4),log,[...h(0)],[...h(1)]]))",
            id="undefined-returns",
        ),
        pytest.param(
            "var o={valueOf(){return 2}};"
            "console.log(+o===+'2',1/+o===1/o,''+o===o+'',o-0===2,delete o.p===!0,(o<3)===('valueOf'in o),"
            "o>>>0===2>>>0,typeof(+'x'),+'x'!==+'x')",
            id="same-type-equality",
        ),
    ],
)
def test_folding_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))


_BS: Final[str] = chr(0x5C)  # backslash, kept out of the literals so every escape is unambiguous
_CR: Final[str] = chr(0x0D)
_LF: Final[str] = chr(0x0A)
_LS: Final[str] = chr(0x2028)
_PS: Final[str] = chr(0x2029)


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
        pytest.param(f'"{_BS}u0041"+"z"', r'"Az"', id="unicode-escape"),
        pytest.param(f'"{_BS}u{{1F600}}"+"!"', '"\U0001f600!"', id="unicode-braced-astral"),
        pytest.param(r'"\n"+"a"', r'"\na"', id="newline"),
        pytest.param(r'"\r"+"a"', r'"\ra"', id="carriage-return"),
        pytest.param(r'"\t"+"a"', r'"\ta"', id="tab"),
        pytest.param(r'"\b"+"a"', r'"\ba"', id="backspace"),
        pytest.param(r'"\f"+"a"', r'"\fa"', id="form-feed"),
        pytest.param(r'"\v"+"a"', r'"\va"', id="vertical-tab"),
        pytest.param(r'"a\\b"+"c"', r'"a\\bc"', id="backslash"),
        pytest.param(r'"x"+"a\"b"', """'xa"b'""", id="escaped-quote-requoted"),
        pytest.param(r'"\q"+"z"', r'"qz"', id="unknown-escape-is-literal"),
        pytest.param(f'"{_BS}u2028"+"a"', f'"{_BS}u2028a"', id="line-separator-value"),
        pytest.param(f'"{_BS}u2029"+"a"', f'"{_BS}u2029a"', id="paragraph-separator-value"),
        # a LineContinuation contributes nothing to the value
        pytest.param(f'"a{_BS}{_LF}b"+"c"', r'"abc"', id="lf-continuation"),
        pytest.param(f'"a{_BS}{_CR}b"+"c"', r'"abc"', id="cr-continuation"),
        pytest.param(f'"a{_BS}{_CR}"+"z"', r'"az"', id="cr-continuation-at-end"),
        pytest.param(f'"a{_BS}{_CR}{_LF}b"+"c"', r'"abc"', id="crlf-continuation"),
        pytest.param(f'"a{_BS}{_LS}b"+"c"', r'"abc"', id="ls-continuation"),
        pytest.param(f'"a{_BS}{_PS}b"+"c"', r'"abc"', id="ps-continuation"),
        pytest.param(r'"ab"+"cd"', r'"abcd"', id="plain"),
        # the joined value may not spell a `</script` or `<!--` the HTML parser would act on inside <script>
        pytest.param(r'"</scr"+"ipt>"', r'"\x3c/script>"', id="script-end-tag"),
        pytest.param(r'"</SCR"+"IPT>"', r'"\x3c/SCRIPT>"', id="script-end-tag-uppercase"),
        pytest.param(r'"<!-"+"-"', r'"\x3c!--"', id="html-comment-open"),
        pytest.param(r'"</scr"+"i"', r'"</scri"', id="script-end-tag-prefix-kept"),
        pytest.param(r'"<"+"b>"', r'"<b>"', id="less-than-kept"),
    ],
)
def test_concat_reencodes_by_value(source: str, expected: str) -> None:
    assert minify_js(f"x={source}") == f"x={expected}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(f'x="{_BS}xc0"', 'x="\u00c0"', id="hex-to-two-bytes"),
        pytest.param(f'x="{_BS}u2019"', 'x="\u2019"', id="unicode-to-three-bytes"),
        pytest.param(f'x="{_BS}ud83d{_BS}ude00"', 'x="\U0001f600"', id="surrogate-pair-to-four-bytes"),
        pytest.param(f'x="{_BS}u{{1F600}}"', 'x="\U0001f600"', id="braced-astral"),
        pytest.param(f'x="{_BS}ud83d"', f'x="{_BS}ud83d"', id="lone-high-surrogate-kept"),
        pytest.param(f'x="{_BS}ud83d"+"a"', f'x="{_BS}ud83da"', id="lone-high-surrogate-joined"),
        pytest.param(f'x="{_BS}ude00"+"a"', f'x="{_BS}ude00a"', id="lone-low-surrogate-joined"),
        pytest.param(f'x="{_BS}x00"', f'x="{_BS}0"', id="nul"),
        pytest.param(f'x="{_BS}x00a"', f'x="{_BS}0a"', id="nul-before-letter"),
        pytest.param(f'x="{_BS}ud83d{_BS}uff21"', f'x="{_BS}ud83d\uff21"', id="high-surrogate-before-non-surrogate"),
        pytest.param(f'x="{_BS}x00"+"1"', f'x="{_BS}x001"', id="nul-before-digit"),
        pytest.param(f'x="a{_BS}"b"', "x='a\"b'", id="double-quote-swapped"),
        pytest.param(f"x='a{_BS}'b'", 'x="a\'b"', id="single-quote-swapped"),
        pytest.param(f'x="{_BS}"\'{_BS}x41"', f'x="{_BS}"\'A"', id="quote-tie-keeps-quote"),
        pytest.param(f'x="{_BS}n"', f'x="{_BS}n"', id="escape-already-shortest"),
        pytest.param(f'x="{_BS}xZZ"', f'x="{_BS}xZZ"', id="valueless-kept"),
        pytest.param(f'x={{"{_BS}x41":1}}', "x={A:1}", id="object-key"),
        pytest.param(f'class A{{"{_BS}x41"(){{}}}}', "class A{A(){}}", id="class-member-key"),
        pytest.param(
            f'function f(){{"{_BS}x41";return 1}}', f'function f(){{"{_BS}x41";return 1}}', id="directive-kept"
        ),
    ],
)
def test_string_literal_respelled_by_value(source: str, expected: str) -> None:
    assert minify_js(source) == expected


# A hex or unicode escape that is malformed, or names a value past U+10FFFF, has no value to join by, and one past
# U+10FFFF would otherwise reach the returned str: the operands stay as written.
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(f'"{_BS}u{{110000}}"+"a"', id="past-the-last-code-point"),
        pytest.param(f'"{_BS}u{{7FFFFFFF}}"+"a"', id="31-bit-value"),
        pytest.param(f'"{_BS}u{{100000041}}"+"a"', id="value-wrapping-32-bits"),
        pytest.param(f'"a"+"{_BS}u{{110000}}"', id="right-operand"),
        pytest.param(f'"{_BS}u{{}}"+"a"', id="empty-braces"),
        pytest.param(f'"{_BS}u{{41"+"a"', id="unterminated-braces"),
        pytest.param(f'"{_BS}u{{4g}}"+"a"', id="non-hex-in-braces"),
        pytest.param(f'"{_BS}u"+"a"', id="unicode-no-digits"),
        pytest.param(f'"{_BS}u004"+"a"', id="unicode-three-digits"),
        pytest.param(f'"{_BS}u004g"+"a"', id="unicode-non-hex-digit"),
        pytest.param(f'"{_BS}u{chr(0x10FFFF) * 4}"+"a"', id="unicode-astral-digits"),
        pytest.param(f'"{_BS}x"+"a"', id="hex-no-digits"),
        pytest.param(f'"{_BS}x4"+"a"', id="hex-one-digit"),
        pytest.param(f'"{_BS}x4g"+"a"', id="hex-non-hex-digit"),
        pytest.param(f'"{_BS}x{chr(0x10FFFF) * 2}"+"a"', id="hex-astral-digits"),
    ],
)
def test_concat_keeps_operands_with_a_valueless_escape(source: str) -> None:
    assert minify_js(f"x={source}") == f"x={source}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(f'"{_BS}u{{10FFFF}}"+"a"', f'"{chr(0x10FFFF)}a"', id="last-code-point"),
        pytest.param(f'"{_BS}u{{0000000041}}"+"a"', '"Aa"', id="braced-leading-zeros"),
        pytest.param(f'"{_BS}x4a"+"{_BS}x4B"', '"JK"', id="hex-either-case"),
        pytest.param(f'"{_BS}x414"+"a"', '"A4a"', id="hex-takes-two-digits"),
        pytest.param(f'"{_BS}u00414"+"a"', '"A4a"', id="unicode-takes-four-digits"),
    ],
)
def test_concat_decodes_escape_at_its_bounds(source: str, expected: str) -> None:
    assert minify_js(f"x={source}") == f"x={expected}"


@pytest.mark.parametrize(
    "operand",
    [
        pytest.param(f'"{"a" * 640}"', id="literal"),
        pytest.param(f'("{"a" * 320}"+"{"a" * 320}")', id="folded"),
        pytest.param(f'(0?"x"+"y":"{"a" * 640}")', id="beside-a-dropped-fold"),
    ],
)
def test_concat_chain_frees_the_lexemes_it_supersedes(operand: str) -> None:
    # every `+` of a chain joins the whole prefix again; keeping each replaced prefix until the call returned made a
    # chain of n literals hold n/2 copies of its value
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")  # PyPy has none
    source: Final[str] = f"x={'+'.join([operand] * 90)}"
    tracing.start()
    try:
        result: Final[str] = minify_js(source)
        peak: Final[int] = tracing.get_traced_memory()[1]
    finally:
        tracing.stop()
    assert (result, peak < 100 * len(source)) == (f'x="{"a" * 640 * 90}"', True), peak


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('x="a"+"b"+y+("c"+"d")', 'x="ab"+y+"cd"', id="operand-after-prefix-keeps-its-fold"),
        pytest.param('x="a"+"b"+"\\xZZ"+"c"', 'x="ab"+"\\xZZ"+"c"', id="valueless-escape-after-prefix"),
        pytest.param('x="a"+"b"+\'c\'+"d"', 'x="abcd"', id="quote-change-after-prefix"),
        pytest.param('x=1+2+"a"+"b"', 'x=3+"a"+"b"', id="integer-prefix-then-string"),
        pytest.param('x="a"+y+"b"+"c"', 'x="a"+y+"b"+"c"', id="string-after-non-string-not-joined"),
        pytest.param('x=a*b+"c"+"d"', 'x=a*b+"c"+"d"', id="product-prefix-not-joined"),
        pytest.param('f(f(f(f(0))));x="a"+"b"+"c"', 'f(f(f(f(0)))),x="abc"', id="chain-after-deeper-statement"),
    ],
)
def test_concat_chain_joins_its_string_prefix(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.parametrize("operand", [pytest.param('"ab"', id="short"), pytest.param(f'"{"a" * 40}"', id="long")])
def test_concat_chain_joins_in_linear_steps(operand: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(minify_js, f"x={'+'.join([operand] * n)}"), (200, 800))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # truthiness reads the decoded value, so a line-continuation-only string (value "") is falsy (#436)
        pytest.param(f'"{_BS}{_LF}"?1:2', "2", id="lf-only-empty-falsy"),
        pytest.param(f'"{_BS}{_CR}"?1:2', "2", id="cr-only-empty-falsy"),
        pytest.param(f'"{_BS}{_CR}{_LF}"?1:2', "2", id="crlf-only-empty-falsy"),
        pytest.param(f'"{_BS}{_LS}"?1:2', "2", id="ls-only-empty-falsy"),
        pytest.param(f'"{_BS}{_PS}"?1:2', "2", id="ps-only-empty-falsy"),
        pytest.param('""?1:2', "2", id="empty-literal-falsy"),
        pytest.param(r'"a"?1:2', "1", id="nonempty-truthy"),
        pytest.param(r'"\n"?1:2', "1", id="escape-value-truthy"),
        pytest.param(f'"a{_BS}{_LF}"?1:2', "1", id="continuation-plus-content-truthy"),
        pytest.param(f'"{_BS}{_CR}x"?1:2', "1", id="cr-continuation-then-content-truthy"),
        # a raw line separator would print as a longer escape, so these literals keep their spelling, continuation
        # included, and the truthiness read steps over it
        pytest.param(f'"{_BS}{_LF}{_LS}"?1:2', "1", id="kept-continuation-then-content-truthy"),
        pytest.param(f'"{_BS}{_CR}{_LF}{_LS}"?1:2', "1", id="kept-crlf-continuation-then-content-truthy"),
        pytest.param(f'"{_BS}{_CR}{_LS}"?1:2', "1", id="kept-cr-continuation-then-content-truthy"),
        pytest.param(f'"{_BS}{_LS}{_LS}{_LS}"?1:2', "1", id="kept-ls-continuation-then-content-truthy"),
        pytest.param(f'"{_BS}{_PS}{_LS}{_LS}"?1:2', "1", id="kept-ps-continuation-then-content-truthy"),
    ],
)
def test_string_truthiness_reads_value(source: str, expected: str) -> None:
    assert minify_js(f"x={source}") == f"x={expected}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "f(typeof undefined,function(undefined){return undefined}(3))",
            "f(typeof void 0,function(a){return a}(3))",
            id="global-read-beside-parameter",
        ),
        pytest.param(
            "function g(undefined){return()=>undefined}f(undefined)",
            "function g(a){return()=>a}f(void 0)",
            id="closure-read-keeps-binding",
        ),
        pytest.param(
            "try{g()}catch(undefined){f(undefined)}f(undefined)",
            "try{g()}catch(a){f(a)}f(void 0)",
            id="catch-parameter",
        ),
        pytest.param(
            "with(o)f(undefined);(function(undefined){f(undefined)})(4)",
            "with(o)f(undefined);(function(undefined){f(undefined)}(4))",
            id="with-keeps-every-read",
        ),
    ],
)
def test_undefined_folds_where_resolution_proves_global(source: str, expected: str) -> None:
    assert (minify_js(source), minify_js(expected)) == (expected, expected)


def test_undefined_kept_without_mangling_when_shadowed() -> None:
    source: Final[str] = "f(typeof undefined,function(undefined){return undefined}(3))"
    assert minify_js(source, JSMinify(mangle=False)) == source


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
        pytest.param(f'"{_BS}u0041"+"1"', id="unicode-then-digit"),
        pytest.param(f'"a{_BS}{_CR}{_LF}b"+"3"', id="crlf-then-digit"),
        pytest.param(r'"</scr"+"ipt><!-"+"-"', id="script-end-tag-and-comment-open"),
    ],
)
def test_concat_matches_node(source: str) -> None:
    minified = minify_js(f"x={source}").removeprefix("x=")
    assert _run(f"console.log(({source}))") == _run(f"console.log(({minified}))")


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_raw_line_separators_in_strings_match_node() -> None:
    snippet: Final[str] = f"var s='a{_LS}b'+'{_PS}';console.log(s.length,s.charCodeAt(1),s.charCodeAt(3))"
    assert _run(snippet) == _run(minify_js(snippet))


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="single"), pytest.param(17, id="heap"), pytest.param(512, id="long")]
)
def test_guard_chain_growth_preserves_return_order(count: int) -> None:
    source: Final[str] = (
        f"function f(x){{{''.join(f'if(x==={index})return g({index});' for index in range(count))}return g(-1)}}"
    )
    expected: Final[str] = f"function f(a){{return g({''.join(f'a==={index}?{index}:' for index in range(count))}-1)}}"
    assert minify_js(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "function f(){if(a){while(b)g()}else{while(c)h()}if(d)return 1;return 2}",
            "function f(){if(a)for(;b;)g();else for(;c;)h();return d?1:2}",
            id="preserve-else",
        ),
        pytest.param(
            "function f(){if(a)while(b)g();if(c)return 1;return 2}",
            "function f(){if(a)for(;b;)g();return c?1:2}",
            id="preserve-loop",
        ),
        pytest.param("function f() { if (a) return g(); }", "function f(){if(a)return g()}", id="last-guard"),
        pytest.param("function f(){if(a)return 1;return}", "function f(){if(a)return 1}", id="void-return"),
    ],
)
def test_guard_chain_growth_preserves_unfolded_statements(source: str, expected: str) -> None:
    assert minify_js(source) == expected


# a statement list is flat, so the parser's nesting cap never meters its length: the guard fold must not turn that
# length into a tree nested once per guard, which the fold, mangle and print walks then overflow the C stack on
@pytest.mark.parametrize(
    "guard",
    [
        pytest.param("if(c)return 4;", id="chain-in-alternate"),
        pytest.param("if(!c)return 4;", id="chain-in-consequent"),
        pytest.param("if(d)return d;", id="chain-refolds-to-logical"),
    ],
)
@pytest.mark.parametrize(
    "options",
    [pytest.param(JSMinify(), id="mangle"), pytest.param(JSMinify(mangle=False), id="fold-only")],
)
def test_guard_chain_nesting_is_capped(guard: str, options: JSMinify) -> None:
    minified: Final[str] = minify_js(f"function f(){{{guard * 3_000}return 2}}", options)
    assert minified.count("if(") == 2_104  # the guards past the cap stay statements
    assert minify_js(minified, options) == minified


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        pytest.param("return function(){", "}", id="rest"),
        pytest.param("if(!c)return function(){", "};return 3", id="branch"),
        pytest.param("if(function(){", "}())return 4;return 3", id="test"),
    ],
)
def test_guard_chain_nesting_cap_spans_nested_functions(opening: str, closing: str) -> None:
    # each `x();` holds the guards before it back one pass, so a function folds its long run a pass after the function
    # inside it did: only a depth counted from the program root keeps the chains from stacking one under the other
    body = "return 2"
    for delay in range(11, 0, -1):
        body = "if(!c)return 4;" * 880 + "if(!c)return 4;x();" * delay + opening + body + closing
    minified: Final[str] = minify_js(f"function f(){{{body}}}")
    assert minify_js(minified) == minified  # the parser takes it back: no chain nests past its cap


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_guard_chain_past_the_cap_preserves_behavior(tmp_path: Path) -> None:
    guards: Final[str] = "".join(
        f"if(x==={index})return g({index});if(!(x!=={index + 1}))return-{index};" for index in range(0, 2000, 2)
    )
    calls: Final[str] = "console.log([0,1,2,999,1100,1101,1998,1999,2000].map(f).join())"
    source: Final[str] = f"function g(v){{return v+1}}function f(x){{{guards}return g(-2)}}{calls}"
    minified: Final[str] = minify_js(source)
    assert 0 < minified.count("if(") < 2000  # some guards folded, the rest stayed statements
    script: Final[Path] = tmp_path / "guard_chain.js"
    assert _run_file(script, minified) == _run_file(script, source)


def _run_file(script: Path, code: str) -> str:
    assert _NODE is not None  # the callers are skipped when node is unavailable
    # a script past the Windows command-line limit (32767 characters) cannot go through `node -e`
    script.write_text(code, encoding="utf-8")
    result = subprocess.run([_NODE, str(script)], capture_output=True, text=True, timeout=300, check=False)  # ruff:ignore[subprocess-without-shell-equals-true] - fixed argv, test-written script
    return result.stdout + result.stderr


def test_logical_groups_do_not_join_past_the_nesting_cap() -> None:
    # the parser meters each parenthesised group on its own; rotated into one spine they would nest once per operand
    group: Final[str] = "&&".join(["b"] * 650)
    groups: Final[str] = "&&".join([f"({group})"] * 6)
    minified: Final[str] = minify_js(f"x={groups}")
    assert (minified.count("("), minified.count("&&")) == (5, 3899)


def test_logical_groups_join_up_to_the_nesting_cap() -> None:
    group: Final[str] = "||".join(["a"] * 100)
    groups: Final[str] = "||".join([f"({group})"] * 40)
    minified: Final[str] = minify_js(f"x={groups}")
    assert (minified.count("("), minified.count("||")) == (32, 3999)


def test_function_inline_does_not_stack_nesting() -> None:
    # each declaration is read under 600 brackets: inlined, the bodies would hang one under the other, 600 levels apart
    body = "return 1"
    for number in range(14, 0, -1):
        body = f"function f{number}(){{{body}}}return {'[' * 600}f{number}{']' * 600}"
    minified: Final[str] = minify_js(f"function o(){{{body}}}")
    assert (minified.count("function("), minify_js(minified)) == (1, minified)


def test_function_inline_under_a_short_expression_still_nests() -> None:
    body = "return 1"
    for number in range(11, 0, -1):
        body = f"function f{number}(){{{body}}}return {'[' * 3}f{number}{']' * 3}"
    opened: Final[str] = "return[[[function(){" * 11
    closed: Final[str] = "}]]]" * 11
    assert minify_js(f"function o(){{{body}}}") == f"function o(){{{opened}return 1{closed}}}"


def test_labeled_function_declaration_is_not_measured_for_inlining() -> None:
    # a labeled declaration sits outside any statement list, so no binding owns it and nothing inlines it
    assert minify_js("function o(){L:function f(){}return f}") == "function o(){a:function f(){}return f}"


@pytest.mark.parametrize("count", [pytest.param(2, id="small"), pytest.param(1000, id="long")])
@pytest.mark.parametrize("nested", [pytest.param(False, id="calls"), pytest.param(True, id="sequences")])
def test_sequence_growth_preserves_call_order(count: int, *, nested: bool) -> None:
    expressions: Final[list[str]] = [f"f({index}),g({index})" if nested else f"f({index})" for index in range(count)]
    assert minify_js(";".join(expressions)) == ",".join(expressions)


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "function f(n){switch(n){case 1:const x=1;return x;case 2:return[x,x]}}"
            "try{f(2)}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-switch-skips-initialization",
        ),
        pytest.param(
            "function f(n){switch(n){case 1:const x=1;return 0;case 2:return x}}"
            "try{f(2)}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-switch-skips-single-initialization",
        ),
        pytest.param(
            "function f(n){switch(n){case 1:const x=1;case 2:return[x,x]}}console.log(JSON.stringify(f(1)))",
            "[1,1]\n",
            id="declarator-switch-fallthrough-initialization",
        ),
        pytest.param(
            "function f(){g();const x=1;function g(){return[x,x]}}try{f()}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-capture-called-before-initialization",
        ),
        pytest.param(
            "function f(){g();const x=1;function g(){return x}}try{f()}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-single-capture-called-before-initialization",
        ),
        pytest.param(
            "function f(){g();const x=1;function g(){return[x,x]}return[x,x]}"
            "try{f()}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-mixed-capture-scopes",
        ),
        pytest.param(
            "function f(){const y=g(),x=1;function g(){return[x,x]}return y}"
            "try{f()}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-capture-called-in-earlier-initializer",
        ),
        pytest.param(
            "function f(){const y=g(),x=1;function g(){return x}return y}try{f()}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-single-capture-called-in-earlier-initializer",
        ),
        pytest.param(
            "function f(){const x=1;return(()=>[x,x])()}console.log(JSON.stringify(f()))",
            "[1,1]\n",
            id="declarator-capture-after-first-initializer",
        ),
        pytest.param(
            "function f(){const x=y,y=1;return[x]}try{f()}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-single-read-tdz",
        ),
        pytest.param(
            "function f(){const result=[x,x];var x=1;return result}console.log(JSON.stringify(f()))",
            "[null,null]\n",
            id="declarator-var-hoisting",
        ),
        pytest.param(
            "function f(){function g(){return[x,x]}const x=1;return g()}console.log(JSON.stringify(f()))",
            "[1,1]\n",
            id="declarator-capture-before-initialization",
        ),
        pytest.param(
            "function f(){var x=1,x;return[x,x]}console.log(JSON.stringify(f()))",
            "[1,1]\n",
            id="declarator-first-repeated-target",
        ),
        pytest.param(
            "function f(){var x=1;var x;return[x,x]}console.log(JSON.stringify(f()))",
            "[1,1]\n",
            id="declarator-later-bare-statement",
        ),
        pytest.param(
            "function f(){var x=1,x=2;return[x,x]}console.log(JSON.stringify(f()))",
            "[2,2]\n",
            id="declarator-reinitialization",
        ),
        pytest.param(
            "function f(){const x=1,{y}={y:2},z=3;return[()=>[x,y,z],x,z]}"
            "const result=f();console.log(JSON.stringify([result[0](),result.slice(1)]))",
            "[[1,2,3],[1,3]]\n",
            id="declarator-destructuring-capture",
        ),
        pytest.param(
            "function f(){const x=y,y=1;return[y,y]}try{f()}catch(error){console.log(error.name)}",
            "ReferenceError\n",
            id="declarator-tdz",
        ),
        pytest.param(
            "const trace=[];function f(g){const x=1,y=g(2),z=3;return[{x,z},x,z,y]}"
            "console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))",
            '[[{"x":1,"z":3},1,3,2],[2]]\n',
            id="declarator-side-effect-order-shorthand",
        ),
        pytest.param(
            "function f(){var y=g(),x=1;function g(){return x}return y}console.log(JSON.stringify(f()))",
            "undefined\n",
            id="captured-var-before-initialization",
        ),
        pytest.param(
            "function f(flag,text){const pattern=/x/;return flag?text:pattern.test(text)}"
            "console.log(JSON.stringify([f(true,'x'),f(false,'x'),f(false,'y')]))",
            '["x",true,false]\n',
            id="regex-conditional-alternate",
        ),
        pytest.param(
            "function f(g){const a=g(1),b=2,{c}={c:g(3)},d=4,e=g(5);return[a,b,b,c,d,d,e]}"
            "const trace=[];console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))",
            "[[1,2,2,3,4,4,5],[1,3,5]]\n",
            id="propagation-retains-destructuring-order",
        ),
        pytest.param(
            "function f(g){const dead=0,keep=g(1),a=2,b=3;return[keep,a,b]}"
            "const trace=[];console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))",
            "[[1,2,3],[1]]\n",
            id="predecessors-after-dead-head",
        ),
        pytest.param(
            "function f(g){const a=1,{keep}={keep:g(2)},b=3;return[a,keep,b]}"
            "const trace=[];console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))",
            "[[1,2,3],[2]]\n",
            id="predecessors-around-destructuring",
        ),
        pytest.param(
            "const trace=[];function mark(x){trace.push(x);return x}"
            "var first=mark(1);var second=mark(2);var third;for(var fourth=mark(3);false;);"
            "console.log(JSON.stringify([trace,first,second,third,fourth]))",
            "[[1,2,3],1,2,null,3]\n",
            id="merge-declarations-before-for-init",
        ),
        pytest.param(
            "const trace=[];function mark(x){trace.push(x);return x}"
            "let first=mark(1);let second=mark(2);const third=mark(3);const fourth=mark(4);"
            "console.log(JSON.stringify([trace,first,second,third,fourth]))",
            "[[1,2,3,4],1,2,3,4]\n",
            id="merge-declarations-stops-at-kind-change",
        ),
        pytest.param(
            "const trace=[];function mark(x){trace.push(x);return{x}}"
            "const {x:first}=mark(1);const {x:second}=mark(2);const {x:third}=mark(3);"
            "console.log(JSON.stringify([trace,first,second,third]))",
            "[[1,2,3],1,2,3]\n",
            id="merge-declarations-keeps-destructuring-order",
        ),
        pytest.param(
            "var first=console.log(1);var second=console.log(2)",
            "1\n2\n",
            id="merge-declarations-at-end",
        ),
    ],
)
def test_declaration_reads_preserve_behavior(source: str, expected: str) -> None:
    assert (_run(source), _run(minify_js(source))) == (expected, expected)


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    ("case", "count"),
    [
        pytest.param(0, 256, id="grouped"),
        pytest.param(1, 256, id="separate"),
        pytest.param(2, 128, id="one-declaration"),
    ],
)
def test_single_use_initializers_preserve_call_order(case: int, count: int) -> None:
    source: Final[Case] = INPUTS["minify-js-single-use"]()[case][1]
    assert isinstance(source, str)
    program: Final[str] = (
        f"{source};const trace=[];console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))"
    )
    expected: Final[list[int]] = list(range(count))
    assert _run(minify_js(program)) == f"{json.dumps([expected, expected], separators=(',', ':'))}\n"


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    ("case", "count"),
    [
        pytest.param(0, 256, id="grouped"),
        pytest.param(1, 256, id="separate"),
        pytest.param(2, 64, id="one-declaration"),
    ],
)
def test_propagation_unlink_preserves_call_order(case: int, count: int) -> None:
    source: Final[Case] = INPUTS["minify-js-unlink"]()[case][1]
    assert isinstance(source, str)
    program: Final[str] = (
        f"{source};const trace=[];console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))"
    )
    expected: Final[str] = (
        f"""{
            json.dumps(
                [[value for index in range(count) for value in (index, index % 10, index % 10)], list(range(count))],
                separators=(",", ":"),
            )
        }"""
        "\n"
    )
    assert (_run(program), _run(minify_js(program))) == (expected, expected)


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    ("case", "rows"),
    [
        pytest.param(
            0,
            [[value for index in range(256) for value in (index, index % 10)], list(range(256))],
            id="many",
        ),
        pytest.param(1, [[v for index in range(80) for v in (index, index % 10)], list(range(80))], id="80"),
        pytest.param(2, [[None] * 256, [None] * 256], id="early"),
    ],
)
def test_var_initialization_benchmark_order(case: int, rows: list[list[int | None]]) -> None:
    source: Final[Case] = INPUTS["minify-js-var-initialization"]()[case][1]
    assert isinstance(source, str)
    program: Final[str] = (
        f"{source};const trace=[];console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))"
    )
    expected: Final[str] = f"{json.dumps(rows, separators=(',', ':'))}\n"
    assert (_run(program), _run(minify_js(program))) == (expected, expected)


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(("case", "count"), [pytest.param(0, 256, id="many"), pytest.param(1, 96, id="few")])
def test_unused_declarators_preserve_call_order(case: int, count: int) -> None:
    source: Final[Case] = INPUTS["minify-js-unused-declarations"]()[case][1]
    assert isinstance(source, str)
    program: Final[str] = (
        f"{source};const trace=[];console.log(JSON.stringify([f(value=>(trace.push(value),value)),trace]))"
    )
    expected: Final[str] = f"{json.dumps([[], list(range(count))], separators=(',', ':'))}\n"
    assert (_run(program), _run(minify_js(program))) == (expected, expected)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "function f(a){a=2;return arguments[0]}", "function f(a){return a=2,arguments[0]}", id="store-then-read"
        ),
        pytest.param(
            "function f(a){a=2;return()=>arguments}", "function f(a){return a=2,()=>arguments}", id="arrow-read"
        ),
        pytest.param(
            "function f(a){var a=2;return arguments}", "function f(a){var a=2;return arguments}", id="var-redeclared"
        ),
        pytest.param(
            "function f(a){(a=g(),a);return arguments}",
            "function f(a){return a=g(),arguments}",
            id="assign-then-read-sequence",
        ),
        pytest.param(
            "function f(a){a=2;return function(){return arguments}}",
            "function f(a){return function(){return arguments}}",
            id="nested-function-own-arguments",
        ),
        pytest.param(
            "function f(a){var arguments;a=2;return arguments}",
            "function f(b){var a;return a}",
            id="local-named-arguments",
        ),
        pytest.param(
            "function f(a,b){a=2;b=3;return[arguments,arguments,arguments]}",
            "function f(a,b){return a=2,b=3,[arguments,arguments,arguments]}",
            id="store-then-repeated-reads",
        ),
        pytest.param(
            "function f(a){a=2;return()=>[()=>arguments,arguments]}",
            "function f(a){return a=2,()=>[()=>arguments,arguments]}",
            id="repeated-arrow-reads",
        ),
        pytest.param(
            "function f(a){a=2;return function(b){b=3;return[arguments,arguments]}(arguments)}",
            "function f(a){return a=2,function(a){return a=3,[arguments,arguments]}(arguments)}",
            id="repeated-reads-per-function",
        ),
        pytest.param("arguments;function f(a){a=2}", "arguments;function f(a){}", id="top-level-arguments"),
        pytest.param(
            "function f(a){var b=1;return arguments}", "function f(a){return arguments}", id="local-var-still-dropped"
        ),
    ],
)
def test_arguments_keeps_parameter_stores(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("console.log(function(a){a=2;return arguments[0]}(1))", id="store-then-read"),
        pytest.param("console.log(function(a,b){b=3;return[].slice.call(arguments)}(1,2))", id="second-parameter"),
        pytest.param("console.log(function(a){a=2;return(()=>arguments[0])()}(1))", id="arrow-read"),
        pytest.param("console.log(function(a){var a=2;return arguments[0]}(1))", id="var-redeclared"),
    ],
)
def test_arguments_alias_preserves_behavior(snippet: str) -> None:
    assert _run(snippet) == _run(minify_js(snippet))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "function f(){if(g())if(x)return;else h()}", "function f(){if(g()){if(x)return;h()}}", id="if-branch"
        ),
        pytest.param(
            "function f(){if(a)if(x)return;else h();else k()}",
            "function f(){if(a){if(x)return;h()}else k()}",
            id="if-branch-before-else",
        ),
        pytest.param(
            "function f(){if(a)k();else if(x)return;else h()}",
            "function f(){if(a)k();else{if(x)return;h()}}",
            id="else-branch",
        ),
        pytest.param(
            "L:while(++b<2)while(1)if(!b)continue L;else break L",
            "L:for(;++b<2;)for(;;){if(!b)continue L;break L}",
            id="loop-body",
        ),
        pytest.param("do if(x)break;else h();while(c)", "do{if(x)break;h()}while(c)", id="do-while-body"),
        pytest.param("function f(){L:if(x)break L;else h()}", "function f(){L:{if(x)break L;h()}}", id="label-body"),
        pytest.param(
            "function f(){with(o)if(x)return;else h()}", "function f(){with(o){if(x)return;h()}}", id="with-body"
        ),
        pytest.param(
            "function f(){for(;;)if(x)return 1;else return 2}", "function f(){for(;;)return x?1:2}", id="returns-fold"
        ),
        pytest.param(
            "function f(){if(g())if(x)return;else{let a=h();k(a)}}",
            "function f(){if(g()){if(x)return;{let a=h();k(a)}}}",
            id="lexical-alternate-keeps-its-block",
        ),
        pytest.param(
            "function f(){if(g())if(x)return;else function k(){}}",
            "function f(){if(g())if(x)return;else function k(){}}",
            id="annex-b-lone-else-function",
        ),
        pytest.param(
            "function f(){if(a)return 1;else if(b)return 2;else h()}",
            "function f(){if(a)return 1;if(b)return 2;h()}",
            id="else-if-chain-splices",
        ),
        pytest.param(
            "function f(){if(a)return;else function g(){}}",
            "function f(){if(a)return;else function g(){}}",
            id="annex-b-else-function",
        ),
        pytest.param("function f(){if(a)g();else h();i()}", "function f(){a?g():h(),i()}", id="falling-through"),
        pytest.param(
            "function f(){if(a)return;else{b();c()}d()}", "function f(){a||(b(),c(),d())}", id="block-alternate"
        ),
        pytest.param(
            "function f(){if(a)return;else{let x=g();h(x)}d()}",
            "function f(){if(!a){{let x=g();h(x)}d()}}",
            id="lexical-block-alternate",
        ),
    ],
)
def test_fold_splices_abrupt_else(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("class C{static{}}", "class C{}", id="only-member"),
        pytest.param("class C{static{;}}", "class C{}", id="empty-statement-body"),
        pytest.param("class C{static{}x=1;static{}static y}", "class C{x=1;static y}", id="between-fields"),
        pytest.param("class C{static{f()}static{}}", "class C{static{f()}}", id="last-member"),
        pytest.param("x=class{static{if(0)f()}}", "x=class{}", id="body-folds-away"),
    ],
)
def test_fold_drops_empty_static_block(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("class C{static{}x=1}", "class C{x=1}", id="empty-static-block"),
        pytest.param("x=async ()=>1", "x=async()=>1", id="async-arrow"),
        pytest.param(
            "x=new X();y=new X().z;w=new(new X)(1)", "x=new X,y=new X().z,w=new new X()(1)", id="new-arguments"
        ),
        pytest.param(
            "function f(){if(g())if(x)return;else h()}", "function f(){if(g()){if(x)return;h()}}", id="lone-else-splice"
        ),
        pytest.param(
            "e:for(;;)if(b)break e;else continue e", "a:for(;;){if(b)break a;continue a}", id="lone-else-splice-loop"
        ),
    ],
)
def test_size_transforms_reach_fixpoint(source: str, expected: str) -> None:
    once: Final[str] = minify_js(source)
    assert (once, minify_js(once)) == (expected, expected)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("delete undefined", "delete undefined", id="delete"),
        pytest.param("delete undefined.x", "delete(void 0).x", id="delete-member"),
        pytest.param("typeof undefined", "typeof void 0", id="typeof"),
        pytest.param("undefined=undefined", "undefined=void 0", id="assign"),
        pytest.param("undefined++", "undefined++", id="update"),
        pytest.param("undefined.x=undefined", "(void 0).x=void 0", id="member-target"),
        pytest.param("for(undefined in undefined);", "for(undefined in void 0);", id="for-in"),
        pytest.param("for(undefined of undefined);", "for(undefined of void 0);", id="for-of"),
        pytest.param("for(;undefined;);", "for(;void 0;);", id="for-test"),
        pytest.param("[undefined=undefined,...undefined]=a", "[undefined=void 0,...undefined]=a", id="array-pattern"),
        pytest.param(
            "({[undefined]:undefined,undefined,u:undefined=undefined}=a)",
            "({[void 0]:undefined,undefined,u:undefined=void 0}=a)",
            id="object-pattern",
        ),
        pytest.param("({undefined=undefined}=a)", "({undefined=void 0}=a)", id="shorthand-default"),
        pytest.param("with(o)undefined;x=undefined", "with(o)undefined;x=void 0", id="with-body"),
        pytest.param(
            "with(undefined)(function(){return undefined})",
            "with(void 0)(function(){return undefined})",
            id="with-nested-function",
        ),
    ],
)
def test_fold_keeps_undefined_reference(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("function f(){return a;class a{}}", "function f(){return a;class a{}}", id="class"),
        pytest.param("function f(){return a;let a}", "function f(){return a;let a}", id="let"),
        pytest.param("function f(){return a;const a=1}", "function f(){return a;const a=1}", id="const"),
        pytest.param("switch(x){case 0:break;let a}", "switch(x){case 0:break;let a}", id="switch-clause"),
        pytest.param("function f(){return a;if(b){let a}}", "function f(){return a}", id="nested-block-dropped"),
        pytest.param("function f(){return a;g()}", "function f(){return a}", id="statement-dropped"),
    ],
)
def test_fold_keeps_unreachable_lexical_declaration(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == expected


@pytest.mark.parametrize("count", [pytest.param(0, id="same-arena"), pytest.param(90, id="grown-arena")])
def test_fold_keeps_dead_branch_whose_hoisting_if_the_pass_appended(count: int) -> None:
    # the first dead branch leaves a remembered answer behind, the booleans each fold into a new node (enough of them
    # outgrow the node arena), and the `if` split from its else is a node appended after both
    source: Final[str] = f"if(0){{var z}}x=[{','.join(['true'] * count)}];L:if(0)if(a){{var v;break L}}else b()"
    expected: Final[str] = f"if(0)var z;x=[{','.join(['!0'] * count)}];a:if(0){{if(a){{var v;break a}}b()}}"
    assert minify_js(source) == expected


@pytest.mark.parametrize(
    "nest",
    [
        pytest.param(lambda depth: f"{'if(0)' * depth}{{{'b:;' * (2 * depth)}var y}}", id="dead-branches"),
        pytest.param(lambda depth: f"{'throw 1;if(r){' * depth}{'b:;' * (2 * depth)}var y{'}' * depth}", id="tails"),
    ],
)
def test_fold_scans_a_kept_nest_once_per_pass(nest: Callable[[int], str], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(minify_js, nest(depth)), (100, 400))


def test_fold_forgets_a_hoist_that_left_with_a_spliced_else() -> None:
    # the dead branch is scanned first and remembers that the inner `if` holds a hoisted function; the else that held
    # it is then spliced out behind the `if`, which must not keep counting as the declaration the cut stops at
    source: Final[str] = "function f(){if(0){return 2;if(q)throw 3;else{a();while(b){function g(){}}}}}"
    assert minify_js(source, JSMinify(mangle=False)) == "function f(){if(0){return 2;for(;b;){function g(){}}}}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "console.log(typeof function c(){c=7;return c}())",
            "console.log(typeof function a(){return a=7,a}())",
            id="function-name-read",
        ),
        pytest.param("(function c(){c=7})()", "(function a(){a=7}())", id="function-name-unread"),
        pytest.param("(class C{m(){C=1}})", "(class a{m(){a=1}})", id="class-name-unread"),
        pytest.param("(function(){const k=1;k=2})()", "(function(){const a=1;a=2}())", id="const-unread"),
        pytest.param(
            "(function(){const k=1;return k=2,k})()", "(function(){const a=1;return a=2,a}())", id="const-read"
        ),
        pytest.param("(function(){let k=1;return k=2,k})()", "(function(){return 2}())", id="let-collapses"),
        pytest.param("(function(){var k;k=g()})()", "(function(){g()}())", id="var-dead-store"),
    ],
)
def test_compress_keeps_immutable_binding_writes(source: str, expected: str) -> None:
    assert minify_js(source) == expected


@pytest.mark.skipif(_NODE is None, reason="node not available")
@pytest.mark.parametrize(
    "options", [pytest.param(JSMinify(mangle=False), id="fold"), pytest.param(JSMinify(), id="full")]
)
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "var x=0;function g(){return 1}function f(){if(g())if(x)return;else console.log('h')}f()",
            "h\n",
            id="dangling-else",
        ),
        pytest.param("console.log(delete undefined)", "false\n", id="delete-undefined"),
        pytest.param("var o={undefined:3};with(o)console.log(undefined)", "3\n", id="with-undefined"),
        pytest.param(
            "var a='FAIL';try{console.log(function(){return a;class a{}}())}catch(e){console.log('PASS')}",
            "PASS\n",
            id="class-tdz",
        ),
        pytest.param("console.log(typeof function c(){c=7;return c}())", "function\n", id="function-name-sloppy"),
        pytest.param(
            "try{(function c(){'use strict';c=7})()}catch(e){console.log(e.name)}",
            "TypeError\n",
            id="function-name-strict",
        ),
        pytest.param(
            "try{(function(){const k=1;k=2})()}catch(e){console.log(e.name)}", "TypeError\n", id="const-write"
        ),
        pytest.param(
            "function f(){return typeof v;{throw 0}g();var v=1}console.log(f())", "undefined\n", id="unreachable-var"
        ),
        pytest.param(
            "function f(){{return typeof g;g=0}function g(){}}console.log(f())", "function\n", id="unreachable-function"
        ),
        pytest.param(
            "try{(function(){{return k;g()}let k})()}catch(e){console.log(e.name)}", "ReferenceError\n", id="let-tdz"
        ),
    ],
)
def test_fold_and_compress_preserve_behavior(source: str, expected: str, options: JSMinify) -> None:
    assert (_run(source), _run(minify_js(source, options))) == (expected, expected)


@pytest.mark.parametrize(
    "link",
    [
        pytest.param("a&&", id="and"),
        pytest.param("a||", id="or"),
        pytest.param("a??", id="nullish"),
        pytest.param("(a&&a&&a)&&", id="grouped-and"),
        pytest.param("(a||a||a)||", id="grouped-or"),
        pytest.param("(a??a??a)??", id="grouped-nullish"),
    ],
)
def test_long_logical_chain_folds_in_linear_steps(link: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(minify_js, f"x={link * n}a"), (200, 800))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # a statement list takes in a redundant block, a constant if's taken branch and the else of an if that
        # always jumps; each is handed over before it is folded by itself, and the list folds them once
        pytest.param("{{a();{b()}}c()}", "a(),b(),c()", id="nested-blocks"),
        pytest.param("switch(x){case 1:{{a()}b()}}", "switch(x){case 1:a(),b()}", id="switch-clause-blocks"),
        pytest.param("{{let a=1;b(a)}c()}", "{let a=1;b(a)}c()", id="scoped-block-kept"),
        pytest.param("if(1){{a()}}b()", "a(),b()", id="constant-if-block"),
        pytest.param("if(true)if(1){a()}b()", "a(),b()", id="constant-if-chain"),
        pytest.param("if(0)a();else{{b()}}c()", "b(),c()", id="constant-if-else-block"),
        pytest.param("for(;x;)if(1){a();b()}", "for(;x;)a(),b()", id="constant-if-loop-body"),
        pytest.param("for(;x;)if(1)if(q)return;else a()", "for(;x;){if(q)return;a()}", id="constant-if-abrupt-body"),
        pytest.param("if(1){throw 0}else{var v;a()}b()", "throw 0;var v;a(),b()", id="constant-if-hoisting-else"),
        pytest.param("if(q){throw 0}else{a()}b()", "if(q)throw 0;a(),b()", id="abrupt-else-block"),
        pytest.param("if(q){throw 0}else{}b()", "if(q)throw 0;b()", id="abrupt-else-empty"),
        pytest.param("if(q){throw 0}else;b()", "if(q)throw 0;b()", id="abrupt-else-empty-statement"),
        pytest.param("if(q){throw 0}else{let a=1;b(a)}c()", "if(q)throw 0;{let a=1;b(a)}c()", id="abrupt-else-scoped"),
        pytest.param("if(q){throw 0}else{;{}}b()", "if(q)throw 0;b()", id="abrupt-else-empties"),
        pytest.param(
            "if(q){throw 0}else{{a()}if(r){throw 1}else{b()}}c()",
            "if(q)throw 0;a();if(r)throw 1;b(),c()",
            id="abrupt-else-nested",
        ),
        pytest.param("if(q){if(1)throw 0}else{a()}b()", "if(q)throw 0;a(),b()", id="abrupt-after-fold"),
        pytest.param("if(q)if(r)throw 0;else throw 1;else{a()}b()", "if(q){if(r)throw 0;throw 1}a(),b()", id="abrupt"),
        pytest.param("if(q)throw 0;else if(r)throw 1;else a();b()", "if(q)throw 0;if(r)throw 1;a(),b()", id="else-if"),
        pytest.param("if(q){throw 0}else function f(){}", "if(q)throw 0;else function f(){}", id="else-function-kept"),
        pytest.param("for(;;)if(q){break}else{a()}", "for(;!q;)a()", id="abrupt-else-loop-body"),
    ],
)
def test_fold_hands_statements_to_enclosing_list(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # a jump cuts what follows it up to the first declaration, so the unreachable tail of a redundant block
        # goes the same whether the block stands alone or its statements join a list that declares a name later
        pytest.param("throw q;a();b()", "throw q", id="no-declaration"),
        pytest.param("throw q;a();var v;b()", "throw q;var v;b()", id="up-to-var"),
        pytest.param("function f(){return a;g();let a}", "function f(){return a;let a}", id="up-to-let"),
        pytest.param("function f(){return;a();if(b)var x}", "function f(){return;if(b)var x}", id="up-to-nested-var"),
        pytest.param("{throw q;a()}var v", "throw q;var v", id="block-before-var"),
        pytest.param("{{throw q}a()}let v", "throw q;let v", id="nested-block-before-let"),
        pytest.param("switch(x){case 1:{return;a()}let k}", "switch(x){case 1:return;let k}", id="clause-block"),
        pytest.param("if(1){throw q;a()}function f(){}", "throw q;function f(){}", id="constant-if-before-function"),
        pytest.param(
            "function f(){if(q){throw 0}else{return;a()}var v}",
            "function f(){if(q)throw 0;return;var v}",
            id="abrupt-else-before-var",
        ),
        pytest.param("for(;;){continue;a();var v;b();break;c()}", "for(;;){continue;var v;b();break}", id="later-jump"),
        pytest.param(
            "for(;;){break;var v;{continue;a()}class C{}}", "for(;;){break;var v;continue;class C{}}", id="later-block"
        ),
        # the cut comes before the merges: an unreachable expression must not ride into a kept for's init
        pytest.param("{throw q;a()}for(;;){var v}", "throw q;for(;;)var v", id="before-for-init-merge"),
    ],
)
def test_fold_cuts_unreachable_up_to_declaration(source: str, expected: str) -> None:
    assert minify_js(source, JSMinify(mangle=False)) == expected


@pytest.mark.parametrize(
    ("opening", "sizes"),
    [
        pytest.param("{", (100, 800), id="block"),
        pytest.param("if(1){", (50, 400), id="constant-if"),
        pytest.param("if(q){b();throw 0}else{", (50, 400), id="abrupt-else"),
    ],
)
def test_fold_folds_absorbed_statements_once(opening: str, sizes: tuple[int, int], work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(minify_js, opening * n + "c:;" * n + "}" * n), sizes)


@pytest.mark.parametrize(
    "chain",
    [
        pytest.param("!", id="not"),
        pytest.param("!void ", id="not-void"),
    ],
)
def test_fold_asks_a_not_chain_for_its_truthiness_once_per_level(chain: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(minify_js, f"x={chain * n}a"), (100, 400))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("x=!!!!!!!''", "x=!0", id="not-odd"),
        pytest.param("x=!!!!!!''", "x=!1", id="not-even"),
        pytest.param("x=!void !void !void 1", "x=!0", id="not-void"),
        pytest.param("x=void !void !void 0?1:2", "x=2", id="void-not"),
        pytest.param("if(!!!!0)a();else b()", "b()", id="test"),
        pytest.param("x=!!!!!b", "x=!!!!!b", id="not-constant"),
    ],
)
def test_fold_still_folds_a_constant_under_a_not_chain(source: str, expected: str) -> None:
    assert minify_js(source) == expected
