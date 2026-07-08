###########
 Transform
###########

.. module:: turbohtml.transform

Apply an XSLT 1.0 stylesheet to a document, the job `lxml <https://lxml.de>`_'s ``etree.XSLT`` does. A stylesheet is an
XML document, so it is read with :func:`turbohtml.parse_xml`; :class:`Transform` holds the parsed stylesheet and is
callable over source documents, the compile-once, apply-many shape. The whole transformation runs in the C extension,
reusing turbohtml's XPath 1.0 engine for every match pattern and select expression rather than growing a second path
evaluator.

.. autoclass:: Transform
    :members: __call__

.. autofunction:: transform

The engine covers the XSLT 1.0 core: ``xsl:template`` (``match``, ``name``, ``mode``, ``priority``),
``xsl:apply-templates`` (``select``, ``mode``, ``xsl:sort``, ``xsl:with-param``), ``xsl:call-template``,
``xsl:for-each``, ``xsl:if``, ``xsl:choose``/``xsl:when``/``xsl:otherwise``, ``xsl:value-of``, ``xsl:copy`` and
``xsl:copy-of``, ``xsl:element``/``xsl:attribute``/``xsl:text``/``xsl:comment``/``xsl:processing-instruction``,
``xsl:variable``/``xsl:param`` (local and top-level), ``xsl:sort`` (``data-type``, ``order``), ``xsl:number``
(``value``, ``format``), ``xsl:key`` with the ``key()`` function, the built-in template rules, and the section 5.5
conflict resolution by priority then document order. It emits the ``xml``, ``html``, and ``text`` output methods, and
adds the XSLT functions ``current()``, ``key()``, ``generate-id()``, ``format-number()``, ``system-property()``,
``function-available()``, and ``element-available()``.

External-document loading is limited. ``xsl:import`` resolves local paths and file URLs against ``base_url``; the
imported declarations join conflict resolution at lower import precedence. ``xsl:include`` and ``document()`` do not
resolve, and ``document()`` returns an empty node-set.
