"""
Reference model of the phone-number recognizer, run on the generator's in-memory tables.

The C recognizer in ``_c/clean/phone.c`` is a port of this module: every decision it makes (how a run of digits is
segmented, which readings a configured region contributes, how libphonenumber's parse order picks the national prefix,
the country code and the region, which candidates are poison) is written here first, in the order ``parseHelper`` makes
it, and validated against the ``phonenumbers`` oracle by ``tools/phone_differential.py``. The model favors clarity over
speed; the C port keeps its structure and decision order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from generate_phone import GENERAL_BIT, MAX_NSN, MIN_NSN, TYPES, Group, RegionTables, Tables
from phone_dfa import Dfa, match_end, pike_spans

if TYPE_CHECKING:
    from collections.abc import Sequence

MAX_GROUPS: Final = 21
MAX_GROUP_DIGITS: Final = 20
MAX_LEAD_PUNCTUATION: Final = 4
MAX_RUN_CHARS: Final = 250
MAX_LEADING_ZEROS: Final = 10
TYPE_UNKNOWN: Final = len(TYPES) + 1
TYPE_FIXED_LINE_OR_MOBILE: Final = len(TYPES)

# libphonenumber's VALID_PUNCTUATION, the characters that may sit between digit groups, and its lead characters.
PUNCTUATION: Final = frozenset(
    "-x\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u30fc\uff0d\uff0e\uff0f \u00a0\u00ad\u200b\u2060\u3000()"
    "\uff08\uff09\uff3b\uff3d.[]/~\u2053\u223c\uff5e"
)
PLUS: Final = frozenset("+\uff0b")
OPENERS: Final = frozenset("([\uff08\uff3b")
CLOSERS: Final = frozenset(")]\uff09\uff3d")
EXTENSION_MARKERS: Final = frozenset("xX\uff58#\uff03~\uff5e")

# Java's \p{Z}: the space separators plus the line and paragraph separators.
_SPACES: Final = "\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"

# PhoneNumberMatcher.INNER_MATCHES: after the whole candidate fails, the splits parseAndVerify is retried on, in this
# order: the part after a slash, each bracketed part, the parts around a spaced hyphen, a wide hyphen, a dot, a space.
_INNER_MATCHES: Final = (
    re.compile(r"/+(.*)"),
    re.compile(r"(\([^(]*)"),
    re.compile(rf"(?:[{_SPACES}]-|-[{_SPACES}])[{_SPACES}]*(.+)"),
    re.compile(rf"[\u2012-\u2015\uff0d][{_SPACES}]*(.+)"),
    re.compile(rf"\.+[{_SPACES}]*([^.]+)"),
    re.compile(rf"[{_SPACES}]+([^{_SPACES}]+)"),
)

# getNumberTypeHelper's precedence: the first matching type wins, fixedLine becomes FIXED_LINE_OR_MOBILE when mobile
# also matches.
_TYPE_PRECEDENCE: Final = (
    TYPES.index("premiumRate"),
    TYPES.index("tollFree"),
    TYPES.index("sharedCost"),
    TYPES.index("voip"),
    TYPES.index("personalNumber"),
    TYPES.index("pager"),
    TYPES.index("uan"),
    TYPES.index("voicemail"),
    TYPES.index("fixedLine"),
    TYPES.index("mobile"),
)


@dataclass(frozen=True)
class Config:
    """The recognizer's settings; ``regions`` is ordered, empty means ``+`` numbers only."""

    regions: tuple[str, ...] = ()
    require_valid: bool = True
    require_separators: bool = False
    skip_card_numbers: bool = True
    require_national_prefix: bool = True
    type_mask: int = 0x7FF
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    """One recognized phone number: its text span, parsed value, and classification."""

    start: int
    end: int
    country_code: int
    nsn: str
    extension: str | None
    region: str | None
    type: int

    @property
    def international_number(self) -> str:
        """The number as ``+<country_code><nsn>``, ignoring formatting."""
        return f"+{self.country_code}{self.nsn}"


@dataclass
class _Group:
    digits: str
    separator: str
    start: int
    end: int


@dataclass
class _Run:
    start: int
    end: int
    plus: bool
    groups: list[_Group]
    extension: str | None
    extension_end: int
    poison: set[int]
    second_number_cut: int


@dataclass(frozen=True)
class _Reading:
    country_code: int
    nsn: str
    region_index: int | None
    type: int
    general: bool


def digit_value(tables: Tables, char: str) -> int:
    """Return the decimal value of ``char`` under Unicode ``Nd``, or -1."""
    code = ord(char)
    if 0x30 <= code <= 0x39:
        return code - 0x30
    page = code >> 8
    if not tables.unicode.nd_pages[page >> 3] >> (page & 7) & 1:
        return -1
    for first, last, zero in tables.unicode.nd_ranges:
        if first <= code <= last:
            return code - zero
    return -1


def _in_ranges(code: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(first <= code <= last for first, last in ranges)


def is_latin_letter(tables: Tables, char: str) -> bool:
    """PhoneNumberMatcher.isLatinLetter: a letter or nonspacing mark inside the six Latin blocks."""
    return _in_ranges(ord(char), tables.unicode.latin_ranges)


def is_invalid_punctuation(tables: Tables, char: str) -> bool:
    """PhoneNumberMatcher.isInvalidPunctuationSymbol: ``%`` or a currency symbol."""
    return char == "%" or _in_ranges(ord(char), tables.unicode.currency_ranges)


class Recognizer:
    """The recognizer over one table set and one configuration."""

    def __init__(self, tables: Tables, config: Config) -> None:
        """Build region indices and validity floors from ``tables`` for ``config``."""
        self.tables = tables
        self.config = config
        self.region_index = {region.region.code: index for index, region in enumerate(tables.regions)}
        for code in config.regions:
            if code not in self.region_index:
                msg = f"unknown phone region {code!r}"
                raise ValueError(msg)
        self.regions = [self.region_index[code] for code in config.regions]
        floors = [
            tables.regions[index].floor_valid if config.require_valid else tables.regions[index].floor_possible
            for index in self.regions
        ]
        self.national_floor = min(floors) if floors else 0

    def find_all(self, text: str) -> list[Match]:
        """Scan ``text`` the way the digit arm of the link scanner would, one match per emitted run."""
        matches: list[Match] = []
        position = 0
        left_bound = 0
        while position < len(text):
            if digit_value(self.tables, text[position]) < 0:
                position += 1
                continue
            match, retry = self.find(text, position, left_bound)
            if match is not None:
                matches.append(match)
                left_bound = match.end
            position = max(retry, position + 1)
        return matches

    def find(self, text: str, digit_pos: int, left_bound: int) -> tuple[Match | None, int]:
        """Recognize the number whose run contains the digit at ``digit_pos``; return it and the retry cursor."""
        run = self._segment(text, digit_pos, left_bound)
        if run is None:
            return None, self._digit_run_end(text, digit_pos)
        retry = run.second_number_cut if run.second_number_cut > 0 else run.extension_end
        if not run.plus and not self.regions:
            return None, retry
        total_digits = sum(len(group.digits) for group in run.groups)
        floor = 3 if run.plus else self.national_floor
        if total_digits < floor:
            return None, retry
        self._poison(text, run)
        if self._is_card(run):
            return None, retry
        for segment_start, segment_end in self._segments(run):
            for start, end in dict.fromkeys(self._chunks(text, segment_start, segment_end)):
                if (found := self._read_chunk(text, run, start, end)) is None:
                    continue
                reading, chunk_end, extension = found
                region = None
                if reading.region_index is not None:
                    region = self.tables.regions[reading.region_index].region.code
                match = Match(start, chunk_end, reading.country_code, reading.nsn, extension, region, reading.type)
                return match, max(chunk_end, run.second_number_cut)
        return None, retry

    @staticmethod
    def _segments(run: _Run) -> list[tuple[int, int]]:
        """List the run's clean candidate segments; a poisoned group ends one the way a letter would."""
        segments: list[tuple[int, int]] = []
        first: int | None = None
        for index, group in enumerate(run.groups):
            poisoned = id(group) in run.poison
            if first is None and not poisoned:
                first = index
            if first is not None and (poisoned or index == len(run.groups) - 1):
                last = index if not poisoned else index - 1
                start = run.start if first == 0 else run.groups[first].start
                end = run.extension_end if last == len(run.groups) - 1 else run.groups[last].end
                segments.append((start, end))
                first = None
        return segments

    def _is_card(self, run: _Run) -> bool:
        return (
            self.config.skip_card_numbers
            and not run.plus
            and _is_card_shape(run.groups)
            and _luhn("".join(group.digits for group in run.groups))
        )

    @staticmethod
    def _chunks(text: str, start: int, end: int) -> list[tuple[int, int]]:
        """List the text ranges parseAndVerify sees: the whole candidate, then extractInnerMatch's splits in order."""
        candidate = text[start:end]
        ranges = [(start, end)]
        for pattern in _INNER_MATCHES:
            for index, found in enumerate(pattern.finditer(candidate)):
                if index == 0:
                    ranges.append((start, start + found.start()))
                ranges.append((start + found.start(1), start + found.end(1)))
        return ranges

    def _read_chunk(self, text: str, run: _Run, start: int, end: int) -> tuple[_Reading, int, str | None] | None:
        """ParseAndVerify on one chunk: its groups, the brackets and neighbor rules, then the readings in order."""
        inside = [index for index, group in enumerate(run.groups) if group.start >= start and group.end <= end]
        if not inside:
            return None
        first, last = inside[0], inside[-1] + 1
        extension: str | None = None
        chunk_end = run.groups[last - 1].end
        if run.extension is not None and last == len(run.groups) and end >= run.extension_end:
            extension, chunk_end = run.extension, run.extension_end
        elif last - first >= 2 and any(char in EXTENSION_MARKERS for char in run.groups[last - 1].separator):
            # the chunk's own extension: an in-run marker group at its end, parsed the way parse() would on the chunk
            consumed = self._walk_extension(text, run.groups[last - 2].end)
            if consumed is not None and consumed[0] >= chunk_end and consumed[1]:
                chunk_end, extension = consumed
                last -= 1
        chunk = text[start:chunk_end]
        if not _brackets_match(chunk) or _PUB_PAGES.search(chunk) or self._blocked_by_neighbors(text, start, chunk_end):
            return None
        digits = "".join(group.digits for group in run.groups[first:last])
        if any(char in PLUS for char in text[start : run.groups[first].start]):
            reading = self._international(digits, None)
        else:
            idd_only = self.config.require_separators and last - first == 1
            reading = next(
                (
                    found
                    for region_index in self.regions
                    if (found := self._national(region_index, digits, idd_only=idd_only)) is not None
                ),
                None,
            )
        if reading is None or not self._accepted_x_rules(text, start, chunk_end, reading, extension):
            return None
        return reading, chunk_end, extension

    def _blocked_by_neighbors(self, text: str, start: int, end: int) -> bool:
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before == "@" or after == "@":
            return True
        if not self.config.require_valid:
            return False
        leads = text[start] in PLUS or text[start] in OPENERS
        if (
            before
            and not leads
            and (is_latin_letter(self.tables, before) or is_invalid_punctuation(self.tables, before))
        ):
            return True
        return bool(after) and (is_latin_letter(self.tables, after) or is_invalid_punctuation(self.tables, after))

    def _accepted_x_rules(self, text: str, start: int, end: int, reading: _Reading, extension: str | None) -> bool:
        """ContainsOnlyValidXChars at VALID: ``xx`` precedes a carrier code, a lone ``x`` precedes the extension."""
        if not self.config.require_valid:
            return True
        candidate = text[start:end]
        for position, char in enumerate(candidate[:-1]):
            if char not in "xX":
                continue
            digits_from = position + 2 if candidate[position + 1] in "xX" else position + 1
            digits_after = "".join(
                chr(0x30 + value)
                for value in (digit_value(self.tables, ch) for ch in candidate[digits_from:])
                if value >= 0
            )
            expected = reading.nsn if digits_from == position + 2 else extension
            if digits_after != expected:
                return False
        return True

    def _digit_run_end(self, text: str, position: int) -> int:
        while position < len(text) and digit_value(self.tables, text[position]) >= 0:
            position += 1
        return position

    def _segment(self, text: str, digit_pos: int, left_bound: int) -> _Run | None:
        # expand left over at most two lead groups: a plus or an opening bracket, each followed by up to four
        # punctuation characters
        # a probe starts a run where the scanner or the matcher's resume put it, even inside a digit group
        digits_start = digit_pos
        start = digit_pos
        # expand right into digit groups separated by up to four punctuation characters
        groups: list[_Group] = []
        position = digits_start
        second_number_cut = 0
        separator = ""
        while position < len(text) and len(groups) < MAX_GROUPS and position - start <= MAX_RUN_CHARS:
            group_start = position
            digits: list[str] = []
            while position < len(text) and (value := digit_value(self.tables, text[position])) >= 0:
                digits.append(chr(0x30 + value))
                position += 1
            if not digits or len(digits) > MAX_GROUP_DIGITS:
                break
            groups.append(_Group("".join(digits), separator, group_start, position))
            probe = position
            punctuation = 0
            while probe < len(text) and text[probe] in PUNCTUATION and punctuation < MAX_LEAD_PUNCTUATION:
                if text[probe] == "/" and _second_number_start(text, probe):
                    second_number_cut = self._second_number_end(text, probe)
                    break
                probe += 1
                punctuation += 1
            if second_number_cut:
                break
            if probe < len(text) and digit_value(self.tables, text[probe]) >= 0 and probe > position:
                separator = text[position:probe]
                position = probe
            else:
                break
        if not groups:
            return None
        end = groups[-1].end
        start = self._lead_start(text, digits_start, end, left_bound)
        plus = any(char in PLUS for char in text[start:digits_start])
        extension, extension_end = self._extension(text, groups, end)
        return _Run(start, end, plus, groups, extension, extension_end, set(), second_number_cut)

    @staticmethod
    def _lead_start(text: str, digits_start: int, end: int, left_bound: int) -> int:
        """
        Find the leftmost start of ``(?:[lead][punct]{0,4}){0,2}`` ending at the digits.

        Returns the start the MATCHING_BRACKETS regex would choose after its inner-bracket retry.
        """
        earliest = max(left_bound, digits_start - 2 * (MAX_LEAD_PUNCTUATION + 1))
        for candidate in range(earliest, digits_start + 1):
            if _lead_groups_match(text[candidate:digits_start]) and _brackets_match(text[candidate:end]):
                return candidate
        return digits_start

    def _second_number_end(self, text: str, slash: int) -> int:
        position = slash + 1
        while position < len(text) and text[position] == " ":
            position += 1
        position += 1
        while position < len(text) and digit_value(self.tables, text[position]) >= 0:
            position += 1
        return position

    def _extension(self, text: str, groups: list[_Group], end: int) -> tuple[str | None, int]:
        """Try the four extension forms from the last separator (in-run ``x``/``~`` forms) and from the run end."""
        candidates = [end]
        if len(groups) > 1 and any(char in EXTENSION_MARKERS for char in groups[-1].separator):
            candidates.append(groups[-2].end)
        for tail_start in candidates:
            consumed = self._walk_extension(text, tail_start)
            if consumed is None:
                continue
            tail_end, digits = consumed
            if tail_start == end and tail_end > end and digits:
                return digits, tail_end
            if tail_start != end and tail_end >= end and digits:
                groups.pop()
                return digits, tail_end
        return None, end

    def _walk_extension(self, text: str, tail_start: int) -> tuple[int, str] | None:
        extension = self.tables.extension
        state = 1
        digits: list[str] = []
        last_accept: tuple[int, str] | None = None
        position = tail_start
        while position < len(text):
            char = text[position]
            value = digit_value(self.tables, char)
            symbol = 1 if value >= 0 else extension.symbol_of.get(ord(char), 0)
            state = extension.dfa.next[state][symbol]
            if state == 0:
                break
            if value >= 0:
                digits.append(chr(0x30 + value))
            elif digits and char != "#":
                digits = []
            position += 1
            if extension.dfa.accepts[state]:
                last_accept = (position, "".join(digits))
        return last_accept

    def _poison(self, text: str, run: _Run) -> None:
        groups = run.groups
        for index in range(len(groups) - 2):
            first, second, third = groups[index], groups[index + 1], groups[index + 2]
            if second.separator == "/" and third.separator == "/" and _is_slash_date(second.digits, third.digits):
                run.poison.update(id(group) for group in (first, second, third))
        if spanned := _timestamp_groups(text, run):
            run.poison.update(id(group) for group in groups[-spanned:])
        if (
            len(groups) == 4
            and not run.plus
            and all(group.separator == "." for group in groups[1:])
            and all(len(group.digits) <= 3 and int(group.digits) <= 255 for group in groups)
        ):
            run.poison.update(id(group) for group in groups)
        if self.config.labels:
            word = _word_before(text, run.start)
            if word and word in self.config.labels:
                run.poison.add(id(groups[0]))

    def _international(self, digits: str, parse_region: int | None) -> _Reading | None:
        """Build a ``+`` reading: country code by the 1-2-3 digit index, the main region's prefix rule, then routing."""
        if len(digits) <= MIN_NSN:
            return None
        group_index, code_length = self._country_code(digits)
        if group_index is None:
            return None
        group = self.tables.groups[group_index]
        nsn = digits[code_length:]
        if len(nsn) < MIN_NSN:
            return None
        main = self.tables.regions[group.main]
        if parse_region is None or parse_region != group.main:
            nsn = self._strip_prefix(main, nsn, adopt=True)
        return self._validate(group, nsn)

    def _country_code(self, digits: str) -> tuple[int | None, int]:
        if digits.startswith("0"):
            return None, 0
        for length in (1, 2, 3):
            if len(digits) >= length and (index := self.tables.group_of_code.get(int(digits[:length]))) is not None:
                return index, length
        return None, 0

    def _national(self, region_index: int, digits: str, *, idd_only: bool = False) -> _Reading | None:
        """ParseHelper with a default region: IDD first, then the region's own country code, then a national read."""
        tables = self.tables.regions[region_index]
        region = tables.region
        if tables.idd is not None:
            idd_end = match_end(tables.idd, [ord(char) - 0x30 for char in digits])
            if idd_end > 0 and (idd_end >= len(digits) or digits[idd_end] != "0"):
                # committed to international parsing: too short or an unknown country code raises upstream
                rest = digits[idd_end:]
                return self._international(rest, None) if len(rest) > MIN_NSN else None
        if idd_only:
            return None
        code = str(region.country_code)
        if digits.startswith(code) and len(digits) > len(code):
            potential = self._strip_prefix(tables, digits[len(code) :], adopt=False)
            full_general = self._general(tables, digits)
            if (not full_general and self._general(tables, potential)) or len(digits) > self._max_possible(tables):
                group = self.tables.groups[
                    tables.region.country_code and self.tables.group_of_code[region.country_code]
                ]
                main = self.tables.regions[group.main]
                if group.main != region_index:
                    potential = self._strip_prefix(main, potential, adopt=True)
                else:
                    potential = self._strip_prefix(tables, potential, adopt=True)
                return self._validate(group, potential)
        nsn = self._strip_prefix(tables, digits, adopt=True)
        if not MIN_NSN <= len(nsn) <= MAX_NSN:
            return None
        group = self.tables.groups[self.tables.group_of_code[region.country_code]]
        reading = self._validate(group, nsn)
        if reading is None or not self.config.require_valid or not self.config.require_national_prefix:
            return reading
        return (
            reading if self._prefix_present_if_required(self.tables.regions[group.main], digits, reading.nsn) else None
        )

    def _prefix_present_if_required(self, main: RegionTables, raw_digits: str, nsn: str) -> bool:
        """Leniency.VALID's isNationalPrefixPresentIfRequired: the chosen format's rule, else the raw digits' prefix."""
        symbols = [ord(char) - 0x30 for char in nsn]
        for item in main.formats:
            if not _looking_at(item.leading, symbols):
                continue
            if (
                not sum(low for low, _high in item.format.groups)
                <= len(nsn)
                <= sum(high for _low, high in item.format.groups)
            ):
                continue
            if not item.format.requires_prefix:
                return True
            return self._strip(main, raw_digits) is not None
        return True

    def _strip_prefix(self, tables: RegionTables, digits: str, *, adopt: bool) -> str:
        """maybeStripNationalPrefixAndCarrierCode, with parseHelper's length adoption when ``adopt``."""
        transformed = self._strip(tables, digits)
        if transformed is None:
            return digits
        if adopt and self._length_result(tables, transformed) in {"TOO_SHORT", "LOCAL_ONLY", "INVALID_LENGTH"}:
            return digits
        return transformed

    def _strip(self, tables: RegionTables, digits: str) -> str | None:
        """Return the prefix rule's rewrite of ``digits``, or None when it fails to match or generalDesc vetoes it."""
        if tables.prefix is None or tables.tag is None or tables.prefix_program is None:
            return None
        symbols = [ord(char) - 0x30 for char in digits]
        end = match_end(tables.prefix, symbols)
        if end < 0:
            return None
        tag = tables.tag
        transformed = digits[end:]
        if tag.group:
            spans = pike_spans(tables.prefix_program, symbols[:end]) or {}
            if (span := spans.get(tag.group)) is not None:
                transformed = tag.literal + digits[span[0] : span[1]] + digits[end:]
        if self._general(tables, digits) and not self._general(tables, transformed):
            return None
        return transformed

    @staticmethod
    def _plan_accept(tables: RegionTables, digits: str) -> int:
        state = 1
        for char in digits:
            state = tables.plan.next[state][ord(char) - 0x30]
            if state == 0:
                return 0
        return tables.plan.accepts[state]

    def _general(self, tables: RegionTables, digits: str) -> bool:
        return bool(self._plan_accept(tables, digits) & GENERAL_BIT)

    @staticmethod
    def _max_possible(tables: RegionTables) -> int:
        lengths = tables.region.possible_national | tables.region.possible_local_only
        return max(lengths) if lengths else 0

    @staticmethod
    def _length_result(tables: RegionTables, digits: str) -> str:
        """TestNumberLength against the general lengths."""
        region = tables.region
        length = len(digits)
        if length in region.possible_local_only:
            return "LOCAL_ONLY"
        national = sorted(region.possible_national)
        if not national:
            return "INVALID_LENGTH"
        if length < national[0]:
            return "TOO_SHORT"
        if length > national[-1]:
            return "TOO_LONG"
        return "POSSIBLE" if length in region.possible_national else "INVALID_LENGTH"

    def _validate(self, group: Group, nsn: str) -> _Reading | None:
        """getRegionCodeForNumber, then isValidNumberForRegion or isPossibleNumber depending on the mode."""
        if not MIN_NSN <= len(nsn) <= MAX_NSN:
            return None
        main = self.tables.regions[group.main]
        region_index, accept = self._route(group, nsn)
        if self.config.require_valid:
            if region_index is None or not accept & GENERAL_BIT:
                return None
            resolved = _resolve_type(accept)
            if resolved == TYPE_UNKNOWN or not self.config.type_mask >> resolved & 1:
                return None
            return _Reading(group.country_code, _cap_zeros(nsn), region_index, resolved, general=True)
        if len(nsn) not in main.region.possible_national | main.region.possible_local_only:
            return None
        general = bool(accept & GENERAL_BIT) if region_index is not None else False
        return _Reading(group.country_code, _cap_zeros(nsn), region_index, TYPE_UNKNOWN, general)

    def _route(self, group: Group, nsn: str) -> tuple[int | None, int]:
        """Return the first region in group order that claims the number, and its plan accept."""
        if len(group.members) == 1:
            index = group.members[0]
            return index, self._plan_accept(self.tables.regions[index], nsn)
        routed_set = 0
        if group.router is not None:
            state = 1
            for char in nsn:
                state = group.router.next[state][ord(char) - 0x30]
                if state == 0:
                    break
            routed_set = group.router.accepts[state] if state else 0
        for position, index in enumerate(group.members):
            tables = self.tables.regions[index]
            accept = self._plan_accept(tables, nsn)
            if group.routed[position]:
                if routed_set >> position & 1:
                    return index, accept
                continue
            if accept & GENERAL_BIT and _resolve_type(accept) != TYPE_UNKNOWN:
                return index, accept
        return None, 0


def _looking_at(dfa: Dfa, symbols: list[int]) -> bool:
    """Java's lookingAt: some prefix of ``symbols`` is in the language."""
    state = 1
    for symbol in symbols:
        state = dfa.next[state][symbol]
        if state == 0:
            return False
        if dfa.accepts[state]:
            return True
    return False


def _matches(dfa: Dfa, symbols: list[int]) -> bool:
    state = 1
    for symbol in symbols:
        state = dfa.next[state][symbol]
        if state == 0:
            return False
    return bool(dfa.accepts[state])


def _resolve_type(accept: int) -> int:
    if not accept & GENERAL_BIT:
        return TYPE_UNKNOWN
    mask = accept & ~GENERAL_BIT & 0x3FF
    for bit in _TYPE_PRECEDENCE:
        if mask >> bit & 1:
            if bit == TYPES.index("fixedLine") and mask >> TYPES.index("mobile") & 1:
                return TYPE_FIXED_LINE_OR_MOBILE
            return bit
    return TYPE_UNKNOWN


def _cap_zeros(nsn: str) -> str:
    stripped = nsn.lstrip("0")
    zeros = len(nsn) - len(stripped)
    if not stripped:
        stripped = "0"
        zeros -= 1
    return "0" * min(zeros, MAX_LEADING_ZEROS) + stripped


def _lead_groups_match(segment: str) -> bool:
    """Check whether ``segment`` is at most two lead groups, each a plus or opener plus up to four punctuation marks."""
    if not segment:
        return True
    if segment[0] not in PLUS and segment[0] not in OPENERS:
        return False
    for punctuation in range(MAX_LEAD_PUNCTUATION + 1):
        rest = segment[1 + punctuation :]
        if punctuation and segment[punctuation] not in PUNCTUATION:
            break
        if not rest or ((rest[0] in PLUS or rest[0] in OPENERS) and _lead_groups_match_one(rest)):
            return True
    return False


def _lead_groups_match_one(segment: str) -> bool:
    if segment[0] not in PLUS and segment[0] not in OPENERS:
        return False
    for punctuation in range(MAX_LEAD_PUNCTUATION + 1):
        if punctuation and segment[punctuation] not in PUNCTUATION:
            return False
        if len(segment) == 1 + punctuation:
            return True
    return False


def _second_number_start(text: str, slash: int) -> bool:
    position = slash + 1
    while position < len(text) and text[position] == " ":
        position += 1
    return position < len(text) and text[position] == "x"


def _is_slash_date(second: str, third: str) -> bool:
    """
    SLASH_SEPARATED_DATES: the middle part is one or two digits of at most 39, the year part has at least two.

    The regex is unanchored, so its first part is satisfied by the last digit of whatever group precedes the slash.
    """
    return len(second) <= 2 and len(third) >= 2 and (len(second) == 1 or second[0] <= "3")


def _timestamp_groups(text: str, run: _Run) -> int:
    """
    Count the groups TIME_STAMPS spans at the run's end when TIME_STAMPS_SUFFIX follows, 0 when it does not.

    Eight digits shaped ``[12]ddd[01]d[0-3]d``, split only after the year and after the month and only by a single
    ``-`` or ``/``, then spaces, a two-digit hour starting 0-2, and ``:MM`` right after the run.
    """
    groups = run.groups
    hour = groups[-1]
    if len(groups) < 2 or len(hour.digits) != 2 or hour.digits[0] > "2":
        return 0
    if not hour.separator or hour.separator.strip(" "):
        return 0
    suffix = text[run.end : run.end + 3]
    if (
        len(suffix) < 3
        or suffix[0] != ":"
        or suffix[1] not in "012345"
        or not (suffix[2].isascii() and suffix[2].isdigit())
    ):
        return 0
    spanned = 1
    accumulated = 0
    for group in reversed(groups[:-1]):
        if accumulated and (group.separator not in {"-", "/"} or accumulated not in {2, 4}):
            return 0
        accumulated += len(group.digits)
        spanned += 1
        if accumulated >= 8:
            break
    if accumulated < 8:
        return 0
    stamp = "".join(group.digits for group in groups[-spanned:-1])[-8:]
    return spanned if stamp[0] in "12" and stamp[4] <= "1" and stamp[6] <= "3" else 0


def _word_before(text: str, start: int) -> str:
    position = start
    while position > 0 and text[position - 1] in " \t.:#-":
        position -= 1
    word_end = position
    while position > 0 and text[position - 1].isalpha() and position > word_end - 12:
        position -= 1
    return text[position:word_end].lower()


# PhoneNumberMatcher.PUB_PAGES: "pages 1-5 (3 pages)", a page range with a bracketed count, is not a number.
_PUB_PAGES: Final = re.compile(r"[0-9]{1,5}-+[0-9]{1,5}[ \t\n\x0b\f\r]{0,4}\([0-9]{1,5}")
_MATCHING_BRACKETS: Final = re.compile(r"\(?(?:x+\))?x+(?:\(x+\)){0,3}x*")


def _brackets_match(candidate: str) -> bool:
    """
    MATCHING_BRACKETS folded to ``(``/``)``/``x``: the shape the upstream regex tests.

    An optional leading opener, an optional leading "text then closer", then at most three balanced pairs and
    no other bracket.
    """
    folded = "".join("(" if char in OPENERS else ")" if char in CLOSERS else "x" for char in candidate)
    return _MATCHING_BRACKETS.fullmatch(folded) is not None


def _is_card_shape(groups: list[_Group]) -> bool:
    lengths = [len(group.digits) for group in groups]
    if len(lengths) == 1:
        return 13 <= lengths[0] <= 19
    return lengths in ([4, 4, 4, 4], [4, 4, 4, 4, 3], [4, 6, 5], [4, 6, 4])


def _luhn(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = ord(char) - 0x30
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


__all__ = [
    "MAX_GROUPS",
    "MAX_RUN_CHARS",
    "TYPE_FIXED_LINE_OR_MOBILE",
    "TYPE_UNKNOWN",
    "Config",
    "Match",
    "Recognizer",
    "digit_value",
    "is_invalid_punctuation",
    "is_latin_letter",
]
