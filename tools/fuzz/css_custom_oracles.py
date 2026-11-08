"""Bounded custom-property productions preserve opaque ASCII payloads."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from turbohtml.clean import minify_css, minify_css_inline

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def css_custom_generate(rng: random.Random) -> str:
    # Finite leaves bound depth and declaration count.
    return f"{rng.choice(_MODES)}:{rng.choice(_KINDS)}:{rng.randint(1, 4)}:{rng.randrange(8)}"


def css_custom_seeds() -> list[str]:
    # Sweep each root, payload production, count and offset.
    return [
        f"{mode}:{kind}:{count}:{offset}"
        for mode in _MODES
        for kind in _KINDS
        for count in range(1, 5)
        for offset in range(8)
    ]


def css_custom_controls() -> dict[str, bool]:
    # Require formatting, case identity and quoted-content conservation.
    return {
        "identity minifier": css_custom_check("sheet:ident:2:0", lambda text: text) is not None,
        "folded name": css_custom_check("sheet:ident:2:0", lambda _text: "a{--foo:MiXeD;--foo:UPPER}") is not None,
        "folded payload": css_custom_check("inline:ident:1:0", lambda _text: "--Foo:mixed") is not None,
        "lost string content": css_custom_check("inline:string:1:0", lambda _text: '--Foo:"AB"') is not None,
    }


def css_custom_check(case: str, minify: Callable[[str], str] | None = None) -> str | None:
    # Check literal custom tokens without sharing the minifier's parser.
    if (match := _CASE.fullmatch(case)) is None:
        raise UnsupportedCssCustomCaseError(case)
    mode, kind, count, offset = match.groups()
    names = ("--Foo", "--foo", "--Bar", "--bar")
    values = {
        "ident": ("MiXeD", "UPPER", "Lower", "Token", "Alpha", "Beta", "Gamma", "Delta"),
        "string": ("A B", "x;y", "MiXeD", "lower", "A:B", "A{B}", "A[B]", "A(B)"),
    }
    declarations: Final = [
        (names[(int(offset) + index) % 4], values[kind][(int(offset) + index) % 8]) for index in range(int(count))
    ]
    source: Final = " ; ".join(f"{name} : {repr(value) if kind == 'string' else value}" for name, value in declarations)
    text: Final = f"a {{ {source} ; }}" if mode == "sheet" else f" {source} ; "
    printer: Final = minify or (minify_css if mode == "sheet" else minify_css_inline)
    output: Final = printer(text)
    expected: Final = ";".join(
        re.escape(name)
        + ":"
        + (f"(?:'{re.escape(value)}'|\"{re.escape(value)}\")" if kind == "string" else re.escape(value))
        for name, value in declarations
    )
    pattern: Final = f"a\\{{{expected}\\}}" if mode == "sheet" else expected
    if re.fullmatch(pattern, output) is None:
        return "custom-property tokens differ from literals"
    return None if printer(output) == output else "custom-property output changes on repeat"


class UnsupportedCssCustomCaseError(ValueError):
    # Separate unsupported programs from failures for valid generated CSS.
    ...


_CASE: Final[re.Pattern[str]] = re.compile(
    r"""
    (?P<mode>sheet|inline):        # the stylesheet is a sheet or inline
    (?P<kind>ident|string):        # the kind of value written
    (?P<count>[1-4]):              # a digit from 1 to 4
    (?P<offset>[0-7])              # a digit from 0 to 7
    """,
    re.VERBOSE,
)
_MODES: Final[tuple[str, ...]] = ("sheet", "inline")
_KINDS: Final[tuple[str, ...]] = ("ident", "string")

__all__ = [
    "UnsupportedCssCustomCaseError",
    "css_custom_check",
    "css_custom_controls",
    "css_custom_generate",
    "css_custom_seeds",
]
