"""The shared buffer-growth helper (``core/vec.h``) hardened against size-overflow.

Every growable buffer in the C core doubles its capacity through ``th_grow_cap``, which bounds both
the doubling step and the ``capacity * element_size`` byte size before either can wrap ``size_t`` and
underallocate (the libxml2 CVE-2022-29824 class). The overflow arms cannot be reached through a real
append -- they need a length no allocation could hold -- so the ``_grow_probe`` hook drives the
helper directly to prove they fail cleanly instead of returning a short buffer.
"""

from __future__ import annotations

import pytest

from turbohtml import _html

_SIZE_MAX = (1 << 64) - 1


@pytest.mark.parametrize(
    ("needed", "current", "initial", "elem_size", "expected"),
    [
        pytest.param(1, 0, 256, 4, (1, 256, 1024), id="first-grow-uses-initial"),
        pytest.param(100, 0, 256, 4, (1, 256, 1024), id="initial-already-covers"),
        pytest.param(300, 256, 256, 4, (1, 512, 2048), id="double-existing-capacity"),
        pytest.param(1000, 256, 256, 4, (1, 1024, 4096), id="double-until-covered"),
        pytest.param(8, 8, 8, 4, (1, 8, 32), id="exact-fit-no-growth"),
    ],
)
def test_grow_probe_reports_capacity_and_byte_size(
    needed: int, current: int, initial: int, elem_size: int, expected: tuple[int, int, int]
) -> None:
    assert _html._grow_probe(needed, current, initial, elem_size) == expected


def test_grow_probe_flags_doubling_overflow() -> None:
    # a length reachable only by doubling past SIZE_MAX/2 fails before the capacity wraps
    assert _html._grow_probe(_SIZE_MAX, 1, 1, 8) == (0, 0, 0)


def test_grow_probe_flags_byte_size_overflow() -> None:
    # a capacity that fits but whose capacity * element_size wraps fails before the multiply
    assert _html._grow_probe(_SIZE_MAX // 4, _SIZE_MAX // 4, 1, 16) == (0, 0, 0)


def test_grow_probe_rejects_a_non_integer_argument() -> None:
    with pytest.raises(TypeError):
        _html._grow_probe("not", "an", "int", "tuple")  # ty: ignore[invalid-argument-type]  # rejected at runtime
