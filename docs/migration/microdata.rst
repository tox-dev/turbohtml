################
 From microdata
################

.. package-meta:: microdata edsu/microdata

`microdata <https://github.com/edsu/microdata>`_ pulls the HTML Microdata items a page embeds: ``get_items(html)``
builds an html5lib tree, walks its ``itemscope`` elements, and returns ``Item`` objects you read with ``.itemtype``,
``.get``/``.get_all``, and ``.json``. turbohtml serves that surface from :func:`turbohtml.extract.microdata` over the
same C walk :meth:`turbohtml.Document.microdata` runs.

***************
 Why turbohtml
***************

:func:`turbohtml.extract.microdata` returns the page's top-level items as :class:`~turbohtml.MicrodataItem` records with
the same accessors -- :meth:`~turbohtml.MicrodataItem.get`, :meth:`~turbohtml.MicrodataItem.get_all`, and
:meth:`~turbohtml.MicrodataItem.json` -- so a nested property that is itself an ``itemscope`` comes back as a nested
:class:`~turbohtml.MicrodataItem` rather than a flat string:

.. testcode::

    from turbohtml.extract import microdata

    item = microdata(
        '<div itemscope itemtype="https://schema.org/Person">'
        '<span itemprop="name">Ada</span>'
        '<span itemprop="name">Lovelace</span>'
        '<div itemprop="address" itemscope><span itemprop="city">London</span></div>'
        "</div>"
    )[0]
    print(item.type)
    print(item.get("name"), item.get_all("name"))
    print(item.get("address").get("city"))

.. testoutput::

    https://schema.org/Person
    Ada ['Ada', 'Lovelace']
    London

``microdata`` starts from the raw HTML string, so it parses before it extracts: it builds an html5lib tree and walks the
``itemscope`` elements in Python, where :func:`~turbohtml.extract.microdata` parses to the WHATWG tree and gathers the
items in one C walk. On a product page carrying Microdata alongside JSON-LD and OpenGraph, and on an 8 KiB catalog of
the same, the walk runs 35 to 42 times faster:

.. bench-table::
    :file: bench/microdata.json

Over the fixtures in ``microdata``'s own test suite the two libraries return byte-identical ``json()`` for every page
but one, where turbohtml follows the spec more closely (see :ref:`microdata-divergences`).

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `microdata <https://github.com/edsu/microdata>`__
      - turbohtml
    - - ``microdata.get_items(html)``
      - :func:`turbohtml.extract.microdata`
    - - ``item.itemtype`` (a list of ``URI``)
      - :attr:`item.type <turbohtml.MicrodataItem.type>` (the ``itemtype`` string verbatim)
    - - ``item.itemid``
      - :attr:`item.id <turbohtml.MicrodataItem.id>`
    - - ``item.props``
      - :attr:`item.properties <turbohtml.MicrodataItem.properties>`
    - - ``item.get(name)`` / ``item.get_all(name)``
      - :meth:`~turbohtml.MicrodataItem.get` / :meth:`~turbohtml.MicrodataItem.get_all`
    - - ``item.json()``
      - :meth:`~turbohtml.MicrodataItem.json`

The items are frozen records that hold no reference back into the tree, so you can keep them after the document is gone:

.. testcode::

    item = microdata('<div itemscope itemid="urn:isbn:1"><span itemprop="name">B</span></div>')[0]
    print(item.id, item.json())

.. testoutput::

    urn:isbn:1 {
      "id": "urn:isbn:1",
      "properties": {
        "name": [
          "B"
        ]
      }
    }

.. _microdata-divergences:

**********
 Pitfalls
**********

- ``item.itemtype`` is a list of ``URI`` objects (``item.itemtype[0].string``); :attr:`~turbohtml.MicrodataItem.type` is
  the raw ``itemtype`` string, which the `WHATWG spec <https://html.spec.whatwg.org/multipage/microdata.html>`_ treats
  as a space-separated token set. Call ``.type.split()`` for the list, the form :meth:`~turbohtml.MicrodataItem.json`
  emits.
- A text property's value is the element's ``textContent`` per the spec, so a ``<script>`` or ``<style>`` nested inside
  a property element contributes its text; ``microdata`` drops that text. This is the one page in ``microdata``'s
  fixtures where the ``json()`` output differs.
- :meth:`~turbohtml.MicrodataItem.get` returns the first value or ``None`` and :meth:`~turbohtml.MicrodataItem.get_all`
  returns the list or ``[]``, matching ``Item.get``/``Item.get_all``; a property that is itself an ``itemscope`` comes
  back as a nested :class:`~turbohtml.MicrodataItem`, not a string.
- :func:`~turbohtml.extract.microdata` reparses the string on each call. Hold a :func:`turbohtml.parse` document and
  call :meth:`~turbohtml.Document.microdata` when you also need :meth:`~turbohtml.Document.json_ld` or
  :meth:`~turbohtml.Document.opengraph` off the same page.
