"""
Round-trip and cross-entry-point oracles: the ``--mode round-trip`` lane of ``tools/fuzz/fuzz.py``.

A printer or minifier bug usually returns wrong text without crashing, so each oracle checks a property every output
must keep. A printed language parses back to the tree it came from and prints again unchanged (HTML, XML, CSS, JS,
style declarations, Markdown through markdown-it-py). A minifier keeps what the input means: JS keeps its free
identifiers and static strings, read by acorn and eslint-scope; CSS keeps every element's computed style and every
selector's match set. Entry points that answer one question agree, and source spans stay ordered and shift exactly
with a prefix.

Every oracle carries negative controls that feed it a deliberately broken printer, minifier or entry point and must
fire, and a vacuity floor on the share of in-scope cases it compared, so an oracle that stops detecting or stops
comparing fails the run (DESIGN-v2 §4d). A finding lands in ``--crash-dir`` as ``crash-<sha256>`` with a JSON sidecar;
the log names only the oracle and the hash, because CI logs on a public repository are public.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import cache, partial
from itertools import pairwise, starmap
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

from fuzz.css_custom_oracles import (
    UnsupportedCssCustomCaseError,
    css_custom_check,
    css_custom_controls,
    css_custom_generate,
    css_custom_seeds,
)
from fuzz.dom_program import (
    UnsupportedDomProgramError,
    dom_program_check,
    dom_program_controls,
    dom_program_generate,
    dom_program_seeds,
)
from fuzz.html_foreign_oracles import (
    UnsupportedHtmlForeignCaseError,
    html_foreign_check,
    html_foreign_controls,
    html_foreign_generate,
    html_foreign_seeds,
)
from fuzz.html_grammar_oracles import (
    UnsupportedHtmlSiblingCaseError,
    html_sibling_check,
    html_sibling_controls,
    html_sibling_generate,
    html_sibling_seeds,
)
from fuzz.html_list_oracles import (
    UnsupportedHtmlListCaseError,
    html_list_check,
    html_list_controls,
    html_list_generate,
    html_list_seeds,
)
from fuzz.html_structure_generators import html_generate
from fuzz.html_table_oracles import (
    UnsupportedHtmlTableCaseError,
    html_table_check,
    html_table_controls,
    html_table_generate,
    html_table_seeds,
)
from fuzz.idna_nfc_oracles import (
    UnsupportedIdnaNfcCaseError,
    idna_nfc_check,
    idna_nfc_controls,
    idna_nfc_generate,
    idna_nfc_seeds,
)
from fuzz.iterator_oracles import (
    UnsupportedIteratorCaseError,
    iterator_sequence_check,
    iterator_sequence_controls,
    iterator_sequence_generate,
    iterator_sequence_seeds,
)
from fuzz.markdown_structure_generators import (
    MarkdownProfileError,
    markdown_controls,
    markdown_generate,
    markdown_html_check,
    markdown_source_check,
    markdown_source_seeds,
)
from fuzz.observer_oracles import (
    UnsupportedObserverCaseError,
    observer_sequence_check,
    observer_sequence_controls,
    observer_sequence_generate,
    observer_sequence_seeds,
)
from fuzz.parser_byte_oracles import (
    UnsupportedParserCaseError,
    parser_bytes_check,
    parser_bytes_controls,
    parser_bytes_generate,
    parser_bytes_seeds,
)
from fuzz.reduce import minimize
from fuzz.xml_grammar_oracles import (
    UnsupportedXmlLiteralCaseError,
    xml_literal_check,
    xml_literal_controls,
    xml_literal_generate,
    xml_literal_seeds,
)
from fuzz.xml_island_oracles import (
    UnsupportedXmlIslandCaseError,
    xml_island_check,
    xml_island_controls,
    xml_island_generate,
    xml_island_seeds,
)
from fuzz.xml_structure_generators import (
    xml_document_check,
    xml_source_controls,
    xml_source_generate,
    xml_source_seeds,
)
from markdown_it import MarkdownIt
from typing_extensions import override

from turbohtml import (
    CData,
    Comment,
    Doctype,
    Document,
    DocumentFragment,
    Element,
    Html,
    HTMLParseError,
    IncrementalParser,
    Namespace,
    Node,
    ProcessingInstruction,
    SelectorSyntaxError,
    SourceLocation,
    SourceSpan,
    Text,
    XPath,
    parse,
    parse_fragment,
    parse_xml,
)
from turbohtml.clean import JSMinify, minify_css, minify_js
from turbohtml.convert import ExpressionError, css_to_xpath
from turbohtml.cssom import StyleDeclaration, StyleSheet, computed_style
from turbohtml.detect import EncodingDetector, EncodingMatch, detect
from turbohtml.extract import clean_url, normalize_url
from turbohtml.query import Matcher
from turbohtml.query import compile as compile_selector

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from urllib.parse import SplitResult

__all__ = [
    "MAX_INPUT",
    "ORACLES",
    "UNBOUNDED",
    "Floor",
    "JsNames",
    "Oracle",
    "OutOfScopeError",
    "clean_url_check",
    "css_semantics_check",
    "encoding_decode_check",
    "encoding_stream_check",
    "fixpoint_check",
    "html_check",
    "idna_host_check",
    "js_names_check",
    "main",
    "markdown_check",
    "normalize_url_check",
    "resolve_links_check",
    "selector_entry_check",
    "span_check",
    "style_check",
    "url_reparse_check",
    "xml_check",
    "xpath_entry_check",
]

_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_NODE_DIR: Final[Path] = _ROOT / "tools" / "bench" / "node"
_MINIMIZE_BUDGET: Final = 600
# TH_MAX_TREE_DEPTH (src/turbohtml/_c/dom/tree.h): past 512 open elements a start tag is inserted but not pushed, so
# further tags become siblings and reparsing the flattened output nests them differently
_MAX_TREE_DEPTH: Final = 512
# the CSS minifier caps nesting at 100 levels and drops a deeper block (docs/changelog.rst, 1.13.0)
_CSS_MAX_NESTING: Final = 100
# tree.h raises this RecursionError for a walk past its depth budget; the one documented depth error
_DEPTH_ERROR: Final = re.compile(r"does not support trees nested \d+ levels or deeper")
# Script text where "<!--" opens and "<script" plus a tag end follows before any "-->" breaks the script content
# restrictions (https://html.spec.whatwg.org/multipage/scripting.html#restrictions-for-contents-of-script-elements):
# reparsing its serialization lands in the double-escaped state, which swallows the closing "</script>". The standard
# warns that such parser-made trees need not survive serialize and reparse
# (https://html.spec.whatwg.org/multipage/parsing.html#serialising-html-fragments).
_SCRIPT_DOUBLE_ESCAPE: Final = re.compile(r"<!--(?:(?!-->).)*?<script[\t\n\f />]", re.IGNORECASE | re.DOTALL)
# rust-cssparser dropped its idempotence assert because dtoa takes two passes to converge (9999995e-45 -> 10e-39 ->
# 1e-38), so a numeric rewrite may settle on the second pass:
# https://github.com/servo/rust-cssparser/blob/106021c7bc55ed2cac83f590c0d356fa530b7061/fuzz/fuzz_targets/cssparser.rs
_NUMBER: Final = re.compile(r"(?<![\w$#\\.])[+-]?(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?n?")
_JS_OFF: Final = JSMinify(mangle=False, fold=False)
_MAX_RENESTS: Final = 5
# cap one case's input: generators stay under a few KiB, but a mutated .dat seed can grow, and to_markdown/to_text
# amplify nested input about 100-240x (tox-dev/turbohtml#1028), so an input this long could print hundreds of MiB
MAX_INPUT: Final = 64 * 1024
# parse() allocates without bound on a selectedcontent nested in its own option (findings/01); the generators never
# emit the tag, so skipping it keeps a machine-safe run while the defect is tracked on its own
UNBOUNDED: Final = re.compile(r"selectedcontent", re.IGNORECASE)
_HEADINGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SELF_NESTING: Final[dict[str, frozenset[str]]] = {
    **{tag: frozenset({tag}) for tag in ("a", "nobr", "p", "button", "select", "form", "table", "option", "li")},
    **dict.fromkeys(("dd", "dt"), frozenset({"dd", "dt"})),
    **dict.fromkeys(_HEADINGS, _HEADINGS),
    **dict.fromkeys(("rb", "rt", "rp", "rtc"), frozenset({"rb", "rt", "rp", "rtc"})),
}
_MARKDOWN_BLOCKS: Final = frozenset({
    "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt", "fieldset", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "ul",
})  # fmt: skip
# the elements whose Markdown form holds only inline content: phrasing elements and GFM table cells
_MARKDOWN_HOSTS: Final = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "del", "dfn", "em", "i", "ins", "kbd", "mark", "q", "s", "samp",
    "small", "span", "strike", "strong", "sub", "sup", "td", "th", "u", "var",
})  # fmt: skip


class _FaithfulMarkdown(MarkdownIt):
    """markdown-it percent-encodes link destinations and drops ``javascript:`` links, reader policy this undoes."""

    @override
    def normalizeLink(self, url: str) -> str:
        return url

    @override
    def validateLink(self, url: str) -> bool:
        return True


_MARKDOWN: Final = _FaithfulMarkdown("gfm-like", {"linkify": False})
# markdown-it-py 4.2.0 strips a paragraph with str.strip (rules_block/paragraph.py), so a paragraph of only U+00A0
# reads as an empty one, where CommonMark strips only spaces and tabs; an empty paragraph shows nothing either way
_EMPTY_PARAGRAPH: Final = re.compile(r"<p></p>\n")


class OutOfScopeError(Exception):
    """The case is outside the oracle's scope; it counts against the oracle's vacuity floor."""


@dataclass(frozen=True)
class Floor:
    """
    The least an oracle must compare for a run to mean anything.

    Modeled on meriyah's ``MINIMUM_COMPARED_RATIO``: ``ratio`` bounds compared cases over compared plus skipped ones,
    ``count`` bounds the compared cases themselves.
    """

    count: int
    ratio: float


@dataclass(frozen=True)
class Oracle:
    """
    One wrong-output oracle.

    ``check`` returns None when the case holds, a shape-only description when it breaks, and raises
    :class:`OutOfScopeError` when the case is out of scope. ``controls`` maps each negative control to whether it fired.
    A case that is a JSON object names the string ``fields`` to shrink one at a time; any other case shrinks whole.
    """

    check: Callable[[str], str | None]
    generate: Callable[[random.Random], str]
    seeds: Callable[[], list[str]]
    controls: Callable[[], dict[str, bool]]
    floor: Floor
    fields: tuple[str, ...] = ()
    syntax: Literal["html", "css", "js"] | None = None


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 when every oracle holds, 1 on a finding, 2 when a control stayed silent or a run compared too little."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=1.0, help="generation budget after the seed pass")
    parser.add_argument(
        "--rng-seed",
        type=int,
        default=int(os.environ.get("FUZZ_RNG_SEED", "0")),
        help="seed of the generated cases (default: $FUZZ_RNG_SEED, else 0)",
    )
    parser.add_argument("--crash-dir", type=Path, default=_ROOT / ".fuzz-crashes", help="where findings land")
    parser.add_argument("--oracle", action="append", choices=sorted(ORACLES), help="run only these (repeatable)")
    args = parser.parse_args(argv)

    names = args.oracle or sorted(ORACLES)
    if blind := [
        f"{name}/{control}" for name in names for control, fired in ORACLES[name].controls().items() if not fired
    ]:
        print(f"BLIND ORACLE: the negative control did not fire for {', '.join(blind)}", file=sys.stderr)
        return 2
    args.crash_dir.mkdir(parents=True, exist_ok=True)
    run = _Run(args.crash_dir)
    for name in names:
        for seed in ORACLES[name].seeds():
            run.case(name, seed)
    rng = random.Random(args.rng_seed)
    deadline = time.monotonic() + args.minutes * 60
    while time.monotonic() < deadline:
        name = rng.choice(names)
        run.case(name, ORACLES[name].generate(rng))
    for name in names:
        print(f"{name}: {dict(run.stats[name])}")
    findings = run.report(args.rng_seed)
    if vacuous := [name for name in names if not _meets(ORACLES[name].floor, run.stats[name])]:
        print(f"VACUOUS RUN: {', '.join(vacuous)} compared below the floor", file=sys.stderr)
        return 2
    print(f"{findings} finding(s) written to {args.crash_dir}")
    return int(findings > 0)


def _meets(floor: Floor, stats: Counter[str]) -> bool:
    compared = stats["compared"]
    return compared >= floor.count and compared >= floor.ratio * (compared + stats["skipped"])


@dataclass
class _Found:
    oracle: str
    detail: str
    text: str
    hits: int = 1


@dataclass
class _Run:
    crash_dir: Path
    stats: dict[str, Counter[str]] = field(default_factory=lambda: {name: Counter() for name in ORACLES})
    found: dict[tuple[str, str], _Found] = field(default_factory=dict)

    def case(self, name: str, text: str) -> None:
        if len(text) > MAX_INPUT or UNBOUNDED.search(text):
            self.stats[name]["skipped"] += 1
            return
        # an ASan abort kills the process, so the input it died on must already be on disk
        (self.crash_dir / "current_input").write_text(text, encoding="utf-8", errors="surrogatepass")
        try:
            detail = _guarded(ORACLES[name].check, text)
        except OutOfScopeError:
            self.stats[name]["skipped"] += 1
            return
        self.stats[name]["compared"] += 1
        if detail is not None:
            if (key := (name, detail)) in self.found:
                self.found[key].hits += 1
            else:
                self.found[key] = _Found(name, detail, text)

    def report(self, rng_seed: int) -> int:
        for found in self.found.values():
            oracle = ORACLES[found.oracle]
            text = _minimize(found.text, partial(_reproduces, oracle.check, found.detail), oracle.fields, oracle.syntax)
            data = text.encode("utf-8", "surrogatepass")
            digest = hashlib.sha256(data).hexdigest()
            (self.crash_dir / f"crash-{digest}").write_bytes(data)
            sidecar = {"oracle": found.oracle, "detail": found.detail, "hits": found.hits, "rng_seed": rng_seed}
            (self.crash_dir / f"crash-{digest}.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
            # only the oracle and a hash reach the log: the input may be an unreported bug (DESIGN-v2 decision D5)
            print(f"FINDING {found.oracle} sha256={digest}", file=sys.stderr, flush=True)
        return len(self.found)


def _guarded(check: Callable[[str], str | None], text: str) -> str | None:
    """Run one check, turning the documented depth error into a skip and any other escape into a finding."""
    try:
        return check(text)
    except RecursionError as error:
        if _DEPTH_ERROR.search(str(error)):
            raise OutOfScopeError from error
        return "raised RecursionError"
    except OutOfScopeError:
        raise
    except Exception as error:  # ruff: ignore[blind-except]  # an unexpected exception type from turbohtml is itself the finding
        return f"raised {type(error).__name__}"


def _reproduces(check: Callable[[str], str | None], detail: str, text: str) -> bool:
    try:
        return _guarded(check, text) == detail
    except OutOfScopeError:
        return False


def _minimize(
    text: str,
    reproduces: Callable[[str], bool],
    fields: Sequence[str],
    syntax: Literal["html", "css", "js"] | None,
) -> str:
    remaining = _MINIMIZE_BUDGET

    def check(candidate: str) -> bool:
        nonlocal remaining
        remaining -= 1
        return reproduces(candidate)

    def shrink(source: str, predicate: Callable[[str], bool], language: Literal["html", "css", "js"] | None) -> str:
        if language is not None:
            source = minimize(source, predicate, language, remaining)
        return _ddmin(source, predicate, remaining)

    if not fields:
        return shrink(text, check, syntax)
    payload = json.loads(text)
    for name in fields:
        payload[name] = shrink(
            payload[name],
            lambda value, name=name: check(json.dumps({**payload, name: value})),
            "html" if name == "html" else syntax,
        )
    return json.dumps(payload)


def _ddmin(text: str, reproduces: Callable[[str], bool], budget: int) -> str:
    """Shrink ``text`` while ``reproduces`` holds, with Zeller and Hildebrandt's ddmin under a call budget."""
    calls = 0
    granularity = 2
    while len(text) >= 2 and calls < budget:
        chunk = max(len(text) // granularity, 1)
        for start in range(0, len(text), chunk):
            if calls == budget:
                return text
            calls += 1
            if reproduces(candidate := text[:start] + text[start + chunk :]):
                text = candidate
                granularity = max(granularity - 1, 2)
                break
        else:
            if chunk == 1:
                break
            granularity = min(granularity * 2, len(text))
    return text


def fixpoint_check(text: str, printer: Callable[[str], str], *, numeric: bool) -> str | None:
    """
    Require ``printer`` to reach a fixpoint: printing its own output changes nothing.

    With ``numeric``, a second pass that only rewrites numbers may settle on the third (``f(f(f(x))) == f(f(x))``), the
    rust-cssparser exception; anything else that moves on the second pass is a finding. A first pass that rejects the
    input skips it; a later pass that rejects the printer's own output is a finding.
    """
    try:
        once = printer(text)
    except ValueError as error:
        raise OutOfScopeError from error
    try:
        twice = printer(once)
    except ValueError:
        return "rejects its own output"
    if twice == once:
        return None
    if numeric and _NUMBER.sub("0", twice) == _NUMBER.sub("0", once) and printer(twice) == twice:
        return None
    return f"not a fixpoint: {_divergence(once, twice)}"


def _html_foreign_check(case: str) -> str | None:
    try:
        return html_foreign_check(case)
    except UnsupportedHtmlForeignCaseError as error:
        raise OutOfScopeError from error


def _html_list_check(case: str) -> str | None:
    try:
        return html_list_check(case)
    except UnsupportedHtmlListCaseError as error:
        raise OutOfScopeError from error


def _html_table_check(case: str) -> str | None:
    try:
        return html_table_check(case)
    except UnsupportedHtmlTableCaseError as error:
        raise OutOfScopeError from error


def _html_sibling_check(case: str) -> str | None:
    try:
        return html_sibling_check(case)
    except UnsupportedHtmlSiblingCaseError as error:
        raise OutOfScopeError from error


def _css_custom_check(case: str) -> str | None:
    try:
        return css_custom_check(case)
    except UnsupportedCssCustomCaseError as error:
        raise OutOfScopeError from error


def _observer_sequence_check(case: str) -> str | None:
    try:
        return observer_sequence_check(case)
    except UnsupportedObserverCaseError as error:
        raise OutOfScopeError from error


def _dom_program_check(case: str) -> str | None:
    try:
        return dom_program_check(case)
    except UnsupportedDomProgramError as error:
        raise OutOfScopeError from error


def _iterator_sequence_check(case: str) -> str | None:
    try:
        return iterator_sequence_check(case)
    except UnsupportedIteratorCaseError as error:
        raise OutOfScopeError from error


_IDNA_LABELS: Final[dict[str, tuple[str, str]]] = {
    "umlaut": ("münchen", "münchen"),
    "sharp-s": ("faß", "faß"),
    "acute-decomposed": ("a\u0301", "á"),
    "acute-precomposed": ("á", "á"),
    "umlaut-decomposed": ("u\u0308", "ü"),
    "japanese": ("日本語", "日本語"),
    "greek": ("δοκιμή", "δοκιμή"),
    "ignored": ("ex\u00adample", "example"),
    "fullwidth": ("\uff26\uff2f\uff2f", "foo"),
    "mapped-dot": ("日本語。\uff2a\uff30", "日本語.jp"),
}


def idna_host_check(case: str, normalize: Callable[[str], str] = normalize_url) -> str | None:
    """Pin supported Unicode 16 host mappings through the public URL normalizer."""
    variant, separator, affix = case.partition("\n")
    if (mapping := _IDNA_LABELS.get(variant)) is None or not separator or not re.fullmatch(r"[a-z0-9]{0,8}", affix):
        raise OutOfScopeError(case)
    source, mapped = mapping
    mapped = affix + mapped
    expected: Final = (
        "https://"
        + ".".join(
            label if label.isascii() else "xn--" + label.encode("punycode").decode("ascii")
            for label in mapped.split(".")
        )
        + ".example/"
    )
    try:
        once = normalize(f"https://{affix}{source}.example/")
    except ValueError:
        return "rejects known-valid Unicode host"
    if (problem := _idna_output_problem(once)) is not None:
        return problem
    if once != expected:
        return "IDNA host differs from expected mapping"
    try:
        twice = normalize(once)
    except ValueError:
        return "rejects canonical IDNA output"
    return None if twice == once else "IDNA output is not a fixpoint"


def _idna_output_problem(once: str) -> str | None:
    if not once.isascii():
        return "IDNA output is not ASCII"
    for label in once.removeprefix("https://").removesuffix("/").split("."):
        if not label.startswith("xn--"):
            continue
        payload = label[4:]
        try:
            decoded = payload.encode("ascii").decode("punycode")
        except UnicodeError:
            return "IDNA output has undecodable Punycode"
        if not unicodedata.is_normalized("NFC", decoded):
            return "IDNA decoded label is not NFC"
        if decoded.encode("punycode").decode("ascii") != payload:
            return "IDNA Punycode is not canonical"
    return None


def _idna_nfc_check(case: str) -> str | None:
    try:
        return idna_nfc_check(case)
    except UnsupportedIdnaNfcCaseError as error:
        raise OutOfScopeError(str(error)) from error


def normalize_url_check(text: str, normalize: Callable[[str], str] = normalize_url) -> str | None:
    """URL normalization promises a stable representation after one successful pass."""
    return fixpoint_check(text, normalize, numeric=False)


def url_reparse_check(text: str, normalize: Callable[[str], str] = normalize_url) -> str | None:
    """Preserve delimiter presence when checking serialization through a reference split."""
    try:
        normalized: Final = normalize(text)
        parts: Final = urlsplit(normalized)
    except ValueError as error:
        raise OutOfScopeError(str(error)) from error
    recomposed: Final = _url_recompose(parts, normalized)
    if recomposed != normalized:
        return f"split/recompose changes normalized URL: {_divergence(normalized, recomposed)}"
    try:
        reparsed: Final = normalize(recomposed)
    except ValueError:
        return "rejects recomposed output"
    return None if reparsed == normalized else f"reparse changes normalized URL: {_divergence(normalized, reparsed)}"


def _url_recompose(parts: SplitResult, text: str) -> str:
    body: Final = text[len(parts.scheme) + 1 :] if parts.scheme else text
    return "".join((
        f"{parts.scheme}:" if parts.scheme else "",
        f"//{parts.netloc}" if body.startswith("//") else "",
        parts.path,
        f"?{parts.query}" if "?" in body.partition("#")[0] else "",
        f"#{parts.fragment}" if "#" in body else "",
    ))


def clean_url_check(text: str, clean: Callable[[str], str | None] = clean_url) -> str | None:
    """Skip rejected inputs; flag rejection of a cleaned URL."""
    try:
        once = clean(text)
    except ValueError as error:
        raise OutOfScopeError from error
    if once is None:
        raise OutOfScopeError(text)
    try:
        twice = clean(once)
    except ValueError:
        return "rejects its own output"
    if twice is None:
        return "rejects its own output"
    return None if twice == once else f"not a fixpoint: {_divergence(once, twice)}"


_ENCODING_FORMATS: Final[dict[str, tuple[str, bytes, str, str]]] = {
    "utf-8-bom": ("utf-8", b"\xef\xbb\xbf", "UTF-8-SIG", ""),
    "utf-16le-bom": ("utf-16-le", b"\xff\xfe", "UTF-16LE", ""),
    "utf-16be-bom": ("utf-16-be", b"\xfe\xff", "UTF-16BE", ""),
    "utf-16-meta-remap": ("utf-8", b"", "UTF-8", "<meta charset=utf-16>"),
    "utf-16le-meta-remap": ("utf-8", b"", "UTF-8", "<meta charset=utf-16le>"),
    "utf-16be-meta-remap": ("utf-8", b"", "UTF-8", "<meta charset=utf-16be>"),
    "utf-8-meta": ("utf-8", b"", "UTF-8", "<meta charset=utf-8>"),
    "windows-1252-meta": ("cp1252", b"", "windows-1252", "<meta charset=windows-1252>"),
    "latin1-meta-alias": ("cp1252", b"", "windows-1252", "<meta charset=latin1>"),
    "iso-8859-1-meta-alias": ("cp1252", b"", "windows-1252", "<meta charset=iso-8859-1>"),
    "us-ascii-meta-alias": ("cp1252", b"", "windows-1252", "<meta charset=us-ascii>"),
    "utf-8-bom-conflicting-meta": ("utf-8", b"\xef\xbb\xbf", "UTF-8-SIG", "<meta charset=windows-1252>"),
    "utf-16le-bom-conflicting-meta": ("utf-16-le", b"\xff\xfe", "UTF-16LE", "<meta charset=utf-8>"),
    "utf-16be-bom-conflicting-meta": ("utf-16-be", b"\xfe\xff", "UTF-16BE", "<meta charset=utf-8>"),
    "utf-8-pragma-first": ("utf-8", b"", "UTF-8", '<meta http-equiv=Content-Type content="text/html; charset=utf-8">'),
    "utf-8-content-first": ("utf-8", b"", "UTF-8", '<meta content="text/html; charset=utf-8" http-equiv=Content-Type>'),
    "windows-1252-pragma-first": (
        "cp1252",
        b"",
        "windows-1252",
        '<meta http-equiv=Content-Type content="text/html; charset=windows-1252">',
    ),
    "windows-1252-content-first": (
        "cp1252",
        b"",
        "windows-1252",
        '<meta content="text/html; charset=windows-1252" http-equiv=Content-Type>',
    ),
}


def encoding_stream_check(
    case: str,
    one_shot: Callable[[bytes], EncodingMatch] = detect,
    stream: Callable[[bytes, int], EncodingMatch] | None = None,
) -> str | None:
    """Pin expected detection independently of the chunk-boundary comparison."""
    expected: Final = _encoding_case(case)
    if one_shot(expected.data) != expected.match:
        return "one-shot detection differs from expected match"
    for width in range(1, 6):
        if (stream or _encoding_stream)(expected.data, width) != expected.match:
            return "chunked detection differs from expected match"
    return None


def encoding_decode_check(
    case: str,
    detect_bytes: Callable[[bytes], EncodingMatch] = detect,
    decode: Callable[[bytes, str], str] = bytes.decode,
    parse_text: Callable[[bytes], str | None] | None = None,
) -> str | None:
    """Require the codec and byte parser to preserve fixture text."""
    expected: Final = _encoding_case(case)
    if expected.match.codec is None:
        raise OutOfScopeError(case)
    if detect_bytes(expected.data) != expected.match:
        return "detection differs from expected match"
    if decode(expected.data, expected.match.codec) != expected.decoded:
        return "detected codec differs from expected text"
    if (parse_text or _encoding_text)(expected.data) != expected.text:
        return "byte parse differs from expected text"
    return None


def _encoding_case(case: str) -> _EncodingCase:
    variant, separator, text = case.partition("\n")
    if not separator or len(text) > 256 or (text and not text.isprintable()) or any(char in text for char in "<>&"):
        raise OutOfScopeError(case)
    if variant == "empty" and not text:
        return _EncodingCase(b"", EncodingMatch(None, 0.0, None), "", "")
    if (formatting := _ENCODING_FORMATS.get(variant)) is None:
        raise OutOfScopeError(case)
    codec, prefix, label, meta = formatting
    markup: Final = f"{meta}<p>{text}</p>"
    try:
        data: Final = prefix + markup.encode(codec)
    except UnicodeEncodeError as error:
        raise OutOfScopeError from error
    decoded: Final = ("\ufeff" if label in {"UTF-16LE", "UTF-16BE"} else "") + markup
    return _EncodingCase(data, EncodingMatch(label, 1.0, None, bool(prefix), f"whatwg-{label.lower()}"), decoded, text)


@dataclass(frozen=True)
class _EncodingCase:
    data: bytes
    match: EncodingMatch
    decoded: str
    text: str


def _encoding_stream(data: bytes, width: int) -> EncodingMatch:
    detector: Final = EncodingDetector()
    detector.feed(b"")
    for start in range(0, len(data), width):
        detector.feed(data[start : start + width])
        detector.feed(b"")
    return detector.close()


def _encoding_text(data: bytes) -> str:
    return parse(data).text


def _divergence(left: str, right: str) -> str:
    """Name the character classes at the first difference, so findings that differ only in payload share a key."""
    pairs = enumerate(zip(left, right, strict=False))
    index = next((position for position, (one, two) in pairs if one != two), min(len(left), len(right)))
    return f"{_char_class(left[index : index + 1])} vs {_char_class(right[index : index + 1])}"


def _char_class(char: str) -> str:
    if not char:
        return "end"
    if char.isalpha():
        return "letter"
    if char.isdigit():
        return "digit"
    if char.isspace():
        return "space"
    return repr(char) if char in "<>&\"'=/;:{}()[],.!*#`\\|_-+~$@%^?" else "other"


def html_check(markup: str, serialize: Callable[[Node], str] = Node.serialize) -> str | None:
    """
    Require HTML serialization to keep a tree's content and reach a fixpoint, for a document and a ``<div>`` fragment.

    The standard warns that parser-made trees need not survive serialize and reparse
    (https://html.spec.whatwg.org/multipage/parsing.html#serialising-html-fragments): nested ``<a>``, a ``<table>`` in
    a quirks-mode ``<p>`` and the like re-nest. So the first reparse may move elements but must keep every text run,
    comment and element with its attributes, and the second serialization must equal the first byte for byte. A tree
    flattened past the depth cap, script text that enters the double-escaped state, and a ``plaintext`` element (its
    serialized end tag is text to the PLAINTEXT state, which never exits) skip. A carriage return compares as a line
    feed: serialization emits it raw and input preprocessing turns it into one, and text holding one skips, since the
    line feed it becomes can then be the one a ``pre`` drops after its start tag. Each reparse may re-nest further, so a
    fixpoint must come within five.
    """
    for label, tree, reparse in (
        ("document", parse(markup), parse),
        ("fragment", parse_fragment(markup), parse_fragment),
    ):
        if _reparse_may_differ(tree):
            raise OutOfScopeError
        content = _content(tree)
        printed = _newlines(_print(tree, serialize))
        for _ in range(_MAX_RENESTS):
            if (again := _print(reparsed := reparse(printed), serialize)) == printed:
                break
            if _content(reparsed) != content:
                return f"{label} reparse changed content"
            printed = again
        else:
            return f"{label} not a fixpoint after {_MAX_RENESTS} reparses"
    return None


def _print(tree: Node, serialize: Callable[[Node], str]) -> str:
    return serialize(tree) if isinstance(tree, Document) else "".join(serialize(child) for child in tree.children)


def _reparse_may_differ(root: Node) -> bool:
    return (
        _past_depth_cap(root)
        or any(_SCRIPT_DOUBLE_ESCAPE.search(script.text) for script in root.iter_elements("script"))
        or any(element.namespace is Namespace.HTML for element in root.iter_elements("plaintext"))
        or any(isinstance(node, Text) and "\r" in node.data for node in root.descendants)
        or any(_nests_in_itself(element) for element in root.iter_elements())
    )


def _nests_in_itself(element: Element) -> bool:
    # a start tag of these names closes, re-nests or drops an open element of its group (WHATWG 13.2.6.4.7 "in
    # body": the adoption agency for a, nobr; "close a p element"; li, dd, dt, option, headings, button, select,
    # form's element pointer, table, ruby), so a parser-made tree nesting one inside its group cannot be re-read
    group = _SELF_NESTING.get(element.tag)
    return (
        group is not None
        and element.namespace is Namespace.HTML
        and any(getattr(ancestor, "tag", None) in group for ancestor in element.ancestors)
    )


def _content(root: Node) -> tuple[str, list[str], set[tuple[str, str, tuple[tuple[str, str], ...]]]]:
    """
    Reduce a tree to what re-nesting keeps: its text in order, its comments in order, its distinct elements.

    A bare ``p`` or ``br`` does not count: re-nesting can strand a ``</p>`` or meet a ``</br>``, and the tree builder
    answers each with a new empty element (WHATWG 13.2.6.4.7, "in body").
    """
    nodes = list(root.descendants)
    elements = {
        (
            element.namespace.value,
            element.tag,
            tuple(sorted((name, _attr_text(value)) for name, value in element.attrs.items())),
        )
        for element in root.iter_elements()
    }
    return (
        _newlines("".join(node.data for node in nodes if isinstance(node, Text))),
        [node.data for node in nodes if isinstance(node, Comment)],
        elements - {("html", "p", ()), ("html", "br", ())},
    )


def _newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _past_depth_cap(root: Node) -> bool:
    # the root node plus 512 ancestors is the deepest placement the tree builder allows, reached only on a full stack
    return any(sum(1 for _ in element.ancestors) > _MAX_TREE_DEPTH for element in root.iter_elements())


def _xml(node: Node) -> str:
    return node.serialize(Html(xml=True))


def xml_check(markup: str, serialize: Callable[[Node], str] = _xml) -> str | None:
    """
    Require ``serialize(Html(xml=True))`` output to re-read with ``parse_xml`` into the tree it came from.

    The string must also print unchanged. Content the ``Html.xml`` docstring says the serializer drops or spaces (C0
    controls, surrogates, U+FFFE/U+FFFF, ``--`` or a trailing ``-`` in a comment, non-XML attribute names) waives only
    the tree comparison.
    """
    document = parse(markup)
    try:
        reread = parse_xml(once := serialize(document))
    except (ValueError, HTMLParseError):
        return "parse_xml rejects the XML serialization"
    if (twice := serialize(reread)) != once:
        return f"xml not a fixpoint: {_divergence(once, twice)}"
    if _xml_lossy(document):
        return None
    expected, got = [*_xml_dump(document), "#end"], [*_xml_dump(reread), "#end"]
    if expected == got:
        return None
    return "xml re-read tree differs: " + next(
        f"{want[:1]} vs {have[:1]}" for want, have in zip(expected, got, strict=False) if want != have
    )


# XML 1.0 Char (https://www.w3.org/TR/xml/#NT-Char) and Name (https://www.w3.org/TR/xml/#NT-Name) productions
_XML_CHAR: Final = re.compile(r"[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
_NAME_START: Final = (
    r":A-Z_a-z\xc0-\xd6\xd8-\xf6\xf8-\u02ff\u0370-\u037d\u037f-\u1fff\u200c\u200d\u2070-\u218f\u2c00-\u2fef"
    r"\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd\U00010000-\U000effff"
)
_XML_NAME: Final = re.compile(rf"[{_NAME_START}][{_NAME_START}\-.0-9\xb7\u0300-\u036f\u203f-\u2040]*")


def _xml_lossy(document: Document) -> bool:
    for node in document.descendants:
        if isinstance(node, (Text, CData, ProcessingInstruction)) and _XML_CHAR.search(node.data):
            return True
        if isinstance(node, Comment) and (_XML_CHAR.search(node.data) or "--" in node.data or node.data.endswith("-")):
            return True
        if isinstance(node, Element) and any(
            _XML_NAME.fullmatch(name) is None or _XML_CHAR.search(_attr_text(value))
            for name, value in node.attrs.items()
        ):
            return True
    return False


def _attr_text(value: str | list[str] | None) -> str:
    return " ".join(value) if isinstance(value, list) else value or ""


def _xml_dump(root: Node) -> list[str]:
    """
    Flatten a tree to comparable lines: element names with their non-namespace attributes, merged text, comments.

    ``parse_xml`` reports every element in the HTML namespace and keeps ``xmlns`` declarations as attributes, and a
    template's content fragment holds what XML reads as the template's children, so those three are normalized away.
    """
    out: list[str] = []
    text = ""
    stack: list[Node | str] = list(reversed(root.children))
    while stack:
        if isinstance(node := stack.pop(), (Text, CData)):
            text += node.data
            continue
        if text:
            out.append(f'"{text}')
            text = ""
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, (Element, DocumentFragment)):
            if isinstance(node, Element):
                out.append(f"<{node.tag} {_xml_attrs(node)}")
                stack.append(f"</{node.tag}")
            stack.extend(reversed(node.children))
        else:
            out.append(_xml_leaf(node))
    if text:
        out.append(f'"{text}')
    return out


def _xml_attrs(element: Element) -> list[tuple[str, str]]:
    return sorted(
        (name, _attr_text(value))
        for name, value in element.attrs.items()
        if name != "xmlns" and not name.startswith("xmlns:")
    )


def _xml_leaf(node: Node) -> str:
    if isinstance(node, Comment):
        return f"!{node.data}"
    if isinstance(node, ProcessingInstruction):
        return f"?{node.target} {node.data}"
    if isinstance(node, Doctype):
        # DOM Parsing 3.2.1.3 writes an external ID only when non-empty, so empty and absent read the same
        return f"D{node.name} {node.public_id or None} {node.system_id or None}"
    return type(node).__name__


def resolve_links_check(text: str, resolve: Callable[[Node, str], None] = Node.resolve_links) -> str | None:
    """Check fixed RFC targets because stable wrong resolutions satisfy a fixed-point check."""
    case, separator, leaf = text.partition(":")
    if not separator or case not in _RESOLUTION_CASES or re.fullmatch(r"[a-z][a-z0-9]{0,15}", leaf) is None:
        raise OutOfScopeError(text)
    reference, target = _RESOLUTION_CASES[case]
    document = parse_fragment('<a href="">link</a>')
    document.select("a")[0].attrs["href"] = reference.format(leaf=leaf)
    resolve(document, _RESOLUTION_BASE)
    expected: Final = target.format(leaf=leaf)
    if document.links()[0].url != expected:
        return "resolution differs from RFC target"
    resolve(document, _RESOLUTION_BASE)
    return None if document.links()[0].url == expected else "resolved link changes on repeat"


def style_check(text: str, read: Callable[[str], StyleDeclaration] = StyleDeclaration.parse) -> str | None:
    """Require ``StyleDeclaration.text`` to re-read to the same declarations and print unchanged."""
    first = read(text)
    second = read(once := first.text)
    if _winning(first) != _winning(second):
        return "declarations differ after re-read"
    if (twice := second.text) != once:
        return f"not a fixpoint: {_divergence(once, twice)}"
    return None


def _winning(declaration: StyleDeclaration) -> list[tuple[str, str | None, bool]]:
    return [(name, declaration.get(name), declaration.important(name)) for name in declaration.properties()]


def markdown_check(markup: str, render: Callable[[Node], str] = Node.to_markdown) -> str | None:
    """
    Require ``to_markdown`` output to mean, to markdown-it-py's GFM reader, what its own re-rendering means.

    ``m1 = to_markdown(html)`` is read into ``h1``, and ``m2 = to_markdown(h1)`` must read into ``h1`` again: the
    Markdown tree is the one compared, so spelling choices (which characters get escaped, how many blank lines) do not
    count, while emphasis delimiters that merge, a table that reads as a paragraph, or a character left unescaped that
    starts a construct do. CommonMark has no syntax for a block inside an inline or inside a GFM table cell, so a tree
    that nests one (a ``<pre>`` in a ``<del>``, as the parser builds from misnested tags) has no faithful Markdown form
    and skips.
    """
    document = parse(markup)
    if any(
        element.tag in _MARKDOWN_BLOCKS
        and any(getattr(ancestor, "tag", "") in _MARKDOWN_HOSTS for ancestor in element.ancestors)
        for element in document.iter_elements()
    ):
        raise OutOfScopeError
    first = _EMPTY_PARAGRAPH.sub("", _MARKDOWN.render(render(document)))
    if (second := _EMPTY_PARAGRAPH.sub("", _MARKDOWN.render(render(parse(first))))) != first:
        return f"not a fixpoint: {_divergence(first, second)}"
    return None


class JsNames:
    """
    The free identifiers and static strings of a JavaScript source, read by acorn and eslint-scope in one Node process.

    ``tools/bench/node/js_names_runner.js`` defines both sets. Kept apart from turbohtml's own JS front end on purpose:
    a reader that shared the minifier's lexer would share its bugs.
    """

    def __init__(self, runner_dir: Path = _NODE_DIR) -> None:
        """Start nothing yet; the Node process runs ``js_names_runner.js`` from ``runner_dir`` on the first read."""
        self._runner_dir = runner_dir
        self._process: subprocess.Popen[str] | None = None

    def read(self, source: str) -> tuple[frozenset[str], frozenset[str]] | None:
        """
        Return ``(free identifiers, static strings)``, or None when acorn rejects ``source``.

        :raises OutOfScopeError: eslint-scope could not scope the tree acorn built.
        """
        process = self._start()
        assert process.stdin is not None  # ruff: ignore[assert]  # Popen with stdin=PIPE always sets it
        assert process.stdout is not None  # ruff: ignore[assert]  # Popen with stdout=PIPE always sets it
        process.stdin.write(json.dumps(source) + "\n")
        process.stdin.flush()
        if (answer := json.loads(process.stdout.readline())) is None:
            return None
        if "error" in answer:
            raise OutOfScopeError(answer["error"])
        return frozenset(answer["free"]), frozenset(answer["strings"])

    def _start(self) -> subprocess.Popen[str]:
        if self._process is None:
            if (node := shutil.which("node")) is None or not (
                self._runner_dir / "node_modules" / "eslint-scope"
            ).is_dir():
                msg = f"node and `npm ci` in {self._runner_dir} are required by the js-names oracle"
                raise FileNotFoundError(msg)
            self._process = subprocess.Popen(
                [node, str(self._runner_dir / "js_names_runner.js")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="surrogatepass",
                cwd=self._runner_dir,
                # an ASan-preloaded run must not hand its runtime to node
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key not in {"LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}
                },
            )
        return self._process


_OPTION_SETS: Final = tuple(JSMinify(mangle=mangle, fold=fold) for fold in (False, True) for mangle in (False, True))


def js_names_check(source: str, names: JsNames, minify: Callable[[str, JSMinify], str] = minify_js) -> str | None:
    """
    Require ``minify_js`` to invent and lose no free identifier and no static string.

    With ``mangle`` and ``fold`` off both sets must match exactly. ``fold`` deletes dead code and concatenates strings
    and ``mangle`` drops unused declarations, so with either on no free identifier may appear that the input lacked.
    Every output must parse as JavaScript.
    """
    if (before := names.read(source)) is None:
        raise OutOfScopeError
    compared = False
    for options in _OPTION_SETS:
        try:
            output = minify(source, options)
        except ValueError:
            continue
        compared = True
        label = f"mangle={options.mangle} fold={options.fold}"
        if (after := names.read(output)) is None:
            return f"{label}: output is not JavaScript"
        if options.fold or options.mangle:
            if after[0] - before[0]:
                return f"{label}: free identifier invented"
        elif after[0] != before[0]:
            return f"{label}: free identifier {'invented' if after[0] - before[0] else 'lost'}"
        elif after[1] != before[1]:
            return f"{label}: static string {'invented' if after[1] - before[1] else 'lost'}"
    if not compared:
        raise OutOfScopeError
    return None


def css_semantics_check(
    markup: str, minify: Callable[[str], str] = minify_css, *, stylesheet: str | None = None
) -> str | None:
    """
    Require ``minify_css`` to keep every element's computed style and every selector's match set.

    cssnano's fuzzCheck model: the document's ``<style>`` sheet is minified in place and ``computed_style`` of each
    element compared. ``computed_style`` returns values as written and keeps invalid ones, so each pair is read with
    the CSS value grammar first: two valid values must be equal values, and a value the grammar rejects must not become
    one it accepts. A value outside what this reader covers makes no claim. Each rule's selector is also minified on its
    own and must select the same elements.
    """
    document = parse(markup)
    style = _css_style(document, stylesheet)
    sheet = style.text
    elements = list(document.iter_elements())
    before = [computed_style(element) for element in elements]
    style.text = minify(sheet)
    after = [computed_style(element) for element in elements]
    for want, got in zip(before, after, strict=True):
        for name in want.properties():
            if (verdict := _value_verdict(name, want[name], got[name])) is not None:
                return f"computed {_VALUE_KIND.get(name, 'keyword')}: {verdict}"
    for rule in StyleSheet(sheet).rules:
        try:
            expected = document.select(rule.selector_text)
        except SelectorSyntaxError:
            continue
        minified = StyleSheet(minify(f"{rule.selector_text}{{color:red}}")).rules
        if len(minified) != 1:
            return "selector rule dropped"
        try:
            if document.select(minified[0].selector_text) != expected:
                return "selector match set differs"
        except SelectorSyntaxError:
            return "minified selector does not parse"
    return None


def _css_style(document: Document, stylesheet: str | None) -> Element:
    if (style := document.select_one("style")) is None:
        raise OutOfScopeError
    if stylesheet is not None:
        style.text = stylesheet
    return style


def _value_verdict(name: str, before: str, after: str) -> str | None:
    if before == after:
        return None
    want, got = _read_value(name, before), _read_value(name, after)
    if want is _INVALID:
        return None if got is None or got is _INVALID else "invalid value became valid"
    if want is None or got is None:
        return None
    if got is _INVALID:
        return "valid value became invalid"
    return None if want == got else "value changed"


_INVALID: Final = ("invalid",)
_SIDES: Final = ("top", "right", "bottom", "left")
_VALUE_KIND: Final[dict[str, str]] = {
    **dict.fromkeys(
        ("color", "background-color", "outline-color", *(f"border-{side}-color" for side in _SIDES)), "color"
    ),
    **dict.fromkeys(
        (
            *_SIDES,
            *(f"{box}-{side}" for box in ("margin", "padding") for side in _SIDES),
            *(f"border-{side}-width" for side in _SIDES),
            *(f"{bound}-{axis}" for bound in ("min", "max") for axis in ("width", "height")),
            "width",
            "height",
            "text-indent",
            "letter-spacing",
            "word-spacing",
            "font-size",
            "outline-width",
        ),
        "length",
    ),
    "line-height": "line-height",
    "z-index": "integer",
    "opacity": "alpha",
    "font-weight": "weight",
}
_LENGTH_UNITS: Final = frozenset({
    "px", "em", "rem", "ex", "ch", "vw", "vh", "vmin", "vmax", "cm", "mm", "q", "in", "pt", "pc", "%",
})  # fmt: skip
_NUMERIC: Final = re.compile(r"([+-]?(?:\d+\.\d+|\d+|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]+|%)?")
# CSS Syntax 3 4.3.12: a "." starts a fraction only before a digit, so "1." is a number and a stray "." delim
_TRAILING_DOT: Final = re.compile(r"[+-]?\d+\.(?:[a-zA-Z]+|%)?")
_NAMED_COLORS: Final = {
    "red": (255, 0, 0, 1.0),
    "white": (255, 255, 255, 1.0),
    "black": (0, 0, 0, 1.0),
    "navy": (0, 0, 128, 1.0),
    "transparent": (0, 0, 0, 0.0),
}


def _read_value(name: str, value: str) -> object:
    """
    Read one computed value with its property's grammar, for the properties the generator writes.

    Returns a comparable value, ``_INVALID`` when the grammar rejects it, or None when it falls outside what this reader
    covers. Sources: CSS Values 4 (lengths, a unitless zero, integers), CSS Color 4 (hex, ``rgb()`` legacy and modern
    syntax, named colors, opacity percentages), CSS Fonts 4 (``bold`` and ``normal`` are 700 and 400).
    """
    text = value.strip()
    kind = _VALUE_KIND.get(name)
    if _TRAILING_DOT.fullmatch(text):
        return _INVALID
    if kind == "color":
        return _read_color(text.lower())
    if (numeric := _NUMERIC.fullmatch(text)) is not None:
        return _read_number(kind, numeric[1], (numeric[2] or "").lower())
    keyword = text.lower()
    if re.fullmatch(r"-?[a-z][a-z-]*", keyword) is None:
        return None
    return {"bold": 700.0, "normal": 400.0}.get(keyword, keyword) if kind == "weight" else keyword


def _read_number(kind: str | None, digits: str, unit: str) -> object:
    number = float(digits)
    readers: dict[str | None, Callable[[], object]] = {
        "integer": lambda: int(number) if not unit and re.fullmatch(r"[+-]?\d+", digits) else _INVALID,
        "alpha": lambda: {"": number, "%": number / 100}.get(unit, _INVALID),
        "weight": lambda: number if not unit and 1 <= number <= 1000 else _INVALID,
        "length": lambda: _read_length(number, unit),
        "line-height": lambda: ("number", number) if not unit else _read_length(number, unit),
    }
    return readers.get(kind, lambda: None)()


def _read_length(number: float, unit: str) -> object:
    if unit and unit not in _LENGTH_UNITS:
        return _INVALID
    if number == 0 and unit != "%":
        return ("length", 0.0, "")
    return ("length", number, unit) if unit else _INVALID


def _read_color(text: str) -> object:
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    if text.startswith("#"):
        digits = text[1:]
        if re.fullmatch(r"[0-9a-f]+", digits) is None or len(digits) not in {3, 4, 6, 8}:
            return _INVALID
        full = "".join(digit * 2 for digit in digits) if len(digits) < 6 else digits
        channels = [int(full[index : index + 2], 16) for index in range(0, len(full), 2)]
        return (*channels[:3], round(channels[3] / 255, 3) if len(channels) == 4 else 1.0)
    if (function := re.fullmatch(r"rgba?\((.*)\)", text, re.DOTALL)) is None or re.search(r"[a-z(]", function[1]):
        return None  # other color functions, calc(), var() and none need a full value parser
    return _read_rgb(function[1])


def _read_rgb(arguments: str) -> object:
    """Read ``rgb()`` arguments: three comma- or space-separated channels and an alpha, after ``/`` in the latter."""
    parts = [part.strip() for part in arguments.split(",")]
    legacy = len(parts) > 1
    if not legacy:
        modern = re.fullmatch(r"(\S+)\s+(\S+)\s+(\S+)(?:\s*/\s*(\S+))?", parts[0])
        parts = [] if modern is None else [part for part in modern.groups() if part is not None]
    elif any(re.search(r"\s", part) for part in parts):
        parts = []
    numbers = [match for part in parts if (match := _NUMERIC.fullmatch(part)) is not None and match[2] in {None, "%"}]
    if len(parts) not in {3, 4} or len(numbers) != len(parts):
        return _INVALID
    if legacy and len({match[2] for match in numbers[:3]}) != 1:
        return _INVALID  # the comma syntax takes three numbers or three percentages, never a mix
    channels = [round(min(max(float(match[1]) * (2.55 if match[2] else 1), 0), 255)) for match in numbers[:3]]
    alpha = 1.0 if len(numbers) == 3 else float(numbers[3][1]) / (100 if numbers[3][2] else 1)
    return (*channels, round(min(max(alpha, 0.0), 1.0), 3))


# several spellings of one value each, so the generator writes values the minifier rewrites
_VALUE_SPELLINGS: Final[dict[str, tuple[str, ...]]] = {
    "red": ("red", "#f00", "#ff0000", "#F00", "#FF0000", "rgb(255,0,0)", "rgb(255 0 0)", "#ff0000ff", "#f00f"),
    "white": ("white", "#fff", "#ffffff", "#FFF", "rgb(255,255,255)", "#ffffffff"),
    "black": ("black", "#000", "#000000", "rgb(0,0,0)", "#000000ff"),
    "navy": ("navy", "#000080", "rgb(0,0,128)"),
    "transparent": ("transparent", "#0000", "#00000000", "rgba(0,0,0,0)"),
    "0": ("0", "0px", "0em", "0.0px", "-0px", "+0px", "00px", ".0px", "0.0", "-0", "+0"),
    "1.5px": ("1.5px", "1.50px", "+1.5px", "01.5px", "15e-1px", "0.15e1px"),
    "10px": ("10px", "1e1px", "10.0px", "+10px", "010px"),
    "1": ("1", "+1", "01"),
    "0.5": ("0.5", ".5", "0.50", "+.5", "5e-1"),
    "700": ("700", "bold"),
    "400": ("400", "normal"),
}


def xpath_entry_check(
    case: str,
    iterate: Callable[[Node, str], Sequence[object]] = lambda node, expression: list(node.xpath_iter(expression)),
    first: Callable[[Node, str], object] = lambda node, expression: node.xpath_one(expression),
) -> str | None:
    """
    Require ``xpath``, ``xpath_iter``, ``xpath_one`` and a compiled ``XPath`` to agree on one expression.

    pugixml evaluates one query through all its result APIs; here ``list(xpath_iter(e)) == xpath(e)`` (a scalar result
    is its single item), ``xpath_one(e)`` is the first item or None, ``XPath(e)(node) == xpath(e)``, and every entry
    point raises the same exception type when one does.
    """
    payload = json.loads(case)
    document = parse(payload["html"])
    expression = payload["xpath"]
    for context in [document, *list(document.iter_elements())[:6]]:
        result = _outcome(lambda context=context: context.xpath(expression))
        compiled = _outcome(lambda context=context: XPath(expression)(context))
        iterated = _outcome(lambda context=context: iterate(context, expression))
        one = _outcome(lambda context=context: first(context, expression))
        if isinstance(result, _Raised):
            if {type(outcome) for outcome in (compiled, iterated, one)} != {_Raised} or len({
                outcome.kind for outcome in (result, compiled, iterated, one) if isinstance(outcome, _Raised)
            }) != 1:
                return "entry points disagree on raising"
            continue
        items = result if isinstance(result, list) else [result]
        if not _same(compiled, result):
            return "XPath()() != xpath()"
        if not _same(iterated, items):
            return "list(xpath_iter()) != xpath()"
        if not _same(one, items[0] if items else None):
            return "xpath_one() != first item"
    return None


@dataclass(frozen=True)
class _Raised:
    kind: str


def _outcome(call: Callable[[], object]) -> object:
    try:
        return call()
    except (ValueError, TypeError) as error:
        return _Raised(type(error).__name__)


def _same(left: object, right: object) -> bool:
    """Compare XPath results: nodes by identity (``Node.__eq__``), NaN equal to itself, lists item by item."""
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(starmap(_same, zip(left, right, strict=False)))
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    return type(left) is type(right) and left == right


def selector_entry_check(case: str, translate: Callable[[str], str] = css_to_xpath) -> str | None:
    """
    Require the CSS entry points to agree on one selector over one document.

    ``select(s) == xpath(css_to_xpath(s))`` from the document; a compiled ``Matcher`` selects, filters and matches what
    ``Node.select`` and ``Node.matches`` do; a scoped select returns exactly the matching descendants, in order; and
    ``closest`` finds the nearest matching ancestor-or-self.
    """
    payload = json.loads(case)
    document = parse(payload["html"])
    selector = payload["css"]
    try:
        selected = document.select(selector)
        matcher = compile_selector(selector)
    except SelectorSyntaxError as error:
        raise OutOfScopeError from error
    if (problem := _matcher_problem(document, selector, matcher, selected)) is not None:
        return problem
    try:
        translated = translate(selector)
    except ExpressionError:
        return None
    return None if document.xpath(translated) == selected else "xpath(css_to_xpath()) != select()"


def _matcher_problem(document: Document, selector: str, matcher: Matcher, selected: list[Element]) -> str | None:
    elements = list(document.iter_elements())
    # a node can come back as a fresh wrapper (free-threaded builds), so match by node equality, not id()
    matching = set(selected)
    first = selected[0] if selected else None
    problems = (
        ([element for element in elements if element.matches(selector)] != selected, "matches() != select()"),
        (matcher.select(document) != selected or matcher.filter(elements) != selected, "Matcher != select()"),
        (first not in {matcher.select_one(document), document.select_one(selector)}, "select_one() != first match"),
    )
    if (problem := next((label for broken, label in problems if broken), None)) is not None:
        return problem
    for scope in elements[:8]:
        if scope.select(selector) != [element for element in scope.iter_elements() if element in matching]:
            return "scoped select() != matching descendants"
        nearest = next((element for element in [scope, *scope.ancestors] if element in matching), None)
        if matcher.closest(scope) != nearest:
            return "closest() != nearest matching ancestor"
    return None


def span_check(
    markup: str, locate: Callable[[Element], SourceLocation | None] = lambda element: element.source_location
) -> str | None:
    """
    Require source spans that slice their constructs out of the source.

    html5gum and Babel's position fuzzing check the same: every span lies inside the newline-normalized source with a
    line and column that match its offset; a start tag span begins with ``<`` and the tag name, an end tag span with
    ``</``, both end with ``>``; attribute spans sit inside their start tag and begin with the attribute name; distinct
    tag spans never overlap; ``source_line``/``source_col`` name the start tag; a comment prefix shifts every span by
    exactly its length; and an ``IncrementalParser`` fed in pieces reports the spans a one-shot parse does.
    """
    source = markup.replace("\r\n", "\n").replace("\r", "\n")
    document = parse(markup, source_locations=True)
    elements = [element for element in document.iter_elements() if locate(element) is not None]
    tokens: set[tuple[int, int]] = set()
    for element in elements:
        if (problem := _element_span_problem(source, element, locate)) is not None:
            return problem
        location = locate(element)
        assert location is not None  # ruff: ignore[assert]  # filtered above
        tokens.update((span.start_offset, span.end_offset) for span in (location.start_tag, location.end_tag) if span)
    ordered = sorted(tokens)
    if any(end > start for (_, end), (start, _) in pairwise(ordered)):
        return "tag spans overlap"
    prefix = f"<!--{chr(10) * (len(markup) % 3)}{'x' * (len(markup) % 5)}-->"
    shifted = parse(prefix + markup, source_locations=True)
    if [element.tag for element in shifted.iter_elements()] != [element.tag for element in document.iter_elements()]:
        return "a comment prefix changed the tree"
    expected = [_shift(locate(element), prefix) for element in document.iter_elements()]
    if [locate(element) for element in shifted.iter_elements()] != expected:
        return "a comment prefix shifted spans inexactly"
    incremental = IncrementalParser(source_locations=True)
    for start in range(0, len(markup), step := len(markup) // 3 + 1):
        incremental.feed(markup[start : start + step])
    if [locate(element) for element in incremental.close().iter_elements()] != [
        locate(element) for element in document.iter_elements()
    ]:
        return "IncrementalParser spans != parse() spans"
    return None


def _element_span_problem(
    source: str, element: Element, locate: Callable[[Element], SourceLocation | None]
) -> str | None:
    location = locate(element)
    assert location is not None  # ruff: ignore[assert]  # the caller passes located elements only
    start, end = location.start_tag, location.end_tag
    if (problem := _span_problem(source, start)) is not None:
        return f"start tag {problem}"
    # the tokenizer reads a NUL in a tag or attribute name as U+FFFD (WHATWG 13.2.5.8, 13.2.5.33)
    text = source[start.start_offset : start.end_offset].replace("\0", "\ufffd")
    # the tree builder renames an "image" start tag to "img" (WHATWG 13.2.6.4.7, in body)
    names = {element.tag.lower(), "image"} if element.tag == "img" else {element.tag.lower()}
    if not text.endswith(">") or not any(
        re.match(f"<{re.escape(name)}[\t\n\f />]", text, re.IGNORECASE) for name in names
    ):
        return "start tag span does not slice <name ...>"
    if (element.source_line, element.source_col) != (start.start_line, start.start_col):
        return "source_line/source_col != start tag start"
    if end is not None and (problem := _end_tag_problem(source, start, end)) is not None:
        return problem
    return _attribute_span_problem(source, start, location.attrs)


def _end_tag_problem(source: str, start: SourceSpan, end: SourceSpan) -> str | None:
    if (problem := _span_problem(source, end)) is not None:
        return f"end tag {problem}"
    if end.start_offset < start.end_offset or not source[end.start_offset : end.end_offset].startswith("</"):
        return "end tag span does not slice </name> after the start tag"
    return None


def _attribute_span_problem(source: str, start: SourceSpan, attrs: dict[str, SourceSpan]) -> str | None:
    previous = start.start_offset
    for name, span in sorted(attrs.items(), key=lambda item: item[1].start_offset):
        if (problem := _span_problem(source, span)) is not None:
            return f"attribute {problem}"
        if span.start_offset < previous or span.end_offset > start.end_offset:
            return "attribute span outside its start tag or overlapping"
        if not source[span.start_offset : span.end_offset].replace("\0", "\ufffd").lower().startswith(name.lower()):
            return "attribute span does not begin with its name"
        previous = span.end_offset
    return None


def _span_problem(source: str, span: SourceSpan) -> str | None:
    if not 0 <= span.start_offset < span.end_offset <= len(source):
        return "span empty or outside the source"
    for offset, line, column in (
        (span.start_offset, span.start_line, span.start_col),
        (span.end_offset, span.end_line, span.end_col),
    ):
        if (line, column) != (source.count("\n", 0, offset) + 1, offset - source.rfind("\n", 0, offset) - 1):
            return "line or column disagrees with offset"
    return None


def _shift(location: SourceLocation | None, prefix: str) -> SourceLocation | None:
    if location is None:
        return None
    lines = prefix.count("\n")
    tail = len(prefix) - prefix.rfind("\n") - 1

    def move(span: SourceSpan) -> SourceSpan:
        return SourceSpan(
            span.start_line + lines,
            span.start_col + tail * (span.start_line == 1),
            span.start_offset + len(prefix),
            span.end_line + lines,
            span.end_col + tail * (span.end_line == 1),
            span.end_offset + len(prefix),
        )

    return SourceLocation(
        move(location.start_tag),
        None if location.end_tag is None else move(location.end_tag),
        {name: move(span) for name, span in location.attrs.items()},
    )


def _css_fixpoint(text: str, printer: Callable[[str], str] = minify_css) -> str | None:
    if _nesting(text) > _CSS_MAX_NESTING:
        raise OutOfScopeError
    return fixpoint_check(text, printer, numeric=True)


def _nesting(text: str) -> int:
    depth = deepest = 0
    for char in text:
        depth += (char in "{([") - (char in "})]")
        deepest = max(deepest, depth)
    return deepest


def _js_fixpoint(text: str) -> str | None:
    # minify_js accepts some scripts acorn rejects (#984), whose output is not a program anyone wrote
    if _JS_NAMES.read(text) is None:
        raise OutOfScopeError
    return fixpoint_check(text, lambda source: minify_js(source, _JS_OFF), numeric=True)


_JS_NAMES: Final = JsNames()


def _js_names(text: str) -> str | None:
    return js_names_check(text, _JS_NAMES)


@dataclass(frozen=True)
class _Corpus:
    html: tuple[str, ...]
    css: tuple[str, ...]
    js: tuple[str, ...]


@cache
def _corpus() -> _Corpus:
    """Read the vendored seed inputs once; a missing source is a setup error, never a silent skip."""
    fuzz_corpus = _ROOT / "tools" / "fuzz" / "corpus"
    regressions = _ROOT / "tests" / "fuzz_regressions"
    tree_tests = _require(_ROOT / "tests" / "html5lib-tests" / "tree-construction")
    html = [
        data
        for path in sorted(tree_tests.glob("*.dat"))
        for data in re.findall(r"^#data\n(.*?)\n#errors", path.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
    ]
    html_files = [
        *(path for name in ("parse", "roundtrip", "minify_html") for path in (fuzz_corpus / name).iterdir()),
        *regressions.glob("*.html"),
    ]
    golden = _require(_ROOT / "tests" / "css" / "minify" / "css_minify_golden.json")
    css_files = [*(fuzz_corpus / "minify_css").iterdir(), *regressions.glob("*.css")]
    js = [
        row["input"]
        for path in sorted(_require(_ROOT / "tests" / "js" / "_corpus").glob("*.json"))
        for row in json.loads(path.read_text(encoding="utf-8"))
    ]
    return _Corpus(
        tuple(dict.fromkeys([*html, *_texts(html_files)])),
        tuple(dict.fromkeys([*(row[0] for row in json.loads(golden.read_text(encoding="utf-8"))), *_texts(css_files)])),
        tuple(dict.fromkeys([*js, *_texts(regressions.glob("*.js"))])),
    )


def _texts(paths: Iterable[Path]) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(paths)]


def _require(path: Path) -> Path:
    if not path.exists():
        msg = f"{path} is missing; tox -e fuzz-round-trip checks out the seed submodules"
        raise FileNotFoundError(msg)
    return path


def _generated(make: Callable[[random.Random], str], count: int = 150) -> Callable[[], list[str]]:
    """Seed an oracle with a fixed batch from its generator, the same on every run."""
    return lambda: [make(rng) for rng in [random.Random(0)] for _ in range(count)]


def _mutate(rng: random.Random, text: str, tokens: Sequence[str]) -> str:
    """Apply one to six edits: insert a token, delete or duplicate a slice, or flip a character's case."""
    for _ in range(rng.randint(1, 6)):
        at = rng.randint(0, len(text))
        end = min(len(text), at + rng.randint(1, 8))
        draw = rng.random()
        if draw < 0.5 or not text:
            text = text[:at] + rng.choice(tokens) + text[at:]
        elif draw < 0.7:
            text = text[:at] + text[end:]
        elif draw < 0.9:
            text = text[:end] + text[at:end] + text[end:]
        else:
            text = text[:at] + text[at:end].swapcase() + text[end:]
    return text


_CLASSES: Final = ("a", "b", "c", "a b", "b c")
_IDS: Final = ("x", "y", "z")


_HTML_TOKENS: Final = (
    "<p>", "</p>", "<b>", "</b>", "<table>", "<td>", "<svg>", "<math>", "<template>", "<script>", "</script>", "<!--",
    "-->", "<![CDATA[", "]]>", "<a href=x>", "</a>", "<select>", "<option>", "<br>", "</br>", "<image>", "&amp;",
    "&#x0;", "\r", "\n", "<!DOCTYPE html>", "<frameset>", "<plaintext>", "<textarea>", "<style>", "<noscript>",
    "<foreignObject>", "<desc>", "<mi>", "<annotation-xml encoding=text/html>", "<li>", "<dd>", "<form>", "</form>",
    "<?x?>", "<x:y>", 'a="b"', "\x00", "<head>", "<body>", "</html>", "<col>", "<tr>", "<caption>",
)  # fmt: skip


def _generate_html(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return _mutate(rng, rng.choice(_corpus().html), _HTML_TOKENS)
    return html_generate(rng).data.decode("utf-8")


_MARKDOWN_TEXT: Final = (
    "word", "two words", "*", "_", "#", "|", "[", "]", "(", ")", "\\", "`", "&amp;", "&lt;", "&gt;", "1.", "2)", "-",
    "+", "=", "~", "!", "<", ">", "'", '"', "&nbsp;", "---", "***", "```", "~~", "x*y", "a_b_c", "[x](y)", "\U0001f600",
    "é", "http://e.x", " ", "  ",
)  # fmt: skip


def _markdown_source_generate(rng: random.Random) -> str:
    return markdown_generate(rng).data.decode("utf-8")


def _markdown_html_generate(rng: random.Random) -> str:
    return markdown_generate(rng, html=True).data.decode("utf-8")


def _markdown_source_check(source: str) -> str | None:
    try:
        return markdown_source_check(source)
    except MarkdownProfileError as error:
        raise OutOfScopeError from error


def _markdown_html_check(source: str) -> str | None:
    try:
        return markdown_html_check(source)
    except MarkdownProfileError as error:
        raise OutOfScopeError from error


def _markdown_document(rng: random.Random) -> str:
    """
    Build conforming HTML that Markdown can express: flow blocks holding phrasing content, never a block inside it.

    CommonMark has no syntax for a block inside an inline, so a parser-made tree that nests one (a ``<pre>`` inside a
    ``<del>``) has no faithful Markdown form; the text in these blocks carries every Markdown-significant character.
    """
    return "".join(_markdown_block(rng, 0) for _ in range(rng.randint(1, 4)))


def _markdown_block(rng: random.Random, depth: int) -> str:
    inline = partial(_markdown_inline, rng, 0)
    choices: list[Callable[[], str]] = [
        lambda: f"<p>{inline()}</p>",
        lambda: f"<p>{inline()}</p>",
        lambda: f"<h{(level := rng.randint(1, 6))}>{inline()}</h{level}>",
        lambda: f"<pre><code>{rng.choice(_MARKDOWN_TEXT)}\n{rng.choice(_MARKDOWN_TEXT)}</code></pre>",
        lambda: "<hr>",
        lambda: _markdown_table(rng, rng.randint(1, 3)),
    ]
    if depth < 2:
        choices += [
            lambda: f"<blockquote>{_markdown_block(rng, depth + 1)}</blockquote>",
            lambda: (
                f"<{(kind := rng.choice(('ul', 'ol')))}>"
                + "".join(
                    f"<li>{inline() if rng.random() < 0.7 else _markdown_block(rng, depth + 1)}</li>"
                    for _ in range(rng.randint(1, 3))
                )
                + f"</{kind}>"
            ),
        ]
    return rng.choice(choices)()


def _markdown_table(rng: random.Random, columns: int) -> str:
    head = "".join(f"<th>{_markdown_inline(rng, 0)}</th>" for _ in range(columns))
    body = "".join(f"<td>{_markdown_inline(rng, 0)}</td>" for _ in range(columns))
    return f"<table><tr>{head}</tr><tr>{body}</tr></table>"


def _markdown_inline(rng: random.Random, depth: int) -> str:
    out = []
    for _ in range(rng.randint(1, 4)):
        draw = rng.random()
        if draw < 0.5 or depth >= 2:
            out.append(rng.choice(_MARKDOWN_TEXT))
        elif draw < 0.75:
            tag = rng.choice(("em", "strong", "del", "b", "i"))
            out.append(f"<{tag}>{_markdown_inline(rng, depth + 1)}</{tag}>")
        elif draw < 0.85:
            out.append(f"<code>{rng.choice(_MARKDOWN_TEXT)}</code>")
        elif draw < 0.93:
            out.append(
                f'<a href="{rng.choice(("x", "a b", "(", "<", "http://e.x/?a=1&amp;b"))}">{rng.choice(_MARKDOWN_TEXT)}</a>'
            )
        elif draw < 0.97:
            out.append(f'<img src="i" alt="{rng.choice(_MARKDOWN_TEXT)}">')
        else:
            out.append("<br>")
    return "".join(out)


_CSS_TOKENS: Final = (
    "{", "}", ";", ":", "!important", "/*x*/", "/*!b*/", "@media screen{", "@supports (display:grid){", "url(a b)",
    'url("x")', '"s\\"t"', "'q'", "\\", "\\61", "#fff", "0px", "1e3", "+.5", "-0", "calc(1px + 2px)", "var(--x)",
    "--x:", "a,b", ">", "+", "~", "*", "[x=y]", ":not(", ")", "::before", "@import", "@charset", "\n", " ", "%",
)  # fmt: skip
_SELECTOR_TAGS: Final = ("div", "p", "span", "a", "b", "i", "li", "ul", "section", "em")
_PSEUDOS: Final = (
    ":first-child", ":last-child", ":only-child", ":nth-child(2n+1)", ":nth-child(odd)", ":nth-child(even)",
    ":nth-last-child(-n+3)", ":nth-of-type(2)", ":first-of-type", ":last-of-type", ":empty", ":root", ":link",
    ":any-link", ":checked", ":disabled", ":enabled", ":lang(en)", ":nth-child(+0n+1)", ":nth-child(1 of .a)",
    ":only-of-type", ":defined", ":required", ":optional", ":read-only", ":read-write", ":placeholder-shown",
)  # fmt: skip


def _selector(rng: random.Random, depth: int = 0) -> str:
    """Draw a selector list over the generators' tag, class, id and attribute vocabulary."""
    return ", ".join(_complex_selector(rng, depth) for _ in range(1 if rng.random() < 0.8 else rng.randint(2, 3)))


def _complex_selector(rng: random.Random, depth: int) -> str:
    parts = [_compound(rng, depth)]
    for _ in range(rng.choice((0, 0, 1, 1, 2, 3))):
        parts += [rng.choice((" ", " > ", ">", " + ", "~", "  ")), _compound(rng, depth)]
    return "".join(parts)


def _compound(rng: random.Random, depth: int) -> str:
    out = rng.choice(("", "", "*", *_SELECTOR_TAGS, "DIV", "P"))
    for _ in range(rng.choice((0, 1, 1, 2, 3)) if out else rng.randint(1, 3)):
        draw = rng.random()
        if draw < 0.25:
            out += "." + rng.choice(("a", "b", "c", "\\61", "A"))
        elif draw < 0.35:
            out += "#" + rng.choice(_IDS)
        elif draw < 0.6:
            name = rng.choice(("title", "data-x", "lang", "class", "id", "href", "TITLE"))
            operator = rng.choice(("", "=", "~=", "|=", "^=", "$=", "*="))
            value = rng.choice(("a", '"b c"', "'x'", '""', "e", "A", "en")) if operator else ""
            flag = rng.choice(("", "", " i", " s", " I")) if operator else ""
            out += f"[{name}{operator}{value}{flag}]"
        elif draw < 0.85 or depth >= 2:
            out += rng.choice(_PSEUDOS)
        else:
            function = rng.choice((":not", ":is", ":where", ":has", ":has", ":not"))
            argument = _selector(rng, depth + 1)
            out += f"{function}({rng.choice(('> ', '+ ', '~ ', '')) if function == ':has' else ''}{argument})"
    return out


def _vocabulary_tree(rng: random.Random, budget: int = 25) -> str:
    """Markup whose tags, classes, ids and attributes are the ones the selector and XPath generators draw from."""
    out: list[str] = []
    stack: list[str] = []
    for _ in range(rng.randint(3, budget)):
        if (draw := rng.random()) < 0.5:
            tag = rng.choice(_SELECTOR_TAGS)
            attrs = "".join(
                f' {name}="{rng.choice(values)}"'
                for name, values in (
                    ("class", _CLASSES),
                    ("id", _IDS),
                    ("title", ("a", "b c", "A", "x", "")),
                    ("lang", ("en", "en-US", "fr")),
                    ("data-x", ("e", "a", "")),
                    ("href", ("#", "x")),
                )
                if rng.random() < 0.25
            )
            out.append(f"<{tag}{attrs}>")
            stack.append(tag)
        elif draw < 0.75 and stack:
            out.append(f"</{stack.pop()}>")
        else:
            out.append(rng.choice(("x", "hello", " ", "", "<!--c-->", "y z")))
    return "".join(out)


_LENGTHS: Final = (*_VALUE_SPELLINGS["0"], *_VALUE_SPELLINGS["1.5px"], *_VALUE_SPELLINGS["10px"], "2em", "5%", "auto")
_COLORS: Final = (
    *_VALUE_SPELLINGS["red"], *_VALUE_SPELLINGS["white"], *_VALUE_SPELLINGS["black"], *_VALUE_SPELLINGS["navy"],
    *_VALUE_SPELLINGS["transparent"], "currentcolor", "currentColor", "inherit",
)  # fmt: skip
_SEMANTIC_VALUES: Final[dict[str, tuple[str, ...]]] = {
    "color": _COLORS,
    "background-color": _COLORS,
    "border-top-color": _COLORS,
    "outline-color": _COLORS,
    "margin-top": _LENGTHS,
    "margin-right": _LENGTHS,
    "margin-bottom": _LENGTHS,
    "margin-left": _LENGTHS,
    "padding-top": _LENGTHS,
    "padding-left": _LENGTHS,
    "width": _LENGTHS,
    "height": _LENGTHS,
    "top": _LENGTHS,
    "text-indent": _LENGTHS,
    "letter-spacing": (*_LENGTHS, "normal"),
    "font-weight": ("bold", "700", "normal", "400", "bolder", "lighter", "100", "900"),
    "opacity": ("1", "+1", "01", "0.5", ".5", "0.50", "50%", "0", "0.0", "inherit"),
    "z-index": ("1", "+1", "01", "auto", "-1", "2", "1.0"),
    "display": ("block", "inline", "none", "flex", "BLOCK", "initial"),
    "text-align": ("left", "center", "right", "start", "justify"),
    "visibility": ("visible", "hidden", "collapse", "unset"),
    "border-top-style": ("solid", "dashed", "none"),
    "line-height": ("1.5", "normal", "10px", "150%", "1.50"),
    "font-size": ("10px", "1e1px", "medium", "small", "1.5em", "0"),
    "margin": (),
    "padding": (),
    "border": (),
    "border-color": (),
    "border-width": (),
    "border-style": (),
    "background": (),
    "outline": (),
}


def _declaration(rng: random.Random) -> str:
    name = rng.choice(list(_SEMANTIC_VALUES))
    if values := _SEMANTIC_VALUES[name]:
        value = rng.choice(values)
    elif name in {"margin", "padding", "border-width"}:
        value = " ".join(rng.choice(_LENGTHS) for _ in range(rng.randint(1, 4)))
    elif name == "border-color":
        value = " ".join(rng.choice(_COLORS[:-1]) for _ in range(rng.randint(1, 4)))
    elif name == "border-style":
        value = " ".join(rng.choice(("solid", "dashed", "none")) for _ in range(rng.randint(1, 4)))
    elif name == "background":
        value = rng.choice(_COLORS[:-1])
    else:
        value = f"{rng.choice(_LENGTHS[:-1])} {rng.choice(('solid', 'dashed'))} {rng.choice(_COLORS[:-1])}"
    return f"{name}:{value}{' !important' if rng.random() < 0.15 else ''}"


def _stylesheet(rng: random.Random) -> str:
    """Draw rules over a small selector and declaration pool, so the minifier finds rules and longhands to merge."""
    selectors = [_selector(rng) for _ in range(rng.randint(1, 5))]
    rules = []
    for _ in range(rng.randint(1, 8)):
        body = ";".join(_declaration(rng) for _ in range(rng.randint(1, 5)))
        if rng.random() < 0.2:
            side = rng.choice(("margin", "padding"))
            body += "".join(f";{side}-{edge}:{rng.choice(_LENGTHS)}" for edge in ("top", "right", "bottom", "left"))
        rule = f"{rng.choice(selectors)}{{{body}}}"
        rules.append(f"@media all{{{rule}}}" if rng.random() < 0.1 else rule)
    return rng.choice(("", "\n", " /*c*/ ")).join(rules)


def _generate_css(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return _mutate(rng, rng.choice(_corpus().css), _CSS_TOKENS)
    return _mutate(rng, _stylesheet(rng), _CSS_TOKENS) if rng.random() < 0.3 else _stylesheet(rng)


def _generate_style(rng: random.Random) -> str:
    body = ";".join(_declaration(rng) for _ in range(rng.randint(1, 6)))
    return _mutate(rng, body, _CSS_TOKENS) if rng.random() < 0.6 else body


def _generate_semantic(rng: random.Random) -> str:
    inline = "" if rng.random() < 0.7 else f' style="{_declaration(rng)}"'
    return f"{_root(rng)}<style>{_stylesheet(rng)}</style><div{inline}>{_vocabulary_tree(rng)}</div>"


def _root(rng: random.Random) -> str:
    """
    Open a no-quirks document whose root element sometimes carries attributes, so root-only matches show.

    Quirks mode matches class and id selectors case-insensitively (Selectors 4 §6.6, DOM §4.2.6.1), which a selector
    translated to XPath cannot know, so every generated document declares the standard mode.
    """
    attrs = "".join(
        f' {name}="{value}"' for name, value in (("lang", "en"), ("class", "a"), ("title", "x")) if rng.random() < 0.3
    )
    return f"<!DOCTYPE html><html{attrs}>"


def _generate_xpath_case(rng: random.Random) -> str:
    return json.dumps({"html": _root(rng) + _vocabulary_tree(rng), "xpath": _xpath(rng)})


def _generate_selector_case(rng: random.Random) -> str:
    return json.dumps({"html": _root(rng) + _vocabulary_tree(rng), "css": _selector(rng)})


_AXES: Final = (
    "child", "descendant", "descendant-or-self", "parent", "ancestor", "ancestor-or-self", "following-sibling",
    "preceding-sibling", "following", "preceding", "self", "attribute",
)  # fmt: skip
_NODE_TESTS: Final = ("*", "node()", "text()", "comment()", "processing-instruction()", *_SELECTOR_TAGS)
_PREDICATES: Final = (
    "1", "2", "last()", "last()-1", "position()<3", "position()=last()", "@id", "@class='a'", "@title", "not(@id)",
    "name()='p'", "count(*)>1", "contains(.,'x')", "starts-with(@class,'a')", "string-length()>2", "text()='x'",
    "*", "b", "0", "true()", ".//a", "@*", "lang('en')", "normalize-space()", "-1", "1.5", "@data-x='e'",
)  # fmt: skip


def _xpath(rng: random.Random) -> str:
    """Draw an XPath 1.0 expression: a location path, a union, or a function or arithmetic over paths."""
    path = _path(rng)
    return rng.choice((
        path,
        path,
        path,
        f"{path} | {_path(rng)}",
        f"count({path})",
        f"string({path})",
        f"sum({path})",
        f"boolean({path})",
        f"name({path})",
        f"({path})[{rng.choice(_PREDICATES)}]",
        f"count({path}) * 2 - 1 div 0",
        f"concat({path}, '-', {_path(rng)})",
        f"normalize-space({path})",
        f"number({path}) mod 3",
        f"{path} = {_path(rng)}",
        f"{path} != 'x'",
        "0 div 0",
        "-0",
    ))


def _path(rng: random.Random) -> str:
    steps = []
    for _ in range(rng.randint(1, 3)):
        if (draw := rng.random()) < 0.15:
            step = rng.choice(("..", ".", "@*", "@id", "@class"))
        else:
            axis = rng.choice(_AXES) + "::" if draw < 0.55 else ""
            step = axis + rng.choice(_NODE_TESTS)
        steps.append(step + "".join(f"[{rng.choice(_PREDICATES)}]" for _ in range(rng.choice((0, 0, 1, 2)))))
    separators = [rng.choice(("/", "//")) for _ in steps]
    return rng.choice(("/", "//", "", "")) + "".join(
        step if index == 0 else separators[index] + step for index, step in enumerate(steps)
    )


_JS_FREE: Final = ("console", "window", "Math", "JSON", "undefined", "NaN", "Infinity", "globalThis", "zz")
_JS_BOUND: Final = ("a", "b", "c", "foo", "bar", "$", "_x", "\\u0061b", "of", "async", "yield", "let")
_JS_STRINGS: Final = (
    "'a'", '"b"', "'it\\'s'", '"\\x41"', "'\\u{1F600}'", '"\\n"', "''", '"use strict"', "'\\0'", '"q\\\r\nr"',
    "'</script>'", '"\\u2028"', "'a\"b'", '"1"', "'if'",
)  # fmt: skip
_JS_NUMBERS: Final = (
    "0",
    "1",
    "1.50",
    ".5",
    "5.",
    "1e3",
    "1E-7",
    "0x1F",
    "0o17",
    "0b101",
    "1_000",
    "123n",
    "0.1",
    "-0",
)
_JS_PROPERTIES: Final = ("a", "b", "if", "class", "length", "constructor", "1", "'q'", '"r s"', "0x10", "[c]", "1.5")


@dataclass
class _Script:
    """
    The state one generated script threads through: a source of fresh names and whether a ``return`` is legal.

    Fresh names keep ``let``/``const``/``class`` declarations from colliding, which acorn rejects as early errors;
    references draw from the declared and free pools, so most of them bind (Domato's scope-threading rule).
    """

    rng: random.Random
    declared: list[str] = field(default_factory=lambda: list(_JS_BOUND[:7]))
    counter: int = 0

    def fresh(self) -> str:
        self.counter += 1
        name = f"v{self.counter}"
        self.declared.append(name)
        return name

    def reference(self) -> str:
        return self.rng.choice((*_JS_FREE, *self.declared))


def _program(rng: random.Random) -> str:
    """Draw a script from statements and expressions chosen for printer hazards: ASI, operator spacing, keys."""
    script = _Script(rng)
    return "".join(_statement(script, 0, in_function=False) for _ in range(rng.randint(1, 6)))


def _end(rng: random.Random) -> str:
    return rng.choice((";", ";", ";", "\n", ";\n"))


def _statement(script: _Script, depth: int, *, in_function: bool) -> str:
    rng = script.rng

    def block(*, function: bool = in_function) -> str:
        statements = "".join(_statement(script, depth + 1, in_function=function) for _ in range(rng.randint(0, 3)))
        return "{" + statements + "}"

    def expression() -> str:
        return _expression(script, depth)

    choices: list[Callable[[], str]] = [
        lambda: f"var {rng.choice(_JS_BOUND)} = {expression()}{_end(rng)}",
        lambda: f"{rng.choice(('let', 'const'))} {script.fresh()} = {expression()}{_end(rng)}",
        lambda: f"{expression()}{_end(rng)}",
        lambda: f"{expression()}{_end(rng)}",
    ]
    if depth < 3:
        choices += [
            lambda: f"function {script.fresh()}({_parameters(rng)}){block(function=True)}",
            lambda: f"if ({expression()}) {block()} else {block()}",
            lambda: f"for (let {script.fresh()} = 0; {script.reference()} < {expression()}; i++) {block()}",
            lambda: f"for (const {script.fresh()} {rng.choice(('in', 'of'))} {expression()}) {block()}",
            lambda: f"try {block()} catch ({script.fresh()}) {block()} finally {block()}",
            lambda: f"switch ({expression()}) {{case {expression()}: {block()} default: break}}",
            lambda: f"{(label := script.fresh())}: for (;;) {{ break {label} }}",
            lambda: _class(script, depth),
            block,
        ]
    if in_function:
        choices.append(lambda: f"return{rng.choice((' ', chr(10)))}{expression()}{_end(rng)}")
    return rng.choice(choices)()


def _parameters(rng: random.Random) -> str:
    return ", ".join(rng.sample(_JS_BOUND[:7], rng.randint(0, 3)))


def _class(script: _Script, depth: int) -> str:
    rng = script.rng
    members = rng.sample(
        [
            f"m() {{ return {_expression(script, depth + 1)} }}",
            f"static s = {_expression(script, depth + 1)};",
            "#p = 1; get g() { return this.#p }",
            "'q'() {}",
            "1() {}",
            f"[{script.reference()}]() {{}}",
            "set h(v) {}",
        ],
        rng.randint(1, 4),
    )
    return f"class {script.fresh()} {rng.choice(('', 'extends Object '))}{{ {' '.join(members)} }}"


def _expression(script: _Script, depth: int) -> str:
    rng = script.rng
    if depth >= 3 or rng.random() < 0.35:
        return rng.choice((
            script.reference,
            lambda: rng.choice(_JS_STRINGS),
            lambda: rng.choice(_JS_NUMBERS),
            lambda: rng.choice(("true", "false", "null", "this", "/a+/g", "/[/]/", "`t`", "``")),
        ))()

    def sub() -> str:
        return _expression(script, depth + 1)

    def target() -> str:
        return rng.choice(script.declared[:7])

    return rng.choice((
        lambda: f"{sub()}.{rng.choice(('a', 'if', 'class', 'length'))}",
        lambda: f"{sub()}[{rng.choice(_JS_STRINGS + _JS_NUMBERS)}]",
        lambda: f"{sub()}?.{rng.choice(('a', 'b'))}",
        lambda: f"{sub()}?.[{sub()}]",
        lambda: f"{sub()}({', '.join(sub() for _ in range(rng.randint(0, 2)))})",
        lambda: f"new {rng.choice(_JS_FREE[:3])}({sub()})",
        lambda: f"({rng.choice(('typeof ', 'void ', '!', '-', '+', '~'))}{sub()})",
        lambda: f"delete {sub()}.a",
        lambda: f"({sub()} {rng.choice(_JS_BINARY)} {sub()})",
        lambda: f"{target()} {rng.choice(('+ +', '- -', '+ ++', '- --', '/ /x/ /'))}{target()}",
        lambda: f"({sub()} ? {sub()} : {sub()})",
        lambda: f"(({', '.join(rng.sample(_JS_BOUND[:5], rng.randint(0, 2)))}) => {sub()})",
        lambda: f"({{{rng.choice(_JS_PROPERTIES)}: {sub()}, {target()}, m() {{ return {sub()} }}, ...{sub()}}})",
        lambda: f"[{sub()}, , ...{sub()}]",
        lambda: f"({target()} {rng.choice(('=', '+=', '??=', '||=', '**='))} {sub()})",
        lambda: f"`a${{{sub()}}}b${{{sub()}}}`",
        lambda: f"{rng.choice(_JS_FREE[:3])}`x\\x41${{{sub()}}}`",
        lambda: f"(function {rng.choice(('', 'f'))}() {{ return {sub()} }})()",
        lambda: f"{rng.choice(_JS_NUMBERS[:6])} .toString()",
        lambda: f"([{target()}, {target()}] = {sub()})",
        lambda: f"({{a: {target()}, b = 1}} = {sub()})",
        lambda: f"{target()}++",
    ))()


_JS_BINARY: Final = (
    "+", "-", "*", "/", "%", "**", "==", "===", "!=", "<", ">=", "&&", "||", "??", "&", "|", "^", "<<", ">>>", "in",
    "instanceof", ",",
)  # fmt: skip


_JS_TOKENS: Final = ("\n", ";", "(", ")", "{", "}", ",", "+", "-", "/", "'", "`", "${", "in ", " of ", "=>", ".", "?.")


def _generate_js(rng: random.Random) -> str:
    if rng.random() < 0.25:
        return _mutate(rng, rng.choice(_corpus().js), _JS_TOKENS)
    return _program(rng)


def _generate_url(rng: random.Random) -> str:
    host = rng.choice(("Example.COM", "bücher.example", "127.0.0.1", "[::1]", "sub.example.org"))
    path = "/".join(rng.choices(("a", "..", ".", "%2e", "café", "a b", "%7e", ""), k=rng.randint(1, 6)))
    query = rng.choice(("", "?b=2&a=1", "?utm_source=x&id=2", "?q=a+b", "?q=%E2%9C%93"))
    fragment = rng.choice(("", "#frag", "#a b"))
    url = f"{rng.choice(('http', 'HTTPS'))}://{host}{rng.choice(('', ':80', ':443', ':8080'))}/{path}{query}{fragment}"
    return rng.choice((url, f" <{url}> ", url.replace("&", "&amp;")))


# RFC 3986 section 5.4 supplies independent targets; safe HTTP leaves avoid scheme-specific edge policies.
_RESOLUTION_BASE: Final = "http://example.test/b/c/d;p?q"
_RESOLUTION_CASES: Final[dict[str, tuple[str, str]]] = {
    "child": ("{leaf}", "http://example.test/b/c/{leaf}"),
    "current": ("./{leaf}", "http://example.test/b/c/{leaf}"),
    "parent": ("../{leaf}", "http://example.test/b/{leaf}"),
    "grandparent": ("../../{leaf}", "http://example.test/{leaf}"),
    "root": ("/{leaf}", "http://example.test/{leaf}"),
    "query": ("?{leaf}", "http://example.test/b/c/d;p?{leaf}"),
    "fragment": ("#{leaf}", "http://example.test/b/c/d;p?q#{leaf}"),
    "directory": ("{leaf}/", "http://example.test/b/c/{leaf}/"),
}


def _generate_resolution(rng: random.Random) -> str:
    return f"{rng.choice(tuple(_RESOLUTION_CASES))}:g{rng.randrange(10000)}"


def _resolution_controls() -> dict[str, bool]:
    return {
        "unchanged relative link": resolve_links_check("parent:g", lambda _node, _base: None) is not None,
        "stable wrong target": resolve_links_check("parent:g", _resolve_wrong_target) is not None,
        "changed repeat": resolve_links_check("parent:g", _resolve_changed_repeat) is not None,
    }


def _resolve_wrong_target(node: Node, _base: str) -> None:
    node.select("a")[0].attrs["href"] = "http://wrong.example/"


def _resolve_changed_repeat(node: Node, base: str) -> None:
    if node.links()[0].url.startswith("http:"):
        node.select("a")[0].attrs["href"] = node.links()[0].url + "a"
    else:
        node.resolve_links(base)


def _generate_encoding(rng: random.Random) -> str:
    variant: Final = rng.choice(tuple(_ENCODING_FORMATS))
    alphabet: Final = "abc 123café€" if _ENCODING_FORMATS[variant][0] == "cp1252" else "abc 123café€中文𐐀"
    return f"{variant}\n{''.join(rng.choices(alphabet, k=rng.randint(0, 64)))}"


def _encoding_stream_controls() -> dict[str, bool]:
    missing: Final = EncodingMatch(None, 0.0, None)
    return {
        "wrong one-shot label": encoding_stream_check("utf-8-bom\ncafé", lambda _data: missing) is not None,
        "wrong chunked label": encoding_stream_check("utf-8-bom\ncafé", stream=lambda _data, _width: missing)
        is not None,
    }


def _encoding_decode_controls() -> dict[str, bool]:
    return {
        "wrong codec text": encoding_decode_check("utf-8-meta\ncafé", decode=lambda _data, _codec: "") is not None,
        "wrong parsed text": encoding_decode_check("utf-8-meta\ncafé", parse_text=lambda _data: "") is not None,
        "wrong detected label": encoding_decode_check(
            "utf-8-meta\ncafé", detect_bytes=lambda _data: EncodingMatch(None, 0.0, None)
        )
        is not None,
    }


def _encoding_seeds() -> list[str]:
    return ["empty\n", *(f"{variant}\ncafé €" for variant in _ENCODING_FORMATS), *_generated(_generate_encoding, 150)()]


def _generate_idna_host(rng: random.Random) -> str:
    return f"{rng.choice(tuple(_IDNA_LABELS))}\n{''.join(rng.choices('abc012', k=rng.randint(0, 8)))}"


def _idna_host_seeds() -> list[str]:
    return [*(f"{variant}\n" for variant in _IDNA_LABELS), *_generated(_generate_idna_host, 150)()]


def _idna_host_controls() -> dict[str, bool]:
    return {
        "unchanged Unicode host": idna_host_check("umlaut\n", lambda text: text) is not None,
        "wrong mapping": idna_host_check("umlaut\n", lambda _text: "https://wrong.example/") is not None,
        "non-NFC label": idna_host_check("umlaut\n", lambda _text: "https://xn--u-ccb.example/") is not None,
        "changed canonical output": idna_host_check(
            "umlaut\n", lambda text: normalize_url(text) if not text.isascii() else text + "a"
        )
        is not None,
    }


def _normalize_url_controls() -> dict[str, bool]:
    return {
        "growing output": normalize_url_check("https://example.org/", lambda text: text + "a") is not None,
        "rejects normalized output": normalize_url_check("raw", _reject_url_output) is not None,
    }


def _url_reparse_controls() -> dict[str, bool]:
    return {
        "stable uppercased scheme": url_reparse_check("raw", lambda _text: "HTTPS://example.test/") is not None,
        "changes after recomposition": url_reparse_check(
            "raw", lambda text: "https://example.test/" if text == "raw" else "https://changed.test/"
        )
        is not None,
        "rejects recomposed output": url_reparse_check("raw", _reject_url_output) is not None,
    }


def _reject_url_output(text: str) -> str:
    if text == "raw":
        return "https://example.org/"
    msg = "rejected"
    raise ValueError(msg)


def _clean_url_controls() -> dict[str, bool]:
    return {
        "growing output": clean_url_check("https://example.org/", lambda text: text + "a") is not None,
        "drops cleaned output": clean_url_check("raw", lambda text: "https://example.org/" if text == "raw" else None)
        is not None,
        "rejects cleaned output": clean_url_check("raw", _reject_url_output) is not None,
    }


def _seeds_url() -> list[str]:
    # WPT supplies inputs: crawl cleaning differs from WHATWG serialization.
    wpt = _require(_ROOT / "tools/fuzz-data/wpt/url/resources/urltestdata.json")
    return list(
        dict.fromkeys([
            *(row["input"] for row in json.loads(wpt.read_text(encoding="utf-8")) if isinstance(row, dict)),
            *_texts((_ROOT / "tools/fuzz/corpus/url").iterdir()),
            *_generated(_generate_url, 500)(),
        ])
    )


def _html_controls() -> dict[str, bool]:
    return {
        "unescaped text": html_check("<p>&lt;b&gt;x</p>", lambda node: _unescape(node.serialize())) is not None,
        "text after an end tag": html_check("<b>x</b>y", lambda node: node.serialize().replace("</b>", "</b> "))
        is not None,
    }


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">")


def _xml_controls() -> dict[str, bool]:
    def xml(node: Node) -> str:
        return node.serialize(Html(xml=True))

    return {
        "unescaped ampersand": xml_check("<p>a&amp;b</p>", lambda node: xml(node).replace("&amp;", "&")) is not None,
        "dropped attribute": xml_check("<p a=1>x</p>", lambda node: xml(node).replace(' a="1"', "")) is not None,
    }


def _css_controls() -> dict[str, bool]:
    return {
        "growing output": _css_fixpoint("a{color:red}", lambda css: minify_css(css) + "/*!x*/") is not None,
        "late non-numeric convergence": fixpoint_check("aa", lambda text: text.replace("a", "b", 1), numeric=True)
        is not None,
    }


def _js_controls() -> dict[str, bool]:
    return {
        "growing output": fixpoint_check("x", lambda source: minify_js(source, _JS_OFF) + "\n0", numeric=True)
        is not None
    }


def _style_controls() -> dict[str, bool]:
    return {
        "growing output": style_check("a:b", lambda text: StyleDeclaration.parse(text + ";z:1")) is not None,
        "changed value": style_check("a:b", lambda text: StyleDeclaration.parse(text.replace(": ", ":x", 1)))
        is not None,
    }


def _markdown_controls() -> dict[str, bool]:
    return {"unescaped text": markdown_check("<p>*a*</p>", lambda node: node.text) is not None}


def _js_names_controls() -> dict[str, bool]:
    def broken(edit: Callable[[str], str]) -> Callable[[str, JSMinify], str]:
        return lambda source, options: edit(minify_js(source, options))

    return {
        "invented free identifier": js_names_check("x", _JS_NAMES, broken(lambda out: out + ";zz")) is not None,
        "lost string": js_names_check("f('q')", _JS_NAMES, broken(lambda out: re.sub(r"['\"]q['\"]", "0", out)))
        is not None,
        "captured free identifier": js_names_check(
            "function f(a){return b}", _JS_NAMES, lambda _source, _options: "function f(b){return b}"
        )
        is not None,
        "output not JavaScript": js_names_check("x", _JS_NAMES, broken(lambda out: out + "(")) is not None,
    }


def _css_semantics_controls() -> dict[str, bool]:
    return {
        "changed value": css_semantics_check(
            "<style>p{color:red}</style><p>x", lambda css: minify_css(css).replace("red", "#00f")
        )
        is not None,
        "rewritten selector": css_semantics_check(
            "<style>div>b{x-unknown:1}</style><div><p><b>x</b></p></div>", lambda css: minify_css(css).replace(">", " ")
        )
        is not None,
    }


def _xpath_controls() -> dict[str, bool]:
    case = json.dumps({"html": "<p></p><p></p>", "xpath": "//p"})
    return {
        "xpath_iter drops an item": xpath_entry_check(
            case, iterate=lambda node, expression: list(node.xpath_iter(expression))[1:]
        )
        is not None,
        "xpath_one returns the last": xpath_entry_check(
            case, first=lambda node, expression: list(node.xpath_iter(expression))[-1]
        )
        is not None,
    }


def _selector_controls() -> dict[str, bool]:
    case = json.dumps({"html": "<p>x</p>", "css": "p"})
    return {
        "translated to the child axis": selector_entry_check(
            case, lambda selector: css_to_xpath(selector, prefix="child::")
        )
        is not None
    }


def _span_controls() -> dict[str, bool]:
    def broken(edit: Callable[[SourceLocation], SourceLocation]) -> Callable[[Element], SourceLocation | None]:
        return lambda element: None if (location := element.source_location) is None else edit(location)

    def one_late(location: SourceLocation) -> SourceLocation:
        start = location.start_tag
        return location._replace(
            start_tag=start._replace(start_offset=start.start_offset + 1, start_col=start.start_col + 1)
        )

    return {
        "start one late": span_check("<p class=a>x</p>", broken(one_late)) is not None,
        "end tag at the start tag": span_check(
            "<p class=a>x</p>", broken(lambda location: location._replace(end_tag=location.start_tag))
        )
        is not None,
    }


def _seeds_html() -> list[str]:
    return list(_corpus().html)


def _seeds_css() -> list[str]:
    return list(_corpus().css)


def _seeds_js() -> list[str]:
    return list(_corpus().js)


def _seeds_style() -> list[str]:
    return [body for css in _corpus().css for body in re.findall(r"\{([^{}]*)\}", css)]


def _parser_bytes_check(case: str) -> str | None:
    try:
        return parser_bytes_check(case)
    except UnsupportedParserCaseError as error:
        raise OutOfScopeError from error


def _xml_island(case: str) -> str | None:
    try:
        return xml_island_check(case)
    except UnsupportedXmlIslandCaseError as error:
        raise OutOfScopeError(str(error)) from error


def _xml_literal(case: str) -> str | None:
    try:
        return xml_literal_check(case)
    except UnsupportedXmlLiteralCaseError as error:
        raise OutOfScopeError(str(error)) from error


ORACLES: Final[dict[str, Oracle]] = {
    "encoding-stream": Oracle(
        encoding_stream_check, _generate_encoding, _encoding_seeds, _encoding_stream_controls, Floor(100, 0.95)
    ),
    "parser-bytes": Oracle(
        _parser_bytes_check, parser_bytes_generate, parser_bytes_seeds, parser_bytes_controls, Floor(100, 1)
    ),
    "encoding-decode": Oracle(
        encoding_decode_check, _generate_encoding, _encoding_seeds, _encoding_decode_controls, Floor(100, 0.95)
    ),
    "css-custom-tokens": Oracle(
        _css_custom_check, css_custom_generate, css_custom_seeds, css_custom_controls, Floor(100, 1)
    ),
    "observer-sequence": Oracle(
        _observer_sequence_check,
        observer_sequence_generate,
        observer_sequence_seeds,
        observer_sequence_controls,
        Floor(100, 1),
    ),
    "dom-program": Oracle(
        _dom_program_check, dom_program_generate, dom_program_seeds, dom_program_controls, Floor(100, 1)
    ),
    "xml-island-grammar": Oracle(_xml_island, xml_island_generate, xml_island_seeds, xml_island_controls, Floor(54, 1)),
    "xml-literal-grammar": Oracle(
        _xml_literal, xml_literal_generate, xml_literal_seeds, xml_literal_controls, Floor(96, 1)
    ),
    "iterator-sequence": Oracle(
        _iterator_sequence_check,
        iterator_sequence_generate,
        iterator_sequence_seeds,
        iterator_sequence_controls,
        Floor(100, 1),
    ),
    "normalize-url-fixpoint": Oracle(
        normalize_url_check, _generate_url, _seeds_url, _normalize_url_controls, Floor(100, 0.5)
    ),
    "url-split-reparse": Oracle(url_reparse_check, _generate_url, _seeds_url, _url_reparse_controls, Floor(100, 0.5)),
    "idna-host": Oracle(idna_host_check, _generate_idna_host, _idna_host_seeds, _idna_host_controls, Floor(100, 0.95)),
    "idna-nfc": Oracle(_idna_nfc_check, idna_nfc_generate, idna_nfc_seeds, idna_nfc_controls, Floor(100, 1)),
    "clean-url-fixpoint": Oracle(clean_url_check, _generate_url, _seeds_url, _clean_url_controls, Floor(100, 0.25)),
    "resolve-links": Oracle(
        resolve_links_check,
        _generate_resolution,
        _generated(_generate_resolution, 500),
        _resolution_controls,
        Floor(100, 1),
        syntax="html",
    ),
    "html-fixpoint": Oracle(html_check, _generate_html, _seeds_html, _html_controls, Floor(500, 0.85), syntax="html"),
    "html-table-grammar": Oracle(
        _html_table_check,
        html_table_generate,
        html_table_seeds,
        html_table_controls,
        Floor(24, 1),
        syntax="html",
    ),
    "html-sibling-grammar": Oracle(
        _html_sibling_check,
        html_sibling_generate,
        html_sibling_seeds,
        html_sibling_controls,
        Floor(100, 1),
        syntax="html",
    ),
    "html-foreign-grammar": Oracle(
        _html_foreign_check,
        html_foreign_generate,
        html_foreign_seeds,
        html_foreign_controls,
        Floor(36, 1),
        syntax="html",
    ),
    "html-list-grammar": Oracle(
        _html_list_check,
        html_list_generate,
        html_list_seeds,
        html_list_controls,
        Floor(24, 1),
        syntax="html",
    ),
    "xml-document-grammar": Oracle(
        xml_document_check, xml_source_generate, xml_source_seeds, xml_source_controls, Floor(60, 1)
    ),
    "xml-fixpoint": Oracle(xml_check, _generate_html, _seeds_html, _xml_controls, Floor(500, 0.95)),
    "css-fixpoint": Oracle(_css_fixpoint, _generate_css, _seeds_css, _css_controls, Floor(500, 0.95), syntax="css"),
    "js-fixpoint": Oracle(_js_fixpoint, _generate_js, _seeds_js, _js_controls, Floor(300, 0.6), syntax="js"),
    "style-fixpoint": Oracle(
        style_check, _generate_style, _seeds_style, _style_controls, Floor(300, 0.95), syntax="css"
    ),
    "markdown-source-grammar": Oracle(
        _markdown_source_check,
        _markdown_source_generate,
        markdown_source_seeds,
        markdown_controls,
        Floor(48, 1),
    ),
    "markdown-html-grammar": Oracle(
        _markdown_html_check,
        _markdown_html_generate,
        partial(markdown_source_seeds, html=True),
        partial(markdown_controls, html=True),
        Floor(29, 1),
        syntax="html",
    ),
    "markdown-fixpoint": Oracle(
        markdown_check,
        _markdown_document,
        _generated(_markdown_document, 500),
        _markdown_controls,
        Floor(500, 0.95),
        syntax="html",
    ),
    "js-names": Oracle(_js_names, _generate_js, _seeds_js, _js_names_controls, Floor(300, 0.6), syntax="js"),
    "css-semantics": Oracle(
        css_semantics_check,
        _generate_semantic,
        _generated(_generate_semantic),
        _css_semantics_controls,
        Floor(100, 0.95),
        syntax="html",
    ),
    "xpath-entry": Oracle(
        xpath_entry_check,
        _generate_xpath_case,
        _generated(_generate_xpath_case),
        _xpath_controls,
        Floor(100, 0.95),
        fields=("html", "xpath"),
    ),
    "selector-entry": Oracle(
        selector_entry_check,
        _generate_selector_case,
        _generated(_generate_selector_case),
        _selector_controls,
        Floor(100, 0.85),
        fields=("html", "css"),
    ),
    "spans": Oracle(span_check, _generate_html, _seeds_html, _span_controls, Floor(500, 0.95), syntax="html"),
}


if __name__ == "__main__":
    raise SystemExit(main())
