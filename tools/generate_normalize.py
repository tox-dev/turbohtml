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
gives from downloaded files. The one piece :mod:`unicodedata` does not expose is the quick-check property
(NFC_QC/NFKC_QC No/Maybe), so that is parsed from ``DerivedNormalizationProps.txt``, fetched at the pinned version and
verified against the SHA-256 of its exact bytes (a poisoned mirror would still serve altered content under the stable
version URL). Hangul syllables are decomposed and composed arithmetically in C (Unicode 3.12), so their 11k rows never
enter any table.

Usage:  python tools/generate_normalize.py src/turbohtml/_c/data/normalize_table.h
"""

from __future__ import annotations

import hashlib
import sys
import unicodedata
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

_Form = Literal["NFC", "NFD", "NFKC", "NFKD"]

# The Unicode version the committed table is pinned to. It must equal the interpreter's own unicodedata version so the
# tables generated from unicodedata match what the C engine is validated against; bump both together and review the
# normalize_table.h diff. Unicode 16.0.0 is what CPython 3.14 ships and what idna_table.h already pins.
UNICODE_VERSION = "16.0.0"
_UCD_BASE = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd"

# SHA-256 of DerivedNormalizationProps.txt at Unicode 16.0.0 (the same file idna_table.h pins), the only download this
# generator needs: unicodedata supplies everything except the quick-check property.
_DERIVED_NORM_SHA256 = "4d4c03892dea9146d674b686e495df2d55a28d071ac474041d73518f887abddc"

_HANGUL_SBASE = 0xAC00
_HANGUL_SCOUNT = 11172
_SURROGATE_LO = 0xD800
_SURROGATE_HI = 0xDFFF

_QC_NO = 1
_QC_MAYBE = 2


def _fetch(url: str, expected_sha256: str) -> str:
    """Return the decoded body of a pinned Unicode data file, aborting if its SHA-256 is not *expected_sha256*."""
    with urllib.request.urlopen(url) as response:  # noqa: S310 -- url is always a pinned https unicode.org constant
        raw = response.read()
    if (digest := hashlib.sha256(raw).hexdigest()) != expected_sha256:
        msg = f"Unicode source {url} has sha256 {digest}, not the pinned {expected_sha256}; review, then bump the pin"
        raise SystemExit(msg)
    return raw.decode("utf-8")


def _is_hangul_syllable(code: int) -> bool:
    """Whether *code* is a precomposed Hangul syllable, whose (de)composition C handles arithmetically."""
    return _HANGUL_SBASE <= code < _HANGUL_SBASE + _HANGUL_SCOUNT


def _code_points() -> Iterable[int]:
    """Every assignable scalar value: the whole space minus the surrogate block, which no string can hold."""
    for code in range(0x110000):
        if _SURROGATE_LO <= code <= _SURROGATE_HI:
            continue
        yield code


def _combining() -> dict[int, int]:
    """Map each code point with a non-zero canonical combining class to that class, read from unicodedata."""
    return {code: klass for code in _code_points() if (klass := unicodedata.combining(chr(code)))}


def _decompositions() -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """
    Return the full canonical (NFD) and compatibility (NFKD) decompositions of every non-Hangul code point.

    ``unicodedata.normalize`` on a single character yields its fully-resolved, canonically-ordered decomposition, so the
    C engine never recurses. The compatibility table stores only the code points whose NFKD differs from their NFD; the
    engine falls back to the canonical table for the rest, halving the compatibility rows.
    """
    canonical: dict[int, list[int]] = {}
    compatibility: dict[int, list[int]] = {}
    for code in _code_points():
        if _is_hangul_syllable(code):
            continue
        char = chr(code)
        if (nfd := unicodedata.normalize("NFD", char)) != char:
            canonical[code] = [ord(part) for part in nfd]
        if (nfkd := unicodedata.normalize("NFKD", char)) != nfd:
            compatibility[code] = [ord(part) for part in nfkd]
    return canonical, compatibility


def _composition_pairs() -> list[tuple[int, int, int]]:
    """
    Return the canonical recomposition pairs ``(first, second, composed)``, sorted.

    A code point is a primary composite iff its canonical decomposition is exactly two code points that NFC recomposes
    back to it; that single test folds in every exclusion (composition exclusions, singletons, and non-starter
    decompositions all fail to recompose), so it is the authoritative source unicodedata already carries.
    """
    pairs: list[tuple[int, int, int]] = []
    for code in _code_points():
        if _is_hangul_syllable(code):
            continue
        decomposition = unicodedata.decomposition(chr(code))
        if not decomposition or decomposition.startswith("<"):
            continue
        parts = decomposition.split()
        if len(parts) != 2:
            continue
        first, second = int(parts[0], 16), int(parts[1], 16)
        if unicodedata.normalize("NFC", chr(first) + chr(second)) == chr(code):
            pairs.append((first, second, code))
    pairs.sort()
    return pairs


def _quick_check(text: str) -> list[tuple[int, int, int, int]]:
    """
    Parse the NFC_QC/NFKC_QC property into ``(first, last, nfc, nfkc)`` ranges, sorted, coalescing equal neighbors.

    The decompose forms (NFD/NFKD) need no quick-check table: a code point is NFD_QC=No exactly when it has a canonical
    decomposition, which the engine already reads from the decomposition tables (Hangul arithmetically). Only the
    compose forms carry a Maybe value (a mark that may fold into a preceding starter), so they are tabled here.
    """
    values: dict[int, list[int]] = {}
    for line in text.splitlines():
        payload = line.split("#", 1)[0]
        fields = [field.strip() for field in payload.split(";")]
        if len(fields) != 3 or fields[1] not in {"NFC_QC", "NFKC_QC"} or fields[2] not in {"N", "M"}:
            continue
        lo_hi = fields[0].split("..")
        low = int(lo_hi[0], 16)
        high = int(lo_hi[-1], 16)
        value = _QC_NO if fields[2] == "N" else _QC_MAYBE
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
    """Decompose *text* through *decomp_map* (Hangul arithmetically), the Python mirror of the C decompose step."""
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
    """Sort each combining-mark run of *seq* into canonical order in place (a stable insertion sort by class)."""
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
    """Recompose the ordered *seq*, folding each unblocked mark into its starter, the mirror of the C compose step."""
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
    """Reconstruct all four forms from the generated tables and confirm they match unicodedata for every code point."""
    compose = {(first, second): composed for first, second, composed in pairs}
    nfkd = {**canonical, **compatibility}
    maps: dict[_Form, dict[int, list[int]]] = {"NFC": canonical, "NFD": canonical, "NFKC": nfkd, "NFKD": nfkd}
    for code in _code_points():
        char = chr(code)
        for form, decomp_map in maps.items():
            seq = _reference_decompose(char, decomp_map)
            _reference_reorder(seq, combining)
            if form in {"NFC", "NFKC"}:
                seq = _reference_compose(seq, combining, compose)
            if "".join(chr(point) for point in seq) != unicodedata.normalize(form, char):
                msg = f"self-check failed for U+{code:04X} {form}"
                raise SystemExit(msg)


def _compose_pair(first: int, second: int, compose: dict[tuple[int, int], int]) -> int:
    """Return the canonical composition of the pair: Hangul arithmetically, everything else from the table, else 0."""
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


def _decomp_block(name: str, table: dict[int, list[int]]) -> str:
    """Render a decomposition row array and its shared pool for *table*."""
    offsets: dict[int, tuple[int, int]] = {}
    pool: list[int] = []
    for code in sorted(table):
        run = table[code]
        offsets[code] = (len(pool), len(run))
        pool.extend(run)
    rows = _wrap(f"{{0x{code:X}, {offsets[code][0]}, {offsets[code][1]}}}" for code in sorted(table))
    pool_rows = _wrap(f"0x{code:X}" for code in pool)
    return (
        f"static const int th_norm_{name}_count = {len(table)};\n"
        f"static const th_norm_decomp_row th_norm_{name}[] = {{\n{rows}\n}};\n\n"
        f"static const uint32_t th_norm_{name}_pool[] = {{\n{pool_rows}\n}};\n\n"
    )


def _emit(
    canonical: dict[int, list[int]],
    compatibility: dict[int, list[int]],
    combining: dict[int, int],
    pairs: list[tuple[int, int, int]],
    quick: list[tuple[int, int, int, int]],
) -> str:
    """Render the generated C header from the combining-class, decomposition, composition, and quick-check tables."""
    ccc_items = sorted(combining.items())
    ccc_rows = _wrap(f"{{0x{code:X}, {klass}}}" for code, klass in ccc_items)
    comp_rows = _wrap(f"{{0x{first:X}, 0x{second:X}, 0x{composed:X}}}" for first, second, composed in pairs)
    quick_rows = _wrap(f"{{0x{first:X}, 0x{last:X}, {nfc}, {nfkc}}}" for first, last, nfc, nfkc in quick)
    canon_text = _decomp_block("canon", canonical)
    compat_text = _decomp_block("compat", compatibility)
    max_expansion = max(len(run) for run in (*canonical.values(), *compatibility.values()))
    return (
        "/* Auto-generated by tools/generate_normalize.py - do not edit. */\n"
        f"/* Unicode {UNICODE_VERSION} normalization (UAX #15): the canonical combining classes, the full canonical\n"
        "   (NFD) and compatibility (NFKD) decompositions, the canonical recomposition pairs, and the NFC/NFKC\n"
        "   quick-check property. Hangul is (de)composed arithmetically in C, so its syllables need no rows. */\n\n"
        "#ifndef TURBOHTML_NORMALIZE_TABLE_H\n"
        "#define TURBOHTML_NORMALIZE_TABLE_H\n\n"
        "#include <stdint.h>\n\n"
        f'#define TH_NORM_UNICODE_VERSION "{UNICODE_VERSION}"\n'
        "/* The longest full decomposition of any single code point, bounding the output at in_len * this. */\n"
        f"#define TH_NORM_MAX_EXPANSION {max_expansion}\n\n"
        "/* Canonical combining class, sorted by code point; a code point absent from the table has class 0. */\n"
        "typedef struct {\n"
        "    uint32_t code;\n"
        "    uint8_t ccc;\n"
        "} th_norm_ccc_row;\n\n"
        f"static const int th_norm_ccc_count = {len(ccc_items)};\n"
        f"static const th_norm_ccc_row th_norm_ccc[] = {{\n{ccc_rows}\n}};\n\n"
        "/* Full decomposition, sorted by code point: pool[offset, offset+length) is the run. The compatibility table\n"
        "   holds only code points whose NFKD differs from their NFD; the canonical table covers the rest. */\n"
        "typedef struct {\n"
        "    uint32_t code;\n"
        "    uint32_t offset;\n"
        "    uint8_t length;\n"
        "} th_norm_decomp_row;\n\n"
        f"{canon_text}"
        f"{compat_text}"
        "/* Canonical composition, sorted by (first, second): the pair composes to `composed`. */\n"
        "typedef struct {\n"
        "    uint32_t first;\n"
        "    uint32_t second;\n"
        "    uint32_t composed;\n"
        "} th_norm_comp_row;\n\n"
        f"static const int th_norm_comp_count = {len(pairs)};\n"
        f"static const th_norm_comp_row th_norm_comp[] = {{\n{comp_rows}\n}};\n\n"
        "/* NFC/NFKC quick-check, sorted by range: 0 Yes (absent), 1 No, 2 Maybe. Decompose forms need no table. */\n"
        "typedef struct {\n"
        "    uint32_t first;\n"
        "    uint32_t last;\n"
        "    uint8_t nfc;\n"
        "    uint8_t nfkc;\n"
        "} th_norm_qc_row;\n\n"
        f"static const int th_norm_qc_count = {len(quick)};\n"
        f"static const th_norm_qc_row th_norm_qc[] = {{\n{quick_rows}\n}};\n\n"
        "#endif /* TURBOHTML_NORMALIZE_TABLE_H */\n"
    )


def generate(out_path: Path) -> None:
    """Write the generated normalization C header to *out_path*."""
    if unicodedata.unidata_version != UNICODE_VERSION:
        msg = (
            f"interpreter unicodedata is Unicode {unicodedata.unidata_version}, not the pinned {UNICODE_VERSION}; "
            "run under a matching Python or bump UNICODE_VERSION and review the diff"
        )
        raise SystemExit(msg)
    combining = _combining()
    canonical, compatibility = _decompositions()
    pairs = _composition_pairs()
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
