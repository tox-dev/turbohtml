from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("p { color: red } ] { color: red }", "p{color:red}]{color:red}", id="unmatched-close-bracket"),
        pytest.param("] { color: red } p { color: red }", "]{color:red}p{color:red}", id="invalid-before-valid"),
        pytest.param("p { color: red } ,q { color: red }", "p{color:red},q{color:red}", id="leading-comma"),
        pytest.param("p { color: red } q, { color: red }", "p{color:red}q,{color:red}", id="trailing-comma"),
        pytest.param("p { color: red } q,,r { color: red }", "p{color:red}q,,r{color:red}", id="empty-arm"),
        pytest.param("p { color: red } q[] { color: red }", "p{color:red}q[]{color:red}", id="empty-attribute"),
        pytest.param("p { color: red } q[a]] { color: red }", "p{color:red}q[a]]{color:red}", id="extra-bracket"),
        pytest.param("p { color: red } q..r { color: red }", "p{color:red}q..r{color:red}", id="empty-class"),
        pytest.param("p { color: red } q > { color: red }", "p{color:red}q>{color:red}", id="trailing-combinator"),
        pytest.param("p { color: red } # { color: red }", "p{color:red}#{color:red}", id="bare-id"),
        pytest.param("p { color: red } : { color: red }", "p{color:red}:{color:red}", id="bare-pseudo"),
        pytest.param("p { color: red } :not() { color: red }", "p{color:red}:not(){color:red}", id="empty-function"),
        pytest.param("p { color: red } [=x] { color: red }", "p{color:red}[=x]{color:red}", id="missing-attr-name"),
        pytest.param('p { color: red } ["x"] { color: red }', 'p{color:red}["x"]{color:red}', id="quoted-attr-name"),
        pytest.param("p { color: red } [a=] { color: red }", "p{color:red}[a=]{color:red}", id="missing-attr-value"),
        pytest.param("p { color: red } [a~] { color: red }", "p{color:red}[a~]{color:red}", id="missing-attr-operator"),
        pytest.param(
            "p { color: red } [a*x] { color: red }", "p{color:red}[a*x]{color:red}", id="invalid-attr-operator"
        ),
        pytest.param(
            "p { color: red } [a!x] { color: red }", "p{color:red}[a!x]{color:red}", id="unknown-attr-operator"
        ),
        pytest.param("p { color: red } [a { color: red }", "p{color:red}[a { color: red }", id="unclosed-attribute"),
        pytest.param(
            "p { color: red } [a=foo { color: red }", "p{color:red}[a=foo { color: red }", id="unclosed-attr-value"
        ),
        pytest.param(
            "p { color: red } [a=foo x] { color: red }", "p{color:red}[a=foo x]{color:red}", id="invalid-attr-flag"
        ),
        pytest.param("p { color: red } [a=foo I] { color: red }", "p,[a=foo I]{color:red}", id="uppercase-attr-flag"),
        pytest.param(
            "p { color: red } [a=foo ii] { color: red }", "p{color:red}[a=foo ii]{color:red}", id="repeated-attr-flag"
        ),
        pytest.param(
            "p { color: red } [a=foo ix] { color: red }",
            "p{color:red}[a=foo ix]{color:red}",
            id="invalid-attr-flag-suffix",
        ),
        pytest.param(
            "p { color: red } [a=foo i x] { color: red }",
            "p{color:red}[a=foo i x]{color:red}",
            id="attr-flag-extra-token",
        ),
        pytest.param("p { color: red } [a|=x] { color: red }", "p,[a|=x]{color:red}", id="attr-prefix-operator"),
        pytest.param("p { color: red } [a~=x] { color: red }", "p,[a~=x]{color:red}", id="attr-word-operator"),
        pytest.param("p { color: red } [a^=x] { color: red }", "p,[a^=x]{color:red}", id="attr-starts-operator"),
        pytest.param("p { color: red } [a$=x] { color: red }", "p,[a$=x]{color:red}", id="attr-ends-operator"),
        pytest.param("p { color: red } [a*=x] { color: red }", "p,[a*=x]{color:red}", id="attr-contains-operator"),
        pytest.param(
            "p { color: red } [a=foo s] { color: red }", "p{color:red}[a=foo s]{color:red}", id="unsupported-attr-flag"
        ),
        pytest.param("p { color: red } [a=foo i] { color: red }", "p,[a=foo i]{color:red}", id="supported-attr-flag"),
        pytest.param("p { color: red } [a=foo i ] { color: red }", "p,[a=foo i ]{color:red}", id="attr-flag-trivia"),
        pytest.param("p { color: red } [a ] { color: red }", "p,[a ]{color:red}", id="attr-presence-trivia"),
        pytest.param('p { color: red } [a="x\\q"] { color: red }', 'p,[a="x\\q"]{color:red}', id="quoted-attr-escape"),
        pytest.param("p { color: red } :active { color: red }", "p,:active{color:red}", id="valid-active"),
        pytest.param("p { color: red } :focus { color: red }", "p,:focus{color:red}", id="valid-focus"),
        pytest.param("p { color: red } :checked { color: red }", "p,:checked{color:red}", id="valid-checked"),
        pytest.param("p { color: red } ::before { color: red }", "p,:before{color:red}", id="valid-pseudo-element"),
        pytest.param("p { color: red } :before { color: red }", "p,:before{color:red}", id="legacy-pseudo-element"),
        pytest.param("p { color: red } ::marker { color: red }", "p,::marker{color:red}", id="modern-pseudo-element"),
        pytest.param("p { color: red } section { color: red }", "p,section{color:red}", id="valid-long-type"),
        pytest.param(
            "p { color: red } section.longclassname:hover { color: red }",
            "p,section.longclassname:hover{color:red}",
            id="long-complex-selector",
        ),
        pytest.param("p { color: red } .1 { color: red }", "p{color:red}.1{color:red}", id="invalid-class-start"),
        pytest.param("p { color: red } q > r { color: red }", "p,q>r{color:red}", id="valid-child-combinator"),
        pytest.param("p { color: red } q[a]r { color: red }", "p{color:red}q[a]r{color:red}", id="adjacent-type"),
        pytest.param("p { color: red } q\\:r { color: red }", "p{color:red}q\\:r{color:red}", id="escaped-type"),
        pytest.param("p { color: red } q r { color: red }", "p,q r{color:red}", id="descendant-combinator"),
        pytest.param("p { color: red } q+u { color: red }", "p,q+u{color:red}", id="next-sibling-combinator"),
        pytest.param("p { color: red } q~u { color: red }", "p,q~u{color:red}", id="later-sibling-combinator"),
        pytest.param("p { color: red } >q { color: red }", "p{color:red}>q{color:red}", id="leading-combinator"),
        pytest.param("p { color: red } q>>r { color: red }", "p{color:red}q>>r{color:red}", id="double-combinator"),
        pytest.param("p { color: red } -- >a { color: red }", "p{color:red}-->a{color:red}", id="cdc-arm"),
        pytest.param("-- >a { color: red } p { color: red }", "-->a{color:red}p{color:red}", id="cdc-first"),
        pytest.param("p { color: red } x -- >a { color: red }", "p{color:red}x -->a{color:red}", id="cdc-after-space"),
        pytest.param("p { color: red } q,-- >a { color: red }", "p{color:red}q,-->a{color:red}", id="cdc-after-comma"),
        pytest.param("p { color: red } a-->b { color: red }", "p,a-->b{color:red}", id="ident-ending-hyphens"),
        pytest.param("p { color: red } ab>a { color: red }", "p,ab>a{color:red}", id="two-letter-type-combinator"),
        pytest.param("p { color: red } -a>a { color: red }", "p,-a>a{color:red}", id="hyphenated-type-combinator"),
        pytest.param(
            "p { color: red } q>,r { color: red }", "p{color:red}q>,r{color:red}", id="combinator-before-comma"
        ),
        pytest.param(
            "p { color: red } q,>r { color: red }", "p{color:red}q,>r{color:red}", id="combinator-after-comma"
        ),
        pytest.param("p { color: red } q,[a] { color: red }", "p,q,[a]{color:red}", id="valid-selector-list"),
        pytest.param("p { color: red } * { color: red }", "p,*{color:red}", id="valid-universal"),
        pytest.param("p { color: red } q* { color: red }", "p{color:red}q*{color:red}", id="misplaced-universal"),
        pytest.param("p { color: red } .foo { color: red }", "p,.foo{color:red}", id="valid-class"),
        pytest.param("p { color: red } q.\\:foo { color: red }", "p{color:red}q.\\:foo{color:red}", id="escaped-class"),
        pytest.param(
            "p { color: red } ::before p { color: red }",
            "p{color:red}:before p{color:red}",
            id="pseudo-element-not-last",
        ),
        pytest.param(
            "p { color: red } q::before,r { color: red }", "p,q:before,r{color:red}", id="pseudo-element-list"
        ),
        pytest.param(
            "p { color: red } :not(q) { color: red }", "p{color:red}:not(q){color:red}", id="functional-pseudo"
        ),
        pytest.param(
            "p { color: red } section:is(.a,.b,.c,.d) { color: red }",
            "p{color:red}section:is(.a,.b,.c,.d){color:red}",
            id="long-functional-pseudo",
        ),
        pytest.param(
            "p { color: red } q { color: red } ] { color: red }", "p,q{color:red}]{color:red}", id="lookahead"
        ),
        pytest.param(
            "p { color: red } q { color: red } -- >a { color: red }",
            "p,q{color:red}-->a{color:red}",
            id="cdc-lookahead",
        ),
        pytest.param(
            "p { color: red } q { margin: 0 } ] { color: red }", "p{color:red}q{margin:0}]{color:red}", id="nonadjacent"
        ),
        pytest.param('p { color: red } [data=","] { color: red }', 'p,[data=","]{color:red}', id="quoted-comma"),
        pytest.param("p { color: red } #q { color: red }", "p,#q{color:red}", id="valid-id"),
        pytest.param("p { color: red } q:hover { color: red }", "p,q:hover{color:red}", id="valid-pseudo"),
        pytest.param(
            "p { color: red } :is(q,r) { color: red }", "p{color:red}:is(q,r){color:red}", id="functional-list"
        ),
        pytest.param("p { color: red } :bogus { color: red }", "p{color:red}:bogus{color:red}", id="unknown-pseudo"),
        pytest.param("p { color: red } ::bogus { color: red }", "p{color:red}::bogus{color:red}", id="unknown-element"),
        pytest.param(
            "p { color: red } :nth-child(foo) { color: red }",
            "p{color:red}:nth-child(foo){color:red}",
            id="invalid-nth",
        ),
    ],
)
def test_identical_body_merge_respects_selector_validity(source: str, expected: str) -> None:
    assert minify_css(source) == expected


def test_identical_body_merge_with_malformed_selector_on_hashed_path() -> None:
    filler: Final = "".join(f".x{index}{{--v{index}:{index}}}" for index in range(40))
    assert minify_css(f"{filler}p {{ color: red }} ] {{ color: red }}") == f"{filler}p{{color:red}}]{{color:red}}"


def test_identical_body_merge_with_malformed_selector_is_fixed_point() -> None:
    first: Final = minify_css("p { color: red } q { color: red } ] { color: red }")
    assert minify_css(first) == first
