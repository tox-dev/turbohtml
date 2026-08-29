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
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from generate_phone import GENERAL_BIT, MAX_NSN, MIN_NSN, TYPES, Group, RegionTables, Tables
from phone_dfa import Dfa, match_end, pike_spans

if TYPE_CHECKING:
    from collections.abc import Sequence

_MAX_LEAD_PUNCTUATION: Final = 4
_TYPE_UNKNOWN: Final = len(TYPES) + 1

# libphonenumber's VALID_PUNCTUATION, the characters that may sit between digit groups, and its lead characters.
_PUNCTUATION: Final = frozenset(
    "-x\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u30fc\uff0d\uff0e\uff0f \u00a0\u00ad\u200b\u2060\u3000()"
    "\uff08\uff09\uff3b\uff3d.[]/~\u2053\u223c\uff5e"
)
_PLUS: Final = frozenset("+\uff0b")
_OPENERS: Final = frozenset("([\uff08\uff3b")
_CLOSERS: Final = frozenset(")]\uff09\uff3d")
_EXTENSION_MARKERS: Final = frozenset("xX\uff58#\uff03~\uff5e")

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
            if _digit_value(self.tables, text[position]) < 0:
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
        if sum(len(group.digits) for group in run.groups) < (3 if run.plus else self.national_floor):
            return None, retry
        self._poison(text, run)
        for segment_start, segment_end in self._segments(run):
            for start, end in dict.fromkeys(self._chunks(text, segment_start, segment_end)):
                if (found := self._read_chunk(text, run, start, end)) is None:
                    continue
                reading, chunk_end, extension = found
                return (
                    Match(
                        start,
                        chunk_end,
                        reading.country_code,
                        reading.nsn,
                        extension,
                        self.tables.regions[reading.region_index].region.code
                        if reading.region_index is not None
                        else None,
                        reading.type,
                    ),
                    max(chunk_end, run.second_number_cut),
                )
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
                segments.append((
                    run.start if first == 0 else run.groups[first].start,
                    run.extension_end if last == len(run.groups) - 1 else run.groups[last].end,
                ))
                first = None
        return segments

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
        elif last - first >= 2 and any(char in _EXTENSION_MARKERS for char in run.groups[last - 1].separator):
            # the chunk's own extension: an in-run marker group at its end, parsed the way parse() would on the chunk
            consumed = self._walk_extension(text, run.groups[last - 2].end)
            if consumed is not None and consumed[0] >= chunk_end and consumed[1]:
                extension = consumed[1]
                last -= 1
        chunk = text[start:chunk_end]
        if not _brackets_match(chunk) or _PUB_PAGES.search(chunk) or self._blocked_by_neighbors(text, start, chunk_end):
            return None
        digits = "".join(group.digits for group in run.groups[first:last])
        lead = text[start : run.groups[first].start]
        first_plus = next((index for index, char in enumerate(lead) if char in _PLUS), None)
        plus = first_plus is not None
        # VALID_PHONE_NUMBER takes plus signs at the very start only, so `+ +1` is not a viable number
        if plus and any(char in _PLUS for char in lead[first_plus:].lstrip("".join(_PLUS))):
            return None
        if plus and self._country_code(digits)[0] is not None:
            reading = self._international(digits, None)
        else:
            # after a plus that led to no calling code, parseHelper drops the plus and lets the default region's
            # international prefix or own code read the digits; it never reads them as a national number
            mode = "with-code" if plus else "idd" if self.config.require_separators and last - first == 1 else "any"
            reading = next(
                (
                    found
                    for region_index in self.regions
                    if (found := self._national(region_index, digits, mode=mode)) is not None
                ),
                None,
            )
        if reading is None or not self._accepted_x_rules(text, start, chunk_end, reading, extension):
            return None
        return reading, chunk_end, extension

    def _blocked_by_neighbors(self, text: str, start: int, end: int) -> bool:
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before == "@" or after == "@" or _in_address_chain(text, start, end):
            return True
        if not self.config.require_valid:
            return False
        if (
            before
            and not (text[start] in _PLUS or text[start] in _OPENERS)
            and (_is_latin_letter(self.tables, before) or _is_invalid_punctuation(self.tables, before))
        ):
            return True
        return bool(after) and (_is_latin_letter(self.tables, after) or _is_invalid_punctuation(self.tables, after))

    def _accepted_x_rules(self, text: str, start: int, end: int, reading: _Reading, extension: str | None) -> bool:
        """ContainsOnlyValidXChars at VALID: ``xx`` precedes a carrier code, a lone ``x`` precedes the extension."""
        if not self.config.require_valid:
            return True
        candidate = text[start:end]
        for position, char in enumerate(candidate[:-1]):
            if char not in "xX":
                continue
            if position > 0 and candidate[position - 1] in "xX":
                continue  # the second `x` of a carrier-code pair is no extension marker
            digits_from = position + 2 if candidate[position + 1] in "xX" else position + 1
            # after a carrier code the digits are the number's, read the way isNumberMatch reads them: with the
            # extension's own digits behind them
            if "".join(
                chr(0x30 + value)
                for value in (_digit_value(self.tables, ch) for ch in candidate[digits_from:])
                if value >= 0
            ) != (reading.nsn + (extension or "") if digits_from == position + 2 else extension):
                return False
        return True

    def _digit_run_end(self, text: str, position: int) -> int:
        while position < len(text) and _digit_value(self.tables, text[position]) >= 0:
            position += 1
        return position

    def _segment(self, text: str, digit_pos: int, left_bound: int) -> _Run | None:
        # a probe starts a run where the scanner or the matcher's resume put it, even inside a digit group
        digits_start = digit_pos
        groups: list[_Group] = []
        position = digits_start
        second_number_cut = 0
        separator = ""
        while position < len(text) and len(groups) < 21 and position - digit_pos <= 250:
            group_start = position
            digits: list[str] = []
            while position < len(text) and (value := _digit_value(self.tables, text[position])) >= 0:
                digits.append(chr(0x30 + value))
                position += 1
            if not digits or len(digits) > 20:
                break
            groups.append(_Group("".join(digits), separator, group_start, position))
            probe = position
            punctuation = 0
            while probe < len(text) and text[probe] in _PUNCTUATION and punctuation < _MAX_LEAD_PUNCTUATION:
                if text[probe] == "/" and _second_number_start(text, probe):
                    second_number_cut = self._second_number_end(text, probe)
                    break
                probe += 1
                punctuation += 1
            if second_number_cut:
                break
            if probe < len(text) and _digit_value(self.tables, text[probe]) >= 0 and probe > position:
                separator = text[position:probe]
                position = probe
            else:
                break
        if not groups:
            return None
        end = groups[-1].end
        start = self._lead_start(text, digits_start, left_bound)
        extension, extension_end = self._extension(text, groups, end)
        return _Run(
            start,
            end,
            any(char in _PLUS for char in text[start:digits_start]),
            groups,
            extension,
            extension_end,
            set(),
            second_number_cut,
        )

    @staticmethod
    def _lead_start(text: str, digits_start: int, left_bound: int) -> int:
        """Find the leftmost start of ``(?:[lead][punct]{0,4}){0,2}`` ending at the digits; brackets are per chunk."""
        for candidate in range(max(left_bound, digits_start - 2 * (_MAX_LEAD_PUNCTUATION + 1)), digits_start + 1):
            if _lead_groups_match(text[candidate:digits_start]):
                return candidate
        return digits_start

    def _second_number_end(self, text: str, slash: int) -> int:
        position = slash + 1
        while position < len(text) and text[position] == " ":
            position += 1
        position += 1
        while position < len(text) and _digit_value(self.tables, text[position]) >= 0:
            position += 1
        return position

    def _extension(self, text: str, groups: list[_Group], end: int) -> tuple[str | None, int]:
        """Try the four extension forms from the last separator (in-run ``x``/``~`` forms) and from the run end."""
        candidates = [end]
        if len(groups) > 1 and any(char in _EXTENSION_MARKERS for char in groups[-1].separator):
            candidates.append(groups[-2].end)
        for tail_start in candidates:
            consumed = self._walk_extension(text, tail_start)
            if consumed is None:
                continue
            tail_end, digits = consumed
            if tail_start == end and tail_end > end and digits:
                return digits, tail_end
            if tail_start != end and tail_end >= end and digits:
                # PATTERN reads `x1234` as a punctuation-separated digit block, so a `#` after it stays outside
                groups.pop()
                return digits, end
        return None, end

    def _walk_extension(self, text: str, tail_start: int) -> tuple[int, str] | None:
        extension = self.tables.extension
        state = 1
        digits: list[str] = []
        last_accept: tuple[int, str] | None = None
        position = tail_start
        while position < len(text):
            char = text[position]
            value = _digit_value(self.tables, char)
            state = extension.dfa.next[state][1 if value >= 0 else extension.symbol_of.get(ord(char), 0)]
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
            second, third = groups[index + 1], groups[index + 2]
            if second.separator == "/" and third.separator == "/" and _is_slash_date(second.digits, third.digits):
                run.poison.update(id(group) for group in groups[index : index + 3])
        if spanned := _timestamp_groups(text, run):
            run.poison.update(id(group) for group in groups[-spanned:])
        if not run.plus:
            self._poison_ipv4(run)
            if self.config.skip_card_numbers:
                self._poison_cards(run)
        if self.config.labels and (word := _word_before(self.tables, text, run.start)) and word in self.config.labels:
            # the label reaches over hyphens and dots, not over the whitespace that starts a new token
            for index, group in enumerate(groups):
                if index > 0 and any(char in " \xa0\u3000" for char in group.separator):
                    break
                run.poison.add(id(group))

    @staticmethod
    def _poison_cards(run: _Run) -> None:
        """Poison a run that is one Luhn-valid card shape, and any unbroken Luhn-valid group of 13 to 19 digits."""
        groups = run.groups
        for group in groups:
            if 13 <= len(group.digits) <= 19 and _luhn(group.digits):
                run.poison.add(id(group))
        if [len(group.digits) for group in groups] in _CARD_SHAPES and _luhn("".join(group.digits for group in groups)):
            run.poison.update(id(group) for group in groups)

    @staticmethod
    def _poison_ipv4(run: _Run) -> None:
        """Poison the four groups of each IPv4 address in the run, wherever it sits, and nothing around it."""
        groups = run.groups
        for index in range(len(groups) - 3):
            window = groups[index : index + 4]
            bounded = (index == 0 or groups[index].separator != ".") and (
                index + 4 == len(groups) or groups[index + 4].separator != "."
            )
            if (
                bounded
                and all(group.separator == "." for group in window[1:])
                and all(len(group.digits) <= 3 and int(group.digits) <= 255 for group in window)
            ):
                run.poison.update(id(group) for group in window)

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
        if parse_region is None or parse_region != group.main:
            nsn = self._strip_prefix(self.tables.regions[group.main], nsn, adopt=True)
        return self._validate(group, nsn)

    def _country_code(self, digits: str) -> tuple[int | None, int]:
        if digits.startswith("0"):
            return None, 0
        for length in (1, 2, 3):
            if len(digits) >= length and (index := self.tables.group_of_code.get(int(digits[:length]))) is not None:
                return index, length
        return None, 0

    def _national(self, region_index: int, digits: str, *, mode: str = "any") -> _Reading | None:
        """ParseHelper with a default region: IDD first, then the region's own country code, then a national read."""
        tables = self.tables.regions[region_index]
        region = tables.region
        if tables.idd is not None:
            idd_end = match_end(tables.idd, [ord(char) - 0x30 for char in digits])
            if idd_end > 0 and (idd_end >= len(digits) or digits[idd_end] != "0"):
                # committed to international parsing: too short or an unknown country code raises upstream
                rest = digits[idd_end:]
                return self._international(rest, None) if len(rest) > MIN_NSN else None
        if mode == "idd":
            return None
        code = str(region.country_code)
        if digits.startswith(code) and len(digits) > len(code):
            potential = self._strip_prefix(tables, digits[len(code) :], adopt=False)
            if (not self._general(tables, digits) and self._general(tables, potential)) or len(
                digits
            ) > self._max_possible(tables):
                group = self.tables.groups[
                    tables.region.country_code and self.tables.group_of_code[region.country_code]
                ]
                if group.main != region_index:
                    potential = self._strip_prefix(self.tables.regions[group.main], potential, adopt=True)
                else:
                    potential = self._strip_prefix(tables, potential, adopt=True)
                return self._validate(group, potential)
        if mode == "with-code" or not MIN_NSN <= len(nsn := self._strip_prefix(tables, digits, adopt=True)) <= MAX_NSN:
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
        if tag.group and (span := (pike_spans(tables.prefix_program, symbols[:end]) or {}).get(tag.group)) is not None:
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
        region_index, accept = self._route(group, nsn)
        if self.config.require_valid:
            if region_index is None or not accept & GENERAL_BIT:
                return None
            resolved = _resolve_type(accept)
            if resolved == _TYPE_UNKNOWN or not self.config.type_mask >> resolved & 1:
                return None
            return _Reading(group.country_code, _cap_zeros(nsn), region_index, resolved, general=True)
        main = self.tables.regions[group.main]
        if len(nsn) not in main.region.possible_national | main.region.possible_local_only:
            return None
        return _Reading(
            group.country_code,
            _cap_zeros(nsn),
            region_index,
            _TYPE_UNKNOWN,
            general=bool(accept & GENERAL_BIT) if region_index is not None else False,
        )

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
            accept = self._plan_accept(self.tables.regions[index], nsn)
            if group.routed[position]:
                if routed_set >> position & 1:
                    return index, accept
                continue
            if accept & GENERAL_BIT and _resolve_type(accept) != _TYPE_UNKNOWN:
                return index, accept
        return None, 0


def _digit_value(tables: Tables, char: str) -> int:
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


def _is_latin_letter(tables: Tables, char: str) -> bool:
    """PhoneNumberMatcher.isLatinLetter: a letter or nonspacing mark inside the six Latin blocks."""
    return _in_ranges(ord(char), tables.unicode.latin_ranges)


def _is_invalid_punctuation(tables: Tables, char: str) -> bool:
    """PhoneNumberMatcher.isInvalidPunctuationSymbol: ``%`` or a currency symbol."""
    return char == "%" or _in_ranges(ord(char), tables.unicode.currency_ranges)


def _in_ranges(code: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(first <= code <= last for first, last in ranges)


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


def _resolve_type(accept: int) -> int:
    if not accept & GENERAL_BIT:
        return _TYPE_UNKNOWN
    mask = accept & ~GENERAL_BIT & 0x3FF
    for bit in _TYPE_PRECEDENCE:
        if mask >> bit & 1:
            if bit == TYPES.index("fixedLine") and mask >> TYPES.index("mobile") & 1:
                return len(TYPES)
            return bit
    return _TYPE_UNKNOWN


def _cap_zeros(nsn: str) -> str:
    stripped = nsn.lstrip("0")
    zeros = len(nsn) - len(stripped)
    if not stripped:
        stripped = "0"
        zeros -= 1
    return "0" * min(zeros, 10) + stripped


def _lead_groups_match(segment: str) -> bool:
    """Check whether ``segment`` is at most two lead groups, each a plus or opener plus up to four punctuation marks."""
    if not segment:
        return True
    if segment[0] not in _PLUS and segment[0] not in _OPENERS:
        return False
    for punctuation in range(_MAX_LEAD_PUNCTUATION + 1):
        if punctuation and segment[punctuation] not in _PUNCTUATION:
            break
        rest = segment[1 + punctuation :]
        if not rest or ((rest[0] in _PLUS or rest[0] in _OPENERS) and _lead_groups_match_one(rest)):
            return True
    return False


def _lead_groups_match_one(segment: str) -> bool:
    if segment[0] not in _PLUS and segment[0] not in _OPENERS:
        return False
    for punctuation in range(_MAX_LEAD_PUNCTUATION + 1):
        if punctuation and segment[punctuation] not in _PUNCTUATION:
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


def _word_before(tables: Tables, text: str, start: int) -> str:
    """Return the ASCII word right before the digits, or "" when a Latin letter glued to it makes a longer word."""
    position = start
    while position > 0 and text[position - 1] in " \xa0\u3000\t\n\r.:#-":
        position -= 1
    word_end = position
    while position > 0 and text[position - 1] in string.ascii_letters and position > word_end - 12:
        position -= 1
    if position > 0 and _is_latin_letter(tables, text[position - 1]):
        return ""
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
    return (
        _MATCHING_BRACKETS.fullmatch(
            "".join("(" if char in _OPENERS else ")" if char in _CLOSERS else "x" for char in candidate)
        )
        is not None
    )


_CARD_SHAPES: Final = ([4, 4, 4, 4], [4, 4, 4, 4, 3], [4, 6, 5], [4, 6, 4])
_ADDRESS_CHAIN: Final = frozenset("0123456789abcdefABCDEF:[]")
_HEX_DIGITS: Final = frozenset(string.hexdigits)
_MAX_ADDRESS_CHARS: Final = 48


def _is_ipv6_literal(chain: str) -> bool:
    """RFC 4291's text form without an IPv4 tail, bare or bracketed with a port."""
    address = chain
    if chain.startswith("["):
        address, closer, port = chain[1:].partition("]")
        if not closer or (port and not (port.startswith(":") and 1 <= len(port) - 1 <= 5 and port[1:].isdigit())):
            return False
    if not address or any(char not in _HEX_DIGITS and char != ":" for char in address):
        return False
    if address.count("::") > 1 or ":::" in address:
        return False
    if (address.startswith(":") and not address.startswith("::")) or (
        address.endswith(":") and not address.endswith("::")
    ):
        return False
    hextets = [group for group in address.split(":") if group]
    if any(len(group) > 4 for group in hextets):
        return False
    return len(hextets) <= 7 if "::" in address else len(hextets) == 8


def _in_address_chain(text: str, start: int, end: int) -> bool:
    """Whether the candidate sits inside an IPv6 literal; a chain past any literal's length is prose."""
    if end - start > _MAX_ADDRESS_CHARS:
        return False
    left = start
    while left > 0 and start - left < _MAX_ADDRESS_CHARS and text[left - 1] in _ADDRESS_CHAIN:
        left -= 1
    right = end
    while right < len(text) and right - end < _MAX_ADDRESS_CHARS and text[right] in _ADDRESS_CHAIN:
        right += 1
    return (
        not ((left > 0 and text[left - 1] in _ADDRESS_CHAIN) or (right < len(text) and text[right] in _ADDRESS_CHAIN))
        and right - left > end - start
        and _is_ipv6_literal(text[left:right])
    )


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
    "Config",
    "Match",
    "Recognizer",
]
