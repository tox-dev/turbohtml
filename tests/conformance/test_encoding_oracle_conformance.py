"""
turbohtml's WHATWG decoders and encoding detector, differentially validated against the Rust originals.

``encoding_rs`` is the Encoding Standard implementation Firefox ships, and ``chardetng`` is the detector turbohtml's
``_c/encoding/detect.h`` is a C port of. Both are vendored as pinned, shallow submodules
(``tests/conformance/encoding_rs``, ``tests/conformance/chardetng``) and driven through the small Rust binaries in
``tests/conformance/oracles``. The chardetng submodule is pinned to the same revision ``detect_data.h``'s banner names,
so a regenerated detector table that came from a different revision fails here.

Reading a spec and reading a codec's tables both go wrong quietly, and this branch's history is the argument: CPython's
``koi8_u`` is not the spec's, and a hand-written expectation is only as good as whoever wrote it. Differential testing
against the implementation the web actually renders with does not have that failure mode.

The cases are generated rather than stored: every one- and two-byte sequence of each legacy encoding, plus seeded
pseudo-random sequences shaped like the four-byte gb18030 and escape-driven ISO-2022-JP forms. The oracle computes the
expected output, so nothing here can drift out of date.

This is a vendored-oracle suite: an absent submodule or a missing Rust toolchain raises rather than skips, since a
conformance suite that silently no-ops is worse than none. Check the oracles out with
``git submodule update --init tests/conformance/encoding_rs tests/conformance/chardetng``.
"""

# ruff: noqa: RUF001  # the sample text is deliberately Greek, Cyrillic and Turkish; that is the point

from __future__ import annotations

import random
import subprocess  # noqa: S404  # the oracles are two vendored Rust binaries this suite builds and drives
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.detect import detect

if TYPE_CHECKING:
    from collections.abc import Iterator

_ORACLES: Final = Path(__file__).parent / "oracles"

# The sweeps below are deterministic; pinning their size keeps a refactor from quietly narrowing the differential.
_DECODE_CASES: Final = 442176
_DETECT_CASES: Final = 39932

# The legacy multi-byte encodings, whose one- and two-byte sequences are swept exhaustively.
_MULTI_BYTE: Final[tuple[str, ...]] = ("big5", "euc-kr", "shift_jis", "euc-jp", "gbk", "gb18030")

# Every legacy single-byte encoding, plus the two the spec defines without an index.
_SINGLE_BYTE: Final[tuple[str, ...]] = (
    "ibm866",
    "iso-8859-2",
    "iso-8859-3",
    "iso-8859-4",
    "iso-8859-5",
    "iso-8859-6",
    "iso-8859-7",
    "iso-8859-8",
    "iso-8859-8-i",
    "iso-8859-10",
    "iso-8859-13",
    "iso-8859-14",
    "iso-8859-15",
    "iso-8859-16",
    "koi8-r",
    "koi8-u",
    "macintosh",
    "windows-874",
    "windows-1250",
    "windows-1251",
    "windows-1252",
    "windows-1253",
    "windows-1254",
    "windows-1255",
    "windows-1256",
    "windows-1257",
    "windows-1258",
    "x-mac-cyrillic",
    "x-user-defined",
)


def _oracle(name: str) -> Path:
    """Build the Rust oracle named *name* and return its binary, raising when the submodule or toolchain is absent."""
    crate = _ORACLES / name
    if not (crate.parent.parent / ("encoding_rs" if name == "decoder" else "chardetng") / "Cargo.toml").exists():
        msg = f"submodule tests/conformance/{'encoding_rs' if name == 'decoder' else 'chardetng'} not checked out"
        raise RuntimeError(msg)
    subprocess.run(["cargo", "build", "--release", "--quiet"], cwd=crate, check=True)  # noqa: S607
    return crate / "target" / "release" / f"{name}-oracle"


def _run(binary: Path, stdin: str, count: int) -> list[str]:
    """Feed *stdin* to *binary* and return its lines, one per case."""
    result = subprocess.run([binary], input=stdin, capture_output=True, text=True, check=True)  # noqa: S603
    lines = result.stdout.splitlines()
    assert len(lines) == count, f"{binary.name} answered {len(lines)} of {count} cases"
    return lines


@pytest.fixture(scope="session")
def decoder_oracle() -> Path:
    return _oracle("decoder")


@pytest.fixture(scope="session")
def detector_oracle() -> Path:
    return _oracle("detector")


def _decode_cases() -> Iterator[tuple[str, bytes]]:
    """Every one- and two-byte sequence of each encoding, plus seeded four-byte and escape-shaped sequences."""
    rng = random.Random(20260709)  # noqa: S311  # a corpus seed, not a security decision
    for label in (*_SINGLE_BYTE, *_MULTI_BYTE):
        for first in range(256):
            yield label, bytes([first])
    for label in _MULTI_BYTE:
        for first in range(256):
            for second in range(256):
                yield label, bytes([first, second])
    for _ in range(20000):
        length = rng.randrange(1, 9)
        yield (
            "gb18030",
            bytes(
                rng.choice([rng.randrange(0x81, 0xFF), rng.randrange(0x30, 0x3A), rng.randrange(256)])
                for _ in range(length)
            ),
        )
    for _ in range(20000):
        length = rng.randrange(1, 10)
        alphabet = [0x1B, 0x24, 0x28, 0x42, 0x4A, 0x49, 0x40]
        yield (
            "iso-2022-jp",
            bytes(rng.choice([*alphabet, rng.randrange(0x21, 0x7F), rng.randrange(256)]) for _ in range(length)),
        )


def test_every_decoder_agrees_with_encoding_rs(decoder_oracle: Path) -> None:
    cases = list(_decode_cases())
    assert len(cases) == _DECODE_CASES, "the sweep shrank; a differential that stops covering a sequence proves nothing"
    stdin = "".join(f"{label}\t{raw.hex()}\n" for label, raw in cases)
    expected = _run(decoder_oracle, stdin, len(cases))
    divergent = [
        (label, raw.hex(), raw.decode(f"whatwg-{label}"), bytes.fromhex(want).decode())
        for (label, raw), want in zip(cases, expected, strict=True)
        if raw.decode(f"whatwg-{label}") != bytes.fromhex(want).decode()
    ]
    assert not divergent, f"{len(divergent)} of {len(cases)} sequences decode differently, first: {divergent[0]}"


def _detect_cases() -> Iterator[bytes]:
    """Real text in each encoding the detector models, plus adversarial byte soup and lead-seeded sequences."""
    samples = {
        "cp1251": "Съешь же ещё этих мягких французских булок",
        "koi8-r": "Широкая электрификация южных губерний",
        "cp1252": "Précédemment, la créativité française",
        "iso-8859-2": "Příliš žluťoučký kůň úpěl ďábelské ódy",
        "iso-8859-7": "Καλημέρα κόσμε, θέλει αρετή και τόλμη",
        "cp1255": "דג סקרן שט בים מאוכזב",
        "cp1256": "نص حكيم له سر قاطع",
        "cp1254": "Pijamalı hasta yağız şoföre çabucak güvendi",
        "cp874": "เป็นมนุษย์สุดประเสริฐเลิศคุณค่า",
        "cp866": "Съешь же ещё этих мягких булок",
        "shift_jis": "吾輩は猫である。名前はまだ無い。",
        "euc_jp": "吾輩は猫である。名前はまだ無い。",
        "cp949": "동해물과 백두산이 마르고 닳도록",
        "big5": "繁體中文字元測試內容",
        "gb18030": "天地玄黄宇宙洪荒日月盈昃",
        "utf-8": "日本語のテキストです",
    }
    for codec, text in samples.items():
        for pad in ("", " ", "\n", " abc", "ZZ "):
            yield (text + pad).encode(codec)
    rng = random.Random(20260709)  # noqa: S311  # a corpus seed, not a security decision
    for _ in range(15000):
        lead = rng.choice([0x81, 0x8E, 0x8F, 0xA1, 0xC9, 0xFE, 0xF0, 0xA0, 0xFD])
        yield bytes([lead, *(rng.randrange(256) for _ in range(rng.randrange(1, 14)))])
    for _ in range(15000):
        yield bytes(rng.randrange(0x80, 0x100) for _ in range(rng.randrange(2, 25)))
    for _ in range(10000):
        yield bytes(rng.randrange(256) for _ in range(rng.randrange(2, 40)))


def test_the_detector_agrees_with_chardetng(detector_oracle: Path) -> None:
    # chardetng does content detection only; a byte-order mark or a pure-ASCII stream is turbohtml's own documented
    # divergence (it honors the mark, and takes the spec's windows-1252 fallback where chardetng answers UTF-8)
    cases = [
        raw
        for raw in _detect_cases()
        if raw
        and any(byte >= 0x80 for byte in raw)
        and raw[:2] not in {b"\xff\xfe", b"\xfe\xff"}
        and raw[:3] != b"\xef\xbb\xbf"
    ]
    assert len(cases) == _DETECT_CASES, "the corpus shrank; a differential that stops covering an input proves nothing"
    expected = _run(detector_oracle, "".join(f"{raw.hex()}\n" for raw in cases), len(cases))
    divergent = [
        (raw.hex(), detect(raw).encoding, want)
        for raw, want in zip(cases, expected, strict=True)
        if (detect(raw).encoding or "").casefold() != want.casefold()
    ]
    assert not divergent, f"{len(divergent)} of {len(cases)} inputs detect differently, first: {divergent[0]}"
