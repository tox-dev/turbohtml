"""
Generate src/turbohtml/_c/data/css_colors.h from the CSS Color 4 named-color table.

The minifier rewrites a color to its shortest equivalent form: a keyword to a shorter hash (``black`` -> ``#000``) or a
hash to a shorter keyword (``#000080`` -> ``navy``). Rather than hand-pick a direction per color, this derives both maps
from the authoritative named-color list in `CSS Color 4 §6.1 <https://www.w3.org/TR/css-color-4/#named-colors>`__ (plus
``transparent`` from §6.2): for each color it computes the shortest hash form, then keeps whichever of keyword or hash
is strictly shorter. Deriving the maps keeps them complete (every §6.1 color is covered) and value-safe: a name that
§6.1 does not define, such as the X11-only ``lightslateblue``, is never emitted, so an invalid keyword is never
rewritten into a valid color.

The emitted header is shaped like ``tld_table.h``: the keyword table is sorted by keyword with a 256-entry first-byte
index for a bucketed scan; the hash table is sorted by hash string for a binary search, since every hash shares the
``#`` first byte.

Usage:  python tools/generate_css_colors.py src/turbohtml/_c/data/css_colors.h
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

# The CSS Color 4 §6.1 named colors, each mapped to its six-digit sRGB value (the spec gives these as the normative
# definition of every keyword). British ``grey`` spellings alias their ``gray`` twins; ``rebeccapurple`` is the Color 4
# addition. ``transparent`` (§6.2) is handled separately below since it is the only keyword with a non-opaque value.
_NAMED_COLORS: Final[dict[str, str]] = {
    "aliceblue": "f0f8ff",
    "antiquewhite": "faebd7",
    "aqua": "00ffff",
    "aquamarine": "7fffd4",
    "azure": "f0ffff",
    "beige": "f5f5dc",
    "bisque": "ffe4c4",
    "black": "000000",
    "blanchedalmond": "ffebcd",
    "blue": "0000ff",
    "blueviolet": "8a2be2",
    "brown": "a52a2a",
    "burlywood": "deb887",
    "cadetblue": "5f9ea0",
    "chartreuse": "7fff00",
    "chocolate": "d2691e",
    "coral": "ff7f50",
    "cornflowerblue": "6495ed",
    "cornsilk": "fff8dc",
    "crimson": "dc143c",
    "cyan": "00ffff",
    "darkblue": "00008b",
    "darkcyan": "008b8b",
    "darkgoldenrod": "b8860b",
    "darkgray": "a9a9a9",
    "darkgreen": "006400",
    "darkgrey": "a9a9a9",
    "darkkhaki": "bdb76b",
    "darkmagenta": "8b008b",
    "darkolivegreen": "556b2f",
    "darkorange": "ff8c00",
    "darkorchid": "9932cc",
    "darkred": "8b0000",
    "darksalmon": "e9967a",
    "darkseagreen": "8fbc8f",
    "darkslateblue": "483d8b",
    "darkslategray": "2f4f4f",
    "darkslategrey": "2f4f4f",
    "darkturquoise": "00ced1",
    "darkviolet": "9400d3",
    "deeppink": "ff1493",
    "deepskyblue": "00bfff",
    "dimgray": "696969",
    "dimgrey": "696969",
    "dodgerblue": "1e90ff",
    "firebrick": "b22222",
    "floralwhite": "fffaf0",
    "forestgreen": "228b22",
    "fuchsia": "ff00ff",
    "gainsboro": "dcdcdc",
    "ghostwhite": "f8f8ff",
    "gold": "ffd700",
    "goldenrod": "daa520",
    "gray": "808080",
    "green": "008000",
    "greenyellow": "adff2f",
    "grey": "808080",
    "honeydew": "f0fff0",
    "hotpink": "ff69b4",
    "indianred": "cd5c5c",
    "indigo": "4b0082",
    "ivory": "fffff0",
    "khaki": "f0e68c",
    "lavender": "e6e6fa",
    "lavenderblush": "fff0f5",
    "lawngreen": "7cfc00",
    "lemonchiffon": "fffacd",
    "lightblue": "add8e6",
    "lightcoral": "f08080",
    "lightcyan": "e0ffff",
    "lightgoldenrodyellow": "fafad2",
    "lightgray": "d3d3d3",
    "lightgreen": "90ee90",
    "lightgrey": "d3d3d3",
    "lightpink": "ffb6c1",
    "lightsalmon": "ffa07a",
    "lightseagreen": "20b2aa",
    "lightskyblue": "87cefa",
    "lightslategray": "778899",
    "lightslategrey": "778899",
    "lightsteelblue": "b0c4de",
    "lightyellow": "ffffe0",
    "lime": "00ff00",
    "limegreen": "32cd32",
    "linen": "faf0e6",
    "magenta": "ff00ff",
    "maroon": "800000",
    "mediumaquamarine": "66cdaa",
    "mediumblue": "0000cd",
    "mediumorchid": "ba55d3",
    "mediumpurple": "9370db",
    "mediumseagreen": "3cb371",
    "mediumslateblue": "7b68ee",
    "mediumspringgreen": "00fa9a",
    "mediumturquoise": "48d1cc",
    "mediumvioletred": "c71585",
    "midnightblue": "191970",
    "mintcream": "f5fffa",
    "mistyrose": "ffe4e1",
    "moccasin": "ffe4b5",
    "navajowhite": "ffdead",
    "navy": "000080",
    "oldlace": "fdf5e6",
    "olive": "808000",
    "olivedrab": "6b8e23",
    "orange": "ffa500",
    "orangered": "ff4500",
    "orchid": "da70d6",
    "palegoldenrod": "eee8aa",
    "palegreen": "98fb98",
    "paleturquoise": "afeeee",
    "palevioletred": "db7093",
    "papayawhip": "ffefd5",
    "peachpuff": "ffdab9",
    "peru": "cd853f",
    "pink": "ffc0cb",
    "plum": "dda0dd",
    "powderblue": "b0e0e6",
    "purple": "800080",
    "rebeccapurple": "663399",
    "red": "ff0000",
    "rosybrown": "bc8f8f",
    "royalblue": "4169e1",
    "saddlebrown": "8b4513",
    "salmon": "fa8072",
    "sandybrown": "f4a460",
    "seagreen": "2e8b57",
    "seashell": "fff5ee",
    "sienna": "a0522d",
    "silver": "c0c0c0",
    "skyblue": "87ceeb",
    "slateblue": "6a5acd",
    "slategray": "708090",
    "slategrey": "708090",
    "snow": "fffafa",
    "springgreen": "00ff7f",
    "steelblue": "4682b4",
    "tan": "d2b48c",
    "teal": "008080",
    "thistle": "d8bfd8",
    "tomato": "ff6347",
    "turquoise": "40e0d0",
    "violet": "ee82ee",
    "wheat": "f5deb3",
    "white": "ffffff",
    "whitesmoke": "f5f5f5",
    "yellow": "ffff00",
    "yellowgreen": "9acd32",
}

# transparent is rgba(0,0,0,0); its four-digit hash #0000 is shorter than the keyword (CSS Color 4 §6.2).
_TRANSPARENT_HASH: Final[str] = "#0000"


def _shortest_hash(rgb: str) -> str:
    """Collapse a six-digit sRGB value to ``#rgb`` when each channel is a doubled nibble, else keep ``#rrggbb``."""
    if rgb[0] == rgb[1] and rgb[2] == rgb[3] and rgb[4] == rgb[5]:
        return f"#{rgb[0]}{rgb[2]}{rgb[4]}"
    return f"#{rgb}"


def _build_maps() -> tuple[dict[str, str], dict[str, str]]:
    """Return (keyword -> shorter hash, hash -> shorter keyword), derived from the named-color table."""
    name_to_hex: dict[str, str] = {"transparent": _TRANSPARENT_HASH}
    hex_to_name: dict[str, str] = {}
    for name, rgb in _NAMED_COLORS.items():
        short = _shortest_hash(rgb)
        if len(short) < len(name):
            name_to_hex[name] = short
            continue
        if len(name) < len(short):
            # the minifier may present either the collapsed or the six-digit hash, so key both; keep the shortest name.
            for form in {short, f"#{rgb}"}:
                if len(name) < len(form) and (form not in hex_to_name or len(name) < len(hex_to_name[form])):
                    hex_to_name[form] = name
    return name_to_hex, hex_to_name


def _entry_rows(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f'    {{"{key}", {len(key)}u, "{value}", {len(value)}u}},' for key, value in pairs)


def _first_byte_index(keys: list[str]) -> str:
    # keys are sorted, so entries sharing a first byte are contiguous; first_index[b] holds the offset of the first
    # entry whose key starts with a byte >= b, so the bucket for byte b is [first_index[b], first_index[b + 1]).
    first_index = [len(keys)] * 257
    for index, key in enumerate(keys):
        first_byte = ord(key[0])
        first_index[first_byte] = min(first_index[first_byte], index)
    running = len(keys)
    for byte_value in range(256, -1, -1):
        running = min(running, first_index[byte_value])
        first_index[byte_value] = running
    return ", ".join(str(first_index[byte_value]) for byte_value in range(257))


def generate(out_path: Path) -> None:
    """Write the generated CSS-color-table C header to *out_path*."""
    name_to_hex, hex_to_name = _build_maps()
    by_name = sorted(name_to_hex.items())
    by_hex = sorted(hex_to_name.items())
    out_path.write_text(
        "/* Auto-generated by tools/generate_css_colors.py - do not edit. */\n"
        "/* Shortest keyword<->hash form for every CSS Color 4 named color. */\n\n"
        "#ifndef TURBOHTML_CSS_COLORS_H\n"
        "#define TURBOHTML_CSS_COLORS_H\n\n"
        "#include <stdint.h>\n\n"
        "typedef struct {\n"
        "    const char *key;\n"
        "    uint8_t key_len;\n"
        "    const char *val;\n"
        "    uint8_t val_len;\n"
        "} th_css_color_entry;\n\n"
        "/* keyword -> shortest hash, sorted by keyword for a first-byte bucketed scan. */\n"
        f"static const int th_css_name_count = {len(by_name)};\n"
        "static const th_css_color_entry th_css_name_to_hex[] = {\n"
        f"{_entry_rows(by_name)}\n"
        "};\n\n"
        "/* th_css_name_first[c] is the first table index whose keyword starts with a byte >= c, so the keywords\n"
        "   beginning with byte c are [th_css_name_first[c], th_css_name_first[c + 1]). */\n"
        "static const uint16_t th_css_name_first[257] = {\n"
        f"    {_first_byte_index([name for name, _ in by_name])}\n"
        "};\n\n"
        "/* hash -> shortest keyword, sorted by hash string for a binary search (every hash shares the '#' byte). */\n"
        f"static const int th_css_hex_count = {len(by_hex)};\n"
        "static const th_css_color_entry th_css_hex_to_name[] = {\n"
        f"{_entry_rows(by_hex)}\n"
        "};\n\n"
        "#endif /* TURBOHTML_CSS_COLORS_H */\n",
        encoding="utf-8",
    )
    print(f"wrote {out_path}: {len(by_name)} keywords, {len(by_hex)} hashes")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        msg = "usage: generate_css_colors.py OUTPUT_HEADER"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]))
