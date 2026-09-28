from __future__ import annotations

import gc
import re
import sys
import sysconfig
from contextlib import nullcontext
from typing import Final

import pytest

from turbohtml import Element, parse_xml
from turbohtml.transform import Transform


@pytest.mark.skipif(
    sys.implementation.name != "cpython" or bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    reason="CPython GIL allocation-triggered collection",
)
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
    destination: Final = Element("destination")
    adopted = False

    def adopt(phase: str, _info: dict[str, int]) -> None:
        nonlocal adopted
        if phase == "start" and not adopted:
            frame = sys._getframe()
            while frame is not None:
                if frame.f_code in {re.compile.__code__, re.sub.__code__}:
                    adopted = True
                    destination.append(source)
                    break
                frame = frame.f_back

    previous: Final = gc.get_threshold()
    result: str | None = None
    re.purge()
    try:
        gc.callbacks.append(adopt)
        gc.set_threshold(1)
        with pytest.raises(re.error) if expected is None else nullcontext():
            result = transform(source)
    finally:
        gc.callbacks.remove(adopt)
        gc.set_threshold(*previous)
    assert (result, adopted, source.parent) == (None if expected is None else f"{expected}0", True, destination)
