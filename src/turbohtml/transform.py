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
expression. It covers XSLT 1.0: ``xsl:template`` (match, name, mode, priority), ``xsl:apply-templates``,
``xsl:call-template``, ``xsl:for-each``, ``xsl:if``, ``xsl:choose``, ``xsl:value-of``, ``xsl:copy``/``xsl:copy-of``,
``xsl:element``/``xsl:attribute``/``xsl:text``, ``xsl:variable``/``xsl:param``, ``xsl:sort``, multi-level
``xsl:number``, ``xsl:key`` and the ``key()`` function, ``xsl:strip-space``/``xsl:preserve-space``,
``xsl:attribute-set``, ``xsl:namespace-alias``, ``xsl:import`` with import precedence, ``xsl:fallback``, simplified
stylesheets, ``cdata-section-elements`` and the ``xml``/``html``/``text`` output methods (html is auto-selected for a
null-namespace ``html`` document element). The documented boundaries are locale-aware ``xsl:sort`` collation (a locale
layer turbohtml does not carry) and ``id()`` over DTD-declared IDs (no DTD layer).

``base_url`` supplies the stylesheet path or file URL used to resolve each ``xsl:import``. Disable imports for an
untrusted stylesheet, or constrain them to ``import_root``. The libxslt XSLT 1.0 Recommendation test corpus covers the
processor (see ``tests/conformance/test_xslt_conformance.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._html import _xslt_resolve_imports, _xslt_transform

if TYPE_CHECKING:
    from pathlib import Path

    from ._html import Node

__all__ = ["Transform", "transform"]


class Transform:
    """
    A compiled XSLT 1.0 stylesheet, callable over source documents (lxml's ``etree.XSLT``).

    :param stylesheet: the stylesheet, a tree parsed with :func:`turbohtml.parse_xml`.
    :param base_url: the stylesheet's path or file URL, against which ``xsl:import`` hrefs resolve; required only when
        the stylesheet imports.
    :param allow_imports: set to :data:`False` when a stylesheet must not read other files.
    :param import_root: when set, imported files must resolve inside this directory, including through nested imports.
    """

    __slots__ = ("_imports", "_stylesheet")

    def __init__(
        self,
        stylesheet: Node,
        *,
        base_url: str | None = None,
        allow_imports: bool = True,
        import_root: str | Path | None = None,
    ) -> None:
        """Hold the parsed stylesheet and pre-resolve its imported stylesheets once."""
        self._stylesheet = stylesheet
        self._imports = _xslt_resolve_imports(stylesheet, base_url, allow_imports, import_root)

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
        return _xslt_transform(self._stylesheet, source, params or None, self._imports)


def transform(
    stylesheet: Node,
    source: Node,
    /,
    *,
    base_url: str | None = None,
    allow_imports: bool = True,
    import_root: str | Path | None = None,
    **params: str,
) -> str:
    """
    Apply an XSLT 1.0 stylesheet to a source document in one call.

    Equivalent to ``Transform(stylesheet, base_url=base_url)(source, **params)``; use :class:`Transform` to apply one
    stylesheet to many documents without re-reading it each time.

    :param stylesheet: the stylesheet, a tree parsed with :func:`turbohtml.parse_xml`.
    :param source: the document to transform, a parsed tree.
    :param base_url: the stylesheet's path or file URL, against which ``xsl:import`` hrefs resolve; required only when
        the stylesheet imports.
    :param allow_imports: set to :data:`False` when a stylesheet must not read other files.
    :param import_root: when set, imported files must resolve inside this directory, including through nested imports.
    :param params: top-level ``xsl:param`` values, each an XPath expression string.
    :returns: the transformed document serialized under the stylesheet's ``xsl:output`` method.
    """
    imports = _xslt_resolve_imports(stylesheet, base_url, allow_imports, import_root)
    return _xslt_transform(stylesheet, source, params or None, imports)
