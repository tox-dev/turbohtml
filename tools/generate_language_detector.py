"""
Generate src/turbohtml/_c/encoding/language_data.h from whatlang.

The content-based language detector (roadmap #459) is a native-C port of whatlang
(https://github.com/greyblake/whatlang-rs, MIT, Copyright Sergey Potapov), the same
approach the encoding detector took with chardetng: rather than hand-transcribe a
model, parse whatlang's own data and emit the equivalent C tables so the detector
reuses the exact, published trigram profiles and script ranges.

whatlang keeps its Unicode script ranges in ``src/scripts/chars.rs``, its script
ordering in ``src/scripts/detect.rs``, the script-to-language grouping in
``src/scripts/grouping.rs``, the language metadata in ``src/lang.rs``, and the
per-language character-trigram profiles (the Cavnar-Trenkle rank model, 300
most-frequent trigrams per language, most-frequent first) in
``src/trigrams/profiles.rs``. This script parses those files and emits one header.

Run it against a whatlang checkout::

    python tools/generate_language_detector.py <whatlang-rs> src/turbohtml/_c/encoding/language_data.h

The generated header is committed; regenerate it only when bumping the pinned
whatlang revision (recorded in the header banner).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_MULTI_SCRIPTS = ("Latin", "Cyrillic", "Arabic", "Devanagari", "Hebrew")
_PROFILE_STATICS = ("LATIN_LANGS", "CYRILLIC_LANGS", "ARABIC_LANGS", "DEVANAGARI_LANGS", "HEBREW_LANGS")

#: The parsed trigram model: per script static, an ordered list of (language, [code-point triples]).
Profiles = dict[str, list[tuple[str, list[tuple[int, int, int]]]]]


def require(match: re.Match[str] | None, what: str) -> re.Match[str]:
    """Return the match, or abort with a clear message when whatlang's layout has drifted."""
    if match is None:
        msg = f"{what} not found; whatlang layout changed"
        raise SystemExit(msg)
    return match


def parse_char_literal(token: str) -> int:
    r"""Return the code point of a Rust char literal: ``'a'`` or ``'\u{1D2B}'``."""
    token = token.strip()
    inner = token[1:-1]
    if inner.startswith("\\u{"):
        return int(inner[3:-1], 16)
    if inner.startswith("\\"):  # only the escapes whatlang actually uses
        return {"\\n": ord("\n"), "\\t": ord("\t"), "\\'": ord("'"), "\\\\": ord("\\")}[inner]
    return ord(inner)


def parse_script_ranges(chars_src: str) -> dict[str, list[tuple[int, int]]]:
    """Parse each ``is_<name>`` matches! body into a list of inclusive code-point ranges."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    for name, body in re.findall(
        r"fn is_(\w+)\(ch: char\) -> bool \{\s*matches!\(ch,\s*(.*?)\)\s*\}", chars_src, re.DOTALL
    ):
        spans: list[tuple[int, int]] = []
        for raw in body.split("|"):
            term = raw.strip().rstrip(",").strip()
            if not term:
                continue
            if "..=" in term:
                low, high = term.split("..=")
                spans.append((parse_char_literal(low), parse_char_literal(high)))
            else:
                point = parse_char_literal(term)
                spans.append((point, point))
        ranges[name] = spans
    return ranges


def parse_script_order(detect_src: str) -> list[tuple[str, str]]:
    """Parse the ``script_counters`` array into ordered (Script, is_<fn>) pairs; the index is the script id."""
    block = re.search(r"let mut script_counters: \[ScriptCounter; 25\] = \[(.*?)\n    \];", detect_src, re.DOTALL)
    if block is None:
        msg = "script_counters array not found; whatlang layout changed"
        raise SystemExit(msg)
    pairs = re.findall(r"\(Script::(\w+), chars::is_(\w+), 0\)", block.group(1))
    if len(pairs) != 25:
        msg = f"expected 25 scripts, parsed {len(pairs)}"
        raise SystemExit(msg)
    return pairs


def parse_lang_groups(grouping_src: str) -> dict[str, tuple[str, str]]:
    """Parse ``to_lang_group`` into Script -> (kind, payload): ('multi', mls) | ('one', lang) | ('mandarin', '')."""
    block = re.search(r"fn to_lang_group.*?match self \{(.*?)\n        \}", grouping_src, re.DOTALL)
    if block is None:
        msg = "to_lang_group match not found; whatlang layout changed"
        raise SystemExit(msg)
    groups: dict[str, tuple[str, str]] = {}
    for scripts, raw_arm in re.findall(r"((?:Script::\w+(?:\s*\|\s*)?)+)\s*=>\s*([^,]+),", block.group(1)):
        names = re.findall(r"Script::(\w+)", scripts)
        arm = raw_arm.strip()
        if arm == "Mandarin":
            target = ("mandarin", "")
        elif (multi := re.fullmatch(r"Multi\(MLS::(\w+)\)", arm)) is not None:
            target = ("multi", multi.group(1))
        else:
            target = ("one", require(re.fullmatch(r"One\(Lang::(\w+)\)", arm), "One(Lang) arm").group(1))
        for name in names:
            groups[name] = target
    return groups


def parse_lang_table(lang_src: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Parse the Lang enum order plus lang_to_code and lang_to_eng_name maps, keyed by the Lang variant."""
    values = require(re.search(r"const VALUES: \[Lang; 70\] = \[(.*?)\];", lang_src, re.DOTALL), "VALUES array")
    order = re.findall(r"Lang::(\w+)", values.group(1))
    code_fn = require(re.search(r"fn lang_to_code.*?\n\}", lang_src, re.DOTALL), "lang_to_code").group()
    code = dict(re.findall(r'Lang::(\w+) => "(\w+)",', code_fn))
    eng_fn = require(re.search(r"fn lang_to_eng_name.*?\n\}", lang_src, re.DOTALL), "lang_to_eng_name").group()
    eng = dict(re.findall(r'Lang::(\w+) => "([^"]+)",', eng_fn))
    if len(order) != 70 or len(code) != 70 or len(eng) != 70:
        msg = f"expected 70 languages, parsed order={len(order)} code={len(code)} eng={len(eng)}"
        raise SystemExit(msg)
    return order, code, eng


def parse_profiles(profiles_src: str) -> Profiles:
    """Parse each ``*_LANGS`` static into an ordered list of (Lang, [trigram code-point triples])."""
    out: Profiles = {}
    for static in _PROFILE_STATICS:
        block = re.search(rf"pub static {static}: LangProfileList = &\[(.*?)\n\];", profiles_src, re.DOTALL)
        if block is None:
            msg = f"{static} not found; whatlang layout changed"
            raise SystemExit(msg)
        langs: list[tuple[str, list[tuple[int, int, int]]]] = []
        for lang, body in re.findall(r"Lang::(\w+),\s*&\[(.*?)\],\s*\n\s*\)", block.group(1), re.DOTALL):
            trigrams = [(ord(a), ord(b), ord(c)) for a, b, c in re.findall(r"Trigram\('(.)', '(.)', '(.)'\)", body)]
            langs.append((lang, trigrams))
        out[static] = langs
    return out


def flatten_ranges(
    order: list[tuple[str, str]], ranges: dict[str, list[tuple[int, int]]]
) -> list[tuple[int, int, int]]:
    """
    Merge every script's ranges into one disjoint (low, high, script_id) list, sorted by low.

    A handful of whatlang ranges overlap (e.g. U+1D2B sits in both the Latin Phonetic Extensions
    block and is separately claimed by Cyrillic). whatlang resolves such a code point by first match
    in its initial script order, so the earlier-listed script wins; the table gives that script
    priority and subtracts the overlap from the later one, keeping the ranges disjoint for a lookup.
    """
    claimed: list[tuple[int, int]] = []
    merged: list[tuple[int, int, int]] = []
    for script_id, (_script, fn_name) in enumerate(order):
        for low, high in ranges[fn_name]:
            for sub_low, sub_high in subtract_claimed(low, high, claimed):
                merged.append((sub_low, sub_high, script_id))
            claimed = union_interval(claimed, low, high)
    merged.sort()
    return merged


def subtract_claimed(low: int, high: int, claimed: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return the parts of [low, high] not already covered by a claimed interval."""
    pieces = [(low, high)]
    for claim_low, claim_high in claimed:
        pieces = [seg for piece in pieces for seg in cut(piece, claim_low, claim_high)]
    return pieces


def cut(piece: tuple[int, int], claim_low: int, claim_high: int) -> list[tuple[int, int]]:
    """Remove [claim_low, claim_high] from one [low, high] segment, yielding the surviving sub-segments."""
    low, high = piece
    if claim_high < low or claim_low > high:
        return [piece]
    result: list[tuple[int, int]] = []
    if low < claim_low:
        result.append((low, claim_low - 1))
    if high > claim_high:
        result.append((claim_high + 1, high))
    return result


def union_interval(claimed: list[tuple[int, int]], low: int, high: int) -> list[tuple[int, int]]:
    """Add [low, high] to a set of intervals, merging any that now touch or overlap."""
    merged = sorted([*claimed, (low, high)])
    out: list[tuple[int, int]] = []
    for start, stop in merged:
        if out and start <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], stop))
        else:
            out.append((start, stop))
    return out


def emit_banner(revision: str) -> list[str]:
    """Build the header comment recording provenance and the one include the tables need."""
    return [
        "/* Generated by tools/generate_language_detector.py from whatlang",
        "   (https://github.com/greyblake/whatlang-rs, MIT, Copyright Sergey Potapov) at",
        f"   revision {revision}. Do not edit by hand; rerun the generator.",
        "",
        "   These are whatlang's Unicode script ranges (mapping each code point to a script),",
        "   its script-to-language grouping, its language metadata, and its character-trigram",
        "   profiles (300 most-frequent trigrams per language, most-frequent first -- the",
        "   Cavnar-Trenkle rank model the detector scores text against). */",
        "",
        "#include <stdint.h>",
        "",
    ]


def emit_scripts(order: list[tuple[str, str]], lang_id: dict[str, int]) -> list[str]:
    """Build the script id enum, the script-name table, and the two language-id defines the detector needs."""
    return [
        "enum {",
        *(f"    TH_LANG_SCRIPT_{script.upper()} = {index}," for index, (script, _fn) in enumerate(order)),
        f"    TH_LANG_SCRIPT_COUNT = {len(order)},",
        "};",
        "",
        f"static const char *const th_lang_script_names[{len(order)}] = {{",
        *(f'    "{script}",' for script, _fn in order),
        "};",
        "",
        f"#define TH_LANG_ID_CMN {lang_id['Cmn']}",
        f"#define TH_LANG_ID_JPN {lang_id['Jpn']}",
        "",
    ]


def emit_ranges(merged_ranges: list[tuple[int, int, int]]) -> list[str]:
    """Render the sorted, disjoint code-point range table the script lookup scans."""
    return [
        "typedef struct {",
        "    uint32_t low;",
        "    uint32_t high;",
        "    uint8_t script;",
        "} th_lang_range;",
        "",
        f"static const th_lang_range th_lang_script_ranges[{len(merged_ranges)}] = {{",
        *(f"    {{0x{low:04X}, 0x{high:04X}, {script_id}}}," for low, high, script_id in merged_ranges),
        "};",
        "",
    ]


def emit_meta(lang_order: list[str], lang_code: dict[str, str], lang_eng: dict[str, str]) -> list[str]:
    """Render the per-language ISO 639-3 code and English name, indexed by language id."""
    return [
        "typedef struct {",
        "    const char *code;",
        "    const char *name;",
        "} th_lang_meta;",
        "",
        f"static const th_lang_meta th_lang_meta_table[{len(lang_order)}] = {{",
        *(f'    {{"{lang_code[name]}", "{lang_eng[name]}"}},' for name in lang_order),
        "};",
        "",
    ]


def emit_groups(order: list[tuple[str, str]], groups: dict[str, tuple[str, str]], lang_id: dict[str, int]) -> list[str]:
    """Render the per-script classification: single language, multi-language script, or Mandarin."""
    multi_index = {name: index for index, name in enumerate(_MULTI_SCRIPTS)}
    rows: list[str] = []
    for script, _fn in order:
        kind, payload = groups[script]
        if kind == "one":
            rows.append(f"    {{0, {lang_id[payload]}}},  /* {script} -> {payload} */")
        elif kind == "multi":
            rows.append(f"    {{1, {multi_index[payload]}}},  /* {script} */")
        else:
            rows.append(f"    {{2, 0}},  /* {script} */")
    return [
        "/* Per-script classification: kind 0 = single language (payload = language id),",
        "   kind 1 = multi-language script (payload = profile-list index), kind 2 = Mandarin",
        "   (Han script, disambiguated from Japanese by the kana ratio). */",
        "typedef struct {",
        "    uint8_t kind;",
        "    uint8_t payload;",
        "} th_lang_script_group;",
        "",
        f"static const th_lang_script_group th_lang_script_groups[{len(order)}] = {{",
        *rows,
        "};",
        "",
    ]


def emit_trigram_arrays(profiles: Profiles, lang_code: dict[str, str]) -> list[str]:
    """Render one packed 300-trigram array per language, four trigrams to a line."""
    lines = ["typedef struct {", "    uint32_t a;", "    uint32_t b;", "    uint32_t c;", "} th_lang_trigram;", ""]
    for static in _PROFILE_STATICS:
        for lang, trigrams in profiles[static]:
            lines.append(f"static const th_lang_trigram th_lang_trigrams_{lang_code[lang]}[{len(trigrams)}] = {{")
            for start in range(0, len(trigrams), 4):
                chunk = "".join(f"{{0x{a:04X},0x{b:04X},0x{c:04X}}}, " for a, b, c in trigrams[start : start + 4])
                lines.append(f"    {chunk.rstrip()}")
            lines.append("};")
    return lines


def emit_profile_lists(profiles: Profiles, lang_code: dict[str, str], lang_id: dict[str, int]) -> list[str]:
    """Render each script's language-to-trigram-array table and the script-indexed list of those tables."""
    lines = [
        "",
        "typedef struct {",
        "    uint8_t lang;",
        "    const th_lang_trigram *trigrams;",
        "    uint16_t count;",
        "} th_lang_profile;",
        "",
    ]
    for static in _PROFILE_STATICS:
        entries = profiles[static]
        lines.append(f"static const th_lang_profile th_lang_profiles_{static[:-6].lower()}[{len(entries)}] = {{")
        lines += [
            f"    {{{lang_id[lang]}, th_lang_trigrams_{lang_code[lang]}, {len(trigrams)}}},"
            for lang, trigrams in entries
        ]
        lines.append("};")
    lines += [
        "",
        "typedef struct {",
        "    const th_lang_profile *profiles;",
        "    uint8_t count;",
        "} th_lang_profile_list;",
        "",
        f"static const th_lang_profile_list th_lang_profile_lists[{len(_MULTI_SCRIPTS)}] = {{",
    ]
    lines += [
        f"    {{th_lang_profiles_{static[:-6].lower()}, {len(profiles[static])}}}," for static in _PROFILE_STATICS
    ]
    lines.append("};")
    return lines


def emit(whatlang: Path) -> str:
    """Return the full generated C header for a whatlang checkout."""
    src = whatlang / "src"
    order = parse_script_order((src / "scripts" / "detect.rs").read_text(encoding="utf-8"))
    ranges = parse_script_ranges((src / "scripts" / "chars.rs").read_text(encoding="utf-8"))
    groups = parse_lang_groups((src / "scripts" / "grouping.rs").read_text(encoding="utf-8"))
    lang_order, lang_code, lang_eng = parse_lang_table((src / "lang.rs").read_text(encoding="utf-8"))
    profiles = parse_profiles((src / "trigrams" / "profiles.rs").read_text(encoding="utf-8"))
    revision = subprocess.run(
        ["git", "-C", str(whatlang), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    lang_id = {name: index for index, name in enumerate(lang_order)}
    sections = [
        *emit_banner(revision),
        *emit_scripts(order, lang_id),
        *emit_ranges(flatten_ranges(order, ranges)),
        *emit_meta(lang_order, lang_code, lang_eng),
        *emit_groups(order, groups, lang_id),
        *emit_trigram_arrays(profiles, lang_code),
        *emit_profile_lists(profiles, lang_code, lang_id),
    ]
    return "\n".join(sections) + "\n"


def main() -> None:
    """Write the generated header to the path in argv[2]."""
    if len(sys.argv) != 3:
        msg = "usage: generate_language_detector.py <whatlang-rs-checkout> <output.h>"
        raise SystemExit(msg)
    Path(sys.argv[2]).write_text(emit(Path(sys.argv[1])), encoding="utf-8")


if __name__ == "__main__":
    main()
