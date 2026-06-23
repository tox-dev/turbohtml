#!/usr/bin/env python3
"""
Benchmark turbohtml's escape/unescape/tokenize/parse against other libraries.

Run with ``tox -e bench``; positional arguments pick the suites to run
(``escape``, ``unescape``, ``tokenize``, ``corpus``, ``parse``, ``query``,
``xpath``, ``serialize``; default all). Remaining arguments are forwarded to
pyperf (pass ``--help`` to see them). pyperf runs every case in isolated worker
processes and reports mean and stddev; the parent process then prints a speedup
table: escape, unescape, and tokenize against the standard library (unescape also
against w3lib's replace_entities), parse against the other HTML tree builders
(lxml, selectolax, resiliparse, html5lib, and BeautifulSoup), the
read-path query and serialize suites against lxml, selectolax, and BeautifulSoup,
the xpath suite against lxml, the only other library with an XPath engine, and the
structured suite (JSON-LD / Microdata / OpenGraph extraction) against extruct.

The escape and unescape inputs span tiny strings (call overhead) and multi-MiB
documents that stream well past the CPU caches: real corpora (Project
Gutenberg's War and Peace from the tools/bench-data submodule and the WHATWG
HTML spec source) plus seeded pseudo-random UCS-2/UCS-4 text, since large real
wide-kind documents are scarce. tokenize and parse also run over html5lib's
benchmark corpus (the tools/html5lib-python submodule): a slice of the WHATWG
HTML spec source plus a size-weighted sample of web-platform-tests pages from
0.6 kB to 234 kB, and two multi-megabyte documents (the ECMAScript and WHATWG
HTML specifications), downloaded once from pinned revisions and cached because
their repositories are too large to vendor.
"""

from __future__ import annotations

import functools
import html
import io
import os
import random
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

import bleach
import html2text
import html5lib
import html_sanitizer
import inscriptis
import lxml_html_clean
import markdownify
import markupsafe
import minify_html
import nh3
import pyperf
import w3lib.html
from bs4 import BeautifulSoup
from inscriptis.model.config import ParserConfig
from linkify_it import LinkifyIt
from lxml import html as lxml_html
from parsel import Selector
from pyquery import PyQuery
from resiliparse.extract.html2text import (  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs
    extract_plain_text as resiliparse_extract_text,
)
from resiliparse.parse.html import HTMLTree  # ty: ignore[unresolved-import]  # Cython extension, ships no type stubs
from selectolax.lexbor import LexborHTMLParser

# html5-parser is built against the system libxml2 and raises at import when that differs from lxml's bundled copy;
# rebuild lxml with ``pip install --no-binary lxml lxml`` to race it. Optional so the suite still runs without it.
try:
    import html5_parser  # gumbo-backed WHATWG parser, returns an lxml tree, no type stubs
except (ImportError, RuntimeError):
    html5_parser = None  # ty: ignore[invalid-assignment]  # optional: re-bind the name when the import is unavailable

# extruct is the JSON-LD / Microdata / OpenGraph extractor turbohtml.structured_data() succeeds; it builds an lxml tree
# and runs one extractor per syntax. Optional so the structured suite still runs (turbohtml-only) without it.
try:
    import extruct  # structured-data extractor over lxml, no type stubs
except ImportError:
    extruct = None  # ty: ignore[invalid-assignment]  # optional: re-bind the name when the import is unavailable

# Terse HTML builders raced against turbohtml.build.E in the build suite; optional so the suite still runs without them.
try:
    import dominate.tags as dominate_tags  # declarative builder, no type stubs
except ImportError:
    dominate_tags = None  # ty: ignore[invalid-assignment]  # optional: re-bind the name when the import is unavailable

try:
    from yattag import Doc as YattagDoc  # context-manager builder, no type stubs
except ImportError:
    YattagDoc = None  # ty: ignore[invalid-assignment]  # optional: re-bind the name when the import is unavailable
# Article extractors raced in the article suite. trafilatura and readability-lxml install cleanly on current Python;
# newspaper3k pins long-unmaintained dependencies and rarely resolves, so each import is optional and the suite races
# whatever is present.
try:
    import trafilatura  # lxml-backed main-text and metadata extractor
except ImportError:
    trafilatura = None  # ty: ignore[invalid-assignment]  # optional: re-bind the name when the import is unavailable

try:
    from readability import Document as ReadabilityDocument  # readability-lxml, the Arc90 Readability port
except ImportError:
    ReadabilityDocument = None  # ty: ignore[invalid-assignment]  # optional: re-bind when readability-lxml is absent

try:
    from newspaper import Article as NewspaperArticle  # newspaper3k news scraper, no type stubs
except ImportError:
    NewspaperArticle = None  # ty: ignore[invalid-assignment]  # optional: re-bind when newspaper3k is absent

# pandas.read_html is the one helper the tables suite races; optional so the suite still runs without it.
try:
    import pandas as pd  # read_html parses every <table> on a page into DataFrames, over lxml/bs4/html5lib
except ImportError:
    pd = None  # ty: ignore[invalid-assignment]  # optional: re-bind the name when pandas is unavailable

# html-text (Zyte) pulls visible text off an lxml tree in Python; optional so the suite still runs without it.
try:
    import html_text  # lxml-based visible-text extractor, no type stubs
except ImportError:
    html_text = None  # ty: ignore[invalid-assignment]  # optional: re-bind the name when the import is unavailable

import turbohtml
from turbohtml import sanitizer as turbo_sanitizer
from turbohtml.build import E as TURBO_E
from turbohtml.linkify import Detector as TurboDetector
from turbohtml.linkify import linkify as turbo_linkify_html
from turbohtml.migration.markupsafe import Markup as TurboMarkup
from turbohtml.migration.markupsafe import escape as turbo_markup_escape
from turbohtml.migration.stdlib import HTMLParser as TurboHTMLParser
from turbohtml.query import Query as TurboQuery

_SANITIZER = turbo_sanitizer.Sanitizer(turbo_sanitizer.Policy.relaxed())
_LXML_CLEANER = lxml_html_clean.Cleaner()
_HTML_SANITIZER = html_sanitizer.Sanitizer()

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from lxml.html import HtmlElement

    from turbohtml import Document, Element

CORPUS_DIR = Path(__file__).parent / "html5lib-python" / "benchmarks" / "data"
CORPUS_FILES: list[tuple[str, str, str]] = [
    ("wpt tiny (0.6 kB)", "wpt/weighted/toBlob.png.html", "utf-8"),
    ("wpt small (4 kB)", "wpt/weighted/align-content-wrap-002.html", "utf-8"),
    ("wpt medium (9.6 kB)", "wpt/weighted/grid-auto-fill-rows-001.html", "utf-8"),
    ("wpt large (92 kB)", "wpt/weighted/test-plan.src.html", "utf-8"),
    ("wpt CJK (124 kB)", "wpt/weighted/big5_chars_extra.html", "big5"),
    ("whatwg spec (235 kB)", "html.html", "utf-8"),
]


def corpus_text(relative: str, encoding: str) -> str:
    """Return a corpus document from the html5lib-python submodule."""
    target = CORPUS_DIR / relative
    if not target.exists():
        msg = f"{target} is missing; run 'git submodule update --init tools/html5lib-python'"
        raise FileNotFoundError(msg)
    return target.read_text(encoding=encoding, errors="replace")


LARGE_DIR = Path(__file__).parent / ".bench_data"
LARGE_FILES: list[tuple[str, str, str]] = [
    (
        "ecmascript spec (3 MB)",
        "ecma262-spec.html",
        "https://raw.githubusercontent.com/tc39/ecma262/8c0c94eb3be152b7ae7dc0cb580f4ee9f0a9a0c2/spec.html",
    ),
    (
        "whatwg spec source (7.9 MB)",
        "whatwg-html-source.html",
        "https://raw.githubusercontent.com/whatwg/html/15ce0d167e4ba413ae2948ee1868d83c38c363f8/source",
    ),
]


def large_text(filename: str, url: str) -> str:
    """Return a multi-megabyte document, downloading and caching it on first use."""
    target = LARGE_DIR / filename
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response:  # noqa: S310  # pinned https URL
            target.write_bytes(response.read())
    return target.read_text(encoding="utf-8")


DATA_DIR = Path(__file__).resolve().parent / "bench-data"

ASCII_WORDS = (
    "the",
    "quick",
    "brown",
    "fox",
    "jumps",
    "over",
    "lazy",
    "dogs",
    "while",
    "zealous",
    "wizards",
    "vex",
    "sphinx",
    "judges",
    "of",
    "black",
    "quartz",
    "monoliths",
)
UCS2_WORDS = (*ASCII_WORDS, "résumé", "café", "naïve", "Москва", "über", "façade", "πλάτων", "Ω")
UCS4_WORDS = (*UCS2_WORDS, "😀", "🎉", "🚀", "🐍")
REFERENCES = ("&amp;", "&lt;", "&gt;", "&quot;", "&#x27;", "&copy;", "&mdash;", "&eacute;", "&#62;", "&#127881;")
NUMERIC_REFERENCES = ("&#62;", "&#x3e;", "&#38;", "&#127881;", "&#x1F600;")

TINY = 64
MEDIUM = 4 * 2**10
LARGE = 4 * 2**20
# unique generated prefix tiled up to LARGE: cache pressure comes from the address
# footprint of the full string, not from the content repeating every 64 KiB
CHUNK = 64 * 2**10


def corpus(relative: str, size: int) -> str:
    """
    Load a slice of a vendored real-world document.

    Decoded as latin-1 so the result is always a 1-byte unicode object exercising
    the byte path regardless of any UTF-8 multibyte sequences in the file (the
    bytes are preserved verbatim; mojibake is irrelevant to throughput).
    """
    path = DATA_DIR / relative
    if not path.exists():
        msg = f"{path} is missing; run: git submodule update --init --depth 1 tools/bench-data/war-and-peace"
        raise FileNotFoundError(msg)
    return path.read_bytes()[:size].decode("latin-1")


def prose_of(rng: random.Random, vocabulary: tuple[str, ...], size: int) -> str:
    """Build at least ``size`` characters of word soup with nothing to escape."""
    pieces: list[str] = []
    total = 0
    while total < size:
        word = rng.choice(vocabulary)
        pieces.append(word)
        total += len(word) + 1
    return " ".join(pieces)


def markup_of(rng: random.Random, vocabulary: tuple[str, ...], size: int) -> str:
    """Build at least ``size`` characters of HTML-looking text rich in characters escape() rewrites."""
    pieces: list[str] = []
    total = 0
    while total < size:
        tag = rng.choice(("p", "div", "span", "em", "li"))
        body = " ".join(rng.choice(vocabulary) for _ in range(rng.randint(4, 12)))
        piece = f'<{tag} class="{rng.choice(vocabulary)}">{body} & {rng.choice(vocabulary)}\'s</{tag}>\n'
        pieces.append(piece)
        total += len(piece)
    return "".join(pieces)


def references_of(
    rng: random.Random, vocabulary: tuple[str, ...], gap: int, size: int, references: tuple[str, ...] = REFERENCES
) -> str:
    """Build at least ``size`` characters of prose with a character reference after every ``gap`` words."""
    pieces: list[str] = []
    total = 0
    while total < size:
        for _ in range(gap):
            word = rng.choice(vocabulary)
            pieces.append(word)
            total += len(word) + 1
        reference = rng.choice(references)
        pieces.append(reference)
        total += len(reference) + 1
    return " ".join(pieces)


def tile(chunk: str, size: int) -> str:
    """Repeat ``chunk`` up to exactly ``size`` characters."""
    return (chunk * (size // len(chunk) + 1))[:size]


def build_cases() -> list[tuple[str, str, str]]:
    """Return the (operation, case name, input) escape/unescape matrix."""
    rng = random.Random(0)
    book_text = corpus("war-and-peace/2600.txt", LARGE)
    book_html = corpus("war-and-peace/2600-h/2600-h.htm", LARGE)
    # byte-slice and decode latin-1 like corpus() so the case stays a 1-byte string
    spec_html = large_text(*LARGE_FILES[1][1:]).encode()[:LARGE].decode("latin-1")
    return [
        ("escape", "tiny plain (64 B)", prose_of(rng, ASCII_WORDS, TINY)),
        ("escape", "medium markup (4 KiB)", markup_of(rng, ASCII_WORDS, MEDIUM)),
        ("escape", "no-op prose (4 MiB)", tile(prose_of(rng, ASCII_WORDS, CHUNK), LARGE)),
        ("escape", "book text (3 MiB)", book_text),
        ("escape", "book HTML (4 MiB)", book_html),
        ("escape", "spec HTML, dense (4 MiB)", spec_html),
        ("escape", "UCS-2 plain (4 MiB)", tile(prose_of(rng, UCS2_WORDS, CHUNK), LARGE)),
        ("escape", "UCS-2 markup (4 MiB)", tile(markup_of(rng, UCS2_WORDS, CHUNK), LARGE)),
        ("escape", "UCS-4 plain (4 MiB)", tile(prose_of(rng, UCS4_WORDS, CHUNK), LARGE)),
        ("escape", "UCS-4 markup (4 MiB)", tile(markup_of(rng, UCS4_WORDS, CHUNK), LARGE)),
        ("unescape", "tiny plain (64 B)", prose_of(rng, ASCII_WORDS, TINY)),
        ("unescape", "medium dense refs (4 KiB)", references_of(rng, ASCII_WORDS, 1, MEDIUM)),
        ("unescape", "numeric refs (4 KiB)", references_of(rng, ASCII_WORDS, 1, MEDIUM, NUMERIC_REFERENCES)),
        ("unescape", "book HTML, real refs (4 MiB)", book_html),
        ("unescape", "escaped book HTML (5 MiB)", html.escape(book_html)),
        ("unescape", "dense refs (4 MiB)", tile(references_of(rng, ASCII_WORDS, 1, CHUNK), LARGE)),
        ("unescape", "UCS-2 refs (4 MiB)", tile(references_of(rng, UCS2_WORDS, 10, CHUNK), LARGE)),
    ]


CASES: list[tuple[str, str, str]] = build_cases()

TOKENIZE_CASES: list[tuple[str, str]] = [
    (
        "typical markup",
        '<div class="row"><p>Tom &amp; Jerry said "hi" to <b>O\'Brien</b>!</p><br/></div>\n' * 60,
    ),
    (
        "text-heavy prose",
        "<p>" + "the quick brown fox jumps over the lazy dog " * 100 + "</p>",
    ),
    (
        "attribute-heavy",
        '<a href="https://example.com/path?q=1" title="example" rel="noopener" target="_blank" data-x=y>link</a>\n'
        * 60,
    ),
    (
        "script-heavy",
        "<script>function f(a, b) { return a < b && b > a; }</script>\n" * 60,
    ),
    (
        "entity-heavy",
        "<p>caf&eacute; &amp; r&eacute;sum&eacute; &#127881; &lt;tag&gt;</p>\n" * 60,
    ),
]


def turbo_tokenize(text: str) -> None:
    """Consume the token stream so lazy Token construction is included."""
    for _ in turbohtml.tokenize(text):
        pass


def turbo_parse(text: str) -> None:
    """Parse a whole document into a navigable tree through the public parse() entry point."""
    turbohtml.parse(text)


def lexbor_parse(text: str) -> None:
    """Parse with lexbor through selectolax (C, WHATWG-conformant)."""
    LexborHTMLParser(text.encode())  # lexbor's native input is UTF-8 bytes


def lxml_parse(text: str) -> None:
    """Parse with lxml's libxml2-backed HTML parser."""
    lxml_html.document_fromstring(text)


def resiliparse_parse(text: str) -> None:
    """Parse with resiliparse, which wraps the same lexbor engine selectolax does."""
    HTMLTree.parse(text)


def html5lib_parse(text: str) -> None:
    """Parse with html5lib, the pure-Python WHATWG reference implementation."""
    html5lib.parse(text)


def soup_parse(text: str) -> None:
    """Parse with BeautifulSoup over its stdlib html.parser backend."""
    BeautifulSoup(text, "html.parser")


def html5_parser_parse(text: str) -> None:
    """Parse with html5-parser, which wraps the gumbo C parser and returns an lxml tree."""
    html5_parser.parse(text)


# Whole-document tree builders raced against turbohtml.parse() in the parse suite, ordered fastest to slowest.
# Each label names the pip-installable package so the comparison stays like-for-like.
PARSE_COMPETITORS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("lxml", lxml_parse),
    ("selectolax", lexbor_parse),
    ("resiliparse", resiliparse_parse),
    *((("html5-parser", html5_parser_parse),) if html5_parser is not None else ()),
    ("BeautifulSoup", soup_parse),
    ("html5lib", html5lib_parse),
)


# --- read-path suites: query and serialize a pre-parsed tree --------------- #
# Each library parses the document once (outside the timed region) into its own
# tree, then the timed function runs one query or one serialization. The trees
# differ per library, so the comparison is the user-level operation ("find every
# anchor", "select a[href]", "serialize to HTML"), each library's idiomatic way.

CSS_SELECTOR = "div a[href]"  # a descendant combinator with an attribute test, common in scrapers


def turbo_tree(text: str) -> Document:
    """Parse with turbohtml into a navigable Document."""
    return turbohtml.parse(text)


def bs4_tree(text: str) -> BeautifulSoup:
    """Parse with BeautifulSoup over its stdlib html.parser backend."""
    return BeautifulSoup(text, "html.parser")


def lxml_tree(text: str) -> HtmlElement:
    """Parse with lxml's libxml2-backed HTML parser."""
    return lxml_html.document_fromstring(text)


def lexbor_tree(text: str) -> LexborHTMLParser:
    """Parse with lexbor through selectolax."""
    return LexborHTMLParser(text.encode())


def turbo_find(doc: Document) -> None:
    """Collect every anchor with turbohtml's find_all."""
    doc.find_all("a")


def bs4_find(soup: BeautifulSoup) -> None:
    """Collect every anchor with BeautifulSoup's find_all."""
    soup.find_all("a")


def lxml_find(tree: HtmlElement) -> None:
    """Collect every anchor with lxml's XPath findall."""
    tree.findall(".//a")


def lexbor_find(tree: LexborHTMLParser) -> None:
    """Collect every anchor with selectolax's css."""
    tree.css("a")


# A text-content search: turbohtml matches elements by their collected text, the search bs4
# spells find_all(string=...) over text nodes. A regex predicate runs Python mid-walk, so the
# C side snapshots the candidates and their text under the lock first. lxml/selectolax/parsel
# expose no equivalent text search, so only turbohtml and BeautifulSoup race here.
FIND_TEXT_PATTERN = re.compile(r"test")  # ubiquitous in the wpt corpus, so the predicate does real work


def turbo_find_text(doc: Document) -> None:
    """Collect every element whose collected text matches the regex with turbohtml's find_all."""
    doc.find_all(text=FIND_TEXT_PATTERN)


def bs4_find_text(soup: BeautifulSoup) -> None:
    """Collect every matching string with BeautifulSoup's find_all(string=...)."""
    soup.find_all(string=FIND_TEXT_PATTERN)


# A whole-document text extraction: turbohtml's text property concatenates every descendant
# Text node, lxml's text_content() and selectolax's text() do the same off their trees, and
# BeautifulSoup spells it get_text(). parsel exposes no node-level text collector (only a
# ::text selector list), so it sits this one out.
def turbo_get_text(doc: Document) -> None:
    """Collect the document's visible text with turbohtml's text property."""
    _ = doc.text


def lxml_text_content(tree: HtmlElement) -> None:
    """Collect the document's visible text with lxml's text_content()."""
    tree.text_content()


def lexbor_text(tree: LexborHTMLParser) -> None:
    """Collect the document's visible text with selectolax's text() method."""
    if (node := tree.body or tree.root) is not None:
        node.text(deep=True)


def bs4_get_text(soup: BeautifulSoup) -> None:
    """Collect the document's visible text with BeautifulSoup's get_text()."""
    soup.get_text()


def turbo_select(doc: Document) -> None:
    """Run the CSS selector with turbohtml's select."""
    doc.select(CSS_SELECTOR)


def bs4_select(soup: BeautifulSoup) -> None:
    """Run the CSS selector with BeautifulSoup's soupsieve select."""
    soup.select(CSS_SELECTOR)


def lxml_select(tree: HtmlElement) -> None:
    """Run the CSS selector with lxml's cssselect."""
    tree.cssselect(CSS_SELECTOR)


def lexbor_select(tree: LexborHTMLParser) -> None:
    """Run the CSS selector with selectolax's css."""
    tree.css(CSS_SELECTOR)


def parsel_tree(text: str) -> Selector:
    """Parse with parsel (Scrapy's selector library), which builds an lxml tree."""
    return Selector(text=text)


def parsel_find(sel: Selector) -> None:
    """Collect every anchor with parsel's css."""
    sel.css("a")


def parsel_select(sel: Selector) -> None:
    """Run the CSS selector with parsel's css (cssselect translates it to XPath on libxml2)."""
    sel.css(CSS_SELECTOR)


HAS_SELECTOR = "div:has(a)"  # the :has() relational pseudo-class: a div that contains a link


def turbo_has_select(doc: Document) -> None:
    """Run the :has() relational selector with turbohtml's select."""
    doc.select(HAS_SELECTOR)


def bs4_has_select(soup: BeautifulSoup) -> None:
    """Run the :has() relational selector with BeautifulSoup's soupsieve."""
    soup.select(HAS_SELECTOR)


def lxml_has_select(tree: HtmlElement) -> None:
    """Run the :has() relational selector with lxml's cssselect."""
    tree.cssselect(HAS_SELECTOR)


def lexbor_has_select(tree: LexborHTMLParser) -> None:
    """Run the :has() relational selector with selectolax's css."""
    tree.css(HAS_SELECTOR)


def turbo_serialize(doc: Document) -> None:
    """Serialize back to HTML with turbohtml's html property."""
    _ = doc.html


def bs4_serialize(soup: BeautifulSoup) -> None:
    """Serialize back to HTML with BeautifulSoup's decode."""
    soup.decode()


def lxml_serialize(tree: HtmlElement) -> None:
    """Serialize back to HTML with lxml's tostring."""
    lxml_html.tostring(tree)


def lexbor_serialize(tree: LexborHTMLParser) -> None:
    """Serialize back to HTML with selectolax's html property."""
    _ = tree.html


# --- write-path suite: build and serialize a tree from scratch ------------- #
# Each function constructs a <ul> of count <li> rows (a class, a data attribute,
# and a text child apiece) and serializes it, the construction work an editor or
# template engine does. selectolax/lexbor is parse-only, so it has no row here.


def turbo_build(count: int) -> None:
    """Build a list with turbohtml's constructors, attribute mapping, and text setter."""
    ul = turbohtml.Element("ul")
    for index in range(count):
        li = turbohtml.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    _ = ul.html


def bs4_build(count: int) -> None:
    """Build the same list with BeautifulSoup's new_tag and string assignment."""
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    _ = ul.decode()


def lxml_build(count: int) -> None:
    """Build the same list with lxml's Element factory and .text."""
    ul = lxml_html.Element("ul")
    for index in range(count):
        li = lxml_html.Element("li", {"class": "item", "data-i": str(index)})
        li.text = f"item {index}"
        ul.append(li)
    _ = lxml_html.tostring(ul)


# Write-path competitors; selectolax is parse-only and so absent.
BUILD_LIBS: tuple[tuple[str, Callable[[int], None]], ...] = (
    ("turbohtml", turbo_build),
    ("lxml", lxml_build),
    ("BeautifulSoup", bs4_build),
)

BUILD_CASES: tuple[tuple[str, int], ...] = (("100 rows", 100), ("1k rows", 1_000), ("10k rows", 10_000))


# --- terse-builder sub-suite: the same fragment through declarative builder DSLs --- #
# turbohtml.build.E against the dedicated HTML generators (dominate, yattag), each
# spelling the same <ul> of count <li> rows in its own nesting syntax.


def turbo_e_build(count: int) -> None:
    """Build the row list with turbohtml's terse E builder: a leading mapping is attributes, strings become text."""
    rows = [TURBO_E.li({"class": "item", "data-i": str(index)}, f"item {index}") for index in range(count)]
    _ = TURBO_E.ul(*rows).serialize()


def dominate_build(count: int) -> None:
    """Build the same list with dominate's tag objects and ``add``."""
    ul = dominate_tags.ul()
    for index in range(count):
        ul.add(dominate_tags.li(f"item {index}", **{"class": "item", "data-i": str(index)}))
    _ = ul.render(pretty=False)


def yattag_build(count: int) -> None:
    """Build the same list with yattag's ``tag``/``text`` context managers."""
    doc, tag, text = YattagDoc().tagtext()
    with tag("ul"):
        for index in range(count):
            with tag("li", ("class", "item"), ("data-i", str(index))):
                text(f"item {index}")
    _ = doc.getvalue()


# Terse builders, fastest-first; dominate and yattag are optional and drop out when absent.
BUILDER_LIBS: tuple[tuple[str, Callable[[int], None]], ...] = (
    ("turbohtml", turbo_e_build),
    *((("dominate", dominate_build),) if dominate_tags is not None else ()),
    *((("yattag", yattag_build),) if YattagDoc is not None else ()),
)

BUILDER_CASES: tuple[tuple[str, int], ...] = (("100 rows", 100), ("1k rows", 1_000))


def run_build_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark programmatic tree construction across every library; return the case names."""
    for name, count in BUILD_CASES:
        for label, build in BUILD_LIBS:
            bench(f"build {name} [{label}]", build, count)
    for name, count in BUILDER_CASES:
        for label, build in BUILDER_LIBS:
            bench(f"build via E {name} [{label}]", build, count)
    return [name for name, _ in BUILD_CASES]


def print_build_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml beside each alternative and its slowdown factor for tree construction."""
    if not cases:
        return
    others = [label for label, _ in BUILD_LIBS if label != "turbohtml"]
    print()
    header = f"{'build benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"build {name} [turbohtml]"]
        row = f"{'build ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"build {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)
    builders = [label for label, _ in BUILDER_LIBS if label != "turbohtml"]
    print()
    builder_header = f"{'terse builder (E)':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in builders)
    print(builder_header)
    for name, _count in BUILDER_CASES:
        if (turbo := means.get(f"build via E {name} [turbohtml]")) is None:
            continue
        row = f"{'E ' + name:28} {turbo * 1e6:8.1f} us"
        for label in builders:
            other = means.get(f"build via E {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# Read-path competitors, fastest-first: a tree builder plus the find/select/serialize/:has/find-text/text ops.
# turbohtml leads each. A None op means the library does not offer it (parsel has no serializer, its
# cssselect cannot compile :has(), and it has no node-level text collector; only turbohtml and bs4 search
# by text content).
READPATH_LIBS: tuple[tuple[str, Callable[[str], object], tuple[Callable[..., None] | None, ...]], ...] = (
    (
        "turbohtml",
        turbo_tree,
        (turbo_find, turbo_select, turbo_serialize, turbo_has_select, turbo_find_text, turbo_get_text),
    ),
    ("lxml", lxml_tree, (lxml_find, lxml_select, lxml_serialize, lxml_has_select, None, lxml_text_content)),
    ("selectolax", lexbor_tree, (lexbor_find, lexbor_select, lexbor_serialize, lexbor_has_select, None, lexbor_text)),
    ("BeautifulSoup", bs4_tree, (bs4_find, bs4_select, bs4_serialize, bs4_has_select, bs4_find_text, bs4_get_text)),
    ("parsel", parsel_tree, (parsel_find, parsel_select, None, None, None, None)),
)

# wpt pages from 4 kB to 92 kB; the multi-MB specs are skipped here since every
# library would re-parse them per worker, which dwarfs the timed query.
READPATH_CASES = CORPUS_FILES[1:4]


# --- edit suite: rewrite attributes across a pre-parsed tree --------------- #
# Each library parses once (outside the timed region), then the timed function
# tags every <a> with rel=nofollow, a classic link-rewriting pass. Setting the
# same value is idempotent, so pyperf's repeated calls do equal work each time.


def turbo_edit(doc: Document) -> None:
    """Tag every link with turbohtml's live attribute mapping."""
    for anchor in doc.find_all("a"):
        anchor.attrs["rel"] = "nofollow"


def bs4_edit(soup: BeautifulSoup) -> None:
    """Tag every link with BeautifulSoup's item assignment."""
    for anchor in soup.find_all("a"):
        anchor["rel"] = "nofollow"


def lxml_edit(tree: HtmlElement) -> None:
    """Tag every link with lxml's Element.set."""
    for anchor in tree.findall(".//a"):
        anchor.set("rel", "nofollow")


# A content-setter pass: replace the <body>'s children by reparsing a fixed fragment in
# context, the work pyquery's .html(markup) does. Reparsing the same fragment each call
# keeps pyperf's repeated runs equal, and the body always exists after a document parse.
SET_HTML_FRAGMENT = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
SET_TEXT_VALUE = "Replacement text, escaped & verbatim."


def turbo_set_html(doc: Document) -> None:
    """Reparse a fragment in the body's context and replace its children."""
    doc.find_all("body")[0].set_inner_html(SET_HTML_FRAGMENT)


def lxml_set_html(tree: HtmlElement) -> None:
    """Clear the body and append a reparsed fragment, lxml's nearest inner-HTML shape."""
    body = tree.findall(".//body")[0]
    body.clear()
    for fragment in lxml_html.fragments_fromstring(SET_HTML_FRAGMENT):
        body.append(fragment)


def bs4_set_html(soup: BeautifulSoup) -> None:
    """Clear the body and append a reparsed fragment, BeautifulSoup's inner-HTML shape."""
    body = soup.find_all("body")[0]
    body.clear()
    for node in list(BeautifulSoup(SET_HTML_FRAGMENT, "html.parser").children):
        body.append(node)


def pyquery_set_html(page: PyQuery) -> None:
    """Replace the body's children with pyquery's .html(markup)."""
    page("body").html(SET_HTML_FRAGMENT)


def turbo_set_text(doc: Document) -> None:
    """Replace the body's children with one verbatim text node."""
    doc.find_all("body")[0].set_text(SET_TEXT_VALUE)


def pyquery_set_text(page: PyQuery) -> None:
    """Replace the body's children with pyquery's .text(value)."""
    page("body").text(SET_TEXT_VALUE)


# Write-on-parsed-tree competitors; selectolax mutation is limited, so it is absent.
EDIT_LIBS: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...] = (
    ("turbohtml", turbo_tree, turbo_edit),
    ("lxml", lxml_tree, lxml_edit),
    ("BeautifulSoup", bs4_tree, bs4_edit),
)

# A second edit pass: a classList churn (add then remove the same token on every
# link), the work a CSS-state toggle does. add then remove is a net no-op, so
# pyperf's repeated calls each do equal work. Raced against lxml's classes set, the
# only other library with a dedicated class-token mutator; run on the largest
# read-path page only. BeautifulSoup has no such helper, so it keeps no entry.
CLASS_EDIT_CASE = "class add/remove (92 kB)"


def turbo_classes_edit(doc: Document) -> None:
    """Add then drop a class token on every link with turbohtml's classList mutators."""
    for anchor in doc.find_all("a"):
        anchor.add_class("seen").remove_class("seen")


def lxml_classes_edit(tree: HtmlElement) -> None:
    """Add then drop a class token on every link with lxml's classes set."""
    for anchor in tree.findall(".//a"):
        anchor.classes.add("seen")
        anchor.classes.discard("seen")


CLASS_EDIT_LIBS: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...] = (
    ("turbohtml", turbo_tree, turbo_classes_edit),
    ("lxml", lxml_tree, lxml_classes_edit),
)

# A bulk tag edit: drop a set of tags with their subtrees, or unwrap them and keep
# their content. Both rewrites are destructive, so a pre-parsed tree could not be
# re-edited each iteration; the timed call parses the page afresh, the string-to-result
# transform these helpers exist to perform. The same code/a/q set is dropped or unwrapped
# on the largest read-path page, raced against each library's bulk tag helper: selectolax's
# strip_tags drops the matches with their content (turbohtml's remove), w3lib's regex
# remove_tags keeps the content (turbohtml's strip_tags), and pyquery offers both .remove()
# and an lxml drop_tag unwrap. selectolax/w3lib/pyquery are already hard imports of this module.
STRIP_CASE = "bulk strip/remove (92 kB)"
STRIP_SELECTOR = "code, a, q"
STRIP_TAGS = ("code", "a", "q")


def turbo_remove(text: str) -> None:
    """Parse, then drop every code/a/q subtree with turbohtml's bulk remove and serialize."""
    _ = turbohtml.parse(text).remove(STRIP_SELECTOR).html


def turbo_strip(text: str) -> None:
    """Parse, then unwrap every code/a/q element with turbohtml's strip_tags and serialize."""
    _ = turbohtml.parse(text).strip_tags(STRIP_SELECTOR).html


def lexbor_strip(text: str) -> None:
    """Drop the same tags with their content using selectolax's strip_tags, then serialize."""
    tree = LexborHTMLParser(text)
    tree.strip_tags(list(STRIP_TAGS))
    _ = tree.html


def w3lib_remove(text: str) -> None:
    """Strip the same tags but keep their text with w3lib's regex remove_tags."""
    _ = w3lib.html.remove_tags(text, which_ones=STRIP_TAGS)


def pyquery_remove(text: str) -> None:
    """Drop the same subtrees with pyquery's .remove(), then serialize."""
    page = PyQuery(text)
    page(STRIP_SELECTOR).remove()
    _ = str(page)


def pyquery_unwrap(text: str) -> None:
    """Unwrap the same tags keeping their content with lxml's drop_tag under pyquery, then serialize."""
    page = PyQuery(text)
    for element in page(STRIP_SELECTOR):
        element.drop_tag()
    _ = str(page)


# (label, edit) pairs for the bulk strip/remove race; the label names the library method so
# the printed table can pair each turbohtml method with the helper of matching semantics.
STRIP_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml remove", turbo_remove),
    ("turbohtml strip_tags", turbo_strip),
    ("selectolax strip_tags", lexbor_strip),
    ("w3lib remove_tags", w3lib_remove),
    ("pyquery remove", pyquery_remove),
    ("pyquery unwrap", pyquery_unwrap),
)


def run_edit_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark a link-rewriting edit across every library; return the case names."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, build, edit in EDIT_LIBS:
            bench(f"edit {name} [{label}]", edit, build(text))
        names.append(name)
    _, path, enc = READPATH_CASES[-1]
    text = corpus_text(path, enc)
    for label, build, edit in CLASS_EDIT_LIBS:
        bench(f"edit {CLASS_EDIT_CASE} [{label}]", edit, build(text))
    names.append(CLASS_EDIT_CASE)
    for strip_label, strip_edit in STRIP_LIBS:
        bench(f"{STRIP_CASE} [{strip_label}]", strip_edit, text)
    # Content setters on one representative page: set_inner_html across the editing-table
    # libraries, plus pyquery's .html()/.text() for the migration guide's comparison.
    set_label, set_path, set_enc = READPATH_CASES[1]
    set_name = f"set inner html {set_label}"
    text = corpus_text(set_path, set_enc)
    bench(f"edit {set_name} [turbohtml]", turbo_set_html, turbo_tree(text))
    bench(f"edit {set_name} [lxml]", lxml_set_html, lxml_tree(text))
    bench(f"edit {set_name} [BeautifulSoup]", bs4_set_html, bs4_tree(text))
    bench(f"edit {set_name} [pyquery]", pyquery_set_html, pyquery_tree(text))
    bench(f"edit set text {set_label} [turbohtml]", turbo_set_text, turbo_tree(text))
    bench(f"edit set text {set_label} [pyquery]", pyquery_set_text, pyquery_tree(text))
    names.append(set_name)
    return names


def print_edit_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml beside each alternative and its slowdown factor for editing a parsed tree."""
    if not cases:
        return
    others = [label for label, _, _ in EDIT_LIBS if label != "turbohtml"]
    print()
    header = f"{'edit benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"edit {name} [turbohtml]"]
        row = f"{'edit ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"edit {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)
    print_strip_table(means)


# turbohtml method paired with the library helper of matching semantics: a drop-the-subtree
# remove and a keep-the-content strip_tags, each beside the competitor that does the same.
STRIP_PAIRS: tuple[tuple[str, str], ...] = (
    ("turbohtml remove", "selectolax strip_tags"),
    ("turbohtml strip_tags", "w3lib remove_tags"),
    ("turbohtml remove", "pyquery remove"),
    ("turbohtml strip_tags", "pyquery unwrap"),
)


def print_strip_table(means: dict[str, float]) -> None:
    """Render the bulk strip/remove race, pairing each turbohtml method with the matching library helper."""
    if f"{STRIP_CASE} [turbohtml remove]" not in means:
        return
    print()
    print(STRIP_CASE)
    print(f"{'turbohtml method':22} {'turbohtml':>11} {'library helper':>22} {'library':>11} {'slowdown':>9}")
    for turbo_label, other_label in STRIP_PAIRS:
        turbo = means[f"{STRIP_CASE} [{turbo_label}]"]
        other = means[f"{STRIP_CASE} [{other_label}]"]
        method = turbo_label.removeprefix("turbohtml ")
        print(f"{method:22} {turbo * 1e6:8.1f} us {other_label:>22} {other * 1e6:8.1f} us {other / turbo:8.1f}x")


# --- navigate suite: walk every descendant of a pre-parsed tree ------------- #
# A full-tree descendant walk, the traversal the lxml ``iterdescendants()`` and
# BeautifulSoup ``descendants`` migrations port to. Each library parses once outside
# the timed region, then walks its own tree its own way (turbohtml's
# :attr:`Node.descendants`, lxml's ``iterdescendants``, BeautifulSoup's
# ``descendants``); selectolax exposes no document-wide descendant iterator, so the
# comparison stays turbohtml/lxml/BeautifulSoup like the build/edit suites.


def turbo_navigate(doc: Document) -> None:
    """Walk every descendant node with turbohtml's descendants iterator."""
    for _node in doc.descendants:
        pass


def lxml_navigate(tree: HtmlElement) -> None:
    """Walk every descendant element with lxml's iterdescendants iterator."""
    for _element in tree.iterdescendants():
        pass


def bs4_navigate(soup: BeautifulSoup) -> None:
    """Walk every descendant with BeautifulSoup's descendants iterator."""
    for _node in soup.descendants:
        pass


NAVIGATE_LIBS: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...] = (
    ("turbohtml", turbo_tree, turbo_navigate),
    ("lxml", lxml_tree, lxml_navigate),
    ("BeautifulSoup", bs4_tree, bs4_navigate),
)


def _run_parsed_op_suite(
    bench: Callable[[str, object, object], None],
    op: str,
    libs: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...],
) -> list[str]:
    """Benchmark one operation over a pre-parsed tree across every library; return the case names."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, build, run in libs:
            bench(f"{op} {name} [{label}]", run, build(text))
        names.append(name)
    return names


def _print_parsed_op_table(
    means: dict[str, float],
    op: str,
    libs: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...],
    cases: list[str],
) -> None:
    """Render turbohtml beside each alternative and its slowdown factor for one parsed-tree operation."""
    if not cases:
        return
    others = [label for label, _, _ in libs if label != "turbohtml"]
    print()
    header = f"{op + ' benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"{op} {name} [turbohtml]"]
        row = f"{op + ' ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"{op} {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


def run_navigate_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark a full-tree descendant walk across turbohtml, lxml, and BeautifulSoup; return the case names."""
    return _run_parsed_op_suite(bench, "navigate", NAVIGATE_LIBS)


def print_navigate_table(means: dict[str, float], cases: list[str]) -> None:
    """Render the descendant-walk race for turbohtml beside lxml and BeautifulSoup."""
    _print_parsed_op_table(means, "navigate", NAVIGATE_LIBS, cases)


# --- chain suite: a pyquery-style fluent chain over a pre-parsed tree ------- #
# Each library parses once (outside the timed region), then the timed function
# runs one fluent chain: select every anchor, keep the linked ones, take the
# first, tag it, and read its href -- turbohtml.query.Query against pyquery, whose
# wrapper delegates to lxml. add_class is idempotent, so each pyperf call is equal.


def pyquery_tree(text: str) -> PyQuery:
    """Parse with pyquery (an lxml tree under a jQuery-style wrapper)."""
    return PyQuery(text)


def turbo_chain(doc: Document) -> None:
    """Run the fluent chain with turbohtml's Query wrapper."""
    TurboQuery(doc)("a").filter("[href]").eq(0).add_class("seen").attr("href")


def pyquery_chain(page: PyQuery) -> None:
    """Run the same fluent chain with pyquery."""
    page("a").filter("[href]").eq(0).add_class("seen").attr("href")


# Fluent-chain competitors; only turbohtml and pyquery offer jQuery-style chaining.
CHAIN_LIBS: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...] = (
    ("turbohtml", turbo_tree, turbo_chain),
    ("pyquery", pyquery_tree, pyquery_chain),
)


def run_chain_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark a pyquery-style fluent chain across every library; return the case names."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, build, chain in CHAIN_LIBS:
            bench(f"chain {name} [{label}]", chain, build(text))
        names.append(name)
    return names


def print_chain_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml beside pyquery and its slowdown factor for a fluent chain."""
    if not cases:
        return
    others = [label for label, _, _ in CHAIN_LIBS if label != "turbohtml"]
    print()
    header = f"{'chain benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"chain {name} [turbohtml]"]
        row = f"{'chain ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"chain {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# --- htmlparser suite: callback-driven parsing, the html.parser model ------- #
# turbohtml's html.parser adapter against the standard library's HTMLParser, both
# subclassed with the same minimal handler, so the comparison is the parser and
# dispatch cost for an identical callback API rather than the handler body.


class _TurboCounter(TurboHTMLParser):
    """A turbohtml adapter subclass whose handler does minimal, identical work."""

    def __init__(self) -> None:
        """Start the running tally."""
        super().__init__()
        self.work = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Tally a start tag and its attributes."""
        self.work += len(tag) + len(attrs)


class _StdlibCounter(HTMLParser):
    """A stdlib HTMLParser subclass doing the same minimal work."""

    def __init__(self) -> None:
        """Start the running tally."""
        super().__init__()
        self.work = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Tally a start tag and its attributes."""
        self.work += len(tag) + len(attrs)


def turbo_htmlparser(text: str) -> None:
    """Drive turbohtml's html.parser adapter with the counting handler."""
    parser = _TurboCounter()
    parser.feed(text)
    parser.close()


def stdlib_htmlparser(text: str) -> None:
    """Drive the standard library's HTMLParser with the same counting handler."""
    parser = _StdlibCounter()
    parser.feed(text)
    parser.close()


# Callback-parser competitors; only turbohtml and the standard library offer the
# subclass-and-override html.parser programming model.
HTMLPARSER_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_htmlparser),
    ("html.parser", stdlib_htmlparser),
)


def run_htmlparser_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark callback-driven parsing across every library; return the case names."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, parse_fn in HTMLPARSER_LIBS:
            bench(f"htmlparser {name} [{label}]", parse_fn, text)
        names.append(name)
    return names


def print_htmlparser_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's adapter beside the standard library and its slowdown factor."""
    if not cases:
        return
    others = [label for label, _ in HTMLPARSER_LIBS if label != "turbohtml"]
    print()
    header = f"{'htmlparser benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"htmlparser {name} [turbohtml]"]
        row = f"{'htmlparser ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"htmlparser {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# --- stream suite: chunked incremental parse vs the whole-document parse ----- #
# IncrementalParser feeds each document in fixed-size chunks and finishes with
# close(); the baseline is turbohtml.parse() over the whole string. The push
# parser does the same WHATWG tree construction plus the per-chunk feed
# bookkeeping, so this measures the cost of streaming a document rather than
# holding it whole.

STREAM_CHUNK = 16 * 1024  # a typical socket or file read size


def turbo_stream(text: str) -> None:
    """Parse a document fed in fixed-size chunks through IncrementalParser."""
    parser = turbohtml.IncrementalParser()
    for start in range(0, len(text), STREAM_CHUNK):
        parser.feed(text[start : start + STREAM_CHUNK])
    parser.close()


STREAM_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("IncrementalParser", turbo_stream),
    ("parse", turbo_parse),
)


def run_stream_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark chunked incremental parsing against the whole-document parse; return the case names."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, parse_fn in STREAM_LIBS:
            bench(f"stream {name} [{label}]", parse_fn, text)
        names.append(name)
    return names


def print_stream_table(means: dict[str, float], cases: list[str]) -> None:
    """Render the chunked parser beside the whole-document parse and its slowdown factor."""
    if not cases:
        return
    print()
    print(f"{'stream benchmark':28} {'IncrementalParser':>18} {'parse':>18}")
    for name in cases:
        stream = means[f"stream {name} [IncrementalParser]"]
        whole = means[f"stream {name} [parse]"]
        print(f"{'stream ' + name:28} {stream * 1e6:13.1f} us {whole * 1e6:13.1f} us {stream / whole:4.1f}x")


# --- markup suite: markupsafe-compatible escape on autoescape-realistic input #
# turbohtml.migration.markupsafe.escape against markupsafe's own C escape. The inputs are the
# small, mostly-clean strings a template engine interpolates under autoescape
# (markupsafe's hottest path), plus an escape-heavy fragment. Both return Markup,
# so the comparison includes the safe-string construction each pays per call.


def turbo_markup(text: str) -> None:
    """Escape with turbohtml.migration.markupsafe.escape, returning a Markup."""
    turbo_markup_escape(text)


def markupsafe_escape(text: str) -> None:
    """Escape with markupsafe's C-accelerated escape, returning a Markup."""
    markupsafe.escape(text)


MARKUP_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_markup),
    ("markupsafe", markupsafe_escape),
)

MARKUP_CASES: tuple[tuple[str, str], ...] = (
    ("clean 8 B", "a value!"),
    ("clean 32 B", "The quick brown fox jumped ok"),
    ("clean 256 B", "The quick brown fox jumps over the lazy dog. " * 6),
    ("name", "O'Brien & Sons"),
    ("escape-heavy", '<a href="/x?a=1&b=2">click & go</a>' * 2),
)


# The Markup operations beyond escape: striptags and unescape run on turbohtml's tokenizer and HTML5 reference
# resolution where markupsafe scans with a regex, and format/join escape their untrusted operands. Each races
# turbohtml.migration.markupsafe against markupsafe's own Markup of the same method, so the comparison is the
# per-call operation a template engine triggers, not just the escape primitive already in the escape table above.
# Every Markup wrapper is built once, outside the timed region, so the benchmarks measure the operation rather
# than construction; markupsafe.Markup() takes a string literal directly, which the untrusted-input lint accepts,
# and turbohtml's Markup wraps the same text.
_MARKUP_FORMAT_ARGS = ("<script>alert(1)</script>", "Tom & Jerry")
_MARKUP_JOIN_PARTS = ("<a href='/x'>link</a>", "Tom & Jerry", "<b>bold</b>", "plain text")
_MS_MARKUP_OPS = markupsafe.Markup(
    "<p>Hello <b>bold</b> &amp; <i>italic</i>, see <a href='/x'>caf&eacute;</a> &#127881;</p>"
)
_TURBO_MARKUP_OPS = TurboMarkup(_MS_MARKUP_OPS)
_TURBO_MARKUP_TEMPLATE = TurboMarkup("<li>{}</li><span>{}</span>")
_MS_MARKUP_TEMPLATE = markupsafe.Markup("<li>{}</li><span>{}</span>")
_TURBO_MARKUP_JOINER = TurboMarkup(", ")
_MS_MARKUP_JOINER = markupsafe.Markup(", ")


def turbo_markup_striptags(markup: TurboMarkup) -> None:
    """Strip tags to plain text with turbohtml's tokenizer-backed striptags."""
    markup.striptags()


def markupsafe_striptags(markup: markupsafe.Markup) -> None:
    """Strip tags to plain text with markupsafe's regex-based striptags."""
    markup.striptags()


def turbo_markup_unescape(markup: TurboMarkup) -> None:
    """Resolve references with turbohtml's HTML5 reference resolution."""
    markup.unescape()


def markupsafe_unescape(markup: markupsafe.Markup) -> None:
    """Resolve references with markupsafe's unescape."""
    markup.unescape()


def turbo_markup_format(args: tuple[str, ...]) -> None:
    """Interpolate untrusted operands into a template, escaping each, with turbohtml's Markup.format."""
    _TURBO_MARKUP_TEMPLATE.format(*args)


def markupsafe_format(args: tuple[str, ...]) -> None:
    """Interpolate untrusted operands into a template, escaping each, with markupsafe's Markup.format."""
    _MS_MARKUP_TEMPLATE.format(*args)


def turbo_markup_join(parts: tuple[str, ...]) -> None:
    """Join untrusted parts, escaping each, with turbohtml's Markup.join."""
    _TURBO_MARKUP_JOINER.join(parts)


def markupsafe_join(parts: tuple[str, ...]) -> None:
    """Join untrusted parts, escaping each, with markupsafe's Markup.join."""
    _MS_MARKUP_JOINER.join(parts)


# (label, turbohtml op, turbohtml input, markupsafe op, markupsafe input) for each Markup operation beyond escape.
MARKUP_OPS: tuple[tuple[str, Callable[..., None], object, Callable[..., None], object], ...] = (
    ("striptags", turbo_markup_striptags, _TURBO_MARKUP_OPS, markupsafe_striptags, _MS_MARKUP_OPS),
    ("unescape", turbo_markup_unescape, _TURBO_MARKUP_OPS, markupsafe_unescape, _MS_MARKUP_OPS),
    ("format (escape operands)", turbo_markup_format, _MARKUP_FORMAT_ARGS, markupsafe_format, _MARKUP_FORMAT_ARGS),
    ("join (escape operands)", turbo_markup_join, _MARKUP_JOIN_PARTS, markupsafe_join, _MARKUP_JOIN_PARTS),
)


def run_markup_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark markupsafe-compatible escaping and the Markup operations against markupsafe; return the case names."""
    for name, text in MARKUP_CASES:
        for label, escape in MARKUP_LIBS:
            bench(f"markup {name} [{label}]", escape, text)
    for label, turbo_run, turbo_arg, markupsafe_run, markupsafe_arg in MARKUP_OPS:
        bench(f"markup op {label} [turbohtml]", turbo_run, turbo_arg)
        bench(f"markup op {label} [markupsafe]", markupsafe_run, markupsafe_arg)
    return [name for name, _ in MARKUP_CASES]


def print_markup_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's escape beside markupsafe and its speedup; values are tens of ns."""
    if not cases:
        return
    others = [label for label, _ in MARKUP_LIBS if label != "turbohtml"]
    print()
    header = f"{'markup benchmark':28} {'turbohtml':>12}" + "".join(f"{label:>20}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"markup {name} [turbohtml]"]
        row = f"{'markup ' + name:28} {turbo * 1e9:8.1f} ns"
        for label in others:
            other = means.get(f"markup {name} [{label}]")
            row += f" {other * 1e9:9.1f} ns {other / turbo:4.1f}x" if other is not None else f"{'-':>20}"
        print(row)
    if f"markup op {MARKUP_OPS[0][0]} [turbohtml]" not in means:
        return
    print()
    print(f"{'markup op benchmark':28} {'turbohtml':>12}{'markupsafe':>20}")
    for label, *_ in MARKUP_OPS:
        if (turbo := means.get(f"markup op {label} [turbohtml]")) is None:
            continue
        other = means.get(f"markup op {label} [markupsafe]")
        row = f"{'op ' + label:28} {turbo * 1e9:8.1f} ns"
        row += f" {other * 1e9:9.1f} ns {other / turbo:4.1f}x" if other is not None else f"{'-':>20}"
        print(row)


# --- minify suite: shrink HTML against minify-html -------------------------- #
# turbohtml.serialize(layout=Minify(...)) against minify-html (Rust, the modern leader).
# Both take a source string and return minified HTML, so each run is a full
# parse-and-minify; the cases are real pages from the html5lib-python corpus.


_MINIFY = turbohtml.Minify()


def turbo_minify(text: str) -> None:
    """Parse and minify with turbohtml's serializer minify mode."""
    _ = turbohtml.parse(text).serialize(layout=_MINIFY)


def minifyhtml_minify(text: str) -> None:
    """Minify with minify-html, the Rust minifier (parse and minify in one call)."""
    minify_html.minify(text)


MINIFY_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_minify),
    ("minify-html", minifyhtml_minify),
)

MINIFY_CASES: tuple[tuple[str, str, str], ...] = (
    ("wpt small (4 kB)", "wpt/weighted/align-content-wrap-002.html", "utf-8"),
    ("wpt medium (9.6 kB)", "wpt/weighted/grid-auto-fill-rows-001.html", "utf-8"),
    ("wpt large (92 kB)", "wpt/weighted/test-plan.src.html", "utf-8"),
)


def run_minify_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark turbohtml's minify mode against minify-html; return the case names."""
    cases = [(name, corpus_text(path, enc)) for name, path, enc in MINIFY_CASES]
    for name, text in cases:
        for label, run in MINIFY_LIBS:
            bench(f"minify {name} [{label}]", run, text)
    return [name for name, _ in cases]


def print_minify_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's minify beside minify-html and the slowdown factor; values in ms."""
    if not cases:
        return
    others = [label for label, _ in MINIFY_LIBS if label != "turbohtml"]
    print()
    header = f"{'minify benchmark':28} {'turbohtml':>12}" + "".join(f"{label:>22}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"minify {name} [turbohtml]"]
        row = f"{'minify ' + name:28} {turbo * 1e3:8.2f} ms"
        for label in others:
            other = means.get(f"minify {name} [{label}]")
            row += f" {other * 1e3:10.2f} ms {other / turbo:4.1f}x" if other is not None else f"{'-':>22}"
        print(row)


# --- tables suite: extract <table> grids against pandas.read_html ----------- #
# turbohtml.parse(html).tables() and find("table").records() against pandas.read_html,
# the one-call table reader scrapers reach for. pandas pulls in NumPy and returns
# DataFrames; turbohtml resolves rowspan/colspan in C and hands back plain lists and
# dicts. All three race the full string-to-structure path: each timed call parses the
# HTML and extracts every table. pandas is optional, guarded like html5-parser above.


def build_table_html(data_rows: int) -> str:
    """Build a header plus ``data_rows`` body rows, four columns, with a colspan to exercise span resolution."""
    header = "<tr><th>Region</th><th>Quarter</th><th>Revenue</th><th>Units</th></tr>"
    body = "".join(
        f"<tr><td>R{index}</td><td>Q{index % 4 + 1}</td><td colspan=2>{index * 10}</td></tr>"
        for index in range(data_rows)
    )
    return f"<table>{header}{body}</table>"


def turbo_table_rows(text: str) -> None:
    """Parse and read every table into list[list[str]] with turbohtml's C grid walk."""
    turbohtml.parse(text).tables()


def turbo_table_records(text: str) -> None:
    """Parse and key the first table's rows by its header with turbohtml's records()."""
    table = turbohtml.parse(text).find("table")
    assert table is not None  # the generated document always holds one <table>  # noqa: S101
    table.records()


def pandas_table_rows(text: str) -> None:
    """Parse and read every table into plain rows with pandas.read_html, then materialize the values."""
    for frame in pd.read_html(io.StringIO(text)):
        frame.to_numpy().tolist()


def pandas_table_records(text: str) -> None:
    """Parse and key the first table's rows by its header with pandas.read_html, then to_dict('records')."""
    pd.read_html(io.StringIO(text), header=0)[0].to_dict("records")


# Each op paired with its turbohtml and pandas driver; pandas is the only read_html-style competitor.
TABLE_OPS: tuple[tuple[str, Callable[[str], None], Callable[[str], None]], ...] = (
    ("rows", turbo_table_rows, pandas_table_rows),
    ("records", turbo_table_records, pandas_table_records),
)

TABLE_CASES: tuple[tuple[str, int], ...] = (("10 rows", 10), ("100 rows", 100), ("1000 rows", 1000))


def run_tables_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark table extraction against pandas.read_html across each size; return the case labels."""
    names: list[str] = []
    for size_name, data_rows in TABLE_CASES:
        text = build_table_html(data_rows)
        for op_name, turbo_run, pandas_run in TABLE_OPS:
            bench(f"tables {op_name} {size_name} [turbohtml]", turbo_run, text)
            if pd is not None:
                bench(f"tables {op_name} {size_name} [pandas]", pandas_run, text)
            names.append(f"{op_name} {size_name}")
    return names


def print_tables_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's table extraction beside pandas.read_html and its slowdown factor."""
    if not cases:
        return
    print()
    header = f"{'tables benchmark':28} {'turbohtml':>11}{'pandas':>18}"
    print(header)
    for name in cases:
        turbo = means[f"tables {name} [turbohtml]"]
        other = means.get(f"tables {name} [pandas]")
        row = f"{'tables ' + name:28} {turbo * 1e6:8.1f} us"
        row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# --- linkify suite: auto-link URLs/emails in HTML against bleach and linkify-it #
# turbohtml.linkify against bleach.linkify, the HTML-aware linkifier it succeeds,
# and linkify-it-py, the pure-Python scanner markdown-it-py pulls in. bleach and
# turbohtml parse the HTML and rewrite it; linkify-it only scans and returns the
# matches, so it does strictly less work yet is the pure-Python pace to beat.


def turbo_linkify(text: str) -> None:
    """Linkify HTML with turbohtml, parsing and rewriting the tree."""
    turbo_linkify_html(text)


def bleach_linkify(text: str) -> None:
    """Linkify HTML with bleach's html5lib-based filter."""
    bleach.linkify(text)


_LINKIFY_IT = LinkifyIt()
_TURBO_DETECTOR = TurboDetector()


def linkifyit_scan(text: str) -> None:
    """Scan plain text for links with linkify-it-py, which finds but does not rewrite."""
    _LINKIFY_IT.match(text)


def turbo_detect_find(text: str) -> None:
    """Find every link span with turbohtml's Detector.find, the C scan behind linkify-it-py's match."""
    _TURBO_DETECTOR.find(text)


def turbo_detect_has(text: str) -> None:
    """Test for any link with turbohtml's Detector.has_link, the C scan behind linkify-it-py's test."""
    _TURBO_DETECTOR.has_link(text)


def linkifyit_test(text: str) -> None:
    """Test for any link with linkify-it-py's test, the boolean form of match."""
    _LINKIFY_IT.test(text)


LINKIFY_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_linkify),
    ("bleach", bleach_linkify),
    ("linkify-it", linkifyit_scan),
)

LINKIFY_CASES: tuple[tuple[str, str], ...] = (
    ("comment", "Ping me at bob@example.com or see https://example.com for details."),
    ("prose 1 KiB", ("See https://example.com/path?q=1 and visit www.example.org for more. " * 15)),
    (
        "markup 4 KiB",
        ('<p>Read <a href="https://kept.example">the post</a> then go to https://example.com/x. ' * 45),
    ),
)
LINKIFY_CASE_NAMES = [name for name, _ in LINKIFY_CASES]

# The like-for-like detection race: turbohtml's Detector.find/has_link against linkify-it-py's match/test, both
# scanning a run of plain text and returning the spans (find/match) or a boolean (has_link/test) without rewriting
# any HTML. The linkify table above times turbohtml's full HTML rewrite against linkify-it's scan; this isolates the
# detection primitive the migration page maps one-to-one. Only the plain-text cases apply, since both libraries scan
# raw text rather than HTML.
DETECT_CASES = LINKIFY_CASES[:2]
DETECT_OPS: tuple[tuple[str, Callable[[str], None], Callable[[str], None]], ...] = (
    ("find", turbo_detect_find, linkifyit_scan),
    ("has_link", turbo_detect_has, linkifyit_test),
)


def run_linkify_suite(bench: Callable[[str, object, object], None]) -> None:
    """Benchmark HTML-aware linkifying against bleach and linkify-it-py, plus the detection primitive."""
    for name, text in LINKIFY_CASES:
        for label, run in LINKIFY_LIBS:
            bench(f"linkify {name} [{label}]", run, text)
    for op_label, turbo_run, linkifyit_run in DETECT_OPS:
        for name, text in DETECT_CASES:
            bench(f"detect {op_label} {name} [turbohtml]", turbo_run, text)
            bench(f"detect {op_label} {name} [linkify-it]", linkifyit_run, text)


def print_linkify_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's linkify beside bleach and linkify-it-py and their slowdown factors."""
    if not cases:
        return
    others = [label for label, _ in LINKIFY_LIBS if label != "turbohtml"]
    print()
    header = f"{'linkify benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"linkify {name} [turbohtml]"]
        row = f"{'linkify ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"linkify {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)
    for op_label, *_ in DETECT_OPS:
        if f"detect {op_label} {DETECT_CASES[0][0]} [turbohtml]" not in means:
            continue
        print()
        print(f"{'detect ' + op_label + ' benchmark':28} {'turbohtml':>11}{'linkify-it-py':>18}")
        for name, _ in DETECT_CASES:
            turbo = means[f"detect {op_label} {name} [turbohtml]"]
            other = means.get(f"detect {op_label} {name} [linkify-it]")
            row = f"{op_label + ' ' + name:28} {turbo * 1e6:8.1f} us"
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
            print(row)


# --- markdown suite: HTML to Markdown against markdownify and html2text ------ #
# turbohtml.to_markdown against markdownify (BeautifulSoup) and html2text (a
# streaming HTMLParser). All three take an HTML string and return Markdown, so
# each does its own parse; turbohtml parses to the WHATWG tree and walks it in C.


def turbo_markdown(text: str) -> None:
    """Convert HTML to Markdown with turbohtml, parsing and walking the tree."""
    turbohtml.parse(text).to_markdown()


def markdownify_markdown(text: str) -> None:
    """Convert HTML to Markdown with markdownify, on BeautifulSoup."""
    markdownify.markdownify(text)


_HTML2TEXT = html2text.HTML2Text()
_HTML2TEXT.body_width = 0


def html2text_markdown(text: str) -> None:
    """Convert HTML to Markdown with html2text, a streaming HTMLParser subclass."""
    _HTML2TEXT.handle(text)


MARKDOWN_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_markdown),
    ("markdownify", markdownify_markdown),
    ("html2text", html2text_markdown),
)

MARKDOWN_CASES: tuple[tuple[str, str], ...] = (
    ("article 2 KiB", ("<h2>Heading</h2><p>A <b>bold</b> <a href='/x'>link</a> and <code>code</code>.</p>" * 18)),
    ("list 4 KiB", ("<ul><li>item <em>one</em></li><li>item two<ul><li>nested</li></ul></li></ul>" * 40)),
    ("table 4 KiB", ("<table><tr><th>Name</th><th>Value</th></tr><tr><td>a</td><td>1</td></tr></table>" * 35)),
)

# the configured case turns on the option machinery in each library (underscore
# emphasis, reference links, padded tables, full escaping) so the cost of a
# non-default conversion is measured, not just the opinionated defaults.
_MARKDOWN_OPTS_HTML = (
    "<h2>H</h2><p>A <b>b</b> & <a href='/x'>l</a>.</p>"
    "<table><tr><th>K</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>"
) * 18


def turbo_markdown_configured(text: str) -> None:
    """Convert with turbohtml's option surface engaged."""
    turbohtml.parse(text).to_markdown(
        strong="__", emphasis="_", link_style="reference", pad_tables=True, escape_mode="all"
    )


def markdownify_configured(text: str) -> None:
    """Convert with markdownify's comparable options."""
    markdownify.markdownify(text, strong_em_symbol="_", heading_style="atx", escape_misc=True)


_HTML2TEXT_CONFIGURED = html2text.HTML2Text()
_HTML2TEXT_CONFIGURED.body_width = 0
_HTML2TEXT_CONFIGURED.emphasis_mark = "_"
_HTML2TEXT_CONFIGURED.strong_mark = "__"
_HTML2TEXT_CONFIGURED.inline_links = False
_HTML2TEXT_CONFIGURED.pad_tables = True
_HTML2TEXT_CONFIGURED.escape_snob = True


def html2text_configured(text: str) -> None:
    """Convert with html2text's comparable options."""
    _HTML2TEXT_CONFIGURED.handle(text)


MARKDOWN_OPT_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_markdown_configured),
    ("markdownify", markdownify_configured),
    ("html2text", html2text_configured),
)

# the google_doc case feeds the inline-CSS styling a Google Docs export carries,
# so the CSS-to-Markdown path (bold/italic weights, fixed-width spans) is measured
# against html2text's google_doc mode; markdownify has no equivalent.
_MARKDOWN_GOOGLE_HTML = (
    '<p><span style="font-weight:700">Bold</span> and <span style="font-style:italic">italic</span> and '
    "<span style=\"font-family:'Courier New'\">code()</span> in a line.</p>"
) * 18


def turbo_markdown_google(text: str) -> None:
    """Convert a Google Docs export with turbohtml's google_doc mode."""
    turbohtml.parse(text).to_markdown(google_doc=True)


_HTML2TEXT_GOOGLE = html2text.HTML2Text()
_HTML2TEXT_GOOGLE.body_width = 0
_HTML2TEXT_GOOGLE.google_doc = True


def html2text_google(text: str) -> None:
    """Convert a Google Docs export with html2text's google_doc mode."""
    _HTML2TEXT_GOOGLE.handle(text)


MARKDOWN_GOOGLE_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_markdown_google),
    ("html2text", html2text_google),
)

MARKDOWN_CASE_NAMES = [name for name, _ in MARKDOWN_CASES] + ["configured 4 KiB", "google_doc 4 KiB"]


def turbo_text(text: str) -> None:
    """Render layout-aware text with turbohtml, walking the tree in C."""
    turbohtml.parse(text).to_text()


def inscriptis_text(text: str) -> None:
    """Render layout-aware text with inscriptis, on an lxml tree."""
    inscriptis.get_text(text)


def html_text_text(text: str) -> None:
    """Extract visible text with html-text, walking an lxml tree in Python."""
    html_text.extract_text(text)


def resiliparse_text(text: str) -> None:
    """Extract visible text with resiliparse, off the lexbor tree it shares with selectolax."""
    resiliparse_extract_text(text)


TEXT_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_text),
    ("inscriptis", inscriptis_text),
    *((("html-text", html_text_text),) if html_text is not None else ()),
    ("resiliparse", resiliparse_text),
)

TEXT_CASES: tuple[tuple[str, str], ...] = (
    ("article 2 KiB", ("<h2>Heading</h2><p>A paragraph of plain prose with a <a href='/x'>link</a> in it.</p>" * 16)),
    ("table 4 KiB", ("<table><tr><th>Region</th><th>Total</th></tr><tr><td>North</td><td>120</td></tr></table>" * 30)),
)
TEXT_CASE_NAMES = [name for name, _ in TEXT_CASES] + ["collapsed 2 KiB", "main 4 KiB", "annotated 4 KiB"]

# the collapsed case turns layout guessing off: turbohtml joins the stripped_strings word stream, the role html-text's
# extract_text(guess_layout=False) fills, so the layout-free path is measured against its lxml word walk. inscriptis and
# resiliparse have no comparable collapsed mode, so they sit the case out.
_COLLAPSED_HTML = TEXT_CASES[0][1]


def turbo_text_collapsed(text: str) -> None:
    """Join turbohtml's stripped_strings into the collapsed word stream, html-text's layout-off output."""
    " ".join(turbohtml.parse(text).stripped_strings)


def html_text_collapsed(text: str) -> None:
    """Extract the collapsed word stream with html-text, layout guessing off."""
    html_text.extract_text(text, guess_layout=False)


TEXT_COLLAPSED_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_text_collapsed),
    *((("html-text", html_text_collapsed),) if html_text is not None else ()),
)

# the main case strips page boilerplate before rendering text: turbohtml's main_text against resiliparse's
# extract_plain_text(main_content=True), both a content-density heuristic followed by a text walk. inscriptis and
# html-text render the whole page, so they have no main-content row.
_MAIN_TEXT_BODY = (
    "<p>A comet is an icy small body that, when it passes close to the Sun, warms up and releases gases, forming a "
    "glowing coma around it.</p>"
)
_MAIN_TEXT_HTML = (
    "<html><head><title>Comets</title></head><body>"
    "<nav><a href='/'>Home</a> <a href='/science'>Science</a></nav>"
    "<article><h1>Comets</h1>" + _MAIN_TEXT_BODY * 12 + "</article>"
    "<footer><p>Copyright notice, all rights reserved here.</p></footer></body></html>"
)


def turbo_main_text(text: str) -> None:
    """Extract the boilerplate-stripped main text with turbohtml's main_text in one C pass."""
    turbohtml.parse(text).main_text()


def resiliparse_main_text(text: str) -> None:
    """Extract the main-content text with resiliparse's extract_plain_text main_content mode."""
    resiliparse_extract_text(text, main_content=True)


MAIN_TEXT_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_main_text),
    ("resiliparse", resiliparse_main_text),
)

# the annotation case labels matching elements with spans, the role inscriptis's
# get_annotated_text fills, so the labeled-span path is measured beside it.
_ANNOTATION_RULES = {"h1": ["heading"], "b": ["emphasis"], "a": ["link"]}
_ANNOTATION_CONFIG = ParserConfig(annotation_rules=_ANNOTATION_RULES)
_ANNOTATION_HTML = "<h1>Q3</h1><p>Up <b>12%</b> with a <a href='/x'>link</a> in prose.</p>" * 16


def turbo_annotated(text: str) -> None:
    """Render annotated layout text with turbohtml, recording spans in C."""
    turbohtml.parse(text).to_annotated_text(_ANNOTATION_RULES)


def inscriptis_annotated(text: str) -> None:
    """Render annotated layout text with inscriptis, on an lxml tree."""
    inscriptis.get_annotated_text(text, _ANNOTATION_CONFIG)


ANNOTATION_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_annotated),
    ("inscriptis", inscriptis_annotated),
)


def run_markdown_suite(bench: Callable[[str, object, object], None]) -> None:
    """Benchmark Markdown rendering against markdownify and html2text."""
    for name, text in MARKDOWN_CASES:
        for label, run in MARKDOWN_LIBS:
            bench(f"markdown {name} [{label}]", run, text)
    for label, run in MARKDOWN_OPT_LIBS:
        bench(f"markdown configured 4 KiB [{label}]", run, _MARKDOWN_OPTS_HTML)
    for label, run in MARKDOWN_GOOGLE_LIBS:
        bench(f"markdown google_doc 4 KiB [{label}]", run, _MARKDOWN_GOOGLE_HTML)


def run_text_extraction_suite(bench: Callable[[str, object, object], None]) -> None:
    """Benchmark string-to-text extraction against inscriptis, html-text, and resiliparse."""
    for name, text in TEXT_CASES:
        for label, run in TEXT_LIBS:
            bench(f"text {name} [{label}]", run, text)
    for label, run in TEXT_COLLAPSED_LIBS:
        bench(f"text collapsed 2 KiB [{label}]", run, _COLLAPSED_HTML)
    for label, run in MAIN_TEXT_LIBS:
        bench(f"text main 4 KiB [{label}]", run, _MAIN_TEXT_HTML)
    for label, run in ANNOTATION_LIBS:
        bench(f"text annotated 4 KiB [{label}]", run, _ANNOTATION_HTML)


def print_text_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's to_text beside inscriptis and html-text and their slowdown factors."""
    if not cases:
        return
    others = [label for label, _ in TEXT_LIBS if label != "turbohtml"]
    print()
    header = f"{'text benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"text {name} [turbohtml]"]
        row = f"{'text ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"text {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


def print_markdown_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's to_markdown beside markdownify and html2text and their slowdown factors."""
    if not cases:
        return
    others = [label for label, _ in MARKDOWN_LIBS if label != "turbohtml"]
    print()
    header = f"{'markdown benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"markdown {name} [turbohtml]"]
        row = f"{'markdown ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"markdown {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# --- sanitize suite: allowlist HTML sanitizing against bleach and nh3 --------#
# turbohtml.sanitizer against bleach (its end-of-life predecessor, on html5lib)
# and nh3 (the Rust ammonia binding). All three parse, filter to an allowlist,
# and reserialize; the inputs are realistic user-generated content with a few
# disallowed tags and a dangerous attribute mixed in.


def turbo_sanitize(text: str) -> None:
    """Sanitize with turbohtml's relaxed policy, reusing a prebuilt Sanitizer."""
    _SANITIZER.sanitize(text)


def bleach_sanitize(text: str) -> None:
    """Sanitize with bleach's html5lib-based clean."""
    bleach.clean(text)


def nh3_sanitize(text: str) -> None:
    """Sanitize with nh3, the Rust ammonia binding."""
    nh3.clean(text)


def lxml_clean_sanitize(text: str) -> None:
    """Sanitize with lxml-html-clean's blocklist Cleaner (the externalized lxml.html.clean)."""
    _LXML_CLEANER.clean_html(text)


def html_sanitizer_sanitize(text: str) -> None:
    """Sanitize with html-sanitizer's allowlist Sanitizer, over lxml."""
    _HTML_SANITIZER.sanitize(text)


SANITIZE_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_sanitize),
    ("nh3", nh3_sanitize),
    ("bleach", bleach_sanitize),
    ("lxml-html-clean", lxml_clean_sanitize),
    ("html-sanitizer", html_sanitizer_sanitize),
)

SANITIZE_CASES: tuple[tuple[str, str], ...] = (
    ("comment", "<p>Thanks for the <a href='http://example.com'>link</a>! <script>evil()</script></p>"),
    (
        "post 4 KiB",
        (
            "<div class=post><h1>Title</h1><p>Some <a href='http://example.com'>link</a> and <b>bold</b> text with "
            "<img src=http://x/i.png onerror=alert(1)> and <script>evil()</script>.</p><ul><li>one</li><li>two</li>"
            "</ul></div>"
        )
        * 20,
    ),
)


def run_sanitize_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark allowlist sanitizing against bleach and nh3; return the case names."""
    for name, text in SANITIZE_CASES:
        for label, run in SANITIZE_LIBS:
            bench(f"sanitize {name} [{label}]", run, text)
    return [name for name, _ in SANITIZE_CASES]


def print_sanitize_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's sanitizer beside nh3 and bleach and their speedup factors."""
    if not cases:
        return
    others = [label for label, _ in SANITIZE_LIBS if label != "turbohtml"]
    print()
    header = f"{'sanitize benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"sanitize {name} [turbohtml]"]
        row = f"{'sanitize ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"sanitize {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


def turbo_structured(text: str) -> None:
    """Extract JSON-LD, Microdata, and OpenGraph from a page with turbohtml (parse plus one C walk)."""
    turbohtml.parse(text).structured_data()


def extruct_structured(text: str) -> None:
    """Extract the same three formats with extruct, which builds an lxml tree and runs one extractor per syntax."""
    extruct.extract(text, syntaxes=["json-ld", "microdata", "opengraph"])


STRUCTURED_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_structured),
    *((("extruct", extruct_structured),) if extruct is not None else ()),
)

# A product page carrying all three formats at once: an OpenGraph/Twitter meta block, a JSON-LD Product script, and an
# equivalent Microdata subtree. The larger case tiles it so the walk has real depth to cover.
_STRUCTURED_PAGE = (
    '<head><meta property="og:title" content="Widget"><meta property="og:type" content="product">'
    '<meta property="og:image" content="https://x/i.png"><meta name="twitter:card" content="summary"></head>'
    '<body><script type="application/ld+json">'
    '{"@context": "https://schema.org", "@type": "Product", "name": "Widget", "sku": "W-1", '
    '"offers": {"@type": "Offer", "price": "9.99", "priceCurrency": "USD", "availability": "InStock"}}</script>'
    '<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Widget</span>'
    '<meta itemprop="sku" content="W-1"><div itemprop="offers" itemscope itemtype="https://schema.org/Offer">'
    '<span itemprop="price">9.99</span><meta itemprop="priceCurrency" content="USD">'
    '<link itemprop="availability" href="https://schema.org/InStock"></div></div></body>'
)

STRUCTURED_CASES: tuple[tuple[str, str], ...] = (
    ("product", _STRUCTURED_PAGE),
    ("catalog 8 KiB", _STRUCTURED_PAGE * 12),
)


def run_structured_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark structured-data extraction against extruct; return the case names."""
    for name, text in STRUCTURED_CASES:
        for label, run in STRUCTURED_LIBS:
            bench(f"structured {name} [{label}]", run, text)
    return [name for name, _ in STRUCTURED_CASES]


def print_structured_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's structured_data() beside extruct and the speedup factor."""
    if not cases:
        return
    others = [label for label, _ in STRUCTURED_LIBS if label != "turbohtml"]
    print()
    header = f"{'structured benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"structured {name} [turbohtml]"]
        row = f"{'structured ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"structured {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# --- article suite: main content + metadata extraction ---------------------- #
# turbohtml.Node.article against trafilatura, readability-lxml, and newspaper3k,
# the article extractors it succeeds. Each takes an HTML string, scores the
# content body, and (trafilatura and newspaper3k) harvests the page metadata
# beside it; turbohtml parses to the WHATWG tree and does both in one C pass. The
# inputs are full pages -- navigation, a scored article, and a footer -- so the
# boilerplate the heuristic must discount is measured, not just the body.


def turbo_article(text: str) -> None:
    """Extract the content body and metadata with turbohtml in one C pass."""
    turbohtml.parse(text).article()


def trafilatura_article(text: str) -> None:
    """Extract content and metadata with trafilatura, on an lxml tree."""
    trafilatura.bare_extraction(text, with_metadata=True)


def readability_article(text: str) -> None:
    """Extract the content body and title with readability-lxml."""
    document = ReadabilityDocument(text)
    document.summary()
    document.short_title()


def newspaper_article(text: str) -> None:
    """Extract content and metadata with newspaper3k, from pre-set HTML."""
    article = NewspaperArticle(url="")
    article.set_html(text)
    article.parse()


ARTICLE_LIBS: tuple[tuple[str, Callable[[str], None] | None], ...] = (
    ("turbohtml", turbo_article),
    ("trafilatura", trafilatura_article if trafilatura is not None else None),
    ("readability-lxml", readability_article if ReadabilityDocument is not None else None),
    ("newspaper3k", newspaper_article if NewspaperArticle is not None else None),
)

_ARTICLE_HEAD = (
    "<html lang=en><head><title>Comets: A Field Guide</title>"
    "<meta name=author content='Ada Lovelace'>"
    "<meta property=article:published_time content='2024-05-06'>"
    "<meta name=description content='A short guide to comets and the tails they trail past the Sun.'></head>"
)
_ARTICLE_NAV = "<body><nav><a href='/'>Home</a> <a href='/science'>Science</a> <a href='/space'>Space</a></nav>"
_ARTICLE_PARA = (
    "<p>A comet is an icy small body that, when it passes close to the Sun, warms up, begins to release gases, "
    "and forms a glowing coma, a thin atmosphere, around it.</p>"
)
_ARTICLE_FOOTER = "<footer><p>Copyright notice, all rights reserved here.</p></footer></body></html>"


def _article_page(paragraphs: int) -> str:
    body = f"<article class=post><h1>Comets</h1>{_ARTICLE_PARA * paragraphs}</article>"
    return f"{_ARTICLE_HEAD}{_ARTICLE_NAV}{body}{_ARTICLE_FOOTER}"


ARTICLE_CASES: tuple[tuple[str, str], ...] = (
    ("post 4 KiB", _article_page(16)),
    ("longform 16 KiB", _article_page(72)),
)


def run_article_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark article extraction against trafilatura, readability-lxml, and newspaper3k; return the case names."""
    for name, text in ARTICLE_CASES:
        for label, run in ARTICLE_LIBS:
            if run is not None:
                bench(f"article {name} [{label}]", run, text)
    return [name for name, _ in ARTICLE_CASES]


def print_article_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's article beside each extractor present and its speedup factor."""
    if not cases:
        return
    others = [label for label, run in ARTICLE_LIBS if label != "turbohtml" and run is not None]
    print()
    header = f"{'article benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"article {name} [turbohtml]"]
        row = f"{'article ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"article {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


def run_readpath_suite(bench: Callable[[str, object, object], None], op_index: int, op: str) -> list[str]:
    """Benchmark one read-path operation (find/select/serialize) across every library."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, build, ops in READPATH_LIBS:
            op_fn = ops[op_index]
            if op_fn is None:
                continue  # the library does not offer this operation (e.g. parsel has no serialize/:has)
            bench(f"{op} {name} [{label}]", op_fn, build(text))
        names.append(name)
    return names


def print_readpath_table(means: dict[str, float], op: str, cases: list[str]) -> None:
    """Render turbohtml beside each alternative and its slowdown factor for one read-path operation."""
    if not cases:
        return
    others = [label for label, _, _ in READPATH_LIBS if label != "turbohtml"]
    print()
    header = f"{op + ' benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"{op} {name} [turbohtml]"]
        row = f"{op + ' ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"{op} {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# (op_index, label, gating suite) for every read-path operation that shares run_readpath_suite.
READPATH_OPS: tuple[tuple[int, str, str], ...] = (
    (0, "find", "query"),
    (1, "select", "query"),
    (3, "select :has", "query"),
    (4, "find-text", "query"),
    (5, "text", "text"),
    (2, "serialize", "serialize"),
)


def run_readpath_ops(bench: Callable[[str, object, object], None], suites: set[str]) -> dict[str, list[str]]:
    """Run every read-path operation whose gating suite is selected; key the case lists by op label."""
    return {
        label: (run_readpath_suite(bench, index, label) if suite in suites else [])
        for index, label, suite in READPATH_OPS
    }


def print_readpath_ops(means: dict[str, float], cases: dict[str, list[str]], labels: tuple[str, ...]) -> None:
    """Print the read-path tables for the given operation labels, in order."""
    for label in labels:
        print_readpath_table(means, label, cases[label])


# --- path suite: generate a unique node locator vs lxml's getpath ----------- #
# css_path()/xpath_path() walk an element's ancestor chain to build the selector
# that re-finds it from the root; lxml's getroottree().getpath() is the libxml2
# equivalent. Each timed call addresses every element in a pre-parsed tree, the
# work of serializing a whole document's worth of node paths.


def turbo_css_path(doc: Document) -> None:
    """Generate the unique CSS selector for every element with turbohtml's css_path."""
    for node in doc.descendants:
        if isinstance(node, turbohtml.Element):
            node.css_path()


def turbo_xpath_path(doc: Document) -> None:
    """Generate the positional XPath for every element with turbohtml's xpath_path."""
    for node in doc.descendants:
        if isinstance(node, turbohtml.Element):
            node.xpath_path()


def lxml_getpath(tree: HtmlElement) -> None:
    """Generate the positional XPath for every element with lxml's getpath."""
    root = tree.getroottree()
    for element in tree.iter():
        if isinstance(element.tag, str):  # skip the comment/PI proxies iter() also yields
            root.getpath(element)


# Path-generation competitors; only turbohtml and lxml address a node from the root.
PATH_LIBS: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...] = (
    ("turbohtml css_path", turbo_tree, turbo_css_path),
    ("turbohtml xpath_path", turbo_tree, turbo_xpath_path),
    ("lxml getpath", lxml_tree, lxml_getpath),
)


def run_path_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark generating every node's path across each page size; return the case names."""
    for size_name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, build, generate in PATH_LIBS:
            bench(f"path {size_name} [{label}]", generate, build(text))
    return [name for name, _, _ in READPATH_CASES]


def print_path_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml's css_path and xpath_path beside lxml's getpath per page size."""
    if not cases:
        return
    labels = [label for label, _, _ in PATH_LIBS]
    print()
    print(f"{'path benchmark':24}" + "".join(f"{label:>24}" for label in labels))
    for name in cases:
        row = f"{'path ' + name:24}"
        for label in labels:
            value = means.get(f"path {name} [{label}]")
            row += f"{value * 1e6:20.1f} us" if value is not None else f"{'-':>24}"
        print(row)


# --- links suite: extract and rewrite the in-document links ----------------- #
# turbohtml's links()/resolve_links()/rewrite_links() against lxml.html's
# iterlinks()/make_links_absolute()/rewrite_links(), the only other library that
# walks the link-bearing attributes (href/src/srcset/...) as a set. extract is
# read-only; absolutize and rewrite are idempotent once applied (an absolute URL
# stays absolute, an identity rewrite is a no-op), so pyperf's repeated calls do
# equal work. Run on the largest read-path page, which carries the most links.
LINKS_BASE_URL = "https://example.com/base/"


def turbo_links_extract(doc: Document) -> None:
    """Collect every link with turbohtml's links()."""
    doc.links()


def lxml_links_extract(tree: HtmlElement) -> None:
    """Collect every link with lxml's iterlinks()."""
    for _link in tree.iterlinks():
        pass


def turbo_links_absolutize(doc: Document) -> None:
    """Resolve every relative link against a base with turbohtml's resolve_links()."""
    doc.resolve_links(LINKS_BASE_URL)


def lxml_links_absolutize(tree: HtmlElement) -> None:
    """Resolve every relative link against a base with lxml's make_links_absolute()."""
    tree.make_links_absolute(LINKS_BASE_URL)


def turbo_links_rewrite(doc: Document) -> None:
    """Rewrite every link through a callback with turbohtml's rewrite_links()."""
    doc.rewrite_links(lambda url: url)


def lxml_links_rewrite(tree: HtmlElement) -> None:
    """Rewrite every link through a callback with lxml's rewrite_links()."""
    tree.rewrite_links(lambda url: url)


# (operation label, turbohtml op, lxml op); each pairs the matching method on both sides.
LINKS_OPS: tuple[tuple[str, Callable[..., None], Callable[..., None]], ...] = (
    ("extract", turbo_links_extract, lxml_links_extract),
    ("absolutize", turbo_links_absolutize, lxml_links_absolutize),
    ("rewrite", turbo_links_rewrite, lxml_links_rewrite),
)


def run_links_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark link extraction and rewriting on the largest read-path page; return the case names."""
    _, path, enc = READPATH_CASES[-1]
    text = corpus_text(path, enc)
    for op_name, turbo_op, lxml_op in LINKS_OPS:
        bench(f"links {op_name} [turbohtml]", turbo_op, turbo_tree(text))
        bench(f"links {op_name} [lxml]", lxml_op, lxml_tree(text))
    return [op_name for op_name, _, _ in LINKS_OPS]


def print_links_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml beside lxml and its slowdown factor for each link operation."""
    if not cases:
        return
    print()
    print(f"{'links benchmark':28} {'turbohtml':>11} {'lxml':>11} {'slowdown':>9}")
    for name in cases:
        turbo = means[f"links {name} [turbohtml]"]
        other = means.get(f"links {name} [lxml]")
        row = f"{'links ' + name:28} {turbo * 1e6:8.1f} us"
        row += f" {other * 1e6:8.1f} us {other / turbo:7.1f}x" if other is not None else f"{'-':>21}"
        print(row)


# --- fragment suite: parse an HTML fragment in a context --------------------- #
# turbohtml's parse_fragment against lxml.html's fromstring (which returns a
# fragment element) and html5lib's parseFragment. The fragment path is what each
# library offers for innerHTML-style snippets that are not whole documents; the
# input is a realistic table-row fragment parsed in its container context.
FRAGMENT_HTML = "<tr><td>cell</td><td><a href='/x'>link</a></td></tr>" * 40


def turbo_parse_fragment(text: str) -> None:
    """Parse a fragment in its container context with turbohtml's parse_fragment."""
    turbohtml.parse_fragment(text, context="tbody")


def lxml_parse_fragment(text: str) -> None:
    """Parse a fragment with lxml.html's fromstring."""
    lxml_html.fromstring(text)


def html5lib_parse_fragment(text: str) -> None:
    """Parse a fragment with html5lib's parseFragment."""
    html5lib.parseFragment(text)


FRAGMENT_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_parse_fragment),
    ("lxml", lxml_parse_fragment),
    ("html5lib", html5lib_parse_fragment),
)


def run_fragment_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark fragment parsing across turbohtml, lxml, and html5lib; return the case name."""
    case = "table-row fragment (2 kB)"
    for label, parse in FRAGMENT_LIBS:
        bench(f"fragment {case} [{label}]", parse, FRAGMENT_HTML)
    return [case]


def print_fragment_table(means: dict[str, float], cases: list[str]) -> None:
    """Render turbohtml beside lxml and html5lib and their slowdown factors for fragment parsing."""
    if not cases:
        return
    others = [label for label, _ in FRAGMENT_LIBS if label != "turbohtml"]
    print()
    header = f"{'fragment benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
    print(header)
    for name in cases:
        turbo = means[f"fragment {name} [turbohtml]"]
        row = f"{'fragment ' + name:28} {turbo * 1e6:8.1f} us"
        for label in others:
            other = means.get(f"fragment {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# --- extract suite: pull values out of a document --------------------------- #
# The extraction idioms the parsel, pyquery, and w3lib migrations center on, none of
# which the find/select/edit suites cover. Selector extraction reads every matched
# node's @href and visible text -- parsel's ``::attr``/``::text`` getall and a pyquery
# ``.items()`` read, against turbohtml selecting once and reading
# :meth:`~turbohtml.Element.attr`/:attr:`~turbohtml.Node.text` off each node, over a
# pre-parsed page. URL-hint extraction reads a document's own ``<base>`` and meta
# refresh -- w3lib's ``get_base_url``/``get_meta_refresh`` against turbohtml's
# :meth:`~turbohtml.Document.base_url`/:meth:`~turbohtml.Document.meta_refresh`; both
# parse the string each call, so that case parses afresh.
EXTRACT_SELECTOR = "a"


def turbo_extract_attr(doc: Document) -> None:
    """Read every anchor's href by selecting once and reading attr off each node."""
    for anchor in doc.select(EXTRACT_SELECTOR):
        anchor.attr("href")


def turbo_extract_text(doc: Document) -> None:
    """Read every anchor's visible text by selecting once and reading text off each node."""
    for anchor in doc.select(EXTRACT_SELECTOR):
        _ = anchor.text


def parsel_extract_attr(sel: Selector) -> None:
    """Pull every anchor's href with parsel's ``::attr(href)`` getall."""
    sel.css("a::attr(href)").getall()


def parsel_extract_text(sel: Selector) -> None:
    """Pull every anchor's text with parsel's ``::text`` getall."""
    sel.css("a::text").getall()


def pyquery_extract_attr(page: PyQuery) -> None:
    """Read every anchor's href by iterating a pyquery ``.items()`` set."""
    for item in page("a").items():
        item.attr("href")


def pyquery_extract_text(page: PyQuery) -> None:
    """Read every anchor's text by iterating a pyquery ``.items()`` set."""
    for item in page("a").items():
        item.text()


# (label, build, (read-attr op, read-text op)); each library reads the same matched set its own way.
EXTRACT_LIBS: tuple[tuple[str, Callable[[str], object], tuple[Callable[..., None], Callable[..., None]]], ...] = (
    ("turbohtml", turbo_tree, (turbo_extract_attr, turbo_extract_text)),
    ("parsel", parsel_tree, (parsel_extract_attr, parsel_extract_text)),
    ("pyquery", pyquery_tree, (pyquery_extract_attr, pyquery_extract_text)),
)
EXTRACT_OP_NAMES = ("attr (@href)", "text")

# A small document carrying both URL hints; w3lib and turbohtml each parse it per call.
_URL_HINT_HTML = (
    "<html><head><base href='/sub/'>"
    "<meta http-equiv='refresh' content='5; url=next.html'>"
    "<title>Doc</title></head><body><p>Body copy.</p></body></html>"
)
_URL_HINT_BASE = "http://site.com/"


def turbo_base_url(text: str) -> None:
    """Resolve the document's base URL with turbohtml's base_url, parsing the string."""
    turbohtml.parse(text).base_url(_URL_HINT_BASE)


def w3lib_base_url(text: str) -> None:
    """Resolve the document's base URL with w3lib's regex get_base_url."""
    w3lib.html.get_base_url(text, _URL_HINT_BASE)


def turbo_meta_refresh(text: str) -> None:
    """Read the meta refresh hint with turbohtml's meta_refresh, parsing the string."""
    turbohtml.parse(text).meta_refresh(_URL_HINT_BASE)


def w3lib_meta_refresh(text: str) -> None:
    """Read the meta refresh hint with w3lib's regex get_meta_refresh."""
    w3lib.html.get_meta_refresh(text, _URL_HINT_BASE)


# (label, turbohtml op, w3lib op) for each document URL hint.
URL_HINT_OPS: tuple[tuple[str, Callable[[str], None], Callable[[str], None]], ...] = (
    ("base url", turbo_base_url, w3lib_base_url),
    ("meta refresh", turbo_meta_refresh, w3lib_meta_refresh),
)


def run_extract_suite(bench: Callable[[str, object, object], None]) -> None:
    """Benchmark selector extraction (vs parsel/pyquery) and URL-hint extraction (vs w3lib)."""
    for op_index, op_name in enumerate(EXTRACT_OP_NAMES):
        for size_name, path, enc in READPATH_CASES:
            text = corpus_text(path, enc)
            for label, build, ops in EXTRACT_LIBS:
                bench(f"extract {op_name} {size_name} [{label}]", ops[op_index], build(text))
    for hint_name, turbo_op, w3lib_op in URL_HINT_OPS:
        bench(f"extract {hint_name} [turbohtml]", turbo_op, _URL_HINT_HTML)
        bench(f"extract {hint_name} [w3lib]", w3lib_op, _URL_HINT_HTML)


def print_extract_table(means: dict[str, float], cases: list[str]) -> None:
    """Render the selector-extraction race (turbohtml/parsel/pyquery) and the URL-hint race (turbohtml/w3lib)."""
    if not cases:
        return
    others = [label for label, _, _ in EXTRACT_LIBS if label != "turbohtml"]
    for op_name in EXTRACT_OP_NAMES:
        print()
        header = f"{'extract ' + op_name:28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
        print(header)
        for size_name, _, _ in READPATH_CASES:
            turbo = means[f"extract {op_name} {size_name} [turbohtml]"]
            row = f"{size_name:28} {turbo * 1e6:8.1f} us"
            for label in others:
                other = means.get(f"extract {op_name} {size_name} [{label}]")
                row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
            print(row)
    print()
    print(f"{'extract url hint':28} {'turbohtml':>11}{'w3lib':>18}")
    for hint_name, *_ in URL_HINT_OPS:
        turbo = means[f"extract {hint_name} [turbohtml]"]
        other = means.get(f"extract {hint_name} [w3lib]")
        row = f"{hint_name:28} {turbo * 1e6:8.1f} us"
        row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


# --- xpath suite: evaluate the XPath feature surface over a pre-parsed tree - #
# turbohtml.xpath against lxml's libxml2 XPath engine, the de-facto XPath in
# Python that parsel, pyquery, and html5-parser all delegate to. selectolax and
# BeautifulSoup expose no XPath, so each table is a straight turbohtml-vs-lxml
# race. One expression per feature class -- name tests, the descendant
# abbreviation, attribute and positional and arithmetic predicates, string and
# aggregate functions, a reverse axis, a union, a computed name test -- runs
# across the wpt pages so the comparison spans the whole engine, not one query.
XPATH_EXPRS: tuple[tuple[str, str], ...] = (
    ("descendant name test", "//div"),
    ("attribute predicate", "//a[@href]"),
    ("descendant combinator", "//div//a[@href]"),
    ("absolute child path", "/html/body/div"),
    ("positional predicate", "//div//a[1]"),
    ("string function", "//a[contains(@href, '/')]"),
    ("arithmetic predicate", "//div[position() <= 3]"),
    ("reverse axis", "//a/ancestor::div"),
    ("union", "//a | //span"),
    ("computed name test", "//*[local-name() = 'a']"),
    ("aggregate count", "count(//a)"),
)


def turbo_xpath(doc: Document, expr: str) -> None:
    """Evaluate one XPath expression with turbohtml's compiled-program engine."""
    doc.xpath(expr)


def lxml_xpath(tree: HtmlElement, expr: str) -> None:
    """Evaluate the same XPath expression with lxml's libxml2 engine."""
    tree.xpath(expr)


# XPath competitors; only turbohtml and lxml expose an XPath engine.
XPATH_LIBS: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...] = (
    ("turbohtml", turbo_tree, turbo_xpath),
    ("lxml", lxml_tree, lxml_xpath),
)


def run_xpath_suite(bench: Callable[[str, object, object], None]) -> tuple[list[str], list[str]]:
    """Benchmark every XPath feature class across each page size; return (features, sizes)."""
    for feature, expr in XPATH_EXPRS:
        for size_name, path, enc in READPATH_CASES:
            text = corpus_text(path, enc)
            for label, build, evaluate in XPATH_LIBS:
                bench(f"xpath {feature} | {size_name} [{label}]", functools.partial(evaluate, expr=expr), build(text))
    return [feature for feature, _ in XPATH_EXPRS], [name for name, _, _ in READPATH_CASES]


def print_xpath_table(means: dict[str, float], suite: tuple[list[str], list[str]]) -> None:
    """Render one table per page size: turbohtml beside lxml across every feature class."""
    features, sizes = suite or ([], [])
    if not features:
        return
    others = [label for label, _, _ in XPATH_LIBS if label != "turbohtml"]
    for size_name in sizes:
        print()
        header = f"{'xpath / ' + size_name:34} {'turbohtml':>11}" + "".join(f"{label:>18}" for label in others)
        print(header)
        for feature in features:
            if (turbo := means.get(f"xpath {feature} | {size_name} [turbohtml]")) is None:
                continue
            row = f"{feature:34} {turbo * 1e6:8.1f} us"
            for label in others:
                other = means.get(f"xpath {feature} | {size_name} [{label}]")
                row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
            print(row)


# --- xpath parity-feature suite: the lxml/parsel call options against lxml -- #
# Each case exercises one option the parity work added: a $variable binding, an
# EXSLT regex, an EXSLT node-set reduction, a smart string, a custom extension.
# These reach past the all-C fast path the structural queries use -- re:
# dispatches to Python's re where lxml uses C libexslt, an extension runs a
# Python callable per match -- so the table is honest about the cost of the
# Python-backed surface. set:distinct stays in C on both sides (turbohtml's
# built-in dispatch, lxml's registered libexslt), so it races C against C.
_EXSLT_NS = {"re": "http://exslt.org/regular-expressions", "set": "http://exslt.org/sets"}
_SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
# a small SVG block appended to each feature page so the namespaced name test resolves over foreign content
_SVG_FRAGMENT = "<svg><rect/><rect/></svg>"


def _bench_count_ext(_context: object, nodes: list[object]) -> float:
    """Count the node-set; a trivial extension registered for both engines."""
    return float(len(nodes))


_XPATH_EXTENSIONS: dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]] = {
    (None, "ext_count"): _bench_count_ext
}


def turbo_variable(doc: Document) -> None:
    """Bind a $variable, turbohtml."""
    doc.xpath("//a[@href=$href]", href="/x")


def lxml_variable(tree: HtmlElement) -> None:
    """Bind a $variable, lxml."""
    tree.xpath("//a[@href=$href]", href="/x")


def turbo_retest(doc: Document) -> None:
    """Run an EXSLT re:test predicate, turbohtml (Python re)."""
    doc.xpath("//a[re:test(@href, '[0-9]')]")


def lxml_retest(tree: HtmlElement) -> None:
    """Run an EXSLT re:test predicate, lxml (C libexslt)."""
    tree.xpath("//a[re:test(@href, '[0-9]')]", namespaces=_EXSLT_NS)


def turbo_setdistinct(doc: Document) -> None:
    """Run an EXSLT set:distinct node-set reduction, turbohtml (built-in dispatch)."""
    doc.xpath("set:distinct(//a)")


def lxml_setdistinct(tree: HtmlElement) -> None:
    """Run an EXSLT set:distinct node-set reduction, lxml (C libexslt, namespace registered)."""
    tree.xpath("set:distinct(//a)", namespaces=_EXSLT_NS)


def turbo_smart(doc: Document) -> None:
    """Collect attributes as smart strings, turbohtml."""
    doc.xpath("//a/@href", smart_strings=True)


def lxml_smart(tree: HtmlElement) -> None:
    """Collect attributes as smart strings, lxml."""
    tree.xpath("//a/@href", smart_strings=True)


def turbo_extension(doc: Document) -> None:
    """Call a custom extension function, turbohtml."""
    doc.xpath("ext_count(//a)", extensions=_XPATH_EXTENSIONS)


def lxml_extension(tree: HtmlElement) -> None:
    """Call a custom extension function, lxml."""
    tree.xpath("ext_count(//a)", extensions=_XPATH_EXTENSIONS)


def _bench_first_two(_context: object, nodes: list[Element]) -> list[Element]:
    """Return the first two nodes as a node-set; the cheapest non-trivial node-set return."""
    return nodes[:2]


_XPATH_NODESET_EXTENSIONS: dict[
    tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]
] = {(None, "ext_first_two"): _bench_first_two}


def turbo_nodeset_extension(doc: Document) -> None:
    """Call an extension that returns a node-set feeding a later path step, turbohtml."""
    doc.xpath("ext_first_two(//a)/@href", extensions=_XPATH_NODESET_EXTENSIONS)


def lxml_nodeset_extension(tree: HtmlElement) -> None:
    """Call an extension that returns a node-set feeding a later path step, lxml."""
    tree.xpath("ext_first_two(//a)/@href", extensions=_XPATH_NODESET_EXTENSIONS)


def turbo_namespaced(doc: Document) -> None:
    """Resolve a namespace-prefixed name test against a ``namespaces=`` mapping, turbohtml."""
    doc.xpath("//svg:rect", namespaces=_SVG_NS)


def lxml_namespaced(tree: HtmlElement) -> None:
    """Resolve the same namespace-prefixed name test against the same mapping, lxml."""
    tree.xpath("//svg:rect", namespaces=_SVG_NS)


def turbo_node_set(doc: Document, rows: Iterable[Element]) -> None:
    """Reuse a prior result by binding it as a node-set ``$variable``, turbohtml."""
    doc.xpath("$rows/div", rows=rows)


def lxml_node_set(tree: HtmlElement, rows: object) -> None:
    """Reuse a prior result by binding it as a node-set ``$variable``, lxml."""
    tree.xpath("$rows/div", rows=rows)


# Each parity feature paired with its turbohtml and lxml driver.
XPATH_FEATURE_CASES: tuple[tuple[str, Callable[[Document], None], Callable[[HtmlElement], None]], ...] = (
    ("$variable binding", turbo_variable, lxml_variable),
    ("EXSLT re:test", turbo_retest, lxml_retest),
    ("EXSLT set:distinct", turbo_setdistinct, lxml_setdistinct),
    ("smart_strings", turbo_smart, lxml_smart),
    ("extension function", turbo_extension, lxml_extension),
    ("extension node-set", turbo_nodeset_extension, lxml_nodeset_extension),
    ("namespaces= name test", turbo_namespaced, lxml_namespaced),
)

# The reuse path turbohtml.XPath adds: parse the expression once into an immutable
# program, then evaluate it on every call without the per-call parse doc.xpath pays.
# lxml's etree.XPath is the same compile-once design, so both objects are built once
# here and pyperf re-evaluates them, isolating the per-evaluation cost.
REUSE_EXPR = "//a[@href]"


def turbo_reuse(state: tuple[turbohtml.XPath, Document]) -> None:
    """Evaluate a precompiled turbohtml.XPath, reusing the compiled program each call."""
    compiled, doc = state
    compiled(doc)


def lxml_reuse(state: tuple[Callable[..., object], HtmlElement]) -> None:
    """Evaluate a precompiled lxml etree.XPath, lxml's matching compile-once path."""
    compiled, tree = state
    compiled(tree)


def run_xpath_feature_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark each parity feature across the page sizes; return the case labels."""
    for label, turbo_run, lxml_run in XPATH_FEATURE_CASES:
        for size_name, path, enc in READPATH_CASES:
            text = corpus_text(path, enc) + _SVG_FRAGMENT
            bench(f"feature {label} | {size_name} [turbohtml]", turbo_run, turbo_tree(text))
            bench(f"feature {label} | {size_name} [lxml]", lxml_run, lxml_tree(text))
    for size_name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        bench(
            f"feature precompiled reuse | {size_name} [turbohtml]",
            turbo_reuse,
            (turbohtml.XPath(REUSE_EXPR), turbo_tree(text)),
        )
        bench(
            f"feature precompiled reuse | {size_name} [lxml]",
            lxml_reuse,
            (lxml_html.etree.XPath(REUSE_EXPR), lxml_tree(text)),
        )
    # The node-set case binds a prior result, so the rows are queried once outside the
    # timed region and reused on every call -- both engines accept a node-set variable.
    for size_name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        turbo_doc, lxml_doc = turbo_tree(text), lxml_tree(text)
        turbo_rows = [node for node in turbo_doc.xpath("//div") if isinstance(node, turbohtml.Element)]
        lxml_rows = lxml_doc.xpath("//div")
        bench(
            f"feature node-set variable | {size_name} [turbohtml]",
            functools.partial(turbo_node_set, rows=turbo_rows),
            turbo_doc,
        )
        bench(
            f"feature node-set variable | {size_name} [lxml]",
            functools.partial(lxml_node_set, rows=lxml_rows),
            lxml_doc,
        )
    return [label for label, _, _ in XPATH_FEATURE_CASES] + ["precompiled reuse", "node-set variable"]


def print_xpath_feature_table(means: dict[str, float], labels: list[str]) -> None:
    """Render one table per page size: turbohtml beside lxml across the parity features."""
    if not labels:
        return
    for size_name, _, _ in READPATH_CASES:
        print()
        header = f"{'xpath feature / ' + size_name:34} {'turbohtml':>11}{'lxml':>18}"
        print(header)
        for label in labels:
            if (turbo := means.get(f"feature {label} | {size_name} [turbohtml]")) is None:
                continue
            other = means.get(f"feature {label} | {size_name} [lxml]")
            row = f"{label:34} {turbo * 1e6:8.1f} us"
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
            print(row)


def stdlib_tokenize(text: str) -> None:
    """Drive the stdlib parser with its default no-op handlers."""
    parser = HTMLParser()
    parser.feed(text)
    parser.close()


def html5lib_tokenize(text: str) -> None:
    """Drive html5lib's tokenizer."""
    # html5lib is an optional comparison lib; the local import lets _has_html5lib() detect its absence
    from html5lib._tokenizer import HTMLTokenizer  # noqa: PLC0415, PLC2701  # html5lib's tokenizer is internal API

    for _ in HTMLTokenizer(text):
        pass


def w3lib_unescape(text: str) -> None:
    """Resolve character references with w3lib's replace_entities, the closest competitor to unescape."""
    w3lib.html.replace_entities(text)


def print_table(means: dict[str, float], rows: list[tuple[str, str]]) -> None:
    """Render the speedup table from the collected per-benchmark means."""
    print()
    header = f"{'benchmark':40} {'turbohtml':>12} {'stdlib':>12} {'speedup':>8}"
    print(f"{header} {'html5lib/w3lib':>14} {'speedup':>8}")
    for op, name in rows:
        turbo = means[f"{op} {name} [turbohtml]"]
        stdlib = means[f"{op} {name} [stdlib]"]
        row = f"{op + ' ' + name:40} {turbo * 1e6:9.2f} us {stdlib * 1e6:9.2f} us {stdlib / turbo:7.1f}x"
        # html5lib (tokenize) and w3lib (unescape) never both apply to one row, so they share the trailing column
        if (other := means.get(f"{op} {name} [html5lib]") or means.get(f"{op} {name} [w3lib]")) is not None:
            row += f" {other * 1e6:11.2f} us {other / turbo:7.1f}x"
        print(row)


def _has_html5lib() -> bool:
    try:
        html5lib_tokenize("")
    except ImportError:
        return False
    return True


def run_parse_suite(bench: Callable[[str, object, object], None]) -> list[tuple[str, str]]:
    """Benchmark whole-document parsing for turbohtml and every alternative; return the cases."""
    cases = [(name, corpus_text(path, enc)) for name, path, enc in CORPUS_FILES]
    cases += [(name, large_text(filename, url)) for name, filename, url in LARGE_FILES]
    for name, arg in cases:
        bench(f"parse {name} [turbohtml]", turbo_parse, arg)
        for label, parse in PARSE_COMPETITORS:
            bench(f"parse {name} [{label}]", parse, arg)
    return cases


def print_parse_table(means: dict[str, float], parse_cases: list[tuple[str, str]]) -> None:
    """Render parse throughput for turbohtml beside each alternative and its slowdown factor."""
    if not parse_cases:
        return
    print()
    header = f"{'parse benchmark':28} {'turbohtml':>11}" + "".join(f"{label:>18}" for label, _ in PARSE_COMPETITORS)
    print(header)
    for name, _ in parse_cases:
        turbo = means[f"parse {name} [turbohtml]"]
        row = f"{'parse ' + name:28} {turbo * 1e6:8.1f} us"
        for label, _ in PARSE_COMPETITORS:
            other = means.get(f"parse {name} [{label}]")
            row += f" {other * 1e6:8.1f} us {other / turbo:4.1f}x" if other is not None else f"{'-':>18}"
        print(row)


def run_string_suites(bench: Callable[[str, object, object], None], suites: set[str]) -> list[tuple[str, str]]:
    """Run the escape/unescape/tokenize/corpus suites; return the ``(op, name)`` rows for the speedup table."""
    rows: list[tuple[str, str]] = []
    for op, name, arg in CASES:
        if op in suites:
            bench(f"{op} {name} [turbohtml]", getattr(turbohtml, op), arg)
            bench(f"{op} {name} [stdlib]", getattr(html, op), arg)
            if op == "unescape":
                bench(f"unescape {name} [w3lib]", w3lib_unescape, arg)
            rows.append((op, name))
    tokenize_cases = TOKENIZE_CASES if "tokenize" in suites else []
    if "corpus" in suites:
        tokenize_cases += [(name, corpus_text(path, enc)) for name, path, enc in CORPUS_FILES]
        tokenize_cases += [(name, large_text(filename, url)) for name, filename, url in LARGE_FILES]
    has_html5lib = _has_html5lib()
    for name, arg in tokenize_cases:
        bench(f"tokenize {name} [turbohtml]", turbo_tokenize, arg)
        bench(f"tokenize {name} [stdlib]", stdlib_tokenize, arg)
        if has_html5lib:
            bench(f"tokenize {name} [html5lib]", html5lib_tokenize, arg)
        rows.append(("tokenize", name))
    return rows


# Suites whose runner returns a case list and whose table takes (means, cases); the
# orchestration drives them through this table instead of a line apiece.
SIMPLE_SUITES: tuple[tuple[str, Callable[..., list[str]], Callable[[dict[str, float], list[str]], None]], ...] = (
    ("build", run_build_suite, print_build_table),
    ("edit", run_edit_suite, print_edit_table),
    ("navigate", run_navigate_suite, print_navigate_table),
    ("links", run_links_suite, print_links_table),
    ("fragment", run_fragment_suite, print_fragment_table),
    ("chain", run_chain_suite, print_chain_table),
    ("htmlparser", run_htmlparser_suite, print_htmlparser_table),
    ("stream", run_stream_suite, print_stream_table),
    ("markup", run_markup_suite, print_markup_table),
    ("minify", run_minify_suite, print_minify_table),
    ("tables", run_tables_suite, print_tables_table),
    ("article", run_article_suite, print_article_table),
)

# Suites whose runner has no return value the orchestration reuses; each prints from its own module
# constants below. Driving them through one table keeps main() under the complexity gate.
VOID_SUITES: tuple[tuple[str, Callable[[Callable[[str, object, object], None]], object]], ...] = (
    ("text", run_text_extraction_suite),
    ("extract", run_extract_suite),
    ("linkify", run_linkify_suite),
    ("markdown", run_markdown_suite),
    ("sanitize", run_sanitize_suite),
    ("structured", run_structured_suite),
)


def main() -> None:
    """Run all cases under pyperf and print the speedup table in the parent."""
    runner = pyperf.Runner()
    runner.argparser.add_argument(
        "suites",
        nargs="*",
        choices=[
            "escape",
            "unescape",
            "tokenize",
            "corpus",
            "parse",
            "fragment",
            "query",
            "text",
            "xpath",
            "serialize",
            "build",
            "edit",
            "navigate",
            "links",
            "extract",
            "chain",
            "htmlparser",
            "stream",
            "markup",
            "minify",
            "tables",
            "linkify",
            "markdown",
            "sanitize",
            "structured",
            "article",
            [],
        ],
        help="suites to run (default: all)",
    )
    args = runner.parse_args()
    # pyperf rebuilds worker command lines from its own options only, so the
    # suite selection rides to the workers through the environment
    if args.suites:
        os.environ["TURBOHTML_BENCH_SUITES"] = ",".join(args.suites)
        args.inherit_environ = [*(args.inherit_environ or []), "TURBOHTML_BENCH_SUITES"]
    suites = set(
        os.environ.get(
            "TURBOHTML_BENCH_SUITES",
            "escape,unescape,tokenize,corpus,parse,fragment,query,text,xpath,serialize,build,edit,navigate,links,extract,chain,htmlparser,stream,markup,minify,tables,linkify,markdown,sanitize,structured,article",
        ).split(",")
    )
    means: dict[str, float] = {}

    def bench(name: str, func: object, arg: object) -> None:
        if (result := runner.bench_func(name, func, arg)) is not None and result.get_nvalue():
            means[name] = result.mean()

    rows = run_string_suites(bench, suites)
    parse_cases = run_parse_suite(bench) if "parse" in suites else []
    readpath_cases = run_readpath_ops(bench, suites)
    path_cases = run_path_suite(bench) if "query" in suites else []
    xpath_cases = run_xpath_suite(bench) if "xpath" in suites else ([], [])
    xpath_feature_cases = run_xpath_feature_suite(bench) if "xpath" in suites else []
    simple_cases = {name: run_fn(bench) for name, run_fn, _ in SIMPLE_SUITES if name in suites}
    for suite_name, run_fn in VOID_SUITES:
        if suite_name in suites:
            run_fn(bench)
    if args.worker or not means:
        return
    print_table(means, rows)
    print_parse_table(means, parse_cases)
    print_readpath_ops(means, readpath_cases, ("find", "select", "select :has", "find-text", "text"))
    print_path_table(means, path_cases)
    print_xpath_table(means, xpath_cases)
    print_xpath_feature_table(means, xpath_feature_cases)
    print_readpath_ops(means, readpath_cases, ("serialize",))
    for name, _, print_fn in SIMPLE_SUITES:
        print_fn(means, simple_cases.get(name, []))
    print_linkify_table(means, LINKIFY_CASE_NAMES if "linkify" in suites else [])
    print_markdown_table(means, MARKDOWN_CASE_NAMES if "markdown" in suites else [])
    print_text_table(means, TEXT_CASE_NAMES if "text" in suites else [])
    print_extract_table(means, list(EXTRACT_OP_NAMES) if "extract" in suites else [])
    print_sanitize_table(means, [n for n, _ in SANITIZE_CASES] if "sanitize" in suites else [])
    print_structured_table(means, [n for n, _ in STRUCTURED_CASES] if "structured" in suites else [])


if __name__ == "__main__":
    main()
