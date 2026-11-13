from __future__ import annotations

import pytest

from turbohtml import parse_xml
from turbohtml._html import xslt_transform


def test_transform_params_must_be_a_dict() -> None:
    sheet = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/">x</xsl:template></xsl:stylesheet>'
    )
    with pytest.raises(TypeError, match="dict or None"):
        xslt_transform(sheet, parse_xml("<r/>"), ["not", "a", "dict"])  # ty: ignore[invalid-argument-type]  # wrong type on purpose


def test_transform_missing_arguments_raise() -> None:
    with pytest.raises(TypeError):
        xslt_transform()  # ty: ignore[missing-argument]  # too few args on purpose
