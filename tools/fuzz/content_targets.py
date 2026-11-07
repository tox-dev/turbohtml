"""Fixed envelopes give content consumers independent expected results per input."""

from __future__ import annotations

import html
import json
import unicodedata
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import turbohtml
from turbohtml import clean, detect, extract, parse_fragment
from turbohtml.clean import (
    DEFAULT_ATTRIBUTES,
    DEFAULT_CALLBACKS,
    DEFAULT_CSS_PROPERTIES,
    DEFAULT_PHONE_LABELS,
    DEFAULT_SCHEMES,
    DEFAULT_TAGS,
    Callback,
    CSSMinify,
    JSMinify,
    LinkCandidate,
    LinkDetector,
    Linker,
    Linkify,
    LinkSpan,
    Minify,
    OnDisallowed,
    PhoneFormat,
    PhoneGrouping,
    PhoneNumber,
    PhoneNumbers,
    PhoneType,
    Policy,
    Removed,
    Sanitizer,
    Transform,
    collapse_whitespace_node,
    linkify,
    linkify_node,
    minify,
    minify_css_inline,
    nofollow,
    sanitize,
    sanitize_node,
    sanitize_report,
    sanitize_report_node,
    strip_comments_node,
    target_blank,
    transform_node,
)
from turbohtml.detect import (
    Detection,
    EncodingDetector,
    EncodingMatch,
    LanguageDetection,
    LanguageMatch,
    detect_all,
    detect_language,
    is_normalized,
    normalize,
)
from turbohtml.extract import (
    Article,
    DateExtraction,
    Element,
    Entry,
    Extraction,
    Link,
    MicrodataItem,
    OpenGraph,
    Paragraph,
    RdfaItem,
    StructuredData,
    UrlCleaning,
    boilerplate,
    clean_url,
    dates,
    extract_links,
    microdata,
    normalize_url,
    opengraph,
)
from turbohtml.extract import feed as _feed
from turbohtml.migration import bleach, markupsafe, stdlib
from turbohtml.migration.bleach import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS, attribute_policy
from turbohtml.migration.markupsafe import EscapeFormatter, Markup

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


def content_targets() -> tuple[Target, ...]:
    # Keep each qualified content export attached to an executable contract.
    return (
        Target(
            name="content-sanitize",
            callback=_sanitize,
            exports=tuple(
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
            name="content-links",
            callback=_links,
            exports=tuple(
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
            name="content-minify",
            callback=_minify,
            exports=tuple(
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
            name="content-detect",
            callback=_detect,
            exports=tuple(
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
            name="content-extract",
            callback=_extract,
            exports=(
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
            name="content-migration",
            callback=_migration,
            exports=(
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
            name="content-stdlib",
            callback=_stdlib,
            exports=("turbohtml.migration.stdlib.HTMLParser",),
            exceptions=(UnicodeDecodeError,),
            chunked=_stdlib_chunks,
        ),
    )


def _sanitize(data: bytes) -> None:
    sanitizer_check(data)
    text: Final = _text(data)
    _equal(Policy.strict().tags, frozenset())
    _equal(Policy.basic().tags, DEFAULT_TAGS)
    _equal("h1" in Policy.relaxed().tags, expected=True)
    markup: Final = f'<b title="{text}" onclick="ignored">{text}</b><!--comment--><unknown>{text}</unknown>'
    options: Final = Policy(
        tags=DEFAULT_TAGS,
        attributes=DEFAULT_ATTRIBUTES,
        url_schemes=DEFAULT_SCHEMES,
        css_properties=DEFAULT_CSS_PROPERTIES,
        on_disallowed_tag=OnDisallowed.STRIP,
        transform_tags={"unknown": Transform("b")},
    )
    expected: Final = f"<b>{text}</b><b>{text}</b>"
    _equal(sanitizer_observation(data), (expected, [Removed("b", "title"), Removed("b", "onclick")]))
    _equal(sanitize(markup, options), expected)
    sanitizer: Final = Sanitizer(options)
    _equal(sanitizer.sanitize(markup), expected)
    rendered, removed = sanitize_report(markup, options)
    _equal(rendered, expected)
    _equal(removed, [Removed("b", "title"), Removed("b", "onclick")])
    _equal(sanitizer.sanitize_report(markup), (rendered, removed))
    _equal(sanitize_node(parse_fragment(markup), options).inner_html, expected)
    node, report = sanitize_report_node(parse_fragment(markup), options)
    _equal((node.inner_html, report), (expected, removed))
    _equal(sanitizer.sanitize_node(parse_fragment(markup)).inner_html, expected)
    node, report = sanitizer.sanitize_report_node(parse_fragment(markup))
    _equal((node.inner_html, report), (expected, removed))
    node = parse_fragment(f"<p>{text}  x<!--comment--></p>")
    _equal(transform_node(node, strip_comments_node, collapse_whitespace_node).text, f"{text} x")


def _links(data: bytes) -> None:
    text: Final = _text(data)
    url: Final = f"https://example.com/{text}"
    candidate: Final = LinkCandidate(url, text)
    callback: Final[Callback] = target_blank
    _equal(callback(nofollow(candidate)).attrs, {"rel": "nofollow", "target": "_blank"})
    options: Final = Linkify(callbacks=(*DEFAULT_CALLBACKS, callback))
    linked: Final = f'<a href="{url}" rel="nofollow" target="_blank">{url}</a>'
    _equal(linkify(url, options), linked)
    _equal(Linker(options).linkify(url), linked)
    _equal(linkify_node(parse_fragment(url), options).inner_html, linked)
    _equal(Linker(options).linkify_node(parse_fragment(url)).inner_html, linked)
    _equal(LinkDetector().find(url), [LinkSpan(0, len(url), url, url, is_email=False)])
    phones: Final = PhoneNumbers(
        regions=("US",),
        grouping=PhoneGrouping.EXACT,
        types=(PhoneType.FIXED_LINE_OR_MOBILE,),
        ignore_numbers_after=DEFAULT_PHONE_LABELS,
    )
    number: Final = PhoneNumber.parse("650-253-0000", regions=("US",))
    _equal(number, PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE))
    _equal(number.format(PhoneFormat.E164), "+16502530000")
    spans: Final = LinkDetector(phones=phones).find(f"{text} 650-253-0000")
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


def minifier_check(
    data: bytes,
    css: Callable[[str], str] = clean.minify_css,
    js: Callable[[str], str] | None = None,
) -> None:
    # Stable minifier output must also preserve the stylesheet meaning.
    text: Final = _text(data)
    sheet: Final = f".{text} {{ color: #ff0000; margin: 0px 0px }}"
    _equal(fixpoint_check(sheet, css, numeric=True), None)
    _equal(css_semantics_check(f'<style>{sheet}</style><p class="{text}">x</p>', css), None)
    _equal(
        fixpoint_check(
            f'const value = "{text}";',
            js or partial(clean.minify_js, options=JSMinify(fold=False, mangle=False)),
            numeric=False,
        ),
        None,
    )


def minifier_observation(data: bytes) -> tuple[str, str, str, str]:
    # Keep printer results available for independent behavioral controls.
    text: Final = _text(data)
    return (
        minify(f"<p><!--comment-->{text}</p>", Minify(omit_optional_tags=False)),
        clean.minify_css(f".{text} {{ color: red; }}", CSSMinify()),
        minify_css_inline("color: red;", CSSMinify()),
        clean.minify_js(f'const value = "{text}";', JSMinify()),
    )


def _detect(data: bytes) -> None:
    text: Final = _text(data)
    _equal(encoding_stream_check(f"utf-8-bom\n{text}"), None)
    _equal(encoding_decode_check(f"utf-8-bom\n{text}"), None)
    encoded: Final = b"\xef\xbb\xbf" + text.encode()
    options: Final = Detection()
    match: Final = detect.detect(encoded, options)
    _equal(
        match,
        EncodingMatch(encoding="UTF-8-SIG", confidence=1.0, language=None, bom=True, codec="whatwg-utf-8-sig"),
    )
    _equal(detect_all(encoded, options)[0], match)
    stream: Final = EncodingDetector(options)
    stream.feed(b"")
    for chunk in encoded:
        stream.feed(bytes((chunk,)))
    _equal(stream.close(), match)
    language: Final = detect_language(str(len(data)), LanguageDetection(allowed=frozenset({"eng"})))
    _equal(language, LanguageMatch(language=None, confidence=0.0, script=None))
    for form in cast("tuple[detect.NormalizationForm, ...]", ("NFC", "NFD", "NFKC", "NFKD")):
        normalized: Final = normalize(form, f"{text}e\u0301")
        _equal(normalized, unicodedata.normalize(form, f"{text}e\u0301"))
        _equal(is_normalized(form, normalized), expected=True)


def _detect_chunks(data: bytes, header: Header) -> None:
    _detect(data)
    detector_check(data, header)


def detector_check(data: bytes, header: Header = BYTEWISE) -> None:
    # Require the fuzz bytes fed in chunks, empty ones included, to reach the answer one ``detect`` call gives.
    options: Final = Detection()
    stream: Final = EncodingDetector(options)
    feed_chunks(stream.feed, data, header.max_chunk)
    _equal(stream.close(), detect.detect(data, options))


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
    _equal(isinstance(article, (Article, turbohtml.Article)), expected=True)
    _equal(article.title, text)
    links: Final = document.links()
    _equal(isinstance(links[0], (Link, turbohtml.Link)), expected=True)
    _equal(links[0].url, url)
    _equal(extract_links(markup), {url})
    _equal(normalize_url(url, UrlCleaning()), url)
    _equal(clean_url(url, UrlCleaning()), url)
    paragraphs: Final = boilerplate(f"<article><p>{text}</p></article>", Extraction(min_length=0))
    _equal(isinstance(paragraphs[0], Paragraph), expected=True)
    _equal(paragraphs[0].text, text)
    _equal(isinstance(document.select_one("article"), Element), expected=True)
    items: Final = microdata(markup)
    _equal(isinstance(items[0], (MicrodataItem, turbohtml.MicrodataItem)), expected=True)
    _equal(items[0].get_all("name"), [text])
    _equal(json.loads(items[0].json())["properties"]["name"], [text])
    graph: Final = opengraph(markup)
    _equal(isinstance(graph, (OpenGraph, turbohtml.OpenGraph)), expected=True)
    _equal((graph["title"], graph.is_valid()), (text, True))
    structured: Final = document.structured_data()
    _equal(isinstance(structured, (StructuredData, turbohtml.StructuredData)), expected=True)
    _equal(isinstance(structured.rdfa[0], (RdfaItem, turbohtml.RdfaItem)), expected=True)
    _equal(structured.rdfa[0].get("https://schema.org/name"), text)
    publication: Final = cast("extract.PublicationDate", dates(markup, DateExtraction()))
    _equal(isinstance(publication, extract.PublicationDate), expected=True)
    _equal(str(publication.date), "2020-01-02")
    feed: Final = cast(
        "extract.Feed",
        _feed(f"<rss version='2.0'><channel><title>{text}</title><item><title>{text}</title></item></channel></rss>"),
    )
    _equal(isinstance(feed, (extract.Feed, turbohtml.Feed)), expected=True)
    _equal((feed.title, feed.entries[0].title), (text, text))
    _equal(isinstance(feed.entries[0], (Entry, turbohtml.Entry)), expected=True)


def url_check(data: bytes, normalize: Callable[[str], str] = normalize_url) -> None:
    # Known-valid Unicode families distinguish stable wrong hosts from valid normalization.
    url: Final = f"https://example.com/{_text(data)}"
    _equal(url_reparse_check(url, normalize), None)
    affix: Final = data[:4].hex()
    _equal(idna_host_check(f"acute-decomposed\n{affix}", normalize), None)
    _equal(idna_nfc_check(f"reorder:{affix}", normalize), None)
    _equal(normalize_url_check(url, normalize), None)
    _equal(clean_url_check(url), None)


def _migration(data: bytes) -> None:
    text: Final = _text(data)
    _equal(
        bleach.clean(
            f"<b>{text}</b><!--comment-->",
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            protocols=ALLOWED_PROTOCOLS,
        ),
        f"<b>{text}</b>",
    )
    names, predicate = attribute_policy({"b": ("title",)})
    _equal((names, predicate), ({"b": frozenset({"title"})}, None))
    raw: Final = f"<{text}>&\"'"
    expected: Final = html.escape(raw).replace("&#x27;", "&#39;").replace("&quot;", "&#34;")
    escaped: Final = markupsafe.escape(raw)
    _equal((isinstance(escaped, Markup), str(escaped)), (True, expected))
    _equal(str(markupsafe.escape_silent(raw)), expected)
    _equal(str(markupsafe.escape_silent(None)), "")
    _equal(markupsafe.soft_str(escaped), escaped)
    _equal(Markup(f"<b>{text}</b>").striptags(), text)
    _equal(EscapeFormatter(markupsafe.escape).vformat("{}", (raw,), {}), expected)


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


class _TextParser(stdlib.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: Final[list[str]] = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def stdlib_observation(data: bytes) -> str:
    # Chunked callbacks must retain decoded text rather than just complete parsing.
    parser: Final = _TextParser()
    for char in f"<b>{_text(data)}&amp;</b>":
        parser.feed(char)
    parser.close()
    return "".join(parser.text)


def _stdlib_chunks(data: bytes, header: Header) -> None:
    _stdlib(data)
    stdlib_check(data, header)


def stdlib_check(data: bytes, header: Header = BYTEWISE) -> None:
    # Require the decoded fuzz text fed in chunks to reach the handlers as one ``feed`` call does.
    source: Final = data.decode("utf-8")
    chunked: Final = _TextParser()
    feed_chunks(chunked.feed, source, header.max_chunk)
    chunked.close()
    whole: Final = _TextParser()
    whole.feed(source)
    whole.close()
    _equal("".join(chunked.text), "".join(whole.text))


def sanitizer_observation(data: bytes) -> tuple[str, list[Removed]]:
    # Distinguish stripping from a no-op sanitizer with an explicit envelope.
    text: Final = _text(data)
    return sanitize_report(
        f'<b title="{text}" onclick="ignored">{text}</b><!--comment--><unknown>{text}</unknown>',
        Policy(on_disallowed_tag=OnDisallowed.STRIP, transform_tags={"unknown": Transform("b")}),
    )


def sanitizer_check(
    data: bytes,
    render: Callable[[bytes], tuple[str, list[Removed]]] = sanitizer_observation,
) -> None:
    # Reject wrong renderer results before treating an input as a valid oracle case.
    text: Final = _text(data)
    _equal(render(data), (f"<b>{text}</b><b>{text}</b>", [Removed("b", "title"), Removed("b", "onclick")]))


def _text(data: bytes) -> str:
    return f"value{data[:32].hex()}"


def _equal(actual: object, expected: object) -> None:
    if actual != expected:
        msg = f"Content API mismatch: {actual!r} != {expected!r}"
        raise AssertionError(msg)


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
