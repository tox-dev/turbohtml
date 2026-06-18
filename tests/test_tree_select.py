"""CSS selectors: select() / select_one() over the common selector subset."""

from __future__ import annotations

import pytest

from turbohtml import parse

_DOC = (
    '<section id="s" class="box wide">'
    '<h2 class="title">T</h2>'
    '<p class="lead first">one <a href="/x" rel="next">link</a></p>'
    "<p>two</p>"
    '<ul><li class="item">a</li><li class="item sel">b</li></ul>'
    '<my-widget data-k="v" lang="en-US">w</my-widget>'
    "</section>"
)


def _sel(html: str, selector: str) -> list[str]:
    return [element.tag for element in parse(html).select(selector)]


@pytest.mark.parametrize(
    ("selector", "tags"),
    [
        # simple selectors
        pytest.param("li", ["li", "li"], id="type"),
        pytest.param(".item", ["li", "li"], id="class"),
        pytest.param("#s", ["section"], id="id"),
        pytest.param("my-widget", ["my-widget"], id="unknown-tag-by-name"),
        # a type selector is ASCII case-insensitive even for a custom/unknown tag (issue #62)
        pytest.param("MY-WIDGET", ["my-widget"], id="unknown-tag-folds-case"),
        pytest.param("li.sel", ["li"], id="compound-type-class"),
        pytest.param("p.lead.first", ["p"], id="compound-two-classes"),
        # attribute operators
        pytest.param("[rel]", ["a"], id="exists"),
        pytest.param('[href="/x"]', ["a"], id="equals"),
        pytest.param('[class~="wide"]', ["section"], id="includes"),
        pytest.param('[lang|="en"]', ["my-widget"], id="dash"),
        pytest.param('[href^="/"]', ["a"], id="prefix"),
        pytest.param('[href$="x"]', ["a"], id="suffix"),
        pytest.param('[href*="/x"]', ["a"], id="substring"),
        pytest.param('[lang="EN-us" i]', ["my-widget"], id="case-insensitive"),
        pytest.param('[lang="EN-us" s]', [], id="case-sensitive"),
        pytest.param("[data-missing]", [], id="name-absent-in-tree"),
        pytest.param("[rel=nope]", [], id="value-mismatch"),
        pytest.param("[rel='next']", ["a"], id="single-quoted"),
        pytest.param("[CLASS]", ["section", "h2", "p", "li", "li"], id="uppercase-name"),
        # attribute operator near misses
        pytest.param('[href^="z"]', [], id="prefix-miss"),
        pytest.param('[href^="/xyz"]', [], id="prefix-too-long"),
        pytest.param('[href$="z"]', [], id="suffix-miss"),
        pytest.param('[href$="//xx"]', [], id="suffix-too-long"),
        pytest.param('[href*="zz"]', [], id="substring-miss"),
        pytest.param('[lang|="en-US"]', ["my-widget"], id="dash-exact"),
        pytest.param('[lang|="en-U"]', [], id="dash-no-boundary"),
        pytest.param('[lang|="en-US-x"]', [], id="dash-prefix-too-long"),
        pytest.param('[lang|="xx"]', [], id="dash-prefix-mismatch-at-boundary"),
        pytest.param('[class~="zzz"]', [], id="includes-miss"),
        # empty operand never matches the prefix/suffix/substring/includes ops
        pytest.param('[href^=""]', [], id="prefix-empty"),
        pytest.param('[href$=""]', [], id="suffix-empty"),
        pytest.param('[href*=""]', [], id="substring-empty"),
        pytest.param('[class~=""]', [], id="includes-empty"),
        # non-ASCII attribute names are not present, so they match nothing
        pytest.param("[café]", [], id="latin1-name"),
        pytest.param("[中]", [], id="bmp-name"),
        pytest.param("[😀]", [], id="astral-name"),
        # combinators
        pytest.param("section a", ["a"], id="descendant"),
        pytest.param("ul > li", ["li", "li"], id="child"),
        pytest.param("section > a", [], id="child-not-direct"),
        pytest.param("h2 + p", ["p"], id="adjacent-sibling"),
        pytest.param("p + h2", [], id="adjacent-no-preceding-element"),
        pytest.param("h2 ~ p", ["p", "p"], id="general-sibling"),
        pytest.param("table ~ p", [], id="general-sibling-no-preceding"),
        pytest.param("h2 ~ ul", ["ul"], id="general-sibling-scans-past-non-matches"),
        pytest.param("div > ul > li", [], id="child-chain-breaks-above-match"),
        pytest.param("h2 + ul", [], id="adjacent-compound-miss"),
        # grouping
        pytest.param("h2, a", ["h2", "a"], id="comma-group-in-document-order"),
        pytest.param("#nope", [], id="id-present-but-mismatched"),
        # the encode buffers cap tag names at 60 bytes and attribute names at 124;
        # a longer name overruns the cap and cannot match any real element
        pytest.param("z" * 130, [], id="overlong-tag-name"),
        pytest.param("[" + "z" * 130 + "]", [], id="overlong-attr-name"),
    ],
)
def test_select_over_doc(selector: str, tags: list[str]) -> None:
    assert _sel(_DOC, selector) == tags


@pytest.mark.parametrize(
    ("html", "selector", "tags"),
    [
        pytest.param("<div></div>text<p>x</p>", "div + p", ["p"], id="adjacent-skips-text-node"),
        # the nearest <div> ancestor has no matching <i>, but a higher one does
        pytest.param(
            "<div class=x><i><div class=y><b>hit</b></div></i></div>", ".x i b", ["b"], id="descendant-backtracks-hit"
        ),
        pytest.param(
            "<div class=x><i><div class=y><b>hit</b></div></i></div>", ".y i b", [], id="descendant-backtracks-miss"
        ),
        # span's preceding p siblings: pick one whose own preceding sibling is an h1
        pytest.param(
            "<h1>a</h1><p class=x>b</p><h1>c</h1><p>d</p><span>e</span>",
            "p ~ span",
            ["span"],
            id="general-sibling-backtracks",
        ),
        # the near <p> fails its left context (an <a> sits before it); the scan
        # continues to the far <p>, whose preceding element is the <i>
        pytest.param(
            "<i></i><p>1</p><a></a><p>2</p><span>x</span>",
            "i + p ~ span",
            ["span"],
            id="general-sibling-backtracks-left-context",
        ),
        # span's previous element p matches, but p's previous element is x, not i
        pytest.param("<x></x><p>p</p><span>s</span>", "i + p + span", [], id="adjacent-chain-misses"),
        pytest.param("<input disabled>", "[disabled]", ["input"], id="valueless-exists"),
        pytest.param("<input disabled>", '[disabled=""]', ["input"], id="valueless-empty-equals"),
        pytest.param("<input disabled>", "[disabled=x]", [], id="valueless-value-mismatch"),
        pytest.param("<div id>", "#x", [], id="valueless-id-matches-nothing"),
        pytest.param("<div class>", ".x", [], id="valueless-class-matches-nothing"),
        pytest.param("<DIV></DIV>", "DIV", ["div"], id="type-folds-case"),
        pytest.param('<div class="a_b">', ".a_b", ["div"], id="class-underscore"),
        pytest.param('<div class="café">', ".café", ["div"], id="class-non-ascii"),
        pytest.param("<café>x", "café", ["café"], id="type-non-ascii"),
        # a token followed by whitespace running to the end of the value still matches
        pytest.param('<div class="a ">', ".a", ["div"], id="trailing-whitespace-class-hit"),
        pytest.param('<div data-x="a ">', '[data-x~="a"]', ["div"], id="trailing-whitespace-attr-hit"),
        # a trailing whitespace run is consumed without yielding an empty token, so
        # the scan reaches the end with no match, exercising the loop-exit branches
        pytest.param('<div class="a ">', ".zzz", [], id="trailing-whitespace-class-miss"),
        pytest.param('<div data-x="a ">', '[data-x~="zzz"]', [], id="trailing-whitespace-attr-miss"),
        # CSS identifier escapes (Syntax 4.3.7): a backslash escapes the literal
        # next character, and \HHHHHH (with one optional trailing space) a code point
        pytest.param('<div class="foo:bar">', r".foo\:bar", ["div"], id="class-escaped-colon"),
        pytest.param('<div class="foo.bar">', r".foo\.bar", ["div"], id="class-escaped-dot"),
        pytest.param('<div id="a:b">', r"#a\:b", ["div"], id="id-escaped-colon"),
        pytest.param('<div id="a b">', r"#a\ b", ["div"], id="id-escaped-space"),
        pytest.param('<div class="©">', r".\A9 ", ["div"], id="class-hex-escape-trailing-space"),
        pytest.param('<div class="©">', r".\0000A9", ["div"], id="class-hex-escape-six-digits"),
        pytest.param("<div></div>", r"\64 iv", ["div"], id="type-hex-escape"),
        pytest.param('<div data-x="a:b">', r"[data\-x=a\:b]", ["div"], id="attr-name-and-value-escapes"),
        # a null, surrogate, or out-of-range hex escape folds to U+FFFD
        pytest.param('<div class="�">', r".\0", ["div"], id="class-null-hex-escape-is-replacement"),
        pytest.param('<div class="�">', r".\D800", ["div"], id="class-surrogate-hex-is-replacement"),
        pytest.param("<div class='\ue000'>", r".\E000", ["div"], id="class-just-above-surrogate-is-kept"),
        pytest.param('<div class="\U0010ffff">', r".\10FFFF", ["div"], id="class-max-code-point-hex-escape"),
        pytest.param('<div class="�">', r".\110000", ["div"], id="class-out-of-range-hex-is-replacement"),
        # a hex escape ends at end of input, at a non-hex char, or at one trailing space
        pytest.param('<div class="©">', r".\A9", ["div"], id="class-hex-escape-at-eof"),
        pytest.param('<div class="Az">', r".\41z", ["div"], id="class-hex-escape-stops-at-non-hex"),
        # a trailing backslash escapes U+FFFD
        pytest.param('<div class="x�">', ".x\\", ["div"], id="class-trailing-backslash-is-replacement"),
        # the WHATWG "case-sensitivity of selectors" set: type/rel/... compare their
        # value ASCII case-insensitively by default on HTML elements (no flag needed)
        pytest.param('<input type="TEXT">', "input[type=text]", ["input"], id="ci-attr-set-default"),
        # an uppercase attribute name in the selector still resolves to the set
        pytest.param('<input type="x">', "[TYPE=X]", ["input"], id="ci-attr-set-uppercase-name"),
        pytest.param('<input type="TEXT">', "[type~=text]", ["input"], id="ci-attr-set-include-op"),
        pytest.param('<input type="TEXT">', "input[type=text s]", [], id="ci-attr-set-s-flag-forces-cs"),
        # the default applies only to HTML-namespace elements, not foreign ones
        pytest.param('<svg><rect type="FOO"></rect></svg>', "[type=foo]", [], id="ci-attr-set-not-foreign"),
        # an attribute outside the set keeps the default case-sensitive comparison
        pytest.param('<div data-x="ABC"></div>', "[data-x=abc]", [], id="non-ci-attr-stays-cs"),
        # sel_attr_default_ci rejects a non-ASCII name, an over-long name, and names
        # that fall before/after the set without matching it
        pytest.param("<div></div>", "[café=x]", [], id="ci-attr-set-non-ascii-name"),
        pytest.param("<div></div>", "[abcdefghijklmnopq=x]", [], id="ci-attr-set-overlong-name"),
        pytest.param("<div></div>", "[aaa=x]", [], id="ci-attr-set-before-set"),
        pytest.param("<div></div>", "[zzz=x]", [], id="ci-attr-set-after-set"),
    ],
)
def test_select_over_custom_html(html: str, selector: str, tags: list[str]) -> None:
    assert _sel(html, selector) == tags


# a five-item list, a mixed-type container, and custom-element siblings so the
# of-type pseudo-classes exercise both the builtin-atom and custom-name paths
_PSEUDO = (
    "<main>"
    "<ul><li>1</li><li>2</li><li>3</li><li>4</li><li>5</li></ul>"
    "<section><h2>t</h2><p>a</p><span>b</span><p></p><!--c--></section>"
    "<nav><x-a>1</x-a><x-b>2</x-b><x-a>3</x-a></nav>"
    "<header><b>x</b><svg></svg></header>"
    "<aside><!--z--></aside>"  # only a comment, so still :empty
    "</main>"
)


# cases whose subjects share a tag are keyed on text, which encodes the position;
# cases that identify elements by kind are keyed on tag in the by-tag test below
@pytest.mark.parametrize(
    ("selector", "texts"),
    [
        pytest.param("li:first-child", ["1"], id="first-child"),
        pytest.param("li:last-child", ["5"], id="last-child"),
        pytest.param("li:nth-child(1)", ["1"], id="nth-child-literal"),
        pytest.param("li:nth-child(2n)", ["2", "4"], id="nth-child-even-coeff"),
        pytest.param("li:nth-child(2n+1)", ["1", "3", "5"], id="nth-child-odd-coeff"),
        pytest.param("li:nth-child(even)", ["2", "4"], id="nth-child-even-keyword"),
        # pseudo-class names and An+B keywords are ASCII case-insensitive
        pytest.param("li:NTH-CHILD(EVEN)", ["2", "4"], id="nth-child-uppercase"),
        pytest.param("li:nth-child(odd)", ["1", "3", "5"], id="nth-child-odd-keyword"),
        pytest.param("li:nth-child(-n+2)", ["1", "2"], id="nth-child-negative-a"),
        pytest.param("li:nth-child(n)", ["1", "2", "3", "4", "5"], id="nth-child-bare-n"),
        pytest.param("li:nth-child( 2n + 1 )", ["1", "3", "5"], id="nth-child-whitespace"),
        pytest.param("li:nth-child(0)", [], id="nth-child-zero-matches-none"),
        pytest.param("li:nth-child(2n-1)", ["1", "3", "5"], id="nth-child-minus-b"),
        pytest.param("li:nth-last-child(1)", ["5"], id="nth-last-child"),
        pytest.param("li:nth-last-child(2)", ["4"], id="nth-last-child-second-from-end"),
        # of-type with a builtin atom: two <p> siblings around a <span>
        pytest.param("p:first-of-type", ["a"], id="first-of-type"),
        pytest.param("p:last-of-type", [""], id="last-of-type"),
        pytest.param("p:nth-of-type(2)", [""], id="nth-of-type"),
        pytest.param("p:nth-last-of-type(1)", [""], id="nth-last-of-type"),
        # of-type with custom elements: distinct names must not be conflated
        pytest.param("x-a:first-of-type", ["1"], id="custom-first-of-type"),
        pytest.param("x-a:nth-of-type(2)", ["3"], id="custom-nth-of-type"),
        pytest.param("x-b:only-of-type", ["2"], id="custom-only-of-type"),
    ],
)
def test_structural_pseudo_by_text(selector: str, texts: list[str]) -> None:
    assert [element.text for element in parse(_PSEUDO).select(selector)] == texts


@pytest.mark.parametrize(
    ("selector", "tags"),
    [
        pytest.param(":root", ["html"], id="root-is-html"),
        # a non-functional pseudo-class followed by a combinator (not '(')
        pytest.param(":root > head", ["head"], id="root-then-combinator"),
        pytest.param(":empty", ["head", "p", "svg", "aside"], id="empty-element-or-comment-only"),
        pytest.param("span:only-of-type", ["span"], id="only-of-type-hit"),
        pytest.param("p:only-of-type", [], id="only-of-type-miss"),
        pytest.param("x-a:only-of-type", [], id="custom-only-of-type-miss"),
        # an html <b> beside an <svg> sibling stays distinct by namespace, so the
        # <b> is still its parent's only element of that type
        pytest.param("b:only-of-type", ["b"], id="only-of-type-distinct-namespace"),
        pytest.param("main:only-child", ["main"], id="only-child-hit"),
        pytest.param("h2:only-child", [], id="only-child-miss"),
        pytest.param("aside:only-child", [], id="only-child-multiple-siblings"),
    ],
)
def test_structural_pseudo_by_tag(selector: str, tags: list[str]) -> None:
    assert [element.tag for element in parse(_PSEUDO).select(selector)] == tags


def test_universal_under_a_root() -> None:
    assert (section := parse(_DOC).select_one("section")) is not None
    assert len(section.select("*")) == 8  # h2, p, a, p, ul, li, li, my-widget


def test_select_is_scoped_to_descendants() -> None:
    assert (section := parse(_DOC).select_one("section")) is not None
    assert section.select("section") == []  # the receiver itself is not a descendant


def test_select_one() -> None:
    assert (match := parse(_DOC).select_one("p.lead")) is not None
    assert match.text.startswith("one")
    assert parse(_DOC).select_one("table") is None


@pytest.mark.parametrize(
    ("selector", "tags"),
    [
        # a namespace prefix is ignored in a namespaceless HTML document
        pytest.param("*|a", ["a"], id="any-ns-type"),
        pytest.param("|a", ["a"], id="no-ns-type"),
        pytest.param("ns|a", ["a"], id="named-ns-type"),
        pytest.param("*|*", ["html", "head", "body", "root", "a", "b"], id="any-ns-universal"),
        pytest.param("|*", ["html", "head", "body", "root", "a", "b"], id="no-ns-universal"),
        pytest.param("ns|*", ["html", "head", "body", "root", "a", "b"], id="named-ns-universal"),
    ],
)
def test_namespace_prefixed_type_selectors(selector: str, tags: list[str]) -> None:
    assert _sel("<root><a>1</a><b>2</b></root>", selector) == tags


def test_universal_followed_by_simple_is_not_a_namespace_prefix() -> None:
    # a '*' not followed by '|' is the plain universal selector, then the next simple
    assert _sel("<root><a class=x>1</a><b>2</b></root>", "*.x") == ["a"]


def test_namespace_prefixed_local_part_decodes_escapes() -> None:
    # the local part after a namespace prefix is a full identifier, so it decodes escapes
    assert _sel("<root><a>1</a></root>", "*|\\61") == ["a"]


_PSEUDO_DOC = (
    "<main>"
    '<section id="s">'
    "<h2>T</h2>"
    '<p class="lead">one <a href="/x">link</a></p>'
    "<p>two</p>"
    '<ul><li>a</li><li class="sel">b</li></ul>'
    "</section>"
    '<aside id="a"><span>side</span></aside>'
    "</main>"
)


@pytest.mark.parametrize(
    ("selector", "tags"),
    [
        # :is() and :where() match any nested alternative; :where() differs only in specificity
        pytest.param(":is(h2, ul)", ["h2", "ul"], id="is-list"),
        pytest.param(":IS(h2, ul)", ["h2", "ul"], id="is-uppercase-name"),  # pseudo-class names fold case
        pytest.param(":where(li)", ["li", "li"], id="where"),
        pytest.param("section :is(p, a)", ["p", "a", "p"], id="is-after-descendant"),
        pytest.param(":is(.lead) a", ["a"], id="is-class-then-descendant"),
        pytest.param("section:is(#s, .none)", ["section"], id="is-compound-subject"),
        pytest.param(":is(:where(li.sel))", ["li"], id="nested-is-where"),
        pytest.param(":is(em, strong)", [], id="is-no-match"),
        # :has() leading combinators: descendant, child, next-sibling, subsequent-sibling
        pytest.param("section:has(a)", ["section"], id="has-descendant"),
        pytest.param("section:has(> h2)", ["section"], id="has-child"),
        pytest.param("section:has(> a)", [], id="has-child-no-match"),
        pytest.param("h2:has(+ p)", ["h2"], id="has-next-sibling"),
        pytest.param("h2:has(~ ul)", ["h2"], id="has-subsequent-sibling"),
        pytest.param("li:has(~ li)", ["li"], id="has-subsequent-sibling-li"),
        # :has() multi-compound relative selectors exercise the interior combinators
        pytest.param("section:has(p a)", ["section"], id="has-descendant-chain"),
        pytest.param("section:has(p > a)", ["section"], id="has-child-chain"),
        pytest.param("section:has(h2 + p)", ["section"], id="has-next-sibling-chain"),
        pytest.param("section:has(h2 ~ ul)", ["section"], id="has-subsequent-chain"),
        # a leading sibling combinator reaches the anchor's following siblings and subtrees
        pytest.param("section:has(+ aside)", ["section"], id="has-next-sibling-element"),
        pytest.param("section:has(~ aside span)", ["section"], id="has-sibling-subtree"),
        # a following sibling matches the subject tag but is not adjacent, so the '+'
        # chain back to the anchor fails while the sibling walk keeps scanning
        pytest.param("h2:has(+ ul)", [], id="has-next-sibling-not-adjacent"),
        pytest.param("aside:has(p)", [], id="has-no-match"),
        pytest.param("aside:has(+ x)", [], id="has-no-following-sibling"),
        # no-match cases where the subject is found but its combinator chain does not
        # reach the anchor, exercising every relative-combinator dead end
        pytest.param("section:has(~ li)", [], id="has-tilde-subject-is-descendant"),
        pytest.param("section:has(span)", [], id="has-descendant-in-sibling-subtree"),
        pytest.param("section:has(li ~ h2)", [], id="has-tilde-chain-no-match"),
        pytest.param("section:has(ul a)", [], id="has-descendant-chain-no-match"),
        # an interior compound that matches only an ancestor of the anchor (main is
        # above section) is not within the anchor's subtree, so :has() does not match
        pytest.param("section:has(main p)", [], id="has-interior-compound-above-anchor"),
    ],
)
def test_functional_pseudo_classes(selector: str, tags: list[str]) -> None:
    assert _sel(_PSEUDO_DOC, selector) == tags


def test_has_skips_non_element_following_sibling() -> None:
    # a comment between the anchor and a following sibling must not break :has()
    doc = parse("<main><section></section><!--c--><aside><b>x</b></aside></main>")
    assert [element.tag for element in doc.select("section:has(~ aside b)")] == ["section"]


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("", id="empty"),
        pytest.param("  ", id="whitespace"),
        # functional pseudo-classes (the structural pseudo cases are grouped below)
        pytest.param(":unknown(p)", id="unsupported-pseudo"),
        pytest.param(":is", id="is-without-args"),
        pytest.param(":is(", id="is-unterminated"),
        pytest.param(":is(p", id="is-unterminated-after-arg"),
        pytest.param(":is()", id="is-empty-args"),
        pytest.param(":is(p,)", id="is-trailing-comma"),
        pytest.param(":is(.)", id="is-invalid-inner"),
        pytest.param(":is.x", id="pseudo-name-then-non-paren"),
        pytest.param(":ix(p)", id="pseudo-name-char-mismatch"),
        pytest.param(":1s(p)", id="pseudo-name-with-digit"),  # a below-'A' byte in the case fold
        pytest.param(":is(p):has(", id="pseudo-then-failing-pseudo"),
        pytest.param(":has(>)", id="has-dangling-combinator"),
        # a namespace prefix must be followed by a type or the universal selector
        pytest.param("*|", id="star-prefix-at-eof"),
        pytest.param("|", id="bare-pipe"),
        pytest.param("*|[a]", id="prefix-then-attribute"),
        pytest.param(".", id="bare-dot"),
        pytest.param("#", id="bare-hash"),
        pytest.param("p..", id="double-dot"),
        pytest.param("p >", id="dangling-combinator"),
        pytest.param("p,", id="trailing-comma"),
        pytest.param("[", id="open-bracket"),
        pytest.param("[a", id="unterminated-attr"),
        pytest.param("[a=]", id="missing-value"),
        pytest.param('[a="x]', id="unterminated-string"),
        pytest.param("[a!=b]", id="bad-operator"),
        pytest.param("[a~b]", id="tilde-without-equals"),
        pytest.param("[a~", id="operator-at-eof"),
        pytest.param("[a=", id="value-then-eof"),
        pytest.param("[a=b", id="value-at-eof"),
        pytest.param("[a=b c]", id="junk-after-value"),
        pytest.param("p!", id="trailing-junk"),
        pytest.param("p !", id="whitespace-then-junk"),
        pytest.param("p" + ".x" * 40, id="too-many-simples"),
        pytest.param(" ".join(["a"] * 40), id="too-many-compounds"),
        pytest.param(",".join(["a"] * 70), id="too-many-groups"),
        # a backslash before any CSS newline (LF, CR, FF) does not start an escape,
        # so the dangling backslash leaves an empty identifier
        pytest.param(".a\\\nb", id="escape-before-lf"),
        pytest.param(".a\\\rb", id="escape-before-cr"),
        pytest.param(".a\\\x0cb", id="escape-before-ff"),
        # pseudo-classes: a bare or unknown one, a pseudo-element, a functional
        # pseudo missing its argument list, and malformed An+B
        pytest.param(":", id="bare-colon"),
        pytest.param("::before", id="pseudo-element"),
        pytest.param(":unknown", id="unknown-pseudo"),
        pytest.param(":root(x)", id="non-functional-with-args"),
        pytest.param(":nth-child", id="functional-without-args"),
        pytest.param(":nth-child()", id="empty-anb"),
        pytest.param(":nth-child(2n+)", id="anb-sign-without-digits"),
        pytest.param(":nth-child(+)", id="anb-bare-sign"),
        pytest.param(":nth-child(2n+1", id="anb-unclosed"),
        pytest.param(":nth-child(", id="anb-eof-after-paren"),
        pytest.param(":nth-child(2n", id="anb-eof-after-n"),
        pytest.param(":nth-child.x", id="functional-without-paren"),
        pytest.param(":nth-child(2n x)", id="anb-trailing-junk"),
    ],
)
def test_invalid_selectors_raise(selector: str) -> None:
    with pytest.raises(ValueError, match="selector"):
        parse(_DOC).select(selector)


def test_select_one_rejects_invalid_selector() -> None:
    with pytest.raises(ValueError, match="selector"):
        parse(_DOC).select_one("[")


@pytest.mark.parametrize("method", [pytest.param("select", id="select"), pytest.param("select_one", id="select_one")])
def test_rejects_non_str(method: str) -> None:
    with pytest.raises(TypeError):
        getattr(parse(_DOC), method)(123)
