"""
URL cleaning, normalization, and page-level link extraction for :mod:`turbohtml.extract`.

The normalization core follows the `WHATWG URL standard <https://url.spec.whatwg.org/>`__ wherever it defines the
behavior: input stripping (basic URL parser, spec 4.4), scheme lowercasing (scheme state), host lowercasing and
domain-to-ASCII (host parsing, spec 3.5), default-port removal (port state), dot-segment resolution including the
``%2e`` forms (path state), the empty-path-to-``/`` serialization of special URLs (URL serializing, spec 4.5), and
the path/query/fragment percent-encode sets (percent-encoded bytes, spec 1.3). On top of that sits the crawl-oriented
canonicalization ``courlan`` and ``w3lib`` users expect -- query-parameter sorting, tracking-parameter removal, an
optional strict parameter allowlist, and a URL-based language filter -- each documented where it goes beyond the spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from ._html import parse

if TYPE_CHECKING:
    from urllib.parse import SplitResult

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

# Names compiled from the public query-stripping lists (Firefox's query-stripping records, the ClearURLs rules, and
# AdGuard's TrackParamFilter), the same sources courlan cites.
_TRACKER_NAMES: Final[frozenset[str]] = frozenset({
    "clickid",
    "dclid",
    "efid",
    "epik",
    "fb_ref",
    "fb_source",
    "fbclid",
    "gbraid",
    "gclid",
    "gclsrc",
    "igsh",
    "igshid",
    "mkt_tok",
    "msclkid",
    "partnerid",
    "s_cid",
    "sc_cid",
    "ttclid",
    "twclid",
    "wbraid",
    "wickedid",
    "yclid",
    "ysclid",
})
_TRACKER_PREFIXES: Final = ("ad_", "ads_", "ga_", "gs_", "hsa_", "itm_", "mc_", "mtm_", "oly_", "pk_", "utm_", "vero_")
_TRACKER_WORDS: Final = re.compile(
    r"(?:^|_)(?:aff(?:i(?:liate)?)?|campaign|cl?id|keyword|kwd|medium|refer(?:r?er)?|ref|session|source|uid|xtor)(?:_|$)"
)

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

# The characters each URL component may carry raw, complementing the WHATWG percent-encode sets (spec 1.3): the path
# set adds # ? { } to the fragment set (C0 controls, space, ", <, >, `), and the special-scheme query set adds '.
# Each safe string pairs with a search pattern for its complement, so the common already-encoded component skips
# urllib's per-character quote loop after one C regex scan.
_PATH_SAFE: Final = "!$%&'()*+,-./:;=@[\\]^_|~"
_PATH_UNSAFE: Final = re.compile(r"[^!$%&'()*+,\-./0-9:;=@A-Z\[\\\]\^_a-z|~]")
_QUERY_SAFE: Final = "!$%&()*+,-./:;=?@[\\]^_`{|}~"
_QUERY_UNSAFE: Final = re.compile(r"[^!$%&()*+,\-./0-9:;=?@A-Z\[\\\]\^_`a-z{|}~]")
_FRAGMENT_SAFE: Final = "!#$%&'()*+,-./:;=?@[\\]^_{|}~"
_FRAGMENT_UNSAFE: Final = re.compile(r"[^!#$%&'()*+,\-./0-9:;=?@A-Z\[\\\]\^_a-z{|}~]")

_ESCAPE: Final = re.compile(r"%[0-9a-fA-F]{2}")
_MARKUP_DELIMITER: Final = re.compile(r'[<>"]')
_C0_AND_SPACE: Final = "".join(map(chr, range(0x21)))
_LANGUAGE_SEGMENT: Final = re.compile(r"([a-z]{2})(?:[-_][a-z]{2,3})?$")
_DEFAULT_PORTS: Final[dict[str, int]] = {"ftp": 21, "http": 80, "ws": 80, "https": 443, "wss": 443}
_WEB_SCHEMES: Final = ("http", "https")
_WEB_PREFIXES: Final = ("http://", "https://")


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
    """
    active = options or _DEFAULT
    scrubbed = _scrub(url)
    try:
        parts = urlsplit(scrubbed)
    except ValueError:
        return None
    host = parts.hostname or ""
    if parts.scheme not in _WEB_SCHEMES or not host or ("." not in host and ":" not in parts.netloc):
        return None
    if active.language is not None and not _language_matches(parts, active.language, strict=active.strict):
        return None
    return _normalize(parts, active)


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
    :raises ValueError: if the URL cannot be split into components (e.g. an unclosed IPv6 bracket).
    """
    return _normalize(urlsplit(url), options or _DEFAULT)


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
    :param external_only: keep only links leaving ``base_url``'s site, where the site boundary is the host with any
        ``www.`` prefix removed, subdomains counting as internal.
    :returns: the surviving absolute URLs.
    :raises ValueError: if ``external_only`` is set without a ``base_url`` to define what external means.
    """
    active = options or _DEFAULT
    if external_only and base_url is None:
        msg = "external_only requires a base_url to compare hosts against"
        raise ValueError(msg)
    site = _site_host(base_url) if base_url is not None else ""
    document = parse(html)
    base = document.base_url(base_url or "") or None  # honor a <base href>, the document base URL (HTML spec 4.2.3)
    found: set[str] = set()
    seen: set[str] = set()
    cleaned_of: dict[str, str | None] = {}  # pages repeat hrefs (navigation, pagination); clean each spelling once
    for link in document.links():
        if link.attribute != "href" or link.element.tag not in {"a", "area"}:
            continue
        attributes = link.element.attrs
        if _has_nofollow(attributes.get("rel")):
            continue
        if active.language is not None and not _hreflang_matches(attributes.get("hreflang"), active.language):
            continue
        if link.url in cleaned_of:
            cleaned = cleaned_of[link.url]
        else:
            candidate = link.url if base is None or link.url.startswith(_WEB_PREFIXES) else urljoin(base, link.url)
            cleaned = cleaned_of[link.url] = clean_url(candidate, active)
        if cleaned is None or (external_only and not _is_external(cleaned, site)):
            continue
        if (key := _variant_key(cleaned)) not in seen:
            seen.add(key)
            found.add(cleaned)
    return found


def _scrub(url: str) -> str:
    """Undo HTML transport damage: whitespace, a CDATA wrapper, markup delimiters, and leftover ``&amp;`` escapes."""
    remainder = "".join(url.strip(_C0_AND_SPACE).split())
    if remainder.startswith("<![CDATA["):
        remainder = remainder.removeprefix("<![CDATA[").removesuffix("]]>")
    remainder = _MARKUP_DELIMITER.split(remainder, maxsplit=1)[0].replace("&amp;", "&")
    return remainder.removesuffix("&") if remainder.endswith("/&") else remainder


def _language_matches(parts: SplitResult, language: str, *, strict: bool) -> bool:
    """
    Judge the URL's own language markers against the target language.

    Three URL-based heuristics (content-based detection is out of scope): a ``lang``/``language`` query parameter
    whose value does not start with the code, a leading path segment that is an ISO 639-1 tag of another language
    (``/de/``, ``/en-us/``), and -- in strict mode -- a two-letter language subdomain (``de.example.org``).
    """
    for pair in parts.query.split("&"):
        key, separator, value = pair.partition("=")
        if separator and unquote(key).lower() in _LANGUAGE_PARAMS:
            code = unquote(value).lower()
            if code and not code.startswith(language):
                return False
    leading = next((segment for segment in parts.path.lower().split("/") if segment), "")
    if (match := _LANGUAGE_SEGMENT.fullmatch(leading)) and match[1] in _ISO_639_1 and match[1] != language:
        return False
    if strict:
        label = (parts.hostname or "").partition(".")[0]
        if len(label) == 2 and label in _ISO_639_1 and label != language:
            return False
    return True


def _normalize(parts: SplitResult, options: UrlCleaning) -> str:
    """Rebuild the URL from spec-normalized components plus the beyond-spec query/fragment canonicalization."""
    scheme = parts.scheme.lower()
    netloc = _normalize_netloc(parts, scheme) if parts.netloc else ""
    path = _encode(parts.path, _PATH_UNSAFE, _PATH_SAFE)
    if netloc:
        path = _remove_dot_segments(path)
    query = _normalize_query(parts.query, options)
    if netloc and scheme in _WEB_SCHEMES and not path:
        path = "/"  # a special URL with a host never serializes an empty path (URL serializing, spec 4.5)
    if not options.trailing_slash and not query and path.endswith("/") and path != "/":
        path = path.rstrip("/")
    fragment = "" if options.strict or options.strip_fragment else _normalize_fragment(parts.fragment, options)
    return urlunsplit((scheme, netloc, path, query, fragment))


def _normalize_netloc(parts: SplitResult, scheme: str) -> str:
    """Lowercase the host into its ASCII form and drop an empty or scheme-default port, keeping userinfo verbatim."""
    userinfo, _, hostport = parts.netloc.rpartition("@")
    if hostport.startswith("["):
        closing = hostport.find("]") + 1
        host, port_suffix = hostport[:closing].lower(), hostport[closing:]
    else:
        name, _, port_digits = hostport.partition(":")
        host = _ascii_host(name)
        port_suffix = f":{port_digits}" if port_digits else ""
    if port_suffix[1:].isdigit():
        port = int(port_suffix[1:])
        port_suffix = "" if port == _DEFAULT_PORTS.get(scheme) else f":{port}"
    elif port_suffix == ":":
        port_suffix = ""  # an empty port serializes as no port at all (port state, spec 4.4)
    prefix = f"{userinfo}@" if userinfo else ""
    return f"{prefix}{host}{port_suffix}"


@lru_cache(maxsize=1024)  # a crawl resolves the same hosts over and over, and the IDNA codec is the pass's hot spot
def _ascii_host(host: str) -> str:
    """Lowercase a domain and convert a Unicode one to punycode (domain-to-ASCII, spec 3.5), best effort."""
    lowered = host.lower()
    if lowered.isascii():
        return lowered
    try:
        # Python's idna codec implements IDNA 2003 rather than the spec's UTS #46, close enough for canonical output
        return lowered.encode("idna").decode("ascii")
    except UnicodeError:
        return lowered


def _encode(text: str, unsafe: re.Pattern[str], safe: str) -> str:
    """Percent-encode a component's out-of-set characters, uppercasing escape hex, skipping already-clean input."""
    text = _uppercase_escapes(text)
    return quote(text, safe=safe) if unsafe.search(text) else text


def _uppercase_escapes(text: str) -> str:
    """Normalize percent-escape hex digits to uppercase, the canonical spelling (RFC 3986 6.2.2.1)."""
    return _ESCAPE.sub(lambda match: match[0].upper(), text) if "%" in text else text


def _remove_dot_segments(path: str) -> str:
    """Resolve ``.`` and ``..`` segments, including their ``%2e`` spellings, as the path state does (spec 4.4)."""
    if "." not in path and "%2E" not in path:
        return path
    output: list[str] = []
    dotted = ""
    for segment in path.split("/"):
        dotted = segment.replace("%2E", ".")
        if dotted == ".":
            continue
        if dotted == "..":
            if len(output) > 1:
                output.pop()
        else:
            output.append(segment)
    if dotted in {".", ".."}:
        output.append("")
    return "/".join(output)


def _normalize_query(query: str, options: UrlCleaning) -> str:
    """Drop denied, tracker, or non-allowlisted parameters and sort the rest, keeping each pair's raw encoding."""
    allow = {name.lower() for name in options.query_allow} if options.query_allow is not None else None
    deny = {name.lower() for name in options.query_deny}
    kept: list[tuple[str, str]] = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = unquote(pair.partition("=")[0]).lower()
        if key in deny:
            continue
        if allow is not None:
            dropped = key not in allow
        elif options.strict:
            dropped = key not in _CONTENT_PARAMS and key not in _LANGUAGE_PARAMS
        else:
            dropped = _is_tracker(key)
        if dropped:
            continue
        kept.append((key, _encode(pair, _QUERY_UNSAFE, _QUERY_SAFE)))
    return "&".join(pair for _key, pair in sorted(kept))


def _is_tracker(key: str) -> bool:
    """Match a query-parameter name against the compiled tracking-parameter vocabulary."""
    return (
        key in _TRACKER_NAMES
        or key.startswith(_TRACKER_PREFIXES)
        or key.endswith("clid")
        or _TRACKER_WORDS.search(key) is not None
    )


def _normalize_fragment(fragment: str, options: UrlCleaning) -> str:
    """Percent-encode the fragment, first scrubbing trackers from one shaped like a query string."""
    if "=" in fragment:
        if "&" in fragment:
            return _normalize_query(fragment, options)
        if _is_tracker(unquote(fragment.partition("=")[0]).lower()):
            return ""
    return _encode(fragment, _FRAGMENT_UNSAFE, _FRAGMENT_SAFE)


def _has_nofollow(rel: str | list[str] | None) -> bool:
    """Whether a ``rel`` attribute value carries the ``nofollow`` token."""
    tokens = rel.lower().split() if isinstance(rel, str) else [token.lower() for token in rel or []]
    return "nofollow" in tokens


def _hreflang_matches(hreflang: str | list[str] | None, language: str) -> bool:
    """Whether an anchor's ``hreflang`` names the target language; a missing value or ``x-default`` always does."""
    if not isinstance(hreflang, str) or not hreflang:
        return True
    code = hreflang.lower()
    return code == "x-default" or code.partition("-")[0] == language


def _site_host(url: str) -> str:
    """Return the host that defines the site boundary: lowercased, without a ``www.`` prefix."""
    return ((urlsplit(url).hostname or "").removeprefix("www.")).lower()


def _is_external(url: str, site: str) -> bool:
    """Whether the URL's host falls outside the site, subdomains counting as inside (no public-suffix registry)."""
    host = _site_host(url)
    return not (host == site or host.endswith(f".{site}") or site.endswith(f".{host}"))


def _variant_key(url: str) -> str:
    """Collapse the scheme and a bare trailing slash so ``http``/``https`` and slash twins deduplicate."""
    remainder = url.partition("://")[2]
    return remainder if "?" in remainder or "#" in remainder else remainder.rstrip("/")
