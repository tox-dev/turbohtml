from __future__ import annotations

import pytest

from turbohtml._html import decode


def test_unknown_label_raises_lookup_error() -> None:
    with pytest.raises(LookupError, match="unknown encoding: no-such-encoding"):
        decode(b"", "no-such-encoding")


def test_decode_rejects_a_non_bytes_first_argument() -> None:
    with pytest.raises(TypeError):
        decode("not bytes", "gbk")  # ty: ignore[invalid-argument-type]  # exercises the C argument guard
