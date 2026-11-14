from __future__ import annotations

import itertools
import random
import string
import time
from typing import TYPE_CHECKING, Final, TypeVar

import pytest

import turbohtml
from turbohtml import Document, parse_xml
from turbohtml.clean import minify_css, minify_js
from turbohtml.detect import detect_language
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable

_T = TypeVar("_T")
_MASK32: Final[int] = (1 << 32) - 1
_MASK64: Final[int] = (1 << 64) - 1
_FNV32: Final[tuple[int, int, int]] = (2166136261, 16777619, _MASK32)
_FNV64: Final[tuple[int, int, int]] = (14695981039346656037, 1099511628211, _MASK64)
_CJK: Final[str] = "".join(map(chr, range(0x4E00, 0x4F00)))
_WIDE: Final[str] = "".join(map(chr, range(0x80, 0xD800)))
_PRIVATE: Final[str] = "".join(map(chr, range(0xE000, 0xF900)))
_LOWER: Final[str] = string.ascii_lowercase


def _fnv(text: str, kind: tuple[int, int, int]) -> int:
    value, prime, mask = kind
    for char in text:
        value = ((value ^ ord(char)) * prime) & mask
    return value


def _ids(count: int, kind: tuple[int, int, int]) -> list[str]:
    names: list[str] = []
    for chars in itertools.product(string.ascii_letters + string.digits, repeat=3):
        prefix = "".join(chars)
        if (last := _fnv(prefix, kind) & 0xFFFFF) >= 0x10000:
            names.append(prefix + chr(last))
            if len(names) == count:
                break
    return names


def _shared_words(count: int, bound: int) -> list[str]:
    made = ("".join(chars) for chars in itertools.product(string.ascii_lowercase, repeat=5))
    return list(itertools.islice((word for word in made if _fnv(word, _FNV32) & 0xFFFF < bound), count))


def _random_texts(count: int, length: int, alphabet: str) -> list[str]:
    rng: Final = random.Random(1)  # ruff: ignore[suspicious-non-cryptographic-random-usage]  # reproducible input
    return ["".join(rng.choices(alphabet, k=length)) for _ in range(count)]


def _random_text(length: int, alphabet: str) -> str:
    return _random_texts(1, length, alphabet)[0]


def _trigram_text(length: int) -> str:
    rng: Final = random.Random(1)  # ruff: ignore[suspicious-non-cryptographic-random-usage]  # reproducible input
    seen: Final[set[int]] = set()
    text: Final = ["a"]
    pair = [0x20, 0x61]
    while len(text) < length:
        char = rng.randrange(0xE000, 0xF900)
        key = (pair[0] << 42) | (pair[1] << 21) | char
        mix = ((key ^ (key >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        mix = ((mix ^ (mix >> 27)) * 0x94D049BB133111EB) & _MASK64
        if key not in seen and (mix ^ (mix >> 31)) & 0xF8000 == 0:
            seen.add(key)
            pair = [pair[1], char]
            text.append(chr(char))
    return "".join(text)


def _translate_text(length: int) -> str:
    count: Final = length * 3 // 8
    chars: Final = sorted(
        (char for char in range(0x80, 0x110000) if not 0xD800 <= char <= 0xDFFF),
        key=lambda char: (char * 2654435761) & (2 * length - 1),
    )[:count]
    chars.append(chars.pop(5))
    return f"2024{''.join(map(chr, chars))}{chr(chars[-1]) * (length - count - 4)}"


def _xsl_values(count: int, length: int) -> list[str]:
    base: Final = [0x4E00 + position for position in range(length)]
    return [
        "".join(chr(base[position] + ((index >> position) & 1) * 0x100000) for position in range(length))
        for index in range(count)
    ]


def _seconds(make: Callable[[], _T], call: Callable[[_T], object]) -> float:
    data: Final = make()
    start: Final = time.perf_counter()
    call(data)
    return time.perf_counter() - start


def _page(body: str) -> Document:
    return turbohtml.parse(f"<!doctype html>{body}")


def _microdata(keys: list[str]) -> float:
    markup = f"<div itemscope itemref=x></div>{''.join(f'<p id={key}>' for key in keys)}"
    return _seconds(lambda: _page(markup), lambda page: page.microdata())


def _css_path(keys: list[str]) -> float:
    markup = "".join(f"<p id={key}>" for key in keys)
    return _seconds(lambda: _page(markup), lambda page: page.select("p")[0].css_path())


def _shadow(keys: list[str]) -> float:
    slots = "".join(f"<slot name={key}></slot>" for key in keys)
    markup = f"<div><template shadowrootmode=open>{slots}</template><b slot=q>y</b></div>"
    return _seconds(lambda: _page(markup), lambda page: page.select("div")[0].flattened_children)


def _xpath_ids(keys: list[str]) -> float:
    markup = f'<input aria-labelledby="{" ".join(keys)}">'
    return _seconds(lambda: _page(markup), lambda page: page.xpath("id(//input/@aria-labelledby)"))


def _translate(text: str) -> float:
    return _seconds(
        lambda: turbohtml.parse(f"<p>{text}</p>"),
        lambda page: page.xpath("translate(//p, translate(//p, '0123456789', ''), '')"),
    )


def _js(names: list[str]) -> float:
    return _seconds(lambda: "var {}".format(",".join(names)), minify_js)


def _css(declarations: list[str]) -> float:
    return _seconds(lambda: "a{{{}}}".format(";".join(f"{word}:0" for word in declarations)), minify_css)


def _detect(text: str) -> float:
    return _seconds(lambda: text, detect_language)


def _xsl(values: list[str]) -> float:
    xsl_document = parse_xml("<items>{}</items>".format("".join(f'<item id="{value}"/>' for value in values)))
    return _seconds(
        lambda: xsl_document,
        Transform(
            parse_xml(
                """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">
  <xsl:output method="text"/>
  <xsl:key name="by-id" match="item" use="@id"/>
  <xsl:template match="/"><xsl:value-of select="count(key('by-id', 'A1'))"/></xsl:template>
</xsl:stylesheet>"""
            ),
            allow_imports=False,
        ),
    )


@pytest.mark.parametrize(
    ("collide", "spread"),
    [
        pytest.param(
            lambda: _microdata(_ids(35_000, _FNV32)),
            lambda: _microdata(_random_texts(35_000, 4, _CJK)),
            id="microdata-itemref-ids",
        ),
        pytest.param(
            lambda: _css_path(_ids(35_000, _FNV64)),
            lambda: _css_path(_random_texts(35_000, 4, _CJK)),
            id="css-path-ids",
        ),
        pytest.param(
            lambda: _shadow(_ids(35_000, _FNV32)),
            lambda: _shadow(_random_texts(35_000, 4, _CJK)),
            id="shadow-slot-names",
        ),
        pytest.param(
            lambda: _xpath_ids(_ids(54_000, _FNV64)),
            lambda: _xpath_ids(_random_texts(54_000, 4, _CJK)),
            id="xpath-id-tokens",
        ),
        pytest.param(
            lambda: _translate(_translate_text(65_536)),
            lambda: _translate(_random_text(65_536, _WIDE)),
            id="xpath-translate-characters",
        ),
        pytest.param(
            lambda: _js(_shared_words(16_000, 2048)),
            lambda: _js(_random_texts(16_000, 5, _LOWER)),
            id="js-mangler-names",
        ),
        pytest.param(
            lambda: _css(_shared_words(32_768, 4096)),
            lambda: _css(_random_texts(32_768, 5, _LOWER)),
            id="css-declarations",
        ),
        pytest.param(
            lambda: _detect(_trigram_text(83_250)),
            lambda: _detect(_random_text(83_250, _PRIVATE)),
            id="language-trigrams",
        ),
        pytest.param(
            lambda: _xsl(_xsl_values(16_384, 16)),
            lambda: _xsl(_random_texts(16_384, 16, _CJK)),
            id="xsl-key-values",
        ),
    ],
)
def test_keys_chosen_to_share_a_slot_cost_linear_time(
    collide: Callable[[], float], spread: Callable[[], float]
) -> None:
    assert collide() < 10.0 * max(spread(), 0.01)
