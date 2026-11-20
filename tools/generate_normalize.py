"""
Generate src/turbohtml/_c/data/normalize_table.h from the pinned Unicode Character Database.

:func:`turbohtml.detect.normalize` runs Unicode normalization (UAX #15) for all four forms -- NFC, NFD, NFKC, NFKD --
in C, the way stdlib :func:`unicodedata.normalize` does whole strings, with a quick-check fast path so
already-normalized text (the common case) is returned untouched. That needs five tables, all pinned to one Unicode
version so a rebuild is deterministic: the canonical combining class of every mark, the full canonical decomposition
(NFD) and the full compatibility decomposition (NFKD) of every code point that decomposes, the canonical recomposition
pairs, and the NFC/NFKC quick-check property.

The combining classes, decompositions, and composition pairs are read straight from the interpreter's own
:mod:`unicodedata`, so the generated tables are the oracle the C engine is tested against -- an exact match is
guaranteed by construction, not merely checked. The build therefore pins to the interpreter's Unicode version
(``unicodedata.unidata_version``) and aborts if it is not the reviewed one, the same guarantee the ``idna_table.h`` pin
gives from downloaded files.

The one piece :mod:`unicodedata` does not expose is the quick-check property (NFC_QC/NFKC_QC No/Maybe), so that is
parsed from ``DerivedNormalizationProps.txt``, fetched at the pinned version and verified against the SHA-256 of its
exact bytes (a poisoned mirror would still serve altered content under the stable version URL). Hangul syllables are
decomposed and composed arithmetically in C (Unicode 3.12), so their 11k rows never enter any table.

Usage:  python tools/generate_normalize.py src/turbohtml/_c/data/normalize_table.h
"""

from __future__ import annotations

import hashlib
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from httpfetch import fetch_bytes

if TYPE_CHECKING:
    from collections.abc import Iterable

_Form = Literal["NFC", "NFD", "NFKC", "NFKD"]

# The Unicode version the committed table is pinned to. It must equal the interpreter's own unicodedata version so the
# tables generated from unicodedata match what the C engine is validated against; bump both together and review the
# normalize_table.h diff. Unicode 16.0.0 is what CPython 3.14 ships and what idna_table.h already pins.
_UNICODE_VERSION: Final[str] = "16.0.0"
_UCD_BASE: Final[str] = f"https://www.unicode.org/Public/{_UNICODE_VERSION}/ucd"

# SHA-256 of DerivedNormalizationProps.txt at Unicode 16.0.0 (the same file idna_table.h pins), the only download this
# generator needs: unicodedata supplies everything except the quick-check property.
_DERIVED_NORM_SHA256: Final[str] = "4d4c03892dea9146d674b686e495df2d55a28d071ac474041d73518f887abddc"

_HANGUL_SBASE: Final[int] = 0xAC00
_HANGUL_SCOUNT: Final[int] = 11172
_SHIFTS: Final[range] = range(5, 9)


def _fetch(url: str, expected_sha256: str) -> str:
    # Return the decoded body of a pinned Unicode data file, aborting if its SHA-256 is not *expected_sha256*.
    raw = fetch_bytes(url)
    if (digest := hashlib.sha256(raw).hexdigest()) != expected_sha256:
        msg = f"Unicode source {url} has sha256 {digest}, not the pinned {expected_sha256}; review, then bump the pin"
        raise SystemExit(msg)
    return raw.decode("utf-8")


def _code_points() -> Iterable[int]:
    # Every assignable scalar value: the whole space minus the surrogate block, which no string can hold.
    surrogate_hi: int = 0xDFFF
    surrogate_lo = 0xD800
    for code in range(0x110000):
        if surrogate_lo <= code <= surrogate_hi:
            continue
        yield code


class _CharacterTables(NamedTuple):
    canonical: dict[int, list[int]]
    compatibility: dict[int, list[int]]
    combining: dict[int, int]


def _character_tables() -> _CharacterTables:
    # Collect combining classes during the decomposition scan to avoid another scalar pass.
    #
    # ``unicodedata.normalize`` on a single character yields its fully-resolved, canonically-ordered decomposition, so
    # the
    # C engine never recurses. The compatibility table stores only the code points whose NFKD differs from their NFD;
    # the engine falls back to the canonical table for the rest, halving the compatibility rows.
    combining: Final[dict[int, int]] = {}
    canonical: Final[dict[int, list[int]]] = {}
    compatibility: Final[dict[int, list[int]]] = {}
    for code in _code_points():
        char = chr(code)
        if klass := unicodedata.combining(char):
            combining[code] = klass
        if _HANGUL_SBASE <= code < _HANGUL_SBASE + _HANGUL_SCOUNT:
            continue
        if (nfd := unicodedata.normalize("NFD", char)) != char:
            canonical[code] = [ord(part) for part in nfd]
        if (nfkd := unicodedata.normalize("NFKD", char)) != nfd:
            compatibility[code] = [ord(part) for part in nfkd]
    return _CharacterTables(canonical, compatibility, combining)


def _composition_pairs(canonical: dict[int, list[int]]) -> list[tuple[int, int, int]]:
    # Return the canonical recomposition pairs ``(first, second, composed)``, sorted.
    #
    # A code point is a primary composite iff its canonical decomposition is exactly two code points that NFC recomposes
    # back to it; that single test folds in every exclusion (composition exclusions, singletons, and non-starter
    # decompositions all fail to recompose), so it is the authoritative source unicodedata already carries.
    pairs: list[tuple[int, int, int]] = []
    for code in canonical:
        parts = unicodedata.decomposition(chr(code)).split()
        if len(parts) != 2:
            continue
        first, second = int(parts[0], 16), int(parts[1], 16)
        if unicodedata.normalize("NFC", chr(first) + chr(second)) == chr(code):
            pairs.append((first, second, code))
    pairs.sort()
    return pairs


def _quick_check(text: str) -> list[tuple[int, int, int, int]]:
    # Parse the NFC_QC/NFKC_QC property into ``(first, last, nfc, nfkc)`` ranges, sorted, coalescing equal neighbors.
    #
    # The decompose forms (NFD/NFKD) need no quick-check table: a code point is NFD_QC=No exactly when it has a
    # canonical decomposition, which the engine already reads from the decomposition tables (Hangul arithmetically).
    # Only the compose forms carry a Maybe value (a mark that may fold into a preceding starter), so they are tabled
    # here.
    values: dict[int, list[int]] = {}
    qc_maybe: int = 2
    qc_no = 1
    for line in text.splitlines():
        payload = line.split("#", 1)[0]
        fields = [field.strip() for field in payload.split(";")]
        if len(fields) != 3 or fields[1] not in {"NFC_QC", "NFKC_QC"} or fields[2] not in {"N", "M"}:
            continue
        lo_hi = fields[0].split("..")
        low = int(lo_hi[0], 16)
        high = int(lo_hi[-1], 16)
        value = qc_no if fields[2] == "N" else qc_maybe
        column = 0 if fields[1] == "NFC_QC" else 1
        for code in range(low, high + 1):
            values.setdefault(code, [0, 0])[column] = value
    ranges: list[tuple[int, int, int, int]] = []
    for code in sorted(values):
        nfc, nfkc = values[code]
        if ranges and ranges[-1][1] == code - 1 and ranges[-1][2] == nfc and ranges[-1][3] == nfkc:
            first, _, _, _ = ranges[-1]
            ranges[-1] = (first, code, nfc, nfkc)
        else:
            ranges.append((code, code, nfc, nfkc))
    return ranges


def _reference_decompose(text: str, decomp_map: dict[int, list[int]]) -> list[int]:
    # Decompose *text* through *decomp_map* (Hangul arithmetically), the Python mirror of the C decompose step.
    out: list[int] = []
    for char in text:
        code = ord(char)
        sindex = code - _HANGUL_SBASE
        if 0 <= sindex < _HANGUL_SCOUNT:
            parts = [0x1100 + sindex // 588, 0x1161 + sindex % 588 // 28]
            if sindex % 28:
                parts.append(0x11A7 + sindex % 28)
            out.extend(parts)
        elif (run := decomp_map.get(code)) is not None:
            out.extend(run)
        else:
            out.append(code)
    return out


def _reference_reorder(seq: list[int], combining: dict[int, int]) -> None:
    # Sort each combining-mark run of *seq* into canonical order in place (a stable insertion sort by class).
    for index in range(1, len(seq)):
        code = seq[index]
        if not (klass := combining.get(code, 0)):
            continue
        back = index
        while back > 0 and combining.get(seq[back - 1], 0) > klass:
            seq[back] = seq[back - 1]
            back -= 1
        seq[back] = code


def _reference_compose(seq: list[int], combining: dict[int, int], compose: dict[tuple[int, int], int]) -> list[int]:
    # Recompose the ordered *seq*, folding each unblocked mark into its starter, the mirror of the C compose step.
    out: list[int] = []
    starter_at = -1
    last_class = 0
    for code in seq:
        klass = combining.get(code, 0)
        unblocked = starter_at >= 0 and (last_class < klass or last_class == 0)
        if unblocked and (composed := _compose_pair(out[starter_at], code, compose)):
            out[starter_at] = composed
            continue
        if klass == 0:
            starter_at = len(out)
        last_class = klass
        out.append(code)
    return out


def _self_check(
    canonical: dict[int, list[int]],
    compatibility: dict[int, list[int]],
    combining: dict[int, int],
    pairs: list[tuple[int, int, int]],
) -> None:
    # Reconstruct all four forms from the generated tables and confirm they match unicodedata for every code point.
    compose = {(first, second): composed for first, second, composed in pairs}
    nfkd = {**canonical, **compatibility}
    for code in _code_points():
        char = chr(code)
        canonical_seq: Final[list[int]] = _reference_decompose(char, canonical)
        _reference_reorder(canonical_seq, combining)
        compatibility_seq = canonical_seq
        if code in compatibility:
            compatibility_seq = _reference_decompose(char, nfkd)
            _reference_reorder(compatibility_seq, combining)
        forms: Final[tuple[tuple[_Form, list[int]], ...]] = (
            ("NFC", _reference_compose(canonical_seq, combining, compose)),
            ("NFD", canonical_seq),
            ("NFKC", _reference_compose(compatibility_seq, combining, compose)),
            ("NFKD", compatibility_seq),
        )
        for form, seq in forms:
            if "".join(chr(point) for point in seq) != unicodedata.normalize(form, char):
                msg = f"self-check failed for U+{code:04X} {form}"
                raise SystemExit(msg)


def _compose_pair(first: int, second: int, compose: dict[tuple[int, int], int]) -> int:
    # Return the canonical composition of the pair: Hangul arithmetically, everything else from the table, else 0.
    lindex = first - 0x1100
    vindex = second - 0x1161
    if 0 <= lindex < 19 and 0 <= vindex < 21:
        return _HANGUL_SBASE + (lindex * 21 + vindex) * 28
    sindex = first - _HANGUL_SBASE
    tindex = second - 0x11A7
    if 0 <= sindex < _HANGUL_SCOUNT and sindex % 28 == 0 and 0 < tindex < 28:
        return first + tindex
    return compose.get((first, second), 0)


def _wrap(items: Iterable[str]) -> str:
    # Return the items as brace-initializer rows wrapped near 116 columns, each line indented four spaces.
    lines: list[str] = []
    current = "   "
    for item in items:
        piece = f" {item},"
        if len(current) + len(piece) > 116:
            lines.append(current)
            current = "   "
        current += piece
    lines.append(current)
    return "\n".join(lines)


class _TwoStage(NamedTuple):
    shift: int
    base: int
    span: int
    index: list[int]
    blocks: list[int]

    def size(self) -> int:
        return len(self.index) + len(self.blocks) * _element_size(max(self.blocks))


def _two_stage_at(values: dict[int, int], shift: int) -> _TwoStage | None:
    # Lay *values* out in blocks of 1 << shift code points: index maps (code - base) >> shift to the id of its block,
    # and blocks holds the distinct blocks back to back, id 0 the all-zero block. The span starts at the lowest code
    # point aligned down to a block, so a code point outside [base, base + span] reads as the default. None when more
    # than 256 distinct blocks would not fit the one-byte index.
    size = 1 << shift
    base = min(values) >> shift << shift
    span = max(values) - base
    ids: dict[tuple[int, ...], int] = {(0,) * size: 0}
    index = [0] * ((span >> shift) + 1)
    for block_number in sorted({(code - base) >> shift for code in values}):
        start = base + (block_number << shift)
        index[block_number] = ids.setdefault(tuple(values.get(start + offset, 0) for offset in range(size)), len(ids))
    if len(ids) > 256:
        return None
    return _TwoStage(shift, base, span, index, [value for block in sorted(ids, key=ids.__getitem__) for value in block])


def _element_size(maximum: int) -> int:
    # Return the byte width of the narrowest unsigned C integer that holds *maximum*.
    if maximum <= 0xFF:
        return 1
    if maximum <= 0xFFFF:
        return 2
    msg = f"value {maximum} does not fit a uint16_t"
    raise SystemExit(msg)


def _table(name: str, values: dict[int, int]) -> str:
    # Render one two-stage table: its layout constants, the one-byte index, and the blocks in the narrowest type.
    designs = [design for shift in _SHIFTS if (design := _two_stage_at(values, shift)) is not None]
    design = min(designs, key=_TwoStage.size)
    upper = name.upper()
    block_type = "uint8_t" if _element_size(max(design.blocks)) == 1 else "uint16_t"
    return (
        f"#define TH_NORM_{upper}_SHIFT {design.shift}\n"
        f"#define TH_NORM_{upper}_BASE 0x{design.base:X}\n"
        f"#define TH_NORM_{upper}_SPAN 0x{design.span:X}\n"
        f"static const uint8_t th_norm_{name}_index[] = {{\n{_wrap(str(block) for block in design.index)}\n}};\n\n"
        f"static const {block_type} th_norm_{name}_blocks[] = {{\n"
        f"{_wrap(str(value) for value in design.blocks)}\n}};\n\n"
    )


def _composition_groups(pairs: list[tuple[int, int, int]]) -> tuple[dict[int, int], list[int]]:
    # Number the distinct first code points of the sorted pairs from 1, and return that numbering with the row offset
    # each group starts at plus a closing offset, so group n spans the rows from starts[n - 1] up to starts[n].
    numbers: dict[int, int] = {}
    starts: list[int] = []
    for offset, (first, _, _) in enumerate(pairs):
        if first not in numbers:
            numbers[first] = len(numbers) + 1
            starts.append(offset)
    starts.append(len(pairs))
    return numbers, starts


def _shared_pool(
    canonical: dict[int, list[int]], compatibility: dict[int, list[int]]
) -> tuple[list[int], dict[tuple[int, ...], int]]:
    # Lay every distinct decomposition run of both tables into one pool, once each, and return it with each run's
    # offset. A run the canonical and compatibility tables share is stored once; the rows reach it by offset.
    offsets: dict[tuple[int, ...], int] = {}
    pool: list[int] = []
    for run in (*canonical.values(), *compatibility.values()):
        if (key := tuple(run)) not in offsets:
            offsets[key] = len(pool)
            pool.extend(run)
    return pool, offsets


def _decomposition(name: str, table: dict[int, list[int]], offsets: dict[tuple[int, ...], int]) -> str:
    # Render the decomposition of *table*: a two-stage map from each code point to its row number (1-based, 0 for none),
    # and the rows' pool offsets and lengths, indexed by row number minus one.
    ordered = sorted(table)
    numbers = {code: number for number, code in enumerate(ordered, start=1)}
    row_offsets = [offsets[tuple(table[code])] for code in ordered]
    row_lengths = [len(table[code]) for code in ordered]
    if max(row_offsets) > 0xFFFF:
        msg = "decomposition pool offset does not fit a uint16_t"
        raise SystemExit(msg)
    return (
        f"{_table(name, numbers)}"
        f"static const uint16_t th_norm_{name}_offset[] = {{\n{_wrap(str(value) for value in row_offsets)}\n}};\n\n"
        f"static const uint8_t th_norm_{name}_length[] = {{\n{_wrap(str(value) for value in row_lengths)}\n}};\n\n"
    )


def _emit(
    canonical: dict[int, list[int]],
    compatibility: dict[int, list[int]],
    combining: dict[int, int],
    pairs: list[tuple[int, int, int]],
    quick: list[tuple[int, int, int, int]],
) -> str:
    # Render the generated C header from the combining-class, decomposition, composition, and quick-check tables.
    pool, offsets = _shared_pool(canonical, compatibility)
    quick_values = {code: nfc | nfkc << 2 for first, last, nfc, nfkc in quick for code in range(first, last + 1)}
    comp_numbers, comp_starts = _composition_groups(pairs)
    comp_rows = _wrap(f"{{0x{second:X}, 0x{composed:X}}}" for _, second, composed in pairs)
    max_expansion = max(len(run) for run in (*canonical.values(), *compatibility.values()))
    return (
        "/* Auto-generated by tools/generate_normalize.py - do not edit. */\n"
        f"/* Unicode {_UNICODE_VERSION} normalization (UAX #15): the canonical combining classes, the full canonical\n"
        "   (NFD) and compatibility (NFKD) decompositions, the canonical recomposition pairs, and the NFC/NFKC\n"
        "   quick-check property. Hangul is (de)composed arithmetically in C, so its syllables need no rows. */\n\n"
        "#ifndef TURBOHTML_NORMALIZE_TABLE_H\n"
        "#define TURBOHTML_NORMALIZE_TABLE_H\n\n"
        "#include <stdint.h>\n\n"
        f'#define TH_NORM_UNICODE_VERSION "{_UNICODE_VERSION}"\n'
        "/* The longest full decomposition of any single code point, bounding the output at in_len * this. */\n"
        f"#define TH_NORM_MAX_EXPANSION {max_expansion}\n\n"
        "/* Every two-stage table below: the code point less its BASE, shifted right by SHIFT, indexes `index` for a\n"
        "   block number; the value sits at blocks[(block << SHIFT) | (offset & ((1 << SHIFT) - 1))]. A code point\n"
        "   above BASE + SPAN, or below BASE, has the default value and skips the table. */\n"
        "/* Canonical combining class: 0 for every code point outside the table, the class of a mark otherwise. */\n"
        f"{_table('ccc', combining)}"
        "/* Decomposition: the pool holds each distinct run once; canon and compat map a code point to its row. The\n"
        "   compatibility table holds only code points whose NFKD differs from their NFD; canon covers the rest. */\n"
        f"static const uint32_t th_norm_pool[] = {{\n{_wrap(f'0x{code:X}' for code in pool)}\n}};\n\n"
        f"{_decomposition('canon', canonical, offsets)}"
        f"{_decomposition('compat', compatibility, offsets)}"
        "/* Canonical composition: the pairs of each first code point form one group, numbered by `comp` (0: none)\n"
        "   and spanning rows comp_group[number - 1] up to comp_group[number], sorted by second. */\n"
        "typedef struct {\n"
        "    uint32_t second;\n"
        "    uint32_t composed;\n"
        "} th_norm_comp_row;\n\n"
        f"static const uint32_t th_norm_comp_second_min = 0x{min(second for _, second, _ in pairs):X};\n"
        f"{_table('comp', comp_numbers)}"
        f"static const uint16_t th_norm_comp_group[] = {{\n{_wrap(str(start) for start in comp_starts)}\n}};\n\n"
        f"static const th_norm_comp_row th_norm_comp[] = {{\n{comp_rows}\n}};\n\n"
        "/* NFC (low 2 bits) and NFKC (bits 2-3) quick-check per code point: 0 Yes (absent), 1 No, 2 Maybe. Decompose\n"
        "   forms need no table. */\n"
        f"{_table('qc', quick_values)}"
        "#endif /* TURBOHTML_NORMALIZE_TABLE_H */\n"
    )


def generate(out_path: Path) -> None:
    # Write the generated normalization C header to *out_path*.
    if unicodedata.unidata_version != _UNICODE_VERSION:
        msg = (
            f"interpreter unicodedata is Unicode {unicodedata.unidata_version}, not the pinned {_UNICODE_VERSION}; "
            "run under a matching Python or bump UNICODE_VERSION and review the diff"
        )
        raise SystemExit(msg)
    canonical, compatibility, combining = _character_tables()
    pairs = _composition_pairs(canonical)
    quick = _quick_check(_fetch(f"{_UCD_BASE}/DerivedNormalizationProps.txt", _DERIVED_NORM_SHA256))
    _self_check(canonical, compatibility, combining, pairs)
    out_path.write_text(_emit(canonical, compatibility, combining, pairs, quick), encoding="utf-8")
    print(
        f"wrote {out_path}: {len(canonical)} canonical, {len(compatibility)} compatibility, "
        f"{len(pairs)} compositions, {len(quick)} quick-check ranges"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        msg = "usage: generate_normalize.py OUTPUT_HEADER"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]))
