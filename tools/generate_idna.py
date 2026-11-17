"""
Generate src/turbohtml/_c/data/idna_table.h from the pinned Unicode Character Database.

:func:`turbohtml.extract.normalize_url` turns a Unicode host into its ASCII form the way the WHATWG URL Standard's host
parser does: Unicode IDNA ``ToASCII`` with ``Transitional_Processing=false`` and ``UseSTD3ASCIIRules=false`` (the
non-strict flag a crawl-oriented cleaner wants), which is UTS #46. The generated data covers the UTS #46 mapping,
canonical combining classes, decompositions, compositions, and NFC quick-check ranges. This tool downloads each source
at the pinned Unicode version and writes one header that ``idna.c`` includes.

The mapping status is collapsed to the four outcomes the ToASCII engine acts on -- keep, map, ignore, disallow.
``valid`` and ``deviation`` (non-transitional keeps the code point) both keep it; ``disallowed`` stays distinct so
``idna.c`` rejects the host (UTS #46 §4.1 validity criterion 6) instead of encoding a code point the WHATWG host parser
forbids.

Each fetched file is also pinned to the SHA-256 of its exact bytes, so a rebuild refuses a silently rewritten or
poisoned mirror the version pin alone would not catch. The pinned mapping file is the "Compatible Preprocessing"
variant, whose ``valid`` rows already fold in ``UseSTD3ASCIIRules=false`` (ASCII symbols such as ``_`` are ``valid``
rather than ``disallowed_STD3_valid``), so no STD3 toggle is needed at run time.

Normalization data is stored fully resolved: each code point's canonical decomposition is expanded recursively to its
non-decomposable form in this generator, so the C step never recurses, and the composition pairs are the canonical
two-code-point decompositions whose target is not a Full_Composition_Exclusion. Hangul is handled arithmetically in C
(the algorithm in Unicode 3.12), so its 11k syllables need no table rows.

Usage:  python tools/generate_idna.py src/turbohtml/_c/data/idna_table.h
"""

from __future__ import annotations

import hashlib
import sys
import unicodedata
from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING, Final

from httpfetch import fetch_bytes

if TYPE_CHECKING:
    from collections.abc import Iterable

# Unicode 16.0.0, the version CPython 3.14's unicodedata ships, so the C NFC step and the interpreter agree. The IDNA
# files live under /Public/idna/<version>/ and the core UCD under /Public/<version>/ucd/; pin the version, review the
# idna_table.h diff on a bump. CPython's own Unicode version is unicodedata.unidata_version.
_UNICODE_VERSION: Final[str] = "16.0.0"
_IDNA_BASE: Final[str] = f"https://www.unicode.org/Public/idna/{_UNICODE_VERSION}"
_UCD_BASE: Final[str] = f"https://www.unicode.org/Public/{_UNICODE_VERSION}/ucd"

# Pin the SHA-256 of the exact bytes of each fetched file, not just the version: a version pin fixes which release the
# rebuild targets, but a poisoned or silently rewritten mirror could still serve altered content under that version's
# stable URL, and no review of idna.c would catch a bad table. A rebuild recomputes each digest and aborts on a
# mismatch. Bump a digest deliberately alongside UNICODE_VERSION and review the idna_table.h diff. All match the
# committed table at Unicode 16.0.0.
_IDNA_MAPPING_SHA256: Final[str] = "6db2ef4ed35f3b3de74ebc2e00404a9607f76d499f576b8d4043cf14f1ed175c"
_BIDI_CLASS_SHA256: Final[str] = "71ed943a49c58568d8d92e80ecc2ba2f06e62aee9c8ebb0e6e8bd2c3ed8b180e"
_JOINING_TYPE_SHA256: Final[str] = "6bd08b97da66b70ccfdab105a352de2984e02625239ec5695422c99b33d854f0"
_GENERAL_CATEGORY_SHA256: Final[str] = "7676ab755a41ef82108460238569e60ad65c191ddafe61b36c6765ec1353f293"

# The bidi classes and joining types, in the order the C checks index them; a code point absent from a file takes the
# first value (class L, joining type U), which is the default UAX #9 and ArabicShaping give unlisted code points.
_BIDI_CLASSES: Final[tuple[str, ...]] = (
    "L", "R", "AL", "EN", "ES", "ET", "AN", "CS", "NSM", "BN", "B", "S", "WS", "ON",
    "LRE", "LRO", "RLE", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI",
)  # fmt: skip
_JOINING_TYPES: Final[tuple[str, ...]] = ("U", "C", "D", "L", "R", "T")
# Only the combining marks matter to UTS #46 (a label must not begin with one); every other category takes index 0.
_MARK_CATEGORIES: Final[tuple[str, ...]] = ("other", "Mn", "Mc", "Me")

_KEEP: Final[int] = 0

_HANGUL_SBASE: Final[int] = 0xAC00
_HANGUL_LCOUNT: Final[int] = 19
_HANGUL_VCOUNT: Final[int] = 21
_HANGUL_TCOUNT: Final[int] = 28
_HANGUL_SCOUNT: Final[int] = _HANGUL_LCOUNT * _HANGUL_VCOUNT * _HANGUL_TCOUNT


def _fetch(url: str, expected_sha256: str) -> str:
    # Return the decoded body of a pinned Unicode data file, aborting if its SHA-256 is not *expected_sha256*.
    raw = fetch_bytes(url)
    if (digest := hashlib.sha256(raw).hexdigest()) != expected_sha256:
        msg = f"Unicode source {url} has sha256 {digest}, not the pinned {expected_sha256}; review, then bump the pin"
        raise SystemExit(msg)
    return raw.decode("utf-8")


def _property_ranges(text: str, values: tuple[str, ...]) -> list[tuple[int, int, int]]:
    # Parse a Unicode ``code; value # comment`` property file into code-point-sorted, merged (first, last, index) rows.
    # A value outside *values* is left out, so its code points take index 0.
    spans: list[tuple[int, int, int]] = []
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        span, value = (field.strip() for field in body.split(";", 1))
        if value not in values:
            continue
        first, _, last = span.partition("..")
        spans.append((int(first, 16), int(last or first, 16), values.index(value)))
    rows: list[tuple[int, int, int]] = []
    for first, last, index in sorted(spans):
        if rows and rows[-1][1] + 1 == first and rows[-1][2] == index:
            rows[-1] = (rows[-1][0], last, index)
        else:
            rows.append((first, last, index))
    return rows


def _mapping_ranges(text: str) -> tuple[list[tuple[int, int, int, int, int]], list[int]]:
    # Return the ``(first, last, status, offset, length)`` mapping rows and the flat replacement-code-point pool.
    rows: list[tuple[int, int, int, int, int]] = []
    pool: list[int] = []
    mapped = 1
    ignored: int = 2
    disallowed: int = 3
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = [part.strip() for part in line.split(";")]
        first, _, last = fields[0].partition("..")
        start = int(first, 16)
        end = int(last, 16) if last else start
        if (status := fields[1]) == "mapped":
            replacement = [int(cp, 16) for cp in fields[2].split()] if len(fields) > 2 and fields[2] else []
            rows.append((start, end, mapped, len(pool), len(replacement)))
            pool.extend(replacement)
        elif status == "ignored":
            rows.append((start, end, ignored, 0, 0))
        elif status == "disallowed":  # UTS #46 forbids it in a host: idna.c rejects rather than encode it
            rows.append((start, end, disallowed, 0, 0))
        else:  # valid and deviation (non-transitional keeps it) leave the code point verbatim
            rows.append((start, end, _KEEP, 0, 0))
    rows.sort()
    return _merge_keep_runs(rows), pool


def _merge_keep_runs(rows: list[tuple[int, int, int, int, int]]) -> list[tuple[int, int, int, int, int]]:
    # Fuse adjacent keep rows so the binary-searched table stays compact.
    merged: list[tuple[int, int, int, int, int]] = []
    for row in rows:
        if merged and row[2] == _KEEP and merged[-1][2] == _KEEP and row[0] == merged[-1][1] + 1:
            first, _, status, offset, length = merged[-1]
            merged[-1] = (first, row[1], status, offset, length)
        else:
            merged.append(row)
    return merged


def _combining_and_decomposition(text: str) -> tuple[dict[int, int], dict[int, list[int]]]:
    # Return the canonical combining class per code point and the raw (one-step) canonical decompositions.
    combining: dict[int, int] = {}
    decomposition: dict[int, list[int]] = {}
    for raw in text.splitlines():
        fields = raw.split(";")
        code = int(fields[0], 16)
        if (klass := int(fields[3])) != 0:
            combining[code] = klass
        if (mapping := fields[5]) and not mapping.startswith("<"):
            decomposition[code] = [int(cp, 16) for cp in mapping.split()]
    return combining, decomposition


def _composition_exclusions(text: str) -> set[int]:
    # Return the Full_Composition_Exclusion code points, the canonical decompositions that never recompose.
    excluded: set[int] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "Full_Composition_Exclusion" not in line:
            continue
        first, _, last = line.split(";")[0].strip().partition("..")
        excluded.update(range(int(first, 16), (int(last, 16) if last else int(first, 16)) + 1))
    return excluded


def _nfc_quick_check(text: str) -> list[tuple[int, int, int]]:
    # Return sorted ``(first, last, value)`` NFC quick-check No/Maybe ranges; absent code points are Yes.
    ranges: list[tuple[int, int, int]] = []
    for raw in text.splitlines():
        fields = [field.strip() for field in raw.split("#", 1)[0].split(";")]
        if len(fields) != 3 or fields[1] != "NFC_QC" or fields[2] not in {"N", "M"}:
            continue
        first, _, last = fields[0].partition("..")
        ranges.append((int(first, 16), int(last, 16) if last else int(first, 16), 1 if fields[2] == "N" else 2))
    ranges.sort()
    merged: list[tuple[int, int, int]] = []
    for first, last, value in ranges:
        if merged and merged[-1][1] == first - 1 and merged[-1][2] == value:
            merged[-1] = (merged[-1][0], last, value)
        else:
            merged.append((first, last, value))
    return merged


def _composition_pairs(decomposition: dict[int, list[int]], excluded: set[int]) -> list[tuple[int, int, int]]:
    # Return the sorted ``(first, second, composed)`` canonical compositions, dropping the excluded targets.
    pairs = [
        (mapping[0], mapping[1], code)
        for code, mapping in decomposition.items()
        if len(mapping) == 2 and code not in excluded
    ]
    pairs.sort()
    return pairs


def _hangul_decompose(code: int) -> list[int]:
    # Return the canonical decomposition of a Hangul syllable into leading, vowel, and optional trailing jamo.
    index = code - _HANGUL_SBASE
    leading = 0x1100 + index // (_HANGUL_VCOUNT * _HANGUL_TCOUNT)
    jamo = [leading, (0x1161 + index % (_HANGUL_VCOUNT * _HANGUL_TCOUNT) // _HANGUL_TCOUNT)]
    if index % _HANGUL_TCOUNT:
        jamo.append(0x11A7 + index % _HANGUL_TCOUNT)
    return jamo


def _decompose(text: str, full: dict[int, list[int]]) -> list[int]:
    # Fully canonically decompose *text* (Hangul arithmetically, the rest through the table).
    out: list[int] = []
    for char in text:
        code = ord(char)
        if _HANGUL_SBASE <= code < _HANGUL_SBASE + _HANGUL_SCOUNT:
            out.extend(_hangul_decompose(code))
        else:
            out.extend(full.get(code, [code]))
    return out


def _compose_pair(first: int, second: int, compose: dict[tuple[int, int], int]) -> int:
    # Return the canonical composition of a pair (Hangul arithmetically, the rest tabled), -1 when they do not.
    lindex = first - 0x1100
    if 0 <= lindex < _HANGUL_LCOUNT and 0x1161 <= second <= 0x1175:
        return _HANGUL_SBASE + (lindex * _HANGUL_VCOUNT + second - 0x1161) * _HANGUL_TCOUNT
    sindex = first - _HANGUL_SBASE
    if 0 <= sindex < _HANGUL_SCOUNT and sindex % _HANGUL_TCOUNT == 0 and 0x11A8 <= second <= 0x11C2:
        return first + second - 0x11A7
    return compose.get((first, second), -1)


def _nfc(text: str, combining: dict[int, int], full: dict[int, list[int]], pairs: list[tuple[int, int, int]]) -> str:
    # Normalize *text* to NFC using only the generated tables, the reference the C port must match.
    decomposed = _decompose(text, full)
    # Put each maximal combining-mark run of *decomposed* into canonical order (a stable sort by combining class).
    start = 0
    while start < len(decomposed):
        if combining.get(decomposed[start], 0) == 0:
            start += 1
            continue
        end = start
        while end < len(decomposed) and combining.get(decomposed[end], 0) != 0:
            end += 1
        decomposed[start:end] = sorted(decomposed[start:end], key=lambda cp: combining.get(cp, 0))
        start = end
    compose = {(first, second): composed for first, second, composed in pairs}
    result: list[int] = []
    starter_at = -1
    last_class = 0
    for code in decomposed:
        klass = combining.get(code, 0)
        if (
            starter_at >= 0
            and (last_class < klass or last_class == 0)
            and (composed := _compose_pair(result[starter_at], code, compose)) >= 0
        ):
            result[starter_at] = composed
            continue
        if klass == 0:
            starter_at = len(result)
        last_class = klass
        result.append(code)
    return "".join(chr(code) for code in result)


def _self_check(combining: dict[int, int], full: dict[int, list[int]], pairs: list[tuple[int, int, int]]) -> None:
    # Fail the build if the table-driven NFC disagrees with the interpreter over the decomposable code points.
    samples = [chr(code) for code in {*full, *combining, *range(0x1100, 0x1113), *range(0xAC00, 0xAC00 + 40)}]
    samples += [chr(base) + chr(mark) for base in (0x0061, 0x1112) for mark in (0x0300, 0x0301, 0x0323, 0x11A8)]
    mismatched = [text for text in samples if _nfc(text, combining, full, pairs) != unicodedata.normalize("NFC", text)]
    if mismatched:
        msg = f"table-driven NFC disagrees with unicodedata on {len(mismatched)} inputs, first {mismatched[0]!r}"
        raise SystemExit(msg)


def _property_table(name: str, rows: list[tuple[int, int, int]]) -> str:
    # Render the (first, last, value) rows of one property as the C array th_idna_<name>, with its count.
    body = _wrap(f"{{0x{first:X}, 0x{last:X}, {value}}}" for first, last, value in rows)
    return (
        f"static const int th_idna_{name}_count = {len(rows)};\n"
        f"static const th_idna_property_row th_idna_{name}[] = {{\n{body}\n}};\n\n"
    )


def _emit(
    mapping: tuple[list[tuple[int, int, int, int, int]], list[int]],
    combining: dict[int, int],
    full: dict[int, list[int]],
    normalization: tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]],
    properties: tuple[list[tuple[int, int, int]], list[tuple[int, int, int]], list[tuple[int, int, int]]],
) -> str:
    # Render the generated C header from the mapping, normalization and bidi/joining tables.
    ranges, pool = mapping
    pairs, quick = normalization
    map_rows = "\n".join(
        f"    {{0x{first:X}, 0x{last:X}, {status}, {offset}, {length}}},"
        for first, last, status, offset, length in ranges
    )
    pool_rows = _wrap(f"0x{cp:X}" for cp in pool) if pool else "    0"
    ccc_items = sorted(combining.items())
    ccc_rows = _wrap(f"{{0x{cp:X}, {klass}}}" for cp, klass in ccc_items)
    decomp_offsets: dict[int, tuple[int, int]] = {}
    decomp_pool: list[int] = []
    for code in sorted(full):
        sequence = full[code]
        decomp_offsets[code] = (len(decomp_pool), len(sequence))
        decomp_pool.extend(sequence)
    decomp_rows = _wrap(f"{{0x{cp:X}, {decomp_offsets[cp][0]}, {decomp_offsets[cp][1]}}}" for cp in sorted(full))
    decomp_pool_rows = _wrap(f"0x{cp:X}" for cp in decomp_pool)
    comp_rows = _wrap(f"{{0x{first:X}, 0x{second:X}, 0x{composed:X}}}" for first, second, composed in pairs)
    quick_rows = _wrap(f"{{0x{first:X}, 0x{last:X}, {value}}}" for first, last, value in quick)
    return (
        "/* Auto-generated by tools/generate_idna.py - do not edit. */\n"
        f"/* Unicode {_UNICODE_VERSION} UTS #46 mapping (Transitional_Processing=false, UseSTD3ASCIIRules=false) and\n"
        "   the canonical combining classes, decompositions, and compositions of Normalization Form C, and the bidi\n"
        "   class, joining type and mark category ranges the RFC 5893, ContextJ and label-start checks read. */\n\n"
        "#ifndef TURBOHTML_IDNA_TABLE_H\n"
        "#define TURBOHTML_IDNA_TABLE_H\n\n"
        "#include <stdint.h>\n\n"
        f'#define TH_IDNA_UNICODE_VERSION "{_UNICODE_VERSION}"\n\n'
        "/* status: 0 keep the code point, 1 replace it with map_pool[offset, offset+length), 2 drop it,\n"
        "   3 disallow it (UTS #46 forbids it in a host, so ToASCII rejects the whole host). */\n"
        "typedef struct {\n"
        "    uint32_t first;\n"
        "    uint32_t last;\n"
        "    uint8_t status;\n"
        "    uint32_t offset;\n"
        "    uint8_t length;\n"
        "} th_idna_map_row;\n\n"
        f"static const int th_idna_map_count = {len(ranges)};\n"
        "static const th_idna_map_row th_idna_map[] = {\n"
        f"{map_rows}\n"
        "};\n\n"
        f"static const uint32_t th_idna_map_pool[] = {{\n{pool_rows}\n}};\n\n"
        "/* Canonical combining class, sorted by code point; a code point absent from the table has class 0. */\n"
        "typedef struct {\n"
        "    uint32_t code;\n"
        "    uint8_t ccc;\n"
        "} th_idna_ccc_row;\n\n"
        f"static const int th_idna_ccc_count = {len(ccc_items)};\n"
        f"static const th_idna_ccc_row th_idna_ccc[] = {{\n{ccc_rows}\n}};\n\n"
        "/* Full canonical decomposition, sorted by code point: decomp_pool[offset, offset+length) is the run. */\n"
        "typedef struct {\n"
        "    uint32_t code;\n"
        "    uint32_t offset;\n"
        "    uint8_t length;\n"
        "} th_idna_decomp_row;\n\n"
        f"static const int th_idna_decomp_count = {len(full)};\n"
        f"static const th_idna_decomp_row th_idna_decomp[] = {{\n{decomp_rows}\n}};\n\n"
        f"static const uint32_t th_idna_decomp_pool[] = {{\n{decomp_pool_rows}\n}};\n\n"
        "/* Canonical composition, sorted by (first, second): the pair composes to `composed`. */\n"
        "typedef struct {\n"
        "    uint32_t first;\n"
        "    uint32_t second;\n"
        "    uint32_t composed;\n"
        "} th_idna_comp_row;\n\n"
        f"static const int th_idna_comp_count = {len(pairs)};\n"
        "/* The lowest `second` of any pair. Sorting by (first, second) leaves no row holding this bound the\n"
        "   way th_idna_ccc[0].code holds the combining table's, so table_compose reads it to reject a pair\n"
        "   below every composition without probing. */\n"
        f"static const uint32_t th_idna_comp_second_min = 0x{min(second for _, second, _ in pairs):X};\n"
        f"static const th_idna_comp_row th_idna_comp[] = {{\n{comp_rows}\n}};\n\n"
        "/* NFC quick-check, sorted by range: 1 No, 2 Maybe; a code point absent from the table is Yes. */\n"
        "typedef struct {\n"
        "    uint32_t first;\n"
        "    uint32_t last;\n"
        "    uint8_t value;\n"
        "} th_idna_qc_row;\n\n"
        f"static const int th_idna_qc_count = {len(quick)};\n"
        f"static const th_idna_qc_row th_idna_qc[] = {{\n{quick_rows}\n}};\n\n"
        "/* A code point's bidi class index into the order of _BIDI_CLASSES, its joining type index into the order of\n"
        "   _JOINING_TYPES, and its mark category index into _MARK_CATEGORIES; a code point in no row takes index 0:\n"
        "   class L, joining type U, category other. */\n"
        "typedef struct {\n"
        "    uint32_t first;\n"
        "    uint32_t last;\n"
        "    uint8_t value;\n"
        f"}} th_idna_property_row;\n\n"
        f"{''.join(starmap(_property_table, zip(('bidi', 'joining', 'mark'), properties, strict=True)))}"
        "#endif /* TURBOHTML_IDNA_TABLE_H */\n"
    )


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


def generate(out_path: Path) -> None:
    # Write the generated IDNA/NFC C header to *out_path*.
    ranges, pool = _mapping_ranges(_fetch(f"{_IDNA_BASE}/IdnaMappingTable.txt", _IDNA_MAPPING_SHA256))
    combining, raw_decomposition = _combining_and_decomposition(
        _fetch(f"{_UCD_BASE}/UnicodeData.txt", "ff58e5823bd095166564a006e47d111130813dcf8bf234ef79fa51a870edb48f")
    )
    # Expand each canonical decomposition recursively to its non-decomposable code points.

    def expand(code: int) -> list[int]:
        if code not in raw_decomposition:
            return [code]
        return [leaf for part in raw_decomposition[code] for leaf in expand(part)]

    full = {code: expand(code) for code in raw_decomposition}
    derived = _fetch(
        f"{_UCD_BASE}/DerivedNormalizationProps.txt", "4d4c03892dea9146d674b686e495df2d55a28d071ac474041d73518f887abddc"
    )
    pairs = _composition_pairs(raw_decomposition, _composition_exclusions(derived))
    quick = _nfc_quick_check(derived)
    bidi = _property_ranges(_fetch(f"{_UCD_BASE}/extracted/DerivedBidiClass.txt", _BIDI_CLASS_SHA256), _BIDI_CLASSES)
    joining = _property_ranges(
        _fetch(f"{_UCD_BASE}/extracted/DerivedJoiningType.txt", _JOINING_TYPE_SHA256), _JOINING_TYPES
    )
    marks = _property_ranges(
        _fetch(f"{_UCD_BASE}/extracted/DerivedGeneralCategory.txt", _GENERAL_CATEGORY_SHA256), _MARK_CATEGORIES
    )
    _self_check(combining, full, pairs)
    out_path.write_text(
        _emit((ranges, pool), combining, full, (pairs, quick), (bidi, joining, marks)), encoding="utf-8"
    )
    print(
        f"wrote {out_path}: {len(ranges)} map rows, {len(full)} decompositions, {len(pairs)} compositions, "
        f"{len(quick)} quick-check ranges, {len(bidi)} bidi ranges, {len(joining)} joining ranges, "
        f"{len(marks)} mark ranges"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        msg = "usage: generate_idna.py OUTPUT_HEADER"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]))
