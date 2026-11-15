from __future__ import annotations

from typing import Final

import pytest

from turbohtml._html import _deep_memo_limit_probe

_FIXED_CAP: Final[int] = 1 << 21


@pytest.mark.parametrize(
    ("rules", "nodes", "expected"),
    [
        pytest.param(0, 401, _FIXED_CAP, id="no-rules"),
        pytest.param(1, 1, _FIXED_CAP, id="small-stays-at-the-fixed-cap"),
        pytest.param(1, 1 << 19, _FIXED_CAP, id="four-slots-exactly-the-fixed-cap"),
        pytest.param(1, (1 << 19) + 1, 1 << 22, id="one-node-past-the-fixed-cap-doubles"),
        pytest.param(3000, 401, 1 << 23, id="three-thousand-rules-over-a-deep-spine"),
        pytest.param(1 << 32, 1 << 29, 1 << 63, id="largest-cap-that-fits"),
    ],
)
def test_deep_memo_limit_is_the_smallest_power_of_two_holding_four_slots_per_rule_and_node(
    rules: int, nodes: int, expected: int
) -> None:
    assert _deep_memo_limit_probe(rules, nodes) == expected


def test_deep_memo_limit_keeps_the_fixed_cap_when_the_product_overflows() -> None:
    # 4 * rules * nodes passes SIZE_MAX, so the cap stays at the fixed size and the table fails as before
    assert _deep_memo_limit_probe(1 << 40, 1 << 40) == _FIXED_CAP


def test_deep_memo_limit_keeps_the_fixed_cap_when_doubling_overflows() -> None:
    # the product fits, but holding it doubles the cap past SIZE_MAX / 2
    assert _deep_memo_limit_probe((1 << 30) + 1, 1 << 31) == _FIXED_CAP


def test_deep_memo_limit_rejects_a_non_integer_argument() -> None:
    with pytest.raises(TypeError):
        _deep_memo_limit_probe("not", "an int")  # ty: ignore[invalid-argument-type]  # rejected at runtime
