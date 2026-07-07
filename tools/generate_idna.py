"""
Generate src/turbohtml/_c/data/idna_table.h from the pinned Unicode Character Database.

:func:`turbohtml.extract.normalize_url` turns a Unicode host into its ASCII form the way the WHATWG URL Standard's host
parser does: Unicode IDNA ``ToASCII`` with ``Transitional_Processing=false`` and ``UseSTD3ASCIIRules=false`` (the
non-strict flag a crawl-oriented cleaner wants), which is UTS #46. That needs three pieces of Unicode data, all pinned
to one version so a rebuild is deterministic: the UTS #46 mapping table (which code points are kept, mapped, or
dropped), and the canonical combining classes, decompositions, and compositions Normalization Form C walks. This tool
downloads them at the pinned version and writes one generated header the ``idna.c`` engine includes.

The mapping status is collapsed to the three outcomes the mechanical ToASCII output depends on -- keep, map, ignore --
because ``valid``, ``deviation`` (non-transitional keeps the code point), and ``disallowed`` all leave the code point in
place: the validity of a disallowed code point is an advisory error the best-effort host cleaner records by producing
the punycode of the label anyway, exactly as the UTS #46 test vectors' toASCII column does. Each fetched file is also
pinned to the SHA-256 of its exact bytes, so a rebuild refuses a silently rewritten or poisoned mirror the version
pin alone would not catch. The pinned mapping file is
the "Compatible Preprocessing" variant, whose ``valid`` rows already fold in ``UseSTD3ASCIIRules=false`` (ASCII symbols
such as ``_`` are ``valid`` rather than ``disallowed_STD3_valid``), so no STD3 toggle is needed at run time.

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
from pathlib import Path
from typing import TYPE_CHECKING

from httpfetch import fetch_bytes

if TYPE_CHECKING:
    from collections.abc import Iterable

# Unicode 16.0.0, the version CPython 3.14's unicodedata ships, so the C NFC step and the interpreter agree. The IDNA
# files live under /Public/idna/<version>/ and the core UCD under /Public/<version>/ucd/; pin the version, review the
# idna_table.h diff on a bump. CPython's own Unicode version is unicodedata.unidata_version.
UNICODE_VERSION = "16.0.0"
_IDNA_BASE = f"https://www.unicode.org/Public/idna/{UNICODE_VERSION}"
_UCD_BASE = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd"

# Pin the SHA-256 of the exact bytes of each fetched file, not just the version: a version pin fixes which release the
# rebuild targets, but a poisoned or silently rewritten mirror could still serve altered content under that version's
# stable URL, and no review of idna.c would catch a bad table. A rebuild recomputes each digest and aborts on a
# mismatch. Bump a digest deliberately alongside UNICODE_VERSION and review the idna_table.h diff. All match the
# committed table at Unicode 16.0.0.
_IDNA_MAPPING_SHA256 = "6db2ef4ed35f3b3de74ebc2e00404a9607f76d499f576b8d4043cf14f1ed175c"
_UNICODE_DATA_SHA256 = "ff58e5823bd095166564a006e47d111130813dcf8bf234ef79fa51a870edb48f"
_DERIVED_NORM_SHA256 = "4d4c03892dea9146d674b686e495df2d55a28d071ac474041d73518f887abddc"

_KEEP = 0
_MAPPED = 1
_IGNORED = 2

_HANGUL_SBASE = 0xAC00
_HANGUL_LCOUNT = 19
_HANGUL_VCOUNT = 21
_HANGUL_TCOUNT = 28
_HANGUL_SCOUNT = _HANGUL_LCOUNT * _HANGUL_VCOUNT * _HANGUL_TCOUNT


def _fetch(url: str, expected_sha256: str) -> str:
    """Return the decoded body of a pinned Unicode data file, aborting if its SHA-256 is not *expected_sha256*."""
    raw = fetch_bytes(url)
    if (digest := hashlib.sha256(raw).hexdigest()) != expected_sha256:
        msg = f"Unicode source {url} has sha256 {digest}, not the pinned {expected_sha256}; review, then bump the pin"
        raise SystemExit(msg)
    return raw.decode("utf-8")


def _mapping_ranges(text: str) -> tuple[list[tuple[int, int, int, int, int]], list[int]]:
    """Return the ``(first, last, status, offset, length)`` mapping rows and the flat replacement-code-point pool."""
    rows: list[tuple[int, int, int, int, int]] = []
    pool: list[int] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = [part.strip() for part in line.split(";")]
        first, _, last = fields[0].partition("..")
        start = int(first, 16)
        end = int(last, 16) if last else start
        status = fields[1]
        if status == "mapped":
            replacement = [int(cp, 16) for cp in fields[2].split()] if len(fields) > 2 and fields[2] else []
            rows.append((start, end, _MAPPED, len(pool), len(replacement)))
            pool.extend(replacement)
        elif status == "ignored":
            rows.append((start, end, _IGNORED, 0, 0))
        else:  # valid, deviation (non-transitional keeps it), disallowed (advisory) all keep the code point verbatim
            rows.append((start, end, _KEEP, 0, 0))
    rows.sort()
    return _merge_keep_runs(rows), pool


def _merge_keep_runs(rows: list[tuple[int, int, int, int, int]]) -> list[tuple[int, int, int, int, int]]:
    """Fuse adjacent keep rows so the binary-searched table stays compact; mapped/ignored rows never merge."""
    merged: list[tuple[int, int, int, int, int]] = []
    for row in rows:
        if merged and row[2] == _KEEP and merged[-1][2] == _KEEP and row[0] == merged[-1][1] + 1:
            first, _, status, offset, length = merged[-1]
            merged[-1] = (first, row[1], status, offset, length)
        else:
            merged.append(row)
    return merged


def _combining_and_decomposition(text: str) -> tuple[dict[int, int], dict[int, list[int]]]:
    """Return the canonical combining class per code point and the raw (one-step) canonical decompositions."""
    combining: dict[int, int] = {}
    decomposition: dict[int, list[int]] = {}
    for raw in text.splitlines():
        fields = raw.split(";")
        code = int(fields[0], 16)
        if (klass := int(fields[3])) != 0:
            combining[code] = klass
        mapping = fields[5]
        if mapping and not mapping.startswith("<"):
            decomposition[code] = [int(cp, 16) for cp in mapping.split()]
    return combining, decomposition


def _full_decomposition(decomposition: dict[int, list[int]]) -> dict[int, list[int]]:
    """Expand each canonical decomposition recursively to its non-decomposable code points."""

    def expand(code: int) -> list[int]:
        if code not in decomposition:
            return [code]
        return [leaf for part in decomposition[code] for leaf in expand(part)]

    return {code: expand(code) for code in decomposition}


def _composition_exclusions(text: str) -> set[int]:
    """Return the Full_Composition_Exclusion code points, the canonical decompositions that never recompose."""
    excluded: set[int] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "Full_Composition_Exclusion" not in line:
            continue
        code_range = line.split(";")[0].strip()
        first, _, last = code_range.partition("..")
        excluded.update(range(int(first, 16), (int(last, 16) if last else int(first, 16)) + 1))
    return excluded


def _composition_pairs(decomposition: dict[int, list[int]], excluded: set[int]) -> list[tuple[int, int, int]]:
    """Return the sorted ``(first, second, composed)`` canonical compositions, dropping the excluded targets."""
    pairs = [
        (mapping[0], mapping[1], code)
        for code, mapping in decomposition.items()
        if len(mapping) == 2 and code not in excluded
    ]
    pairs.sort()
    return pairs


def _hangul_decompose(code: int) -> list[int]:
    """Return the canonical decomposition of a Hangul syllable into leading, vowel, and optional trailing jamo."""
    index = code - _HANGUL_SBASE
    leading = 0x1100 + index // (_HANGUL_VCOUNT * _HANGUL_TCOUNT)
    vowel = 0x1161 + index % (_HANGUL_VCOUNT * _HANGUL_TCOUNT) // _HANGUL_TCOUNT
    jamo = [leading, vowel]
    if index % _HANGUL_TCOUNT:
        jamo.append(0x11A7 + index % _HANGUL_TCOUNT)
    return jamo


def _decompose(text: str, full: dict[int, list[int]]) -> list[int]:
    """Fully canonically decompose *text* (Hangul arithmetically, the rest through the table)."""
    out: list[int] = []
    for char in text:
        code = ord(char)
        if _HANGUL_SBASE <= code < _HANGUL_SBASE + _HANGUL_SCOUNT:
            out.extend(_hangul_decompose(code))
        else:
            out.extend(full.get(code, [code]))
    return out


def _reorder(seq: list[int], combining: dict[int, int]) -> None:
    """Put each maximal combining-mark run of *seq* into canonical order (a stable sort by combining class)."""
    start = 0
    while start < len(seq):
        if combining.get(seq[start], 0) == 0:
            start += 1
            continue
        end = start
        while end < len(seq) and combining.get(seq[end], 0) != 0:
            end += 1
        seq[start:end] = sorted(seq[start:end], key=lambda cp: combining.get(cp, 0))
        start = end


def _compose_pair(first: int, second: int, compose: dict[tuple[int, int], int]) -> int:
    """Return the canonical composition of a pair (Hangul arithmetically, the rest tabled), -1 when they do not."""
    lindex = first - 0x1100
    if 0 <= lindex < _HANGUL_LCOUNT and 0x1161 <= second <= 0x1175:
        return _HANGUL_SBASE + (lindex * _HANGUL_VCOUNT + second - 0x1161) * _HANGUL_TCOUNT
    sindex = first - _HANGUL_SBASE
    if 0 <= sindex < _HANGUL_SCOUNT and sindex % _HANGUL_TCOUNT == 0 and 0x11A8 <= second <= 0x11C2:
        return first + second - 0x11A7
    return compose.get((first, second), -1)


def _nfc(text: str, combining: dict[int, int], full: dict[int, list[int]], pairs: list[tuple[int, int, int]]) -> str:
    """Normalize *text* to NFC using only the generated tables, the reference the C port must match."""
    decomposed = _decompose(text, full)
    _reorder(decomposed, combining)
    compose = {(first, second): composed for first, second, composed in pairs}
    result: list[int] = []
    starter_at = -1
    last_class = 0
    for code in decomposed:
        klass = combining.get(code, 0)
        if starter_at >= 0 and (last_class < klass or last_class == 0):
            composed = _compose_pair(result[starter_at], code, compose)
            if composed >= 0:
                result[starter_at] = composed
                continue
        if klass == 0:
            starter_at = len(result)
        last_class = klass
        result.append(code)
    return "".join(chr(code) for code in result)


def _self_check(combining: dict[int, int], full: dict[int, list[int]], pairs: list[tuple[int, int, int]]) -> None:
    """Fail the build if the table-driven NFC disagrees with the interpreter over the decomposable code points."""
    samples = [chr(code) for code in {*full, *combining, *range(0x1100, 0x1113), *range(0xAC00, 0xAC00 + 40)}]
    samples += [chr(base) + chr(mark) for base in (0x0061, 0x1112) for mark in (0x0300, 0x0301, 0x0323, 0x11A8)]
    mismatched = [text for text in samples if _nfc(text, combining, full, pairs) != unicodedata.normalize("NFC", text)]
    if mismatched:
        msg = f"table-driven NFC disagrees with unicodedata on {len(mismatched)} inputs, first {mismatched[0]!r}"
        raise SystemExit(msg)


def _emit(
    ranges: list[tuple[int, int, int, int, int]],
    pool: list[int],
    combining: dict[int, int],
    full: dict[int, list[int]],
    pairs: list[tuple[int, int, int]],
) -> str:
    """Render the generated C header from the mapping, combining-class, decomposition, and composition tables."""
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
    return (
        "/* Auto-generated by tools/generate_idna.py - do not edit. */\n"
        f"/* Unicode {UNICODE_VERSION} UTS #46 mapping (Transitional_Processing=false, UseSTD3ASCIIRules=false) and\n"
        "   the canonical combining classes, decompositions, and compositions of Normalization Form C. */\n\n"
        "#ifndef TURBOHTML_IDNA_TABLE_H\n"
        "#define TURBOHTML_IDNA_TABLE_H\n\n"
        "#include <stdint.h>\n\n"
        f'#define TH_IDNA_UNICODE_VERSION "{UNICODE_VERSION}"\n\n'
        "/* status: 0 keep the code point, 1 replace it with map_pool[offset, offset+length), 2 drop it. */\n"
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
        f"static const th_idna_comp_row th_idna_comp[] = {{\n{comp_rows}\n}};\n\n"
        "#endif /* TURBOHTML_IDNA_TABLE_H */\n"
    )


def _wrap(items: Iterable[str]) -> str:
    """Return the items as brace-initializer rows wrapped near 116 columns, each line indented four spaces."""
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
    """Write the generated IDNA/NFC C header to *out_path*."""
    ranges, pool = _mapping_ranges(_fetch(f"{_IDNA_BASE}/IdnaMappingTable.txt", _IDNA_MAPPING_SHA256))
    combining, raw_decomposition = _combining_and_decomposition(
        _fetch(f"{_UCD_BASE}/UnicodeData.txt", _UNICODE_DATA_SHA256)
    )
    full = _full_decomposition(raw_decomposition)
    excluded = _composition_exclusions(_fetch(f"{_UCD_BASE}/DerivedNormalizationProps.txt", _DERIVED_NORM_SHA256))
    pairs = _composition_pairs(raw_decomposition, excluded)
    _self_check(combining, full, pairs)
    out_path.write_text(_emit(ranges, pool, combining, full, pairs), encoding="utf-8")
    print(f"wrote {out_path}: {len(ranges)} map rows, {len(full)} decompositions, {len(pairs)} compositions")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        msg = "usage: generate_idna.py OUTPUT_HEADER"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]))
