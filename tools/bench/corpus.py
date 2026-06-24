"""
Real and generated corpora for the parse, string, and read-path operations.

Standard library only, so it loads in any worker venv. Documents come from two vendored submodules under ``tools/`` --
the html5lib-python wpt sample and Project Gutenberg's War and Peace -- plus two multi-megabyte specs downloaded and
cached on first use (too large to vendor). The generated escape/unescape inputs are seeded, so every run is identical.
"""

from __future__ import annotations

import html
import random
import urllib.request
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent

CORPUS_DIR = _TOOLS / "html5lib-python" / "benchmarks" / "data"
CORPUS_FILES: tuple[tuple[str, str, str], ...] = (
    ("wpt tiny (0.6 kB)", "wpt/weighted/toBlob.png.html", "utf-8"),
    ("wpt small (4 kB)", "wpt/weighted/align-content-wrap-002.html", "utf-8"),
    ("wpt medium (9.6 kB)", "wpt/weighted/grid-auto-fill-rows-001.html", "utf-8"),
    ("wpt large (92 kB)", "wpt/weighted/test-plan.src.html", "utf-8"),
    ("wpt CJK (124 kB)", "wpt/weighted/big5_chars_extra.html", "big5"),
    ("whatwg spec (235 kB)", "html.html", "utf-8"),
)


def corpus_text(relative: str, encoding: str) -> str:
    """Return a corpus document from the html5lib-python submodule."""
    target = CORPUS_DIR / relative
    if not target.exists():
        msg = f"{target} is missing; run 'git submodule update --init tools/html5lib-python'"
        raise FileNotFoundError(msg)
    return target.read_text(encoding=encoding, errors="replace")


_LARGE_DIR = _TOOLS / ".bench_data"
LARGE_FILES: tuple[tuple[str, str, str], ...] = (
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
)

# Real-world content pages for the read-path suite. The wpt fixtures are CSS layout tests with no
# nested div/a or links, so the selector and link operations matched nothing on them; these are real
# saved web pages (a blog, a news article, a product blog) from the mozilla/readability test corpus,
# pinned by commit, so find/select/:has/edit/chain/extract all run against genuine structure.
_READABILITY = "08be6b4bdb204dd333c9b7a0cfbc0e730b257252"
_RP = f"https://raw.githubusercontent.com/mozilla/readability/{_READABILITY}/test/test-pages"
REAL_PAGES: tuple[tuple[str, str, str], ...] = (
    ("daring fireball (10 kB)", "real-daringfireball.html", f"{_RP}/daringfireball-1/source.html"),
    ("ars technica (56 kB)", "real-ars.html", f"{_RP}/ars-1/source.html"),
    ("mozilla blog (95 kB)", "real-mozilla.html", f"{_RP}/mozilla-1/source.html"),
)


def large_text(filename: str, url: str) -> str:
    """Return a multi-megabyte document, downloading and caching it on first use."""
    target = _LARGE_DIR / filename
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response:  # noqa: S310  # pinned https URL
            target.write_bytes(response.read())
    return target.read_text(encoding="utf-8")


_DATA_DIR = _TOOLS / "bench-data"

_ASCII_WORDS = (
    "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dogs", "while", "zealous",
    "wizards", "vex", "sphinx", "judges", "of", "black", "quartz", "monoliths",
)  # fmt: skip
_UCS2_WORDS = (*_ASCII_WORDS, "résumé", "café", "naïve", "Москва", "über", "façade", "πλάτων", "Ω")
_UCS4_WORDS = (*_UCS2_WORDS, "😀", "🎉", "🚀", "🐍")
_REFERENCES = ("&amp;", "&lt;", "&gt;", "&quot;", "&#x27;", "&copy;", "&mdash;", "&eacute;", "&#62;", "&#127881;")
_NUMERIC_REFERENCES = ("&#62;", "&#x3e;", "&#38;", "&#127881;", "&#x1F600;")

TINY = 64
MEDIUM = 4 * 2**10
LARGE = 4 * 2**20
_CHUNK = 64 * 2**10


def corpus(relative: str, size: int) -> str:
    """Return a latin-1-decoded byte slice of a vendored document (always a 1-byte unicode object)."""
    path = _DATA_DIR / relative
    if not path.exists():
        msg = f"{path} is missing; run: git submodule update --init --depth 1 tools/bench-data/war-and-peace"
        raise FileNotFoundError(msg)
    return path.read_bytes()[:size].decode("latin-1")


def _prose_of(rng: random.Random, vocabulary: tuple[str, ...], size: int) -> str:
    """Build at least ``size`` characters of word soup with nothing to escape."""
    pieces: list[str] = []
    total = 0
    while total < size:
        word = rng.choice(vocabulary)
        pieces.append(word)
        total += len(word) + 1
    return " ".join(pieces)


def _markup_of(rng: random.Random, vocabulary: tuple[str, ...], size: int) -> str:
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


def _references_of(rng: random.Random, vocab: tuple[str, ...], gap: int, size: int, refs: tuple[str, ...]) -> str:
    """Build at least ``size`` characters of prose with a character reference after every ``gap`` words."""
    pieces: list[str] = []
    total = 0
    while total < size:
        for _ in range(gap):
            word = rng.choice(vocab)
            pieces.append(word)
            total += len(word) + 1
        reference = rng.choice(refs)
        pieces.append(reference)
        total += len(reference) + 1
    return " ".join(pieces)


def _tile(chunk: str, size: int) -> str:
    """Repeat ``chunk`` up to exactly ``size`` characters."""
    return (chunk * (size // len(chunk) + 1))[:size]


def escape_cases() -> tuple[tuple[str, str], ...]:
    """Return the (case name, input) pairs for the escape operation."""
    rng = random.Random(0)
    return (
        ("tiny plain (64 B)", _prose_of(rng, _ASCII_WORDS, TINY)),
        ("medium markup (4 KiB)", _markup_of(rng, _ASCII_WORDS, MEDIUM)),
        ("no-op prose (4 MiB)", _tile(_prose_of(rng, _ASCII_WORDS, _CHUNK), LARGE)),
        ("book text (3 MiB)", corpus("war-and-peace/2600.txt", LARGE)),
        ("book HTML (4 MiB)", corpus("war-and-peace/2600-h/2600-h.htm", LARGE)),
        ("spec HTML, dense (4 MiB)", large_text(*LARGE_FILES[1][1:]).encode()[:LARGE].decode("latin-1")),
        ("UCS-2 plain (4 MiB)", _tile(_prose_of(rng, _UCS2_WORDS, _CHUNK), LARGE)),
        ("UCS-2 markup (4 MiB)", _tile(_markup_of(rng, _UCS2_WORDS, _CHUNK), LARGE)),
        ("UCS-4 plain (4 MiB)", _tile(_prose_of(rng, _UCS4_WORDS, _CHUNK), LARGE)),
        ("UCS-4 markup (4 MiB)", _tile(_markup_of(rng, _UCS4_WORDS, _CHUNK), LARGE)),
    )


def unescape_cases() -> tuple[tuple[str, str], ...]:
    """Return the (case name, input) pairs for the unescape operation."""
    rng = random.Random(1)
    book_html = corpus("war-and-peace/2600-h/2600-h.htm", LARGE)
    return (
        ("tiny plain (64 B)", _prose_of(rng, _ASCII_WORDS, TINY)),
        ("medium dense refs (4 KiB)", _references_of(rng, _ASCII_WORDS, 1, MEDIUM, _REFERENCES)),
        ("numeric refs (4 KiB)", _references_of(rng, _ASCII_WORDS, 1, MEDIUM, _NUMERIC_REFERENCES)),
        ("book HTML, real refs (4 MiB)", book_html),
        ("escaped book HTML (5 MiB)", html.escape(book_html)),
        ("dense refs (4 MiB)", _tile(_references_of(rng, _ASCII_WORDS, 1, _CHUNK, _REFERENCES), LARGE)),
        ("UCS-2 refs (4 MiB)", _tile(_references_of(rng, _UCS2_WORDS, 10, _CHUNK, _REFERENCES), LARGE)),
    )
