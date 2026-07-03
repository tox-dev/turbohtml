"""XPath front-end (lexer + parser) conformance via the ``_xpath_parse`` hook.

The hook compiles an expression and returns a canonical S-expression of the AST, so
these assert the parse shape without an evaluator. Evaluation lands in a later phase.
"""

from __future__ import annotations

import re

import pytest

from turbohtml import _html

parse = _html._xpath_parse


@pytest.mark.parametrize(
    ("expr", "ast"),
    [
        # location paths and abbreviations
        pytest.param("div", "(path rel (step child name 'div'))", id="bare-name"),
        pytest.param("*", "(path rel (step child *))", id="wildcard"),
        pytest.param("/html/body", "(path abs (step child name 'html') (step child name 'body'))", id="absolute"),
        pytest.param(
            "//a",
            "(path abs (step descendant name 'a'))",
            id="double-slash",
        ),
        pytest.param(
            "a/b//c",
            "(path rel (step child name 'a') (step child name 'b') (step descendant name 'c'))",
            id="mixed-slashes",
        ),
        # the // collapse leaves descendant-or-self in place when the abbreviation
        # does not apply: a predicate on it, no following step, or a non-child step
        pytest.param(
            "//@x",
            "(path abs (step descendant-or-self node()) (step attribute name 'x'))",
            id="double-slash-attribute",
        ),
        pytest.param(
            "a/descendant-or-self::node()",
            "(path rel (step child name 'a') (step descendant-or-self node()))",
            id="trailing-descendant-or-self",
        ),
        pytest.param(
            "descendant-or-self::node()[1]/a",
            "(path rel (step descendant-or-self node() (pred (num 1))) (step child name 'a'))",
            id="descendant-or-self-predicate",
        ),
        # a position-independent predicate still collapses; a positional one does not
        pytest.param(
            "//a[@href]",
            "(path abs (step descendant name 'a' (pred (path rel (step attribute name 'href')))))",
            id="double-slash-nonpositional-predicate",
        ),
        pytest.param(
            "//a[last()]",
            "(path abs (step descendant-or-self node()) (step child name 'a' (pred (call 'last'))))",
            id="double-slash-positional-function",
        ),
        pytest.param(
            "//a[2 + 1]",
            "(path abs (step descendant-or-self node()) (step child name 'a' (pred (+ (num 2) (num 1)))))",
            id="double-slash-arithmetic-predicate",
        ),
        pytest.param(
            "//a[round(1.5)]",
            "(path abs (step descendant-or-self node()) (step child name 'a' (pred (call 'round' (num 1.5)))))",
            id="double-slash-numeric-function",
        ),
        pytest.param(
            "//a[1 = position()]",
            "(path abs (step descendant-or-self node()) (step child name 'a' (pred (= (num 1) (call 'position')))))",
            id="double-slash-position-on-right",
        ),
        pytest.param(
            "//a[contains(b, position())]",
            "(path abs (step descendant-or-self node()) (step child name 'a' "
            "(pred (call 'contains' (path rel (step child name 'b')) (call 'position')))))",
            id="double-slash-position-in-argument",
        ),
        pytest.param("/", "(path abs)", id="root-only"),
        pytest.param(".", "(path rel (step self node()))", id="dot"),
        pytest.param("..", "(path rel (step parent node()))", id="dotdot"),
        pytest.param(
            ".//p",
            "(path rel (step self node()) (step descendant name 'p'))",
            id="context-descendants",
        ),
        # axes spelled out
        pytest.param("child::div", "(path rel (step child name 'div'))", id="axis-child"),
        pytest.param("descendant::a", "(path rel (step descendant name 'a'))", id="axis-descendant"),
        pytest.param(
            "descendant-or-self::a",
            "(path rel (step descendant-or-self name 'a'))",
            id="axis-descendant-or-self",
        ),
        pytest.param("attribute::href", "(path rel (step attribute name 'href'))", id="axis-attribute"),
        pytest.param("self::node()", "(path rel (step self node()))", id="axis-self"),
        pytest.param("parent::*", "(path rel (step parent *))", id="axis-parent"),
        pytest.param("ancestor::div", "(path rel (step ancestor name 'div'))", id="axis-ancestor"),
        pytest.param(
            "ancestor-or-self::div",
            "(path rel (step ancestor-or-self name 'div'))",
            id="axis-ancestor-or-self",
        ),
        pytest.param(
            "following-sibling::li",
            "(path rel (step following-sibling name 'li'))",
            id="axis-following-sibling",
        ),
        pytest.param(
            "preceding-sibling::li",
            "(path rel (step preceding-sibling name 'li'))",
            id="axis-preceding-sibling",
        ),
        pytest.param("following::p", "(path rel (step following name 'p'))", id="axis-following"),
        pytest.param("preceding::p", "(path rel (step preceding name 'p'))", id="axis-preceding"),
        pytest.param("namespace::x", "(path rel (step namespace name 'x'))", id="axis-namespace"),
        # a namespace-prefixed name test keeps the whole QName; the prefix binds at eval time
        pytest.param("a:b", "(path rel (step child name 'a:b'))", id="prefixed-name"),
        pytest.param("//svg:circle", "(path abs (step descendant name 'svg:circle'))", id="prefixed-descendant"),
        # node tests
        pytest.param("node()", "(path rel (step child node()))", id="test-node"),
        pytest.param("text()", "(path rel (step child text()))", id="test-text"),
        pytest.param("comment()", "(path rel (step child comment()))", id="test-comment"),
        pytest.param(
            "processing-instruction()",
            "(path rel (step child pi()))",
            id="test-pi-bare",
        ),
        pytest.param(
            "processing-instruction('php')",
            "(path rel (step child pi('php')))",
            id="test-pi-target",
        ),
        pytest.param("@href", "(path rel (step attribute name 'href'))", id="attr-abbrev"),
        # predicates
        pytest.param("a[1]", "(path rel (step child name 'a' (pred (num 1))))", id="pred-position"),
        pytest.param(
            "a[@id='x']",
            "(path rel (step child name 'a' (pred (= (path rel (step attribute name 'id')) (lit 'x')))))",
            id="pred-attr-eq",
        ),
        pytest.param(
            "a[@href][2]",
            "(path rel (step child name 'a' (pred (path rel (step attribute name 'href'))) (pred (num 2))))",
            id="pred-chain",
        ),
        # predicate binds to a step vs the whole set
        pytest.param(
            "//a[1]",
            "(path abs (step descendant-or-self node()) (step child name 'a' (pred (num 1))))",
            id="step-predicate",
        ),
        pytest.param(
            "(//a)[1]",
            "(filter (path abs (step descendant name 'a')) (pred (num 1)))",
            id="filter-predicate",
        ),
        pytest.param(
            "(//a)[1]/b",
            "(path rel (from (filter (path abs (step descendant name 'a')) (pred (num 1)))) (step child name 'b'))",
            id="filter-then-path",
        ),
        # primaries
        pytest.param("'hi'", "(lit 'hi')", id="literal"),
        pytest.param("42", "(num 42)", id="integer"),
        pytest.param("1.5", "(num 1.5)", id="decimal"),
        pytest.param(".5", "(num 0.5)", id="leading-dot-number"),
        pytest.param("(1)", "(num 1)", id="parens"),
        pytest.param("$foo", "(var 'foo')", id="variable"),
        pytest.param("$x[1]", "(filter (var 'x') (pred (num 1)))", id="variable-filter"),
        pytest.param(
            "//a[@id=$wanted]",
            "(path abs (step descendant name 'a' (pred (= (path rel (step attribute name 'id')) (var 'wanted')))))",
            id="variable-in-predicate",
        ),
        pytest.param(
            "count(//a)",
            "(call 'count' (path abs (step descendant name 'a')))",
            id="call-one-arg",
        ),
        pytest.param("true()", "(call 'true')", id="call-no-args"),
        pytest.param(
            "concat('a','b','c')",
            "(call 'concat' (lit 'a') (lit 'b') (lit 'c'))",
            id="call-multi-arg",
        ),
        pytest.param(
            "contains(@class,'y')",
            "(call 'contains' (path rel (step attribute name 'class')) (lit 'y'))",
            id="call-contains",
        ),
        # operators and precedence
        pytest.param("1 + 2 * 3", "(+ (num 1) (* (num 2) (num 3)))", id="prec-add-mul"),
        pytest.param("-1 + 2", "(+ (neg (num 1)) (num 2))", id="unary-minus"),
        pytest.param("1 - 2 - 3", "(- (- (num 1) (num 2)) (num 3))", id="left-assoc-sub"),
        pytest.param("6 div 2 mod 2", "(mod (div (num 6) (num 2)) (num 2))", id="div-mod"),
        pytest.param("1 = 2 != 3", "(!= (= (num 1) (num 2)) (num 3))", id="equality"),
        pytest.param("1 < 2 >= 3", "(>= (< (num 1) (num 2)) (num 3))", id="relational"),
        pytest.param("1 <= 2 > 3", "(> (<= (num 1) (num 2)) (num 3))", id="relational-le-gt"),
        pytest.param(
            "a and b or c",
            "(or (and (path rel (step child name 'a')) (path rel (step child name 'b'))) "
            "(path rel (step child name 'c')))",
            id="and-or",
        ),
        pytest.param(
            "//a | //b",
            "(union (path abs (step descendant name 'a')) (path abs (step descendant name 'b')))",
            id="union",
        ),
        # the operator-name disambiguation: a leading 'and' is a name test, not an operator
        pytest.param("//and", "(path abs (step descendant name 'and'))", id="and-as-name"),
        pytest.param("div/mod", "(path rel (step child name 'div') (step child name 'mod'))", id="opnames-as-names"),
        # name character classes
        pytest.param("DIV", "(path rel (step child name 'DIV'))", id="uppercase-name"),
        pytest.param("_x", "(path rel (step child name '_x'))", id="underscore-name"),
        pytest.param("café", "(path rel (step child name 'café'))", id="unicode-name"),
        pytest.param("a-b", "(path rel (step child name 'a-b'))", id="hyphen-name"),
        pytest.param("a1", "(path rel (step child name 'a1'))", id="digit-in-name"),
        pytest.param("a.b", "(path rel (step child name 'a.b'))", id="dot-in-name"),
        pytest.param("no", "(path rel (step child name 'no'))", id="keyword-prefix-name"),
        pytest.param("text", "(path rel (step child name 'text'))", id="kind-test-name-as-name"),
        pytest.param(
            "text/a",
            "(path rel (step child name 'text') (step child name 'a'))",
            id="kind-test-name-then-step",
        ),
        pytest.param("''", "(lit '')", id="empty-literal"),
        pytest.param("1000000000000000", "(num 1e+15)", id="huge-number"),
        # abbreviations right after a leading slash
        pytest.param("/*", "(path abs (step child *))", id="abs-wildcard"),
        pytest.param("/@id", "(path abs (step attribute name 'id'))", id="abs-attribute"),
        pytest.param("/.", "(path abs (step self node()))", id="abs-dot"),
        pytest.param("/..", "(path abs (step parent node()))", id="abs-dotdot"),
        pytest.param(
            "1.5/a",
            "(path rel (from (num 1.5)) (step child name 'a'))",
            id="number-filter-then-step",
        ),
        pytest.param(
            "(//a)//b",
            "(path rel (from (path abs (step descendant name 'a'))) (step descendant name 'b'))",
            id="grouped-then-descendants",
        ),
    ],
)
def test_parses(expr: str, ast: str) -> None:
    assert parse(expr) == ast


def test_whitespace_between_tokens_is_ignored() -> None:
    assert parse("1\t+\r\n2") == parse("1 + 2")


def test_large_expression_grows_the_arena() -> None:
    expr = "/".join(f"e{i}" for i in range(40))
    assert parse(expr).count("step child name") == 40


@pytest.mark.parametrize(
    ("expr", "message"),
    [
        pytest.param("", "node test", id="empty"),
        pytest.param("//", "node test", id="trailing-slashes"),
        pytest.param("a[", "node test", id="open-predicate"),
        pytest.param("a[1", "expected ']'", id="unclosed-predicate"),
        pytest.param("@", "node test", id="lone-at"),
        pytest.param("(", "node test", id="open-paren"),
        pytest.param("(1", r"expected '\)'", id="unclosed-paren"),
        pytest.param("'unterminated", "invalid character", id="unterminated-string"),
        pytest.param("a!b", "invalid character", id="stray-bang"),
        pytest.param("a:", "invalid character", id="stray-colon"),
        pytest.param("a[1];", "invalid character", id="stray-after-token"),
        pytest.param("a~b", "invalid character", id="tilde"),
        pytest.param("1 foo", "trailing tokens", id="name-in-operator-position"),
        pytest.param("a&b", "invalid character", id="ampersand"),
        pytest.param(".a", "trailing tokens", id="dot-then-name"),
        pytest.param("4a", "trailing tokens", id="digit-then-name"),
        pytest.param("1.5a", "trailing tokens", id="fraction-then-name"),
        pytest.param("a:5", "invalid character", id="colon-then-non-name"),
        pytest.param("div::", "trailing tokens", id="bad-axis"),
        pytest.param("count(", "node test", id="unclosed-call"),
        pytest.param("1 2", "trailing tokens", id="two-numbers"),
        pytest.param("$5", "expected a name", id="dollar-then-number"),
        pytest.param("$", "expected a name", id="dollar-at-end"),
    ],
)
def test_rejects(expr: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse(expr)


def test_non_string_argument() -> None:
    with pytest.raises(TypeError, match="must be a str"):
        parse(123)  # ty: ignore[invalid-argument-type]  # non-str exercises the TypeError path


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        pytest.param("a~b", "invalid character in expression at offset 1 near '~'", id="stray-char"),
        pytest.param("1 2", "trailing tokens at offset 2 near '2'", id="trailing-token"),
        pytest.param("(1", "expected ')' at the end of the expression", id="early-end"),
        pytest.param("1 δ", "trailing tokens at offset 2 near '?'", id="non-ascii-token"),
    ],
)
def test_parse_error_names_position_and_token(expr: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=re.escape(fragment)):
        parse(expr)


def test_parse_error_clamps_a_long_offending_token() -> None:
    with pytest.raises(ValueError, match="offset") as excinfo:
        parse("1 " + "z" * 60)
    assert "z" * 31 in str(excinfo.value)
    assert "z" * 32 not in str(excinfo.value)


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("(" * 5000 + "1" + ")" * 5000, id="nested-groups"),
        pytest.param("-" * 5000 + "1", id="unary-minus-chain"),
    ],
)
def test_deeply_nested_expression_is_rejected(expr: str) -> None:
    # nested groups and a chain of unary minus both recurse in the parser; the depth cap
    # raises before the C stack overflows (issue #421)
    with pytest.raises(ValueError, match="nested too deeply"):
        parse(expr)
