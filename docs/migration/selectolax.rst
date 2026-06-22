#################
 From selectolax
#################

`selectolax <https://github.com/rushter/selectolax>`_ wraps the same `lexbor <https://lexbor.com>`_ engine turbohtml
benchmarks against, so the speed is comparable; the move is mostly API surface. selectolax searches with CSS only and
exposes ``text()`` as a method, while turbohtml adds the ``find``/``find_all`` filter grammar and makes
:attr:`~turbohtml.Node.text` a property.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - selectolax
      - turbohtml
    - - ``LexborHTMLParser(html)``
      - ``turbohtml.parse(html)``
    - - ``parser.root``, ``parser.body``
      - ``doc.root``, ``doc.find("body")``
    - - ``node.css("a")``, ``node.css_first("a")``
      - ``node.select("a")``, ``node.select_one("a")``
    - - ``node.tag``
      - ``node.tag`` (same)
    - - ``node.attributes``
      - ``node.attrs``
    - - ``node.text()`` (a method)
      - ``node.text`` (a property), ``node.strings``, ``node.stripped_strings``
    - - ``node.html``, ``node.decompose()``, ``node.unwrap()``
      - the same names

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
- selectolax mutation is limited; turbohtml's edit surface (``append``, ``insert``, ``wrap``, ``unwrap``,
  ``replace_with``, and the rest) is full.
- selectolax's lexbor-specific knobs and its raw C-level node handles are not exposed; turbohtml's public surface is the
  typed Python tree, not the underlying engine's C API.
