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
    "emit": Operation("emit a built tree", "us"),
    "parse": Operation("parse to a tree", "us"),
    "fragment": Operation("parse a fragment", "us"),
    "escape": Operation("escape", "us"),
    "unescape": Operation("unescape", "us"),
    "tokenize": Operation("tokenize", "us"),
    "find": Operation("find every anchor", "us"),
    "select": Operation("select div a[href]", "us"),
    "select-has": Operation("select div:has(a)", "us"),
    "match": Operation("match each anchor against div a[href]", "us"),
    "find-text": Operation("find by text content", "us"),
    "text-content": Operation("collect visible text", "us"),
    "serialize": Operation("serialize a parsed tree", "us"),
    "minify": Operation("minify a document", "us"),
    "edit": Operation("tag every link rel=nofollow", "us"),
    "class-edit": Operation("class add/remove on every link", "us"),
    "strip-remove": Operation("drop tags with content (remove)", "us"),
    "strip-tags": Operation("unwrap tags keep content (strip_tags)", "us"),
    "set-html": Operation("replace body inner HTML", "us"),
    "set-text": Operation("replace body text", "us"),
    "navigate": Operation("walk every descendant", "us"),
    "chain": Operation("fluent jQuery-style chain", "us"),
    "links-extract": Operation("extract every link", "us"),
    "links-absolutize": Operation("absolutize every link", "us"),
    "links-rewrite": Operation("rewrite every link", "us"),
    "socialcard": Operation("social-card extraction", "us"),
    "structured": Operation("structured-data extraction", "us"),
    "sanitize": Operation("sanitize", "us"),
    "markup": Operation("markupsafe-compatible escape", "ns"),
    "markup-op": Operation("Markup operations", "ns"),
    "linkify": Operation("linkify HTML", "us"),
    "detect": Operation("detect links in text", "us"),
    "markdown": Operation("HTML to Markdown", "us"),
    "markdown-google": Operation("Google Docs export to Markdown", "us"),
    "tables": Operation("extract table grids", "us"),
    "article": Operation("article extraction", "us"),
    "text-render": Operation("layout-aware text", "us"),
    "text-collapsed": Operation("collapsed word stream", "us"),
    "text-main": Operation("main-content text", "us"),
    "text-annotated": Operation("annotated layout text", "us"),
    "extract-attr": Operation("extract @href per match", "us"),
    "extract-text": Operation("extract text per match", "us"),
    "extract-url": Operation("extract URL hints", "us"),
    "htmlparser": Operation("feed and dispatch a page", "us"),
    "path": Operation("css_path for every element", "us"),
    "path-xpath": Operation("xpath_path for every element", "us"),
    "xpath": Operation("XPath feature surface (9.6 kB)", "us"),
    "minify-css": Operation("minify CSS", "us"),
    "minify-js": Operation("minify a JS library", "ms"),
}


def _parse_cases() -> tuple[tuple[str, object], ...]:
    """Return the corpus documents the parse suite runs over (loaded from the html5lib-python submodule)."""
    return tuple((name, corpus.corpus_text(relative, encoding)) for name, relative, encoding in corpus.CORPUS_FILES)


def _readpath_cases() -> tuple[tuple[str, object], ...]:
    """
    Return the pages the read-path operations parse once then query.

    Real saved web pages (a blog, a news article, a product blog) spanning 10-95 kB, plus the whatwg spec at 235 kB.
    The wpt fixtures they replace are CSS layout tests with no nested ``div``/``a`` or links, so ``div a[href]``,
    ``div:has(a)``, and the link/edit/chain/extract operations matched nothing and timed empty walks; these carry the
    real structure those operations exist to traverse.
    """
    pages = [(name, corpus.large_text(filename, url)) for name, filename, url in corpus.REAL_PAGES]
    label, relative, encoding = corpus.CORPUS_FILES[5]  # whatwg spec (235 kB), the large content page
    pages.append((label, corpus.corpus_text(relative, encoding)))
    return tuple(pages)


_TOKENIZE_CASES = (
    ("typical markup", '<div class="row"><p>Tom &amp; Jerry said "hi" to <b>O\'Brien</b>!</p><br/></div>\n' * 60),
    ("text-heavy prose", "<p>" + "the quick brown fox jumps over the lazy dog " * 100 + "</p>"),
    (
        "attribute-heavy",
        '<a href="https://example.com/path?q=1" title="example" rel="noopener" target="_blank" data-x=y>link</a>\n'
        * 60,
    ),
    ("script-heavy", "<script>function f(a, b) { return a < b && b > a; }</script>\n" * 60),
    ("entity-heavy", "<p>caf&eacute; &amp; r&eacute;sum&eacute; &#127881; &lt;tag&gt;</p>\n" * 60),
)

_FRAGMENT_HTML = "<tr><td>cell</td><td><a href='/x'>link</a></td></tr>" * 40

_MARKUP_ESCAPE_CASES = (
    ("clean (8 B)", "a value!"),
    ("clean (32 B)", "The quick brown fox jumped ok"),
    ("clean (256 B)", "The quick brown fox jumps over the lazy dog. " * 6),
    ("name with ' and &", "O'Brien & Sons"),
    ("escape-heavy markup", '<a href="/x?a=1&b=2">click & go</a>' * 2),
)

_MARKUP_OPS_HTML = "<p>Hello <b>bold</b> &amp; <i>italic</i>, see <a href='/x'>caf&eacute;</a> &#127881;</p>"
_MARKUP_FORMAT_ARGS = ("<script>alert(1)</script>", "Tom & Jerry")
_MARKUP_JOIN_PARTS = ("<a href='/x'>link</a>", "Tom & Jerry", "<b>bold</b>", "plain text")

_LINKIFY_CASES = (
    ("comment (1 link, 1 email)", "Ping me at bob@example.com or see https://example.com for details."),
    ("prose (1 KiB)", "See https://example.com/path?q=1 and visit www.example.org for more. " * 15),
    ("markup (4 KiB)", '<p>Read <a href="https://kept.example">the post</a> then go to https://example.com/x. ' * 45),
)

_MARKDOWN_ARTICLE = "<h2>Heading</h2><p>A <b>bold</b> <a href='/x'>link</a> and <code>code</code>.</p>" * 18
_MARKDOWN_LIST = "<ul><li>item <em>one</em></li><li>item two<ul><li>nested</li></ul></li></ul>" * 40
_MARKDOWN_TABLE = "<table><tr><th>Name</th><th>Value</th></tr><tr><td>a</td><td>1</td></tr></table>" * 35
_MARKDOWN_CONFIGURED = (
    "<h2>H</h2><p>A <b>b</b> & <a href='/x'>l</a>.</p>"
    "<table><tr><th>K</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>"
) * 18
_MARKDOWN_GOOGLE = (
    '<p><span style="font-weight:700">Bold</span> and <span style="font-style:italic">italic</span> and '
    "<span style=\"font-family:'Courier New'\">code()</span> in a line.</p>"
) * 18

_TEXT_ARTICLE = "<h2>Heading</h2><p>A paragraph of plain prose with a <a href='/x'>link</a> in it.</p>" * 16
_TEXT_TABLE = "<table><tr><th>Region</th><th>Total</th></tr><tr><td>North</td><td>120</td></tr></table>" * 30
_TEXT_MAIN = (
    "<html><head><title>Comets</title></head><body>"
    "<nav><a href='/'>Home</a> <a href='/science'>Science</a></nav>"
    "<article><h1>Comets</h1>"
    + (
        "<p>A comet is an icy small body that, when it passes close to the Sun, warms up and releases gases, forming a "
        "glowing coma around it.</p>" * 12
    )
    + "</article><footer><p>Copyright notice, all rights reserved here.</p></footer></body></html>"
)
_TEXT_ANNOTATED = "<h1>Q3</h1><p>Up <b>12%</b> with a <a href='/x'>link</a> in prose.</p>" * 16

_URL_HINT_HTML = (
    "<html><head><base href='/sub/'>"
    "<meta http-equiv='refresh' content='5; url=next.html'>"
    "<title>Doc</title></head><body><p>Body copy.</p></body></html>"
)

_SVG_FRAGMENT = "<svg><rect/><rect/></svg>"
_XPATH_FEATURES = (
    "//div",
    "//a[@href]",
    "//div//a[@href]",
    "/html/body/div",
    "//div//a[1]",
    "//a[contains(@href, '/')]",
    "//div[position() <= 3]",
    "//a/ancestor::div",
    "//a | //span",
    "//*[local-name() = 'a']",
    "count(//a)",
)
_XPATH_PARITY = (
    ("//a[@href=$x] (variable)", "variable"),
    ("//a[re:test(@href, ...)] (EXSLT)", "re:test"),
    ("set:distinct(//a) (EXSLT)", "set:distinct"),
    ("//a/@href (smart_strings)", "smart_strings"),
    ("ext(//a) (extensions)", "extension"),
    ("ext(//a)/@href (node-set extension)", "nodeset_extension"),
    ("//svg:rect (namespaces=)", "namespaces"),
    ("$rows/div (node-set variable)", "node_set_variable"),
    ("//a[@href] (precompiled, reused)", "precompiled"),
)


def _table_html(data_rows: int) -> str:
    """Build a header plus ``data_rows`` four-column body rows, with a colspan to exercise span resolution."""
    header = "<tr><th>Region</th><th>Quarter</th><th>Revenue</th><th>Units</th></tr>"
    body = "".join(
        f"<tr><td>R{index}</td><td>Q{index % 4 + 1}</td><td colspan=2>{index * 10}</td></tr>"
        for index in range(data_rows)
    )
    return f"<table>{header}{body}</table>"


def _article_page(paragraphs: int) -> str:
    """Build a full page -- navigation, a scored article of ``paragraphs`` paragraphs, and a footer."""
    head = (
        "<html lang=en><head><title>Comets: A Field Guide</title>"
        "<meta name=author content='Ada Lovelace'>"
        "<meta property=article:published_time content='2024-05-06'>"
        "<meta name=description content='A short guide to comets and the tails they trail past the Sun.'></head>"
    )
    nav = "<body><nav><a href='/'>Home</a> <a href='/science'>Science</a> <a href='/space'>Space</a></nav>"
    para = (
        "<p>A comet is an icy small body that, when it passes close to the Sun, warms up, begins to release gases, "
        "and forms a glowing coma, a thin atmosphere, around it.</p>"
    )
    article = f"<article class=post><h1>Comets</h1>{para * paragraphs}</article>"
    return f"{head}{nav}{article}<footer><p>Copyright notice, all rights reserved here.</p></footer></body></html>"


def _xpath_cases() -> tuple[tuple[str, object], ...]:
    """Return one (label, (kind, text)) pair per XPath feature over the 9.6 kB page; the namespaced row carries SVG."""
    _name, relative, encoding = corpus.CORPUS_FILES[2]
    text = corpus.corpus_text(relative, encoding)
    structural = tuple((feature, (feature, text)) for feature in _XPATH_FEATURES)
    parity = tuple(
        (label, (kind, text + _SVG_FRAGMENT if kind == "namespaces" else text)) for label, kind in _XPATH_PARITY
    )
    return structural + parity


def _tokenize_cases() -> tuple[tuple[str, object], ...]:
    """Return synthetic and corpus documents for the tokenization table."""
    corpus_cases = tuple(
        (name, corpus.corpus_text(relative, encoding)) for name, relative, encoding in corpus.CORPUS_FILES
    )
    large_cases = tuple((name, corpus.large_text(filename, url)) for name, filename, url in corpus.LARGE_FILES)
    return _TOKENIZE_CASES + corpus_cases + large_cases


def _minify_cases() -> tuple[tuple[str, object], ...]:
    """Return the real-world stylesheets the CSS-minify suite runs over (fetched and cached on first use)."""
    return tuple((name, corpus.large_text(filename, url)) for name, filename, url in corpus.STYLESHEETS)


def _minify_js_cases() -> tuple[tuple[str, object], ...]:
    """Return the real-world JavaScript libraries the minify operation shrinks, a size ladder."""
    return tuple((name, corpus.large_text(filename, url)) for name, filename, url in corpus.JS_FILES)


INPUTS: dict[str, Callable[[], tuple[tuple[str, object], ...]]] = {
    "build": lambda: _ROWS,
    "build-e": lambda: _ROWS,
    "construct": lambda: _ROWS,
    "emit": lambda: _ROWS,
    "parse": _parse_cases,
    "fragment": lambda: (("table-row fragment (2 kB)", _FRAGMENT_HTML),),
    "escape": corpus.escape_cases,
    "unescape": corpus.unescape_cases,
    "tokenize": _tokenize_cases,
    "edit": _readpath_cases,
    "class-edit": _readpath_cases,
    "strip-remove": _readpath_cases,
    "strip-tags": _readpath_cases,
    "set-html": _readpath_cases,
    "set-text": _readpath_cases,
    "navigate": _readpath_cases,
    "chain": _readpath_cases,
    "links-extract": _readpath_cases,
    "links-absolutize": _readpath_cases,
    "links-rewrite": _readpath_cases,
    "find": _readpath_cases,
    "select": _readpath_cases,
    "select-has": _readpath_cases,
    "match": _readpath_cases,
    "find-text": _readpath_cases,
    "text-content": _readpath_cases,
    "serialize": _readpath_cases,
    "minify": _readpath_cases,
    "socialcard": lambda: (
        ("head", _SOCIAL_HEAD),
        ("article 8 KiB", f"{_SOCIAL_HEAD}<body>{'<p>filler text</p>' * 400}</body>"),
    ),
    "structured": lambda: (("product", _STRUCTURED_PAGE), ("catalog 8 KiB", _STRUCTURED_PAGE * 12)),
    "sanitize": lambda: (
        ("comment", "<p>Thanks for the <a href='http://example.com'>link</a>! <script>evil()</script></p>"),
        ("post 4 KiB", _SANITIZE_POST * 20),
    ),
    "markup": lambda: _MARKUP_ESCAPE_CASES,
    "markup-op": lambda: (
        ("striptags", ("striptags", _MARKUP_OPS_HTML)),
        ("unescape", ("unescape", _MARKUP_OPS_HTML)),
        ("format (escapes operands)", ("format", _MARKUP_FORMAT_ARGS)),
        ("join (escapes operands)", ("join", _MARKUP_JOIN_PARTS)),
    ),
    "linkify": lambda: _LINKIFY_CASES,
    "detect": lambda: (
        ("find comment (1 link, 1 email)", ("find", _LINKIFY_CASES[0][1])),
        ("find prose (1 KiB)", ("find", _LINKIFY_CASES[1][1])),
        ("has_link comment", ("has", _LINKIFY_CASES[0][1])),
        ("has_link prose (1 KiB)", ("has", _LINKIFY_CASES[1][1])),
    ),
    "markdown": lambda: (
        ("article (2 KiB)", ("default", _MARKDOWN_ARTICLE)),
        ("list (4 KiB)", ("default", _MARKDOWN_LIST)),
        ("table (4 KiB)", ("default", _MARKDOWN_TABLE)),
        ("configured (4 KiB)", ("configured", _MARKDOWN_CONFIGURED)),
    ),
    "markdown-google": lambda: (("google_doc (4 KiB)", _MARKDOWN_GOOGLE),),
    "tables": lambda: (
        ("rows (10 rows)", ("rows", _table_html(10))),
        ("records (10 rows)", ("records", _table_html(10))),
        ("rows (100 rows)", ("rows", _table_html(100))),
        ("records (100 rows)", ("records", _table_html(100))),
        ("rows (1000 rows)", ("rows", _table_html(1_000))),
        ("records (1000 rows)", ("records", _table_html(1_000))),
    ),
    "article": lambda: (("post (4 KiB)", _article_page(16)), ("longform (16 KiB)", _article_page(72))),
    "text-render": lambda: (("article (2 KiB)", _TEXT_ARTICLE), ("table (4 KiB)", _TEXT_TABLE)),
    "text-collapsed": lambda: (("collapsed (2 KiB)", _TEXT_ARTICLE),),
    "text-main": lambda: (("main (4 KiB)", _TEXT_MAIN),),
    "text-annotated": lambda: (("annotated (4 KiB)", _TEXT_ANNOTATED),),
    "extract-attr": _readpath_cases,
    "extract-text": _readpath_cases,
    "extract-url": lambda: (
        ("base_url / get_base_url", ("base", _URL_HINT_HTML)),
        ("meta_refresh / get_meta_refresh", ("refresh", _URL_HINT_HTML)),
    ),
    "htmlparser": _readpath_cases,
    "path": _readpath_cases,
    "path-xpath": _readpath_cases,
    "xpath": _xpath_cases,
    "minify-css": _minify_cases,
    "minify-js": _minify_js_cases,
}
