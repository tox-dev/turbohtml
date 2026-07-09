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


# The suite's scripted/ directory declares its charset from inside document.write, so only
# a UA that executes scripts ever sees the <meta> element. turbohtml does not run scripts,
# and neither does html5lib, which returns windows-1252 for those cases too.
def _cases() -> list:
    cases = []
    for name in ("tests1.dat", "tests2.dat", "test-yahoo-jp.dat"):
        path = _SUITE / name
        for index, chunk in enumerate(path.read_bytes().split(b"#data\n")[1:]):
            head, _, tail = chunk.partition(b"#encoding\n")
            data = head[:-1] if head.endswith(b"\n") else head
            encoding = tail.split(b"\n", 1)[0].decode("ascii").strip()
            cases.append(pytest.param(data, encoding, id=f"{name}-{index}"))
    return cases


@pytest.mark.parametrize(("data", "expected"), _cases())
def test_encoding_sniffing(data: bytes, expected: str) -> None:
    encoding = parse(data).encoding
    assert encoding is not None
    assert encoding.lower() == expected.lower()
