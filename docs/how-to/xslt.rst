#########################
 Transform XML with XSLT
#########################

You have an XML document and an XSLT 1.0 stylesheet, and you want the transformed output. Parse both, then apply the
stylesheet.

********************
 Apply a stylesheet
********************

A stylesheet is itself XML, so read it with :func:`turbohtml.parse_xml`; the source is any parsed tree. Wrap the
stylesheet in :class:`turbohtml.transform.Transform` and call it with the source:

.. testcode::

    from turbohtml import parse_xml
    from turbohtml.transform import Transform

    style = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="html"/>'
        '<xsl:template match="/"><ul>'
        '<xsl:apply-templates select="list/item"><xsl:sort select="."/></xsl:apply-templates>'
        "</ul></xsl:template>"
        '<xsl:template match="item"><li><xsl:value-of select="."/></li></xsl:template>'
        "</xsl:stylesheet>"
    )
    convert = Transform(style)
    print(convert(parse_xml("<list><item>pear</item><item>apple</item></list>")))

.. testoutput::

    <ul><li>apple</li><li>pear</li></ul>

:class:`~turbohtml.transform.Transform` compiles the stylesheet once; call it on as many documents as you like. For a
one-off, :func:`turbohtml.transform.transform` does both steps in a single call.

*****************
 Pass parameters
*****************

A top-level ``xsl:param`` becomes a keyword argument. Each value is an XPath expression, so quote a string literal the
way lxml does:

.. testcode::

    style = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/>'
        '<xsl:param name="greeting" select="\'Hello\'"/>'
        '<xsl:template match="/"><xsl:value-of select="$greeting"/>, <xsl:value-of select="//name"/></xsl:template>'
        "</xsl:stylesheet>"
    )
    doc = parse_xml("<r><name>World</name></r>")
    print(Transform(style)(doc, greeting="'Hi'"))

.. testoutput::

    Hi, World

*****************
 Choose a method
*****************

The stylesheet's ``xsl:output method`` picks the serialization: ``xml`` (the default, with a leading declaration unless
``omit-xml-declaration="yes"``), ``html`` (void elements stay open), or ``text`` (character data only). A malformed
stylesheet, a bad expression, or a reference to an undeclared key or template raises :exc:`ValueError`; an
``xsl:message`` with ``terminate="yes"`` raises :exc:`RuntimeError`.

*********************
 Import a stylesheet
*********************

``xsl:import`` composes stylesheets across files. Because a parsed tree carries no location, pass the importing
stylesheet's path as ``base_url`` so each ``href`` resolves against it; the imported templates join at lower import
precedence, so the importer's own rules win a conflict.

.. testcode::

    import tempfile
    from pathlib import Path
    from turbohtml import parse_xml
    from turbohtml.transform import transform

    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder, "base.xsl")
        base.write_text(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:template match="a">[<xsl:value-of select="."/>]</xsl:template></xsl:stylesheet>',
            encoding="utf-8",
        )
        main = Path(folder, "main.xsl")
        main.write_text(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/><xsl:import href="base.xsl"/>'
            '<xsl:template match="/"><xsl:apply-templates select="r/a"/></xsl:template></xsl:stylesheet>',
            encoding="utf-8",
        )
        source = parse_xml("<r><a>x</a></r>")
        print(transform(parse_xml(main.read_text(encoding="utf-8")), source, base_url=str(main)))

.. testoutput::

    [x]

The only features out of reach are locale-aware ``xsl:sort`` collation, ``id()`` over DTD-declared IDs, ``xsl:include``,
and ``document()``. For the design, see :doc:`/explanation/xslt`; for a port from lxml, see :doc:`/migration/lxml`.
