###############
 From html5lib
###############

`html5lib <https://github.com/html5lib/html5lib-python>`_ runs the same WHATWG algorithm turbohtml does, so the *tree*
it produces matches; what changes is that html5lib hands you a generic tree you select with a treebuilder (an
:mod:`xml.etree.ElementTree` element by default, or DOM, or lxml), while turbohtml has one typed hierarchy with
navigation, search, and serialization built in.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html5lib
      - turbohtml
    - - ``html5lib.parse(s)``
      - ``turbohtml.parse(s)``
    - - ``html5lib.parse(s, treebuilder="dom")``
      - one typed tree, no treebuilder choice
    - - ``html5lib.parseFragment(s, container="div")``
      - ``turbohtml.parse_fragment(s, "div")``
    - - the html5lib tokenizer
      - ``turbohtml.tokenize(s)``, ``turbohtml.Tokenizer``
    - - ``el.tag`` namespaced (``{http://www.w3.org/1999/xhtml}div``)
      - ``el.tag`` plus an :class:`~turbohtml.Namespace` on ``el.namespace``
    - - the treebuilder's own walk and ``el.attrib``
      - ``el.children``, ``el.find``/``el.select``, ``el.attrs``

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
