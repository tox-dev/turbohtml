from __future__ import annotations

import pytest

from turbohtml._html import conformance_filter
from turbohtml.conformance import ConformanceMessage


def test_filter_rejects_a_non_string_severity() -> None:
    message = ConformanceMessage("img-missing-alt", "error", "m", 1, 0)
    with pytest.raises(TypeError):
        conformance_filter((message,), 1)  # ty: ignore[invalid-argument-type]  # exercises the C argument guard
