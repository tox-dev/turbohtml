###############
 From html5lib
###############

.. image:: https://static.pepy.tech/badge/html5lib
    :alt: html5lib downloads
    :target: https://pepy.tech/project/html5lib

`html5lib <https://html5lib.readthedocs.io>`_ is the reference pure-Python implementation of the WHATWG parsing
algorithm: it tokenizes and builds a tree that you select through a treebuilder (an :mod:`xml.etree.ElementTree` element
by default, or DOM, or lxml), and it is the conformance baseline other parsers are checked against.

***************
 Why turbohtml
***************

turbohtml runs the same WHATWG algorithm, so the *tree* matches, but it produces one typed hierarchy with navigation,
search, and serialization built in instead of a foreign tree behind a treebuilder choice. The algorithm runs in C, so
parsing and tokenizing are 30 to 80 times faster than the pure-Python implementation:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - html5lib
      - speed-up
    - - parse wpt page (4 kB)
      - 11.4 µs
      - 620 µs
      - 54.5x
    - - parse wpt page (92 kB)
      - 272 µs
      - 16.7 ms
      - 61.6x
    - - tokenize typical markup
      - 32.1 µs
      - 815 µs
      - 25.4x
    - - tokenize whatwg spec (235 kB)
      - 687 µs
      - 19.2 ms
      - 27.9x

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html5lib
      - turbohtml
    - - :func:`html5lib.parse() <html5lib.html5parser.parse>`
      - :func:`turbohtml.parse`
    - - ``html5lib.parse(s, treebuilder="dom")``
      - one typed tree, no treebuilder choice
    - - :func:`html5lib.parseFragment() <html5lib.html5parser.parseFragment>`
      - :func:`turbohtml.parse_fragment`
    - - the html5lib tokenizer
      - :func:`turbohtml.tokenize`, :class:`turbohtml.Tokenizer`
    - - ``el.tag`` namespaced (``{http://www.w3.org/1999/xhtml}div``)
      - :attr:`~turbohtml.Element.tag` plus :class:`~turbohtml.Namespace` on :attr:`~turbohtml.Element.namespace`
    - - the treebuilder's own walk and ``el.attrib``
      - :attr:`~turbohtml.Node.children`, :meth:`~turbohtml.Node.find` / :meth:`~turbohtml.Node.select`,
        :attr:`~turbohtml.Element.attrs`

.. testcode::

    doc = parse("<table><tr><td>x")  # the same tree html5lib and a browser build
    print(doc.find("td").text)

.. testoutput::

    x

**********
 Pitfalls
**********

- html5lib gives you a foreign tree (ElementTree, DOM, or lxml) and you pick a treebuilder; turbohtml has one typed
  tree, so there is nothing to choose and the node types are sealed and pattern-matchable.
- html5lib's ElementTree output namespaces names; turbohtml keeps ``tag`` plain and carries the namespace separately as
  :attr:`~turbohtml.Element.namespace`.
