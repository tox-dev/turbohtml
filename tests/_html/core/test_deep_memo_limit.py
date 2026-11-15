from __future__ import annotations

from typing import Final

import pytest

from turbohtml._html import _deep_memo_limit_probe

_FIXED_CAP: Final[int] = 1 << 21
_PAGE_BYTES: Final[int] = 512
_PAGE_NODES: Final[int] = 1 << 11


@pytest.mark.parametrize(
    ("rules", "nodes", "expected"),
    [
        pytest.param(0, 401, _FIXED_CAP, id="no-rules"),
        pytest.param(1, 1, _FIXED_CAP, id="small-stays-at-the-fixed-cap"),
        pytest.param(3000, 401, 3000 * 4 * _PAGE_BYTES, id="three-thousand-rules-over-a-deep-spine"),
        pytest.param(
            1 << 20,
            (1 << 29) + 1,
            (1 << 20) * 4 * ((1 << 29) // _PAGE_NODES + 1) * _PAGE_BYTES,
            id="pages-grow-with-the-nodes",
        ),
        pytest.param(1 << 40, 1 << 10, (1 << 40) * 4 * _PAGE_BYTES, id="largest-cap-that-fits"),
    ],
)
def test_deep_memo_limit_is_the_store_bytes_its_rules_and_nodes_need(rules: int, nodes: int, expected: int) -> None:
    assert _deep_memo_limit_probe(rules, nodes) == expected


def test_deep_memo_limit_keeps_the_fixed_cap_when_the_product_overflows() -> None:
    # the bytes for 4 keys per rule and one page per 2048 nodes pass SIZE_MAX, so the cap stays at the fixed size
    assert _deep_memo_limit_probe(1 << 40, 1 << 40) == _FIXED_CAP


def test_deep_memo_limit_rejects_a_non_integer_argument() -> None:
    with pytest.raises(TypeError):
        _deep_memo_limit_probe("not", "an int")  # ty: ignore[invalid-argument-type]  # rejected at runtime
