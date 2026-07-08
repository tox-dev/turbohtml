"""
The offline training corpus for the profile-guided build, split from the CodSpeed benchmark corpus.

The release wheel trains the profile-guided build on this module. Every input is local -- vendored under a submodule or
generated in process -- so the wheel build collects the whole profile even in an offline cibuildwheel sandbox, where the
network fetches :mod:`bench.corpus` performs for the CodSpeed suite fail. Keeping this corpus separate from
:func:`bench.ci.benchmarks` (the inputs the LTO-only CodSpeed gate measures) is what lets :mod:`pgo_validate` measure
the profiled wheel on held-out pages the profile never trained on.

Representativeness is code-path coverage, not input volume: the compiler lays out blocks and inlines from the branches a
run takes, so each operation trains over a *set* spanning its branch classes rather than one clean document. The read
path, the tokenizer, and the tree builder see clean markup (the whatwg spec and the wpt fixtures), deliberate tag soup
(the html5lib-tests tree-construction fragments, which fire the adoption-agency, foster-parenting, and
foreign-content-breakout recovery the clean spec never reaches), and real saved pages (the vendored mozilla/readability
corpus). Encoding detection sees legacy multi-byte streams (Shift-JIS, GBK, EUC-KR, windows-1251/1252, UTF-16 with and
without a BOM, plus the real Japanese and Big5 html5lib samples). The extractors see structured-data markup in all four
syntaxes (JSON-LD, microdata, RDFa, OpenGraph), and the minifiers see real vendored stylesheets and scripts. Operations
with no corpus (the tree builders, escapers, URL and selector helpers) reuse the bench's own offline inline cases.
"""

from __future__ import annotations

import sys
from functools import cache, partial
from pathlib import Path
from typing import TYPE_CHECKING, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the bench package sits beside this script

from bench import corpus
from bench.core import OPERATIONS
from bench.operations import INPUTS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_TOOLS: Final = Path(__file__).resolve().parent
_ROOT: Final = _TOOLS.parent
_BENCH_DATA: Final = _TOOLS / "bench-data"
_TREE_CONSTRUCTION: Final = _ROOT / "tests" / "html5lib-tests" / "tree-construction"
_ENCODING_SAMPLES: Final = _ROOT / "tests" / "html5lib-tests" / "encoding"

_SLICE: Final = 1 << 16  # 64 kB of book text: enough work per call without a multi-megabyte parse in the profile


def _safe(load: Callable[[], list[object]]) -> list[object]:
    """Return ``load()``'s inputs, or an empty list when its vendored source is not checked out."""
    try:
        return load()
    except OSError:  # a submodule is absent, so this branch class simply goes untrained on a bare checkout
        return []


@cache
def _clean_documents() -> tuple[str, ...]:
    """Return the vendored clean markup: the wpt size ladder (0.6 to 92 kB, one Big5) and the whatwg spec (235 kB)."""
    return tuple(corpus.corpus_text(relative, encoding) for _name, relative, encoding in corpus.CORPUS_FILES)


def _data_fragments(dat: Path) -> Iterator[str]:
    """Yield each ``#data`` payload from an html5lib-tests ``.dat`` file (the deliberate tag-soup fragment)."""
    collecting = False
    payload: list[str] = []
    for line in dat.read_text(encoding="utf-8").splitlines():
        if line == "#data":
            collecting, payload = True, []
        elif line == "#errors":
            if collecting:
                yield "\n".join(payload)
            collecting = False
        elif collecting:
            payload.append(line)


_SOUP_BUCKETS: Final = 4


@cache
def _tag_soup() -> tuple[str, ...]:
    """Return the tree-construction fragments concatenated into a few documents that drive the recovery paths."""
    fragments = [fragment for dat in sorted(_TREE_CONSTRUCTION.glob("*.dat")) for fragment in _data_fragments(dat)]
    if not fragments:
        return ()  # the html5lib-tests submodule is not checked out
    buckets = [""] * _SOUP_BUCKETS
    for index, fragment in enumerate(fragments):
        buckets[index % _SOUP_BUCKETS] += f"{fragment}\n"
    return tuple(buckets)


_TRAINING_PAGES: Final = ("bbc-1", "blogger", "buzzfeed-1", "aktualne")  # disjoint from the bench and held-out pages


def real_pages(names: tuple[str, ...]) -> list[str]:
    """Return the named mozilla/readability saved pages; :mod:`pgo_validate` reads a held-out set through this."""
    base = _BENCH_DATA / "readability" / "test" / "test-pages"
    return [(base / name / "source.html").read_text(encoding="utf-8", errors="replace") for name in names]


def _html_documents() -> list[object]:
    """Return the parse/read-path input set: clean spec and fixtures, deliberate tag soup, and real saved pages."""
    return [*_clean_documents(), *_tag_soup(), *_safe(lambda: list(real_pages(_TRAINING_PAGES)))]


def _book(relative: str) -> str:
    """Return a 64 kB latin-1 slice of a vendored War-and-Peace document."""
    return corpus.corpus(relative, _SLICE)


def _escape_inputs() -> list[object]:
    """Return the escaper's input set: real book prose and HTML, plus the entity-dense spec."""
    return [
        *_safe(lambda: [_book("war-and-peace/2600.txt"), _book("war-and-peace/2600-h/2600-h.htm")]),
        *_clean_documents()[-1:],
    ]


_JAPANESE: Final = "日本語のテキストをここに書きます。今日はとても良い天気ですね。桜が咲いています。"
_CHINESE: Final = "这是一段中文文本用来测试字符编码的自动检测功能。今天天气很好。"
_KOREAN: Final = "이것은 문자 인코딩 자동 감지를 시험하기 위한 한국어 문장입니다. 오늘 날씨가 좋습니다."
_RUSSIAN: Final = "Программирование помогает понять структуру вычислительных систем сегодня здесь снова."
_FRENCH: Final = "Précédemment, la créativité française était très développée près de Paris ici et là."


def _encoding_streams() -> list[object]:
    """Return the byte streams the encoding sniffer trains on: legacy multi-byte, UTF-16 with a BOM, real samples."""
    streams: list[object] = [
        (_JAPANESE * 20).encode("shift_jis"),
        (_CHINESE * 20).encode("gbk"),
        (_KOREAN * 20).encode("euc_kr"),
        (_RUSSIAN * 20).encode("cp1251"),
        (_FRENCH * 20).encode("cp1252"),
        (_RUSSIAN * 20).encode("utf-16-le"),  # BOM-less little-endian
        (_JAPANESE * 20).encode("utf-16"),  # BOM-prefixed, native endianness
    ]
    samples = (_ENCODING_SAMPLES / "test-yahoo-jp.dat", _ENCODING_SAMPLES / "chardet" / "test_big5.txt")
    streams.extend(sample.read_bytes() for sample in samples if sample.exists())
    return streams


_JSONLD_PAGE: Final = """<!doctype html><html><head><title>Recipe</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Recipe","name":"Pancakes","author":{"@type":"Person","name":"Ada"},
"datePublished":"2024-03-01","recipeIngredient":["flour","milk","eggs"],
"aggregateRating":{"@type":"AggregateRating","ratingValue":"4.6","reviewCount":"84"}}
</script></head><body><h1>Pancakes</h1><p>A short recipe.</p></body></html>"""

_MICRODATA_PAGE: Final = """<!doctype html><html><body>
<div itemscope itemtype="https://schema.org/Product">
  <span itemprop="name">Widget</span><meta itemprop="sku" content="W-1">
  <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
    <span itemprop="price">9.99</span><meta itemprop="priceCurrency" content="USD">
    <link itemprop="availability" href="https://schema.org/InStock"></div>
</div></body></html>"""

_RDFA_PAGE: Final = """<!doctype html><html prefix="og: https://ogp.me/ns# schema: https://schema.org/">
<body vocab="https://schema.org/" typeof="Article">
  <h1 property="headline">A Field Guide</h1>
  <span property="author" typeof="Person"><span property="name">Ada Lovelace</span></span>
  <meta property="datePublished" content="2024-05-06">
  <div property="articleBody">Body copy with <a property="url" href="/x">a link</a>.</div>
</body></html>"""

_OPENGRAPH_PAGE: Final = """<!doctype html><html><head>
<meta property="og:title" content="Widget"><meta property="og:type" content="product">
<meta property="og:image" content="https://example.com/i.png">
<meta property="og:description" content="A small widget for every desk.">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:site" content="@example">
<title>Widget</title></head><body><p>Body.</p></body></html>"""


@cache
def _structured_pages() -> tuple[str, ...]:
    """Return synthetic pages carrying JSON-LD, microdata, RDFa, and OpenGraph, one per extractor syntax."""
    return (_JSONLD_PAGE, _MICRODATA_PAGE, _RDFA_PAGE, _OPENGRAPH_PAGE)


def _inline(operation: str) -> list[object]:
    """Return the cases the offline bench already defines inline for ``operation`` (no corpus, no network)."""
    return [case for _label, case in INPUTS[operation]()]


def _structured_inputs() -> list[object]:
    """Return the structured-data extractor's set: the four synthetic syntaxes, the inline cases, and a real page."""
    return [*_structured_pages(), *_inline("structured"), *_safe(lambda: list(real_pages(_TRAINING_PAGES[:1])))]


def _socialcard_inputs() -> list[object]:
    """Return the social-card extractor's set: the OpenGraph page, the inline cases, and a real page."""
    return [_OPENGRAPH_PAGE, *_inline("socialcard"), *_safe(lambda: list(real_pages(_TRAINING_PAGES[:1])))]


def _sanitize_inputs() -> list[object]:
    """Return the sanitizer's set: deliberate tag soup (its real input), the inline cases, and a real page."""
    return [*_tag_soup(), *_inline("sanitize"), *_safe(lambda: list(real_pages(_TRAINING_PAGES[:1])))]


def _article_inputs() -> list[object]:
    """Return the article/boilerplate/date extractors' set: the synthetic pages plus real saved pages."""
    return [*_inline("article"), *_safe(lambda: list(real_pages(_TRAINING_PAGES)))]


def _stylesheets() -> list[object]:
    """Return the vendored stylesheets the CSS minifier trains on: a 6 kB reset and a 93 kB framework."""
    files = (_BENCH_DATA / "normalize.css" / "normalize.css", _BENCH_DATA / "pico" / "css" / "pico.css")
    return [path.read_text(encoding="utf-8") for path in files if path.exists()]


def _scripts() -> list[object]:
    """Return the vendored scripts the JS minifier trains on: two ES5 libraries, underscore and backbone."""
    files = (_BENCH_DATA / "underscore" / "underscore.js", _BENCH_DATA / "backbone" / "backbone.js")
    return [path.read_text(encoding="utf-8") for path in files if path.exists()]


_HTML_DOCUMENT_OPS: Final = frozenset({
    "parse", "parse-shadow", "tokenize", "sax", "rewrite", "fragment", "serialize", "minify", "htmlparser",
    "path", "path-xpath", "find", "select", "select-has", "match", "find-text", "text-content",
    "edit", "class-edit", "strip-remove", "strip-tags", "set-html", "set-text", "observe", "navigate", "chain",
    "lossless-serialize", "links-extract", "links-absolutize", "links-rewrite", "extract-attr", "extract-text",
    "links-filter",
})  # fmt: skip

_SPECIAL: Final[dict[str, Callable[[], list[object]]]] = {
    "escape": _escape_inputs,
    "encoding": _encoding_streams,
    "structured": _structured_inputs,
    "socialcard": _socialcard_inputs,
    "sanitize": _sanitize_inputs,
    "article": _article_inputs,
    "boilerplate": _article_inputs,
    "date": _article_inputs,
    "minify-css": _stylesheets,
    "minify-js": _scripts,
}


def loader_for(operation: str) -> Callable[[], list[object]]:
    """Return the loader yielding ``operation``'s training input set: an HTML document set, a special set, or inline."""
    if operation in _HTML_DOCUMENT_OPS:
        return _html_documents
    if operation in _SPECIAL:
        return _SPECIAL[operation]
    return partial(_inline, operation)


def training_corpus() -> Iterator[tuple[str, object, Callable[[], list[object]]]]:
    """Yield ``(name, callable, load)`` for every operation, in registry order, over its offline training input set."""
    for name, (run, _owner) in OPERATIONS.items():
        yield name, run, loader_for(name)


__all__ = [
    "loader_for",
    "real_pages",
    "training_corpus",
]
