"""
Operation metadata and shared inputs: the single source of truth for what is benchmarked.

Pure data, standard library only — every layer (orchestrator, worker, renderer) imports this, in environments that may
hold neither turbohtml nor any competitor. Each :class:`Operation` carries its display title, time unit, and the ordered
cases both turbohtml and every competitor are timed on, so a speedup is always a like-for-like ratio over identical
input. ``build`` cases are integer row counts; the rest are HTML strings -- never a parsed tree, so this imports
anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    """One benchmarked operation: how its table is labeled and the inputs every implementation runs on."""

    title: str
    unit: str
    cases: tuple[tuple[str, object], ...]


_SOCIAL_HEAD = (
    '<head><meta property="og:title" content="Widget"><meta property="og:type" content="product">'
    '<meta property="og:image" content="https://x/i.png"><meta property="og:description" content="A small widget">'
    '<meta name="twitter:card" content="summary"><meta name="twitter:site" content="@x"></head>'
)

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

_SANITIZE_POST = (
    "<div class=post><h1>Title</h1><p>Some <a href='http://example.com'>link</a> and <b>bold</b> text with "
    "<img src=http://x/i.png onerror=alert(1)> and <script>evil()</script>.</p><ul><li>one</li><li>two</li></ul></div>"
)

OPERATIONS: dict[str, Operation] = {
    "build": Operation(
        "build a list (constructors)", "us", (("100 rows", 100), ("1k rows", 1_000), ("10k rows", 10_000))
    ),
    "build-e": Operation("build a list (terse builders)", "us", (("100 rows", 100), ("1k rows", 1_000))),
    "socialcard": Operation(
        "social-card extraction",
        "us",
        (
            ("card", f"{_SOCIAL_HEAD}<body><p>intro</p></body>"),
            ("article 8 KiB", f"{_SOCIAL_HEAD}<body>{'<p>filler text</p>' * 400}</body>"),
        ),
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
