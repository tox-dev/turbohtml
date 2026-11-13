"""Fixed byte fixtures expose shared decoding mistakes that parser agreement misses."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from turbohtml import IncrementalParser, parse

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def parser_bytes_generate(rng: random.Random) -> str:
    # Keep generated prefixes inside the fixed byte fixtures' encoding domain.
    return f"{rng.choice(tuple(_FIXTURES))}:g{rng.randrange(10000)}"


def parser_bytes_seeds() -> list[str]:
    # Cover each codec and BOM override before random generation.
    return [f"{variant}:g{index}" for variant in _FIXTURES for index in range(30)]


def parser_bytes_controls() -> dict[str, bool]:
    # Detect wrong labels and shared wrong text before trusting chunk agreement.
    return {
        "wrong one-shot text": parser_bytes_check("utf8:g", lambda _data, _label: ("", "UTF-8")) is not None,
        "wrong one-shot label": parser_bytes_check("utf8:g", lambda _data, _label: ("g:café 日本😀", None)) is not None,
        "wrong streaming text": parser_bytes_check("utf8:g", stream=lambda _data, _label, _width: ("", "UTF-8"))
        is not None,
        "wrong streaming label": parser_bytes_check(
            "utf8:g", stream=lambda _data, _label, _width: ("g:café 日本😀", None)
        )
        is not None,
        "shared wrong result": parser_bytes_check(
            "utf8:g", lambda _data, _label: ("", None), lambda _data, _label, _width: ("", None)
        )
        is not None,
    }


def parser_bytes_check(
    case: str,
    one_shot: Callable[[bytes, str], tuple[str, str | None]] | None = None,
    stream: Callable[[bytes, str, int], tuple[str, str | None]] | None = None,
) -> str | None:
    # Check literal text and labels because two parsers can agree on a wrong decoding.
    variant, separator, leaf = case.partition(":")
    if not separator or variant not in _FIXTURES or _PARSER_BYTES_CHECK_PATTERN.fullmatch(leaf) is None:
        raise UnsupportedParserCaseError(case)
    label, encoding, prefix, payload, text, framing = _FIXTURES[variant]
    data: Final = prefix + f"<p>{leaf}:".encode(framing) + payload + "</p>".encode(framing)
    expected: Final = (f"{leaf}:{text}", encoding)
    if (one_shot or _parse_bytes)(data, label) != expected:
        return "one-shot parser differs from fixed text or label"
    for width in (1, 2, 3, 4, 5, len(data)):
        if (stream or _stream_bytes)(data, label, width) != expected:
            return "streaming parser differs from fixed text or label"
    return None


class UnsupportedParserCaseError(ValueError):
    # Keep invalid fixture grammar distinct from parser rejection of valid bytes.
    ...


def _parse_bytes(data: bytes, label: str) -> tuple[str, str | None]:
    document: Final = parse(data, encoding=label)
    return document.text, document.encoding


def _stream_bytes(data: bytes, label: str, width: int) -> tuple[str, str | None]:
    parser: Final = IncrementalParser(encoding=label)
    parser.feed(b"")
    for start in range(0, len(data), width):
        parser.feed(data[start : start + width])
        parser.feed(b"")
    document: Final = parser.close()
    return document.text, document.encoding


_PARSER_BYTES_CHECK_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9]{0,15}")


_FIXTURES: Final[MappingProxyType[str, tuple[str, str, bytes, bytes, str, str]]] = MappingProxyType({
    "utf8": ("utf-8", "UTF-8", b"", b"caf\xc3\xa9 \xe6\x97\xa5\xe6\x9c\xac\xf0\x9f\x98\x80", "café 日本😀", "ascii"),
    "shift-jis": ("shift_jis", "Shift_JIS", b"", b"\x93\xfa\x96{\x8c\xea", "日本語", "ascii"),
    "iso-2022-jp": ("iso-2022-jp", "ISO-2022-JP", b"", b"\x1b$BF|K\\8l\x1b(B", "日本語", "ascii"),
    "windows1252": ("windows-1252", "windows-1252", b"", b"caf\xe9 \x80", "café €", "ascii"),
    "utf8-bom": ("windows-1252", "UTF-8", b"\xef\xbb\xbf", b"\xc3\xa9", "é", "ascii"),
    "utf16le-bom": ("windows-1252", "UTF-16LE", b"\xff\xfe", b"\xe9\x00", "é", "utf-16-le"),
    "utf16be-bom": ("utf-8", "UTF-16BE", b"\xfe\xff", b"\x00\xe9", "é", "utf-16-be"),
})


__all__ = [
    "UnsupportedParserCaseError",
    "parser_bytes_check",
    "parser_bytes_controls",
    "parser_bytes_generate",
    "parser_bytes_seeds",
]
