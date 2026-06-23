###################
 From html5-parser
###################

.. image:: https://static.pepy.tech/badge/html5-parser/month
    :alt: html5-parser monthly downloads
    :target: https://pepy.tech/project/html5-parser

`html5-parser <https://html5-parser.readthedocs.io>`_ wraps gumbo, the C WHATWG parser, and hands the result back as an
`lxml <https://lxml.de>`_/ElementTree tree. It is turbohtml's closest direct competitor: a native parse with no
pure-Python pass.

***************
 Why turbohtml
***************

The difference is what you get back. ``html5_parser.parse`` returns a read-oriented lxml element built on ``libxml2``,
while :func:`turbohtml.parse` returns a fully type annotated :class:`~turbohtml.Document` with a mutable, natively typed
tree and a real ``<template>`` content document, and no ``libxml2``/gumbo build dependency. html5-parser builds its tree
through gumbo into ``libxml2``, where turbohtml runs its own C engine straight into the native tree, so parsing the same
document is more than an order of magnitude faster:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - parse
      - turbohtml
      - html5-parser
      - speed-up
    - - 4 kB document
      - 2.8 µs
      - 64.1 µs
      - 22.9x
    - - 92 kB document
      - 130 µs
      - 1.84 ms
      - 14.2x
    - - 3 MB document
      - 4.63 ms
      - 59.7 ms
      - 12.9x

*************
 The renames
*************

.. code-block:: python

    # html5-parser
    from html5_parser import parse

    root = parse(markup)  # an lxml.etree element

.. testcode::

    from turbohtml import parse
    doc = parse("<table><tr><td>cell</td></table>")
    print(doc.find("td").text)  # the tbody the WHATWG algorithm inserts is walked the same way

.. testoutput::

    cell

Because the tree it returns is lxml's, the element accessors port exactly as in the :ref:`From lxml <migration-lxml>`
section: ``el.get``/``el.attrib`` become :attr:`~turbohtml.Element.attrs`, the ``el.text``/``el.tail`` string pair
becomes child :class:`~turbohtml.Text` nodes, ``el.getparent()`` becomes :attr:`~turbohtml.Node.parent`, and
``el.xpath(...)`` maps to turbohtml's XPath 1.0 :meth:`~turbohtml.Node.xpath` (or CSS :meth:`~turbohtml.Node.select` and
the :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` grammar).

**********
 Pitfalls
**********

Like :ref:`From lxml <migration-lxml>`, the gap is the rest of the libxml2 stack the returned tree would otherwise
carry, dropped on purpose with the ``libxml2``/gumbo build dependency:

- XSLT, DTD/RelaxNG/XML-Schema validation, and C14N have no equivalent. XPath is at parity: both run XPath 1.0 with
  EXSLT (libxml2 has no XPath 2.0/XQuery either).
- The ``.text``/``.tail`` model becomes real :class:`~turbohtml.Text` nodes rather than the two string fields.
