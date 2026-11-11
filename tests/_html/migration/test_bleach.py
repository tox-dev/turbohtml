from __future__ import annotations

import pytest

from turbohtml._html import bleach_attributes as _bleach_attributes


def test_the_entry_takes_two_arguments() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes(["href"])  # ty: ignore[missing-argument]  # tests the C argument guard


def test_the_shape_test_must_be_a_type() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes({"a": ["href"]}, 5)  # ty: ignore[invalid-argument-type]  # tests the C argument guard
