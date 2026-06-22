###################
 From html5-parser
###################

`html5-parser <https://github.com/kovidgoyal/html5-parser>`_ wraps gumbo, the C WHATWG parser, and hands the result back
as an `lxml <https://lxml.de>`_/ElementTree tree. It is turbohtml's closest direct competitor: a native parse with no
pure-Python pass. The difference is what you get back. ``html5_parser.parse`` returns a read-oriented lxml element built
on ``libxml2``, while :func:`turbohtml.parse` returns a :class:`~turbohtml.Document` with a mutable, natively typed tree
and a real ``<template>`` content document, and no ``libxml2``/gumbo build dependency.

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
section above: ``el.get``/``el.attrib`` become ``el.attrs``, the ``el.text``/``el.tail`` string pair becomes child
:class:`~turbohtml.Text` nodes, ``el.getparent()`` becomes ``el.parent``, and ``el.xpath(...)`` maps to turbohtml's
XPath 1.0 :meth:`~turbohtml.Node.xpath` (or CSS :meth:`~turbohtml.Node.select` and the
:meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` grammar). The :doc:`/development/performance` page
benchmarks the WHATWG tree builders; html5-parser sits in the same native-gumbo tier as the C parsers measured there, so
it is cross-linked rather than given a separate row, since its lxml tree is bound to a specific ``libxml2`` build that
the benchmark cannot pin portably.

******************************
 Not yet ported / limitations
******************************

Like :ref:`From lxml <migration-lxml>`, the gap is the rest of the libxml2 stack the returned tree would otherwise
carry, dropped on purpose with the ``libxml2``/gumbo build dependency:

- XSLT, DTD/RelaxNG/XML-Schema validation, and C14N have no equivalent; XPath is the 1.0 engine, not 2.0+/XQuery.
- The ``.text``/``.tail`` model becomes real :class:`~turbohtml.Text` nodes rather than the two string fields.
