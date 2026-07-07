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

External resources are out of scope: ``xsl:include``, ``xsl:import``, and ``document()`` do not load other files. For
the design, see :doc:`/explanation/xslt`; for a port from lxml, see :doc:`/migration/lxml`.
