#!/usr/bin/env python3
"""
Benchmark turbohtml's escape/unescape/tokenize/parse against other libraries.

Run with ``tox -e bench``; positional arguments pick the suites to run
(``escape``, ``unescape``, ``tokenize``, ``corpus``, ``parse``, ``query``,
``serialize``; default all). Remaining arguments are forwarded to pyperf (pass
``--help`` to see them). pyperf runs every case in isolated worker processes and
reports mean and stddev; the parent process then prints a speedup table: escape,
unescape, and tokenize against the standard library, parse against the other HTML
tree builders (lxml, selectolax, html5lib, and BeautifulSoup), and the read-path
query and serialize suites against lxml, selectolax, and BeautifulSoup.

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

import html
import os
import random
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

import bleach
import html5lib
import markupsafe
import nh3
import pyperf
from bs4 import BeautifulSoup
from linkify_it import LinkifyIt
from lxml import html as lxml_html
from selectolax.lexbor import LexborHTMLParser

import turbohtml
from turbohtml import sanitizer as turbo_sanitizer
from turbohtml.linkify import linkify as turbo_linkify_html
from turbohtml.markup import escape as turbo_markup_escape

_SANITIZER = turbo_sanitizer.Sanitizer(turbo_sanitizer.Policy.relaxed())

if TYPE_CHECKING:
    from collections.abc import Callable

    from lxml.html import HtmlElement

    from turbohtml import Document

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


def html5lib_parse(text: str) -> None:
    """Parse with html5lib, the pure-Python WHATWG reference implementation."""
    html5lib.parse(text)


def soup_parse(text: str) -> None:
    """Parse with BeautifulSoup over its stdlib html.parser backend."""
    BeautifulSoup(text, "html.parser")


# Whole-document tree builders raced against turbohtml.parse() in the parse suite, ordered fastest to slowest.
# Each label names the pip-installable package so the comparison stays like-for-like.
PARSE_COMPETITORS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("lxml", lxml_parse),
    ("selectolax", lexbor_parse),
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


def run_build_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark programmatic tree construction across every library; return the case names."""
    for name, count in BUILD_CASES:
        for label, build in BUILD_LIBS:
            bench(f"build {name} [{label}]", build, count)
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


# Read-path competitors, fastest-first: a tree builder plus the find/select/serialize/:has ops. turbohtml leads each.
READPATH_LIBS: tuple[tuple[str, Callable[[str], object], tuple[Callable[..., None], ...]], ...] = (
    ("turbohtml", turbo_tree, (turbo_find, turbo_select, turbo_serialize, turbo_has_select)),
    ("lxml", lxml_tree, (lxml_find, lxml_select, lxml_serialize, lxml_has_select)),
    ("selectolax", lexbor_tree, (lexbor_find, lexbor_select, lexbor_serialize, lexbor_has_select)),
    ("BeautifulSoup", bs4_tree, (bs4_find, bs4_select, bs4_serialize, bs4_has_select)),
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


# Write-on-parsed-tree competitors; selectolax mutation is limited, so it is absent.
EDIT_LIBS: tuple[tuple[str, Callable[[str], object], Callable[..., None]], ...] = (
    ("turbohtml", turbo_tree, turbo_edit),
    ("lxml", lxml_tree, lxml_edit),
    ("BeautifulSoup", bs4_tree, bs4_edit),
)


def run_edit_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark a link-rewriting edit across every library; return the case names."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, build, edit in EDIT_LIBS:
            bench(f"edit {name} [{label}]", edit, build(text))
        names.append(name)
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


# --- markup suite: markupsafe-compatible escape on autoescape-realistic input #
# turbohtml.markup.escape against markupsafe's own C escape. The inputs are the
# small, mostly-clean strings a template engine interpolates under autoescape
# (markupsafe's hottest path), plus an escape-heavy fragment. Both return Markup,
# so the comparison includes the safe-string construction each pays per call.


def turbo_markup(text: str) -> None:
    """Escape with turbohtml.markup.escape, returning a Markup."""
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


def run_markup_suite(bench: Callable[[str, object, object], None]) -> list[str]:
    """Benchmark markupsafe-compatible escaping against markupsafe; return the case names."""
    for name, text in MARKUP_CASES:
        for label, escape in MARKUP_LIBS:
            bench(f"markup {name} [{label}]", escape, text)
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


def linkifyit_scan(text: str) -> None:
    """Scan plain text for links with linkify-it-py, which finds but does not rewrite."""
    _LINKIFY_IT.match(text)


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


def run_linkify_suite(bench: Callable[[str, object, object], None]) -> None:
    """Benchmark HTML-aware linkifying against bleach and linkify-it-py."""
    for name, text in LINKIFY_CASES:
        for label, run in LINKIFY_LIBS:
            bench(f"linkify {name} [{label}]", run, text)


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


SANITIZE_LIBS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("turbohtml", turbo_sanitize),
    ("nh3", nh3_sanitize),
    ("bleach", bleach_sanitize),
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


def run_readpath_suite(bench: Callable[[str, object, object], None], op_index: int, op: str) -> list[str]:
    """Benchmark one read-path operation (find/select/serialize) across every library."""
    names: list[str] = []
    for name, path, enc in READPATH_CASES:
        text = corpus_text(path, enc)
        for label, build, ops in READPATH_LIBS:
            bench(f"{op} {name} [{label}]", ops[op_index], build(text))
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


def print_table(means: dict[str, float], rows: list[tuple[str, str]]) -> None:
    """Render the speedup table from the collected per-benchmark means."""
    print()
    print(f"{'benchmark':40} {'turbohtml':>12} {'stdlib':>12} {'speedup':>8} {'html5lib':>12} {'speedup':>8}")
    for op, name in rows:
        turbo = means[f"{op} {name} [turbohtml]"]
        stdlib = means[f"{op} {name} [stdlib]"]
        row = f"{op + ' ' + name:40} {turbo * 1e6:9.2f} us {stdlib * 1e6:9.2f} us {stdlib / turbo:7.1f}x"
        if (five := means.get(f"{op} {name} [html5lib]")) is not None:
            row += f" {five * 1e6:9.2f} us {five / turbo:7.1f}x"
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
            "query",
            "serialize",
            "build",
            "edit",
            "markup",
            "linkify",
            "sanitize",
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
            "escape,unescape,tokenize,corpus,parse,query,serialize,build,edit,markup,linkify,sanitize",
        ).split(",")
    )
    means: dict[str, float] = {}

    def bench(name: str, func: object, arg: object) -> None:
        if (result := runner.bench_func(name, func, arg)) is not None and result.get_nvalue():
            means[name] = result.mean()

    rows = run_string_suites(bench, suites)
    parse_cases = run_parse_suite(bench) if "parse" in suites else []
    find_cases = run_readpath_suite(bench, 0, "find") if "query" in suites else []
    select_cases = run_readpath_suite(bench, 1, "select") if "query" in suites else []
    has_select_cases = run_readpath_suite(bench, 3, "select :has") if "query" in suites else []
    serialize_cases = run_readpath_suite(bench, 2, "serialize") if "serialize" in suites else []
    build_cases = run_build_suite(bench) if "build" in suites else []
    edit_cases = run_edit_suite(bench) if "edit" in suites else []
    markup_cases = run_markup_suite(bench) if "markup" in suites else []
    if "linkify" in suites:
        run_linkify_suite(bench)
    if "sanitize" in suites:
        run_sanitize_suite(bench)
    if args.worker or not means:
        return
    print_table(means, rows)
    print_parse_table(means, parse_cases)
    print_readpath_table(means, "find", find_cases)
    print_readpath_table(means, "select", select_cases)
    print_readpath_table(means, "select :has", has_select_cases)
    print_readpath_table(means, "serialize", serialize_cases)
    print_build_table(means, build_cases)
    print_edit_table(means, edit_cases)
    print_markup_table(means, markup_cases)
    print_linkify_table(means, LINKIFY_CASE_NAMES if "linkify" in suites else [])
    print_sanitize_table(means, [n for n, _ in SANITIZE_CASES] if "sanitize" in suites else [])


if __name__ == "__main__":
    main()
