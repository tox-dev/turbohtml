################
 From microdata
################

.. package-meta:: microdata edsu/microdata

`microdata <https://github.com/edsu/microdata>`_ is a small pure-Python library that pulls the HTML Microdata items a
page embeds -- the WHATWG ``itemscope``/``itemprop`` vocabulary sites use to mark up schema.org products, recipes,
people, and events. Its ``get_items`` entry point parses the page with html5lib, walks the ``itemscope`` elements in
Python, and returns ``Item`` objects you read with ``.itemtype``, ``.itemid``, ``.props``, ``.get``/``.get_all``, and
``.json``. It has long been the go-to dependency for scrapers that need schema.org markup off a page.

turbohtml serves that surface from :func:`turbohtml.extract.microdata`, backed by the same C walk that
:meth:`turbohtml.Document.microdata` runs over the WHATWG tree. It returns the page's top-level items as frozen
:class:`~turbohtml.MicrodataItem` records with the same accessors, so a scraper's item-reading code ports without a
rewrite while the parse-and-walk runs in the C extension rather than in Python over an html5lib tree.

************************
 turbohtml vs microdata
************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - microdata
    - - Scope
      - Full WHATWG HTML5 parser, serializer, and an extraction suite (Microdata, JSON-LD, OpenGraph, links, dates,
        boilerplate)
      - Microdata extraction only
    - - Feature breadth
      - Microdata, JSON-LD, and OpenGraph gathered in one walk; nested ``itemscope`` returns as a nested record
      - ``get_items`` from a URL, file, or string; nested items; a mutable ``Item`` you can also build and edit
    - - Performance
      - One C walk over the parsed tree; 35 to 42x faster on the benchmark pages
      - Python walk over an html5lib tree, built on each ``get_items`` call
    - - Typing
      - Fully typed; :class:`~turbohtml.MicrodataItem` is a frozen, slotted dataclass with shipped stubs
      - Untyped
    - - Dependencies
      - C extension, no pure-Python runtime dependencies
      - Requires html5lib
    - - Maintenance
      - Actively developed
      - Minimal, low-activity single-maintainer project, stable for years

Feature overlap
===============

The item-reading surface ports one-to-one:

- ``microdata.get_items(html)`` -> :func:`turbohtml.extract.microdata`, both returning the page's top-level items in
  document order.
- ``item.itemid`` -> :attr:`item.id <turbohtml.MicrodataItem.id>`.
- ``item.props`` -> :attr:`item.properties <turbohtml.MicrodataItem.properties>`, each ``itemprop`` name mapped to its
  values in document order.
- ``item.get(name)`` / ``item.get_all(name)`` -> :meth:`~turbohtml.MicrodataItem.get` /
  :meth:`~turbohtml.MicrodataItem.get_all`, with the same first-value-or-``None`` and list-or-``[]`` contract.
- ``item.json()`` -> :meth:`~turbohtml.MicrodataItem.json`, the same two-space-indented JSON of the tree below the item.
- A property that is itself an ``itemscope`` comes back as a nested item in both libraries, not a flat string.

What turbohtml adds
===================

- Nested items and multi-valued properties resolve directly off the record, so a property that is another ``itemscope``
  is a nested :class:`~turbohtml.MicrodataItem`:

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

- JSON-LD and OpenGraph off the same page in the same C walk: :meth:`turbohtml.Document.json_ld`,
  :meth:`turbohtml.Document.opengraph`, and :meth:`turbohtml.Document.structured_data`, plus the string-input helpers
  :func:`turbohtml.extract.opengraph` and the rest of the extraction suite. ``microdata`` covers Microdata alone.
- :meth:`turbohtml.Document.microdata` extracts from an already-parsed :func:`turbohtml.parse` document, so pages you
  hold for other work do not get reparsed.
- The records are frozen and hold no reference back into the tree, so they outlive the document:

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

- A text property's value follows the spec's ``textContent`` rule, so text nested in the property element is kept where
  ``microdata`` drops it (see :ref:`microdata-divergences`).
- URL-valued properties (an ``a``/``area``/``link`` href, a media ``src``, an ``object`` data) are returned verbatim by
  default; pass ``base_url=`` to :func:`~turbohtml.extract.microdata` (or :meth:`~turbohtml.Document.microdata`) to
  absolutize them against it, the way ``microdata``'s ``url`` argument resolves them. A ``<base href>`` refines the base
  URL.

What microdata has that turbohtml does not
==========================================

- **Fetching from a URL or file.** ``get_items(location, encoding=None)`` accepts a URL or file-like object and reads it
  for you. :func:`~turbohtml.extract.microdata` takes an HTML string only. Workaround: fetch the bytes yourself
  (``urllib``/``requests``) and pass the decoded text, or feed the response to :func:`turbohtml.parse` and call
  :meth:`~turbohtml.Document.microdata`.
- **An explicit encoding argument.** ``get_items`` takes ``encoding`` to decode the fetched bytes. turbohtml handles
  encoding at parse time on the byte input instead; there is no separate argument on the string helper.
- **``itemtype`` as parsed URI objects.** ``item.itemtype`` is a list of ``URI`` objects (``item.itemtype[0].string``).
  :attr:`~turbohtml.MicrodataItem.type` is the raw ``itemtype`` string; call ``.type.split()`` for the token list.
- **A mutable, buildable item.** ``microdata``'s ``Item`` is an open object whose ``props`` dict you can edit and add
  to. :class:`~turbohtml.MicrodataItem` is a frozen read-only record. No equivalent; build your own structure if you
  need to synthesize or mutate items.

Performance
===========

``microdata`` starts from the raw HTML string, so it parses before it extracts: it builds an html5lib tree and walks the
``itemscope`` elements in Python, where :func:`~turbohtml.extract.microdata` parses to the WHATWG tree and gathers the
items in one C walk. On a product page carrying Microdata alongside JSON-LD and OpenGraph, and on an 8 KiB catalog of
the same, the walk runs 35 to 42 times faster:

.. bench-table::
    :file: bench/microdata.json

Over the fixtures in ``microdata``'s own test suite the two libraries return byte-identical ``json()`` for every page
but one, where turbohtml follows the spec more closely (see :ref:`microdata-divergences`).

****************
 How to migrate
****************

Swap the import and the entry point:

.. code-block:: python

    # before
    from microdata import get_items

    # after
    from turbohtml.extract import microdata

The call and item API map directly:

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

The item-reading code is the same shape on both sides; only the ``itemtype`` accessor changes:

.. code-block:: python

    # before
    from microdata import get_items

    items = get_items(
        '<div itemscope itemtype="https://schema.org/Person">'
        '<span itemprop="name">Ada</span>'
        '<div itemprop="address" itemscope><span itemprop="city">London</span></div>'
        "</div>"
    )
    item = items[0]
    print(item.itemtype[0].string)
    print(item.get("name"))
    print(item.get("address").get("city"))

    # after
    from turbohtml.extract import microdata

    item = microdata(
        '<div itemscope itemtype="https://schema.org/Person">'
        '<span itemprop="name">Ada</span>'
        '<div itemprop="address" itemscope><span itemprop="city">London</span></div>'
        "</div>"
    )[0]
    print(item.type)
    print(item.get("name"))
    print(item.get("address").get("city"))

.. _microdata-divergences:

**********************
 Gotchas and pitfalls
**********************

- ``item.itemtype`` is a list of ``URI`` objects, read via ``item.itemtype[0].string``.
  :attr:`~turbohtml.MicrodataItem.type` is the raw ``itemtype`` string, which the `WHATWG spec
  <https://html.spec.whatwg.org/multipage/microdata.html>`_ treats as a space-separated token set. Call
  ``.type.split()`` for the list, the form :meth:`~turbohtml.MicrodataItem.json` emits.
- A text property's value is the element's ``textContent`` per the spec, so a ``<script>`` or ``<style>`` nested inside
  a property element contributes its text; ``microdata`` drops that text. This is the one page in ``microdata``'s
  fixtures where the ``json()`` output differs.
- :meth:`~turbohtml.MicrodataItem.get` returns the first value or ``None`` and :meth:`~turbohtml.MicrodataItem.get_all`
  returns the list or ``[]``, matching ``Item.get``/``Item.get_all``; a property that is itself an ``itemscope`` comes
  back as a nested :class:`~turbohtml.MicrodataItem`, not a string.
- :func:`~turbohtml.extract.microdata` takes an HTML string and reparses it on each call. To read a URL or file, fetch
  the text yourself first. When you also need :meth:`~turbohtml.Document.json_ld` or
  :meth:`~turbohtml.Document.opengraph` off the same page, hold a :func:`turbohtml.parse` document and call
  :meth:`~turbohtml.Document.microdata` so the page is parsed once.
