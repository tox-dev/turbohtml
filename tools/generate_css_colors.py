"""
Generate src/turbohtml/_c/data/css_colors.h from tdewolff/minify's CSS color maps.

The CSS minifier rewrites a color to its shortest equivalent: a hash to a shorter keyword (``#000080`` -> ``navy``) and
a keyword to a shorter hash (``black`` -> ``#000``). tdewolff hand-picked which direction is shorter for each color, so
those two maps are the source of truth here. This emits a generated header shaped like ``tld_table.h``: the keyword
table is sorted by keyword with a 256-entry first-byte index, so a keyword is matched by bucketing on its first byte;
the hash table is sorted by hash string for a binary search, since every hash shares the ``#`` first byte.

Usage:  python tools/generate_css_colors.py src/turbohtml/_c/data/css_colors.h
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

# keyword -> shortest hash form (when the hash is shorter than the keyword)
_NAME_TO_HEX: Final[dict[str, str]] = {
    "black": "#000",
    "darkblue": "#00008b",
    "mediumblue": "#0000cd",
    "darkgreen": "#006400",
    "darkcyan": "#008b8b",
    "deepskyblue": "#00bfff",
    "darkturquoise": "#00ced1",
    "mediumspringgreen": "#00fa9a",
    "springgreen": "#00ff7f",
    "midnightblue": "#191970",
    "dodgerblue": "#1e90ff",
    "lightseagreen": "#20b2aa",
    "forestgreen": "#228b22",
    "seagreen": "#2e8b57",
    "darkslategray": "#2f4f4f",
    "limegreen": "#32cd32",
    "mediumseagreen": "#3cb371",
    "turquoise": "#40e0d0",
    "royalblue": "#4169e1",
    "steelblue": "#4682b4",
    "darkslateblue": "#483d8b",
    "mediumturquoise": "#48d1cc",
    "darkolivegreen": "#556b2f",
    "cadetblue": "#5f9ea0",
    "cornflowerblue": "#6495ed",
    "mediumaquamarine": "#66cdaa",
    "slateblue": "#6a5acd",
    "olivedrab": "#6b8e23",
    "slategray": "#708090",
    "lightslateblue": "#789",
    "mediumslateblue": "#7b68ee",
    "lawngreen": "#7cfc00",
    "chartreuse": "#7fff00",
    "aquamarine": "#7fffd4",
    "lightskyblue": "#87cefa",
    "blueviolet": "#8a2be2",
    "darkmagenta": "#8b008b",
    "saddlebrown": "#8b4513",
    "darkseagreen": "#8fbc8f",
    "lightgreen": "#90ee90",
    "mediumpurple": "#9370db",
    "darkviolet": "#9400d3",
    "palegreen": "#98fb98",
    "darkorchid": "#9932cc",
    "yellowgreen": "#9acd32",
    "darkgray": "#a9a9a9",
    "lightblue": "#add8e6",
    "greenyellow": "#adff2f",
    "paleturquoise": "#afeeee",
    "lightsteelblue": "#b0c4de",
    "powderblue": "#b0e0e6",
    "firebrick": "#b22222",
    "darkgoldenrod": "#b8860b",
    "mediumorchid": "#ba55d3",
    "rosybrown": "#bc8f8f",
    "darkkhaki": "#bdb76b",
    "mediumvioletred": "#c71585",
    "indianred": "#cd5c5c",
    "chocolate": "#d2691e",
    "lightgray": "#d3d3d3",
    "goldenrod": "#daa520",
    "palevioletred": "#db7093",
    "gainsboro": "#dcdcdc",
    "burlywood": "#deb887",
    "lightcyan": "#e0ffff",
    "lavender": "#e6e6fa",
    "darksalmon": "#e9967a",
    "palegoldenrod": "#eee8aa",
    "lightcoral": "#f08080",
    "aliceblue": "#f0f8ff",
    "honeydew": "#f0fff0",
    "sandybrown": "#f4a460",
    "whitesmoke": "#f5f5f5",
    "mintcream": "#f5fffa",
    "ghostwhite": "#f8f8ff",
    "antiquewhite": "#faebd7",
    "lightgoldenrodyellow": "#fafad2",
    "fuchsia": "#f0f",
    "magenta": "#f0f",
    "deeppink": "#ff1493",
    "orangered": "#ff4500",
    "darkorange": "#ff8c00",
    "lightsalmon": "#ffa07a",
    "lightpink": "#ffb6c1",
    "peachpuff": "#ffdab9",
    "navajowhite": "#ffdead",
    "moccasin": "#ffe4b5",
    "mistyrose": "#ffe4e1",
    "blanchedalmond": "#ffebcd",
    "papayawhip": "#ffefd5",
    "lavenderblush": "#fff0f5",
    "seashell": "#fff5ee",
    "cornsilk": "#fff8dc",
    "lemonchiffon": "#fffacd",
    "floralwhite": "#fffaf0",
    "yellow": "#ff0",
    "lightyellow": "#ffffe0",
    "white": "#fff",
    # transparent is the keyword for rgba(0,0,0,0); its 4-digit hex #0000 is shorter (CSS Color 4 §5.2, §6.1)
    "transparent": "#0000",
    # British spellings are exact aliases of the gray-family names (CSS Color 4 §6.1)
    "darkgrey": "#a9a9a9",
    "lightgrey": "#d3d3d3",
    "slategrey": "#708090",
    "darkslategrey": "#2f4f4f",
    "lightslategrey": "#789",
}

# hash -> shortest keyword form (when the keyword is shorter than the hash)
_HEX_TO_NAME: Final[dict[str, str]] = {
    "#000080": "navy",
    "#008000": "green",
    "#008080": "teal",
    "#4b0082": "indigo",
    "#800000": "maroon",
    "#800080": "purple",
    "#808000": "olive",
    "#808080": "gray",
    "#a0522d": "sienna",
    "#a52a2a": "brown",
    "#c0c0c0": "silver",
    "#cd853f": "peru",
    "#d2b48c": "tan",
    "#da70d6": "orchid",
    "#dda0dd": "plum",
    "#ee82ee": "violet",
    "#f0e68c": "khaki",
    "#f0ffff": "azure",
    "#f5deb3": "wheat",
    "#f5f5dc": "beige",
    "#fa8072": "salmon",
    "#faf0e6": "linen",
    "#ff6347": "tomato",
    "#ff7f50": "coral",
    "#ffa500": "orange",
    "#ffc0cb": "pink",
    "#ffd700": "gold",
    "#ffe4c4": "bisque",
    "#fffafa": "snow",
    "#fffff0": "ivory",
    "#ff0000": "red",
    "#f00": "red",
}


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
    by_name = sorted(_NAME_TO_HEX.items())
    by_hex = sorted(_HEX_TO_NAME.items())
    out_path.write_text(
        "/* Auto-generated by tools/generate_css_colors.py - do not edit. */\n"
        "/* tdewolff/minify's CSS color maps: the shortest keyword<->hash form for each color. */\n\n"
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
