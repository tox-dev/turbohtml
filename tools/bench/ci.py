"""
Real, offline benchmark cases for the CodSpeed CI regression gate.

The pyperf suite times every operation in :data:`bench.core.OPERATIONS` over real corpora (see :mod:`bench.corpus`).
:func:`benchmarks` walks that same registry and pairs each turbohtml operation with a lazy loader for one of those real
inputs -- the vendored html5lib-python and War-and-Peace corpora for the markup and text operations, the pinned upstream
stylesheet and JS library for the minifiers, and the bench's own inline cases for the rest. Selected operations add a
second case when one input cannot cover the relevant branch patterns. The loaders are lazy so the corpus is read only
when a benchmark runs (under ``--codspeed``); a case whose corpus is missing raises, and the test skips it.

Operations whose inline case is too small to clear Valgrind's heap-jitter floor are re-homed on a large real corpus and
surfaced under a corpus-qualified benchmark id (see :data:`_RESIZED`), so the noise-prone identity retires and a stable
one takes its place without CodSpeed reading the input change as a regression.
"""

from __future__ import annotations

from functools import cache, partial
from typing import TYPE_CHECKING, Final

from bench import corpus
from bench.core import OPERATIONS
from bench.operations import INPUTS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_TEXT_BYTES = 1 << 16  # a 64 kB slice of the book: representative work without a multi-megabyte parse under Valgrind


@cache
def _spec() -> str:
    """Return the vendored whatwg HTML spec (235 kB): the read-path operations' shared real document."""
    _name, relative, encoding = corpus.CORPUS_FILES[5]
    return corpus.corpus_text(relative, encoding)


def _xpath_case() -> tuple[str, str]:
    """Return the ``(kind, document)`` pair the xpath operation expects, over the spec."""
    return "//a[@href]", _spec()


# read-path, parse and tokenize all run over one shared real document (bench.core caches the parse), so they measure the
# query, not a re-parse; xpath, the text corpora and the minifier corpora load their own real inputs.
_DOCUMENT_OPS = (
    "parse",
    "tokenize",
    "find",
    "select",
    "select-has",
    "match",
    "find-text",
    "text-content",
    "serialize",
    "serialize-inner",
    "parse-inner",
    "parse-inner-encode",
    "serialize-inner-indent",
    "serialize-inner-minify",
    "encode-inner",
    "encode-inner-indent",
    "encode-inner-minify",
    "iterate-inner",
    "iterate-inner-indent",
    "collapse-whitespace",
    "strip-comments",
    "transform-tree",
    "whitespace-roundtrip",
    "conformance",
    "serialize-xml",
    "canonicalize",
    "lossless-serialize",
    "minify",
    "edit",
    "class-edit",
    "strip-remove",
    "strip-tags",
    "set-html",
    "set-text",
    "observe",
    "navigate",
    "treewalk",
    "chain",
    "range-clone",
    "links-extract",
    "links-absolutize",
    "links-rewrite",
    "extract-attr",
    "extract-text",
    "htmlparser",
    "sax",
    "treebuild",
    "rewrite",
    "stream",
    "path",
    "path-xpath",
    "links-filter",
)

_LOADERS: dict[str, Callable[[], object]] = dict.fromkeys(_DOCUMENT_OPS, _spec)
_LOADERS["xpath"] = _xpath_case
_LOADERS["escape"] = partial(corpus.corpus, "war-and-peace/2600.txt", _TEXT_BYTES)
_LOADERS["unescape"] = partial(corpus.corpus, "war-and-peace/2600-h/2600-h.htm", _TEXT_BYTES)
_LOADERS["encoding"] = lambda: corpus.corpus("war-and-peace/2600.txt", _TEXT_BYTES).encode("cp1252")
_LOADERS["normalize"] = partial(corpus.corpus, "war-and-peace/2600.txt", _TEXT_BYTES)
_LOADERS["minify-css"] = partial(corpus.large_text, *corpus.STYLESHEETS[1][1:])  # pico.css (90 kB)
_LOADERS["minify-js"] = partial(corpus.large_text, *corpus.JS_FILES[0][1:])  # underscore (67 kB)
_LOADERS["parse-scripting"] = partial(
    corpus.large_text, *corpus.REAL_PAGES[2][1:]
)  # mozilla blog (95 kB), carries <noscript>
_LOADERS["parse-locations"] = _spec  # the whatwg spec: many tags and attributes to stamp spans for
_LOADERS["computed-style"] = lambda: INPUTS["computed-style"]()[0][1]


def _spec_case(kind: str) -> tuple[str, str]:
    """Wrap the spec in the ``(kind, document)`` pair a tuple-shaped operation expects."""
    return kind, _spec()


def _book_text() -> str:
    """Return a War-and-Peace plain-text slice: the string operations' large real input."""
    return corpus.corpus("war-and-peace/2600.txt", _TEXT_BYTES)


def _book_html() -> str:
    """Return a War-and-Peace HTML slice: the markup operations' large real input."""
    return corpus.corpus("war-and-peace/2600-h/2600-h.htm", _TEXT_BYTES)


# An inline case is too few instructions under Valgrind to clear the heap-layout jitter floor, so it swings >5%
# run-to-run (text-main and boilerplate tripped the required CodSpeed check on unrelated PRs). Re-home each on a large
# real corpus under a new corpus-qualified id: the flaky small id retires with no base and the large one is gated
# fresh, so the input change never reads as a regression. The synthetic tree builders, the fragment parser, the
# single-selector translate, and the fixed URL batch have no document corpus to grow into and stay inline.
# The eight document converters carry a ``-parse-`` id because their measurement changed: each used to query a
# document parsed once and cached, which credited it with a parse no caller gets, and now parses the string it is
# handed. Reusing the old id would have CodSpeed read the added parse as a regression against a figure that measured
# less work, so the old identity retires and the new one gates from its own baseline.
# ``structured_data()`` likewise has a new identity now that each call copies one immutable tree snapshot before
# extraction; its old baseline measured six independent reads without the snapshot guarantee.
_RESIZED: dict[str, tuple[str, Callable[[], object]]] = {
    "socialcard": ("socialcard-spec", _spec),
    "structured": ("structured-snapshot-spec", _spec),
    "sanitize": ("sanitize-spec", _spec),
    "sanitize-templates": ("sanitize-templates-spec", _spec),
    "sanitize-named-props": ("sanitize-named-props-spec", _spec),
    "sanitize-report": ("sanitize-report-spec", _spec),
    "sanitize-styles": ("sanitize-styles-spec", _spec),
    "sanitize-transform": ("sanitize-transform-spec", _spec),
    "sanitize-custom-elements": ("sanitize-custom-elements-spec", _spec),
    "sanitize-xml": ("sanitize-xml-spec", _spec),
    "linkify": ("linkify-spec", _spec),
    "sanitize-node": ("sanitize-node-fresh-spec", _spec),
    "linkify-node": ("linkify-node-fresh-spec", _spec),
    "markdown-google": ("markdown-google-parse-spec", _spec),
    "article": ("article-parse-spec", _spec),
    "boilerplate": ("boilerplate-spec", _spec),
    "date": ("date-spec", _spec),
    "text-render": ("text-render-parse-spec", _spec),
    "text-collapsed": ("text-collapsed-parse-spec", _spec),
    "text-main": ("text-main-parse-spec", _spec),
    "text-annotated": ("text-annotated-parse-spec", _spec),
    "markdown": ("markdown-parse-spec", partial(_spec_case, "default")),
    "tables": ("tables-parse-spec", partial(_spec_case, "rows")),
    "tables-wide": ("tables-wide-parse", lambda: INPUTS["tables-wide"]()[0][1]),
    "extract-url": ("extract-url-spec", partial(_spec_case, "base")),
    "markup": ("markup-book", _book_text),
    "markup-op": ("markup-op-book", lambda: ("striptags", _book_html())),
    "detect": ("detect-book", lambda: ("find", _book_text())),
    "normalize": ("normalize-decomposed", lambda: INPUTS["normalize"]()[2][1]),
    "decode": ("decode-gb18030-ranges", lambda: INPUTS["decode"]()[1][1]),
}
_ADDITIONAL_CASES: Final[dict[str, tuple[str, int]]] = {
    "encoding-result-ascii": ("encoding-result", 1),
    "encoding-result-bom": ("encoding-result", 2),
    "encoding-result-stream-ascii": ("encoding-result-stream", 1),
    "encoding-result-stream-bom": ("encoding-result-stream", 2),
    "minify-css-conflicts-short": ("minify-css-conflicts", 1),
    "minify-css-conflicts-small": ("minify-css-conflicts", 2),
    "minify-css-merges-media-large": ("minify-css-merges", 2),
    "minify-css-merges-selectors-small": ("minify-css-merges", 3),
    "minify-css-merges-selectors-large": ("minify-css-merges", 5),
    "minify-css-merges-alternating": ("minify-css-merges", 6),
    "minify-css-merges-comments": ("minify-css-merges", 7),
    "computed-style-selectors-matching": ("computed-style-selectors", 1),
    "computed-style-selectors-shallow": ("computed-style-selectors", 3),
    "query-root-groups-connected": ("query-root-groups", 1),
    "query-root-groups-small": ("query-root-groups", 2),
    "query-root-groups-documents": ("query-root-groups", 3),
    "radio-group-small": ("radio-group", 1),
    "radio-group-many-forms": ("radio-group", 2),
    "radio-group-invalidation": ("radio-group", 3),
    "form-data-fieldsets-legend": ("form-data-fieldsets", 1),
    "form-data-fieldsets-enabled": ("form-data-fieldsets", 2),
    "form-data-fieldsets-plain": ("form-data-fieldsets", 3),
    "query-roots-shuffled": ("query-roots", 1),
    "query-roots-sorted": ("query-roots", 2),
    "query-roots-small": ("query-roots", 3),
    "sax-records-tiny": ("sax-records", 1),
    "tables-spans-ordinary": ("tables-spans", 1),
    "tables-spans-records": ("tables-spans", 2),
    "linkify-node-wide": ("linkify-node", 1),
    "linkify-node-ascii": ("linkify-node", 2),
    "linkify-node-wide-clean": ("linkify-node", 3),
    "linkify-node-wide-links": ("linkify-node", 4),
    "linkify-traversal-wide-callbacks": ("linkify-traversal", 5),
    "syndication-extensions": ("syndication", 1),
    "syndication-atom": ("syndication", 2),
    "sanitize-attributes-allowed": ("sanitize-attributes", 1),
    "sanitize-attributes-tiny": ("sanitize-attributes", 2),
    "html-options-none": ("html-options", 1),
    "text-options-none": ("text-options", 1),
    "canonical-options-none": ("canonical-options", 1),
    "links-external-deep": ("links-external", 1),
    "minify-js-jquery": ("minify-js", 2),
    "minify-js-integers-trailing-zeros": ("minify-js-integers", 1),
    "minify-js-integers-single": ("minify-js-integers", 2),
    "token-attributes-empty": ("token-attributes", 1),
    "token-attributes-one": ("token-attributes", 2),
    "token-attributes-ten": ("token-attributes", 3),
    "computed-style-filter-matching": ("computed-style-filter", 1),
    "computed-style-filter-class-first": ("computed-style-filter", 2),
    "minify-js-var-initialization-single": ("minify-js-var-initialization", 1),
    "minify-js-var-initialization-early": ("minify-js-var-initialization", 2),
    "minify-js-unlink-separated": ("minify-js-unlink", 1),
    "minify-js-unlink-single": ("minify-js-unlink", 2),
    "minify-js-unused-declarations-single": ("minify-js-unused-declarations", 1),
    "minify-js-single-use-separated": ("minify-js-single-use", 1),
    "minify-js-single-use-single": ("minify-js-single-use", 2),
    "minify-js-propagation-single": ("minify-js-propagation", 1),
    "minify-js-propagation-declaration": ("minify-js-propagation", 2),
    "canonicalize-deep-xlink": ("canonicalize-deep", 1),
    "canonicalize-deep-shallow": ("canonicalize-deep", 2),
    "computed-style-specificity-ordinary": ("computed-style-specificity", 1),
    "computed-style-specificity-cold-ordinary": ("computed-style-specificity-cold", 1),
    "rewrite-attributes-single": ("rewrite-attributes", 1),
    "parse-xml-values-references": ("parse-xml-values", 1),
    "parse-xml-values-tiny": ("parse-xml-values", 2),
    "parse-xml-text-references": ("parse-xml-text", 1),
    "parse-xml-text-tiny": ("parse-xml-text", 2),
    "parse-xml-prefixes-single": ("parse-xml-prefixes", 1),
    "query-closest-distinct": ("query-closest", 1),
    "query-parents-distinct": ("query-parents", 1),
    "query-siblings-single": ("query-siblings", 1),
    "prune-shared-single": ("prune-shared", 1),
    "minify-js-sequences-small": ("minify-js-sequences", 1),
    "minify-js-guards-single": ("minify-js-guards", 1),
    "markdown-wrap-ordinary": ("markdown-wrap", 1),
    "markdown-asterisks": ("markdown", 4),
    "markdown-letters": ("markdown", 5),
    "shadow-assignment-single": ("shadow-assignment", 1),
    "shadow-fallback-single": ("shadow-fallback", 1),
    "observe-registrations-unrelated": ("observe-registrations", 1),
    "parse-foster-single": ("parse-foster", 1),
    "parse-crlf-lf": ("parse-crlf", 1),
    "parse-xml-append-single": ("parse-xml-append", 1),
    "parse-nul-clean": ("parse-nul", 1),
    "parse-afe-identical": ("parse-afe", 1),
    "parse-formatting-shallow": ("parse-formatting", 1),
    "range-boundary-end": ("range-boundary", 1),
    "range-contained-single": ("range-contained", 1),
    "range-partial-single": ("range-partial", 1),
    "transform-names-small": ("transform-names", 1),
    "transform-rules-single": ("transform-rules", 1),
    "transform-number-single": ("transform-number", 2),
    "transform-number-any": ("transform-number", 8),
    "transform-number-any-reversed": ("transform-number", 9),
    "transform-number-count": ("transform-number", 14),
    "transform-number-predicate": ("transform-number", 20),
    "transform-number-count-from": ("transform-number", 22),
    "transform-number-count-last": ("transform-number", 27),
    "transform-number-from-alternating": ("transform-number", 29),
    "node-equals-reversed": ("node-equals", 7),
    "node-equals-early-mismatch": ("node-equals", 8),
    "node-equals-duplicates": ("node-equals", 12),
    "attribute-replace": ("attribute-grow", 7),
    "normalize-dom-long-text": ("normalize-dom", 5),
    "normalize-dom-empty-tail": ("normalize-dom", 6),
    "xpath-compare-unequal": ("xpath-compare", 6),
    "xpath-compare-scalar": ("xpath-compare", 9),
    "xpath-order-le": ("xpath-order", 5),
    "xpath-order-gt": ("xpath-order", 8),
    "xpath-order-ge": ("xpath-order", 11),
    "xpath-id-nodes-long": ("xpath-id-nodes", 1),
    "xpath-replace-short": ("xpath-replace", 1),
    "date-tally-repeated": ("date-tally", 1),
    "validate-facets-plain": ("validate-facets", 1),
    "validate-rng-reuse-interleave": ("validate-rng-reuse", 1),
    "validate-rng-reuse-small": ("validate-rng-reuse", 2),
    "validate-rng-reuse-recursive": ("validate-rng-reuse", 3),
    "compile-rng-reuse-small": ("compile-rng-reuse", 1),
    "validate-attributes-small": ("validate-attributes", 1),
    "validate-attributes-sparse": ("validate-attributes", 2),
    "validate-attributes-few-declarations": ("validate-attributes", 3),
    "validate-facets-attributes": ("validate-facets", 2),
    "validate-facets-small": ("validate-facets", 3),
    "validate-numeric-facets-bounded": ("validate-numeric-facets", 1),
    "validate-numeric-facets-small": ("validate-numeric-facets", 2),
    "validate-numeric-facets-string": ("validate-numeric-facets", 3),
    "validate-pattern-plain": ("validate-pattern-reuse", 1),
    "xpath-concat-long": ("xpath-concat", 1),
    "xpath-translate-short": ("xpath-translate", 3),
    "xpath-translate-repeated": ("xpath-translate", 7),
    "xpath-translate-long-map": ("xpath-translate", 6),
    "xpath-translate-varied": ("xpath-translate", 9),
    "xpath-set-difference": ("xpath-set", 6),
    "xpath-set-disjoint": ("xpath-set", 10),
    "xpath-set-overlap": ("xpath-set", 14),
    "xpath-distinct-unique": ("xpath-distinct", 5),
    "xpath-distinct-empty": ("xpath-distinct", 9),
    "xpath-distinct-singleton": ("xpath-distinct", 10),
    "shadow-slot-empty": ("shadow-slot", 4),
    "shadow-slot-comments": ("shadow-slot", 7),
    "shadow-slot-nonmatching": ("shadow-slot", 10),
    "select-nth-filtered": ("select-nth", 4),
    "xpath-wide-union": ("xpath-wide", 4),
    "microdata-itemref-interleaved": ("microdata-itemref", 2),
    "microdata-itemref-reversed": ("microdata-itemref", 1),
    "microdata-itemref-small": ("microdata-itemref", 3),
    "transform-dispatch-1": ("transform-dispatch", 1),
    "transform-dispatch-4": ("transform-dispatch", 2),
    "transform-dispatch-16": ("transform-dispatch", 3),
    "collapse-whitespace-unchanged": ("collapse-whitespace", 4),
    "collapse-whitespace-dense": ("collapse-whitespace", 5),
    "idna-varied": ("idna", 1),
    "linkify-traversal-small-nodes": ("linkify-traversal", 1),
    "linkify-traversal-skipped": ("linkify-traversal", 2),
    "linkify-traversal-callbacks": ("linkify-traversal", 3),
    "linkify-traversal-empty-nodes": ("linkify-traversal", 4),
    "phone-possible": ("phone", 1),
    "phone-regions-8": ("phone", 2),
    "phone-prose": ("phone", 4),
    "phone-noise": ("phone", 5),
    "phone-adversarial": ("phone", 6),
    "phone-adversarial-8": ("phone", 7),
    "phone-fullwidth": ("phone", 8),
    "phone-ucs2-prose": ("phone", 9),
    "phone-ucs4-prose": ("phone", 10),
    "phone-short": ("phone", 11),
    "phone-short-8": ("phone", 12),
    "detect-numeric-prose": ("detect", 5),
    "detect-ucs2-prose": ("detect", 6),
    "detect-ucs4-prose": ("detect", 7),
    "select-relative": ("select-relative", 0),
    "find-text-exact": ("find-text-exact", 0),
    "find-attr-presence": ("find-attr-presence", 0),
    "urls-clean-plain-query": ("urls-clean", 2),
    "urls-clean-escaped-query": ("urls-clean", 3),
    "select-relative-child": ("select-relative", 4),
    "select-relative-sibling": ("select-relative", 11),
    "find-text-exact-nested": ("find-text-exact", 2),
    "find-text-exact-wide": ("find-text-exact", 4),
    "find-attr-presence-values": ("find-attr-presence", 4),
    "find-attr-absence-values": ("find-attr-presence", 5),
    "sanitize-templates-plain": ("sanitize-templates", 1),
    "sanitize-templates-attribute": ("sanitize-templates", 2),
    "sanitize-templates-late": ("sanitize-templates", 3),
    "minify-js-names": ("minify-js-names", 0),
    "minify-js-names-single-character": ("minify-js-names", 2),
    "minify-js-names-multiple-characters": ("minify-js-names", 3),
    "serialize-attributes": ("serialize-attributes", 0),
    "serialize-attributes-sorted": ("serialize-attributes", 1),
    "serialize-attributes-small": ("serialize-attributes", 2),
    "serialize-attributes-unsorted": ("serialize-attributes", 3),
    "translate-id-literals": ("translate", 19),
    "translate-long-literals": ("translate", 21),
    "translate-class-literals": ("translate", 24),
    "minify-css-unicode-ranges-small": ("minify-css", len(corpus.STYLESHEETS)),
    "minify-css-unicode-ranges-sorted": ("minify-css", len(corpus.STYLESHEETS) + 1),
    "minify-css-unicode-ranges-reversed": ("minify-css", len(corpus.STYLESHEETS) + 2),
    "urls-clean-undotted-path": ("urls-clean", 4),
    "urls-clean-short-path": ("urls-clean", 5),
    "urls-clean-parent-path": ("urls-clean", 6),
    "urls-clean-encoded-parent-path": ("urls-clean", 7),
    "urls-clean-extension-path": ("urls-clean", 8),
    "sanitize-attributes-repeated": ("sanitize-attributes", 3),
    "is-valid": ("is-valid", 0),
    "is-valid-valid": ("is-valid", 1),
    "is-valid-one-error": ("is-valid", 2),
    "is-valid-rng": ("is-valid-rng", 0),
    "is-valid-rng-valid": ("is-valid-rng", 1),
    "is-valid-rng-one-error": ("is-valid-rng", 2),
    "conformance-sections-small": ("conformance", len(corpus.REAL_PAGES) + 1),
    "conformance-sections-medium": ("conformance", len(corpus.REAL_PAGES) + 2),
    "conformance-sections-large": ("conformance", len(corpus.REAL_PAGES) + 3),
    "conformance-sections-shallow": ("conformance", len(corpus.REAL_PAGES) + 4),
    "conformance-sections-heading": ("conformance", len(corpus.REAL_PAGES) + 5),
    "serialize-named": ("serialize-named", 0),
    "serialize-named-ascii": ("serialize-named", 1),
    "serialize-named-unnamed": ("serialize-named", 2),
    "serialize-named-small": ("serialize-named", 3),
    "sanitize-disallowed": ("sanitize-disallowed", 0),
    "sanitize-escape-attributes-medium": ("sanitize-disallowed", 1),
    "sanitize-escape-attributes-wide": ("sanitize-disallowed", 2),
    "sanitize-strip-attributes-small": ("sanitize-disallowed", 3),
    "sanitize-strip-attributes-medium": ("sanitize-disallowed", 4),
    "sanitize-strip-attributes-wide": ("sanitize-disallowed", 5),
    "transform-text": ("transform-text", 0),
    "transform-text-builtin-small": ("transform-text", 1),
    "transform-text-literal": ("transform-text", 2),
    "transform-text-literal-small": ("transform-text", 3),
    "transform-text-explicit": ("transform-text", 4),
    "transform-text-explicit-small": ("transform-text", 5),
    "minify-css-zero-exponent-single": ("minify-css", len(corpus.STYLESHEETS) + 3),
    "minify-css-zero-exponent-many": ("minify-css", len(corpus.STYLESHEETS) + 4),
    "minify-css-zero-exponent-small": ("minify-css", len(corpus.STYLESHEETS) + 5),
    "minify-css-number-arithmetic": ("minify-css", len(corpus.STYLESHEETS) + 6),
    "minify-css-negative-exponent": ("minify-css", len(corpus.STYLESHEETS) + 7),
    "minify-css-dimension-exponent": ("minify-css", len(corpus.STYLESHEETS) + 8),
    "minify-css-unitless-exponent": ("minify-css", len(corpus.STYLESHEETS) + 9),
    "transform-sort-integer-small": ("transform-sort", 2),
    "transform-sort-integer-large": ("transform-sort", 3),
    "transform-sort-string-small": ("transform-sort", 4),
    "transform-sort-string-large": ("transform-sort", 5),
    "transform-sort-text-small": ("transform-sort", 6),
    "transform-sort-text-large": ("transform-sort", 7),
    "transform-sort-fraction-small": ("transform-sort", 8),
    "transform-sort-fraction-large": ("transform-sort", 9),
    "normalize-ascii-prefix": ("normalize", 4),
    "normalize-ascii-prefix-small": ("normalize", 5),
    "normalize-composable-pairs": ("normalize", 6),
    "normalize-hangul": ("normalize", 7),
    "select-default": ("select-default", 0),
    "select-default-medium": ("select-default", 1),
    "select-default-small": ("select-default", 2),
    "select-default-many-forms": ("select-default", 3),
    "select-default-tag-control": ("select-default", 4),
    "select-default-explicit-submit": ("select-default", 5),
    "normalize-marks-large": ("normalize-marks", 2),
    "normalize-marks-small": ("normalize-marks", 0),
    "normalize-hebrew-marks": ("normalize", 8),
    "transform-key": ("transform-key", 0),
    "transform-key-attribute": ("transform-key", 1),
    "transform-key-repeated": ("transform-key", 2),
    "transform-key-unique": ("transform-key", 3),
    "transform-key-small": ("transform-key", 4),
    "minify-css-nested-functions": ("minify-css", 16),
    "minify-css-nested-functions-small": ("minify-css", 17),
    "minify-css-shallow-functions": ("minify-css", 18),
    "transform-scope": ("transform-scope", 0),
    "transform-scope-local-small": ("transform-scope", 1),
    "transform-scope-global": ("transform-scope", 2),
    "transform-scope-global-small": ("transform-scope", 3),
    "normalize-supplementary-prefix": ("normalize", 9),
    "transform-namespaces": ("transform-namespaces", 0),
    "transform-namespaces-alternating": ("transform-namespaces", 1),
    "transform-namespaces-shallow": ("transform-namespaces", 2),
    "transform-namespaces-small": ("transform-namespaces", 3),
    "transform-namespaces-empty": ("transform-namespaces", 4),
    "transform-namespaces-once": ("transform-namespaces-once", 0),
    "stream-nul-bmp": ("stream", 4),
    "stream-nul-bmp-small": ("stream", 5),
    "stream-nul-late": ("stream", 6),
    "stream-nul-free": ("stream", 7),
    "stream-nul-ascii": ("stream", 8),
    "stream-nul-supplementary": ("stream", 9),
    "encoding-chunks": ("encoding-chunks", 0),
    "encoding-chunks-small": ("encoding-chunks", 1),
    "encoding-chunks-single": ("encoding-chunks", 2),
    "encoding-chunks-ascii": ("encoding-chunks", 3),
    "urls-ascii-host-long": ("urls-clean", 9),
    "urls-ascii-host-short": ("urls-clean", 10),
    "urls-unicode-host": ("urls-clean", 11),
    "urls-unicode-ascii-host": ("urls-clean", 12),
    "phone-format-late-code": ("phone-format", 4),
    "phone-format-early-code": ("phone-format", 5),
    "phone-format-late-e164": ("phone-format", 6),
    "phone-construct": ("phone-construct", 0),
}


def _inline(operation: str, case_index: int = 0) -> object:
    """Return one case the bench already defines inline for ``operation`` (no corpus needed)."""
    return INPUTS[operation]()[case_index][1]


_LOADERS["computed-style-selectors-reverse"] = partial(_inline, "computed-style-selectors-reverse", 2)
_LOADERS["xpath-id-nodes"] = partial(_inline, "xpath-id-nodes")
_LOADERS["xpath-replace"] = partial(_inline, "xpath-replace")
_LOADERS["date-tally"] = partial(_inline, "date-tally")
_LOADERS["validate-pattern-reuse"] = partial(_inline, "validate-pattern-reuse")
_LOADERS["compile-pattern"] = partial(_inline, "compile-pattern")
_LOADERS["validate-facets"] = partial(_inline, "validate-facets")
_LOADERS["validate-rng-reuse"] = partial(_inline, "validate-rng-reuse")
_LOADERS["compile-rng-reuse"] = partial(_inline, "compile-rng-reuse")
_LOADERS["validate-attributes"] = partial(_inline, "validate-attributes")
_LOADERS["validate-numeric-facets"] = partial(_inline, "validate-numeric-facets")
_LOADERS["compile-facets"] = partial(_inline, "compile-facets")
_LOADERS["xpath-concat"] = partial(_inline, "xpath-concat")
_LOADERS["node-equals"] = partial(_inline, "node-equals", 5)
_LOADERS["transform-names-compile"] = partial(_inline, "transform-names-compile")
_LOADERS["transform-names"] = partial(_inline, "transform-names")
_LOADERS["transform-rules"] = partial(_inline, "transform-rules")
_LOADERS["transform-number"] = partial(_inline, "transform-number", 3)
_LOADERS["normalize-dom"] = partial(_inline, "normalize-dom", 4)
_LOADERS.update({name: partial(_inline, name, 3) for name in ("attribute-grow", "parse-xml-attrs")})


_LOADERS.update({
    name: partial(_inline, name, 1)
    for name in (
        "select-nth",
        "xpath-wide",
        "computed-style-deep",
        "microdata-wide",
        "microdata-empty-scope",
        "structured-empty",
        "article-wide",
        "article-deep",
        "xpath-set",
        "xpath-compare",
        "xpath-order",
        "xpath-translate",
        "normalize-marks",
        "canonicalize-attrs",
        "path-wide",
        "path-xpath-wide",
        "detect-language-long",
        "path-cold",
        "path-xpath-cold",
        "path-one-cold",
        "path-xpath-one-cold",
        "path-class-edit",
        "validate-pattern",
        "shadow-slot",
    )
})


def loader_for(operation: str) -> Callable[[], object]:
    """Return the lazy loader for ``operation``: a resized real corpus, the spec, or the bench's own inline case."""
    if operation in _RESIZED:
        return _RESIZED[operation][1]
    return _LOADERS.get(operation, partial(_inline, operation))


def benchmarks() -> Iterator[tuple[str, object, Callable[[], object]]]:
    """
    Yield ``(name, callable, load)`` for every turbohtml operation, in registry order.

    A noise-prone inline operation is surfaced under its corpus-qualified id (see :data:`_RESIZED`) so CodSpeed
    retires the flaky small-input benchmark and gates the large-input one as a fresh identity.
    """
    for name, (run, _owner) in OPERATIONS.items():
        if name == "startup" or name in _ADDITIONAL_CASES:
            continue  # Explicit cases retain their configured position; startup needs a fresh interpreter.
        identity = _RESIZED[name][0] if name in _RESIZED else name
        yield identity, run, loader_for(name)
    for identity, (name, case_index) in _ADDITIONAL_CASES.items():
        yield identity, OPERATIONS[name][0], partial(_inline, name, case_index)


__all__ = [
    "benchmarks",
    "loader_for",
]
