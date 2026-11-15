from __future__ import annotations

import pytest

from turbohtml import Html, Indent, Minify
from turbohtml._html import _minify_document


@pytest.mark.parametrize(
    ("html", "layout", "message"),
    [
        pytest.param("<p>x", Indent(), "layout must be a Minify", id="indent-layout"),
        pytest.param("<p>x", Html(), "layout must be an Indent, a Minify, or None", id="not-a-layout"),
        pytest.param(b"<p>x", Minify(), "must be str", id="bytes-html"),
    ],
)
def test_minify_document_rejects(html: str | bytes, layout: Html | Indent | Minify, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        _minify_document(html, layout)  # ty: ignore[invalid-argument-type]  # wrong types on purpose
