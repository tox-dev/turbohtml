"""Fixed envelopes give content consumers independent expected results per input."""

from __future__ import annotations

import html
import json
import unicodedata
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import turbohtml
from turbohtml import clean, detect, extract, parse_fragment
from turbohtml.migration import bleach, markupsafe, stdlib

from .atheris_header import BYTEWISE, Header, feed_chunks
from .atheris_registry import Target
from .idna_nfc_oracles import idna_nfc_check
from .round_trip_oracles import (
    clean_url_check,
    css_semantics_check,
    encoding_decode_check,
    encoding_stream_check,
    fixpoint_check,
    idna_host_check,
    normalize_url_check,
    url_reparse_check,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "content_targets",
    "detector_check",
    "minifier_check",
    "minifier_observation",
    "sanitizer_check",
    "sanitizer_observation",
    "stdlib_check",
    "stdlib_observation",
    "url_check",
]


def content_targets() -> tuple[Target, ...]:
    """Keep each qualified content export attached to an executable contract."""
    return (
        Target(
            "content-sanitize",
            _sanitize,
            tuple(
                f"turbohtml.clean.{name}"
                for name in (
                    "DEFAULT_ATTRIBUTES",
                    "DEFAULT_CSS_PROPERTIES",
                    "DEFAULT_SCHEMES",
                    "DEFAULT_TAGS",
                    "OnDisallowed",
                    "Policy",
                    "Removed",
                    "Sanitizer",
                    "Transform",
                    "sanitize",
                    "sanitize_node",
                    "sanitize_report",
                    "sanitize_report_node",
                    "collapse_whitespace_node",
                    "strip_comments_node",
                    "transform_node",
                )
            ),
        ),
        Target(
            "content-links",
            _links,
            tuple(
                f"turbohtml.clean.{name}"
                for name in (
                    "DEFAULT_CALLBACKS",
                    "DEFAULT_PHONE_LABELS",
                    "Callback",
                    "LinkCandidate",
                    "LinkDetector",
                    "LinkSpan",
                    "Linker",
                    "Linkify",
                    "PhoneFormat",
                    "PhoneGrouping",
                    "PhoneNumber",
                    "PhoneNumbers",
                    "PhoneType",
                    "linkify",
                    "linkify_node",
                    "nofollow",
                    "target_blank",
                )
            ),
        ),
        Target(
            "content-minify",
            _minify,
            tuple(
                f"turbohtml.clean.{name}"
                for name in (
                    "CSSMinify",
                    "JSMinify",
                    "Minify",
                    "minify",
                    "minify_css_inline",
                )
            ),
        ),
        Target(
            "content-detect",
            _detect,
            tuple(
                f"turbohtml.detect.{name}"
                for name in (
                    "Detection",
                    "EncodingDetector",
                    "EncodingMatch",
                    "LanguageDetection",
                    "LanguageMatch",
                    "NormalizationForm",
                    "detect",
                    "detect_all",
                    "detect_language",
                    "is_normalized",
                    "normalize",
                )
            ),
            chunked=_detect_chunks,
        ),
        Target(
            "content-extract",
            _extract,
            (
                *(
                    f"turbohtml.extract.{name}"
                    for name in (
                        "Article",
                        "DateExtraction",
                        "Element",
                        "Entry",
                        "Extraction",
                        "Feed",
                        "Link",
                        "MicrodataItem",
                        "OpenGraph",
                        "Paragraph",
                        "PublicationDate",
                        "RdfaItem",
                        "StructuredData",
                        "UrlCleaning",
                        "boilerplate",
                        "clean_url",
                        "dates",
                        "extract_links",
                        "feed",
                        "microdata",
                        "normalize_url",
                        "opengraph",
                    )
                ),
                *(
                    f"turbohtml.{name}"
                    for name in (
                        "Article",
                        "Entry",
                        "Feed",
                        "Link",
                        "MicrodataItem",
                        "OpenGraph",
                        "RdfaItem",
                        "StructuredData",
                    )
                ),
            ),
        ),
        Target(
            "content-migration",
            _migration,
            (
                *(
                    f"turbohtml.migration.bleach.{name}"
                    for name in ("ALLOWED_ATTRIBUTES", "ALLOWED_PROTOCOLS", "ALLOWED_TAGS", "attribute_policy", "clean")
                ),
                *(
                    f"turbohtml.migration.markupsafe.{name}"
                    for name in ("EscapeFormatter", "Markup", "escape", "escape_silent", "soft_str")
                ),
            ),
        ),
        Target(
            "content-stdlib",
            _stdlib,
            ("turbohtml.migration.stdlib.HTMLParser",),
            (UnicodeDecodeError,),
            _stdlib_chunks,
        ),
    )


def _sanitize(data: bytes) -> None:
    sanitizer_check(data)
    text: Final = _text(data)
    markup: Final = f'<b title="{text}" onclick="ignored">{text}</b><!--comment--><unknown>{text}</unknown>'
    options: Final = clean.Policy(
        tags=clean.DEFAULT_TAGS,
        attributes=clean.DEFAULT_ATTRIBUTES,
        url_schemes=clean.DEFAULT_SCHEMES,
        css_properties=clean.DEFAULT_CSS_PROPERTIES,
        on_disallowed_tag=clean.OnDisallowed.STRIP,
        transform_tags={"unknown": clean.Transform("b")},
    )
    expected: Final = f"<b>{text}</b><b>{text}</b>"
    _equal(sanitizer_observation(data), (expected, [clean.Removed("b", "title"), clean.Removed("b", "onclick")]))
    _equal(clean.sanitize(markup, options), expected)
    sanitizer: Final = clean.Sanitizer(options)
    _equal(sanitizer.sanitize(markup), expected)
    rendered, removed = clean.sanitize_report(markup, options)
    _equal(rendered, expected)
    _equal(removed, [clean.Removed("b", "title"), clean.Removed("b", "onclick")])
    _equal(sanitizer.sanitize_report(markup), (rendered, removed))
    _equal(clean.sanitize_node(parse_fragment(markup), options).inner_html, expected)
    node, report = clean.sanitize_report_node(parse_fragment(markup), options)
    _equal((node.inner_html, report), (expected, removed))
    _equal(sanitizer.sanitize_node(parse_fragment(markup)).inner_html, expected)
    node, report = sanitizer.sanitize_report_node(parse_fragment(markup))
    _equal((node.inner_html, report), (expected, removed))
    node = parse_fragment(f"<p>{text}  x<!--comment--></p>")
    _equal(clean.transform_node(node, clean.strip_comments_node, clean.collapse_whitespace_node).text, f"{text} x")


def _links(data: bytes) -> None:
    text: Final = _text(data)
    url: Final = f"https://example.com/{text}"
    candidate: Final = clean.LinkCandidate(url, text)
    callback: Final[clean.Callback] = clean.target_blank
    _equal(callback(clean.nofollow(candidate)).attrs, {"rel": "nofollow", "target": "_blank"})
    options: Final = clean.Linkify(callbacks=(*clean.DEFAULT_CALLBACKS, callback))
    linked: Final = f'<a href="{url}" rel="nofollow" target="_blank">{url}</a>'
    _equal(clean.linkify(url, options), linked)
    _equal(clean.Linker(options).linkify(url), linked)
    _equal(clean.linkify_node(parse_fragment(url), options).inner_html, linked)
    _equal(clean.Linker(options).linkify_node(parse_fragment(url)).inner_html, linked)
    _equal(clean.LinkDetector().find(url), [clean.LinkSpan(0, len(url), url, url, is_email=False)])
    phones: Final = clean.PhoneNumbers(
        regions=("US",),
        grouping=clean.PhoneGrouping.EXACT,
        types=(clean.PhoneType.FIXED_LINE_OR_MOBILE,),
        ignore_numbers_after=clean.DEFAULT_PHONE_LABELS,
    )
    number: Final = clean.PhoneNumber.parse("650-253-0000", regions=("US",))
    _equal(number, clean.PhoneNumber(1, "6502530000", None, "US", clean.PhoneType.FIXED_LINE_OR_MOBILE))
    _equal(number.format(clean.PhoneFormat.E164), "+16502530000")
    spans: Final = clean.LinkDetector(phones=phones).find(f"{text} 650-253-0000")
    _equal([span.phone for span in spans], [number])


def _minify(data: bytes) -> None:
    minifier_check(data)
    text: Final = _text(data)
    _equal(
        minifier_observation(data),
        (
            f"<html><head></head><body><p>{text}</p></body></html>",
            f".{text}{{color:red}}",
            "color:red",
            f'const value="{text}"',
        ),
    )


def _detect(data: bytes) -> None:
    text: Final = _text(data)
    _equal(encoding_stream_check(f"utf-8-bom\n{text}"), None)
    _equal(encoding_decode_check(f"utf-8-bom\n{text}"), None)
    encoded: Final = b"\xef\xbb\xbf" + text.encode()
    options: Final = detect.Detection()
    match: Final = detect.detect(encoded, options)
    _equal(match, detect.EncodingMatch("UTF-8-SIG", 1.0, None, bom=True, codec="whatwg-utf-8-sig"))
    _equal(detect.detect_all(encoded, options)[0], match)
    stream: Final = detect.EncodingDetector(options)
    stream.feed(b"")
    for chunk in encoded:
        stream.feed(bytes((chunk,)))
    _equal(stream.close(), match)
    language: Final = detect.detect_language(str(len(data)), detect.LanguageDetection(allowed=frozenset({"eng"})))
    _equal(language, detect.LanguageMatch(None, 0.0, None))
    for form in cast("tuple[detect.NormalizationForm, ...]", ("NFC", "NFD", "NFKC", "NFKD")):
        normalized: Final = detect.normalize(form, f"{text}e\u0301")
        _equal(normalized, unicodedata.normalize(form, f"{text}e\u0301"))
        _equal(detect.is_normalized(form, normalized), expected=True)


def _detect_chunks(data: bytes, header: Header) -> None:
    _detect(data)
    detector_check(data, header)


def _extract(data: bytes) -> None:
    url_check(data)
    text: Final = _text(data)
    url: Final = f"https://example.com/{text}"
    markup: Final = (
        f'<title>{text}</title><meta property="og:title" content="{text}">'
        f'<meta property="og:type" content="article"><meta property="og:image" content="{url}">'
        f'<meta property="og:url" content="{url}"><meta name="date" content="2020-01-02">'
        f'<article><p>{text}</p></article><a href="{url}">{text}</a>'
        f'<div itemscope itemtype="https://schema.org/Thing"><span itemprop="name">{text}</span></div>'
        f'<div vocab="https://schema.org/" typeof="Thing"><span property="name">{text}</span></div>'
    )
    document: Final = turbohtml.parse(markup)
    article: Final = document.article()
    _equal(isinstance(article, (extract.Article, turbohtml.Article)), expected=True)
    _equal(article.title, text)
    links: Final = document.links()
    _equal(isinstance(links[0], (extract.Link, turbohtml.Link)), expected=True)
    _equal(links[0].url, url)
    _equal(extract.extract_links(markup), {url})
    _equal(extract.normalize_url(url, extract.UrlCleaning()), url)
    _equal(extract.clean_url(url, extract.UrlCleaning()), url)
    paragraphs: Final = extract.boilerplate(f"<article><p>{text}</p></article>", extract.Extraction(min_length=0))
    _equal(isinstance(paragraphs[0], extract.Paragraph), expected=True)
    _equal(paragraphs[0].text, text)
    _equal(isinstance(document.select_one("article"), extract.Element), expected=True)
    items: Final = extract.microdata(markup)
    _equal(isinstance(items[0], (extract.MicrodataItem, turbohtml.MicrodataItem)), expected=True)
    _equal(items[0].get_all("name"), [text])
    _equal(json.loads(items[0].json())["properties"]["name"], [text])
    graph: Final = extract.opengraph(markup)
    _equal(isinstance(graph, (extract.OpenGraph, turbohtml.OpenGraph)), expected=True)
    _equal((graph["title"], graph.is_valid()), (text, True))
    structured: Final = document.structured_data()
    _equal(isinstance(structured, (extract.StructuredData, turbohtml.StructuredData)), expected=True)
    _equal(isinstance(structured.rdfa[0], (extract.RdfaItem, turbohtml.RdfaItem)), expected=True)
    _equal(structured.rdfa[0].get("https://schema.org/name"), text)
    publication: Final = cast("extract.PublicationDate", extract.dates(markup, extract.DateExtraction()))
    _equal(isinstance(publication, extract.PublicationDate), expected=True)
    _equal(str(publication.date), "2020-01-02")
    feed: Final = cast(
        "extract.Feed",
        extract.feed(f"<rss><channel><title>{text}</title><item><title>{text}</title></item></channel></rss>"),
    )
    _equal(isinstance(feed, (extract.Feed, turbohtml.Feed)), expected=True)
    _equal((feed.title, feed.entries[0].title), (text, text))
    _equal(isinstance(feed.entries[0], (extract.Entry, turbohtml.Entry)), expected=True)


def _migration(data: bytes) -> None:
    text: Final = _text(data)
    _equal(
        bleach.clean(
            f"<b>{text}</b><!--comment-->",
            tags=bleach.ALLOWED_TAGS,
            attributes=bleach.ALLOWED_ATTRIBUTES,
            protocols=bleach.ALLOWED_PROTOCOLS,
        ),
        f"<b>{text}</b>",
    )
    names, predicate = bleach.attribute_policy({"b": ("title",)})
    _equal((names, predicate), ({"b": frozenset({"title"})}, None))
    raw: Final = f"<{text}>&\"'"
    expected: Final = html.escape(raw).replace("&#x27;", "&#39;").replace("&quot;", "&#34;")
    escaped: Final = markupsafe.escape(raw)
    _equal((isinstance(escaped, markupsafe.Markup), str(escaped)), (True, expected))
    _equal(str(markupsafe.escape_silent(raw)), expected)
    _equal(str(markupsafe.escape_silent(None)), "")
    _equal(markupsafe.soft_str(escaped), escaped)
    _equal(markupsafe.Markup(f"<b>{text}</b>").striptags(), text)
    _equal(markupsafe.EscapeFormatter(markupsafe.escape).vformat("{}", (raw,), {}), expected)


def _stdlib(data: bytes) -> None:
    text: Final = _text(data)
    parser: Final = _TextParser()
    markup: Final = f"<b>{text}&amp;</b>"
    parser.feed("")
    for char in markup:
        parser.feed(char)
    parser.close()
    _equal("".join(parser.text), f"{text}&")
    _equal(stdlib_observation(data), f"{text}&")
    parser.reset()
    _equal(parser.getpos(), (1, 0))


def _stdlib_chunks(data: bytes, header: Header) -> None:
    _stdlib(data)
    stdlib_check(data, header)


def detector_check(data: bytes, header: Header = BYTEWISE) -> None:
    """Require the fuzz bytes fed in chunks, empty ones included, to reach the answer one ``detect`` call gives."""
    options: Final = detect.Detection()
    stream: Final = detect.EncodingDetector(options)
    feed_chunks(stream.feed, data, header.max_chunk)
    _equal(stream.close(), detect.detect(data, options))


def stdlib_check(data: bytes, header: Header = BYTEWISE) -> None:
    """Require the decoded fuzz text fed in chunks to reach the handlers as one ``feed`` call does."""
    source: Final = data.decode("utf-8")
    chunked: Final = _TextParser()
    feed_chunks(chunked.feed, source, header.max_chunk)
    chunked.close()
    whole: Final = _TextParser()
    whole.feed(source)
    whole.close()
    _equal("".join(chunked.text), "".join(whole.text))


def minifier_check(
    data: bytes,
    css: Callable[[str], str] = clean.minify_css,
    js: Callable[[str], str] | None = None,
) -> None:
    """Stable minifier output must also preserve the stylesheet meaning."""
    text: Final = _text(data)
    sheet: Final = f".{text} {{ color: #ff0000; margin: 0px 0px }}"
    _equal(fixpoint_check(sheet, css, numeric=True), None)
    _equal(css_semantics_check(f'<style>{sheet}</style><p class="{text}">x</p>', css), None)
    _equal(
        fixpoint_check(
            f'const value = "{text}";',
            js or partial(clean.minify_js, options=clean.JSMinify(fold=False, mangle=False)),
            numeric=False,
        ),
        None,
    )


def url_check(data: bytes, normalize: Callable[[str], str] = extract.normalize_url) -> None:
    """Known-valid Unicode families distinguish stable wrong hosts from valid normalization."""
    url: Final = f"https://example.com/{_text(data)}"
    _equal(url_reparse_check(url, normalize), None)
    affix: Final = data[:4].hex()
    _equal(idna_host_check(f"acute-decomposed\n{affix}", normalize), None)
    _equal(idna_nfc_check(f"reorder:{affix}", normalize), None)
    _equal(normalize_url_check(url, normalize), None)
    _equal(clean_url_check(url), None)


def sanitizer_observation(data: bytes) -> tuple[str, list[clean.Removed]]:
    """Distinguish stripping from a no-op sanitizer with an explicit envelope."""
    text: Final = _text(data)
    return clean.sanitize_report(
        f'<b title="{text}" onclick="ignored">{text}</b><!--comment--><unknown>{text}</unknown>',
        clean.Policy(on_disallowed_tag=clean.OnDisallowed.STRIP, transform_tags={"unknown": clean.Transform("b")}),
    )


def sanitizer_check(
    data: bytes,
    render: Callable[[bytes], tuple[str, list[clean.Removed]]] = sanitizer_observation,
) -> None:
    """Reject wrong renderer results before treating an input as a valid oracle case."""
    text: Final = _text(data)
    _equal(render(data), (f"<b>{text}</b><b>{text}</b>", [clean.Removed("b", "title"), clean.Removed("b", "onclick")]))


def minifier_observation(data: bytes) -> tuple[str, str, str, str]:
    """Keep printer results available for independent behavioral controls."""
    text: Final = _text(data)
    return (
        clean.minify(f"<p><!--comment-->{text}</p>", clean.Minify(omit_optional_tags=False)),
        clean.minify_css(f".{text} {{ color: red; }}", clean.CSSMinify()),
        clean.minify_css_inline("color: red;", clean.CSSMinify()),
        clean.minify_js(f'const value = "{text}";', clean.JSMinify()),
    )


def stdlib_observation(data: bytes) -> str:
    """Chunked callbacks must retain decoded text rather than just complete parsing."""
    parser: Final = _TextParser()
    for char in f"<b>{_text(data)}&amp;</b>":
        parser.feed(char)
    parser.close()
    return "".join(parser.text)


def _text(data: bytes) -> str:
    return "value" + data[:32].hex()


def _equal(actual: object, expected: object) -> None:
    if actual != expected:
        msg = f"Content API mismatch: {actual!r} != {expected!r}"
        raise AssertionError(msg)


class _TextParser(stdlib.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: Final[list[str]] = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)
