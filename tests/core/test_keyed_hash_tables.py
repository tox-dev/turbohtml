from __future__ import annotations

import itertools
import random
import string
import time
from typing import TYPE_CHECKING, Final, NamedTuple, TypeVar

import pytest

import turbohtml
from turbohtml import Document, parse_xml, tokenize
from turbohtml.clean import minify_js
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable

_T = TypeVar("_T")
_COUNT: Final[int] = 3 << 13
_BITS: Final[int] = 16
_MASK: Final[int] = (1 << _BITS) - 1
_MASK64: Final[int] = (1 << 64) - 1
_STEM: Final[int] = 4


class _Table(NamedTuple):
    basis: int
    prime: int
    width: int
    low: int
    high: int


_XSL_KEYS: Final[_Table] = _Table(1469598103934665603, 1099511628211, 4, 0x100, 0x110000)
_ID32: Final[_Table] = _Table(2166136261, 16777619, 4, 0x100, 0x110000)
_ID64: Final[_Table] = _Table(14695981039346656037, 1099511628211, 4, 0x100, 0x110000)
_JS_NAMES: Final[_Table] = _Table(2166136261, 16777619, 4, 0x4E00, 0xA000)
_ATTR_NAMES: Final[_Table] = _Table(2166136261, 16777619, 2, 0x100, 0x10000)


def _in_span(table: _Table, code_point: int) -> bool:
    return table.low <= code_point < table.high and not 0xD800 <= code_point <= 0xDFFF and code_point & 0xFFFE != 0xFFFE


def _units(text: str, width: int) -> list[int]:
    if width == 4:
        return [ord(char) for char in text]
    return [unit for char in text for unit in (ord(char) & 0xFF, ord(char) >> 8)]


def _state(units: list[int], table: _Table) -> int:
    value = table.basis
    for unit in units:
        value = ((value ^ unit) * table.prime) & _MASK64
    return value


def _last_char(state: int, table: _Table) -> str | None:
    if table.width == 4:
        for code_point in range(state & _MASK, 0x110000, _MASK + 1):
            if _in_span(table, code_point):
                return chr(code_point)
        return None
    for low in range(256):
        if (high := (((state ^ low) * table.prime) & _MASK64) & _MASK) < 256 and _in_span(table, low + 256 * high):
            return chr(low + 256 * high)
    return None


def _collide(table: _Table) -> list[str]:
    names: list[str] = []
    for letters in itertools.product(string.ascii_lowercase, repeat=_STEM):
        prefix = "".join(letters)
        if (char := _last_char(_state(_units(prefix, table.width), table), table)) is not None:
            names.append(prefix + char)
            if len(names) == _COUNT:
                break
    return names


def _spread(table: _Table) -> list[str]:
    rng: Final = random.Random(1)  # ruff: ignore[suspicious-non-cryptographic-random-usage]  # reproducible input
    names: list[str] = []
    for _ in range(_COUNT):
        code_point = rng.randrange(table.low, table.high)
        while not _in_span(table, code_point):
            code_point = rng.randrange(table.low, table.high)
        names.append("".join(rng.choices(string.ascii_lowercase, k=_STEM)) + chr(code_point))
    return names


def _timed(make: Callable[[], _T], call: Callable[[_T], object]) -> float:
    data: Final = make()
    start: Final = time.process_time()
    call(data)
    return time.process_time() - start


def _cost(make: Callable[[], _T], call: Callable[[_T], object]) -> float:
    _timed(make, call)
    return min(_timed(make, call) for _ in range(3))


def _page(body: str) -> Document:
    return turbohtml.parse(f"<!doctype html>{body}")


def _xsl(keys: list[str]) -> float:
    markup = "<items>{}</items>".format("".join(f'<item id="{key}"/>' for key in keys))
    key_sheet = parse_xml(
        """<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">
  <xsl:output method="text"/>
  <xsl:key name="by-id" match="item" use="@id"/>
  <xsl:template match="/"><xsl:value-of select="count(key('by-id', 'A1'))"/></xsl:template>
</xsl:stylesheet>"""
    )
    return _cost(lambda: parse_xml(markup), Transform(key_sheet, allow_imports=False))


def _microdata(keys: list[str]) -> float:
    markup = f"<div itemscope itemref=x></div>{''.join(f'<p id={key}>' for key in keys)}"
    return _cost(lambda: _page(markup), lambda document: document.microdata())


def _css_path(keys: list[str]) -> float:
    markup = "".join(f"<p id={key}>" for key in keys)
    return _cost(lambda: _page(markup), lambda document: document.select("p")[0].css_path())


def _shadow(keys: list[str]) -> float:
    slots = "".join(f"<slot name={key}></slot>" for key in keys)
    markup = f"<div><template shadowrootmode=open>{slots}</template><b slot=q>y</b></div>"
    return _cost(lambda: _page(markup), lambda document: document.select("div")[0].flattened_children)


def _xpath_ids(keys: list[str]) -> float:
    markup = f'<input aria-labelledby="{" ".join(keys)}">'
    return _cost(lambda: _page(markup), lambda document: document.xpath("id(//input/@aria-labelledby)"))


def _js(names: list[str]) -> float:
    source = f"var {','.join(names)}"
    return _cost(lambda: source, minify_js)


def _tokenize(names: list[str]) -> float:
    source = f"<a {' '.join(names)}>"
    return _cost(lambda: source, lambda text: list(tokenize(text)))


@pytest.mark.parametrize(
    ("collide", "spread"),
    [
        pytest.param(lambda: _xsl(_collide(_XSL_KEYS)), lambda: _xsl(_spread(_XSL_KEYS)), id="xsl-key-values"),
        pytest.param(lambda: _microdata(_collide(_ID32)), lambda: _microdata(_spread(_ID32)), id="microdata-ids"),
        pytest.param(lambda: _shadow(_collide(_ID32)), lambda: _shadow(_spread(_ID32)), id="shadow-slot-names"),
        pytest.param(lambda: _css_path(_collide(_ID64)), lambda: _css_path(_spread(_ID64)), id="css-path-ids"),
        pytest.param(lambda: _xpath_ids(_collide(_ID64)), lambda: _xpath_ids(_spread(_ID64)), id="xpath-id-tokens"),
        pytest.param(lambda: _js(_collide(_JS_NAMES)), lambda: _js(_spread(_JS_NAMES)), id="js-mangler-names"),
        pytest.param(
            lambda: _tokenize(_collide(_ATTR_NAMES)), lambda: _tokenize(_spread(_ATTR_NAMES)), id="attribute-names"
        ),
    ],
)
def test_keys_chosen_to_share_a_slot_cost_linear_time(
    collide: Callable[[], float], spread: Callable[[], float]
) -> None:
    assert collide() < 10.0 * max(spread(), 0.01)
