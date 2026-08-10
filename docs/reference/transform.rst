###########
 Transform
###########

.. module:: turbohtml.transform

Apply an XSLT 1.0 stylesheet to a document, the job `lxml <https://lxml.de>`_'s ``etree.XSLT`` does. Parse the XML
stylesheet with :func:`turbohtml.parse_xml`; :class:`Transform` compiles it into a callable for source documents. The C
extension performs the transformation and sends every match pattern and select expression through turbohtml's XPath 1.0
engine.

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
imported declarations join conflict resolution at lower import precedence. The compatibility default permits any local
path. Set ``allow_imports=False`` for an untrusted stylesheet, or set ``import_root`` so parent traversal, absolute
paths, file URLs, and resolved symlinks must stay inside one directory. ``xsl:include`` and ``document()`` do not
resolve, and ``document()`` returns an empty node-set.

:class:`Transform` copies the principal and imported stylesheets into private native storage, then builds the stylesheet
model and compiled XPath programs during construction. Each call allocates source-specific evaluation state; callers can
use one instance with different documents across threads without sharing writable caches.
