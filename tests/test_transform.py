from __future__ import annotations

import re
import sys
from contextlib import nullcontext
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Document, DocumentFragment, Element, parse_xml
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from types import CodeType


@pytest.mark.parametrize(
    ("expression", "expected", "outcome"),
    [
        pytest.param("re:test('a', 'a')", "true1", nullcontext, id="test"),
        pytest.param("re:replace('a', 'a', '', 'b')", "b1", nullcontext, id="replace"),
        pytest.param("re:test('a', '(')", None, lambda: pytest.raises(re.error), id="invalid-pattern"),
    ],
)
def test_transform_regex_runs_no_python_code(
    expression: str,
    expected: str | None,
    outcome: Callable[[], AbstractContextManager[None] | AbstractContextManager[pytest.ExceptionInfo[re.error]]],
) -> None:
    transform: Final[Transform] = Transform(
        parse_xml(
            '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
            '<xsl:output method="text"/>'
            f'<xsl:template match="/"><xsl:value-of select="{expression}"/>'
            '<xsl:value-of select="count(/*)"/></xsl:template></xsl:stylesheet>'
        )
    )
    source: Final[Element | None] = parse_xml("<root><child>payload</child></root>").find("root")
    assert source is not None
    parent: Final[Element | Document | DocumentFragment | None] = source.parent
    target: Final[Element] = Element("destination")
    codes: Final[set[CodeType]] = {re.compile.__code__, re.sub.__code__}
    previous = sys.getprofile()
    result: str | None = None
    re.purge()
    try:
        sys.setprofile(lambda frame, event, _: event == "call" and frame.f_code in codes and target.append(source))
        with outcome():
            result = transform(source)
    finally:
        sys.setprofile(previous)
    # the regex engine is native, so the hook that would move the source never fires
    assert (result, source.parent) == (expected, parent)
