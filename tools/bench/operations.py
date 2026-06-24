"""
Operation metadata and shared inputs: the single source of truth for what is benchmarked.

Pure data, standard library only -- every layer (orchestrator, worker, renderer) imports this, in environments that may
hold neither turbohtml nor any competitor. Each :class:`Operation` carries its display title, time unit, and the ordered
cases every implementation runs on, so a speedup is always a like-for-like ratio over identical input.

Operations come at two granularities, both feeding the documentation tables. The aggregate ones (``build``, ``build-e``)
time a realistic workload swept by size -- building an N-row list and emitting it. The method-level ones (``construct``,
``serialize``) isolate a single API call so the migration tables can attribute cost method by method rather than to a
mixed pipeline. ``build``/``construct``/``serialize`` cases are integer row counts; the extraction and sanitize cases
are HTML strings (never a parsed tree, so this module imports anywhere).
"""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

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
    """One benchmarked operation: how its table is labeled and the inputs every implementation runs on."""

    title: str
    unit: str
    cases: tuple[tuple[str, object], ...]


OPERATIONS: dict[str, Operation] = {
    "build": Operation("build a list (constructors)", "us", _ROWS),
    "build-e": Operation("build a list (terse builders)", "us", _ROWS[:2]),
    "construct": Operation("construct N elements (no serialize)", "us", _ROWS),
    "serialize": Operation("serialize a built tree", "us", _ROWS),
    "socialcard": Operation(
        "social-card extraction",
        "us",
        (("head", _SOCIAL_HEAD), ("article 8 KiB", f"{_SOCIAL_HEAD}<body>{'<p>filler text</p>' * 400}</body>")),
    ),
    "structured": Operation(
        "structured-data extraction", "us", (("product", _STRUCTURED_PAGE), ("catalog 8 KiB", _STRUCTURED_PAGE * 12))
    ),
    "sanitize": Operation(
        "sanitize",
        "us",
        (
            ("comment", "<p>Thanks for the <a href='http://example.com'>link</a>! <script>evil()</script></p>"),
            ("post 4 KiB", _SANITIZE_POST * 20),
        ),
    ),
}
