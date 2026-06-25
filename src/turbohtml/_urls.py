"""
Clean, normalize, and harvest the URLs in a document, the way ``courlan`` and ``w3lib.url`` do.

``courlan`` (a ``trafilatura`` dependency) scrubs tracking junk off a URL, canonicalizes it, and pulls the filtered
links out of a page; ``w3lib.url`` canonicalizes a single URL. turbohtml already finds and absolutizes links in C
(:meth:`~turbohtml.Node.links` / :meth:`~turbohtml.Node.resolve_links`); this module adds the small pure-Python
normalization pass that turns those raw links into a clean, deduplicated set.

What is deliberately left out of ``courlan``: the language heuristics (``TARGET_LANGS``/``hreflang`` filtering) and the
spam/quality heuristics (navigation-page detection, redirect probing over HTTP). turbohtml normalizes and deduplicates
URLs; it does not guess a page's language or rank a link's crawl-worthiness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qsl, quote, urlencode, urlsplit, urlunsplit

from ._html import parse

__all__ = ["UrlCleaning", "clean_url", "extract_links", "normalize_url"]

# Control characters (everything below U+0020) are stripped from a scrubbed URL's ends, matching courlan's scrub_url.
_CONTROL_CHARS = "".join(map(chr, range(0x20)))

# Short markup remnants (``</p>``, ``{...}``) that survive a sloppy copy-paste of an href out of a page.
_REMAINING_MARKUP = re.compile(r"</?[a-z]{,4}?>|{.+?}")

# A URL ends at the first whitespace, quote, or angle bracket: everything past it is the surrounding markup, not the URL.
_TRAILING_PARTS = re.compile(r'(.*?)[<>"\s]')

# A scheme's default port carries no information once the scheme is known, so ``:80``/``:443`` is dropped after a host.
_DEFAULT_PORT = re.compile(r"(?<=\w):(?:80|443)$")

# Runs of slashes in a path collapse to one; a leading ``/../`` cannot climb above the root and is removed.
_DUP_SLASH = re.compile(r"/+")
_LEADING_PARENT = re.compile(r"^(?:/\.\.(?![^/]))+")

# Tracking query parameters (ad-click ids, analytics campaign tags) carry no content and are dropped from a clean URL.
# Ported from courlan's TRACKERS_RE, itself drawn from the AdGuard and ClearURLs rule sets.
_TRACKERS = re.compile(
    r"^(?:dc|fbc|gc|twc|yc|ysc)lid|"
    r"^(?:click|gbra|msclk|igsh|partner|wbra)id|"
    r"^(?:ads?|mc|ga|gs|itm|mc|mkt|ml|mtm|oly|pk|utm|vero)_|"
    r"(?:\b|_)(?:aff|affi|affiliate|campaign|cl?id|eid|ga|gl|"
    r"kwd|keyword|medium|ref|referr?er|session|source|uid|xtor)"
)

# Under strict cleaning only these content-bearing identifiers survive in the query string; every other parameter goes.
_ALLOWED_PARAMS = frozenset({
    "aid",
    "article_id",
    "artnr",
    "id",
    "itemid",
    "objectid",
    "p",
    "page",
    "pagenum",
    "page_id",
    "pid",
    "post",
    "postid",
    "product_id",
})

# Only web links survive extraction and normalization; mailto/tel/javascript and bare fragments are not crawlable URLs.
_WEB_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class UrlCleaning:
    """
    How :func:`clean_url`, :func:`normalize_url`, and :func:`extract_links` canonicalize a URL.

    Immutable and thread-safe: build one and reuse it across threads. Every field has a default that reproduces the
    no-argument behaviour, so ``UrlCleaning()`` is the same as passing nothing.

    :param strict: keep only the small allowlist of content-bearing query parameters (``id``, ``page``, ...) and drop
        every other parameter and the fragment; the strongest canonicalization, for deduplicating crawl frontiers.
    :param trailing_slash: keep a path's trailing slash; set to ``False`` to trim it so ``/a/`` and ``/a`` collapse.
    :param strip_fragment: drop the ``#fragment``; ``strict`` already implies this.
    :param strip_trackers: drop tracking query parameters (ad-click ids, ``utm_*`` campaign tags) in non-strict mode;
        meaningless under ``strict`` (which already keeps only the allowlist) so the two cannot be combined.
    """

    strict: bool = False
    trailing_slash: bool = True
    strip_fragment: bool = False
    strip_trackers: bool = True

    def __post_init__(self) -> None:
        """Reject the contradiction of asking to keep trackers while strict mode drops everything but the allowlist."""
        if self.strict and not self.strip_trackers:
            message = "strip_trackers=False is meaningless with strict=True, which keeps only the allowlist"
            raise ValueError(message)

    @classmethod
    def aggressive(cls) -> UrlCleaning:
        """The strongest canonicalization: allowlist-only query, no fragment, no trailing slash (courlan's strict mode)."""
        return cls(strict=True, trailing_slash=False, strip_fragment=True)


_DEFAULT = UrlCleaning()


def _clean_query(query: str, options: UrlCleaning) -> str:
    """Sort the query parameters and drop the unwanted ones, returning a canonical query string."""
    if not query:
        return ""
    kept = []
    for name, value in sorted(parse_qsl(query, keep_blank_values=True)):
        lowered = name.lower()
        if options.strict:
            if lowered not in _ALLOWED_PARAMS:
                continue
        elif options.strip_trackers and _TRACKERS.search(lowered):
            continue
        kept.append((name, value))
    return urlencode(kept)


def _normalize_fragment(fragment: str, options: UrlCleaning) -> str:
    """Drop a fragment that is itself a tracker, otherwise percent-encode it; empty when fragments are stripped."""
    if options.strict or options.strip_fragment:
        return ""
    if "=" in fragment and "&" not in fragment and options.strip_trackers and _TRACKERS.search(fragment):
        return ""
    return quote(fragment, safe="/%!=:,-")


def normalize_url(url: str | SplitResult, options: UrlCleaning | None = None, /) -> str:
    """
    Canonicalize a single URL: lowercase the scheme and host, sort and filter the query, and tidy the path.

    This is the ``courlan.normalize_url`` / ``w3lib.url.canonicalize_url`` mapping. It assumes an already-absolute URL
    (run it through :func:`clean_url` or :meth:`~turbohtml.Node.resolve_links` first if it might be relative or dirty).

    :param url: the URL string, or a pre-split :class:`urllib.parse.SplitResult`.
    :param options: the cleaning configuration; ``None`` uses the defaults.
    :returns: the canonical URL string.
    """
    options = options if options is not None else _DEFAULT
    parsed = url if isinstance(url, SplitResult) else urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = _DEFAULT_PORT.sub("", parsed.netloc.lower()) if scheme in _WEB_SCHEMES else parsed.netloc.lower()
    path = quote(_LEADING_PARENT.sub("", _DUP_SLASH.sub("/", parsed.path)), safe="/%!=:,-")
    query = _clean_query(parsed.query, options)
    if query and not path:
        path = "/"
    elif not options.trailing_slash and not query and len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    fragment = _normalize_fragment(parsed.fragment, options)
    return urlunsplit((scheme, netloc, path, query, fragment))


def _scrub(url: str) -> str:
    """Strip whitespace, control characters, CDATA wrappers, and stray markup off a raw href."""
    url = "".join(url.split()).strip(_CONTROL_CHARS)
    if url.startswith("<![CDATA[") and url.endswith("]]>"):
        url = url[len("<![CDATA[") : -len("]]>")]
    url = _REMAINING_MARKUP.sub("", url)
    url = url.replace("&amp;", "&")
    url = url.removesuffix("/&")
    if (match := _TRAILING_PARTS.match(url)) is not None:
        url = match[1]
    if url.count("/") == 3 or url.count("://") > 1:
        url = url.rstrip("/")
    return url


def clean_url(url: str, options: UrlCleaning | None = None, /) -> str | None:
    """
    Scrub tracking junk and stray markup off a URL, then canonicalize it; the ``courlan.clean_url`` mapping.

    Returns ``None`` when the input cannot be made into a usable URL (it is empty after scrubbing), so a caller can
    filter a stream of dirty hrefs in one pass.

    :param url: the raw URL, possibly carrying surrounding markup, escaped ampersands, or tracking parameters.
    :param options: the cleaning configuration; ``None`` uses the defaults.
    :returns: the cleaned, canonical URL, or ``None`` when nothing usable remains.
    """
    scrubbed = _scrub(url)
    if not scrubbed:
        return None
    return normalize_url(scrubbed, options)


def extract_links(
    html: str,
    base_url: str | None = None,
    *,
    external_only: bool = False,
    options: UrlCleaning | None = None,
) -> set[str]:
    """
    Harvest every web link in a document as a clean, absolute, deduplicated set; the ``courlan.extract_links`` mapping.

    Parses ``html`` with the WHATWG tree builder, finds every link the way :meth:`~turbohtml.Node.links` does (so it
    sees ``srcset``, ``<meta refresh>``, and CSS ``url()`` too, not only ``<a href>``), absolutizes each against
    ``base_url``, then keeps only the ``http``/``https`` links, cleaned and canonicalized through :func:`clean_url`.

    :param html: the page source.
    :param base_url: the document's own URL, used to absolutize relative links; without it relative links are dropped.
    :param external_only: keep only links whose host differs from ``base_url``'s (``True``) or only same-host links
        (``False``); ignored when ``base_url`` is ``None``.
    :param options: the cleaning configuration applied to each link; ``None`` uses the defaults.
    :returns: the set of cleaned absolute URLs.
    """
    document = parse(html)
    if base_url is not None:
        document.resolve_links(base_url)
    reference_host = urlsplit(base_url).netloc.lower() if base_url is not None else None
    links: set[str] = set()
    for link in document.links():
        cleaned = clean_url(link.url, options)
        if cleaned is None:
            continue
        parsed = urlsplit(cleaned)
        if parsed.scheme not in _WEB_SCHEMES or not parsed.netloc:
            continue
        if reference_host is not None and external_only == (parsed.netloc.lower() == reference_host):
            continue
        links.add(cleaned)
    return links
