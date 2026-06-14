#!/usr/bin/env python3
"""
Benchmark turbohtml's escape/unescape/tokenize/parse against other libraries.

Run with ``tox -e bench``; positional arguments pick the suites to run
(``escape``, ``unescape``, ``tokenize``, ``corpus``, ``parse``; default all).
Remaining arguments are forwarded to pyperf (pass ``--help`` to see them).
pyperf runs every case in isolated worker processes and reports mean and
stddev; the parent process then prints a speedup table: escape, unescape, and
tokenize against the standard library, and parse against the other HTML tree
builders (lexbor through selectolax, lxml, html5lib, and BeautifulSoup).

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

import html5lib
import pyperf
from bs4 import BeautifulSoup
from lxml import html as lxml_html
from selectolax.lexbor import LexborHTMLParser

import turbohtml

if TYPE_CHECKING:
    from collections.abc import Callable

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


def main() -> None:
    """Run all cases under pyperf and print the speedup table in the parent."""
    runner = pyperf.Runner()
    runner.argparser.add_argument(
        "suites",
        nargs="*",
        choices=["escape", "unescape", "tokenize", "corpus", "parse", []],
        help="suites to run (default: all)",
    )
    args = runner.parse_args()
    # pyperf rebuilds worker command lines from its own options only, so the
    # suite selection rides to the workers through the environment
    if args.suites:
        os.environ["TURBOHTML_BENCH_SUITES"] = ",".join(args.suites)
        args.inherit_environ = [*(args.inherit_environ or []), "TURBOHTML_BENCH_SUITES"]
    selection = os.environ.get("TURBOHTML_BENCH_SUITES", "escape,unescape,tokenize,corpus,parse")
    suites = set(selection.split(","))
    means: dict[str, float] = {}

    def bench(name: str, func: object, arg: object) -> None:
        if (result := runner.bench_func(name, func, arg)) is not None and result.get_nvalue():
            means[name] = result.mean()

    for op, name, arg in CASES:
        if op in suites:
            bench(f"{op} {name} [turbohtml]", getattr(turbohtml, op), arg)
            bench(f"{op} {name} [stdlib]", getattr(html, op), arg)
    has_html5lib = _has_html5lib()
    tokenize_cases = TOKENIZE_CASES if "tokenize" in suites else []
    if "corpus" in suites:
        tokenize_cases += [(name, corpus_text(path, enc)) for name, path, enc in CORPUS_FILES]
        tokenize_cases += [(name, large_text(filename, url)) for name, filename, url in LARGE_FILES]
    for name, arg in tokenize_cases:
        bench(f"tokenize {name} [turbohtml]", turbo_tokenize, arg)
        bench(f"tokenize {name} [stdlib]", stdlib_tokenize, arg)
        if has_html5lib:
            bench(f"tokenize {name} [html5lib]", html5lib_tokenize, arg)
    parse_cases = run_parse_suite(bench) if "parse" in suites else []
    if args.worker or not means:
        return
    rows = [(op, name) for op, name, _ in CASES if op in suites]
    rows += [("tokenize", name) for name, _ in tokenize_cases]
    print_table(means, rows)
    print_parse_table(means, parse_cases)


if __name__ == "__main__":
    main()
