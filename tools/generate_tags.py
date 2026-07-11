"""
Generate src/turbohtml/_c/data/tag_atom.h from the HTML element categories.

The tree builder compares element identities as integers, never strings: the
tokenizer's lowercased tag name is interned once at element insertion via a
first-byte bucket lookup over the sorted name table here, and every later check
(is this a special element? a formatting element? in scope?) reads an integer
atom and its category bitmask. The category sets (special / formatting / scoping) are the
ones the WHATWG tree-construction algorithm special-cases; keeping them in a
generated table means a spec change is a regeneration, not a code edit.

Usage:  python tools/generate_tags.py src/turbohtml/_c/data/tag_atom.h
"""

from __future__ import annotations

import sys
from pathlib import Path

# Element categories the tree-construction algorithm tests against, in the HTML
# namespace. The foreign-content insertion mode handles MathML/SVG scoping
# markers separately.
SPECIAL = frozenset([
    "address",
    "applet",
    "area",
    "article",
    "aside",
    "base",
    "basefont",
    "bgsound",
    "blockquote",
    "body",
    "br",
    "button",
    "caption",
    "center",
    "col",
    "colgroup",
    "command",
    "dd",
    "details",
    "dir",
    "div",
    "dl",
    "dt",
    "embed",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "frame",
    "frameset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hgroup",
    "hr",
    "html",
    "iframe",
    "image",
    "img",
    "input",
    "isindex",
    "keygen",
    "li",
    "link",
    "listing",
    "main",
    "marquee",
    "menu",
    "meta",
    "nav",
    "noembed",
    "noframes",
    "noscript",
    "object",
    "ol",
    "p",
    "param",
    "plaintext",
    "pre",
    "script",
    "search",
    "section",
    "select",
    "source",
    "style",
    "summary",
    "table",
    "tbody",
    "td",
    "template",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "title",
    "tr",
    "track",
    "ul",
    "wbr",
    "xmp",
])
FORMATTING = frozenset([
    "a",
    "b",
    "big",
    "code",
    "em",
    "font",
    "i",
    "nobr",
    "s",
    "small",
    "strike",
    "strong",
    "tt",
    "u",
])
# Elements that form a scope boundary (the "default" scope marker set; select
# joined it with the customizable-select parsing change).
SCOPING = frozenset(["applet", "caption", "html", "marquee", "object", "select", "table", "td", "th", "template"])
# Start tags that switch the tokenizer's content model (tree construction owns this).
RAWTEXT = frozenset(["style", "xmp", "iframe", "noembed", "noframes", "noscript"])
RCDATA = frozenset(["title", "textarea"])

# MathML / SVG element names the foreign-content insertion mode special-cases
# (text integration points, HTML integration points, name adjustments).
FOREIGN = frozenset([
    "mi",
    "mo",
    "mn",
    "ms",
    "mtext",
    "annotation-xml",
    "foreignobject",
    "desc",
    "g",
    "mglyph",
    "malignmark",
    "path",
])

# Extra commonly-seen names so frequent elements get a stable atom even when not
# in a special category (keeps the intern hit-rate high for real documents).
EXTRA = frozenset([
    "abbr",
    "address",
    "b",
    "bdi",
    "bdo",
    "cite",
    "data",
    "datalist",
    "del",
    "dfn",
    "dialog",
    "figcaption",
    "hgroup",
    "ins",
    "kbd",
    "label",
    "legend",
    "mark",
    "meter",
    "optgroup",
    "option",
    "output",
    "picture",
    "progress",
    "q",
    "rb",
    "rp",
    "rt",
    "rtc",
    "ruby",
    "samp",
    "selectedcontent",
    "slot",
    "span",
    "sub",
    "sup",
    "time",
    "var",
    "video",
    "audio",
    "canvas",
    "map",
    "area",
    "math",
    "svg",
])

CATEGORY_FLAGS = {
    "TH_TAG_SPECIAL": SPECIAL,
    "TH_TAG_FORMATTING": FORMATTING,
    "TH_TAG_SCOPING": SCOPING,
    "TH_TAG_RAWTEXT": RAWTEXT,
    "TH_TAG_RCDATA": RCDATA,
}


def generate(out_path: Path) -> None:
    """Write the generated tag-atom C header to *out_path*."""
    names = sorted(SPECIAL | FORMATTING | SCOPING | RAWTEXT | RCDATA | FOREIGN | EXTRA)
    atoms = [f"TH_TAG_{name.upper().replace('-', '_')}" for name in names]

    flag_bits = {flag: 1 << i for i, flag in enumerate(CATEGORY_FLAGS)}
    flag_defs = "\n".join(f"#define {flag} {bit}u" for flag, bit in flag_bits.items())

    enum_lines = "\n".join(f"    {atom}," for atom in atoms)

    table_lines = []
    wide_names = []
    for name, atom in zip(names, atoms, strict=True):
        flags = " | ".join(flag for flag, members in CATEGORY_FLAGS.items() if name in members) or "0"
        table_lines.append(f'    {{"{name}", {len(name)}u, {atom}, {flags}, {len(wide_names)}u}},')
        wide_names.extend(map(ord, name))

    # Names are sorted, so entries sharing a first byte are contiguous.
    # first_index[b] holds the offset of the first entry whose name starts with a
    # byte >= b, so the bucket for byte b is [first_index[b], first_index[b + 1]).
    # Interning then scans one first-byte bucket rather than binary-searching the
    # whole table.
    first_index = [len(names)] * 257
    for index, name in enumerate(names):
        first_byte = ord(name[0])
        first_index[first_byte] = min(first_index[first_byte], index)
    running = len(names)
    for byte_value in range(256, -1, -1):
        running = min(running, first_index[byte_value])
        first_index[byte_value] = running
    index_rows = ", ".join(str(first_index[byte_value]) for byte_value in range(257))

    out_path.write_text(
        "/* Auto-generated by tools/generate_tags.py - do not edit. */\n"
        "/* HTML element atoms and the tree-construction category bitmasks. */\n\n"
        "#ifndef TURBOHTML_TAG_ATOM_H\n"
        "#define TURBOHTML_TAG_ATOM_H\n\n"
        "#include <stdint.h>\n\n"
        "/* TH_TAG_UNKNOWN (0) is every name not in the table; atoms are otherwise\n"
        "   assigned in sorted name order so the first-byte buckets are contiguous. */\n"
        "enum th_tag {\n"
        "    TH_TAG_UNKNOWN = 0,\n"
        f"{enum_lines}\n"
        "};\n\n"
        "/* Category membership, OR-ed; read via th_tag_flags(atom). */\n"
        f"{flag_defs}\n\n"
        "typedef struct {\n"
        "    const char *name;\n"
        "    uint8_t name_len;\n"
        "    uint16_t atom;\n"
        "    uint8_t flags;\n"
        "    uint16_t wide_offset;\n"
        "} th_tag_entry;\n\n"
        f"static const int th_tag_count = {len(names)};\n"
        "static const th_tag_entry th_tag_table[] = {\n"
        f"{chr(10).join(table_lines)}\n"
        "};\n\n"
        "static const uint32_t th_tag_wide_names[] = {\n"
        f"    {', '.join(f'{char}u' for char in wide_names)}\n"
        "};\n\n"
        "static inline const uint32_t *th_tag_wide_name(uint16_t atom) {\n"
        "    return th_tag_wide_names + th_tag_table[atom - 1].wide_offset;\n"
        "}\n\n"
        "/* th_tag_first[c] is the first table index whose name starts with a byte\n"
        "   >= c, so the entries beginning with byte c are\n"
        "   [th_tag_first[c], th_tag_first[c + 1]). */\n"
        "static const uint16_t th_tag_first[257] = {\n"
        f"    {index_rows}\n"
        "};\n\n"
        "#endif /* TURBOHTML_TAG_ATOM_H */\n",
        encoding="utf-8",
    )
    print(f"wrote {out_path}: {len(names)} atoms")


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
