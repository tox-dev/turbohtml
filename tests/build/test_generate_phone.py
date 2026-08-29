from __future__ import annotations

import hashlib
import itertools
import random
import re
import string
import xml.etree.ElementTree as ET  # ruff:ignore[suspicious-xml-etree-import]  # literal fixtures, never untrusted input
from typing import TYPE_CHECKING, Final

import generate_phone
import pytest
from generate_phone import (
    JAVA_CONSTANTS,
    MAX_TABLE_BYTES,
    Format,
    GenerationError,
    TypeDesc,
    check_java_constants,
    compile_groups,
    compile_region,
    compile_tables,
    emit_header,
    fetch_sources,
    generate,
    parse_alternate_formats,
    parse_formats,
    parse_groups,
    parse_metadata,
    parse_template,
    parse_transform,
    parse_unicode,
    router_replay,
)
from phone_dfa import (
    UnsupportedPatternError,
    accepted_lengths,
    compile_dfa,
    compile_priority_dfa,
    compile_program,
    lag,
    longest_accept,
    match_end,
    max_threads,
    minimize,
    pike_spans,
    shortest_accept,
    union_program,
)

from turbohtml.clean import DEFAULT_PHONE_LABELS

if TYPE_CHECKING:
    from pathlib import Path

    from phone_dfa import Dfa
    from pytest_mock import MockerFixture

_DIGITS: Final = string.digits


def _accepts(dfa: Dfa, digits: str) -> int:
    state = 1
    for char in digits:
        state = dfa.next[state][int(char)]
        if state == 0:
            return 0
    return dfa.accepts[state]


def _strings(alphabet: str, longest: int) -> list[str]:
    return ["".join(item) for length in range(longest + 1) for item in itertools.product(alphabet, repeat=length)]


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param(r"[2-9]\d{2}", id="class-and-count"),
        pytest.param(r"1(?:[02-9]\d|1[02-9])\d", id="nested-alternation"),
        pytest.param(r"0(?:1|22)3|0", id="alternatives-of-different-length"),
        pytest.param(r"(?:12|1)3?", id="optional-suffix"),
        pytest.param(r"\d{2,4}", id="bounded-repeat"),
        pytest.param(r"(?:1[02]){1,3}", id="repeated-group"),
        pytest.param(r"[13579]?[02468]", id="optional-class"),
    ],
)
def test_dfa_agrees_with_re_on_every_short_string(pattern: str) -> None:
    dfa = compile_dfa(compile_program(pattern, capture=False))
    assert {digits for digits in _strings(_DIGITS, 5) if _accepts(dfa, digits)} == {
        digits for digits in _strings(_DIGITS, 5) if re.fullmatch(pattern, digits)
    }


def test_union_keeps_each_program_label() -> None:
    dfa = compile_dfa(
        union_program([
            compile_program(r"1\d", label=1, capture=False),
            compile_program(r"\d2", label=2, capture=False),
        ])
    )
    assert (_accepts(dfa, "12"), _accepts(dfa, "13"), _accepts(dfa, "32"), _accepts(dfa, "33")) == (3, 1, 2, 0)


def test_minimize_is_idempotent_and_keeps_the_language() -> None:
    once = minimize(compile_dfa(compile_program(r"1(?:2|3)4|1(?:2|3)5", capture=False)))
    assert minimize(once) == once
    assert {digits for digits in _strings("12345", 4) if _accepts(once, digits)} == {"124", "134", "125", "135"}


def test_accepted_lengths_reports_the_lengths_with_their_labels() -> None:
    assert accepted_lengths(compile_dfa(compile_program(r"\d{2,4}|1\d{6}", capture=False))) == {2: 1, 3: 1, 4: 1, 7: 1}


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param(r"0(8(?:[1-46-8]|5\d\d))?", id="optional-group-with-lag"),
        pytest.param(r"0|80?", id="shorter-alternative-first"),
        pytest.param(r"([457]\d{6})$|1", id="end-anchored-alternative"),
        pytest.param(r"0(?:1|22)3|0", id="longer-alternative-first"),
        pytest.param(r"0(?:0|11)", id="shared-prefix"),
        pytest.param(r"1(?:5|6)?", id="optional-suffix"),
    ],
)
def test_priority_match_end_is_javas_leftmost_first_end(pattern: str) -> None:
    dfa = compile_priority_dfa(compile_program(pattern))
    for digits in _strings("015678", 6):
        matched = re.match(pattern, digits)
        assert match_end(dfa, [int(char) for char in digits]) == (matched.end() if matched else -1), digits


def test_priority_bounds() -> None:
    dfa = compile_priority_dfa(compile_program(r"0(8(?:[1-46-8]|5\d\d))?"))
    assert (shortest_accept(dfa), longest_accept(dfa), lag(dfa)) == (1, 5, 3)


@pytest.mark.parametrize(
    ("pattern", "digits", "expected"),
    [
        pytest.param(r"([457]\d{6})$|1", "4601234", {1: (0, 7)}, id="anchored-group"),
        pytest.param(r"([457]\d{6})$|1", "1", {}, id="group-not-taken"),
        pytest.param(r"([457]\d{6})$|1", "12", None, id="no-path-to-the-end"),
        pytest.param(r"9?(11|[2368]\d)", "911", {1: (1, 3)}, id="optional-prefix"),
        pytest.param(r"0?(?:(11|[2368]\d)(?:15)?)?", "", {}, id="empty-match"),
    ],
)
def test_pike_spans_follow_javas_path(pattern: str, digits: str, expected: dict[int, tuple[int, int]] | None) -> None:
    assert pike_spans(compile_program(pattern), [int(char) for char in digits]) == expected


def test_max_threads_counts_the_live_threads() -> None:
    assert max_threads(compile_program(r"0?(11|[2368]\d)")) == 3


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param(r"\w", id="word-class"),
        pytest.param(r"(\d)\1", id="backreference"),
        pytest.param(r"(?=1)\d", id="lookahead"),
        pytest.param(r"\d+", id="unbounded-repeat"),
        pytest.param(r"^1", id="start-anchor"),
    ],
)
def test_unsupported_constructs_are_refused(pattern: str) -> None:
    with pytest.raises(UnsupportedPatternError):
        compile_program(pattern)


def test_unbounded_repeat_is_allowed_only_on_request() -> None:
    dfa = compile_dfa(compile_program(r"1\d+", capture=False, allow_unbounded=True))
    assert (_accepts(dfa, "1"), _accepts(dfa, "12"), _accepts(dfa, "1234567")) == (0, 1, 1)


_METADATA = b"""<?xml version="1.0" encoding="UTF-8"?>
<phoneNumberMetadata>
  <territories>
    <territory id="XA" countryCode="1" mainCountryForCode="true" internationalPrefix="011" nationalPrefix="1"
               nationalPrefixOptionalWhenFormatting="true">
      <availableFormats>
        <numberFormat pattern="(\\d{3})(\\d{3})(\\d{4})">
          <leadingDigits>[2-9]</leadingDigits><format>$1 $2 $3</format></numberFormat>
      </availableFormats>
      <generalDesc><nationalNumberPattern>[2-9]\\d{9}</nationalNumberPattern></generalDesc>
      <fixedLine><nationalNumberPattern>[2-9]\\d{2}[2-9]\\d{6}</nationalNumberPattern>
        <possibleLengths national="10" localOnly="7"/><exampleNumber>2012345678</exampleNumber></fixedLine>
      <mobile><nationalNumberPattern>[2-9]\\d{2}[2-9]\\d{6}</nationalNumberPattern>
        <possibleLengths national="10" localOnly="7"/><exampleNumber>2012345678</exampleNumber></mobile>
      <tollFree><nationalNumberPattern>8(?:00|88)[2-9]\\d{6}</nationalNumberPattern>
        <possibleLengths national="10"/><exampleNumber>8002345678</exampleNumber></tollFree>
    </territory>
    <territory id="XB" countryCode="1" leadingDigits="268" internationalPrefix="011" nationalPrefix="1"
               nationalPrefixForParsing="([457]\\d{6})$|1" nationalPrefixTransformRule="268$1">
      <generalDesc><nationalNumberPattern>(?:268|[58]\\d\\d)\\d{7}</nationalNumberPattern></generalDesc>
      <fixedLine><nationalNumberPattern>268(?:4(?:6[0-38]|84)|56[0-2])\\d{4}</nationalNumberPattern>
        <possibleLengths national="10" localOnly="7"/><exampleNumber>2684601234</exampleNumber></fixedLine>
      <tollFree><nationalNumberPattern>8(?:00|88)[2-9]\\d{6}</nationalNumberPattern>
        <possibleLengths national="10"/><exampleNumber>8002345678</exampleNumber></tollFree>
    </territory>
    <territory id="XC" countryCode="49" internationalPrefix="00" nationalPrefix="0"
               nationalPrefixFormattingRule="$NP$FG" preferredExtnPrefix=" Anexo ">
      <availableFormats>
        <numberFormat pattern="(\\d{2})(\\d{3,11})">
          <leadingDigits>3[02]|40|[68]9</leadingDigits><format>$1 $2</format>
          <intlFormat>$1-$2</intlFormat></numberFormat>
        <numberFormat pattern="(\\d{3})(\\d{5,8})" nationalPrefixFormattingRule="($FG)">
          <format>$1 $2</format><intlFormat>NA</intlFormat></numberFormat>
      </availableFormats>
      <generalDesc><nationalNumberPattern>[1-9]\\d{4,13}</nationalNumberPattern></generalDesc>
      <fixedLine><nationalNumberPattern>3[02]\\d{6,9}|[1-9]\\d{6,12}</nationalNumberPattern>
        <possibleLengths national="[7-14]" localOnly="[5-6]"/><exampleNumber>30123456</exampleNumber></fixedLine>
      <mobile><nationalNumberPattern>15\\d{9}|1(?:6[023]|7\\d)\\d{7,8}</nationalNumberPattern>
        <possibleLengths national="[10-11]"/><exampleNumber>15123456789</exampleNumber></mobile>
    </territory>
  </territories>
</phoneNumberMetadata>
"""


def test_parse_metadata_reads_territories_and_derives_general_lengths() -> None:
    regions = parse_metadata(_METADATA)
    assert [region.code for region in regions] == ["XA", "XB", "XC"]
    main, routed, german = regions
    assert (main.main, main.country_code, main.national_prefix, main.idd_pattern) == (True, 1, "1", "011")
    assert (routed.leading_digits, routed.prefix_pattern, routed.transform) == ("268", r"([457]\d{6})$|1", "268$1")
    assert (main.possible_national, main.possible_local_only) == (frozenset({10}), frozenset({7}))
    assert (german.possible_national, german.possible_local_only) == (frozenset(range(7, 15)), frozenset({5, 6}))
    assert main.examples == {"fixedLine": "2012345678", "mobile": "2012345678", "tollFree": "8002345678"}


def test_parse_formats_resolves_the_prefix_rules_and_templates() -> None:
    main, _routed, german = parse_metadata(_METADATA)
    assert main.formats == [
        Format(
            "[2-9]", r"(\d{3})(\d{3})(\d{4})", ((3, 3), (3, 3), (4, 4)), "$1 $2 $3", "$1 $2 $3", requires_prefix=False
        ),
    ]
    assert german.formats == [
        Format("3[02]|40|[68]9", r"(\d{2})(\d{3,11})", ((2, 2), (3, 11)), "0$1 $2", "$1-$2", requires_prefix=True),
        Format(None, r"(\d{3})(\d{5,8})", ((3, 3), (5, 8)), "($1) $2", None, requires_prefix=False),
    ]
    assert (main.ext_prefix, german.ext_prefix) == (None, " Anexo ")


def test_parse_formats_keeps_the_last_leading_digits_and_needs_a_pattern() -> None:
    territory = ET.fromstring(  # ruff:ignore[suspicious-xml-element-tree-usage]  # a literal fixture, not untrusted input
        '<territory id="XD" nationalPrefix="0" nationalPrefixFormattingRule="$NP $FG"><availableFormats>'
        '<numberFormat pattern="(\\d{4})"><leadingDigits>1</leadingDigits><leadingDigits>12</leadingDigits>'
        "<format>$1</format></numberFormat></availableFormats></territory>"
    )
    assert [(item.leading, item.national, item.requires_prefix) for item in parse_formats(territory, "0")] == [
        ("12", "0 $1", True)
    ]
    number_format = territory.find("availableFormats/numberFormat")
    assert number_format is not None
    number_format.attrib.pop("pattern")
    with pytest.raises(GenerationError, match="needs a pattern"):
        parse_formats(territory, "0")


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        pytest.param(
            b'<territory id="XE" countryCode="7"><generalDesc><nationalNumberPattern>\\d{5}</nationalNumberPattern>'
            b'<possibleLengths national="5"/></generalDesc></territory>',
            "no possibleLengths",
            id="general-desc-with-lengths",
        ),
        pytest.param(
            b'<territory id="XE" countryCode="7"><generalDesc><nationalNumberPattern>\\d{5}</nationalNumberPattern>'
            b"</generalDesc><mobile><nationalNumberPattern>\\d{5}</nationalNumberPattern></mobile></territory>",
            "needs a pattern and possibleLengths",
            id="type-without-lengths",
        ),
        pytest.param(b"<territories/>", "no territories", id="empty"),
    ],
)
def test_parse_metadata_rejects_malformed_territories(xml: bytes, message: str) -> None:
    with pytest.raises(GenerationError, match=message):
        parse_metadata(b"<phoneNumberMetadata><territories>" + xml + b"</territories></phoneNumberMetadata>")


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        pytest.param(r"(\d{3})(\d{4})", ((3, 3), (4, 4)), id="fixed-lengths"),
        pytest.param(r"(\d{2})(\d{3,4})", ((2, 2), (3, 4)), id="length-range"),
        pytest.param(r"(\d)(\d{2,15})", ((1, 1), (2, 15)), id="bare-digit-and-the-longest-group"),
    ],
)
def test_parse_groups_reads_the_bounds(pattern: str, expected: tuple[tuple[int, int], ...]) -> None:
    assert parse_groups("XA", pattern) == expected


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        pytest.param(r"([2-9]\d{2})(\d{4})", "not a run of digit groups", id="class"),
        pytest.param(r"(\d{2}|\d{3})(\d{4})", "not a run of digit groups", id="alternation"),
        pytest.param(r"\d(\d{4})", "not a run of digit groups", id="digit-outside-a-group"),
        pytest.param(r"(\d)(\d)(\d)(\d)(\d)(\d)(\d)", "exceeds the group bounds", id="seven-groups"),
        pytest.param(r"(\d{16})", "exceeds the group bounds", id="sixteen-digit-group"),
        pytest.param(r"(\d{0,3})", "exceeds the group bounds", id="empty-group"),
        pytest.param(r"(\d{4,3})", "exceeds the group bounds", id="inverted-bounds"),
    ],
)
def test_parse_groups_refuses_other_shapes(pattern: str, message: str) -> None:
    with pytest.raises(GenerationError, match=message):
        parse_groups("XA", pattern)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param(None, "is empty", id="missing"),
        pytest.param("", "is empty", id="empty"),
        pytest.param("$1 " * 9, "longer than", id="long"),
        pytest.param("123", "references a missing group", id="no-reference"),
        pytest.param("$1 $3", "references a missing group", id="reference-past-the-groups"),
        pytest.param("$1/$2", "unexpected character", id="slash"),
        pytest.param("$1 $2 ", "does not end with a group", id="trailing-separator"),
    ],
)
def test_parse_template_refuses_bad_templates(text: str | None, message: str) -> None:
    with pytest.raises(GenerationError, match=message):
        parse_template("XA", text, 2)


_ALTERNATE_FORMATS = b"""<?xml version="1.0" encoding="UTF-8"?>
<phoneNumberMetadata>
  <territories>
    <territory countryCode="49">
      <availableFormats>
        <numberFormat pattern="(\\d{3})(\\d{5,9})">
          <leadingDigits>[3-9]</leadingDigits><format>$1 $2</format></numberFormat>
        <numberFormat pattern="(\\d{4})(\\d{4,8})"><format>$1 $2</format></numberFormat>
      </availableFormats>
    </territory>
  </territories>
</phoneNumberMetadata>
"""


def test_parse_alternate_formats_reads_each_calling_code() -> None:
    assert parse_alternate_formats(_ALTERNATE_FORMATS) == {
        49: [
            Format("[3-9]", r"(\d{3})(\d{5,9})", ((3, 3), (5, 9)), "$1 $2", "$1 $2", requires_prefix=False),
            Format(None, r"(\d{4})(\d{4,8})", ((4, 4), (4, 8)), "$1 $2", "$1 $2", requires_prefix=False),
        ]
    }


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        pytest.param(
            b'<territory countryCode="49"/><territory countryCode="49"/>', "listed twice", id="duplicate-code"
        ),
        pytest.param(
            b'<territory countryCode="49"><availableFormats><numberFormat pattern="(\\d{4})"><leadingDigits>1'
            b"</leadingDigits><leadingDigits>12</leadingDigits><format>$1</format></numberFormat></availableFormats>"
            b"</territory>",
            "several leadingDigits",
            id="several-leading-digits",
        ),
    ],
)
def test_parse_alternate_formats_rejects_malformed_territories(xml: bytes, message: str) -> None:
    with pytest.raises(GenerationError, match=message):
        parse_alternate_formats(b"<phoneNumberMetadata><territories>" + xml + b"</territories></phoneNumberMetadata>")


def test_compile_tables_refuses_alternate_formats_for_an_unassigned_code(sources: dict[str, bytes]) -> None:
    sources["PhoneNumberAlternateFormats.xml"] = _ALTERNATE_FORMATS.replace(b'countryCode="49"', b'countryCode="99"')
    with pytest.raises(GenerationError, match=r"\+99: alternate formats for a calling code"):
        compile_tables(sources)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        pytest.param("9$1", ("9", 1), id="literal-and-group"),
        pytest.param("$1", ("", 1), id="group-only"),
        pytest.param("0549$1", ("0549", 1), id="four-digit-literal"),
        pytest.param(None, ("", 0), id="absent"),
    ],
)
def test_parse_transform_accepts_the_grammar(rule: str | None, expected: tuple[str, int]) -> None:
    assert parse_transform(rule, compile_program(r"0?(9)?(11)")) == expected


@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("$1$2", id="two-groups"),
        pytest.param("$1 5", id="trailing-text"),
        pytest.param("9$", id="no-group"),
        pytest.param("$0", id="group-zero"),
        pytest.param("$3", id="group-past-the-pattern"),
        pytest.param("12345$1", id="literal-too-long"),
    ],
)
def test_parse_transform_rejects_other_rules(rule: str) -> None:
    with pytest.raises(GenerationError):
        parse_transform(rule, compile_program(r"0?(9)?(11)"))


def test_default_labels_match_the_runtime() -> None:
    assert tuple(sorted(generate_phone.DEFAULT_LABELS)) == DEFAULT_PHONE_LABELS


def test_java_constants_must_occur_verbatim() -> None:
    every = "\n".join(literal for literals in JAVA_CONSTANTS.values() for literal in literals)
    check_java_constants(every)
    with pytest.raises(GenerationError, match="MAX_LENGTH_FOR_NSN"):
        check_java_constants(every.replace("MAX_LENGTH_FOR_NSN = 17", "MAX_LENGTH_FOR_NSN = 16"))


def _digit_rows(zero: int, name: str) -> list[str]:
    return [f"{zero + value:04X};{name} DIGIT {value};Nd;0;EN;;{value};{value};{value};N;;;;;" for value in range(10)]


_UNICODE_DATA = "\n".join([
    "0025;PERCENT SIGN;Po;0;ET;;;;;N;;;;;",
    *_digit_rows(0x30, "DIGIT"),
    "0041;LATIN CAPITAL LETTER A;Lu;0;L;;;;;N;;;;0061;",
    "0061;LATIN SMALL LETTER A;Ll;0;L;;;;;N;;;0041;;0041",
    "004B;LATIN CAPITAL LETTER K;Lu;0;L;;;;;N;;;;006B;",
    "006B;LATIN SMALL LETTER K;Ll;0;L;;;;;N;;;004B;;004B",
    "0024;DOLLAR SIGN;Sc;0;ET;;;;;N;;;;;",
    "00A3;POUND SIGN;Sc;0;ET;;;;;N;;;;;",
    "00C9;LATIN CAPITAL LETTER E WITH ACUTE;Lu;0;L;0045 0301;;;;N;;;;00E9;",
    "0301;COMBINING ACUTE ACCENT;Mn;230;NSM;;;;;N;;;;;",
    "03B1;GREEK SMALL LETTER ALPHA;Ll;0;L;;;;;N;;;0391;;0391",
    *_digit_rows(0x660, "ARABIC-INDIC"),
    "212A;KELVIN SIGN;Lu;0;L;004B;;;;N;DEGREES KELVIN;;;006B;",
    "4E00;<CJK Ideograph, First>;Lo;0;L;;;;;N;;;;;",
    "9FFF;<CJK Ideograph, Last>;Lo;0;L;;;;;N;;;;;",
    *_digit_rows(0x1D7CE, "MATHEMATICAL BOLD"),
])
_BLOCKS = (
    "# Blocks-16.0.0.txt\n"
    "0000..007F; Basic Latin\n"
    "0080..00FF; Latin-1 Supplement\n"
    "0100..017F; Latin Extended-A\n"
    "0180..024F; Latin Extended-B\n"
    "0300..036F; Combining Diacritical Marks\n"
    "0370..03FF; Greek and Coptic\n"
    "1E00..1EFF; Latin Extended Additional\n"
    "4E00..9FFF; CJK Unified Ideographs"
)


def test_parse_unicode_tables() -> None:
    tables = parse_unicode(_UNICODE_DATA, _BLOCKS)
    assert tables.nd_ranges == [(0x30, 0x39, 0x30), (0x660, 0x669, 0x660), (0x1D7CE, 0x1D7D7, 0x1D7CE)]
    assert tables.currency_ranges == [(0x24, 0x24), (0xA3, 0xA3)]
    assert tables.latin_ranges == [(0x41, 0x41), (0x4B, 0x4B), (0x61, 0x61), (0x6B, 0x6B), (0xC9, 0xC9), (0x301, 0x301)]
    assert (0x41, 0x41) in tables.letter_ranges
    assert (0x301, 0x301) not in tables.letter_ranges
    assert (0x30, 0x39) in tables.number_ranges
    assert tables.case_classes[0x6B] == frozenset({0x4B, 0x6B, 0x212A})
    assert tables.nd_pages[0] & 1
    assert tables.nd_pages[(0x1D7CE >> 8) >> 3] >> ((0x1D7CE >> 8) & 7) & 1


def test_parse_unicode_needs_every_latin_block() -> None:
    with pytest.raises(GenerationError, match="Latin block"):
        parse_unicode(_UNICODE_DATA, "0000..007F; Basic Latin")


@pytest.fixture
def sources() -> dict[str, bytes]:
    return {
        "PhoneNumberMetadata.xml": _METADATA,
        "PhoneNumberAlternateFormats.xml": _ALTERNATE_FORMATS,
        "PhoneNumberUtil.java": "\n".join(
            literal for literals in JAVA_CONSTANTS.values() for literal in literals
        ).encode(),
        "Blocks.txt": _BLOCKS.encode(),
        "UnicodeData.txt": _UNICODE_DATA.encode(),
    }


@pytest.fixture
def sources_dir(tmp_path: Path, sources: dict[str, bytes]) -> Path:
    for name, raw in sources.items():
        (tmp_path / name).write_bytes(raw)
    return tmp_path


@pytest.fixture
def pinned_sources_dir(sources_dir: Path, sources: dict[str, bytes], mocker: MockerFixture) -> Path:
    mocker.patch.object(
        generate_phone,
        "SOURCES",
        {
            name: (url, hashlib.sha256(sources[name]).hexdigest())
            for name, (url, _digest) in generate_phone.SOURCES.items()
        },
    )
    return sources_dir


def test_compile_region_and_groups_on_the_fixture() -> None:
    rng = random.Random(1)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # the self-check sampler
    programs = []
    compiled = [compile_region(region, programs, rng) for region in parse_metadata(_METADATA)]
    main, routed, german = compiled
    assert (main.floor_valid, main.floor_possible) == (10, 7)
    assert routed.tag is not None
    assert (routed.tag.literal, routed.tag.group) == ("268", 1)
    assert german.prefix is not None
    assert german.idd is not None
    assert [item.format.requires_prefix for item in german.formats] == [True, False]
    groups, group_of_code = compile_groups(compiled, rng)
    assert group_of_code == {1: 0, 49: 1}
    assert (groups[0].members, groups[0].main, groups[0].routed) == ([0, 1], 0, [False, True])
    assert groups[0].router is not None
    assert router_replay(groups[0].router) == 3
    assert groups[1].router is None


def test_compile_region_refuses_a_type_pattern_longer_than_its_lengths() -> None:
    region = parse_metadata(_METADATA)[0]
    region.types["mobile"] = TypeDesc(r"[2-9]\d{9,10}", frozenset({10}), frozenset())
    with pytest.raises(GenerationError, match="XA/mobile: the pattern accepts lengths \\[11\\]"):
        compile_region(region, [], random.Random(1))  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # sampler


def test_emit_header_on_the_fixture(sources: dict[str, bytes]) -> None:
    header, payload = emit_header(compile_tables(sources))
    assert "#define TH_PHONE_REGION_COUNT 3" in header
    assert "#define TH_PHONE_GROUP_COUNT 2" in header
    assert '{"XA", 2u, 1u, 0u' in header
    assert '{"001"' not in header
    assert "th_phone_formats[]" in header
    assert "th_phone_alt_formats[]" in header
    assert "#define TH_PHONE_EXT_PARSING_DFA" in header
    assert '"$1 $2 $3\\0"' in header
    assert '" Anexo \\0"' in header
    assert 0 < payload < MAX_TABLE_BYTES


def test_generate_writes_the_header_from_local_sources(pinned_sources_dir: Path) -> None:
    out = pinned_sources_dir / "phone_table.h"
    generate(out, pinned_sources_dir)
    assert "#define TH_PHONE_REGION_COUNT 3" in out.read_text()


def test_fetch_sources_rejects_a_hash_mismatch(sources_dir: Path) -> None:
    with pytest.raises(GenerationError, match="not the pinned"):
        fetch_sources(sources_dir)


def test_generate_enforces_the_size_gate(pinned_sources_dir: Path, mocker: MockerFixture) -> None:
    mocker.patch.object(generate_phone, "MAX_TABLE_BYTES", 1)
    with pytest.raises(GenerationError, match="over the 1 gate"):
        generate(pinned_sources_dir / "phone_table.h", pinned_sources_dir)
