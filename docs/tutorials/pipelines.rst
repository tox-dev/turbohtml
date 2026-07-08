##################################
 Transforming without a full tree
##################################

Not every job needs a navigable tree. Sometimes you want to change markup as it streams, reshape one document into
another, or check a document against a contract. This tutorial runs a streaming rewrite, an XSLT transform, and a schema
validation -- three transforms that never ask you to walk a tree by hand.

***********************
 Rewrite as it streams
***********************

:func:`turbohtml.rewrite.rewrite` transforms a document in one pass that never builds a tree, the way Cloudflare's
lol-html rewrites at the edge. Register a ``(selector, handler)`` pair; the handler receives each match and edits it,
while everything you do not touch is copied through. Here every image gets ``loading="lazy"``:

.. testcode::

    from turbohtml.rewrite import rewrite


    def lazy(img):
        img.set_attribute("loading", "lazy")


    print(rewrite("<img src=hero.jpg><img src=x.png loading=eager>", elements=[("img", lazy)]))

.. testoutput::

    <img src="hero.jpg" loading="lazy"><img src="x.png" loading="lazy">

Because the rewriter never looks ahead, only selectors decidable from an element and its ancestors match;
:doc:`/how-to/rewriting` lists which, and :doc:`/explanation/streaming` explains why.

*********************
 Transform with XSLT
*********************

To reshape one document into another, apply an XSLT 1.0 stylesheet, the job lxml's ``etree.XSLT`` does. Parse the
stylesheet with :func:`turbohtml.parse_xml`, wrap it in :class:`turbohtml.transform.Transform`, and call it on a source:

.. testcode::

    from turbohtml import parse_xml
    from turbohtml.transform import Transform

    style = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="html"/>'
        '<xsl:template match="/"><ul>'
        '<xsl:apply-templates select="list/item"/></ul></xsl:template>'
        '<xsl:template match="item"><li><xsl:value-of select="."/></li></xsl:template>'
        "</xsl:stylesheet>"
    )
    print(Transform(style)(parse_xml("<list><item>one</item><item>two</item></list>")))

.. testoutput::

    <ul><li>one</li><li>two</li></ul>

***************************
 Validate against a schema
***************************

When the input is XML with a contract, check it with :class:`turbohtml.validate.XMLSchema` or
:class:`~turbohtml.validate.RelaxNG`. Compile once, then validate; the result carries a ``valid`` flag and one
:class:`~turbohtml.validate.ValidationError` per violation, each with the path that located it:

.. testcode::

    from turbohtml import parse_xml
    from turbohtml.validate import XMLSchema

    schema = XMLSchema(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="order"><xs:complexType><xs:sequence>'
        '<xs:element name="sku" type="xs:string"/>'
        '<xs:element name="qty" type="xs:positiveInteger"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    result = schema.validate(parse_xml("<order><sku>A-1</sku><qty>0</qty></order>"))
    print(result.valid)
    print(result.errors[0].path, result.errors[0].type)

.. testoutput::

    False
    /order/qty datatype

Stream-edit, transform, validate -- three ways to move a document forward without materializing a tree to walk. Last,
:doc:`cli` runs these jobs straight from a shell.
