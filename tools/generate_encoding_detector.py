"""
Generate src/turbohtml/encoding_detect_data.h from Firefox's chardetng.

The content-based encoding detector (issue #182) is a native-C port of chardetng
(https://github.com/hsivonen/chardetng, MIT/Apache-2.0, Copyright Mozilla
Foundation). chardetng keeps its character-class and character-pair-frequency
tables in ``src/data.rs``; this script parses that file and emits the equivalent
C tables so the detector reuses the exact, battle-tested data rather than a
hand-transcribed copy.

Run it against a chardetng checkout::

    python tools/generate_encoding_detector.py <chardetng> src/turbohtml/encoding_detect_data.h

The generated header is committed; regenerate it only when bumping the pinned
chardetng revision (recorded in the header banner).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# A DetectorData field of this length is a per-byte character-class table; any
# other length is a variable-length character-pair probability table.
_CLASS_TABLE_LEN = 128

# The 16-bit CJK frequency tables, kept apart from the 8-bit single-byte tables.
_U16_FIELDS = ("frequent_simplified", "frequent_kanji", "frequent_hangul")

_SINGLE_BYTE_COUNT = 20

# The WHATWG/Python codec label each chardetng Encoding maps to, keyed by the
# chardetng ``*_INIT`` static name. The detector never returns one outside this map.
_ENCODING_LABEL = {
    "WINDOWS_1258_INIT": "windows-1258",
    "WINDOWS_1250_INIT": "windows-1250",
    "ISO_8859_2_INIT": "iso-8859-2",
    "WINDOWS_1251_INIT": "windows-1251",
    "KOI8_U_INIT": "koi8-u",
    "ISO_8859_5_INIT": "iso-8859-5",
    "IBM866_INIT": "ibm866",
    "WINDOWS_1252_INIT": "windows-1252",
    "WINDOWS_1253_INIT": "windows-1253",
    "ISO_8859_7_INIT": "iso-8859-7",
    "WINDOWS_1254_INIT": "windows-1254",
    "WINDOWS_1255_INIT": "windows-1255",
    "ISO_8859_8_INIT": "iso-8859-8",
    "WINDOWS_1256_INIT": "windows-1256",
    "ISO_8859_6_INIT": "iso-8859-6",
    "WINDOWS_1257_INIT": "windows-1257",
    "ISO_8859_13_INIT": "iso-8859-13",
    "ISO_8859_4_INIT": "iso-8859-4",
    "WINDOWS_874_INIT": "windows-874",
}


def parse_numbers(text: str) -> list[int]:
    r"""
    Return every integer literal in a Rust array body, in order.

    The class tables are decimal; the CJK frequency tables are hex (``0x4E2D``),
    so match a hex literal before falling back to a decimal run (a bare ``\d+``
    would split ``0x4E2D`` into ``0``, ``4``, ``2``).
    """
    return [int(token, 0) for token in re.findall(r"0[xX][0-9A-Fa-f]+|\d+", text)]


def parse_detector_data(source: str) -> dict[str, list[int]]:
    """Parse the named arrays inside the ``DETECTOR_DATA`` struct instance."""
    body = re.search(r"pub static DETECTOR_DATA: DetectorData = DetectorData \{(.*?)\n\};", source, re.DOTALL)
    if body is None:
        msg = "DETECTOR_DATA instance not found; chardetng layout changed"
        raise SystemExit(msg)
    tables: dict[str, list[int]] = {}
    # the trailing newline lets the last field (whose closing "]," abuts the struct
    # close) match the same "]," + newline pattern as every earlier field
    for name, content in re.findall(r"(\w+):\s*\[(.*?)\],\s*\n", body.group(1) + "\n", re.DOTALL):
        tables[name] = parse_numbers(content)
    return tables


def parse_single_byte(source: str) -> list[tuple[str, ...]]:
    """Parse the 20 ``SINGLE_BYTE_DATA`` rows into (encoding, lower, upper, prob, ascii, non_ascii)."""
    block = re.search(r"pub static SINGLE_BYTE_DATA: \[SingleByteData; 20\] = \[(.*?)\n\];", source, re.DOTALL)
    if block is None:
        msg = "SINGLE_BYTE_DATA not found; chardetng layout changed"
        raise SystemExit(msg)
    rows = re.findall(
        r"encoding:\s*&(\w+),\s*lower:\s*&DETECTOR_DATA\.(\w+),\s*upper:\s*&DETECTOR_DATA\.(\w+),"
        r"\s*probabilities:\s*&DETECTOR_DATA\.(\w+),\s*ascii:\s*(\w+),\s*non_ascii:\s*(\w+),",
        block.group(1),
    )
    if len(rows) != _SINGLE_BYTE_COUNT:
        msg = f"expected {_SINGLE_BYTE_COUNT} single-byte encodings, parsed {len(rows)}"
        raise SystemExit(msg)
    return rows


def parse_consts(source: str) -> dict[str, int]:
    """Parse the ``const NAME: usize = N;`` boundary constants the rows reference."""
    return {name: int(value) for name, value in re.findall(r"const (\w+): usize = (\d+);", source)}


def emit_array(name: str, values: list[int], ctype: str) -> str:
    """Render a C ``static const`` array, sixteen values per line."""
    rows = []
    for start in range(0, len(values), 16):
        chunk = ", ".join(str(value) for value in values[start : start + 16])
        rows.append(f"    {chunk},")
    return f"static const {ctype} {name}[{len(values)}] = {{\n" + "\n".join(rows) + "\n};\n"


def emit_tables(tables: dict[str, list[int]]) -> list[str]:
    """Render every class, frequency, and probability table as a C array."""
    out: list[str] = []
    for name, values in tables.items():
        if name in _U16_FIELDS:
            out.append(emit_array(f"th_detect_freq_{name}", values, "uint16_t"))
        elif len(values) == _CLASS_TABLE_LEN:
            out.append(emit_array(f"th_detect_class_{name}", values, "uint8_t"))
        else:
            out.append(emit_array(f"th_detect_prob_{name}", values, "uint8_t"))
    return out


def emit_single_byte(rows: list[tuple[str, ...]], consts: dict[str, int]) -> list[str]:
    """Render the single-byte encoding table that ties the rows to their data."""
    out = [
        "",
        "/* One row per single-byte candidate encoding: the WHATWG label it resolves to,",
        "   its lower/upper class tables, its probability table, and the class-count",
        "   boundaries the scoring uses. */",
        "typedef struct {",
        "    const char *label;",
        "    const uint8_t *lower;",
        "    const uint8_t *upper;",
        "    const uint8_t *probabilities;",
        "    uint16_t ascii;",
        "    uint16_t non_ascii;",
        "} th_detect_single_byte;",
        "",
        f"static const th_detect_single_byte th_detect_single_byte_data[{len(rows)}] = {{",
    ]
    for encoding, lower, upper, prob, ascii_const, non_ascii_const in rows:
        label = _ENCODING_LABEL[encoding]
        out.append(
            f'    {{"{label}", th_detect_class_{lower}, th_detect_class_{upper}, '
            f"th_detect_prob_{prob}, {consts[ascii_const]}, {consts[non_ascii_const]}}},"
        )
    out.append("};")
    return out


def generate(chardetng: Path) -> str:
    """Return the full generated C header for a chardetng checkout."""
    source = (chardetng / "src" / "data.rs").read_text(encoding="utf-8")
    revision = subprocess.run(
        ["git", "-C", str(chardetng), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    banner = [
        "/* Generated by tools/generate_encoding_detector.py from chardetng",
        "   (https://github.com/hsivonen/chardetng, MIT/Apache-2.0, Copyright Mozilla",
        f"   Foundation) at revision {revision}. Do not edit by hand; rerun the generator.",
        "",
        "   These are chardetng's character-class tables (one per encoding, mapping each",
        "   byte to a scoring class) and character-pair frequency tables (the bigram",
        "   scores, shared across the encodings of a language family). */",
        "",
    ]
    sections = (
        banner
        + emit_tables(parse_detector_data(source))
        + emit_single_byte(parse_single_byte(source), parse_consts(source))
    )
    return "\n".join(sections) + "\n"


def main() -> None:
    """Write the generated header to the path in argv[2]."""
    if len(sys.argv) != 3:
        msg = "usage: generate_encoding_detector.py <chardetng-checkout> <output.h>"
        raise SystemExit(msg)
    Path(sys.argv[2]).write_text(generate(Path(sys.argv[1])), encoding="utf-8")


if __name__ == "__main__":
    main()
