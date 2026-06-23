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
    - - ``parser.strip_tags(["script"])``, ``node.unwrap_tags(["b"])``
      - :meth:`node.remove("script") <turbohtml.Node.remove>`, :meth:`node.strip_tags("b") <turbohtml.Node.strip_tags>`

.. testcode::

    doc = parse("<ul><li>a</li><li>b</li></ul>")
    print([li.text for li in doc.select("li")])

.. testoutput::

    ['a', 'b']

*************
 Performance
*************

Dropping a set of tags together with their subtrees: selectolax's ``strip_tags`` against turbohtml's
:meth:`~turbohtml.Node.remove`, over a 92 kB page holding 839 ``<code>``/``<a>``/``<q>`` elements. Both rewrites are
destructive, so the timed call parses the page, drops every match, and serializes the result -- the string-to-result
pass these helpers exist for. turbohtml's lighter native tree runs the whole pass three times faster:

.. list-table::
    :header-rows: 1
    :widths: 46 18 18 18

    - - drop tags with content (92 kB)
      - turbohtml
      - selectolax
      - speed-up
    - - ``remove`` vs ``strip_tags``
      - 554 µs
      - 1.73 ms
      - 3.1x

Collecting a node's visible text -- selectolax's ``text()`` method against turbohtml's :attr:`~turbohtml.Node.text`
property -- over the wpt pages. Both walk the descendant text runs, but turbohtml concatenates them in one C pass into a
buffer reserved up front, where selectolax crosses the lexbor boundary per node, so it runs six to thirteen times faster
(``tox -e bench text``):

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - text content
      - turbohtml
      - selectolax
      - speed-up
    - - wpt page (4 kB)
      - 0.8 µs
      - 5.2 µs
      - 6.4x
    - - wpt page (9.6 kB)
      - 1.1 µs
      - 12.1 µs
      - 11.4x
    - - wpt page (92 kB)
      - 36.9 µs
      - 488 µs
      - 13.2x

**********
 Pitfalls
**********

- selectolax queries are CSS-only; turbohtml adds the ``find``/``find_all`` filter grammar with axes and regex or
  callable filters.
- ``node.text`` is a property; drop the parentheses.
- the bulk tag strippers are named the other way around: selectolax's ``strip_tags`` drops the tags *with* their content
  (turbohtml's :meth:`~turbohtml.Node.remove`), while its ``unwrap_tags`` keeps the content (turbohtml's
  :meth:`~turbohtml.Node.strip_tags`). Both turbohtml methods take a full CSS selector, not just a tag-name list.
- selectolax's lexbor-specific knobs and its raw C-level node handles are not exposed; turbohtml's public surface is the
  typed Python tree, not the underlying engine's C API.
