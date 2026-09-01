"""
Generate src/turbohtml/_c/data/tld_table.h from IANA's list of top-level domains.

linkify recognizes a bare domain like ``example.com`` as a link only when its last label is a real TLD, the same
rule bleach used. The canonical list is IANA's, so this downloads it, lowercases and sorts every entry (including the
``xn--`` punycode TLDs, so a real ``xn--p1ai`` matches while a made-up ``xn--whatever`` does not), and emits a
generated header shaped like ``tag_atom.h``: a sorted ``{name, len}`` array plus a 256-entry first-byte index, so a
label is matched by bucketing on its first byte and a case-insensitive ``memcmp``. Alongside it goes the Unicode
spelling of each internationalized TLD, decoded from its A-label, because that is how ``президент.рф`` is written in
running text. IANA serves only the current list, so the expected ``IANA_VERSION`` and the SHA-256 of its exact bytes
are pinned and a rebuild refuses a silent drift or a poisoned source; the version is also recorded in the header so a
deliberate bump shows an auditable diff.

Usage:  python tools/generate_tlds.py src/turbohtml/_c/data/tld_table.h
"""

from __future__ import annotations

import codecs
import hashlib
import itertools
import sys
from pathlib import Path

from httpfetch import fetch_bytes

IANA_TLDS_URL = "https://data.iana.org/TLD/tlds-alpha-by-domain.txt"

# IANA publishes only the latest list at a stable URL, so a rebuild can never fetch a past version -- the committed
# table would drift with whatever IANA serves that day. Pin the expected version and the SHA-256 of its exact bytes;
# a rebuild fails when either differs, so a bumped version or a poisoned/silently rewritten source cannot land without
# review. Bump both deliberately and review the tld_table.h diff. Both match the committed table.
IANA_VERSION = "Version 2026062302"
IANA_SHA256 = "01dd82fed6299013f2e4bc4c5af2469b95497a4e9825801741ffff95b6e55d8f"


def fetch_tlds() -> tuple[str, list[str]]:
    """Return the pinned IANA version and the lowercased ASCII TLDs, punycode entries included."""
    raw = fetch_bytes(IANA_TLDS_URL)
    version = ""
    names: list[str] = []
    for line in raw.decode("ascii").splitlines():
        if line.startswith("#"):
            if "Version" in line:  # "# Version 2026061600, Last Updated ..."; keep just the version number
                version = line.lstrip("# ").split(",", 1)[0].strip()
            continue
        if name := line.strip().lower():
            names.append(name)
    if version != IANA_VERSION:
        msg = f"IANA served {version!r}, expected the pinned {IANA_VERSION!r}; bump the pins to regenerate"
        raise SystemExit(msg)
    if (digest := hashlib.sha256(raw).hexdigest()) != IANA_SHA256:
        msg = f"IANA source for {version} has sha256 {digest}, not the pinned {IANA_SHA256}; review, then bump the pin"
        raise SystemExit(msg)
    return version, sorted(names)


def unicode_labels(names: list[str]) -> list[str]:
    """
    Return the sorted Unicode forms of the ``xn--`` TLDs, upper-cased variants included.

    IANA publishes an internationalized TLD only as its punycode A-label, but people write the U-label: ``президент.рф``
    is what a bare domain looks like in running text. Decoding the A-label here keeps the two spellings of one TLD in
    step by construction. A script with case contributes its upper-case spelling too when that spelling has the same
    length, which covers ``.РФ`` without a case-folding pass in the matcher; a case-less script yields one entry.
    """
    labels = set()
    for name in names:
        if not name.startswith("xn--"):
            continue
        label = codecs.decode(name[4:].encode("ascii"), "punycode")
        labels.update({label} | ({label.upper()} if len(label.upper()) == len(label) else set()))
    return sorted(labels - set(names))


def generate(out_path: Path) -> None:
    """Write the generated TLD-table C header to *out_path*."""
    version, names = fetch_tlds()
    render(out_path, version, names)
    print(f"wrote {out_path}: {len(names)} TLDs ({version})")


def render(out_path: Path, version: str, names: list[str]) -> None:
    """Write the header for the sorted lowercase ASCII TLD *names* of IANA's *version*."""
    table_lines = "\n".join(f'    {{"{name}", {len(name)}u}},' for name in names)

    # Names are sorted, so entries sharing a first byte are contiguous; first_index[b] holds the offset of the first
    # entry whose name starts with a byte >= b, so the bucket for byte b is [first_index[b], first_index[b + 1]).
    first_index = [len(names)] * 257
    for index, name in enumerate(names):
        first_byte = ord(name[0])
        first_index[first_byte] = min(first_index[first_byte], index)
    running = len(names)
    for byte_value in range(256, -1, -1):
        running = min(running, first_index[byte_value])
        first_index[byte_value] = running
    index_rows = ", ".join(str(first_index[byte_value]) for byte_value in range(257))

    labels = unicode_labels(names)
    point_rows = "\n".join("    " + " ".join(f"0x{ord(point):04X}," for point in label) for label in labels)
    offsets = itertools.accumulate((len(label) for label in labels), initial=0)
    unicode_rows = "\n".join(
        f"    {{th_tld_unicode_points + {offset}, {len(label)}u}},"
        for offset, label in zip(offsets, labels, strict=False)
    )

    out_path.write_text(
        "/* Auto-generated by tools/generate_tlds.py - do not edit. */\n"
        f"/* IANA top-level domains ({version}), including the xn-- punycode TLDs. */\n\n"
        "#ifndef TURBOHTML_TLD_TABLE_H\n"
        "#define TURBOHTML_TLD_TABLE_H\n\n"
        "#include <stdint.h>\n\n"
        "typedef struct {\n"
        "    const char *name;\n"
        "    uint8_t name_len;\n"
        "} th_tld_entry;\n\n"
        f"static const int th_tld_count = {len(names)};\n"
        "static const th_tld_entry th_tld_table[] = {\n"
        f"{table_lines}\n"
        "};\n\n"
        "/* th_tld_first[c] is the first table index whose name starts with a byte >= c, so the entries\n"
        "   beginning with byte c are [th_tld_first[c], th_tld_first[c + 1]). */\n"
        "static const uint16_t th_tld_first[257] = {\n"
        f"    {index_rows}\n"
        "};\n\n"
        "/* The same internationalized TLDs as their Unicode U-labels, the spelling people write, sorted by code\n"
        "   point; a script with case carries its upper-case spelling as its own entry. */\n"
        "typedef struct {\n"
        "    const uint32_t *points;\n"
        "    uint8_t len;\n"
        "} th_tld_unicode_entry;\n\n"
        "static const uint32_t th_tld_unicode_points[] = {\n"
        f"{point_rows}\n"
        "};\n\n"
        f"static const int th_tld_unicode_count = {len(labels)};\n"
        "static const th_tld_unicode_entry th_tld_unicode[] = {\n"
        f"{unicode_rows}\n"
        "};\n\n"
        "#endif /* TURBOHTML_TLD_TABLE_H */\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        msg = "usage: generate_tlds.py OUTPUT_HEADER"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]))
