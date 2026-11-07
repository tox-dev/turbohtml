"""Canonical-equivalent labels need an independent target because stable wrong hosts satisfy idempotence."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Final

from turbohtml.extract import normalize_url

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def idna_nfc_generate(rng: random.Random) -> str:
    # Limit labels to established Unicode-16 mappings without unrelated IDNA policies.
    return f"{rng.choice(tuple(_LABELS))}:g{rng.randrange(10000)}"


def idna_nfc_seeds() -> list[str]:
    # Exercise composition and ordering families before random generation.
    return [f"{variant}:g{index}" for variant in _LABELS for index in range(20)]


def idna_nfc_controls() -> dict[str, bool]:
    # Reject stable wrong results as well as unstable canonical output.
    return {
        "unchanged Unicode": idna_nfc_check("reorder:", lambda text: text) is not None,
        "stable wrong target": idna_nfc_check("reorder:", lambda _text: "https://wrong.example/") is not None,
        "changed repeat": idna_nfc_check("reorder:", lambda text: f"{text}x" if text.isascii() else normalize_url(text))
        is not None,
    }


def idna_nfc_check(case: str, normalize: Callable[[str], str] = normalize_url) -> str | None:
    # Pin equivalent spellings to an independent target instead of trusting their agreement.
    variant, separator, affix = case.partition(":")
    if not separator or variant not in _LABELS or re.fullmatch(r"[a-z0-9]{0,8}", affix) is None:
        raise UnsupportedIdnaNfcCaseError(case)
    source: Final = affix + _LABELS[variant]
    composed: Final = unicodedata.normalize("NFC", source)
    expected: Final = f"https://xn--{composed.encode('punycode').decode('ascii')}.example/"
    for label in (source, composed, unicodedata.normalize("NFD", source)):
        try:
            output = normalize(f"https://{label}.example/")
        except ValueError:
            return "rejects known-valid canonical host"
        if output != expected:
            return "canonical host differs from independent target"
    try:
        repeated: Final = normalize(expected)
    except ValueError:
        return "rejects canonical host output"
    return None if repeated == expected else "canonical host changes on repeat"


class UnsupportedIdnaNfcCaseError(ValueError):
    # Keep unsupported fixture grammar distinct from rejection of valid hosts.
    ...


_LABELS: Final[dict[str, str]] = {
    "reorder": "q\u0301\u0323",
    "blocked": "a\u0305\u0301",
    "successive-compose": "a\u030a\u0301",
    "noncomposing-tail": "s\u0323\u0301",
    "lower-class-intervening": "a\u0316\u0301",
    "hangul-lv": "\u1100\u1161",
    "hangul-lvt": "\u1100\u1161\u11a8",
    "hangul-lv-plus-t": "\uac00\u11a8",
}

__all__ = [
    "UnsupportedIdnaNfcCaseError",
    "idna_nfc_check",
    "idna_nfc_controls",
    "idna_nfc_generate",
    "idna_nfc_seeds",
]
