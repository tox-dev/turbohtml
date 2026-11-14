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


def _attribute_tag(names: list[str]) -> str:
    return f"<i {' '.join(names)}>"


def _tokenize(names: list[str]) -> Callable[[], object]:
    return lambda: list(turbohtml.tokenize(_attribute_tag(names)))


def _parse(names: list[str]) -> Callable[[], object]:
    return lambda: turbohtml.parse(_attribute_tag(names))


def _css_declarations(names: list[str]) -> Callable[[], object]:
    return lambda: minify_css(f"p{{{';'.join(f'{name}:1' for name in names)}}}")


def _xpath_id(names: list[str]) -> Callable[[], object]:
    document: Final = turbohtml.parse(
        f'<input aria-labelledby="{" ".join(names)}">{"".join(f"<p id={name}>" for name in names)}'
    )
    return lambda: document.xpath("id(//input/@aria-labelledby)")


def _xsl_key(names: list[str]) -> Callable[[], object]:
    source: Final = turbohtml.parse_xml("<items>{}</items>".format("".join(f'<item id="{name}"/>' for name in names)))
    transform: Final = Transform(
        turbohtml.parse_xml(
            '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
            '<xsl:output method="text"/><xsl:key name="by-id" match="item" use="@id"/>'
            '<xsl:template match="/"><xsl:value-of select="count(key(\'by-id\', //item/@id))"/></xsl:template>'
            "</xsl:stylesheet>"
        ),
        allow_imports=False,
    )
    return lambda: transform(source)


def _minify_js_names(names: list[str]) -> Callable[[], object]:
    source: Final = f"function f(){{var {','.join(names)};return [{','.join(names)}]}}"
    return lambda: minify_js(source)


def _microdata(names: list[str]) -> Callable[[], object]:
    document: Final = turbohtml.parse(f"<div itemscope itemref=x></div>{''.join(f'<p id={name}>' for name in names)}")
    return document.microdata


def _shadow_slots(names: list[str]) -> Callable[[], object]:
    slots: Final = "".join(f"<slot name={name}></slot>" for name in names)
    document: Final = turbohtml.parse(f"<div><template shadowrootmode=open>{slots}</template><b slot=q>y</b></div>")
    return lambda: document.select("div")[0].flattened_children


def _css_path(names: list[str]) -> Callable[[], object]:
    document: Final = turbohtml.parse(f"<!doctype html>{''.join(f'<p id={name}>' for name in names)}")
    return document.select("p")[0].css_path


@pytest.mark.parametrize(
    ("fnv", "build"),
    [
        pytest.param(_FNV32, _tokenize, id="tokenizer-attribute-names"),
        pytest.param(_FNV32, _parse, id="tree-attribute-names"),
        pytest.param(_FNV32, _css_declarations, id="css-declarations"),
        pytest.param(_FNV64, _xpath_id, id="xpath-id-tokens"),
        pytest.param(_XSLT64, _xsl_key, id="xsl-key-values"),
        pytest.param(_FNV32, _minify_js_names, id="minify-js-names"),
        pytest.param(_FNV32, _microdata, id="microdata-ids"),
        pytest.param(_FNV32, _shadow_slots, id="shadow-slot-names"),
        pytest.param(_FNV64, _css_path, id="css-path-ids"),
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
        seen: dict[int, str] = {}
        for letters in itertools.product(string.ascii_lowercase, repeat=3):
            block = "".join(letters)
            low = state
            for char in block:
                low = ((low ^ ord(char)) * prime) & _LOW
            if low in seen:
                pairs.append((seen[low], block))
                state = low
                break
            seen[low] = block
    return pairs


def _spread(count: int, length: int) -> list[str]:
    rng: Final = random.Random(1)  # ruff: ignore[suspicious-non-cryptographic-random-usage]  # reproducible input
    return ["".join(rng.choices(string.ascii_lowercase, k=length)) for _ in range(count)]


def _attribute_misses(
    build: Callable[[list[str]], Callable[[], object]],
) -> Callable[[list[str], list[str]], Callable[[], object]]:
    return lambda run, misses: build(run + misses)


def _xpath_id_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    # The tokens go from the last home back, so none is seated behind a filled slot and only a miss finds the run.
    document: Final = turbohtml.parse(
        f'<input aria-labelledby="{" ".join(reversed(run))}">{"".join(f"<p id={name}>" for name in misses)}'
    )
    return lambda: document.xpath("id(//input/@aria-labelledby)")


def _xsl_key_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    items: Final = "".join(f'<item id="{name}"/>' for name in run) + "".join(f'<probe v="{name}"/>' for name in misses)
    source: Final = turbohtml.parse_xml(f"<items>{items}</items>")
    transform: Final = Transform(
        turbohtml.parse_xml(
            '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
            '<xsl:output method="text"/><xsl:key name="by-id" match="item" use="@id"/>'
            '<xsl:template match="/"><xsl:value-of select="count(key(\'by-id\', //probe/@v))"/></xsl:template>'
            "</xsl:stylesheet>"
        ),
        allow_imports=False,
    )
    return lambda: transform(source)


def _minify_js_misses(run: list[str], misses: list[str]) -> Callable[[], object]:
    # Each absent name is read four times, so its misses outweigh the steps the mangler takes on any source this size.
    source: Final = f"function f(){{var {','.join(run)};return [{','.join(misses * 4)}]}}"
    return lambda: minify_js(source)


@pytest.mark.parametrize(
    ("fnv", "build"),
    [
        pytest.param(_FNV32, _attribute_misses(_tokenize), id="tokenizer-attribute-names"),
        pytest.param(_FNV32, _attribute_misses(_parse), id="tree-attribute-names"),
        pytest.param(_FNV64, _xpath_id_misses, id="xpath-id-tokens"),
        pytest.param(_XSLT64, _xsl_key_misses, id="xsl-key-values"),
        pytest.param(_FNV32, _minify_js_misses, id="minify-js-names"),
    ],
)
def test_misses_along_lined_up_homes_stay_within_the_step_budget(
    work_steps: WorkSteps, fnv: tuple[int, int], build: Callable[[list[str], list[str]], Callable[[], object]]
) -> None:
    run, misses = _lined_up(fnv)
    control = _spread(len(run) + len(misses), len(run[0]))
    work_steps.assert_within(build(run, misses), build(control[: len(run)], control[len(run) :]), factor=_FACTOR)


def _lined_up(fnv: tuple[int, int]) -> tuple[list[str], list[str]]:
    # _LINED_UP names on consecutive home slots, each seated on its own home, and as many absent names, one more on each
    # of those homes: a miss walks to the end of the run although no name sits away from its home. The low 16 bits make
    # the homes consecutive in any table of _LINED_UP to 2**16 slots.
    basis, prime = fnv
    prefixes: dict[int, str] = {}
    for letters in itertools.product(string.ascii_lowercase, repeat=3):
        state = basis & _LOW
        for char in letters:
            state = ((state ^ ord(char)) * prime) & _LOW
        prefixes.setdefault(state, "".join(letters))
    run, misses = zip(*(_ending_at(fnv, prefixes, home)[:2] for home in range(_LINED_UP)), strict=True)
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
    source: Final = "".join(characters)
    document: Final = turbohtml.parse_xml(f"<r>{source * 8}</r>")
    return lambda: document.xpath(f"translate(string(/r), '{source}', '{source[::-1]}')")


def _code_points(start: int, stride: int) -> list[str]:
    # Code points from start in steps of stride, skipping the surrogates and the two noncharacters XML rejects.
    points = (point for point in range(start, 0x110000, stride) if not 0xD800 <= point <= 0xDFFF)
    return [chr(point) for point in itertools.islice(points, _TRANSLATE_FROM) if point & 0xFFFE != 0xFFFE]
