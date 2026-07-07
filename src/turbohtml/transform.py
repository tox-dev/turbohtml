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

An ``xsl:import`` is resolved relative to ``base_url`` (the stylesheet's own path or URL, as lxml uses ``etree.parse``'s
base): pass it when the stylesheet imports. Validated against libxslt's XSLT 1.0 Recommendation test corpus (see
``tests/conformance/test_xslt_conformance.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ._html import Element, _xslt_transform, parse_xml

if TYPE_CHECKING:
    from ._html import Node

__all__ = ["Transform", "transform"]

_XSLT_NS = "http://www.w3.org/1999/XSL/Transform"


def _xsl_prefix(root: Element) -> str:
    """Return the prefix bound to the XSLT namespace on a stylesheet root, defaulting to ``xsl``."""
    for name, value in root.attrs.items():
        if name.startswith("xmlns:") and value == _XSLT_NS:
            return name[6:]
    return "xsl"


def _load_imports(root: Element, base: Path) -> list[Node]:
    """
    Resolve the ``xsl:import`` chain under ``root`` into parsed stylesheets, lowest import precedence first.

    Each import's own imports precede it (lower precedence) and, within a stylesheet, a later import outranks an earlier
    one, exactly the section 2.6.2 precedence order the C engine's conflict resolution then applies.
    """
    prefix = _xsl_prefix(root)
    imports: list[Node] = []
    for child in root.children:
        if not isinstance(child, Element) or child.tag != f"{prefix}:import":
            continue
        href = child.attrs.get("href")
        if href is None:
            msg = "xsl:import requires an href attribute"
            raise ValueError(msg)
        path = base / str(href)
        imported = parse_xml(path.read_text(encoding="utf-8"))
        # parse_xml raises HTMLParseError on a document with no root element, so imported.root is set here.
        imports.extend(_load_imports(cast("Element", imported.root), path.parent))
        imports.append(imported)
    return imports


def _resolve(stylesheet: Node, base_url: str | None) -> list[Node] | None:
    """Return the imported stylesheets a transform must merge, or None when the stylesheet imports nothing."""
    root = stylesheet if isinstance(stylesheet, Element) else getattr(stylesheet, "root", None)
    if not isinstance(root, Element):
        return None
    prefix = _xsl_prefix(root)
    if not any(isinstance(child, Element) and child.tag == f"{prefix}:import" for child in root.children):
        return None
    if base_url is None:
        msg = "xsl:import needs a base_url to resolve the imported stylesheet's href against"
        raise ValueError(msg)
    return _load_imports(root, Path(base_url).parent)


class Transform:
    """
    A compiled XSLT 1.0 stylesheet, callable over source documents (lxml's ``etree.XSLT``).

    :param stylesheet: the stylesheet, a tree parsed with :func:`turbohtml.parse_xml`.
    :param base_url: the stylesheet's path or URL, against which ``xsl:import`` hrefs resolve; required only when the
        stylesheet imports.
    """

    __slots__ = ("_imports", "_stylesheet")

    def __init__(self, stylesheet: Node, *, base_url: str | None = None) -> None:
        """Hold the parsed stylesheet and pre-resolve its imported stylesheets once."""
        self._stylesheet = stylesheet
        self._imports = _resolve(stylesheet, base_url)

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


def transform(stylesheet: Node, source: Node, /, *, base_url: str | None = None, **params: str) -> str:
    """
    Apply an XSLT 1.0 stylesheet to a source document in one call.

    Equivalent to ``Transform(stylesheet, base_url=base_url)(source, **params)``; use :class:`Transform` to apply one
    stylesheet to many documents without re-reading it each time.

    :param stylesheet: the stylesheet, a tree parsed with :func:`turbohtml.parse_xml`.
    :param source: the document to transform, a parsed tree.
    :param base_url: the stylesheet's path or URL, against which ``xsl:import`` hrefs resolve; required only when the
        stylesheet imports.
    :param params: top-level ``xsl:param`` values, each an XPath expression string.
    :returns: the transformed document serialized under the stylesheet's ``xsl:output`` method.
    """
    return _xslt_transform(stylesheet, source, params or None, _resolve(stylesheet, base_url))
