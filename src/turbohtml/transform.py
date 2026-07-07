"""
turbohtml.transform: XSLT 1.0 transformation, the job ``lxml.etree.XSLT`` does.

An XSLT stylesheet is itself an XML document, so it is parsed with :func:`turbohtml.parse_xml`; the source document is
any parsed tree. :class:`Transform` holds a parsed stylesheet and applies it to a source, mirroring lxml's compile-once,
apply-many shape::

    from turbohtml import parse_xml
    from turbohtml.transform import Transform

    style = parse_xml(stylesheet_source)
    convert = Transform(style)
    result = convert(parse_xml(document_source))

The whole transform runs in the C extension, reusing turbohtml's XPath 1.0 engine for every match pattern and select
expression. It covers the XSLT 1.0 instruction set -- ``xsl:template`` (match, name, mode, priority),
``xsl:apply-templates``, ``xsl:call-template``, ``xsl:for-each``, ``xsl:if``, ``xsl:choose``, ``xsl:value-of``,
``xsl:copy``/``xsl:copy-of``,
``xsl:element``/``xsl:attribute``/``xsl:text``, ``xsl:variable``/``xsl:param``, ``xsl:sort``, ``xsl:number``,
``xsl:key`` and the ``key()`` function, the built-in template rules, the section 5.5 conflict resolution,
``exclude-result-prefixes`` with the section 7.1.1 namespace-node copying, and the ``xml``/``html``/``text`` output
methods. External-document loading (``xsl:include``, ``xsl:import``, ``document()``) is not modeled.

Validated against libxslt's XSLT 1.0 Recommendation test corpus (the ``REC`` and ``REC2`` example triples it ships):
56 of the 79 cases pass exactly, and the remainder exercise features turbohtml does not model (whitespace stripping,
namespace-alias, attribute sets, multi-level numbering, cdata-section-elements, extension elements, and the html
output method's meta injection); see ``tests/conformance/test_xslt_conformance.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._html import _xslt_transform

if TYPE_CHECKING:
    from ._html import Node

__all__ = ["Transform", "transform"]


class Transform:
    """
    A compiled XSLT 1.0 stylesheet, callable over source documents (lxml's ``etree.XSLT``).

    :param stylesheet: the stylesheet, a tree parsed with :func:`turbohtml.parse_xml`.
    """

    __slots__ = ("_stylesheet",)

    def __init__(self, stylesheet: Node) -> None:
        """Hold the parsed stylesheet the transform applies."""
        self._stylesheet = stylesheet

    def __call__(self, source: Node, /, **params: str) -> str:
        """
        Transform a source document and return the serialized result.

        :param source: the document to transform, a parsed tree.
        :param params: top-level ``xsl:param`` values, each an XPath expression string (quote a string literal, as
            lxml does: ``convert(doc, title="'Report'")``).
        :raises ValueError: if the stylesheet or an expression is malformed, or a referenced key or named template is
            undeclared.
        :raises RuntimeError: on an ``xsl:message`` with ``terminate="yes"``.
        :returns: the transformed document serialized under the stylesheet's ``xsl:output`` method.
        """
        return _xslt_transform(self._stylesheet, source, params or None)


def transform(stylesheet: Node, source: Node, /, **params: str) -> str:
    """
    Apply an XSLT 1.0 stylesheet to a source document in one call.

    Equivalent to ``Transform(stylesheet)(source, **params)``; use :class:`Transform` to apply one stylesheet to many
    documents without re-reading it each time.

    :param stylesheet: the stylesheet, a tree parsed with :func:`turbohtml.parse_xml`.
    :param source: the document to transform, a parsed tree.
    :param params: top-level ``xsl:param`` values, each an XPath expression string.
    :returns: the transformed document serialized under the stylesheet's ``xsl:output`` method.
    """
    return _xslt_transform(stylesheet, source, params or None)
