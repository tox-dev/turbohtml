"""
Turn URLs, email addresses and phone numbers in HTML into links, the way bleach.linkify did.

bleach is the only library that shipped an HTML-aware linkifier, and it is end of life, so this is its replacement.
The typed layer compiles the options and parses the input with turbohtml's WHATWG tree builder. The C core snapshots
eligible text and anchors, scans each text run, invokes callbacks in document order, and mutates the tree. Text inside
an existing ``<a>``, a raw-text element (``<script>``/``<style>``), or a caller's ``skip_tags`` stays unchanged.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Final, TypeAlias, cast

from turbohtml._html import (
    _linkify_apply,
    _linkify_find,
    _linkify_has,
    _phone_config_compile,
    _phone_number_check,
    _phone_number_format,
    _phone_parse,
    parse_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from typing_extensions import Self

    from turbohtml._html import _PhoneConfig

_EMAIL_KIND: Final = 1

# The ``scheme://host`` schemes autolinked when a config registers none: the fixed set linkify-it recognizes, so a typo
# scheme or a ``javascript://`` payload stays plain text. A ``Linkify.schemes`` restricts to its own set (bleach), while
# a ``LinkDetector``'s ``schemes`` extends this one; the low-level scanner without an allowlist stays permissive.
_DEFAULT_URL_SCHEMES: Final = ("ftp", "http", "https")


class PhoneType(Enum):
    """The line type a numbering plan assigns a number; ``UNKNOWN`` appears only in possible mode."""

    FIXED_LINE = "fixed_line"
    MOBILE = "mobile"
    FIXED_LINE_OR_MOBILE = "fixed_line_or_mobile"
    TOLL_FREE = "toll_free"
    PREMIUM_RATE = "premium_rate"
    SHARED_COST = "shared_cost"
    VOIP = "voip"
    PERSONAL_NUMBER = "personal_number"
    PAGER = "pager"
    UAN = "uan"
    VOICEMAIL = "voicemail"
    UNKNOWN = "unknown"


class PhoneFormat(Enum):
    """The ways :meth:`PhoneNumber.format` writes a number."""

    E164 = "e164"
    """``+16502530000``: the calling code and national significant number, no separators and no extension."""
    INTERNATIONAL = "international"
    """``+1 650-253-0000``: the calling code, then the national number in its plan's grouping."""
    NATIONAL = "national"
    """``(650) 253-0000``: the national number as dialed within its region, national prefix included."""
    RFC3966 = "rfc3966"
    """``tel:+1-650-253-0000``: the RFC 3966 URI, hyphens between groups and ``;ext=`` for an extension."""


_PHONE_FORMATS: Final = (PhoneFormat.E164, PhoneFormat.INTERNATIONAL, PhoneFormat.NATIONAL, PhoneFormat.RFC3966)


class PhoneGrouping(Enum):
    """How closely the written digit groups must follow the number's format."""

    ANY = "any"
    """The groups may fall anywhere."""
    STRICT = "strict"
    """Each group of the number's format, or of an alternate format of its calling code, occurs in the text in order;
    ``415 6667777`` passes, ``41 566 67777`` does not."""
    EXACT = "exact"
    """The written groups are the format's groups, or the whole national number unbroken; ``415 6667777`` does not
    pass."""


_PHONE_GROUPINGS: Final = (PhoneGrouping.ANY, PhoneGrouping.STRICT, PhoneGrouping.EXACT)
# PhoneType members in the order the C recognizer numbers them (enum th_phone_type).
_PHONE_TYPES: Final = (
    PhoneType.FIXED_LINE,
    PhoneType.MOBILE,
    PhoneType.TOLL_FREE,
    PhoneType.PREMIUM_RATE,
    PhoneType.SHARED_COST,
    PhoneType.PERSONAL_NUMBER,
    PhoneType.VOIP,
    PhoneType.PAGER,
    PhoneType.UAN,
    PhoneType.VOICEMAIL,
    PhoneType.FIXED_LINE_OR_MOBILE,
    PhoneType.UNKNOWN,
)
_ALL_PHONE_TYPES: Final = 0x7FF
_MAX_E164_DIGITS: Final = 15

#: Words that mark the digits right after them as an identifier rather than a phone number.
DEFAULT_PHONE_LABELS: Final = (
    "account",
    "acct",
    "asin",
    "bic",
    "doi",
    "ean",
    "iban",
    "invoice",
    "isbn",
    "issn",
    "order",
    "postcode",
    "ref",
    "reference",
    "serial",
    "sku",
    "ticket",
    "tracking",
    "upc",
    "vat",
    "zip",
)


@dataclass(frozen=True, slots=True, init=False)
class PhoneNumbers:
    """
    Phone-number detection settings for :class:`Linkify` and :class:`LinkDetector`.

    ``regions`` are the ordered fallback regions for a number written without ``+`` (empty finds ``+`` numbers only);
    order decides which national reading wins, so any ordered iterable of ``str`` is accepted (a list, a tuple, a
    ``deque``, a generator, a caller's own iterable) and a bare string, a set or a mapping is a ``TypeError``, a
    runtime rule the ``Iterable[str]`` annotation cannot express. ``ignore_numbers_after`` are the words that mark
    the digits right after them as an identifier, not a number, as far as the groups joined without whitespace reach:
    ``Order 12345`` and ``Order 650-253-0000`` are skipped, the phone in ``Order 12345, 650-253-0000`` is still found,
    and ``Phone no. 650-253-0000`` links because ``no`` is not in the default list. Words are ASCII letters, compared
    case-folded against the word immediately before the digits and stored sorted; ``()`` disables the rule.

    :param regions: the ordered fallback regions, ISO 3166-1 alpha-2 codes (``"US"``).
    :param require_valid: link only numbers the region's numbering plan assigns; False links every number of a
        possible length, with type ``UNKNOWN`` and possibly no region.
    :param require_separators: a bare digit run with no ``+``, separators or international prefix is not a number.
    :param skip_card_numbers: a Luhn-valid payment-card shape is not a number.
    :param require_national_prefix: a number written without ``+`` must carry the national prefix its number format
        writes (``20 7946 0958`` is not a British number, ``020 7946 0958`` is); False links it the way people dial
        locally. Applies with ``require_valid``.
    :param grouping: how closely the written digit groups must follow the number's format. Needs ``require_valid``.
    :param types: link only numbers of these resolved types; ``None`` links every type. Needs ``require_valid``.
    :param ignore_numbers_after: words that mark the digits right after them as an identifier.
    """

    regions: tuple[str, ...]
    require_valid: bool
    require_separators: bool
    skip_card_numbers: bool
    require_national_prefix: bool
    grouping: PhoneGrouping
    types: frozenset[PhoneType] | None
    ignore_numbers_after: tuple[str, ...]
    _config: _PhoneConfig = field(init=False, repr=False, compare=False)

    def __init__(  # ruff:ignore[too-many-arguments]  # one keyword per setting, the dataclass field list
        self,
        *,
        regions: Iterable[str] = (),
        require_valid: bool = True,
        require_separators: bool = False,
        skip_card_numbers: bool = True,
        require_national_prefix: bool = True,
        grouping: PhoneGrouping = PhoneGrouping.ANY,
        types: Iterable[PhoneType] | None = None,
        ignore_numbers_after: Iterable[str] = DEFAULT_PHONE_LABELS,
    ) -> None:
        """Raise on any mistake here, never at scan time."""
        for name, flag in (
            ("require_valid", require_valid),
            ("require_separators", require_separators),
            ("skip_card_numbers", skip_card_numbers),
            ("require_national_prefix", require_national_prefix),
        ):
            if not isinstance(flag, bool):
                msg = f"{name} must be bool"
                raise TypeError(msg)
        if not isinstance(grouping, PhoneGrouping):
            msg = "grouping must be a PhoneGrouping"
            raise TypeError(msg)
        if grouping is not PhoneGrouping.ANY and not require_valid:
            msg = "grouping needs require_valid=True"
            raise ValueError(msg)
        wanted = None
        if types is not None:
            wanted = frozenset(types)
            if not wanted or any(not isinstance(member, PhoneType) for member in wanted):
                msg = "types must be a non-empty iterable of PhoneType members"
                raise TypeError(msg) if wanted else ValueError(msg)
            if PhoneType.UNKNOWN in wanted:
                msg = "types cannot include PhoneType.UNKNOWN; it is what possible mode reports"
                raise ValueError(msg)
            if not require_valid:
                msg = "types needs require_valid=True"
                raise ValueError(msg)
        # folding case only on ASCII: `ß` would otherwise fold to South Sudan's `SS`
        object.__setattr__(
            self,
            "regions",
            tuple(
                dict.fromkeys(
                    code.strip().upper() if code.isascii() else code for code in _ordered_strings(regions, "regions")
                )
            ),
        )
        object.__setattr__(self, "require_valid", require_valid)
        object.__setattr__(self, "require_separators", require_separators)
        object.__setattr__(self, "skip_card_numbers", skip_card_numbers)
        object.__setattr__(self, "require_national_prefix", require_national_prefix)
        object.__setattr__(self, "grouping", grouping)
        object.__setattr__(self, "types", wanted)
        object.__setattr__(
            self,
            "ignore_numbers_after",
            tuple(
                sorted({
                    word.strip().lower() for word in _ordered_strings(ignore_numbers_after, "ignore_numbers_after")
                })
            ),
        )
        # compiled once here, where a mistake raises, and handed to every scanner and parse that takes the settings
        object.__setattr__(self, "_config", _compile_settings(self, PhoneNumber))

    def __reduce__(self) -> tuple[Callable[[], PhoneNumbers], tuple[()]]:
        # the compiled configuration is a native object, so a copy or a pickle rebuilds from the settings
        return functools.partial(
            PhoneNumbers,
            regions=self.regions,
            require_valid=self.require_valid,
            require_separators=self.require_separators,
            skip_card_numbers=self.skip_card_numbers,
            require_national_prefix=self.require_national_prefix,
            grouping=self.grouping,
            types=self.types,
            ignore_numbers_after=self.ignore_numbers_after,
        ), ()


def _ordered_strings(values: Iterable[str], name: str) -> tuple[str, ...]:
    """Reject a bare string, a set or a mapping: it has no order to preserve."""
    if isinstance(values, (str, bytes, AbstractSet, Mapping)):
        msg = f"{name} must be an ordered iterable of str, not {type(values).__name__}"
        raise TypeError(msg)
    items = tuple(values)
    if any(not isinstance(item, str) for item in items):
        msg = f"{name} entries must be str"
        raise TypeError(msg)
    return items


@dataclass(frozen=True, slots=True)
class PhoneNumber:
    """
    A detected number, in the form the tables produced it.

    ``international_number`` is ``+`` followed by the country code and the national significant number with no
    separators and no extension, the E.164 layout and, when it fits E.164, the value the ``tel:`` href carries.
    ``e164`` is the same string when it fits ITU E.164's 15-digit limit and ``None`` otherwise: the pinned metadata
    declares longer valid national services (DE, ID, JP, KR, NG and UY, up to 19 digits with the country code), and
    those link with RFC 3966's local form, ``tel:200000000000000;phone-context=+49``, since a global ``tel:`` number
    must be E.164; an extension goes first among the parameters, as the RFC orders them.

    :param country_code: the calling code, ``1`` for ``+1``.
    :param national_number: the national significant number, ASCII digits with the leading zeros the plan keeps.
    :param extension: the extension digits, or ``None``.
    :param region: the region whose plan assigns the number (``"US"``, ``"001"`` for a non-geographic code), or
        ``None`` when possible mode could not tell.
    :param type: the resolved line type.
    """

    country_code: int
    national_number: str
    extension: str | None
    region: str | None
    type: PhoneType

    def __post_init__(self) -> None:
        """Reject a value the tables never produce."""
        if type(self.country_code) is not int or type(self.national_number) is not str:
            msg = "country_code must be int and national_number must be str"
            raise TypeError(msg)
        if (self.extension is not None and type(self.extension) is not str) or (
            self.region is not None and type(self.region) is not str
        ):
            msg = "extension and region must be str or None"
            raise TypeError(msg)
        if not isinstance(self.type, PhoneType):
            msg = "type must be a PhoneType"
            raise TypeError(msg)
        if self.extension is not None and not (
            0 < len(self.extension) <= 20 and self.extension.isascii() and self.extension.isdigit()
        ):
            msg = "extension must be 1-20 ASCII digits"
            raise ValueError(msg)
        _phone_number_check(self.country_code, self.national_number, self.region, _PHONE_TYPES.index(self.type))

    @classmethod
    def parse(cls, text: str, *, regions: Iterable[str] = (), require_valid: bool = True) -> Self:
        """
        Read a string that holds one phone number.

        The number starts at the first ``+`` or digit, so a ``tel:`` scheme or a word before it (``Tel: 650-253-0000``)
        is skipped; characters that are neither digits, letters nor ``#`` are dropped from the end, and a second
        number after ``/x`` is cut off. The remainder must be digits, separators and ASCII letters, with an extension
        in any written form at the end, the auto-dialling ``650-253-0000,,1234`` and ``650-253-0000;1234`` included.
        Three or more letters make a vanity number whose letters are keypad digits (``1-800-FLOWERS``); fewer are
        dropped. An RFC 3966 local number reads through its ``phone-context`` (``tel:2530000;phone-context=+1650``),
        and ``;isub=`` ends the number. A number written without ``+`` is read with each of ``regions`` in turn, and
        unlike detection it needs neither separators nor the national prefix its format writes, and a payment-card
        shape is not refused. A string over 250 characters is no number.

        :param text: the text holding the number.
        :param regions: the ordered fallback regions for a number written without ``+``.
        :param require_valid: the number must be one the plan assigns; False accepts any possible length.
        :returns: the number.
        :raises ValueError: when the text does not hold one such number.
        """
        if not isinstance(text, str):
            msg = "text must be str"
            raise TypeError(msg)
        if (
            number := _phone_parse(
                _parse_config(cls, _ordered_strings(regions, "regions"), require_valid=require_valid), text
            )
        ) is None:
            msg = f"{text!r} is not a phone number"
            raise ValueError(msg)
        return cast("Self", number)  # the native factory built cls, which the binding's annotation cannot say

    @classmethod
    def _from_native(cls, *fields: object) -> PhoneNumber:
        # the recognizer resolved (country_code, national_number, extension, region, type) from the same tables the
        # check would consult, so the frozen instance is filled without it
        number = object.__new__(cls)
        for name, value in zip(("country_code", "national_number", "extension", "region", "type"), fields, strict=True):
            object.__setattr__(number, name, value)  # ruff:ignore[unnecessary-dunder-call]  # bypasses the frozen guard
        return number

    @property
    def international_number(self) -> str:
        """The ``+`` number with no separators, what the ``tel:`` href carries."""
        return f"+{self.country_code}{self.national_number}"

    @property
    def e164(self) -> str | None:
        """The international number when it fits E.164's fifteen digits, else ``None``."""
        number = self.international_number
        return number if len(number) - 1 <= _MAX_E164_DIGITS else None

    def format(self, style: PhoneFormat = PhoneFormat.INTERNATIONAL) -> str:
        """
        Write the number in one of the four layouts.

        A number past E.164's fifteen digits takes RFC 3966's local form (``tel:200000000000000;phone-context=+49``)
        in :attr:`PhoneFormat.RFC3966`, since a global ``tel:`` number must be E.164.

        The grouping comes from the number formats of the calling code's main region (``+1`` numbers group the
        North American way whatever their region), chosen by the number's leading digits and length; a number no
        format covers is written as its bare national significant number. An extension follows the region's
        preferred marker (``ext.`` by default, ``;ext=`` for :attr:`PhoneFormat.RFC3966`) and is left out of
        :attr:`PhoneFormat.E164`.

        :param style: the layout to write.
        :returns: the formatted number, ASCII.
        """
        if not isinstance(style, PhoneFormat):
            msg = "style must be a PhoneFormat"
            raise TypeError(msg)
        return _phone_number_format(
            self.country_code, self.national_number, self.extension, _PHONE_FORMATS.index(style)
        )


@functools.lru_cache(maxsize=32)
def _parse_config(number_type: type[PhoneNumber], regions: tuple[str, ...], *, require_valid: bool) -> _PhoneConfig:
    # keyed on the raw regions so a repeated parse call pays a cache lookup, not a settings object and its compile
    return _compile_settings(
        PhoneNumbers(
            regions=regions,
            require_valid=require_valid,
            skip_card_numbers=False,
            require_national_prefix=False,
            ignore_numbers_after=(),
        ),
        number_type,
        parsing=True,
    )


def _compile_settings(phones: PhoneNumbers, number_type: type[PhoneNumber], *, parsing: bool = False) -> _PhoneConfig:
    return _phone_config_compile((
        phones.regions,
        phones.require_valid,
        phones.require_separators,
        phones.skip_card_numbers,
        phones.require_national_prefix,
        _PHONE_GROUPINGS.index(phones.grouping),
        _ALL_PHONE_TYPES if phones.types is None else sum(1 << _PHONE_TYPES.index(member) for member in phones.types),
        phones.ignore_numbers_after,
        number_type,
        _PHONE_TYPES,
        parsing,
    ))


class LinkCandidate:
    """
    A link handed to each callback to mutate or veto.

    A callback returns the link to keep it, or ``None`` to drop the anchor: a detected link stays plain text, an
    existing one is unwrapped to its contents.

    :param url: the link's ``href``.
    :param text: the visible link text the reader sees.
    :param attrs: extra attributes to put on the ``<a>`` (``rel``, ``target``, ``class``, ...).
    :param existing: True when reprocessing an ``<a>`` already in the input, False for a freshly detected link.
    :param phone: the detected number when the link is a phone number found in plain text; ``None`` for a URL, an
        email, and every existing anchor whatever its ``href``.
    """

    __slots__ = ("attrs", "existing", "phone", "text", "url")

    def __init__(
        self,
        url: str,
        text: str,
        attrs: dict[str, str] | None = None,
        *,
        existing: bool = False,
        phone: PhoneNumber | None = None,
    ) -> None:
        """Create a link."""
        self.url = url
        self.text = text
        self.attrs = attrs if attrs is not None else {}
        self.existing = existing
        self.phone = phone


# A callback receives the generated :class:`LinkCandidate` and returns it to keep the link, or ``None`` to leave the
# text bare.
Callback: TypeAlias = "Callable[[LinkCandidate], LinkCandidate | None]"


def _is_web_url(url: str) -> bool:
    """Is this an ``http``/``https`` URL? The scheme is matched case-insensitively, so ``HTTP://`` counts."""
    return url[:6].lower().startswith(("http:", "https:"))


def nofollow(link: LinkCandidate) -> LinkCandidate | None:
    """
    Add ``rel="nofollow"`` to a web link so search engines skip it, leaving ``mailto:`` and other links alone.

    :param link: the link to adjust.
    :returns: the link, with ``nofollow`` added when it is a web link.
    """
    if _is_web_url(link.url):
        rels = link.attrs.get("rel", "").split()
        if "nofollow" not in rels:
            rels.append("nofollow")
        link.attrs["rel"] = " ".join(rels)
    return link


def target_blank(link: LinkCandidate) -> LinkCandidate | None:
    """
    Open a web link in a new tab, stripping a stale ``target`` from a non-web link so it cannot leak through.

    :param link: the link to adjust.
    :returns: the link, with ``target`` set on a web link or cleared on a non-web link.
    """
    if _is_web_url(link.url):
        link.attrs["target"] = "_blank"
    else:
        link.attrs.pop("target", None)
    return link


#: The callbacks linkify applies when a caller passes none, matching bleach's default.
DEFAULT_CALLBACKS: Final = (nofollow,)


@dataclass(frozen=True)
class Linkify:
    """
    An immutable, thread-safe description of how :func:`linkify` and :class:`Linker` find and rewrite links.

    Build one and reuse it across threads.

    :param callbacks: callables run on each detected link to adjust or veto it (defaults to ``DEFAULT_CALLBACKS``).
    :param skip_tags: tags whose text is left untouched, such as ``pre`` and ``code``.
    :param parse_email: also autolink bare email addresses as ``mailto:`` links.
    :param process_existing: run the callbacks over ``<a>`` tags already present, not only freshly detected links.
    :param extra_tlds: top-level domains that make a bare domain a link, on top of the built-in IANA table.
    :param schemes: the exact set of ``scheme://`` URL schemes that autolink; ``None`` keeps the built-in
        ``http``/``https``/``ftp`` default, so a typo scheme or a ``javascript://`` payload stays plain text. A bare
        domain is always treated as ``http`` and is governed by the TLD table, not ``schemes``.
    :param phones: also link phone numbers as ``tel:`` links, per these :class:`PhoneNumbers` settings; ``None``
        leaves digits alone. A ``tel:`` URI already written in the text links as itself.
    """

    callbacks: Iterable[Callback] = DEFAULT_CALLBACKS
    skip_tags: Iterable[str] | None = None
    parse_email: bool = False
    process_existing: bool = False
    extra_tlds: Iterable[str] | None = None
    schemes: Iterable[str] | None = None
    phones: PhoneNumbers | None = None


class Linker:
    """
    A reusable linkifier; build it once from a :class:`Linkify` configuration and call :meth:`linkify` per document.

    :param options: the configuration to apply; None uses ``DEFAULT_CALLBACKS`` and detects nothing else.
    """

    def __init__(self, options: Linkify | None = None) -> None:
        """Compile a configuration into the form the walk consumes."""
        config = options if options is not None else Linkify()
        self.callbacks = tuple(config.callbacks)
        self.skip_tags = (
            tuple(sorted({tag.lower() for tag in config.skip_tags})) if config.skip_tags is not None else ()
        )
        self.parse_email = config.parse_email
        self.process_existing = config.process_existing
        self.extra_tlds = tuple(sorted({tld.lower() for tld in config.extra_tlds})) if config.extra_tlds else ()
        self.url_schemes = (
            tuple(sorted({scheme.lower() for scheme in config.schemes}))
            if config.schemes is not None
            else _DEFAULT_URL_SCHEMES
        )
        self.phones = config.phones
        self._phone_config = _compile_phones(config.phones)

    def linkify(self, text: str) -> str:
        """
        Linkify HTML, leaving everything but eligible text runs untouched.

        :param text: the HTML to linkify.
        :returns: the linkified HTML.
        """
        root = parse_fragment(text)
        _linkify_apply(
            root,
            self.callbacks,
            self.parse_email,
            self.extra_tlds,
            self.url_schemes,
            self.process_existing,
            self.skip_tags,
            LinkCandidate,
            self._phone_config,
        )
        return root.inner_html


def linkify(text: str, options: Linkify | None = None) -> str:
    """
    Find URLs, email addresses and phone numbers in HTML and wrap them in ``<a>`` links.

    Existing markup is left untouched.

    :param text: the HTML to linkify.
    :param options: the configuration to apply; None uses ``DEFAULT_CALLBACKS`` and detects nothing else.
    :returns: the linkified HTML.
    """
    return Linker(options).linkify(text)


class LinkSpan:
    """
    One URL, email address or phone number found in a run of plain text.

    :param start: the half-open start offset of the match in the scanned text.
    :param end: the half-open end offset of the match in the scanned text.
    :param text: the matched substring exactly as it appeared.
    :param url: the normalized ``href`` (``mailto:`` for an email, ``http://`` for a bare domain, the number's own
        ``tel:`` URI for a phone number and for a written ``tel:`` URI, the text itself for a ``scheme://`` or
        registered scheme-less URL).
    :param is_email: whether the match is an email address.
    :param phone: the detected number when the match is a phone number, else ``None``.
    """

    __slots__ = ("end", "is_email", "phone", "start", "text", "url")

    def __init__(  # ruff:ignore[too-many-arguments]  # the five fields are the span's positional contract
        self,
        start: int,
        end: int,
        text: str,
        url: str,
        is_email: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # the pre-existing positional contract
        *,
        phone: PhoneNumber | None = None,
    ) -> None:
        """Create a link span."""
        self.start = start
        self.end = end
        self.text = text
        self.url = url
        self.is_email = is_email
        self.phone = phone

    def __repr__(self) -> str:
        """Render the span with its offsets and url, the way a debugger or a failing test wants to see it."""
        phone = f", phone={self.phone!r}" if self.phone is not None else ""
        return f"LinkSpan(start={self.start}, end={self.end}, text={self.text!r}, url={self.url!r}{phone})"

    def __eq__(self, other: object) -> bool:
        """Two spans are equal when every field matches; comparing to a non-span defers to the other operand."""
        if not isinstance(other, LinkSpan):
            return NotImplemented
        return (self.start, self.end, self.text, self.url, self.is_email, self.phone) == (
            other.start,
            other.end,
            other.text,
            other.url,
            other.is_email,
            other.phone,
        )

    __hash__ = None  # a span carries offsets into one specific string, so it is not a stable dict key


def _span_from_match(text: str, span: tuple[int, int, int, str, PhoneNumber | None]) -> LinkSpan:
    start, end, kind, url, phone = span
    return LinkSpan(start, end, text[start:end], url, kind == _EMAIL_KIND, phone=phone)


class LinkDetector:
    """
    Find the links in plain text, configured once and reused per call.

    Unlike :class:`Linker`, which rewrites HTML, a detector only *locates* links and hands back :class:`LinkSpan`
    objects, leaving the text untouched.

    :param emails: detect bare email addresses.
    :param bare_domains: detect bare domains (``example.com``) with no explicit scheme.
    :param tlds: custom top-level domains accepted for bare-domain matching, on top of the IANA table.
    :param schemes: extra schemes to detect, both as scheme-less opaque URLs (``tel:``, ``bitcoin:``) and as
        ``scheme://`` authority URLs, on top of the built-in ``http``/``https``/``ftp`` set; an unregistered scheme
        such as ``javascript://`` or a typo like ``hppt://`` is not detected.
    :param phones: also detect phone numbers, per these :class:`PhoneNumbers` settings; ``None`` leaves digits
        alone. With phones on, a written ``tel:`` or ``tel://`` URI whose payload reads as a number is a phone link
        too: the span covers the URI as written and carries the number, whose own ``tel:`` URI is the ``url``.
    """

    def __init__(
        self,
        *,
        emails: bool = True,
        bare_domains: bool = True,
        tlds: Iterable[str] = (),
        schemes: Iterable[str] = (),
        phones: PhoneNumbers | None = None,
    ) -> None:
        """Build a reusable detector."""
        self.emails = emails
        self.bare_domains = bare_domains
        self.phones = phones
        self._phone_config = _compile_phones(phones)
        self._tlds = tuple({tld.lower().removeprefix(".") for tld in tlds})
        self._registered = tuple(sorted({scheme.lower().rstrip(":") for scheme in schemes}))
        self._schemes = self._registered if phones is None else tuple(sorted({*self._registered, "tel"}))
        self._url_schemes = tuple(sorted(set(_DEFAULT_URL_SCHEMES).union(self._registered)))

    def __reduce__(self) -> tuple[Callable[[], LinkDetector], tuple[()]]:
        # the compiled phone configuration is a native object, so a copy or a pickle rebuilds from the settings
        return functools.partial(
            LinkDetector,
            emails=self.emails,
            bare_domains=self.bare_domains,
            tlds=self._tlds,
            schemes=self._registered,
            phones=self.phones,
        ), ()

    def find(self, text: str) -> list[LinkSpan]:
        """
        Find every link in a run of text.

        :param text: the text to scan.
        :returns: every link as a :class:`LinkSpan`, in the order it appears.
        """
        return [
            _span_from_match(text, span)
            for span in _linkify_find(
                text, self.emails, self.bare_domains, self._tlds, self._schemes, self._url_schemes, self._phone_config
            )
        ]

    def has_link(self, text: str) -> bool:
        """
        Test a run of text for any link, cheaper than :meth:`find` when only presence matters.

        :param text: the text to scan.
        :returns: whether the text contains at least one link.
        """
        return _linkify_has(
            text, self.emails, self.bare_domains, self._tlds, self._schemes, self._url_schemes, self._phone_config
        )


def _compile_phones(phones: PhoneNumbers | None) -> _PhoneConfig | None:
    if phones is None:
        return None
    if not isinstance(phones, PhoneNumbers):
        msg = "phones must be PhoneNumbers or None"
        raise TypeError(msg)
    return phones._config  # ruff:ignore[private-member-access]  # the settings compiled themselves


__all__ = [
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
    "nofollow",
    "target_blank",
]
