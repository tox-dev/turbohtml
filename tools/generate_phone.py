"""
Generate src/turbohtml/_c/data/phone_table.h from libphonenumber's numbering-plan metadata.

The phone recognizer behind ``turbohtml.clean.linkify`` validates candidates against every numbering plan without a
regex engine: each plan's patterns are compiled here, at generation time, into the automata ``phone_dfa`` builds, and
the C walks tables. The source of truth is Google's ``PhoneNumberMetadata.xml`` at a pinned release tag, plus the
``PhoneNumberUtil.java`` of the same tag for the extension grammar and the punctuation set (each copied constant must
occur verbatim in the fetched file, so a tag bump without a grammar review fails), plus the Unicode Character Database
files that define which code points are digits, letters, currency symbols and case-equivalent. Every fetched file is
pinned by SHA-256 as well as by version, so a rebuild is deterministic and a poisoned mirror cannot land a table.

The Python port ``phonenumbers`` is the conformance oracle, so the metadata tag follows the port's latest release.

Usage:  python tools/generate_phone.py src/turbohtml/_c/data/phone_table.h [--sources DIR]
"""

from __future__ import annotations

import hashlib
import random
import re
import string
import sys
import xml.etree.ElementTree as ET  # ruff:ignore[suspicious-xml-etree-import]  # the metadata is pinned by SHA-256, never untrusted
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from httpfetch import fetch_bytes
from phone_dfa import (
    ALL_DIGITS,
    ASSERT_END,
    CHAR,
    DIGIT_SYMBOLS,
    MATCH,
    SAVE,
    SPLIT,
    Dfa,
    PriorityDfa,
    Program,
    SreItems,
    accepted_lengths,
    compile_dfa,
    compile_priority_dfa,
    compile_program,
    lag,
    longest_accept,
    match_end,
    max_threads,
    pike_spans,
    shortest_accept,
    sre_constants,
    sre_parse,
    sticky_program,
    union_program,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

# The metadata pin and the python-phonenumbers conformance submodule are one number, bumped together: the port's
# latest release decides, never the other way round. Each digest is the SHA-256 of the exact bytes the pinned URL
# serves; a rebuild recomputes it and aborts on a mismatch.
LIBPHONENUMBER_TAG: Final = "v9.0.38"
_UNICODE_VERSION: Final = "16.0.0"

_UPSTREAM: Final = f"https://raw.githubusercontent.com/google/libphonenumber/{LIBPHONENUMBER_TAG}"
_UCD: Final = f"https://www.unicode.org/Public/{_UNICODE_VERSION}/ucd"
SOURCES: Final = {
    "PhoneNumberMetadata.xml": (
        f"{_UPSTREAM}/resources/PhoneNumberMetadata.xml",
        "505eb93659bb6cc7daff90576c1db3d7cfca6591b0038f2f3fcc187e7ea7ea35",
    ),
    "PhoneNumberAlternateFormats.xml": (
        f"{_UPSTREAM}/resources/PhoneNumberAlternateFormats.xml",
        "3cfdc3ed6ac674214aa8fc2409fcb14632b0ddcb6a5834612bcb1aefe657cbd5",
    ),
    "PhoneNumberUtil.java": (
        f"{_UPSTREAM}/java/libphonenumber/src/com/google/i18n/phonenumbers/PhoneNumberUtil.java",
        "580eb2da64567319fac9bc8e288ccf6a2561a72a47d44fe4a905c3a07cafcb17",
    ),
    "Blocks.txt": (f"{_UCD}/Blocks.txt", "f3907b395d410f1b97342292ca6bc83dd12eb4b205f2a0c48efdef99e517d7b0"),
    "UnicodeData.txt": (f"{_UCD}/UnicodeData.txt", "ff58e5823bd095166564a006e47d111130813dcf8bf234ef79fa51a870edb48f"),
}

# The type descriptions in libphonenumber's order; the bit of each is its index, generalDesc is bit 10.
TYPES: Final = (
    "fixedLine",
    "mobile",
    "tollFree",
    "premiumRate",
    "sharedCost",
    "personalNumber",
    "voip",
    "pager",
    "uan",
    "voicemail",
)
GENERAL_BIT: Final = 1 << len(TYPES)
MAX_NSN: Final = 17
MIN_NSN: Final = 2
_MAX_ROUTER_REPLAY: Final = 8
_MAX_FORMAT_GROUPS: Final = 6
_MAX_TEMPLATE_CHARS: Final = 24
MAX_TABLE_BYTES: Final = 400 * 1024

# Copied from PhoneNumberUtil.java at the pinned tag; ``check_java_constants`` proves each still occurs there verbatim.
JAVA_CONSTANTS: Final = {
    "VALID_PUNCTUATION": (
        '"-x\\u2010-\\u2015\\u2212\\u30FC\\uFF0D-\\uFF0F "',
        '"\\u00A0\\u00AD\\u200B\\u2060\\u3000()\\uFF08\\uFF09\\uFF3B\\uFF3D.\\\\[\\\\]/~\\u2053\\u223C\\uFF5E"',
    ),
    "DIGITS": ('"\\\\p{Nd}"',),
    "PLUS_CHARS": ('"+\\uFF0B"',),
    "SECOND_NUMBER_START": ('"[\\\\\\\\/] *x"',),
    "MAX_INPUT_STRING_LENGTH": ("MAX_INPUT_STRING_LENGTH = 250",),
    "MAX_LENGTH_FOR_NSN": ("MAX_LENGTH_FOR_NSN = 17",),
    "extension labels": (
        '"(?:e?xt(?:ensi(?:o\\u0301?|\\u00F3))?n?|\\uFF45?\\uFF58\\uFF54\\uFF4E?|\\u0434\\u043E\\u0431|anexo)"',
        '"(?:[x\\uFF58#\\uFF03~\\uFF5E]|int|\\uFF49\\uFF4E\\uFF54)"',
        '"[ \\u00A0\\\\t,]*"',
        '"[:\\\\.\\uFF0E]?[ \\u00A0\\\\t,-]*"',
        '"[ \\u00A0\\\\t]*"',
        '"(?:,{2}|;)"',
        '"(?:,)+"',
        "extLimitAfterExplicitLabel = 20",
        "extLimitAfterAmbiguousChar = 9",
        "extLimitWhenNotSure = 6",
        '"[- ]+"',
        'RFC3966_EXTN_PREFIX = ";ext="',
    ),
}

# The extension grammar of EXTN_PATTERNS_FOR_MATCHING in Python's dialect (\\p{Nd} is \\d, which the symbolizer maps to
# the DIGIT class); the four alternatives in Java's order, each with the digit cap it allows.
_SEP_BEFORE_LABEL: Final = "[ \u00a0\t,]*"
_AFTER_LABEL: Final = "[:.\uff0e]?[ \u00a0\t,-]*"
_EXPLICIT_LABELS: Final = "(?:e?xt(?:ensi(?:o\u0301?|\u00f3))?n?|\uff45?\uff58\uff54\uff4e?|\u0434\u043e\u0431|anexo)"
_AMBIGUOUS_LABELS: Final = "(?:[x\uff58#\uff03~\uff5e]|int|\uff49\uff4e\uff54)"
_EXTENSION_FORMS: Final = (
    ("rfc", ";ext=(\\d{1,20})", 20),
    ("explicit", f"{_SEP_BEFORE_LABEL}{_EXPLICIT_LABELS}{_AFTER_LABEL}(\\d{{1,20}})#?", 20),
    ("ambiguous", f"{_SEP_BEFORE_LABEL}{_AMBIGUOUS_LABELS}{_AFTER_LABEL}(\\d{{1,9}})#?", 9),
    ("american", "[- ]+(\\d{1,6})#", 6),
)
# createExtnPattern(forParsing = true) adds the auto-dialling forms a held string may carry, which the matcher never
# reads out of prose: `,,` or `;` before the digits, or one or more commas alone.
_SEP_BEFORE_AUTODIAL: Final = "[ \u00a0\t]*"
_PARSING_EXTENSION_FORMS: Final = (
    *_EXTENSION_FORMS,
    ("autodial", f"{_SEP_BEFORE_AUTODIAL}(?:,,|;){_AFTER_LABEL}(\\d{{1,15}})#?", 15),
    ("commas", f"{_SEP_BEFORE_AUTODIAL},+{_AFTER_LABEL}(\\d{{1,9}})#?", 9),
)

# Words that mark the digit run right after them as an identifier, not a phone number; sorted, lowercase, ASCII.
DEFAULT_LABELS: Final = (
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

# The six Java UnicodeBlocks PhoneNumberMatcher.isLatinLetter names, by their Blocks.txt names.
_LATIN_BLOCKS: Final = (
    "Basic Latin",
    "Latin-1 Supplement",
    "Latin Extended-A",
    "Latin Extended-B",
    "Latin Extended Additional",
    "Combining Diacritical Marks",
)

_WHITESPACE: Final = re.compile(r"\s+")
# PhoneNumberUtil.FIRST_GROUP_ONLY_PREFIX_PATTERN: a rule of just the first group needs no national prefix.
_FIRST_GROUP_ONLY: Final = re.compile(r"\(?\$1\)?")
# a numberFormat pattern is a run of capture groups over digits, each `\d`, `\d{n}` or `\d{n,m}`, so choosing it is
# a length test and formatNsn's replacement is a greedy split of the digits into those groups, no regex engine needed
_GROUP_RUN: Final = re.compile(r"(?:\(\\d(?:\{\d+(?:,\d+)?\})?\))+")
_GROUP_PART: Final = re.compile(r"\(\\d(?:\{(\d+)(?:,(\d+))?\})?\)")
_TEMPLATE_REF: Final = re.compile(r"\$(\d)")
_TEMPLATE_CHARS: Final = frozenset(" -.()0123456789")
_TRANSFORM_RULE: Final = re.compile(r"^(\d{0,4})\$(\d)$")
_LENGTH_ITEM: Final = re.compile(r"^(\d+)$|^\[(\d+)-(\d+)\]$")


class GenerationError(RuntimeError):
    """The pinned sources or the compiled tables violate an invariant the recognizer relies on."""


@dataclass
class TypeDesc:
    """One phone-number type's numberDesc: its pattern, and the lengths proven possible for it."""

    pattern: str
    national: frozenset[int]
    local_only: frozenset[int]


@dataclass
class Format:
    """
    One numberFormat as formatNsn and isNationalPrefixPresentIfRequired read it.

    ``groups`` are the digit-count bounds of the pattern's capture groups; ``national`` is the format template with
    the national prefix formatting rule applied to its first group, ``intl`` the international template, ``None``
    when intlFormat is ``NA`` and the format is absent from the international list.
    """

    leading: str | None
    pattern: str
    groups: tuple[tuple[int, int], ...]
    national: str
    intl: str | None
    requires_prefix: bool


@dataclass
class Region:
    """One territory's numbering-plan metadata, parsed from PhoneNumberMetadata.xml."""

    code: str
    country_code: int
    main: bool
    leading_digits: str | None
    national_prefix: str | None
    prefix_pattern: str | None
    transform: str | None
    idd_pattern: str | None
    general: str
    types: dict[str, TypeDesc]
    ext_prefix: str | None = None
    formats: list[Format] = field(default_factory=list)
    possible_national: frozenset[int] = frozenset()
    possible_local_only: frozenset[int] = frozenset()
    examples: dict[str, str] = field(default_factory=dict)


@dataclass
class _PrefixTag:
    """The national-prefix transform rule's literal and capture group, and which program captured it."""

    literal: str
    group: int
    program: int


@dataclass
class _FormatTables:
    """One numberFormat compiled: its leadingDigits automaton and the format it came from."""

    leading: Dfa
    format: Format


@dataclass
class RegionTables:
    """One region's compiled automata: plan, prefix, IDD and formats, plus the type labels and validity floors."""

    region: Region
    plan: Dfa
    labels: list[int]
    prefix: PriorityDfa | None
    prefix_program: Program | None
    tag: _PrefixTag | None
    idd: PriorityDfa | None
    floor_valid: int
    floor_possible: int
    formats: list[_FormatTables] = field(default_factory=list)


@dataclass
class Group:
    """One country-calling-code group: its member regions, the main region, and the inter-region router."""

    country_code: int
    members: list[int]
    main: int
    router: Dfa | None
    routed: list[bool]


@dataclass
class _UnicodeTables:
    """The Unicode ranges and case classes the recognizer consults: digits, Latin letters, currency symbols."""

    nd_ranges: list[tuple[int, int, int]]
    nd_pages: list[int]
    currency_ranges: list[tuple[int, int]]
    latin_ranges: list[tuple[int, int]]
    letter_ranges: list[tuple[int, int]]
    number_ranges: list[tuple[int, int]]
    case_classes: dict[int, frozenset[int]]


@dataclass
class _ExtensionTables:
    """The extension grammars compiled to DFAs over one private symbol alphabet, plus that alphabet's classes."""

    classes: list[tuple[int, int, int]]
    symbols: int
    dfa: Dfa
    parsing_dfa: Dfa
    symbol_of: dict[int, int]


@dataclass
class Tables:
    """Every compiled table the C recognizer reads: regions, groups, Unicode data and the extension grammar."""

    regions: list[RegionTables]
    groups: list[Group]
    group_of_code: dict[int, int]
    alternates: list[list[_FormatTables]]
    unicode: _UnicodeTables
    extension: _ExtensionTables
    max_lag: int
    max_prefix_digits: int
    max_router_replay: int
    nfa_threads: int
    nfa_slots: int


def fetch_sources(local: Path | None) -> dict[str, bytes]:
    """Fetch (or read from ``local``) every pinned source and verify each SHA-256."""
    sources: dict[str, bytes] = {}
    for name, (url, digest) in SOURCES.items():
        raw = (local / name).read_bytes() if local is not None else fetch_bytes(url)
        if (actual := hashlib.sha256(raw).hexdigest()) != digest:
            msg = f"{name} has sha256 {actual}, not the pinned {digest}; review the source, then bump the pin"
            raise GenerationError(msg)
        sources[name] = raw
    return sources


def check_java_constants(java: str) -> None:
    """Every constant copied from PhoneNumberUtil.java must still occur there verbatim at the pinned tag."""
    for name, literals in JAVA_CONSTANTS.items():
        for literal in literals:
            if literal not in java:
                msg = f"{name}: {literal!r} no longer occurs in PhoneNumberUtil.java at {LIBPHONENUMBER_TAG}"
                raise GenerationError(msg)


def parse_metadata(xml: bytes) -> list[Region]:
    """Read every territory, deriving the general possible lengths the way BuildMetadataFromXml does."""
    regions: list[Region] = []
    for territory in ET.fromstring(xml).iter("territory"):  # ruff:ignore[suspicious-xml-element-tree-usage]  # pinned by hash, not untrusted
        general = territory.find("generalDesc")
        if general is None or general.find("possibleLengths") is not None:
            msg = f"{territory.get('id')}: generalDesc must exist and carry no possibleLengths"
            raise GenerationError(msg)
        types: dict[str, TypeDesc] = {}
        examples: dict[str, str] = {}
        for name in TYPES:
            desc = territory.find(name)
            if desc is None:
                continue
            pattern = desc.find("nationalNumberPattern")
            lengths = desc.find("possibleLengths")
            if pattern is None or lengths is None:
                msg = f"{territory.get('id')}/{name}: a type needs a pattern and possibleLengths"
                raise GenerationError(msg)
            types[name] = TypeDesc(
                _WHITESPACE.sub("", pattern.text or ""),
                _lengths(lengths.get("national")),
                _lengths(lengths.get("localOnly")),
            )
            if (example := desc.find("exampleNumber")) is not None and example.text:
                examples[name] = example.text
        national = frozenset().union(*(desc.national for desc in types.values()))
        national_prefix = territory.get("nationalPrefix")
        regions.append(
            Region(
                code=territory.get("id", ""),
                country_code=int(territory.get("countryCode", "0")),
                main=territory.get("mainCountryForCode") == "true",
                leading_digits=_strip_or_none(territory.get("leadingDigits")),
                national_prefix=national_prefix,
                prefix_pattern=_strip_or_none(territory.get("nationalPrefixForParsing") or national_prefix),
                transform=territory.get("nationalPrefixTransformRule"),
                idd_pattern=_strip_or_none(territory.get("internationalPrefix")),
                general=_WHITESPACE.sub("", general.findtext("nationalNumberPattern") or ""),
                types=types,
                ext_prefix=territory.get("preferredExtnPrefix"),
                formats=parse_formats(territory, national_prefix or ""),
                possible_national=national,
                possible_local_only=frozenset().union(*(desc.local_only for desc in types.values())) - national,
                examples=examples,
            )
        )
    if not regions:
        msg = "no territories in the metadata"
        raise GenerationError(msg)
    return sorted(regions, key=lambda region: (region.code, region.country_code))


def _lengths(text: str | None) -> frozenset[int]:
    if not text:
        return frozenset()
    values: set[int] = set()
    for item in text.split(","):
        matched = _LENGTH_ITEM.match(item.strip())
        if matched is None:
            msg = f"unparsable possibleLengths item {item!r}"
            raise GenerationError(msg)
        if matched.group(1):
            values.add(int(matched.group(1)))
        else:
            values.update(range(int(matched.group(2)), int(matched.group(3)) + 1))
    return frozenset(values)


def parse_formats(territory: ET.Element, national_prefix: str) -> list[Format]:
    """Read the availableFormats the way BuildMetadataFromXml's loadAvailableFormats resolves their prefix rules."""
    name = territory.get("id") or f"+{territory.get('countryCode')}"
    territory_rule = _formatting_rule(territory.get("nationalPrefixFormattingRule"), national_prefix)
    territory_optional = territory.get("nationalPrefixOptionalWhenFormatting") == "true"
    formats: list[Format] = []
    for element in territory.iter("numberFormat"):
        rule = territory_rule
        if (own_rule := element.get("nationalPrefixFormattingRule")) is not None:
            rule = _formatting_rule(own_rule, national_prefix)
        optional = territory_optional
        if (own_optional := element.get("nationalPrefixOptionalWhenFormatting")) is not None:
            optional = own_optional == "true"
        leading = [_WHITESPACE.sub("", item.text or "") for item in element.iter("leadingDigits")]
        pattern = element.get("pattern")
        if not pattern:
            msg = f"{name}: a numberFormat needs a pattern"
            raise GenerationError(msg)
        groups = parse_groups(name, pattern)
        template = parse_template(name, element.findtext("format"), len(groups))
        intl_text = element.findtext("intlFormat")
        intl = None if intl_text == "NA" else parse_template(name, intl_text or template, len(groups))
        formats.append(
            Format(
                leading[-1] if leading else None,
                pattern,
                groups,
                parse_template(name, _apply_rule(rule, template), len(groups)),
                intl,
                bool(rule) and not optional and _FIRST_GROUP_ONLY.fullmatch(rule) is None,
            )
        )
    return formats


def parse_groups(name: str, pattern: str) -> tuple[tuple[int, int], ...]:
    """Read the digit-count bounds of a numberFormat pattern's capture groups; refuse any other pattern shape."""
    if _GROUP_RUN.fullmatch(pattern) is None:
        msg = f"{name}: numberFormat pattern {pattern!r} is not a run of digit groups"
        raise GenerationError(msg)
    groups = tuple((int(low or 1), int(high or low or 1)) for low, high in _GROUP_PART.findall(pattern))
    if len(groups) > _MAX_FORMAT_GROUPS or any(low < 1 or high < low or high > 15 for low, high in groups):
        msg = f"{name}: numberFormat pattern {pattern!r} exceeds the group bounds"
        raise GenerationError(msg)
    return groups


def parse_template(name: str, text: str | None, group_count: int) -> str:
    """Check a format template: ``$N`` references into the pattern's groups and the separators the formatter emits."""
    if not text or len(text) > _MAX_TEMPLATE_CHARS:
        msg = f"{name}: format template {text!r} is empty or longer than {_MAX_TEMPLATE_CHARS}"
        raise GenerationError(msg)
    refs = _TEMPLATE_REF.findall(text)
    if (
        not refs
        or any(not 1 <= int(ref) <= group_count for ref in refs)
        or not set(_TEMPLATE_REF.sub("", text)) <= _TEMPLATE_CHARS
    ):
        msg = f"{name}: format template {text!r} references a missing group or uses an unexpected character"
        raise GenerationError(msg)
    if _TEMPLATE_REF.fullmatch(text[-2:]) is None:
        # RFC 3966's separator rewrite never has to flush a trailing run, and every group split ends on a digit
        msg = f"{name}: format template {text!r} does not end with a group"
        raise GenerationError(msg)
    return text


def _formatting_rule(rule: str | None, national_prefix: str) -> str:
    return (rule or "").replace("$NP", national_prefix).replace("$FG", "$1")


def _apply_rule(rule: str, template: str) -> str:
    """FormatNsnUsingPattern's NATIONAL rewrite: the rule replaces the template's first group reference."""
    if not rule:
        return template
    first = next(_TEMPLATE_REF.finditer(template))
    return template[: first.start()] + rule.replace("$1", first.group()) + template[first.end() :]


def parse_alternate_formats(xml: bytes) -> dict[int, list[Format]]:
    """Read PhoneNumberAlternateFormats.xml: per calling code, the groupings the matcher also accepts."""
    formats: dict[int, list[Format]] = {}
    for territory in ET.fromstring(xml).iter("territory"):  # ruff:ignore[suspicious-xml-element-tree-usage]  # pinned by hash, not untrusted
        code = int(territory.get("countryCode", "0"))
        if code in formats:
            msg = f"+{code}: alternate formats listed twice"
            raise GenerationError(msg)
        if any(len(element.findall("leadingDigits")) > 1 for element in territory.iter("numberFormat")):
            msg = f"+{code}: an alternate format carries several leadingDigits; the matcher reads only the first"
            raise GenerationError(msg)
        formats[code] = parse_formats(territory, "")
    return formats


def _strip_or_none(text: str | None) -> str | None:
    return _WHITESPACE.sub("", text) if text else None


def parse_transform(rule: str | None, prefix_program: Program | None) -> tuple[str, int]:
    """Parse a transform rule, ``<digits>? "$" <group>`` and nothing else, into its literal and group."""
    if rule is None:
        return "", 0
    matched = _TRANSFORM_RULE.match(rule)
    if matched is None:
        msg = f"unsupported nationalPrefixTransformRule {rule!r}"
        raise GenerationError(msg)
    literal, group = matched.group(1), int(matched.group(2))
    if len(literal) > 4 or group == 0 or prefix_program is None or 2 * group + 1 >= prefix_program.slots:
        msg = f"transform rule {rule!r} does not fit the prefix pattern"
        raise GenerationError(msg)
    return literal, group


def compile_region(region: Region, programs: list[Program], rng: random.Random) -> RegionTables:
    """Compile one territory's plan, prefix and IDD automata and prove or store its per-type lengths."""
    type_programs = [
        compile_program(region.types[name].pattern, label=1 << bit, capture=False) if name in region.types else None
        for bit, name in enumerate(TYPES)
    ]
    plan = compile_dfa(
        union_program([
            *[program for program in type_programs if program is not None],
            compile_program(region.general, label=GENERAL_BIT, capture=False),
        ])
    )
    labels = sorted({accept for accept in plan.accepts if accept} - {0})
    if len(labels) > 15:
        msg = f"{region.code}: {len(labels)} distinct plan labels, the dictionary holds 15"
        raise GenerationError(msg)
    _check_type_lengths(region, type_programs)
    prefix = prefix_program = tag = None
    if region.prefix_pattern:
        prefix_program = compile_program(region.prefix_pattern)
        prefix = compile_priority_dfa(prefix_program)
        literal, group = parse_transform(region.transform, prefix_program)
        programs.append(prefix_program)
        tag = _PrefixTag(literal, group, len(programs) - 1)
    if region.idd_pattern is None and region.code != "001":
        msg = f"{region.code}: a geographic region needs an internationalPrefix"
        raise GenerationError(msg)
    idd = compile_priority_dfa(compile_program(region.idd_pattern, capture=False)) if region.idd_pattern else None
    compiled = _CompiledRegion(region, plan, prefix, prefix_program, tag, idd)
    _self_check_region(compiled, rng)
    return RegionTables(
        region,
        plan,
        labels,
        prefix,
        prefix_program,
        tag,
        idd,
        _floor(compiled, valid=True),
        _floor(compiled, valid=False),
        [_compile_format(item) for item in region.formats],
    )


def _compile_format(item: Format) -> _FormatTables:
    # a format without leadingDigits applies to every number, so its automaton accepts any first digit
    return _FormatTables(compile_dfa(compile_program(item.leading or r"\d", capture=False)), item)


def _check_type_lengths(region: Region, type_programs: list[Program | None]) -> None:
    """Prove each type regex accepts only its possible lengths, so a plan accept never needs a length test."""
    for bit, name in enumerate(TYPES):
        program = type_programs[bit]
        if program is None:
            continue
        if escaped := set(accepted_lengths(compile_dfa(program))) - (
            region.types[name].national or region.possible_national
        ):
            msg = f"{region.code}/{name}: the pattern accepts lengths {sorted(escaped)} outside its possibleLengths"
            raise GenerationError(msg)


def _prefix_shapes(program: Program, group: int) -> set[tuple[int, int]]:
    """Every ``(match length, captured length)`` a prefix path can produce; -1 when the group did not participate."""
    shapes: set[tuple[int, int]] = set()
    seen: set[tuple[int, int, int, int]] = set()
    stack = [(program.start, 0, -1, -1)]
    while stack:
        index, consumed, group_start, group_end = stack.pop()
        key = (index, consumed, group_start, group_end)
        if key in seen:
            continue
        seen.add(key)
        op = program.ops[index]
        if op.kind == MATCH:
            shapes.add((consumed, group_end - group_start if group_start >= 0 and group_end >= 0 else -1))
        elif op.kind == SPLIT:
            stack.extend(((op.next, consumed, group_start, group_end), (op.alt, consumed, group_start, group_end)))
        elif op.kind == SAVE:
            if op.arg == 2 * group:
                stack.append((op.next, consumed, consumed, group_end))
            elif op.arg == 2 * group + 1:
                stack.append((op.next, consumed, group_start, consumed))
            else:
                stack.append((op.next, consumed, group_start, group_end))
        elif op.kind == ASSERT_END:
            stack.append((op.next, consumed, group_start, group_end))
        elif op.kind == CHAR:
            stack.append((op.next, consumed + 1, group_start, group_end))
    return shapes


@dataclass
class _CompiledRegion:
    """One region's automata before validity floors are measured, bundled for ``_self_check_region`` and ``_floor``."""

    region: Region
    plan: Dfa
    prefix: PriorityDfa | None
    prefix_program: Program | None
    tag: _PrefixTag | None
    idd: PriorityDfa | None


def _floor(compiled: _CompiledRegion, *, valid: bool) -> int:
    """Measure the shortest raw digit string any reading of the region accepts, transforms and prefixes included."""
    region = compiled.region
    if valid:
        nsn_lengths = {
            length
            for length, label in accepted_lengths(compiled.plan).items()
            if label & GENERAL_BIT and label & ~GENERAL_BIT
        }
    else:
        nsn_lengths = set(region.possible_national | region.possible_local_only)
    nsn_lengths = {length for length in nsn_lengths if MIN_NSN <= length <= MAX_NSN}
    if not nsn_lengths:
        return 255
    candidates = set(nsn_lengths)
    if compiled.prefix_program is not None and (tag := compiled.tag) is not None:
        for match_length, captured in _prefix_shapes(compiled.prefix_program, tag.group):
            if tag.group and captured >= 0:
                inserted = len(tag.literal) + captured
                candidates.update(match_length + length - inserted for length in nsn_lengths if length >= inserted)
            else:
                candidates.update(match_length + length for length in nsn_lengths)
    if compiled.idd is not None and (idd_length := shortest_accept(compiled.idd)) >= 0:
        candidates.update(idd_length + len(str(region.country_code)) + length for length in nsn_lengths)
    candidates.update(len(str(region.country_code)) + length for length in nsn_lengths)
    return max(min(candidates), MIN_NSN)


def _self_check_region(compiled: _CompiledRegion, rng: random.Random) -> None:
    """Compare every automaton with Python's ``re`` on the example numbers and seeded random digit strings."""
    _check_plan(compiled, rng)
    _check_priority_dfas(compiled, rng)


def _check_plan(compiled: _CompiledRegion, rng: random.Random) -> None:
    """Walk the plan DFA over example and random digit strings and compare its label against ``re``."""
    region = compiled.region
    general = re.compile(region.general)
    type_patterns = {name: re.compile(desc.pattern) for name, desc in region.types.items()}
    samples = [*region.examples.values(), *(_random_digits(rng, 1, MAX_NSN) for _ in range(2000))]
    samples += [mutated for example in region.examples.values() for mutated in _mutations(rng, example)]
    for sample in samples:
        state = 1
        for digit in [ord(char) - 0x30 for char in sample]:
            state = compiled.plan.next[state][digit]
        label = compiled.plan.accepts[state] if state else 0
        expected = GENERAL_BIT if general.fullmatch(sample) else 0
        for bit, name in enumerate(TYPES):
            if name in type_patterns and type_patterns[name].fullmatch(sample):
                expected |= 1 << bit
        if label != expected:
            msg = f"{region.code}: plan DFA disagrees with re on {sample!r} ({label:#x} vs {expected:#x})"
            raise GenerationError(msg)


def _check_priority_dfas(compiled: _CompiledRegion, rng: random.Random) -> None:
    """Compare the prefix and IDD priority DFAs, and the prefix Pike VM's transform group, against ``re``."""
    region = compiled.region
    for automaton, pattern in ((compiled.prefix, region.prefix_pattern), (compiled.idd, region.idd_pattern)):
        if automaton is None or pattern is None:
            continue
        pattern_re = re.compile(pattern)
        for _ in range(4000):
            sample = _random_digits(rng, 0, MAX_NSN)
            digits = [ord(char) - 0x30 for char in sample]
            matched = pattern_re.match(sample)
            if match_end(automaton, digits) != (matched.end() if matched else -1):
                msg = f"{region.code}: priority DFA disagrees with re.match on {sample!r} for {pattern!r}"
                raise GenerationError(msg)
            if (
                matched
                and automaton is compiled.prefix
                and compiled.prefix_program is not None
                and compiled.tag is not None
                and compiled.tag.group
            ):
                spans = pike_spans(compiled.prefix_program, digits[: matched.end()]) or {}
                if spans.get(compiled.tag.group) != (
                    matched.span(compiled.tag.group) if matched.span(compiled.tag.group) != (-1, -1) else None
                ):
                    msg = f"{region.code}: Pike VM disagrees with re on the transform group for {sample!r}"
                    raise GenerationError(msg)


def _random_digits(rng: random.Random, low: int, high: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(rng.randint(low, high)))


def _mutations(rng: random.Random, example: str) -> list[str]:
    variants: list[str] = []
    for _ in range(3):
        position = rng.randrange(len(example))
        variants.append(example[:position] + rng.choice(string.digits) + example[position + 1 :])
    variants.extend((example[:-1], example + rng.choice(string.digits)))
    return variants


def compile_groups(regions: list[RegionTables], rng: random.Random) -> tuple[list[Group], dict[int, int]]:
    """Group territories by calling code, main region first, with one sticky router per shared code."""
    by_code: dict[int, list[int]] = {}
    for index, tables in enumerate(regions):
        by_code.setdefault(tables.region.country_code, []).append(index)
    groups: list[Group] = []
    group_of_code: dict[int, int] = {}
    for country_code, members in sorted(by_code.items()):
        mains = [index for index in members if regions[index].region.main]
        if len(members) > 1 and len(mains) != 1:
            msg = f"+{country_code}: {len(mains)} main regions for a shared code"
            raise GenerationError(msg)
        main = mains[0] if mains else members[0]
        ordered = [main, *[index for index in members if index != main]]
        routed = [regions[index].region.leading_digits is not None for index in ordered]
        router = None
        if any(routed):
            router = compile_dfa(
                sticky_program(
                    union_program([
                        compile_program(regions[index].region.leading_digits or "", label=1 << position, capture=False)
                        for position, index in enumerate(ordered)
                        if routed[position]
                    ]),
                    ALL_DIGITS,
                )
            )
            _self_check_router(router, [regions[index].region for index in ordered], routed, rng)
        group_of_code[country_code] = len(groups)
        groups.append(Group(country_code, ordered, main, router, routed))
    return groups, group_of_code


def _self_check_router(router: Dfa, members: list[Region], routed: list[bool], rng: random.Random) -> None:
    patterns = [
        (1 << position, re.compile(region.leading_digits or ""))
        for position, region in enumerate(members)
        if routed[position]
    ]
    for _ in range(2000):
        sample = _random_digits(rng, 1, 12)
        state = 1
        for char in sample:
            state = router.next[state][ord(char) - 0x30]
        expected = 0
        for bit, pattern in patterns:
            if pattern.match(sample):
                expected |= bit
        if (router.accepts[state] if state else 0) != expected:
            msg = f"+{members[0].country_code}: router disagrees with the sequential lookingAt loop on {sample!r}"
            raise GenerationError(msg)


def router_replay(router: Dfa) -> int:
    """Measure the deepest first accept over the router: the digits a newly routed region's plan walk must replay."""
    frontier = {1}
    deepest = 0
    for depth in range(1, _MAX_ROUTER_REPLAY + 2):
        successors = {router.next[state][symbol] for state in frontier for symbol in range(DIGIT_SYMBOLS)} - {0}
        if any(router.accepts[state] for state in successors):
            deepest = depth
        frontier = {state for state in successors if not router.accepts[state]}
        if not frontier:
            return deepest
    msg = "router first accept deeper than the replay cap"
    raise GenerationError(msg)


def parse_unicode(unicode_data: str, blocks: str) -> _UnicodeTables:
    """Digits with their zero, currency symbols, the Latin letters and marks of six blocks, and case mappings."""
    categories, decimal, upper, lower, title = _parse_unicode_records(unicode_data)
    nd_ranges = _digit_ranges(decimal)
    pages = [0] * ((0x110000 // 256 + 7) // 8)
    for first, last, _zero in nd_ranges:
        for page in range(first >> 8, (last >> 8) + 1):
            pages[page >> 3] |= 1 << (page & 7)
    block_ranges = _blocks(blocks)
    if set(_LATIN_BLOCKS) - set(block_ranges):
        msg = "a Latin block named by isLatinLetter is missing from Blocks.txt"
        raise GenerationError(msg)
    return _UnicodeTables(
        nd_ranges,
        pages,
        _ranges({code for code, category in categories.items() if category == "Sc"}),
        _ranges({
            code
            for code, category in categories.items()
            if (category.startswith("L") or category == "Mn")
            and any(first <= code <= last for name, (first, last) in block_ranges.items() if name in _LATIN_BLOCKS)
        }),
        _ranges({code for code, category in categories.items() if category.startswith("L")}),
        _ranges({code for code, category in categories.items() if category.startswith("N")}),
        _case_classes(upper, lower, title),
    )


def _parse_unicode_records(
    unicode_data: str,
) -> tuple[dict[int, str], dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
    """Read UnicodeData.txt into per-code-point category, decimal value and case mappings, expanding First/Last runs."""
    categories: dict[int, str] = {}
    decimal: dict[int, int] = {}
    upper: dict[int, int] = {}
    lower: dict[int, int] = {}
    title: dict[int, int] = {}
    pending_first: tuple[int, str] | None = None
    for line in unicode_data.splitlines():
        fields = line.split(";")
        if len(fields) < 15:
            continue
        code = int(fields[0], 16)
        name, category = fields[1], fields[2]
        if name.endswith(", First>"):
            pending_first = (code, category)
            continue
        if name.endswith(", Last>") and pending_first is not None:
            for ranged in range(pending_first[0], code + 1):
                categories[ranged] = category
            pending_first = None
            continue
        categories[code] = category
        if category == "Nd" and fields[6]:
            decimal[code] = int(fields[6])
        if fields[12]:
            upper[code] = int(fields[12], 16)
        if fields[13]:
            lower[code] = int(fields[13], 16)
        if fields[14]:
            title[code] = int(fields[14], 16)
    return categories, decimal, upper, lower, title


def _digit_ranges(decimal: dict[int, int]) -> list[tuple[int, int, int]]:
    ranges: list[tuple[int, int, int]] = []
    for zero in sorted({code - value for code, value in decimal.items()}):
        if any(decimal.get(zero + digit) != digit for digit in range(10)):
            msg = f"decimal digits at U+{zero:04X} are not a contiguous 0-9 run"
            raise GenerationError(msg)
        ranges.append((zero, zero + 9, zero))
    return ranges


def _blocks(blocks: str) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for line in blocks.splitlines():
        if not line or line.startswith("#"):
            continue
        span, name = line.split(";")
        first, last = span.split("..")
        result[name.strip()] = (int(first, 16), int(last, 16))
    return result


def _ranges(codes: set[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for code in sorted(codes):
        if ranges and ranges[-1][1] == code - 1:
            ranges[-1] = (ranges[-1][0], code)
        else:
            ranges.append((code, code))
    return ranges


def _case_classes(upper: dict[int, int], lower: dict[int, int], title: dict[int, int]) -> dict[int, frozenset[int]]:
    """Closure of the forward and inverse simple case mappings: the code points Java's UNICODE_CASE treats as equal."""
    neighbors: dict[int, set[int]] = {}
    for mapping in (upper, lower, title):
        for source, target in mapping.items():
            neighbors.setdefault(source, set()).add(target)
            neighbors.setdefault(target, set()).add(source)
    classes: dict[int, frozenset[int]] = {}
    for code in neighbors:
        if code in classes:
            continue
        members = {code}
        stack = [code]
        while stack:
            for neighbor in neighbors.get(stack.pop(), ()):
                if neighbor not in members:
                    members.add(neighbor)
                    stack.append(neighbor)
        frozen = frozenset(members)
        for member in members:
            classes[member] = frozen
    return classes


def _compile_extension(unicode: _UnicodeTables) -> _ExtensionTables:
    """Compile the extension forms over a classifier alphabet: case classes, separators, one digit class."""
    symbol_of: dict[int, int] = {}
    next_symbol = 2
    for code in sorted(_extension_literals()):
        if code in symbol_of:
            continue
        for member in sorted(
            unicode.case_classes.get(code, frozenset({code})) if chr(code).isalpha() else frozenset({code})
        ):
            symbol_of[member] = next_symbol
        next_symbol += 1
    if next_symbol > 255:
        msg = f"extension alphabet has {next_symbol} symbols, the classifier holds 255"
        raise GenerationError(msg)

    def symbolize(code: int) -> int:
        return 1 << symbol_of[code]

    def compile_forms(forms: tuple[tuple[str, str, int], ...]) -> Dfa:
        return compile_dfa(
            union_program([
                compile_program(
                    pattern, symbolize=symbolize, digit_mask=1 << 1, label=cap, capture=False, allow_unbounded=True
                )
                for _name, pattern, cap in forms
            ]),
            symbols=next_symbol,
            end_symbol=-1,
        )

    classes: list[tuple[int, int, int]] = [(first, last, 1) for first, last, _zero in unicode.nd_ranges]
    for code, symbol in symbol_of.items():
        classes.append((code, code, symbol))
    classes.sort()
    return _ExtensionTables(
        classes, next_symbol, compile_forms(_EXTENSION_FORMS), compile_forms(_PARSING_EXTENSION_FORMS), symbol_of
    )


def _extension_literals() -> set[int]:
    """Collect every literal code point an extension form spells, the alphabet both grammars share."""
    literals: set[int] = set()
    for _name, pattern, _cap in _PARSING_EXTENSION_FORMS:
        for opcode, argument in _walk(list(sre_parse.parse(pattern))):
            if opcode == "literal":
                literals.add(argument)
    return literals


def _walk(items: SreItems) -> Iterable[tuple[str, int]]:
    for opcode, argument in items:
        if opcode is sre_constants.LITERAL:
            yield "literal", cast("int", argument)
        elif opcode is sre_constants.IN:
            yield from _walk_in(cast("SreItems", argument))
        elif opcode is sre_constants.SUBPATTERN:
            yield from _walk(cast("tuple[int | None, int, int, SreItems]", argument)[3])
        elif opcode is sre_constants.BRANCH:
            for alternative in cast("tuple[None, list[SreItems]]", argument)[1]:
                yield from _walk(alternative)
        elif opcode is sre_constants.MAX_REPEAT:
            yield from _walk(cast("tuple[int, int, SreItems]", argument)[2])


def _walk_in(argument: SreItems) -> Iterable[tuple[str, int]]:
    for inner, value in argument:
        if inner is sre_constants.LITERAL:
            yield "literal", cast("int", value)
        elif inner is sre_constants.RANGE:
            low, high = cast("tuple[int, int]", value)
            yield from (("literal", code) for code in range(low, high + 1))


def compile_tables(sources: dict[str, bytes], seed: int = 758) -> Tables:
    """Build every table from the verified sources, checking each automaton against ``re`` as it goes."""
    check_java_constants(sources["PhoneNumberUtil.java"].decode("utf-8"))
    rng = random.Random(seed)
    programs: list[Program] = []
    compiled = [compile_region(region, programs, rng) for region in parse_metadata(sources["PhoneNumberMetadata.xml"])]
    groups, group_of_code = compile_groups(compiled, rng)
    for group in groups:
        # a reading validates against any plan of its calling code, so a region's floor is its group's shortest
        members = [compiled[index] for index in group.members]
        for tables in members:
            tables.floor_valid = min(item.floor_valid for item in members)
            tables.floor_possible = min(item.floor_possible for item in members)
    unicode = parse_unicode(sources["UnicodeData.txt"].decode("utf-8"), sources["Blocks.txt"].decode("utf-8"))
    max_lag = max((lag(tables.prefix) for tables in compiled if tables.prefix), default=0)
    max_lag = max(max_lag, *(lag(tables.idd) for tables in compiled if tables.idd))
    max_prefix = max(
        *(longest_accept(tables.prefix) for tables in compiled if tables.prefix),
        *(longest_accept(tables.idd) for tables in compiled if tables.idd),
    )
    replay = max((router_replay(group.router) for group in groups if group.router), default=0)
    threads = max((max_threads(program) for program in programs), default=0)
    slots = max((program.slots for program in programs), default=0)
    if max_lag > 20 or max_prefix > 20 or replay > _MAX_ROUTER_REPLAY or threads > 64 or slots > 8:
        msg = f"bounds exceeded: lag {max_lag}, prefix {max_prefix}, replay {replay}, threads {threads}, slots {slots}"
        raise GenerationError(msg)
    return Tables(
        compiled,
        groups,
        group_of_code,
        _compile_alternates(sources["PhoneNumberAlternateFormats.xml"], len(groups), group_of_code),
        unicode,
        _compile_extension(unicode),
        max_lag,
        max_prefix,
        replay,
        threads,
        slots,
    )


def _compile_alternates(xml: bytes, group_count: int, group_of_code: dict[int, int]) -> list[list[_FormatTables]]:
    """Compile the alternate formats of each calling code, indexed like the groups."""
    alternates: list[list[_FormatTables]] = [[] for _group in range(group_count)]
    for code, items in parse_alternate_formats(xml).items():
        if code not in group_of_code:
            msg = f"+{code}: alternate formats for a calling code the metadata does not assign"
            raise GenerationError(msg)
        alternates[group_of_code[code]] = [_compile_format(item) for item in items]
    return alternates


def emit_header(  # ruff:ignore[complex-structure, too-many-branches, too-many-statements, too-many-locals]  # one emitter section per C table; splitting would scatter one format across many co-dependent functions
    tables: Tables,
) -> tuple[str, int]:
    """Render the C header; return it with the byte size of the encoded automata."""
    dfas: list[tuple[Dfa | PriorityDfa, int]] = []
    region_dfa_index: list[dict[str, int]] = []
    type_masks: list[int] = [0]
    label_offsets: list[tuple[int, int]] = []

    def add(automaton: Dfa | PriorityDfa, symbols: int) -> int:
        dfas.append((automaton, symbols))
        return len(dfas) - 1

    for region_tables in tables.regions:
        offset = len(type_masks)
        type_masks.extend(region_tables.labels)
        label_offsets.append((offset, len(region_tables.labels)))
        dictionary = {label: position + 1 for position, label in enumerate(region_tables.labels)}
        plan = region_tables.plan
        entry = {
            "plan": add(
                Dfa(
                    plan.symbols,
                    plan.next,
                    [(accept & GENERAL_BIT and 0x8000) | dictionary.get(accept, 0) for accept in plan.accepts],
                ),
                DIGIT_SYMBOLS,
            )
        }
        if region_tables.prefix is not None:
            entry["prefix"] = add(region_tables.prefix, DIGIT_SYMBOLS + 1)
        if region_tables.idd is not None:
            entry["idd"] = add(region_tables.idd, DIGIT_SYMBOLS + 1)
        region_dfa_index.append(entry)
    router_index: dict[int, int] = {}
    router_sets: list[int] = [0]
    for group_index, group in enumerate(tables.groups):
        if group.router is None:
            continue
        set_index = {0: 0}
        for accept in group.router.accepts:
            if accept and accept not in set_index:
                set_index[accept] = len(router_sets)
                router_sets.append(accept)
        router_index[group_index] = add(
            Dfa(group.router.symbols, group.router.next, [set_index[accept] for accept in group.router.accepts]),
            DIGIT_SYMBOLS,
        )
    extension_index = add(tables.extension.dfa, tables.extension.symbols)
    parsing_extension_index = add(tables.extension.parsing_dfa, tables.extension.symbols)
    shared_dfa: dict[tuple, int] = {}

    def add_shared(automaton: Dfa) -> int:
        key = (automaton.symbols, tuple(map(tuple, automaton.next)), tuple(automaton.accepts))
        if key not in shared_dfa:
            shared_dfa[key] = add(automaton, DIGIT_SYMBOLS)
        return shared_dfa[key]

    templates: list[str] = []
    template_offset: dict[str, int] = {}

    def add_template(text: str) -> int:
        if text not in template_offset:
            template_offset[text] = sum(len(item) + 1 for item in templates)
            templates.append(text)
        return template_offset[text]

    def format_row(item: _FormatTables) -> str:
        groups = [(low << 4) | high for low, high in item.format.groups]
        groups.extend([0] * (_MAX_FORMAT_GROUPS - len(groups)))
        national = add_template(item.format.national)
        intl = 0xFFFF if item.format.intl is None else add_template(item.format.intl)
        return (
            f"{{{add_shared(item.leading)}u, {len(item.format.groups)}u, {{{', '.join(f'{g}u' for g in groups)}}}, "
            f"{national}u, {intl}u, {int(item.format.requires_prefix)}u}}"
        )

    format_rows: list[str] = []
    format_spans: list[tuple[int, int]] = []
    mains = {group.main for group in tables.groups}
    for index, region_tables in enumerate(tables.regions):
        first = len(format_rows)
        if index in mains:
            format_rows.extend(format_row(item) for item in region_tables.formats)
        format_spans.append((first, len(format_rows) - first))
    alternate_rows: list[str] = []
    alternate_spans: list[tuple[int, int]] = []
    for items in tables.alternates:
        first = len(alternate_rows)
        alternate_rows.extend(format_row(item) for item in items)
        alternate_spans.append((first, len(alternate_rows) - first))
    ext_prefixes = [
        0xFFFF if region_tables.region.ext_prefix is None else add_template(region_tables.region.ext_prefix)
        for region_tables in tables.regions
    ]
    rows8, rows16, accepts, descriptors = _rows(dfas)
    ops: list[tuple[int, int, int, int]] = []
    program_table: list[tuple[int, int, int]] = []
    class_masks: list[int] = []
    for program in [
        region_tables.prefix_program for region_tables in tables.regions if region_tables.prefix_program is not None
    ]:
        first = len(ops)
        for op in program.ops:
            arg = op.arg
            if op.kind == CHAR:
                if op.arg not in class_masks:
                    class_masks.append(op.arg)
                arg = class_masks.index(op.arg)
            ops.append((op.kind, arg, op.next if op.next >= 0 else 0xFFFF, op.alt if op.alt >= 0 else 0xFFFF))
        program_table.append((first, len(program.ops), program.start))
    lines: list[str] = []
    out = lines.append
    out("/* Auto-generated by tools/generate_phone.py - do not edit. */")
    out(
        f"/* libphonenumber {LIBPHONENUMBER_TAG} numbering plans compiled to DFAs; "
        f"Unicode {_UNICODE_VERSION} tables. */"
    )
    out("")
    out("#ifndef TURBOHTML_PHONE_TABLE_H")
    out("#define TURBOHTML_PHONE_TABLE_H")
    out("")
    out("#include <stdint.h>")
    out("")
    out(f'#define TH_PHONE_METADATA_TAG "{LIBPHONENUMBER_TAG}"')
    out(f"#define TH_PHONE_MAX_NSN {MAX_NSN}")
    out(f"#define TH_PHONE_MAX_LAG {tables.max_lag}")
    out(f"#define TH_PHONE_MAX_PREFIX_DIGITS {tables.max_prefix_digits}")
    out(f"#define TH_PHONE_MAX_ROUTER_REPLAY {tables.max_router_replay}")
    out(f"#define TH_PHONE_NFA_THREADS {tables.nfa_threads}")
    out(f"#define TH_PHONE_NFA_SLOTS {max(tables.nfa_slots, 2)}")
    out(f"#define TH_PHONE_EXT_SYMBOLS {tables.extension.symbols}")
    out(f"#define TH_PHONE_REGION_COUNT {len(tables.regions)}")
    out(f"#define TH_PHONE_GROUP_COUNT {len(tables.groups)}")
    out(f"#define TH_PHONE_EXT_DFA {extension_index}")
    out(f"#define TH_PHONE_EXT_PARSING_DFA {parsing_extension_index}")
    out("#define TH_PHONE_GENERAL_BIT 0x8000")
    out(f"#define TH_PHONE_FORMAT_GROUPS {_MAX_FORMAT_GROUPS}")
    out(f"#define TH_PHONE_TEMPLATE_CHARS {_MAX_TEMPLATE_CHARS}")
    out("")
    out("typedef struct {")
    out("    uint32_t next_offset;")
    out("    uint32_t accept_offset;")
    out("    uint16_t states;")
    out("    uint8_t symbols;")
    out("    uint8_t wide;")
    out("} th_phone_dfa;")
    out("")
    out("typedef struct {")
    out("    uint8_t literal_len;")
    out("    uint8_t group;")
    out("    uint16_t program;")
    out("    char literal[5];")
    out("} th_phone_prefix_tag;")
    out("")
    out("typedef struct {")
    out("    char code[4];")
    out("    uint8_t code_len;")
    out("    uint16_t country_code;")
    out("    uint16_t group;")
    out("    uint16_t idd;")
    out("    uint16_t national_prefix;")
    out("    uint16_t plan;")
    out("    uint16_t labels;")
    out("    uint8_t label_count;")
    out("    uint16_t prefix_tag;")
    out("    uint32_t possible_national;")
    out("    uint32_t possible_local_only;")
    out("    uint8_t floor_valid;")
    out("    uint8_t floor_possible;")
    out("    uint16_t format_first;")
    out("    uint8_t format_count;")
    out("    uint16_t ext_prefix; /* into th_phone_templates, 0xFFFF for the default */")
    out("    uint8_t has_ndd;     /* the territory declares a nationalPrefix */")
    out("} th_phone_region;")
    out("")
    out(
        "/* A numberFormat of a calling code's main region (or an alternate format of the code): the last leadingDigits"
    )
    out("   pattern (any digit when it has none), each capture group's digit-count bounds as (min << 4) | max, the")
    out("   NATIONAL template with the national prefix rule applied and the international one (0xFFFF when intlFormat")
    out("   is NA), both offsets into th_phone_templates, and whether VALID needs the prefix in the written digits. */")
    out("typedef struct {")
    out("    uint16_t leading;")
    out("    uint8_t group_count;")
    out("    uint8_t groups[TH_PHONE_FORMAT_GROUPS];")
    out("    uint16_t national;")
    out("    uint16_t intl;")
    out("    uint8_t requires_prefix;")
    out("} th_phone_format;")
    out("")
    out("typedef struct {")
    out("    uint16_t country_code;")
    out("    uint16_t router;")
    out("    uint16_t first;")
    out("    uint8_t count;")
    out("    uint16_t main;")
    out("    uint16_t alt_first;")
    out("    uint8_t alt_count;")
    out("} th_phone_group;")
    out("")
    out("typedef struct {")
    out("    uint8_t op;")
    out("    uint8_t arg;")
    out("    uint16_t next;")
    out("    uint16_t alt;")
    out("} th_phone_nfa_op;")
    out("")
    out("typedef struct {")
    out("    uint16_t first;")
    out("    uint8_t count;")
    out("    uint8_t start;")
    out("} th_phone_nfa;")
    out("")
    out(f"#define TH_PHONE_NFA_CHAR {CHAR}")
    out(f"#define TH_PHONE_NFA_SPLIT {SPLIT}")
    out(f"#define TH_PHONE_NFA_SAVE {SAVE}")
    out(f"#define TH_PHONE_NFA_ASSERT_END {ASSERT_END}")
    out(f"#define TH_PHONE_NFA_MATCH {MATCH}")
    out("")
    out(_array("uint8_t", "th_phone_rows8", rows8))
    out(_array("uint16_t", "th_phone_rows16", rows16 or [0]))
    out(_array("uint16_t", "th_phone_accepts", accepts))
    out("static const th_phone_dfa th_phone_dfas[] = {")
    for next_offset, accept_offset, states, symbols, wide in descriptors:
        out(f"    {{{next_offset}u, {accept_offset}u, {states}u, {symbols}u, {wide}u}},")
    out("};")
    out("")
    out(_array("uint16_t", "th_phone_type_masks", type_masks))
    out(_array("uint32_t", "th_phone_router_sets", router_sets))
    tags: list[_PrefixTag] = [_PrefixTag("", 0, 0)]
    out("static const th_phone_region th_phone_regions[] = {")
    for index, region_tables in enumerate(tables.regions):
        region = region_tables.region
        entry = region_dfa_index[index]
        tag_index = 0
        if region_tables.tag is not None:
            tags.append(region_tables.tag)
            tag_index = len(tags) - 1
        out(
            f'    {{"{region.code}", {len(region.code)}u, {region.country_code}u, '
            f"{tables.group_of_code[region.country_code]}u, "
            f"{entry.get('idd', 0xFFFF)}u, {entry.get('prefix', 0xFFFF)}u, {entry['plan']}u, "
            f"{label_offsets[index][0]}u, {label_offsets[index][1]}u, {tag_index}u, "
            f"{sum(1 << length for length in region.possible_national)}u, "
            f"{sum(1 << length for length in region.possible_local_only)}u, "
            f"{region_tables.floor_valid}u, {region_tables.floor_possible}u, "
            f"{format_spans[index][0]}u, {format_spans[index][1]}u, {ext_prefixes[index]}u, "
            f"{int(bool(region.national_prefix))}u}},"
        )
    out("};")
    out("")
    empty_format = "{0xFFFFu, 0u, {0u, 0u, 0u, 0u, 0u, 0u}, 0xFFFFu, 0xFFFFu, 0u}"
    out(_array("th_phone_format", "th_phone_formats", format_rows or [empty_format], raw=True))
    out(_array("th_phone_format", "th_phone_alt_formats", alternate_rows or [empty_format], raw=True))
    out("static const char th_phone_templates[] =")
    for text in templates or [""]:
        out(f'    "{text}\\0"')
    out("    ;")
    out("")
    out("static const th_phone_prefix_tag th_phone_prefix_tags[] = {")
    for tag in tags:
        out(f'    {{{len(tag.literal)}u, {tag.group}u, {tag.program}u, "{tag.literal}"}},')
    out("};")
    out("")
    group_regions: list[int] = []
    out("static const th_phone_group th_phone_groups[] = {")
    for group_index, group in enumerate(tables.groups):
        first = len(group_regions)
        group_regions.extend(
            member | (0x8000 if group.routed[position] else 0) for position, member in enumerate(group.members)
        )
        out(
            f"    {{{group.country_code}u, {router_index.get(group_index, 0xFFFF)}u, {first}u, "
            f"{len(group.members)}u, {group.main}u, {alternate_spans[group_index][0]}u, "
            f"{alternate_spans[group_index][1]}u}},"
        )
    out("};")
    out("")
    out(_array("uint16_t", "th_phone_group_regions", group_regions))
    for width in (1, 2, 3):
        table = [0xFF] * (10**width)
        for country_code, group_index in tables.group_of_code.items():
            if len(str(country_code)) == width:
                table[country_code] = group_index
        out(_array("uint8_t", f"th_phone_cc{width}", table))
    out(
        _array(
            "th_phone_nfa_op",
            "th_phone_nfa_ops",
            [f"{{{op}u, {arg}u, {nxt}u, {alt}u}}" for op, arg, nxt, alt in ops] or ["{0u, 0u, 0u, 0u}"],
            raw=True,
        )
    )
    out(
        _array(
            "th_phone_nfa",
            "th_phone_nfas",
            [f"{{{first}u, {count}u, {start}u}}" for first, count, start in program_table] or ["{0u, 0u, 0u}"],
            raw=True,
        )
    )
    out(_array("uint16_t", "th_phone_nfa_classes", class_masks or [0]))
    out(_array("uint8_t", "th_phone_nd_pages", tables.unicode.nd_pages))
    out(
        _array(
            "uint32_t",
            "th_phone_nd_ranges",
            [value for first, last, zero in tables.unicode.nd_ranges for value in (first, last, zero)],
        )
    )
    out(
        _array(
            "uint32_t", "th_phone_currency_ranges", [value for pair in tables.unicode.currency_ranges for value in pair]
        )
    )
    out(_array("uint32_t", "th_phone_latin_ranges", [value for pair in tables.unicode.latin_ranges for value in pair]))
    out(
        _array("uint32_t", "th_phone_letter_ranges", [value for pair in tables.unicode.letter_ranges for value in pair])
    )
    out(
        _array("uint32_t", "th_phone_number_ranges", [value for pair in tables.unicode.number_ranges for value in pair])
    )
    out(
        _array(
            "uint32_t",
            "th_phone_ext_classes",
            [value for first, last, symbol in tables.extension.classes for value in (first, last, symbol)],
        )
    )
    out(f"#define TH_PHONE_ND_RANGE_COUNT {len(tables.unicode.nd_ranges)}")
    out(f"#define TH_PHONE_CURRENCY_RANGE_COUNT {len(tables.unicode.currency_ranges)}")
    out(f"#define TH_PHONE_LATIN_RANGE_COUNT {len(tables.unicode.latin_ranges)}")
    out(f"#define TH_PHONE_LETTER_RANGE_COUNT {len(tables.unicode.letter_ranges)}")
    out(f"#define TH_PHONE_NUMBER_RANGE_COUNT {len(tables.unicode.number_ranges)}")
    out(f"#define TH_PHONE_EXT_CLASS_COUNT {len(tables.extension.classes)}")
    out(f"#define TH_PHONE_LABEL_COUNT {len(DEFAULT_LABELS)}")
    out("static const char *const th_phone_default_labels[] = {")
    out("    " + ", ".join(f'"{label}"' for label in DEFAULT_LABELS) + ",")
    out("};")
    out("")
    out("#endif /* TURBOHTML_PHONE_TABLE_H */")
    return (
        "\n".join(lines) + "\n",
        len(rows8) + 2 * len(rows16) + 2 * len(accepts) + 4 * len(ops) + 12 * len(tables.extension.classes),
    )


def _rows(
    dfas: list[tuple[Dfa | PriorityDfa, int]],
) -> tuple[list[int], list[int], list[int], list[tuple[int, int, int, int, int]]]:
    """Lay every automaton out as narrow or wide rows plus one accept word per state."""
    rows8: list[int] = []
    rows16: list[int] = []
    accepts: list[int] = []
    descriptors: list[tuple[int, int, int, int, int]] = []
    for automaton, symbols in dfas:
        states = len(automaton.next)
        wide = states > 255
        target = rows16 if wide else rows8
        descriptors.append((len(target), len(accepts), states, symbols, int(wide)))
        for state in range(states):
            target.extend(automaton.next[state][symbol] for symbol in range(symbols))
            if isinstance(automaton, PriorityDfa):
                word = (0x8000 if automaton.accept[state] else 0) | (0x4000 if automaton.final[state] else 0)
                word |= automaton.offset_back[state] << 8
            else:
                word = automaton.accepts[state]
            accepts.append(word)
    return rows8, rows16, accepts, descriptors


def _array(ctype: str, name: str, values: list, *, raw: bool = False) -> str:
    rendered = [str(value) if raw else f"{value}u" for value in values]
    body = ",\n    ".join(", ".join(rendered[index : index + 16]) for index in range(0, len(rendered), 16))
    return f"static const {ctype} {name}[] = {{\n    {body},\n}};\n"


def generate(out_path: Path, local: Path | None) -> None:
    """Write the generated header to *out_path*."""
    tables = compile_tables(fetch_sources(local))
    header, payload = emit_header(tables)
    if payload > MAX_TABLE_BYTES:
        msg = f"the tables encode to {payload} bytes, over the {MAX_TABLE_BYTES} gate"
        raise GenerationError(msg)
    out_path.write_text(header, encoding="utf-8")
    print(
        f"wrote {out_path}: {len(tables.regions)} regions, {len(tables.groups)} calling codes, "
        f"lag {tables.max_lag}, prefix digits {tables.max_prefix_digits}, {payload // 1024} KiB of tables, "
        f"{len(header.encode()) // 1024} KiB of header text"
    )


__all__ = [
    "DEFAULT_LABELS",
    "GENERAL_BIT",
    "JAVA_CONSTANTS",
    "LIBPHONENUMBER_TAG",
    "MAX_NSN",
    "MAX_TABLE_BYTES",
    "MIN_NSN",
    "SOURCES",
    "TYPES",
    "Format",
    "GenerationError",
    "Group",
    "Region",
    "RegionTables",
    "Tables",
    "TypeDesc",
    "check_java_constants",
    "compile_groups",
    "compile_region",
    "compile_tables",
    "emit_header",
    "fetch_sources",
    "generate",
    "parse_alternate_formats",
    "parse_formats",
    "parse_groups",
    "parse_metadata",
    "parse_template",
    "parse_transform",
    "parse_unicode",
    "router_replay",
]


if __name__ == "__main__":
    arguments = sys.argv[1:]
    local_dir = None
    if "--sources" in arguments:
        position = arguments.index("--sources")
        local_dir = Path(arguments[position + 1])
        del arguments[position : position + 2]
    if len(arguments) != 1:
        msg = "usage: generate_phone.py OUTPUT_HEADER [--sources DIR]"
        raise SystemExit(msg)
    generate(Path(arguments[0]), local_dir)
