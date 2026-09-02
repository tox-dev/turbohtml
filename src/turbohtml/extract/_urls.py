"""
URL cleaning, normalization, and page-level link extraction for :mod:`turbohtml.extract`.

The normalization core follows the `WHATWG URL standard <https://url.spec.whatwg.org/>`__ wherever it defines the
behavior: input stripping (basic URL parser, spec 4.4), scheme lowercasing (scheme state), host lowercasing and
domain-to-ASCII (host parsing, spec 3.5), default-port removal (port state), dot-segment resolution including the
``%2e`` forms (path state), the empty-path-to-``/`` serialization of special URLs (URL serializing, spec 4.5), and
the path/query/fragment percent-encode sets (percent-encoded bytes, spec 1.3). On top of that sits the crawl-oriented
canonicalization ``courlan`` and ``w3lib`` users expect -- query-parameter sorting, tracking-parameter removal, an
optional strict parameter allowlist, and a URL-based language filter -- each documented where it goes beyond the spec.
The pipeline runs in C (``_url_clean``, ``_url_normalize`` and ``Document._extract_links``); this module holds the
option record and the two vocabularies it hands over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from turbohtml._html import _url_clean, _url_normalize, parse

__all__ = [
    "UrlCleaning",
    "clean_url",
    "extract_links",
    "normalize_url",
]

_ISO_639_1: Final[frozenset[str]] = frozenset([
    *("aa", "ab", "ae", "af", "ak", "am", "an", "ar", "as", "av", "ay", "az", "ba", "be", "bg", "bh", "bi", "bm"),
    *("bn", "bo", "br", "bs", "ca", "ce", "ch", "co", "cr", "cs", "cu", "cv", "cy", "da", "de", "dv", "dz", "ee"),
    *("el", "en", "eo", "es", "et", "eu", "fa", "ff", "fi", "fj", "fo", "fr", "fy", "ga", "gd", "gl", "gn", "gu"),
    *("gv", "ha", "he", "hi", "ho", "hr", "ht", "hu", "hy", "hz", "ia", "id", "ie", "ig", "ii", "ik", "io", "is"),
    *("it", "iu", "ja", "jv", "ka", "kg", "ki", "kj", "kk", "kl", "km", "kn", "ko", "kr", "ks", "ku", "kv", "kw"),
    *("ky", "la", "lb", "lg", "li", "ln", "lo", "lt", "lu", "lv", "mg", "mh", "mi", "mk", "ml", "mn", "mr", "ms"),
    *("mt", "my", "na", "nb", "nd", "ne", "ng", "nl", "nn", "no", "nr", "nv", "ny", "oc", "oj", "om", "or", "os"),
    *("pa", "pi", "pl", "ps", "pt", "qu", "rm", "rn", "ro", "ru", "rw", "sa", "sc", "sd", "se", "sg", "si", "sk"),
    *("sl", "sm", "sn", "so", "sq", "sr", "ss", "st", "su", "sv", "sw", "ta", "te", "tg", "th", "ti", "tk", "tl"),
    *("tn", "to", "tr", "ts", "tt", "tw", "ty", "ug", "uk", "ur", "uz", "ve", "vi", "vo", "wa", "wo", "xh", "yi"),
    *("yo", "za", "zh", "zu"),
])
"""The ISO 639-1 two-letter language codes, gating which URL segments count as language markers."""

_CONTENT_PARAMS: Final[frozenset[str]] = frozenset({
    "aid",
    "article_id",
    "artnr",
    "id",
    "itemid",
    "objectid",
    "p",
    "page",
    "page_id",
    "pagenum",
    "pid",
    "post",
    "postid",
    "product_id",
})
"""The content-identifying query parameters strict mode keeps; everything else is presumed decorative."""

_LANGUAGE_PARAMS: Final[frozenset[str]] = frozenset({"lang", "language"})


@dataclass(frozen=True, slots=True)
class UrlCleaning:
    """
    Options shared by :func:`clean_url`, :func:`normalize_url`, and :func:`extract_links`.

    :param strict: keep only the content-identifying query parameters (page, id, post, ... plus the language
        parameters) instead of merely dropping known trackers, and drop the fragment. The default keeps every
        parameter that is not a known tracker.
    :param trailing_slash: keep a path's trailing slash. ``False`` trims it from any path but the root ``/`` when no
        query string follows, folding ``/dir/`` and ``/dir`` into one form.
    :param strip_fragment: always drop the fragment. The default keeps it (scrubbed of tracker parameters), since the
        fragment can address content (``#page2``, text fragments).
    :param language: an ISO 639-1 code; :func:`clean_url` and :func:`extract_links` then reject URLs whose language
        markers (a leading path segment such as ``/de/``, a ``lang``/``language`` query parameter, or an anchor's
        ``hreflang``) point at another language. :func:`normalize_url` never rejects, so it ignores this field.
    :param query_allow: when set, keep only these query parameters (matched case-insensitively against the decoded
        name), the ``w3lib.url.url_query_cleaner`` keep-list; a listed parameter survives even when it is a known
        tracker. Mutually exclusive with ``strict``, which is itself an allowlist.
    :param query_deny: always drop these query parameters (matched the same way), the ``url_query_cleaner``
        ``remove=True`` mode; the tracker or ``strict`` filtering still applies to the rest.
    """

    strict: bool = False
    trailing_slash: bool = True
    strip_fragment: bool = False
    language: str | None = None
    query_allow: frozenset[str] | None = None
    query_deny: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Reject a non-ISO-639-1 language and the contradiction of two query allowlists at once."""
        if self.language is not None and self.language not in _ISO_639_1:
            msg = f"language must be an ISO 639-1 code, got {self.language!r}"
            raise ValueError(msg)
        if self.strict and self.query_allow is not None:
            msg = "strict and query_allow are mutually exclusive, each is a query-parameter allowlist"
            raise ValueError(msg)

    @classmethod
    def w3lib(cls) -> UrlCleaning:
        """Return ``w3lib.url.canonicalize_url``'s mode: fragments dropped, every non-tracker parameter kept."""
        return cls(strip_fragment=True)


_DEFAULT: Final = UrlCleaning()


def clean_url(url: str, options: UrlCleaning | None = None, /) -> str | None:
    """
    Scrub a URL scraped from markup and normalize it, or return ``None`` when nothing usable remains.

    The scrub recovers from HTML transport damage: it strips the surrounding whitespace and control characters the
    WHATWG basic URL parser removes (spec 4.4 steps 1-2, extended to embedded spaces since those only appear in
    scraped junk), unwraps ``<![CDATA[...]]>``, truncates at a stray ``<``/``>``/``"`` delimiter, and undoes a
    leftover ``&amp;`` escape. The survivor must be an ``http``/``https`` URL with a plausible host and pass the
    ``language`` filter, then it is returned through :func:`normalize_url`.

    :param url: the URL as found in the wild.
    :param options: the cleaning options; defaults to :class:`UrlCleaning` (drop trackers, keep slash and fragment).
    :returns: the cleaned, normalized URL, or ``None`` for anything that is not a fetchable web URL.
    :raises TypeError: if ``url`` is not a ``str``.
    """
    active = options or _DEFAULT
    return _url_clean(url, *_knobs(active), active.language, _ISO_639_1)


def normalize_url(url: str, options: UrlCleaning | None = None, /) -> str:
    """
    Return the canonical form of a URL, so that two spellings of the same resource compare equal.

    The spec-defined part lowercases the scheme and host, converts a Unicode host to its ASCII (punycode) form the
    way the URL standard's host parser does (spec 3.5; browsers serialize ``münchen.de`` as ``xn--mnchen-3ya.de``),
    drops a default port (port state), resolves ``.``/``..``/``%2e`` path segments (path state), percent-encodes what
    the path/query/fragment percent-encode sets require -- with uppercase hex digits, leaving existing escapes alone
    -- and serializes an empty special-URL path as ``/`` (URL serializing). Beyond the spec, query parameters are
    sorted and known tracking parameters dropped (strict mode instead keeps only the content-identifying allowlist),
    and a fragment shaped like a query string is scrubbed the same way. Unlike ``courlan``, repeated slashes are kept
    (the spec preserves them) and punycode is the output form, not the input form.

    :param url: an absolute or relative URL; a relative one keeps its shape, only its components are normalized.
    :param options: the cleaning options; defaults to :class:`UrlCleaning` (drop trackers, keep slash and fragment).
    :returns: the normalized URL.
    :raises TypeError: if ``url`` is not a ``str``.
    :raises ValueError: if the URL cannot be split into components (e.g. an unclosed IPv6 bracket) or carries a
        character that cannot be percent-encoded (a lone surrogate).
    """
    return _url_normalize(url, *_knobs(options or _DEFAULT))


def extract_links(
    html: str,
    base_url: str | None = None,
    options: UrlCleaning | None = None,
    /,
    *,
    external_only: bool = False,
) -> set[str]:
    """
    Collect the cleaned page links of an HTML document, the ``courlan.extract_links`` counterpart.

    The document is parsed with the WHATWG tree builder, so links are read from the real DOM rather than regex
    matches. An anchor (``<a>``/``<area>``) contributes its ``href`` unless its ``rel`` carries ``nofollow`` or, with
    a ``language`` filter active, its ``hreflang`` names another language (``x-default`` passes). Each candidate is
    resolved against the document base URL (a ``<base href>`` wins over ``base_url``, per HTML spec 4.2.3), cleaned
    through :func:`clean_url`, and deduplicated across trivial variants (the ``http``/``https`` twin and the
    trailing-slash twin), the first occurrence in document order winning.

    :param html: the page markup.
    :param base_url: the URL the page was fetched from; relative links resolve against it, and it anchors the
        external/internal split. Without it relative links are dropped, since they cannot be made absolute.
    :param options: the cleaning options; defaults to :class:`UrlCleaning` (drop trackers, keep slash and fragment).
    :param external_only: keep only links leaving ``base_url``'s site, where the site boundary is the registrable
        domain (the public suffix plus one label, ``spam.example.co.uk`` and ``example.co.uk`` counting as one site);
        the eTLD+1 is read from the shipped IANA and Public Suffix List tables, so sibling subdomains stay internal.
    :returns: the surviving absolute URLs.
    :raises ValueError: if ``external_only`` is set without a ``base_url`` to define what external means.
    """
    active = options or _DEFAULT
    return parse(html)._extract_links(  # ruff:ignore[private-member-access]  # the Document type's private C entry point
        base_url, external_only, *_knobs(active), active.language, _ISO_639_1
    )


def _knobs(
    options: UrlCleaning,
) -> tuple[bool, bool, bool, frozenset[str] | None, frozenset[str], frozenset[str], frozenset[str]]:
    """Return the option fields and vocabularies every C entry point takes, in its argument order."""
    return (
        options.strict,
        options.trailing_slash,
        options.strip_fragment,
        options.query_allow,
        options.query_deny,
        _CONTENT_PARAMS,
        _LANGUAGE_PARAMS,
    )
