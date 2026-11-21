from __future__ import annotations

import pickle  # ruff:ignore[suspicious-pickle-import]  # fixed payloads name released turbohtml objects
from typing import TYPE_CHECKING

import pytest

from turbohtml import SelectorSyntaxError, SourceLocation, SourceSpan, XPathString
from turbohtml.extract import Extraction, Paragraph, boilerplate, feed, microdata, opengraph
from turbohtml.query import Query

if TYPE_CHECKING:
    from types import FunctionType


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(b"cturbohtml._internal._locations\nSourceLocation\n.", SourceLocation, id="source-location"),
        pytest.param(b"cturbohtml._internal._locations\nSourceSpan\n.", SourceSpan, id="source-span"),
        pytest.param(
            b"cturbohtml._internal._selectors\nSelectorSyntaxError\n.",
            SelectorSyntaxError,
            id="selector-error",
        ),
        pytest.param(b"cturbohtml.query._xpath\nXPathString\n.", XPathString, id="xpath-string"),
        pytest.param(b"cturbohtml.query\nQuery\n.", Query, id="query"),
        pytest.param(b"cturbohtml.extract\nExtraction\n.", Extraction, id="extraction"),
        pytest.param(b"cturbohtml.extract\nParagraph\n.", Paragraph, id="paragraph"),
        pytest.param(b"cturbohtml.extract\nboilerplate\n.", boilerplate, id="boilerplate"),
        pytest.param(b"cturbohtml.extract\nfeed\n.", feed, id="feed"),
        pytest.param(b"cturbohtml.extract\nmicrodata\n.", microdata, id="microdata"),
        pytest.param(b"cturbohtml.extract\nopengraph\n.", opengraph, id="opengraph"),
    ],
)
def test_released_pickle_paths(payload: bytes, expected: type | FunctionType) -> None:
    assert pickle.loads(payload) is expected  # ruff:ignore[suspicious-pickle-usage]  # fixed released-path payloads


def test_released_nested_node_pickle_loads() -> None:
    # 1.16.0 pickled each child as its own nested _reconstruct call
    payload = (
        b"\x80\x04\x95\x81\x00\x00\x00\x00\x00\x00\x00\x8c\x0fturbohtml._html\x94\x8c\x0c_reconstruct\x94\x93\x94(K"
        b"\x01\x8c\x01p\x94}\x94\x8c\x05class\x94]\x94\x8c\x01a\x94as\x86\x94]\x94(h\x02(K\x02\x8c\x01x\x94]\x94K"
        b"\x00t\x94R\x94h\x02(K\x01\x8c\x01b\x94}\x94\x86\x94]\x94h\x02(K\x02\x8c\x01y\x94]\x94K\x00t\x94R\x94aK"
        b"\x00t\x94R\x94eK\x00t\x94R\x94."
    )
    node = pickle.loads(payload)  # ruff:ignore[suspicious-pickle-usage]  # fixed released payload
    assert node.html == '<p class="a">x<b>y</b></p>'
