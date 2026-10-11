from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from subprocess import run  # ruff: ignore[suspicious-subprocess-import] - libFuzzer owns a separate interpreter.
from typing import TYPE_CHECKING, Final, cast

import pytest
from fuzz.atheris_header import HEADER_SIZE, SEED_HEADER, Header
from fuzz.atheris_runtime import build_runtime

import turbohtml
from turbohtml import Html, Markdown, Minify, NodeIterator, _html, parse, parse_xml
from turbohtml._html import _tree_verify
from turbohtml.clean import (
    CSSMinify,
    JSMinify,
    LinkDetector,
    Linker,
    Linkify,
    PhoneFormat,
    PhoneNumber,
    PhoneNumbers,
    Policy,
    Sanitizer,
    minify,
    minify_css,
    minify_css_inline,
    minify_js,
)
from turbohtml.migration.stdlib import HTMLParser
from turbohtml.rewrite import rewrite
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable

# numbers from three regions, an extension, an email and a URL, so a linkify walk splits the run into many nodes
_TEXT: Final = (
    "Call +1 650-253-0000 or (650) 253-0000 ext. 123, London +44 20 7946 0958, Berlin 030 123456, "
    "mail a@example.com or see https://example.com/x"
)
_PHONES: Final = PhoneNumbers(regions=("US", "GB", "DE"))
_LINKER: Final = Linker(Linkify(phones=_PHONES, parse_email=True))
_DETECTOR: Final = LinkDetector(phones=_PHONES)
_NUMBER: Final = PhoneNumber.parse("+44 20 7946 0958")


def _fragment() -> turbohtml.Node:
    # a fresh tree per run; serializing copies the parsed text out of the input, so the sweep fails only the walk
    root = turbohtml.parse_fragment(f"<p>{_TEXT}</p>")
    root.serialize()
    return root


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    ("call", "setup"),
    [
        pytest.param(_LINKER.linkify_node, _fragment, id="linkify-node"),
        pytest.param(lambda _: _DETECTOR.find(_TEXT), lambda: None, id="find"),
        pytest.param(lambda _: PhoneNumber.parse("(650) 253-0000", regions=("US",)), lambda: None, id="parse"),
        pytest.param(lambda _: [_NUMBER.format(style) for style in PhoneFormat], lambda: None, id="format"),
    ],
)
def test_phone_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], call: Callable[[object], object], setup: Callable[[], object]
) -> None:
    call(setup())  # fill the lazily built region tables first, so the sweep counts only the call's own allocations
    assert set(alloc_sweep(call, setup)) == {"MemoryError"}


if TYPE_CHECKING:
    from collections.abc import Callable

_SANITIZER: Final = Sanitizer(Policy.strict())
# Elements only: a text node would add a lazy text copy, which these tests do not target. The custom attribute names
# make every copy intern them again in its own tree.
_TREE: Final = "<b zq=1><i></i></b><x y=1></x>"
# the input, the radio and the option carry enough attributes that adding value, checked or selected outgrows their
# arena block
_INPUT: Final = "<input a0=1 a1=1 a2=1>"
_RADIO: Final = "<form id=f></form><input type=radio name=r form=f a=1 b=2 c=3 d=4 e=5 f=6 g=7>"
_SELECT: Final = "<select><option value=a a0=1 a1=1></option></select>"


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    ("markup", "call"),
    [
        pytest.param(_TREE, _SANITIZER.sanitize_node, id="sanitize-node"),
        # enough escaped tags that the sanitizer's text nodes outgrow the copy's first arena blocks
        pytest.param("<x y=1></x>" * 4, _SANITIZER.sanitize_node, id="sanitize-escaped-tags"),
        pytest.param(_TREE, copy.copy, id="copy"),
        pytest.param(_TREE, lambda _: turbohtml.Element("p", {"zq": "1", "yq": ""}), id="element"),
        pytest.param(_TREE, lambda fragment: fragment.children[0].attrs.__setitem__("zr", "1"), id="set-attribute"),
        pytest.param(_INPUT, lambda fragment: setattr(fragment.children[0], "field_value", "v"), id="input-value"),
        pytest.param(_RADIO, lambda fragment: setattr(fragment.children[1], "checked", True), id="radio-checked"),
        pytest.param(_SELECT, lambda fragment: setattr(fragment.children[0], "field_value", "a"), id="select-value"),
    ],
)
def test_dom_construction_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], markup: str, call: Callable[[turbohtml.Node], object]
) -> None:
    assert set(alloc_sweep(call, lambda: turbohtml.parse_fragment(markup))) == {"MemoryError"}


if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import ParseError

# Each seed opens with the error it targets, so recording that error is the error list's first allocation: a control
# and a noncharacter for the preprocessing scan (in a wide and a one-byte str, which it reads on separate paths), a
# duplicate attribute and a NUL reference for the tokenizer, and a second doctype and a self-closed div for the tree
# builder.
_SEEDS: Final = {
    "control-wide": "\x01\ufdd0<!DOCTYPE html>",
    "control-one-byte": "\x01\xa0<!DOCTYPE html>",
    "duplicate-attribute": "<!DOCTYPE html><p a=1 a=2>",
    "nul-reference": "<!DOCTYPE html>&#0;",
    "second-doctype": "<!DOCTYPE html><!DOCTYPE x>",
    "self-closed-div": "<!DOCTYPE html><div/>",
}


def _errors(seed: str) -> list[ParseError]:
    return turbohtml.parse(seed).errors


def _strict(seed: str) -> None:
    with contextlib.suppress(turbohtml.HTMLParseError):
        turbohtml.parse(seed, strict=True)


def _streamed(seed: str) -> list[ParseError]:
    parser = turbohtml.IncrementalParser()
    parser.feed(seed[:4])
    parser.feed(seed[4:])
    return parser.close().errors


@pytest.mark.fuzz_build
@pytest.mark.parametrize("seed", [pytest.param(seed, id=name) for name, seed in _SEEDS.items()])
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(_errors, id="errors"),
        pytest.param(_strict, id="strict"),
        pytest.param(_streamed, id="incremental"),
    ],
)
def test_parse_error_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], call: Callable[[str], object], seed: str
) -> None:
    assert set(alloc_sweep(lambda _: call(seed))) == {"MemoryError"}


@pytest.mark.fuzz_build
def test_xml_parse_error_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]],
) -> None:
    def parse_malformed(_: object) -> None:
        with contextlib.suppress(turbohtml.HTMLParseError):
            turbohtml.parse_xml("<a><b></a>")

    assert set(alloc_sweep(parse_malformed)) == {"MemoryError"}


@pytest.mark.fuzz_build
def test_parse_error_read_retries_after_allocation_failure(alloc_sweep: Callable[..., list[str]]) -> None:
    # the first read of Document.errors merges the preprocessing errors; a read that failed to allocate leaves them
    # unmerged, so the next read returns the complete list
    seed = _SEEDS["control-wide"]
    expected = [(error.code, error.line, error.col) for error in turbohtml.parse(seed).errors]
    documents: list[turbohtml.Document] = []

    def setup() -> turbohtml.Document:
        documents.append(turbohtml.parse(seed))
        return documents[-1]

    alloc_sweep(lambda document: document.errors, setup)
    assert {tuple((error.code, error.line, error.col) for error in document.errors) for document in documents} == {
        tuple(expected)
    }


if TYPE_CHECKING:
    from collections.abc import Callable

# duplicate and dynamic attribute names, a non-ASCII name, leading text and a misnested formatting run reach the
# attribute sink, the dynamic name table, the text copy and the open-element stack
_SEED: Final = "  x<p zq=1 zq=2 \u00e9t\u00e9=3>t</p><b><i>u</b>v<table><td>w"
# a selected option fills the select's selectedcontent cache, which the parse allocates on first use
_ALLOC_TREE_BUILDER_SELECT: Final = (
    "<select><button><selectedcontent></selectedcontent></button><option selected>a</option></select>"
)
# text that opens no element: the implied html, head and body and the text run allocate in one token
_UNCLOSED: Final = "d>\n (min-width: 600px) { .grid {"
# namespaced and plain attribute names the XML parser interns into the tree's dynamic table
_STYLESHEET: Final = (
    '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
    '<xsl:param name="value"/><xsl:template match="/"><xsl:value-of select="$value"/></xsl:template></xsl:stylesheet>'
)


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda _: turbohtml.parse(_SEED), id="parse"),
        pytest.param(lambda _: turbohtml.parse_fragment(_SEED), id="parse-fragment"),
        pytest.param(lambda _: turbohtml.parse(_ALLOC_TREE_BUILDER_SELECT), id="parse-selectedcontent"),
        pytest.param(lambda _: turbohtml.parse("x").serialize(), id="parse-text"),
        pytest.param(lambda _: turbohtml.parse(_UNCLOSED).serialize(), id="parse-unclosed-text"),
        pytest.param(lambda _: turbohtml.parse("<?p x?>"), id="parse-processing-instruction"),
        pytest.param(lambda _: minify("x"), id="minify"),
        pytest.param(lambda _: turbohtml.parse_xml(_STYLESHEET), id="parse-xml-attributes"),
        pytest.param(lambda _: HTMLParser().feed("<b>x</b>"), id="stdlib-parser"),
        pytest.param(lambda _: turbohtml.Tokenizer(), id="tokenizer"),
        pytest.param(lambda _: rewrite(_SEED), id="rewrite"),
        pytest.param(
            lambda _: rewrite(_SEED, elements=[("[zq]", lambda element: element.set_attribute("yq", "1"))]),
            id="rewrite-custom-attributes",
        ),
    ],
)
def test_tokenizer_consumer_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], call: Callable[[object], object]
) -> None:
    assert set(alloc_sweep(call)) == {"MemoryError"}


if TYPE_CHECKING:
    from collections.abc import Callable

# the stubs describe the production module, which lacks the hook
_VERIFY_BROKEN: Final[Callable[[str], tuple[int, int, int, int]]] = vars(_html)["_fuzz_verify_broken"]


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        pytest.param("intact", (0, 0, 0, 0), id="intact"),
        pytest.param("sibling-cycle", (1, 0, 0, 0), id="sibling-cycle"),
        pytest.param("parent-mismatch", (1, 0, 0, 0), id="parent-mismatch"),
        pytest.param("foreign-child", (0, 1, 0, 0), id="foreign-child"),
        pytest.param("foreign-start", (0, 1, 0, 0), id="foreign-start"),
    ],
)
def test_tree_verify_reports_a_broken_tree_without_walking_into_it(
    kind: str, expected: tuple[int, int, int, int]
) -> None:
    assert _VERIFY_BROKEN(kind) == expected


@pytest.mark.fuzz_build
def test_tree_verify_breakage_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown breakage"):
        _VERIFY_BROKEN("bogus")


if TYPE_CHECKING:
    from collections.abc import Callable

# Destructuring targets, arrow and function parameter lists, and a parenthesized optional chain reach the parser's
# early-error walks; unused, single-use, literal and shadowing bindings reach the compress and mangle passes; a string
# concatenation, a separated BigInt and a 70-digit number reach the fold and print buffers, and an escaped name the
# identifier decoder.
_SCRIPT: Final[str] = (
    f"var long_name = 1, s = 'a' + 'b', big = 1_000n, wide = {'1' * 70}, \\u0061bc = 0; console.log(long_name + 2);"
    "function f(alpha, [beta, {gamma = 1, ...rest}], ...more) { const unused = alpha; let x = [1, , 3];"
    " if (alpha) { return !0 } else { return beta ? gamma : rest } } function p() { const c = 5; return c + c }"
    "var g = (p, q) => p + q, h = async function* () { yield* g(1, 2) }, o = (a?.b).c;"
    "class K { static s = 1; get v() { return this.s } m() { return typeof k == 'undefined' } }"
    "for (const [k, v] of Object.entries({a: 1, b: 2})) { label: while (k) { break label } }"
    "try { f(1) } catch ({message}) { x = `t${message}u` } new K().m(), void 0;"
)
_PAGE: Final[str] = f"<p>x</p><script>{_SCRIPT}</script><script type=module>let m = 1; m++</script>"
_MINIFY_JS: Final[Html] = Html(layout=Minify(minify_js=JSMinify()))


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: minify_js("var a = 1"), id="declaration"),
        pytest.param(lambda: minify_js("function f(long_name) { return long_name + 1 }"), id="mangle"),
        pytest.param(lambda: minify_js("[a, b, c, d] = [1, 2, 3, 4]"), id="assignment-pattern"),
        pytest.param(lambda: minify_js("var [a, b, c, d, e, f, g, h] = i"), id="binding-pattern"),
        pytest.param(lambda: minify_js(_SCRIPT), id="script"),
        pytest.param(lambda: minify_js(_SCRIPT, JSMinify(fold=False, mangle=False)), id="parse-only"),
        # the empty statements fill the fuzz build's node arena to a doubling, so the sequence node the fold pass
        # allocates for the merged calls takes the failed growth
        pytest.param(lambda: minify_js(";;;;;a(); b(); c();"), id="fold-allocation"),
    ],
)
def test_js_minify_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], call: Callable[[], object]
) -> None:
    assert set(alloc_sweep(lambda _: call())) == {"MemoryError"}


@pytest.mark.fuzz_build
def test_html_js_minify_allocation_failure_raises_memory_error(alloc_sweep: Callable[..., list[str]]) -> None:
    assert set(alloc_sweep(lambda document: document.serialize(_MINIFY_JS), _page)) == {"MemoryError"}


def _page() -> turbohtml.Document:
    document = turbohtml.parse(_PAGE)
    document.serialize()  # copy the parsed text out of the input now, so the sweep fails only the minifier
    return document


if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    ("markup", "options"),
    [
        pytest.param("<table><td>x", Markdown(), id="table-cell"),
        pytest.param("<table><td>x", Markdown(tables=Markdown.Tables(pad=True)), id="padded-table-cell"),
        pytest.param("<table><td><blockquote>x<blockquote>y", Markdown(), id="table-cell-quote-prefix"),
        pytest.param("<ul><li>x<ul><li>", Markdown(), id="nested-list-prefix"),
        pytest.param(
            "<span><ul><li>x<ul><li>y</span>",
            Markdown(converters={"span": lambda _element, content: content}),
            id="converter-list-prefix",
        ),
    ],
)
def test_markdown_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], markup: str, options: Markdown
) -> None:
    assert set(alloc_sweep(lambda document: document.to_markdown(options), lambda: turbohtml.parse(markup))) == {
        "MemoryError"
    }


if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node

# the stubs describe the production module, which lacks the hook
_ESCAPE_ITERATORS: Final[Callable[[Node], int]] = vars(_html)["_fuzz_escape_iterators"]


@pytest.mark.fuzz_build
def test_tree_verify_counts_an_iterator_reference_outside_its_root() -> None:
    document: Final = parse("<p><b></b></p>")
    iterator: Final = NodeIterator(document.select("p")[0])
    assert (_ESCAPE_ITERATORS(document), _tree_verify(document), iterator.reference_node) == (1, (0, 0, 1, 0), document)


if TYPE_CHECKING:
    from collections.abc import Callable

# One stylesheet that walks the engine's buffers: merged box and font shorthands, calc() and color folds, a legacy
# filter, a unicode-range list, adjacent @media blocks that join, a nested rule, and more than 32 rules so the rule
# merge builds its hash table.
_RULES: Final[str] = "".join(f".r{index}{{color:#ffffff;margin:0 0 0 0}}" for index in range(40))
_ALLOC_CSS_MINIFY_STYLESHEET: Final[str] = (
    '@charset "utf-8";'
    ".a{margin-top:1px;margin-right:2px;margin-bottom:1px;margin-left:2px;color:rgb(255,0,0);"
    "width:calc(100% - 2*10px);font-family:'Times New Roman',serif;"
    "filter:progid:DXImageTransform.Microsoft.Alpha(Opacity=80);"
    "background:url('x.png') no-repeat;transform:rotate(calc(0.5turn));color:rgb(calc(255),0,0)}"
    ".b{color:red}.a{padding:0 0 0 0}"
    "@media screen{.c{z-index:calc(1 + 2)}}@media screen{.d{font-weight:bold}}"
    "@font-face{unicode-range:U+0025-00FF,u+4??}"
    ".e{&:hover{color:hsl(120deg 100% 50% / 50%)}--custom: { a } ;flex:1 1 0%}"
    f"{_RULES}"
)
# one selector repeated with a new property each time, so each merge re-renders a longer body into the pool
_MERGES: Final[str] = "".join(f".m{{margin-{side}:{index}px}}" for index, side in enumerate(["top", "left"] * 12))
# enough declarations that the dedup table outgrows its stack slots
_DECLARATIONS: Final[str] = ";".join(f"--p{index}:{index}px" for index in range(300)) + ";color:#ff0000;margin:0 0 0 0"
_ALLOC_CSS_MINIFY_PAGE: Final[str] = (
    f"<style>{_ALLOC_CSS_MINIFY_STYLESHEET}</style><p style='margin:0 0 0 0;color:#ff0000'>x</p>"
)
_MINIFY_CSS: Final[Html] = Html(layout=Minify(minify_css=CSSMinify()))


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: minify_css(".a{color:red}"), id="one-rule"),
        pytest.param(lambda: minify_css(".a{margin:0 0 0 0}"), id="one-shorthand"),
        pytest.param(lambda: minify_css_inline("color:red;margin:0 0 0 0"), id="short-inline"),
        pytest.param(lambda: minify_css(_ALLOC_CSS_MINIFY_STYLESHEET), id="stylesheet"),
        pytest.param(lambda: minify_css_inline(_DECLARATIONS), id="inline"),
        pytest.param(lambda: minify_css(".\\31 x{color:red}.y{content:'open"), id="spelled"),
        pytest.param(lambda: minify_css(_MERGES), id="merges"),
    ],
)
def test_css_minify_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], call: Callable[[], object]
) -> None:
    assert set(alloc_sweep(lambda _: call())) == {"MemoryError"}


@pytest.mark.fuzz_build
def test_html_css_minify_allocation_failure_raises_memory_error(alloc_sweep: Callable[..., list[str]]) -> None:
    assert set(alloc_sweep(lambda document: document.serialize(_MINIFY_CSS), _alloc_css_minify_page)) == {"MemoryError"}


def _alloc_css_minify_page() -> turbohtml.Document:
    document = turbohtml.parse(_ALLOC_CSS_MINIFY_PAGE)
    document.serialize()  # copy the parsed text out of the input now, so the sweep fails only the minifier
    return document


if TYPE_CHECKING:
    from collections.abc import Callable

# only the fuzz-only build (meson -Dfuzzing=true) defines the hook, so the stubs omit it
_HOOKS: Final = vars(importlib.import_module("turbohtml._html"))


@pytest.fixture
def alloc_sweep() -> Callable[..., list[str]]:
    """
    Fail each PyMem allocation of a call in turn and name what each failure produced.

    A count-only run (position 0) measures the call's allocations, then runs 1..N each fail one of them, the failure
    libxml2's fuzzers inject at one chosen position (``xmlFuzzInjectFailure`` in its ``fuzz/fuzz.c``). Each entry is
    the escaping exception's name or ``result``, with a suffix when the injected failure never fired. ``setup`` builds
    a fresh argument for each run outside the injection window, so a cache the first run fills cannot shift later
    positions.
    """
    if "_fuzz_inject_failure" not in _HOOKS:
        pytest.fail("fuzz-build tests need the fuzz-only build: meson setup -Dfuzzing=true")
    inject: Final[Callable[[int], tuple[int, bool]]] = _HOOKS["_fuzz_inject_failure"]

    def sweep(call: Callable[[object], object], setup: Callable[[], object] = lambda: None) -> list[str]:
        argument = setup()
        inject(0)
        call(argument)
        allocations, _ = inject(0)
        outcomes = []
        for position in range(1, allocations + 1):
            argument = setup()
            inject(position)
            try:
                try:
                    call(argument)
                finally:
                    _, failed = inject(0)
            except Exception as exc:  # ruff: ignore[blind-except] - the outcome under test is which exception escapes
                outcome = type(exc).__name__
            else:
                outcome = "result"
            outcomes.append(outcome if failed else f"{outcome} without the injected failure")
        return outcomes

    return sweep


if TYPE_CHECKING:
    from collections.abc import Callable

# three compounds, two arms, a forgiving :is() list and a relative :has() argument grow every parser buffer; the
# tag-subject selectors build the document's element index
_SELECTOR: Final = "main > p[zq] a, :is(b, i, [zq]) :has(> a)"


def _document() -> turbohtml.Document:
    # a fresh document per run, so the compiled-selector cache of one run cannot skip the parser in the next
    return turbohtml.parse("<!DOCTYPE html><main><p zq=1><a>t</a><b>u</b></p></main>")


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda document: document.select(_SELECTOR), id="select"),
        pytest.param(lambda document: document.select_one(_SELECTOR), id="select-one"),
        pytest.param(lambda document: document.select("b"), id="select-tag-index"),
        pytest.param(lambda document: document.select("p b"), id="select-index"),
        pytest.param(lambda document: document.select_one("p b"), id="select-one-index"),
        pytest.param(lambda document: document.find_all("b"), id="find-all-index"),
    ],
)
def test_selector_allocation_failure_raises_memory_error(
    alloc_sweep: Callable[..., list[str]], call: Callable[[turbohtml.Document], object]
) -> None:
    assert set(alloc_sweep(call, _document)) == {"MemoryError"}


if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Document

_SIBLINGS: Final = parse_xml("<r>" + "<a/>" * 100 + "</r>")
_NESTED: Final = "count(//a[count(//a[count(//a) > 0]) > 0])"
_DOUBLING: Final = parse_xml(
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:output method="text"/><xsl:param name="depth"/>'
    '<xsl:template match="/"><xsl:call-template name="split">'
    '<xsl:with-param name="level" select="$depth"/></xsl:call-template></xsl:template>'
    '<xsl:template name="split"><xsl:param name="level"/>'
    '<xsl:if test="$level &gt; 0">'
    '<xsl:call-template name="split"><xsl:with-param name="level" select="$level - 1"/></xsl:call-template>'
    '<xsl:call-template name="split"><xsl:with-param name="level" select="$level - 1"/></xsl:call-template>'
    '</xsl:if><xsl:if test="$level = 0">x</xsl:if></xsl:template></xsl:stylesheet>'
)


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    ("run", "expected"),
    [
        pytest.param(lambda: str(_SIBLINGS.xpath("count(//a[count(//a) > 0])")), "100.0", id="xpath"),
        pytest.param(lambda: Transform(_DOUBLING)(_SIBLINGS, depth="10"), "x" * 1024, id="xslt-apply"),
        pytest.param(lambda: Transform(_literal_sheet(100))(_SIBLINGS), "x" * 100, id="xslt-compile"),
    ],
)
def test_operation_limit_within_budget(run: Callable[[], str], expected: str) -> None:
    assert run() == expected


@pytest.mark.fuzz_build
@pytest.mark.parametrize(
    ("run", "message"),
    [
        pytest.param(
            lambda: str(_SIBLINGS.xpath(_NESTED)), r"^xpath: evaluation exceeded the operation limit$", id="xpath"
        ),
        pytest.param(
            lambda: Transform(_DOUBLING)(_SIBLINGS, depth="17"),
            r"^xslt: applying the stylesheet exceeded the operation limit of \d+$",
            id="xslt-apply",
        ),
        pytest.param(
            lambda: Transform(_value_of_sheet(_NESTED))(_SIBLINGS),
            r"^xslt: expression error \(evaluation exceeded the operation limit\)$",
            id="xslt-select",
        ),
        pytest.param(
            lambda: Transform(_literal_sheet(200_000))(_SIBLINGS),
            r"^xslt: compiling the stylesheet exceeded the operation limit of \d+$",
            id="xslt-compile",
        ),
    ],
)
def test_operation_limit_stops(run: Callable[[], str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        run()


def _literal_sheet(elements: int) -> Document:
    return parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        f'<xsl:output method="text"/><xsl:template match="/">{"<e>x</e>" * elements}</xsl:template></xsl:stylesheet>'
    )


def _value_of_sheet(expression: str) -> Document:
    return parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"><xsl:output method="text"/>'
        f'<xsl:template match="/"><xsl:value-of select="{expression.replace(">", "&gt;")}"/></xsl:template>'
        "</xsl:stylesheet>"
    )


if TYPE_CHECKING:
    from typing import Protocol

    class _Runtime(Protocol):
        def path(self) -> str: ...


_CONSUME: Final = """
import sys
from pathlib import Path
from typing import Final
from fuzz.atheris_header import split_header
from fuzz.atheris_targets import public_targets
_TARGET: Final = next(target for target in public_targets() if target.name == sys.argv[1])
header, payload = split_header(Path(sys.argv[2]).read_bytes())
_TARGET.run(payload, header)
"""

# the window results go to preallocated slots, since an append would allocate inside the window and take the failure
_FAIL: Final = """
import sys
from pathlib import Path
from fuzz.atheris_runtime import failure_hook, rejecting_callback
from fuzz.atheris_targets import public_targets
target = next(target for target in public_targets() if target.name == sys.argv[1])
hook = failure_hook()
windows = [None, None]
def inject(position):
    windows[position == 0] = window = hook(position)
    return window
rejecting_callback(target.run, target.exceptions, lambda: None, inject)(Path(sys.argv[2]).read_bytes())
assert windows[1][1], windows
"""


_LIST: Final = """
import json
from fuzz.atheris_generation_targets import encoding_observation, generation_targets
assert encoding_observation(b"<p>\\xe9</p>", "windows-1252") == "é"
assert [target.name for target in generation_targets("windows-1252")] == [
    "markdown-source", "markdown-html", "encoding-bytes"
]
from fuzz.atheris_targets import owner_inventory, public_targets
print(json.dumps({"owners": len(owner_inventory()), "targets": [target.name for target in public_targets()]}))
"""


@pytest.mark.atheris_native
@pytest.mark.oracle
def test_atheris_public_consumers_run_with_native_coverage(tmp_path: Path) -> None:
    if sys.platform != "linux" or importlib.util.find_spec("atheris") is None:
        pytest.skip("Atheris's native runtime requires its optional Linux wheel")
    runtime: Final = cast("_Runtime", importlib.import_module("atheris"))
    library: Final = build_runtime(Path(runtime.path()) / "libclang_rt.fuzzer_no_main.a", tmp_path / "runtime")
    environment: Final = {
        **os.environ,
        "LD_PRELOAD": str(library),
        "PYTHONPATH": str(Path(__file__).parents[1] / "tools"),
        "GCOV_PREFIX": str(tmp_path / "inventory-gcda"),
    }
    inventory: Final = run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and owned modules.
        [sys.executable, "-c", _LIST],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    targets: Final = json.loads(inventory.stdout)
    assert (targets["owners"], len(targets["targets"])) == (223, 34)
    generated_seeds: Final[dict[str, bytes]] = {}
    for profile in ("html", "xml", "css-stylesheet", "markdown-source", "markdown-html", "encoding"):
        generated: Final = tmp_path / ("generated-" + profile)
        run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and finite production sweep.
            [
                sys.executable,
                "-m",
                "fuzz.atheris_corpora",
                "--profile",
                profile,
                *(["--count", "0", "--sweep"] if profile == "encoding" else ["--count", "1"]),
                "--budget",
                "512",
                "--output",
                str(generated),
            ],
            env={**environment, "GCOV_PREFIX": str(tmp_path / (profile + "-generation-gcda"))},
            capture_output=True,
            check=True,
        )
        generated_manifest: Final = json.loads((generated / "manifest.json").read_text())
        assert generated_manifest["entries"]
        assert not generated_manifest["rejected"]
        for generated_entry in generated_manifest["entries"]:
            if generated_entry["encoding"] is None or (
                generated_entry["encoding"] == "UTF-8" and not generated_entry["sniff"]
            ):
                generated_seeds[generated_entry["target"]] = (generated / generated_entry["file"]).read_bytes()
    assert {
        "html-tokenizer",
        "xml-schema",
        "css-stylesheet",
        "markdown-source",
        "markdown-html",
        "encoding-bytes",
    } <= generated_seeds.keys()
    (tmp_path / "generated-targets.json").write_text(
        json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in generated_seeds.items()}, sort_keys=True)
    )
    outcomes: Final[dict[str, dict[str, int | str]]] = {}
    for target in targets["targets"]:
        corpus: Final = tmp_path / target
        corpus.mkdir()
        seed: Final = generated_seeds.get(
            target,
            SEED_HEADER
            + (
                b"p.x"
                if target == "css-translate"
                else b'consume("kept",external);'
                if target == "javascript"
                else "水😀".encode()
            ),
        )
        (corpus / "utf8").write_bytes(seed)
        counters: Final = tmp_path / f"{target}-gcda"
        run(  # ruff: ignore[subprocess-without-shell-equals-true] - public callback and private native counters.
            [sys.executable, "-c", _CONSUME, target, str(corpus / "utf8")],
            env={**environment, "GCOV_PREFIX": str(counters)},
            capture_output=True,
            check=True,
        )
        result = run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and finite public corpus.
            [
                sys.executable,
                "-m",
                "fuzz.atheris_targets",
                "--target",
                target,
                "--corpus",
                str(corpus),
                "-atheris_runs=8",
                "-seed=1",
                "-max_len=64",
                "-detect_leaks=0",
            ],
            env={**environment, "GCOV_PREFIX": str(tmp_path / f"{target}-gcda")},
            capture_output=True,
            check=False,
        )
        (tmp_path / f"{target}.log").write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (target, result.stdout + result.stderr)
        assert b"ATHERIS_REJECTION_BRIDGE=1" in result.stderr
        assert b"Done 8" in result.stderr
        assert b"inline 8-bit counters" in result.stderr
        assert b"PC tables" in result.stderr
        assert b"cov:" in result.stderr
        assert b"ft:" in result.stderr
        assert b"Coverage symbols are being provided by a library other than libFuzzer" not in result.stderr
        manifest: Final = json.loads(corpus.with_suffix(".json").read_text())
        assert (manifest["target"], bool(manifest["exports"])) == (
            target,
            target not in {"markdown-source", "markdown-html", "encoding-bytes"},
        )
        native_counters: Final = tuple(counters.rglob("*.gcda"))
        assert native_counters, target
        outcomes[target] = {
            "exit": result.returncode,
            "corpus": str(corpus),
            "native_counter_files": len(native_counters),
        }
    (tmp_path / "public-targets.json").write_text(json.dumps(outcomes, indent=2) + "\n", encoding="utf-8")


@pytest.mark.atheris_native
@pytest.mark.oracle
def test_atheris_failure_header_reaches_allocator_hook(tmp_path: Path) -> None:
    if sys.platform != "linux" or importlib.util.find_spec("atheris") is None:
        pytest.skip("Atheris's native runtime requires its optional Linux wheel")
    runtime: Final = cast("_Runtime", importlib.import_module("atheris"))
    library: Final = build_runtime(Path(runtime.path()) / "libclang_rt.fuzzer_no_main.a", tmp_path / "runtime")
    # the header fails the first PyMem allocation, and the callback must answer with MemoryError
    failing: Final = tmp_path / "failing"
    failing.write_bytes(Header(0, 1, 256).encode() + "水😀".encode())
    run(  # ruff: ignore[subprocess-without-shell-equals-true] - public callback and the real allocator hook.
        [sys.executable, "-c", _FAIL, "html-document", str(failing)],
        env={
            **os.environ,
            "LD_PRELOAD": str(library),
            "PYTHONPATH": str(Path(__file__).parents[1] / "tools"),
            "GCOV_PREFIX": str(tmp_path / "gcda"),
        },
        capture_output=True,
        check=True,
    )


_TARGET: Final = """
from __future__ import annotations

import ctypes
import os
from typing import Final

__all__ = ["consume"]
_MODE: Final = os.environ["ATHERIS_CONTROL_MODE"]
_VISITS: Final = [0]
_DUMP: Final = ctypes.CFUNCTYPE(None)(("turbohtml_fuzz_dump_coverage", ctypes.CDLL(None)))


def consume(data: bytes) -> None:
    _VISITS[0] += 1
    if _VISITS[0] == 63:
        _DUMP()
    if _MODE == "reset" and _VISITS[0] == 1:
        raise UnicodeError("documented initial control")
    if _MODE == "unexpected":
        raise RuntimeError("unexpected control")
    if data == b"BENIGN_A":
        if _MODE in {"reject", "mixed"}:
            raise UnicodeError("documented control")
    elif data == b"BENIGN_B" and _MODE == "reject":
        raise UnicodeError("documented control")
"""

_CHILD: Final = """
from __future__ import annotations

import sys
from pathlib import Path

from capability_target import consume
from fuzz.atheris_driver import fuzz
from fuzz.atheris_registry import Target


def mutate(data: bytes, max_size: int, seed: int) -> bytes:
    if sys.argv[2] == "reset":
        return b"BENIGN_B"[:max_size]
    return (b"BENIGN_A", b"BENIGN_B")[seed % 2][:max_size]


fuzz(
    [Target("benign", consume, ("capability_target.consume",), (UnicodeError,))],
    ["capability_target"],
    "benign",
    [
        sys.argv[0],
        sys.argv[1],
        sys.argv[3],
        "-atheris_runs=64",
        "-seed=1",
        "-max_len=32",
        "-artifact_prefix=" + sys.argv[1] + "/",
    ],
    custom_mutator=None if sys.argv[2] == "default" else mutate,
)
"""


@pytest.mark.atheris_native
@pytest.mark.oracle
def test_atheris_native_corpus_rejection(tmp_path: Path) -> None:
    if sys.platform != "linux" or importlib.util.find_spec("atheris") is None:
        pytest.skip("Atheris's released runtime requires Linux and its optional wheel")
    runtime = cast("_Runtime", importlib.import_module("atheris"))
    archive = Path(runtime.path()) / "libclang_rt.fuzzer_no_main.a"
    library = build_runtime(archive, tmp_path / "build", coverage=True)
    (tmp_path / "capability_target.py").write_text(_TARGET, encoding="utf-8")
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    environment = {
        **os.environ,
        "LD_PRELOAD": str(library),
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(Path(__file__).parents[1] / "tools"))),
    }
    # libFuzzer reads the seed directory but writes only new units, to the first directory
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "seed").write_bytes(SEED_HEADER + b"SEED")
    corpora: dict[str, list[str]] = {}
    for mode in ("accept", "reject", "mixed", "reset", "default", "unexpected"):
        corpus = tmp_path / f"{mode}-corpus"
        corpus.mkdir()
        result = run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and local control.
            [sys.executable, str(child), str(corpus), mode, str(seeds)],
            env={**environment, "ATHERIS_CONTROL_MODE": mode},
            capture_output=True,
            check=False,
        )
        (tmp_path / f"{mode}.log").write_bytes(result.stdout + result.stderr)
        assert b"ATHERIS_REJECTION_BRIDGE=1" in result.stderr
        assert b"Coverage symbols are being provided by a library other than libFuzzer" not in result.stderr
        if mode == "unexpected":
            assert result.returncode != 0
            assert b"RuntimeError: unexpected control" in result.stdout + result.stderr
        else:
            assert result.returncode == 0
            assert b"Done 64" in result.stderr
            if mode != "default":
                assert b"ATHERIS_CUSTOM_MUTATOR_BRIDGE=1" in result.stderr
                corpora[mode] = sorted({path.read_bytes()[HEADER_SIZE:].decode("ascii") for path in corpus.iterdir()})
    (tmp_path / "corpora.json").write_text(json.dumps(corpora), encoding="utf-8")
    assert corpora == {"accept": ["BENIGN_A", "BENIGN_B"], "reject": [], "mixed": ["BENIGN_B"], "reset": ["BENIGN_B"]}
