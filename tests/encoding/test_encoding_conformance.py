"""WHATWG encoding sniffing against the html5lib-tests encoding suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from turbohtml import parse

_SUITE = Path(__file__).parents[1] / "html5lib-tests" / "encoding"

# CI always checks out the submodule (actions/checkout submodules: true); this guard fires only locally
if not _SUITE.is_dir() or not any(_SUITE.glob("*.dat")):  # pragma: no cover
    msg = "submodule tests/html5lib-tests not checked out; run: git submodule update --init tests/html5lib-tests"
    raise RuntimeError(msg)

# A few tests1.dat cases place the only <meta charset> well past byte 1024 (the
# "N characters" boundary fixtures and the multi-script test, meta at 2026..8323)
# and expect it honored. The WHATWG "prescan a byte stream to determine its
# encoding" algorithm examines only the first 1024 bytes, so a meta beyond that is
# ignored and the document falls back to windows-1252. The spec is authoritative
# over the pinned .dat (see https://github.com/tox-dev/turbohtml/issues/60), so we
# assert the spec-correct result. Keyed by (filename, case index) over the pinned
# submodule.
_SPEC_OVERRIDES: dict[tuple[str, int], str] = {
    ("tests1.dat", index): "windows-1252" for index in (47, 48, 49, 50, 51, 52, 53)
}


def _cases() -> list:
    cases = []
    for name in ("tests1.dat", "tests2.dat"):
        path = _SUITE / name
        for index, chunk in enumerate(path.read_bytes().split(b"#data\n")[1:]):
            head, _, tail = chunk.partition(b"#encoding\n")
            data = head[:-1] if head.endswith(b"\n") else head
            encoding = tail.split(b"\n", 1)[0].decode("ascii").strip()
            encoding = _SPEC_OVERRIDES.get((name, index), encoding)
            cases.append(pytest.param(data, encoding, id=f"{name}-{index}"))
    return cases


@pytest.mark.parametrize(("data", "expected"), _cases())
def test_encoding_sniffing(data: bytes, expected: str) -> None:
    encoding = parse(data).encoding
    assert encoding is not None
    assert encoding.lower() == expected.lower()
