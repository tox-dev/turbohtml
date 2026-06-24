"""
Operation metadata and shared inputs: the single source of truth for what is benchmarked.

``OPERATIONS`` (title plus time unit) is pure data the orchestrator and renderer read in any environment. ``INPUTS``
holds the cases lazily -- a callable per operation returning ``(case name, input)`` pairs -- so corpora load only inside
a worker that asks for them, never when the orchestrator imports this module. Both turbohtml's core timing and every
competitor consume the identical input for an operation, so a speedup is a like-for-like ratio. ``build``-family cases
are integer row counts; the rest are HTML strings or corpus documents.
"""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import TYPE_CHECKING

from bench import corpus

if TYPE_CHECKING:
    from collections.abc import Callable

_ROWS = (("100 rows", 100), ("1k rows", 1_000), ("10k rows", 10_000))

_SOCIAL_HEAD = dedent("""\
    <head>
      <meta property="og:title" content="Widget">
      <meta property="og:type" content="product">
      <meta property="og:image" content="https://x/i.png">
      <meta property="og:description" content="A small widget">
      <meta name="twitter:card" content="summary">
      <meta name="twitter:site" content="@x">
    </head>""")

_STRUCTURED_PAGE = dedent("""\
    <head>
      <meta property="og:title" content="Widget">
      <meta property="og:type" content="product">
      <meta property="og:image" content="https://x/i.png">
      <meta name="twitter:card" content="summary">
    </head>
    <body>
      <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Widget", "sku": "W-1",
         "offers": {"@type": "Offer", "price": "9.99", "priceCurrency": "USD", "availability": "InStock"}}
      </script>
      <div itemscope itemtype="https://schema.org/Product">
        <span itemprop="name">Widget</span>
        <meta itemprop="sku" content="W-1">
        <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
          <span itemprop="price">9.99</span>
          <meta itemprop="priceCurrency" content="USD">
          <link itemprop="availability" href="https://schema.org/InStock">
        </div>
      </div>
    </body>""")

_SANITIZE_POST = dedent("""\
    <div class=post>
      <h1>Title</h1>
      <p>Some <a href='http://example.com'>link</a> and <b>bold</b> text with
        <img src=http://x/i.png onerror=alert(1)> and <script>evil()</script>.</p>
      <ul><li>one</li><li>two</li></ul>
    </div>""")


@dataclass(frozen=True)
class Operation:
    """One benchmarked operation: its display title and the time unit (``ns``, ``us``, ``ms``) its table prints in."""

    title: str
    unit: str


OPERATIONS: dict[str, Operation] = {
    "build": Operation("build a list (constructors)", "us"),
    "build-e": Operation("build a list (terse builders)", "us"),
    "construct": Operation("construct N elements (no serialize)", "us"),
    "serialize": Operation("serialize a built tree", "us"),
    "parse": Operation("parse to a tree", "us"),
    "fragment": Operation("parse a fragment", "us"),
    "escape": Operation("escape", "us"),
    "unescape": Operation("unescape", "us"),
    "tokenize": Operation("tokenize", "us"),
    "socialcard": Operation("social-card extraction", "us"),
    "structured": Operation("structured-data extraction", "us"),
    "sanitize": Operation("sanitize", "us"),
}


def _parse_cases() -> tuple[tuple[str, object], ...]:
    """Return the corpus documents the parse suite runs over (loaded from the html5lib-python submodule)."""
    return tuple((name, corpus.corpus_text(relative, encoding)) for name, relative, encoding in corpus.CORPUS_FILES)


_TOKENIZE_CASES = (
    ("typical markup", '<div class="row"><p>Tom &amp; Jerry said "hi" to <b>O\'Brien</b>!</p><br/></div>\n' * 60),
    ("text-heavy prose", "<p>" + "the quick brown fox jumps over the lazy dog " * 100 + "</p>"),
    (
        "attribute-heavy",
        '<a href="https://example.com/path?q=1" title="example" rel="noopener" target="_blank" data-x=y>link</a>\n'
        * 60,
    ),
    ("script-heavy", "<script>function f(a, b) { return a < b && b > a; }</script>\n" * 60),
)

_FRAGMENT_HTML = "<tr><td>cell</td><td><a href='/x'>link</a></td></tr>" * 40


INPUTS: dict[str, Callable[[], tuple[tuple[str, object], ...]]] = {
    "build": lambda: _ROWS,
    "build-e": lambda: _ROWS[:2],
    "construct": lambda: _ROWS,
    "serialize": lambda: _ROWS,
    "parse": _parse_cases,
    "fragment": lambda: (("table-row fragment (2 kB)", _FRAGMENT_HTML),),
    "escape": corpus.escape_cases,
    "unescape": corpus.unescape_cases,
    "tokenize": lambda: _TOKENIZE_CASES,
    "socialcard": lambda: (
        ("head", _SOCIAL_HEAD),
        ("article 8 KiB", f"{_SOCIAL_HEAD}<body>{'<p>filler text</p>' * 400}</body>"),
    ),
    "structured": lambda: (("product", _STRUCTURED_PAGE), ("catalog 8 KiB", _STRUCTURED_PAGE * 12)),
    "sanitize": lambda: (
        ("comment", "<p>Thanks for the <a href='http://example.com'>link</a>! <script>evil()</script></p>"),
        ("post 4 KiB", _SANITIZE_POST * 20),
    ),
}
