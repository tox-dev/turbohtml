from __future__ import annotations

import re
import sys
from contextlib import nullcontext
from typing import Final

import pytest

from turbohtml import Element, parse_xml
from turbohtml.transform import Transform


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("re:test('a', 'a')", "true", id="test"),
        pytest.param("re:replace('a', 'a', '', 'b')", "b", id="replace"),
        pytest.param("re:test('a', '(')", None, id="invalid-pattern"),
    ],
)
def test_transform_retains_source_during_regex(expression: str, expected: str | None) -> None:
    transform: Final = Transform(
        parse_xml(
            '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
            '<xsl:output method="text"/>'
            f'<xsl:template match="/"><xsl:value-of select="{expression}"/>'
            '<xsl:value-of select="count(/*)"/></xsl:template></xsl:stylesheet>'
        )
    )
    source: Final = parse_xml("<root><child>payload</child></root>").find("root")
    assert source is not None
    target: Final = Element("destination")
    codes: Final = {re.compile.__code__, re.sub.__code__}
    previous: Final = sys.getprofile()
    result: str | None = None
    re.purge()
    try:
        sys.setprofile(lambda frame, event, _: event == "call" and frame.f_code in codes and target.append(source))
        with pytest.raises(re.error) if expected is None else nullcontext():
            result = transform(source)
    finally:
        sys.setprofile(previous)
    assert (result, source.parent) == (None if expected is None else f"{expected}0", target)
