"""
Why a published column does not compare like for like.

A ratio invites the reader to conclude one library is faster at the same job. For most columns that holds. For the
ones named here it does not: the party answers a different question, produces different output, or does not answer at
all, and its timing is the cost of that lesser work rather than a faster implementation. Each note says what differs
and, where it was measured, by how much, so a reader can judge the column instead of trusting or dismissing it.

Keyed by operation, then by the party label the competitor module publishes. :mod:`bench.report` writes the notes for
an operation into its feed, and the ``bench-table`` directive renders them under the table.
"""

from __future__ import annotations

from typing import Final

_STRIPPER_JS: Final = (
    "strips whitespace and comments without parsing the source, so it performs none of the renaming or structural "
    "compression the parsed minifiers do: on jQuery it emits 141.1 kB where turbohtml emits 87.8 kB"
)
_STRIPPER_CSS: Final = (
    "strips whitespace and comments without parsing, so it applies none of the color, number, and shorthand rewrites "
    "turbohtml does; on these stylesheets that leaves its output within 1.01-1.03x of turbohtml's, and 0.998x on "
    "bulma, so the structural shortenings change little on already-tight framework CSS"
)
_BUILDER: Final = (
    "concatenates a string rather than constructing a navigable tree, so nothing it builds can afterwards be queried "
    "or mutated"
)

NOTES: Final[dict[str, dict[str, str]]] = {
    "stream": {
        "lxml": "libxml2 produces different trees for the four real-page inputs; only the six generated inputs match",
    },
    "canonicalize-deep": {
        "lxml method=c14n": "HTML parsing omits the empty head element and SVG/xlink namespace declarations",
    },
    "transform-sort": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "transform-text": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "transform-key": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "transform-scope": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "transform-namespaces": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "transform-namespaces-once": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "transform-number": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "transform-dense": {
        "lxml.etree": "returns an XSLT result tree; conversion to a Python string is outside timing",
    },
    "sanitize-node": {
        "lxml-html-clean": "blocklist policy rather than turbohtml's allowlist; not security equivalence"
    },
    "sanitize-attributes": {
        "DOMPurify": "includes Node startup and pipe I/O; not in-process JavaScript engine timing",
    },
    "linkify-node": {"lxml-html-clean": "host exclusions disabled; URL/mailto linking but no bare email addresses"},
    "collapse-whitespace": {
        "lxml": "Python text/regex walk; parser recovery and text storage differ",
        "BeautifulSoup (html.parser)": "Python text/regex walk; parser recovery and text storage differ",
        "BeautifulSoup (lxml)": "Python text/regex walk; parser recovery and text storage differ",
        "selectolax": "Python text/regex walk; rejects template input; parser recovery differs",
    },
    "strip-comments": {
        "selectolax": "template-containing inputs are rejected because template contents are not exposed",
    },
    "transform-tree": {
        "lxml": "Python text/regex walk; parser recovery and text storage differ",
        "BeautifulSoup (html.parser)": "Python text/regex walk; parser recovery and text storage differ",
        "BeautifulSoup (lxml)": "Python text/regex walk; parser recovery and text storage differ",
        "selectolax": "Python text/regex walk; rejects template input; parser recovery differs",
    },
    "serialize-inner-indent": {
        "lxml": "own pretty-print whitespace policy; output bytes differ from turbohtml",
        "BeautifulSoup (html.parser)": "own pretty-print whitespace policy; output bytes differ from turbohtml",
        "BeautifulSoup (lxml)": "own pretty-print whitespace policy; output bytes differ from turbohtml",
    },
    "encode-inner-indent": {
        "lxml": "own pretty-print whitespace policy; output bytes differ from turbohtml",
        "BeautifulSoup (html.parser)": "own pretty-print whitespace policy; output bytes differ from turbohtml",
        "BeautifulSoup (lxml)": "own pretty-print whitespace policy; output bytes differ from turbohtml",
    },
    "serialize-inner": {
        "lxml": "joins child serializations and escaped leading text",
        "parsel": "selects children, escapes direct text, and joins results",
    },
    "encode-inner": {
        "lxml": "joins Unicode child output then encodes UTF-8",
        "parsel": "joins escaped child output then encodes UTF-8",
        "selectolax": "serializes to a Unicode string before UTF-8 encoding",
    },
    "iterate-inner": {
        "html5lib": "consumes serializer tokens rather than bounded-size chunks",
    },
    "transform-dispatch": {
        "stdlib": "plain Python identity loop; omits Node validation, None results, and replacement ownership",
        "Python (validated)": "Python loop with native Nodes, root/result checks, None handling, and replacement",
    },
    "whitespace-roundtrip": {
        "html5lib": "filter/reparse/serialize; different listing, title, and foreign-text policy",
    },
    "serialize-inner-minify": {
        "html5lib": "different preservation policy; removing comments can leave two spaces across token boundaries",
    },
    "encode-inner-minify": {
        "html5lib": "different preservation policy; removing comments can leave two spaces across token boundaries",
    },
    "parse-inner": {
        "parse5": "includes Node startup and pipe I/O; not in-process JavaScript engine timing",
        "jsdom": "includes Node startup and pipe I/O; not in-process JavaScript engine timing",
    },
    "parse-inner-encode": {
        "parse5": "includes Node startup and pipe I/O; not in-process JavaScript engine timing",
        "jsdom": "includes Node startup and pipe I/O; not in-process JavaScript engine timing",
    },
    "minify-js": dict.fromkeys(("rjsmin", "jsmin", "css-html-js-minify"), _STRIPPER_JS),
    "minify-css": dict.fromkeys(("rcssmin", "cssmin", "css-html-js-minify"), _STRIPPER_CSS),
    "strip-remove": {
        "w3lib": (
            "removes the tags with a regular expression over the raw string and never parses, so it cannot honor "
            "nesting or the tokenizer rules that decide where an element really ends"
        ),
    },
    "build-e": dict.fromkeys(("simple-html", "markyp", "yattag", "htbuilder", "htpy", "fast-html"), _BUILDER),
    "construct": dict.fromkeys(("simple-html", "markyp", "htbuilder", "htpy"), _BUILDER),
    "decode": {
        "stdlib": (
            "decodes with the nearest CPython codec under errors=replace, which is not the WHATWG decoder of that "
            "label: the two disagree on both the mapping tables and where decoding resumes after an error"
        ),
    },
    "linkify": {
        "lxml-html-clean": (
            "links URLs inside existing markup and never linkifies email addresses; on the prose case it produces no "
            "links at all, so that timing is the cost of finding nothing"
        ),
    },
    "date": dict.fromkeys(
        ("htmldate", "trafilatura"),
        "returns no date for the 100-candidate case, so its timing there is the cost of giving up rather than of "
        "finding the date turbohtml reports",
    ),
    "text-content": {
        "resiliparse": (
            "reports about 11% fewer elements than every other parser here (876 against 989 on the mozilla page), so "
            "it collects text from a smaller tree"
        ),
    },
}
NOTES["navigate"] = {"resiliparse": NOTES["text-content"]["resiliparse"]}
NOTES.update({
    operation: {
        **dict.fromkeys(
            ("esbuild", "terser", "tdewolff"), "runs a fresh command per call, including process startup and pipe I/O"
        ),
        **dict.fromkeys(
            ("rjsmin", "jsmin", "css-html-js-minify"),
            "removes whitespace and comments; does not perform declaration or expression compression",
        ),
    }
    for operation in ("minify-js-unlink", "minify-js-unused-declarations", "minify-js-var-initialization")
})
NOTES.update({
    operation: {
        **dict.fromkeys(
            ("esbuild", "tdewolff"), "runs a fresh command per call, including process startup and pipe I/O"
        ),
        **dict.fromkeys(
            ("rcssmin", "cssmin", "css-html-js-minify"),
            "removes whitespace and comments; does not merge rules",
        ),
    }
    for operation in ("minify-css-merges", "minify-css-conflicts")
})

__all__ = ["NOTES"]
