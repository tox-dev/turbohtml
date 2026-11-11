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
