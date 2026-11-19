from __future__ import annotations

import itertools
import random
import string
from typing import TYPE_CHECKING, Final

import pytest

import turbohtml
from turbohtml.clean import minify_css, minify_js
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps

_LOW: Final[int] = 0xFFFF
_FNV32: Final[tuple[int, int]] = (2166136261, 16777619)
_FNV64: Final[tuple[int, int]] = (14695981039346656037, 1099511628211)
_XSLT64: Final[tuple[int, int]] = (1469598103934665603, 1099511628211)  # the basis xslt.c's str_hash starts from
_STEPS: Final[int] = 10
_FACTOR: Final[float] = 16.0
_LINED_UP: Final[int] = 512
_TRANSLATE_FROM: Final[int] = 512
_TRANSLATE_CAPACITY: Final[int] = 1024  # th_grow_cap(2 * 512) slots, the stride that puts every from character home
_TRANSLATE_MULTIPLIER: Final[int] = 2654435761  # the multiplicative hash of translate() in xpath/functions.c


def _attribute_tag(names: list[str]) -> str:
    return f"<i {' '.join(names)}>"


def _tokenize(names: list[str]) -> Callable[[], object]:
    return lambda: list(turbohtml.tokenize(_attribute_tag(names)))


def _parse(names: list[str]) -> Callable[[], object]:
    return lambda: turbohtml.parse(_attribute_tag(names))


def _css_declarations(names: list[str]) -> Callable[[], object]:
    return lambda: minify_css(f"p{{{';'.join(f'{name}:1' for name in names)}}}")


def _xpath_id(names: list[str]) -> Callable[[], object]:
    document: Final[turbohtml.Document] = turbohtml.parse(
        f'<input aria-labelledby="{" ".join(names)}">{"".join(f"<p id={name}>" for name in names)}'
    )
    return lambda: document.xpath("id(//input/@aria-labelledby)")


def _xsl_key(names: list[str]) -> Callable[[], object]:
    source: Final[turbohtml.Document] = turbohtml.parse_xml(
        "<items>{}</items>".format("".join(f'<item id="{name}"/>' for name in names))
    )
    transform: Final[Transform] = Transform(
        turbohtml.parse_xml(
            '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
            '<xsl:output method="text"/><xsl:key name="by-id" match="item" use="@id"/>'
            '<xsl:template match="/"><xsl:value-of select="count(key(\'by-id\', //item/@id))"/></xsl:template>'
            "</xsl:stylesheet>"
        ),
        allow_imports=False,
    )
    return lambda: transform(source)


def _xsl_attribute_sets(names: list[str]) -> Callable[[], object]:
    sets: Final[str] = "".join(f'<xsl:attribute-set name="{name}"/>' for name in names)
    uses: Final[str] = " ".join(names)
    sheet: Final[turbohtml.Document] = turbohtml.parse_xml(
        f'<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">{sets}'
        f'<xsl:template match="/"><r xsl:use-attribute-sets="{uses}"/></xsl:template></xsl:stylesheet>'
    )
    source: Final[turbohtml.Document] = turbohtml.parse_xml("<r/>")
    return lambda: Transform(sheet, allow_imports=False)(source)


def _xsl_key_names(names: list[str]) -> Callable[[], object]:
    keys: Final[str] = "".join(f'<xsl:key name="{name}" match="item" use="@id"/>' for name in names)
    sheet: Final[turbohtml.Document] = turbohtml.parse_xml(
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
        f'<xsl:output method="text"/>{keys}<xsl:template match="/">'
        f"<xsl:value-of select=\"count(key('{names[-1]}', 'x'))\"/></xsl:template></xsl:stylesheet>"
    )
    source: Final[turbohtml.Document] = turbohtml.parse_xml('<items><item id="x"/></items>')
    return lambda: Transform(sheet, allow_imports=False)(source)


def _xsl_named_templates(names: list[str]) -> Callable[[], object]:
    templates: Final[str] = "".join(
        f'<xsl:template name="{name}"><xsl:text>ok</xsl:text></xsl:template>' for name in names
    )
    sheet: Final[turbohtml.Document] = turbohtml.parse_xml(
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
        f'<xsl:output method="text"/>{templates}<xsl:template match="/">'
        f'<xsl:call-template name="{names[-1]}"/></xsl:template></xsl:stylesheet>'
    )
    source: Final[turbohtml.Document] = turbohtml.parse_xml("<r/>")
    return lambda: Transform(sheet, allow_imports=False)(source)


def _minify_js_names(names: list[str]) -> Callable[[], object]:
    source: Final[str] = f"function f(){{var {','.join(names)};return [{','.join(names)}]}}"
    return lambda: minify_js(source)


def _microdata(names: list[str]) -> Callable[[], object]:
    document: Final[turbohtml.Document] = turbohtml.parse(
        f"<div itemscope itemref=x></div>{''.join(f'<p id={name}>' for name in names)}"
    )
    return document.microdata


def _shadow_slots(names: list[str]) -> Callable[[], object]:
    slots: Final[str] = "".join(f"<slot name={name}></slot>" for name in names)
    document: Final[turbohtml.Document] = turbohtml.parse(
        f"<div><template shadowrootmode=open>{slots}</template><b slot=q>y</b></div>"
    )
    return lambda: document.select("div")[0].flattened_children


def _css_path(names: list[str]) -> Callable[[], object]:
    document: Final[turbohtml.Document] = turbohtml.parse(
        f"<!doctype html>{''.join(f'<p id={name}>' for name in names)}"
    )
    return document.select("p")[0].css_path


def _radio_group(names: list[str]) -> Callable[[], object]:
    ids: Final[str] = "".join(f"<p id={name}>" for name in names)
    radios: Final[str] = "".join(f"<input type=radio name=g checked form={name}>" for name in names)
    document: Final[turbohtml.Document] = turbohtml.parse(f"{ids}{radios}<input type=radio name=g>")
    target: Final[turbohtml.Element] = document.select("input")[-1]

    def check() -> None:
        target.checked = True

    return check


def _css_rule_selectors(names: list[str]) -> Callable[[], object]:
    source: Final[str] = "".join(f"{name}{{order:{index}}}" for index, name in enumerate(names))
    return lambda: minify_css(source)


@pytest.mark.parametrize(
    ("fnv", "build"),
    [
        pytest.param(_FNV32, _tokenize, id="tokenizer-attribute-names"),
        pytest.param(_FNV32, _parse, id="tree-attribute-names"),
        pytest.param(_FNV32, _css_declarations, id="css-declarations"),
        pytest.param(_FNV64, _xpath_id, id="xpath-id-tokens"),
        pytest.param(_XSLT64, _xsl_key, id="xsl-key-values"),
        pytest.param(_XSLT64, _xsl_attribute_sets, id="xsl-attribute-set-names"),
        pytest.param(_XSLT64, _xsl_key_names, id="xsl-key-names"),
        pytest.param(_XSLT64, _xsl_named_templates, id="xsl-template-names"),
        pytest.param(_FNV32, _minify_js_names, id="minify-js-names"),
        pytest.param(_FNV32, _microdata, id="microdata-ids"),
        pytest.param(_FNV32, _shadow_slots, id="shadow-slot-names"),
        pytest.param(_FNV64, _css_path, id="css-path-ids"),
        pytest.param(_FNV64, _radio_group, id="radio-group-form-ids"),
        pytest.param(_FNV32, _css_rule_selectors, id="css-rule-selectors"),
    ],
)
def test_names_on_one_home_slot_stay_within_the_step_budget(
    work_steps: WorkSteps, fnv: tuple[int, int], build: Callable[[list[str]], Callable[[], object]]
) -> None:
    collide = ["".join(choice) for choice in itertools.product(*_block_pairs(fnv, _STEPS))]
    work_steps.assert_within(build(collide), build(_spread(len(collide), len(collide[0]))), factor=_FACTOR)


def _block_pairs(fnv: tuple[int, int], steps: int) -> list[tuple[str, str]]:
    # Two three-letter blocks that take the same low 16 bits of FNV-1a state to the same low 16 bits, chained: the low
    # bits of the state only ever depend on the low bits before them, so any choice of one block per pair lands there.
    basis, prime = fnv
    state = basis & _LOW
    pairs: list[tuple[str, str]] = []
    for _ in range(steps):
        groups: dict[int, list[str]] = {}
        for letters in itertools.product(string.ascii_lowercase, repeat=3):
            block = "".join(letters)
            low = state
            for char in block:
                low = ((low ^ ord(char)) * prime) & _LOW
            groups.setdefault(low, []).append(block)
        shared = next(key for key, blocks in groups.items() if len(blocks) >= 2)
        pairs.append((groups[shared][0], groups[shared][1]))
        state = shared
    return pairs


def _spread(count: int, length: int) -> list[str]:
    rng: Final[random.Random] = random.Random(1)  # ruff: ignore[suspicious-non-cryptographic-random-usage]  # reproducible input
    return ["".join(rng.choices(string.ascii_lowercase, k=length)) for _ in range(count)]


def test_attribute_lookup_after_the_name_table_moved_to_the_keyed_hash() -> None:
    names: Final[list[str]] = ["".join(choice) for choice in itertools.product(*_block_pairs(_FNV32, _STEPS))]
    document: Final[turbohtml.Document] = turbohtml.parse(_attribute_tag(names))

    found = (document.select(f"i[{names[-1]}]"), document.select("i[absent]"))

    assert (len(found[0]), len(found[1])) == (1, 0)


def test_attribute_name_interned_again_after_the_name_table_moved_to_the_keyed_hash() -> None:
    names: Final[list[str]] = ["".join(choice) for choice in itertools.product(*_block_pairs(_FNV32, _STEPS))]
    document: Final[turbohtml.Document] = turbohtml.parse(f"{_attribute_tag(names)}<i {names[0]}>")

    assert len(document.select(f"i[{names[0]}]")) == 2


def test_attribute_equality_when_both_name_tables_moved_to_the_keyed_hash() -> None:
    names: Final[list[str]] = ["".join(choice) for choice in itertools.product(*_block_pairs(_FNV32, _STEPS))]
    left: Final[turbohtml.Element] = turbohtml.Element("div", dict.fromkeys(names, "same"))
    right: Final[turbohtml.Element] = turbohtml.Element("div", dict.fromkeys(reversed(names), "same"))
    changed: Final[turbohtml.Element] = turbohtml.Element(
        "div", dict.fromkeys(reversed(names), "same") | {names[0]: "other"}
    )

    assert (left.equals(right), left.equals(changed)) == (True, False)


@pytest.mark.parametrize(
    "overrides",
    [pytest.param({}, id="equal"), pytest.param({"data-3": "other"}, id="changed-value")],
)
def test_attribute_equality_across_a_keyed_and_a_fnv_name_table(overrides: dict[str, str]) -> None:
    names: Final[list[str]] = ["".join(choice) for choice in itertools.product(*_block_pairs(_FNV32, _STEPS))]
    ordinary: Final[dict[str, str]] = {f"data-{index}": "same" for index in range(40)}
    keyed: Final[turbohtml.Element] = turbohtml.Element("div", dict.fromkeys(names, "same"))
    for name in names:
        del keyed.attrs[name]
    keyed.attrs.update(ordinary | overrides)
    plain: Final[turbohtml.Element] = turbohtml.Element("div", dict(reversed(ordinary.items())))

    assert (keyed.equals(plain), plain.equals(keyed)) == (not overrides, not overrides)


def test_xsl_key_duplicate_value_after_the_map_moved_to_the_keyed_hash() -> None:
    names: Final[list[str]] = ["".join(choice) for choice in itertools.product(*_block_pairs(_XSLT64, _STEPS))]

    assert _xsl_key(names + names[:1])() == str(len(names) + 1)


def test_duplicate_slot_names_after_the_slot_index_moved_to_the_keyed_hash() -> None:
    names: Final[list[str]] = ["".join(choice) for choice in itertools.product(*_block_pairs(_FNV32, _STEPS))]
    slots: Final[str] = "".join(f"<slot name={name}></slot>" for name in [*names, *names[:4]])
    children: Final[str] = "".join(f"<b slot={name}>{name}</b>" for name in names[:4])
    document: Final[turbohtml.Document] = turbohtml.parse(
        f"<div><template shadowrootmode=open>{slots}</template>{children}</div>"
    )

    assert [node.text for node in document.select("div")[0].flattened_children] == names[:4]


def _attribute_misses(
    build: Callable[[list[str]], Callable[[], object]],
) -> Callable[[list[str], list[str]], Callable[[], object]]:
    return lambda run, misses: build(run + misses)


def _xpath_id_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    # The tokens go from the last home back, so none is seated behind a filled slot and only a miss finds the run.
    document: Final[turbohtml.Document] = turbohtml.parse(
        f'<input aria-labelledby="{" ".join(reversed(run))}">{"".join(f"<p id={name}>" for name in misses)}'
    )
    return lambda: document.xpath("id(//input/@aria-labelledby)")


def _xsl_key_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    items: Final[str] = "".join(f'<item id="{name}"/>' for name in run) + "".join(
        f'<probe v="{name}"/>' for name in misses
    )
    source: Final[turbohtml.Document] = turbohtml.parse_xml(f"<items>{items}</items>")
    transform: Final[Transform] = Transform(
        turbohtml.parse_xml(
            '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
            '<xsl:output method="text"/><xsl:key name="by-id" match="item" use="@id"/>'
            '<xsl:template match="/"><xsl:value-of select="count(key(\'by-id\', //probe/@v))"/></xsl:template>'
            "</xsl:stylesheet>"
        ),
        allow_imports=False,
    )
    return lambda: transform(source)


def _xsl_attribute_set_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    # An attribute set that use-attribute-sets names but the stylesheet never declares is skipped, so every element
    # walks each such miss again.
    sets: Final[str] = "".join(f'<xsl:attribute-set name="{name}"/>' for name in run)
    sheet: Final[turbohtml.Document] = turbohtml.parse_xml(
        f'<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">{sets}'
        f'<xsl:template match="/"><r xsl:use-attribute-sets="{" ".join(misses)}"/></xsl:template></xsl:stylesheet>'
    )
    source: Final[turbohtml.Document] = turbohtml.parse_xml("<r/>")
    return lambda: Transform(sheet, allow_imports=False)(source)


def _shadow_slot_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    # Light children name slots the shadow root never declares, so each one's fill walks the run of declared slots.
    # Every fourth declared slot also gets a child, so some lookups end their FNV-1a walk on an empty slot.
    slots: Final[str] = "".join(f"<slot name={name}></slot>" for name in run)
    children: Final[str] = "".join(f"<b slot={name}>y</b>" for name in [*misses, *run[::4]])
    document: Final[turbohtml.Document] = turbohtml.parse(
        f"<div><template shadowrootmode=open>{slots}</template>{children}</div>"
    )
    return lambda: document.select("div")[0].flattened_children


def _minify_js_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    # Each absent name is read four times, so its misses outweigh the steps the mangler takes on any source this size.
    source: Final[str] = f"function f(){{var {','.join(run)};return [{','.join(misses * 4)}]}}"
    return lambda: minify_js(source)


@pytest.mark.parametrize(
    ("fnv", "build"),
    [
        pytest.param(_FNV32, _attribute_misses(_tokenize), id="tokenizer-attribute-names"),
        pytest.param(_FNV32, _attribute_misses(_parse), id="tree-attribute-names"),
        pytest.param(_FNV64, _xpath_id_misses, id="xpath-id-tokens"),
        pytest.param(_XSLT64, _xsl_key_misses, id="xsl-key-values"),
        pytest.param(_XSLT64, _xsl_attribute_set_misses, id="xsl-attribute-set-names"),
        pytest.param(_FNV32, _minify_js_misses, id="minify-js-names"),
        pytest.param(_FNV32, _shadow_slot_misses, id="shadow-slot-names"),
    ],
)
def test_misses_along_lined_up_homes_stay_within_the_step_budget(
    work_steps: WorkSteps, fnv: tuple[int, int], build: Callable[[list[str], list[str]], Callable[[], object]]
) -> None:
    run, misses = _lined_up(fnv)
    control = _spread(len(run) + len(misses), len(run[0]))
    work_steps.assert_within(build(run, misses), build(control[: len(run)], control[len(run) :]), factor=_FACTOR)


def _lined_up(fnv: tuple[int, int], base: int = 0) -> tuple[list[str], list[str]]:
    # _LINED_UP names on consecutive home slots from base, each seated on its own home, and as many absent names, one
    # more on each of those homes: a miss walks to the end of the run although no name sits away from its home. The low
    # 16 bits make the homes consecutive in any table of _LINED_UP to 2**16 slots.
    basis, prime = fnv
    prefixes: dict[int, str] = {}
    for letters in itertools.product(string.ascii_lowercase, repeat=3):
        state = basis & _LOW
        for char in letters:
            state = ((state ^ ord(char)) * prime) & _LOW
        prefixes.setdefault(state, "".join(letters))
    run, misses = zip(*(_ending_at(fnv, prefixes, (base + home) & _LOW)[:2] for home in range(_LINED_UP)), strict=True)
    return list(run), list(misses)


def _ending_at(fnv: tuple[int, int], prefixes: dict[int, str], target: int) -> list[str]:
    # Undo the last two letters from target (the prime is odd, so it has an inverse modulo 2**16) and look the state
    # before them up among the prefixes: the five-letter names whose FNV-1a state ends with low 16 bits target.
    inverse = pow(fnv[1], -1, _LOW + 1)
    names: list[str] = []
    for first, second in itertools.product(string.ascii_lowercase, repeat=2):
        state = (((target * inverse) & _LOW) ^ ord(second)) * inverse & _LOW ^ ord(first)
        if (prefix := prefixes.get(state)) is not None:
            names.append(prefix + first + second)
    return names


def test_translate_characters_on_one_home_slot_stay_within_the_step_budget(work_steps: WorkSteps) -> None:
    # The multiplicative hash keeps the product's low bits, which code points that agree in their low bits share.
    work_steps.assert_within(
        _translate(_code_points(0x400, _TRANSLATE_CAPACITY)), _translate(_code_points(0x400, 1)), factor=_FACTOR
    )


def _translate(characters: list[str]) -> Callable[[], object]:
    source: Final[str] = "".join(characters)
    document: Final[turbohtml.Document] = turbohtml.parse_xml(f"<r>{source * 8}</r>")
    return lambda: document.xpath(f"translate(string(/r), '{source}', '{source[::-1]}')")


def _code_points(start: int, stride: int) -> list[str]:
    # Code points from start in steps of stride, skipping the surrogates and the two noncharacters XML rejects.
    points = (point for point in range(start, 0x110000, stride) if not 0xD800 <= point <= 0xDFFF)
    return [chr(point) for point in itertools.islice(points, _TRANSLATE_FROM) if point & 0xFFFE != 0xFFFE]


def test_translate_misses_along_lined_up_homes_stay_within_the_step_budget(work_steps: WorkSteps) -> None:
    # Every from character on its own home, the homes in one run that wraps past the last slot, and a text of absent
    # characters on the same homes: each miss walks to the end of the run although no character sits away from home.
    homes: Final[range] = range(_TRANSLATE_CAPACITY - 100, _TRANSLATE_CAPACITY - 100 + _TRANSLATE_FROM)
    work_steps.assert_within(
        _translate_misses(_on_homes(homes, _TRANSLATE_CAPACITY), _on_homes(homes, 2 * _TRANSLATE_CAPACITY)),
        _translate_misses(_code_points(0x400, 1), _code_points(0x800, 1)),
        factor=_FACTOR,
    )


def _translate_misses(source: list[str], misses: list[str]) -> Callable[[], object]:
    document: Final[turbohtml.Document] = turbohtml.parse_xml(f"<r>{''.join(misses) * 4}</r>")
    return lambda: document.xpath(f"translate(string(/r), '{''.join(source)}', '')")


def _on_homes(homes: range, offset: int) -> list[str]:
    # The code points from offset whose multiplicative hash puts them on each of homes in _TRANSLATE_CAPACITY slots.
    inverse: Final[int] = pow(_TRANSLATE_MULTIPLIER, -1, _TRANSLATE_CAPACITY)
    return [chr(home % _TRANSLATE_CAPACITY * inverse % _TRANSLATE_CAPACITY + offset) for home in homes]


def test_translate_keeps_a_short_lined_up_run() -> None:
    source: Final[str] = "".join(_on_homes(range(40), _TRANSLATE_CAPACITY)) + "".join(
        chr(0x3000 + 7 * i) for i in range(60)
    )
    target: Final[str] = "".join(chr(0x4000 + index) for index in range(len(source)))
    text: Final[str] = f"{source}xyz" * 4
    document: Final[turbohtml.Document] = turbohtml.parse_xml(f"<r>{text}</r>")

    result = document.xpath(f"translate(string(/r), '{source}', '{target}')")

    assert result == text.translate(str.maketrans(source, target))


def test_translate_duplicate_from_character_on_the_keyed_fill() -> None:
    source: Final[str] = "".join(_code_points(0x400, _TRANSLATE_CAPACITY))
    target: Final[str] = source[::-1]
    text: Final[str] = source * 8
    document: Final[turbohtml.Document] = turbohtml.parse_xml(f"<r>{text}</r>")

    result = document.xpath(f"translate(string(/r), '{source}{source[0]}', '{target}Z')")

    assert result == text.translate(str.maketrans(source, target))


def test_css_path_in_quirks_mode_after_the_id_map_moved_to_the_keyed_hash() -> None:
    names: Final[list[str]] = ["".join(choice) for choice in itertools.product(*_block_pairs(_FNV64, _STEPS))]
    long_id: Final[str] = "q" * 70  # longer than the 64-code-point buffer the case-folding hash works in
    document: Final[turbohtml.Document] = turbohtml.parse("".join(f"<p id={name}>" for name in [*names, long_id]))

    assert document.select("p")[-1].css_path() == f"#{long_id}"


def test_radio_form_miss_after_the_id_table_moved_to_the_keyed_hash() -> None:
    run, misses = _lined_up(_FNV64)
    forms: Final[str] = "".join(f"<form id={name}></form>" for name in run)
    # The first miss moves the table to the keyed hash; the later ones look up a form id whose FNV-1a home is empty.
    misses_radios: Final[str] = "".join(f"<input type=radio name=g checked form={name}>" for name in misses[:21])
    document: Final[turbohtml.Document] = turbohtml.parse(
        f"{forms}<input type=radio name=g checked form={run[0]}>{misses_radios}<input type=radio name=g>"
    )
    radios: Final[list[turbohtml.Element]] = document.select("input")

    radios[-1].checked = True

    assert [radio.checked for radio in radios] == [True, *[False] * 21, True]


def test_wide_keyword_resets_a_longhand_after_the_declaration_table_moved_to_the_keyed_hash() -> None:
    # The shorthand's own name starts the walk over the run, so the FNV-1a pass gives up and the keyed pass drops the
    # longhand the keyword resets.
    run, _ = _lined_up(_FNV32, _low_state(_FNV32, "margin"))
    declarations: Final[str] = ";".join(f"{name}:1" for name in run)

    assert minify_css(f"p{{margin-top:9px;{declarations};margin:inherit}}") == minify_css(
        f"p{{{declarations};margin:inherit}}"
    )


def _low_state(fnv: tuple[int, int], text: str) -> int:
    basis, prime = fnv
    state = basis & _LOW
    for char in text:
        state = ((state ^ ord(char)) * prime) & _LOW
    return state


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(_xsl_key_names, "1", id="xsl-key-names"),
        pytest.param(_xsl_named_templates, "ok", id="xsl-template-names"),
    ],
)
def test_xsl_names_lined_up_across_the_end_of_the_index_still_resolve(
    build: Callable[[list[str]], Callable[[], object]], expected: str
) -> None:
    # The run starts 100 slots before the end of a 1024-slot index, so the walk from its first full block runs back
    # across the end, and the lookup of its last name must still find the declaration.
    names, _ = _lined_up(_XSLT64, 1024 - 100)

    assert build(names)() == expected
