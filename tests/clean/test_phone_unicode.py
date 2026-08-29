from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import LinkDetector, PhoneNumbers

_US: Final = PhoneNumbers(regions=("US",))


def _urls(text: str, phones: PhoneNumbers = _US) -> list[str]:
    return [span.url for span in LinkDetector(phones=phones).find(text)]


def _in_script(zero: int, text: str = "650-253-0000") -> str:
    return "".join(chr(zero + int(char)) if char.isdigit() else char for char in text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_in_script(0x0660), id="arabic-indic"),
        pytest.param(_in_script(0x0966), id="devanagari"),
        pytest.param(_in_script(0xFF10), id="fullwidth"),
        pytest.param(_in_script(0x1D7CE), id="mathematical-bold-astral"),
        pytest.param("650-" + _in_script(0x0660, "253") + "-" + _in_script(0xFF10, "0000"), id="mixed-scripts"),
        pytest.param(_in_script(0x11F50), id="kawi-added-in-unicode-15"),
    ],
)
def test_digits_of_every_script(text: str) -> None:
    assert _urls(text) == ["tel:+16502530000"]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("650-253-0000 ext \u0661\u0662", id="arabic-indic-extension"),
        pytest.param("650-253-0000 x\uff11\uff12", id="fullwidth-extension"),
    ],
)
def test_extension_digits_of_other_scripts(text: str) -> None:
    assert _urls(text) == ["tel:+16502530000;ext=12"]


@pytest.mark.parametrize(
    ("text", "valid", "possible"),
    [
        pytest.param("a650-253-0000", [], ["tel:+16502530000"], id="latin-letter-before"),
        pytest.param("650-253-0000a", [], ["tel:+16502530000"], id="latin-letter-after"),
        pytest.param("\u00c9650-253-0000", [], ["tel:+16502530000"], id="accented-letter-before"),
        pytest.param("650-253-0000\u0301", [], ["tel:+16502530000"], id="combining-accent-after"),
        pytest.param("$650-253-0000", [], ["tel:+16502530000"], id="dollar-before"),
        pytest.param("650-253-0000\u00a3", [], ["tel:+16502530000"], id="pound-after"),
        pytest.param("650-253-0000%", [], ["tel:+16502530000"], id="percent-after"),
        pytest.param("\u00a5650-253-0000", [], ["tel:+16502530000"], id="yen-before"),
        pytest.param("a+1 650-253-0000", ["tel:+16502530000"], ["tel:+16502530000"], id="lead-plus-after-letter"),
        pytest.param("a(650) 253-0000", ["tel:+16502530000"], ["tel:+16502530000"], id="lead-bracket-after-letter"),
        pytest.param(
            "\u6211\u7684\u7535\u8bdd650-253-0000\u3002",
            ["tel:+16502530000"],
            ["tel:+16502530000"],
            id="chinese-context",
        ),
        pytest.param("\u03b1650-253-0000", ["tel:+16502530000"], ["tel:+16502530000"], id="greek-letter-before"),
    ],
)
def test_neighboring_letters_and_currency(text: str, valid: list[str], possible: list[str]) -> None:
    assert _urls(text) == valid
    assert _urls(text, PhoneNumbers(regions=("US",), require_valid=False)) == possible


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("650\u00a0253\u00a00000", id="nbsp"),
        pytest.param("650\u3000253\u30000000", id="ideographic-space"),
        pytest.param("650\u2010253\u20100000", id="hyphen-u2010"),
        pytest.param("650\u2212253\u22120000", id="minus-sign"),
        pytest.param("650\uff0d253\uff0d0000", id="fullwidth-hyphen"),
        pytest.param("650\uff0e253\uff0e0000", id="fullwidth-full-stop"),
        pytest.param("\uff08650\uff09 253-0000", id="fullwidth-brackets"),
        pytest.param("650\u30fc253\u30fc0000", id="katakana-prolonged-sound-mark"),
        pytest.param("650\u200b253\u200b0000", id="zero-width-space"),
    ],
)
def test_separators_of_every_kind(text: str) -> None:
    assert _urls(text) == ["tel:+16502530000"]


def test_offsets_count_code_points_not_bytes() -> None:
    span = LinkDetector(phones=_US).find("\U0001f600\U0001f600 650-253-0000")[0]
    assert (span.start, span.end) == (3, 15)
