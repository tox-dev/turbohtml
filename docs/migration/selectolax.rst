#################
 From selectolax
#################

.. image:: https://static.pepy.tech/badge/selectolax
    :alt: selectolax downloads
    :target: https://pepy.tech/project/selectolax

`selectolax <https://github.com/rushter/selectolax>`_ is a fast CSS-only HTML parser that wraps the `lexbor
<https://lexbor.com>`_ engine. It searches with CSS selectors and exposes ``text()`` as a method, with limited tree
mutation.

***************
 Why turbohtml
***************

turbohtml builds the same WHATWG tree but adds full static typing, the ``find``/``find_all`` filter grammar on top of
CSS, a complete edit surface, and :attr:`~turbohtml.Node.text` as a property. Because selectolax wraps lexbor behind a
heavier object layer, turbohtml's lighter native tree parses and serializes faster:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - selectolax
      - speed-up
    - - parse wpt page (4 kB)
      - 11.4 µs
      - 42.2 µs
      - 3.7x
    - - parse wpt page (92 kB)
      - 272 µs
      - 917 µs
      - 3.4x
    - - serialize wpt page (92 kB)
      - 105 µs
      - 339 µs
      - 3.2x

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - selectolax
      - turbohtml
    - - ``LexborHTMLParser(html)``
      - :func:`turbohtml.parse`
    - - ``parser.root``, ``parser.body``
      - :attr:`doc.root <turbohtml.Document.root>`, :meth:`doc.find("body") <turbohtml.Node.find>`
    - - ``node.css("a")``, ``node.css_first("a")``
      - :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.select_one`
    - - ``node.tag``
      - :attr:`~turbohtml.Element.tag` (same)
    - - ``node.attributes``
      - :attr:`~turbohtml.Element.attrs`
    - - ``node.text()`` (a method)
      - :attr:`~turbohtml.Node.text` (a property), :attr:`~turbohtml.Node.strings`,
        :attr:`~turbohtml.Node.stripped_strings`
    - - ``node.html``, ``node.decompose()``, ``node.unwrap()``
      - :attr:`~turbohtml.Node.html`, :meth:`~turbohtml.Node.decompose`, :meth:`~turbohtml.Node.unwrap`

.. testcode::

    doc = parse("<ul><li>a</li><li>b</li></ul>")
    print([li.text for li in doc.select("li")])

.. testoutput::

    ['a', 'b']

**********
 Pitfalls
**********

- selectolax queries are CSS-only; turbohtml adds the ``find``/``find_all`` filter grammar with axes and regex or
  callable filters.
- ``node.text`` is a property; drop the parentheses.
- selectolax's lexbor-specific knobs and its raw C-level node handles are not exposed; turbohtml's public surface is the
  typed Python tree, not the underlying engine's C API.
