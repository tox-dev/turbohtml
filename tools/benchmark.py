#!/usr/bin/env python3
"""
Benchmark turbohtml's escape/unescape/tokenize against the standard library.

Run with ``tox -e bench``; positional arguments pick the suites to run
(``escape``, ``unescape``, ``tokenize``, ``corpus`` — default all); remaining
arguments are forwarded to pyperf (pass ``--help`` to see them). pyperf runs
every case in isolated worker processes and reports mean ± stddev; the parent
process then prints a speedup table
against the standard library and, for tokenize, html5lib's pure-Python
tokenizer.

Besides the synthetic cases, tokenize also runs over html5lib's benchmark
corpus (the tools/html5lib-python submodule) — a slice of the WHATWG HTML spec
source plus a size-weighted sample of web-platform-tests pages, 0.6 kB to
234 kB — and two multi-megabyte real documents (the ECMAScript specification
and the full WHATWG HTML spec source), downloaded once from pinned revisions
and cached because their repositories are far too large to vendor.
"""

from __future__ import annotations

import html
import os
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import pyperf

import turbohtml

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


CASES: list[tuple[str, str, str]] = [
    ("escape", "plain prose, no specials", "the quick brown fox jumps over the lazy dog " * 80),
    ("escape", "typical HTML markup", '<p>Tom & Jerry said "hi" to <b>O\'Brien</b> & co.</p> ' * 60),
    ("escape", "special-dense", "<>&\"'" * 500),
    ("escape", "non-ASCII prose (UCS-2)", "résumé café naïve Москва " * 120),
    ("escape", "astral text (UCS-4)", "emoji \U0001f600 party \U0001f389 " * 120),
    ("unescape", "named references (dense)", "&amp;&lt;&gt;&quot;&copy;&mdash;&eacute; " * 60),
    ("unescape", "numeric references (dense)", "&#62;&#x3e;&#38;&#127881;&#x1F600; " * 60),
    ("unescape", "mixed named + numeric", "Tom &amp; Jerry &mdash; caf&eacute; &#127881; &lt;b&gt; " * 30),
    ("unescape", "prose, sparse references", ("the quick brown fox " * 20 + "&amp; ") * 12),
    ("unescape", "non-ASCII with references", "café &amp; résumé &copy; Москва &mdash; " * 60),
]

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


def stdlib_tokenize(text: str) -> None:
    """Drive the stdlib parser with its default no-op handlers."""
    parser = HTMLParser()
    parser.feed(text)
    parser.close()


def html5lib_tokenize(text: str) -> None:
    """Drive html5lib's tokenizer; skipped when html5lib is not installed."""
    from html5lib._tokenizer import (  # noqa: PLC0415, PLC2701  # optional dependency; the tokenizer is internal API
        HTMLTokenizer,
    )

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


def main() -> None:
    """Run all cases under pyperf and print the speedup table in the parent."""
    runner = pyperf.Runner()
    runner.argparser.add_argument(
        "suites",
        nargs="*",
        choices=["escape", "unescape", "tokenize", "corpus", []],
        help="suites to run (default: all)",
    )
    args = runner.parse_args()
    # pyperf rebuilds worker command lines from its own options only, so the
    # suite selection rides to the workers through the environment
    if args.suites:
        os.environ["TURBOHTML_BENCH_SUITES"] = ",".join(args.suites)
        args.inherit_environ = [*(args.inherit_environ or []), "TURBOHTML_BENCH_SUITES"]
    selection = os.environ.get("TURBOHTML_BENCH_SUITES", "escape,unescape,tokenize,corpus")
    suites = set(selection.split(","))
    means: dict[str, float] = {}

    def bench(name: str, func: object, arg: str) -> None:
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
    if args.worker or not means:
        return
    rows = [(op, name) for op, name, _ in CASES if op in suites]
    rows += [("tokenize", name) for name, _ in tokenize_cases]
    print_table(means, rows)


if __name__ == "__main__":
    main()
