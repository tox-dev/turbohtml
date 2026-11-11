from __future__ import annotations

import pytest

from turbohtml._html import minify_js as _minify_js


def test_low_level_binding_requires_all_arguments() -> None:
    with pytest.raises(TypeError):
        _minify_js("x")  # ty: ignore[missing-argument]  # tests the C argument guard
