"""
Generate src/turbohtml/_c/data/encoding_tables.h from the WHATWG Encoding Standard's index tables.

turbohtml used to decode legacy bytes by handing the label's name to a CPython codec. That is wrong: CPython's codecs
predate the Encoding Standard and none of them is the WHATWG decoder of the same name. ``koi8_u`` is KOI8-U where the
spec's ``koi8-u`` is KOI8-RU; ``big5`` covers a strict subset of the spec's Big5 index; ``euc_kr`` and ``shift_jis``
reject thousands of sequences ``cp949`` and ``cp932`` accept; CPython's ``gb18030`` is the 2000 revision and maps twenty
code points into a private-use area the 2005 revision moved; every single-byte codec raises on the C1 bytes the spec
maps to the C1 control itself. Beyond the tables, no CPython codec reproduces the spec's error handling: WHATWG pushes
an ASCII trail byte back onto the stream so ``81 41`` in Big5 emits one U+FFFD and keeps the ``A``, and consumes a
non-ASCII one so ``81 FF`` emits exactly one U+FFFD, not two.

So the decoders are native, and their data comes from the spec's own index tables rather than a hand-transcribed copy.
The tables are stored decode-side only (pointer -> code point); turbohtml never encodes to a legacy encoding.

Layout, following encoding_rs's observation that Big5's astral code points all sit in plane 2: ``th_big5_low`` holds the
low sixteen bits of each pointer's code point and ``th_big5_astral`` is a bitmap of the pointers whose code point needs
``0x20000`` added back. Every other index is a flat ``uint16_t`` array indexed by pointer. Zero is the unmapped
sentinel: no index maps a pointer to U+0000. ``gb18030-ranges`` stays a sorted (pointer, code point) pair list, which
the four-byte decoder binary-searches.

The index data is Copyright WHATWG (Apple, Google, Mozilla, Microsoft) and, incorporated into source code, is licensed
under the BSD 3-Clause License; see licenses/LICENSE-WHATWG.

Usage:  python tools/generate_encoding_tables.py src/turbohtml/_c/data/encoding_tables.h
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from httpfetch import fetch_bytes

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_INDEXES_URL: Final = "https://encoding.spec.whatwg.org/indexes.json"
_INDEXES_SHA256: Final = "b7c8961095f0b4cae8f4a16a5011e7c84047a60c1277e28ee8df9e56074ee509"

# The spec's "legacy single-byte encodings", in the order the C enum uses. A canonical name that shares another's index
# (ISO-8859-8-I is decoded as ISO-8859-8) is not listed: the label table points both at the same slot.
_SINGLE_BYTE: Final[tuple[str, ...]] = (
    "ibm866",
    "iso-8859-2",
    "iso-8859-3",
    "iso-8859-4",
    "iso-8859-5",
    "iso-8859-6",
    "iso-8859-7",
    "iso-8859-8",
    "iso-8859-10",
    "iso-8859-13",
    "iso-8859-14",
    "iso-8859-15",
    "iso-8859-16",
    "koi8-r",
    "koi8-u",
    "macintosh",
    "windows-874",
    "windows-1250",
    "windows-1251",
    "windows-1252",
    "windows-1253",
    "windows-1254",
    "windows-1255",
    "windows-1256",
    "windows-1257",
    "windows-1258",
    "x-mac-cyrillic",
)

# Big5 pointers below this are all unmapped, so the table rebases and the decoder subtracts before indexing.
_BIG5_FIRST: Final = 942

# Every Big5 code point above the BMP lives in plane 2, so the table keeps the low sixteen bits and a per-pointer flag.
_BIG5_PLANE: Final = 0x20000

_REPLACEMENT: Final = 0xFFFD


def _fetch_indexes() -> tuple[dict[str, list[int | None]], list[tuple[int, int]]]:
    """Return the spec's pointer tables and the gb18030 range list, aborting if the bytes are not the pinned ones."""
    raw = fetch_bytes(_INDEXES_URL)
    if (digest := hashlib.sha256(raw).hexdigest()) != _INDEXES_SHA256:
        msg = f"{_INDEXES_URL} has sha256 {digest}, not the pinned {_INDEXES_SHA256}; review, then bump the pin"
        raise SystemExit(msg)
    parsed = json.loads(raw)  # json.loads is Any; the spec's shape is a pointer table per name, plus one range list
    ranges = [(pointer, point) for pointer, point in parsed["gb18030-ranges"]]
    indexes = {name: table for name, table in parsed.items() if name != "gb18030-ranges"}
    return indexes, ranges


def _rows(values: Iterable[int], per_row: int = 16) -> str:
    """Format *values* as comma-separated hex, wrapped to *per_row* per line and indented one level."""
    items = [f"0x{value:04X}" for value in values]
    lines = (", ".join(items[start : start + per_row]) for start in range(0, len(items), per_row))
    return "\n".join(f"    {line}," for line in lines)


def _slot_name(name: str) -> str:
    """Return the C constant naming *name*'s row in th_sb_index: ``x-mac-cyrillic`` -> ``TH_SB_X_MAC_CYRILLIC``."""
    return "TH_SB_" + name.upper().replace("-", "_")


def _single_byte_tables(indexes: dict[str, list[int | None]]) -> tuple[str, str]:
    """Emit the slot constants and the 27 single-byte tables, each covering all 256 bytes."""
    tables = []
    for name in _SINGLE_BYTE:
        # the ASCII half is the identity, so the decoder indexes by the raw byte and needs no compare per byte
        points = list(range(0x80)) + [_REPLACEMENT if point is None else point for point in indexes[name]]
        tables.append(f"    {{ /* {name} */\n{_rows(points)}\n    }},")
    slots = "\n".join(f"#define {_slot_name(name)} {slot}" for slot, name in enumerate(_SINGLE_BYTE))
    body = "\n".join(tables)
    return f"{slots}\n", f"static const uint16_t th_sb_index[TH_SB_INDEX_COUNT][256] = {{\n{body}\n}};\n"


def _big5_tables(index: Sequence[int | None]) -> str:
    """Emit Big5's rebased low-bits table plus the bitmap of pointers whose code point is in plane 2."""
    tail = index[_BIG5_FIRST:]
    low = [0 if point is None else point & 0xFFFF for point in tail]
    bitmap = bytearray((len(tail) + 7) // 8)
    for pointer, point in enumerate(tail):
        if point is not None and point > 0xFFFF:
            if point >> 16 != _BIG5_PLANE >> 16:
                msg = f"Big5 pointer {pointer + _BIG5_FIRST} maps to U+{point:X}, outside plane 2"
                raise SystemExit(msg)
            bitmap[pointer // 8] |= 1 << (pointer % 8)
    bits = "\n".join(
        f"    {line},"
        for line in (", ".join(f"0x{byte:02X}" for byte in bitmap[at : at + 16]) for at in range(0, len(bitmap), 16))
    )
    return (
        f"#define TH_BIG5_FIRST {_BIG5_FIRST}\n"
        f"#define TH_BIG5_PLANE 0x{_BIG5_PLANE:X}\n\n"
        "/* Low sixteen bits of the code point for Big5 pointer TH_BIG5_FIRST + i; 0 is unmapped. */\n"
        f"static const uint16_t th_big5_low[{len(low)}] = {{\n{_rows(low)}\n}};\n\n"
        "/* Bit i is set when th_big5_low[i] needs TH_BIG5_PLANE added back. */\n"
        f"static const uint8_t th_big5_astral[{len(bitmap)}] = {{\n{bits}\n}};\n"
    )


def _flat_table(name: str, index: Sequence[int | None]) -> str:
    """Emit one flat pointer -> code point table; 0 marks an unmapped pointer."""
    points = [0 if point is None else point for point in index]
    if any(point > 0xFFFF for point in points):
        msg = f"index {name} has an astral code point; it cannot use a uint16_t table"
        raise SystemExit(msg)
    if name == "gb18030" and 0 in points:
        # the two-byte decoder indexes this table without a hole test, which only holds while every pointer maps
        msg = "index gb18030 gained an unmapped pointer; the decoder must test for a hole again"
        raise SystemExit(msg)
    return f"static const uint16_t th_{name}[{len(points)}] = {{\n{_rows(points)}\n}};\n"


def _gb18030_ranges(ranges: Sequence[Sequence[int]]) -> str:
    """Emit the sorted (pointer, code point) pairs the gb18030 four-byte decoder binary-searches."""
    rows = "\n".join(f"    {{{pointer}u, 0x{point:04X}u}}," for pointer, point in ranges)
    return (
        "typedef struct {\n    uint32_t pointer;\n    uint32_t code_point;\n} th_gb18030_range;\n\n"
        f"static const th_gb18030_range th_gb18030_ranges[{len(ranges)}] = {{\n{rows}\n}};\n"
    )


def generate(out_path: Path) -> None:
    """Write the generated WHATWG decode-table header to *out_path*."""
    indexes, ranges = _fetch_indexes()
    sb_slots, sb_table = _single_byte_tables(indexes)
    parts = [
        (
            "/* Auto-generated by tools/generate_encoding_tables.py - do not edit. */\n"
            f"/* WHATWG Encoding Standard index tables ({_INDEXES_URL},\n"
            f"   sha256 {_INDEXES_SHA256}).\n"
            "   Copyright WHATWG (Apple, Google, Mozilla, Microsoft); the index data, incorporated into\n"
            "   source code, is licensed BSD-3-Clause. See licenses/LICENSE-WHATWG. */\n\n"
            "#ifndef TURBOHTML_ENCODING_TABLES_H\n"
            "#define TURBOHTML_ENCODING_TABLES_H\n\n"
            "#include <stdint.h>\n\n"
            f"#define TH_SB_INDEX_COUNT {len(_SINGLE_BYTE)}\n"
        ),
        "/* The row each single-byte encoding occupies in th_sb_index; the label table names one of these. */\n"
        + sb_slots,
        "/* Byte -> code point for each legacy single-byte encoding. The ASCII half is the identity;\n"
        "   U+FFFD marks a byte the spec's index leaves null, which the decoder reports as an error.\n"
        "   No index maps a byte below U+0080, so a decoder reaching this table always emits a\n"
        "   non-ASCII code point. */\n" + sb_table,
        _big5_tables(indexes["big5"]),
        _flat_table("euc_kr", indexes["euc-kr"]),
        _flat_table("gb18030", indexes["gb18030"]),
        _flat_table("jis0208", indexes["jis0208"]),
        _flat_table("jis0212", indexes["jis0212"]),
        _gb18030_ranges(ranges),
        "#endif /* TURBOHTML_ENCODING_TABLES_H */\n",
    ]
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out_path}: {len(_SINGLE_BYTE)} single-byte tables, {out_path.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        msg = "usage: generate_encoding_tables.py OUTPUT_HEADER"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]))
