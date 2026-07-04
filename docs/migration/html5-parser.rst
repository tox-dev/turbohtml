###################
 From html5-parser
###################

.. package-meta:: html5-parser kovidgoyal/html5-parser

`html5-parser <https://html5-parser.readthedocs.io>`_ wraps gumbo, the C WHATWG parser, and hands the result back as an
`lxml <https://lxml.de>`_/ElementTree tree. It is a parse-only front end: gumbo tokenizes and tree-builds in C, then the
nodes are copied into whatever backend you pick with ``treebuilder`` (``lxml`` by default, or ``lxml_html``, ``etree``,
``dom``, ``soup``). Everything after the parse — querying, mutation, serialization — is the chosen backend's job, not
html5-parser's. It ships in `calibre <https://calibre-ebook.com>`_ and is a common drop-in wherever a WHATWG-correct
tree is wanted inside an existing lxml pipeline.

turbohtml covers the same ground with a single library: it parses the same WHATWG tree in its own C engine, then keeps
you inside a fully typed :class:`~turbohtml.Document` for querying, editing, and serialization, with no ``libxml2`` or
gumbo build dependency to carry.

***************************
 turbohtml vs html5-parser
***************************

.. list-table::
    :header-rows: 1
    :widths: 16 42 42

    - - Dimension
      - turbohtml
      - html5-parser
    - - Scope
      - Parse, query, mutate, and serialize in one library
      - Parse only; the returned tree is handed to lxml/etree/dom/soup for everything else
    - - Feature breadth
      - CSS :meth:`~turbohtml.Node.select`, XPath 1.0 :meth:`~turbohtml.Node.xpath`, the
        :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` grammar, a full edit surface, Markdown/plain-text
        renderers, sanitizer, linkifier, structured-data extraction
      - Whatever the selected backend exposes (lxml's XPath/cssselect, ElementTree's API, BeautifulSoup's, minidom's)
    - - Performance
      - Native C engine straight into the native tree; see the table below
      - Native gumbo parse copied into a libxml2/backend tree
    - - Typing
      - Fully type annotated with bundled stubs
      - Untyped; static types come from the chosen backend (e.g. lxml-stubs)
    - - Dependencies
      - Self-contained C extension, no ``libxml2``/gumbo
      - Links ``libxml2`` and bundles gumbo; the default path also needs ``lxml``
    - - Maintenance
      - Actively developed
      - Stable and maintained by the calibre author, low churn

Feature overlap
===============

Both are native WHATWG parsers with no pure-Python pass, so the parse call and the tree walk port directly:

- ``html5_parser.parse(markup)`` for ``str`` or ``bytes`` input maps to :func:`turbohtml.parse`.
- Encoding control (``transport_encoding``/``fallback_encoding``) maps to the ``encoding`` and ``detect_encoding``
  arguments of :func:`turbohtml.parse`.
- The root element (``return_root=True``, the default) is :attr:`doc.root <turbohtml.Document.root>`.
- XPath 1.0 querying is at parity: ``root.xpath(...)`` maps to :meth:`~turbohtml.Node.xpath`, and both stop at XPath 1.0
  with EXSLT (libxml2 has no XPath 2.0/XQuery either).
- Element accessors port exactly as in the :ref:`From lxml <migration-lxml>` section, since html5-parser returns lxml's
  tree: ``el.get``/``el.attrib`` become :attr:`~turbohtml.Element.attrs`, ``el.getparent()`` becomes
  :attr:`~turbohtml.Node.parent`.

What turbohtml adds
===================

- CSS selection built in: :meth:`~turbohtml.Node.select`/:meth:`~turbohtml.Node.select_one` plus the
  :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` filter grammar, with no ``lxml.cssselect`` detour.
- A full mutation surface on the typed tree, so edits do not require pulling in libxml2 semantics.
- Serialization in the same library: the :attr:`~turbohtml.Node.html` property, :meth:`~turbohtml.Node.encode`, and the
  :class:`~turbohtml.Markdown`/:class:`~turbohtml.PlainText`/:class:`~turbohtml.Html` renderers.
- Text extraction as first-class API: :attr:`~turbohtml.Node.text`, :attr:`~turbohtml.Node.strings`,
  :attr:`~turbohtml.Node.stripped_strings`.
- A real ``<template>`` content document, and source positions built in via ``parse(..., positions=True)``.
- Higher-level extraction that html5-parser leaves to your pipeline: the sanitizer, the linkifier, and structured-data
  parsing.
- Full static typing across the tree, where html5-parser is untyped.

What html5-parser has that turbohtml does not
=============================================

- **Pluggable treebuilders.** ``treebuilder='etree'``, ``'dom'``, or ``'soup'`` returns a stdlib ElementTree,
  ``xml.dom.minidom``, or BeautifulSoup tree directly. turbohtml returns only its own :class:`~turbohtml.Document`; to
  feed one of those ecosystems you serialize with :attr:`~turbohtml.Node.html` and reparse in the target library.
- **The libxml2 stack on the returned tree.** Because the default output is an lxml element, XSLT,
  DTD/RelaxNG/XML-Schema validation, and C14N are one call away. turbohtml has no equivalent for these; only XPath 1.0
  is at parity.
- **Namespaced XHTML output.** ``maybe_xhtml`` and ``namespace_elements`` produce libxml2 namespace-prefixed nodes for
  XHTML and foreign content. turbohtml builds the WHATWG HTML tree (SVG/MathML foreign content included) but does not
  expose it as namespaced libxml2 elements.
- **Line numbers as attributes.** ``line_number_attr`` writes each element's source line into an attribute on the tree
  itself. turbohtml records source positions with ``positions=True``, but as node metadata, not as injected attributes.

Performance
===========

html5-parser builds its tree through gumbo into ``libxml2``, where turbohtml runs its own C engine straight into the
native tree, so parsing the same document is more than an order of magnitude faster:

.. bench-table::
    :file: bench/html5-parser.json

****************
 How to migrate
****************

.. code-block:: python

    # html5-parser
    from html5_parser import parse

    root = parse(markup)  # an lxml.etree element

Swap the import for turbohtml's :func:`~turbohtml.parse`, which returns a :class:`~turbohtml.Document` instead of a bare
root element:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `html5-parser <https://html5-parser.readthedocs.io>`__
      - turbohtml
    - - ``parse(markup)``
      - :func:`turbohtml.parse`
    - - ``parse(markup, return_root=True)`` (root element)
      - :attr:`doc.root <turbohtml.Document.root>`
    - - ``parse(markup, return_root=False)`` (ElementTree)
      - the :class:`~turbohtml.Document` itself
    - - ``parse(markup, transport_encoding=..., fallback_encoding=...)``
      - :func:`parse(markup, encoding=..., detect_encoding=...) <turbohtml.parse>`
    - - ``root.xpath("//td")``
      - :meth:`~turbohtml.Node.xpath`
    - - ``root.cssselect("td")`` (via lxml)
      - :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.select_one`
    - - ``el.get(...)`` / ``el.attrib``
      - :attr:`~turbohtml.Element.attrs`
    - - ``el.text`` / ``el.tail``
      - child :class:`~turbohtml.Text` nodes, or the :attr:`~turbohtml.Node.text` property
    - - ``el.getparent()``
      - :attr:`~turbohtml.Node.parent`
    - - ``treebuilder='etree' | 'dom' | 'soup'``
      - no equivalent; serialize with :attr:`~turbohtml.Node.html` and reparse in the target library

.. testcode::

    from turbohtml import parse

    doc = parse("<table><tr><td>cell</td></table>")
    print(doc.find("td").text)  # the tbody the WHATWG algorithm inserts is walked the same way

.. testoutput::

    cell

**********************
 Gotchas and pitfalls
**********************

- **Return type.** html5-parser hands back a bare lxml root element (or an ElementTree with ``return_root=False``);
  turbohtml hands back a :class:`~turbohtml.Document`. Reach the root through :attr:`doc.root
  <turbohtml.Document.root>`.
- **The text/tail model.** lxml's two string fields (``.text``, ``.tail``) become real :class:`~turbohtml.Text` child
  nodes. Read a subtree's visible text through the :attr:`~turbohtml.Node.text` property instead of stitching the pair
  together.
- **The rest of the libxml2 stack is gone on purpose.** Dropping the ``libxml2``/gumbo build dependency also drops XSLT,
  DTD/RelaxNG/XML-Schema validation, and C14N. If a pipeline depends on those, keep html5-parser for that stage.
- **Encoding.** html5-parser distinguishes ``transport_encoding`` (an authoritative label) from ``fallback_encoding`` (a
  guess). turbohtml's ``encoding`` is the explicit label and ``detect_encoding=True`` opts into sniffing; there is no
  separate fallback slot.
- **Namespaces.** Without ``maybe_xhtml``/``namespace_elements`` there is nothing to port; with them, expect the
  turbohtml tree to be plain WHATWG HTML rather than namespace-prefixed libxml2 nodes.
