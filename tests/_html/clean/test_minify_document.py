from __future__ import annotations

import pytest

from turbohtml import Html, Indent, Minify
from turbohtml._html import _minify_document


@pytest.mark.parametrize(
    ("layout", "regions", "message"),
    [
        pytest.param(Indent(), (), "layout must be a Minify", id="indent-layout"),
        pytest.param(Html(), (), "layout must be an Indent, a Minify, or None", id="not-a-layout"),
        pytest.param(Minify(), [(0, 1)], "must be tuple, not list", id="regions-list"),
        pytest.param(Minify(), ((0,),), "exactly 2 arguments", id="short-region"),
        pytest.param(Minify(), (("a", 1),), "cannot be interpreted as an integer", id="non-integer-region"),
    ],
)
def test_minify_document_rejects(
    layout: Html | Indent | Minify, regions: tuple[tuple[int | str, ...], ...] | list[tuple[int, int]], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        _minify_document("<p>x", layout, regions)  # ty: ignore[invalid-argument-type]  # malformed regions on purpose
