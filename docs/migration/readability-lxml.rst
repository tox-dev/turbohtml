#######################
 From readability-lxml
#######################

.. package-meta:: readability-lxml buriy/python-readability

`readability-lxml <https://github.com/buriy/python-readability>`_ is a Python port of Arc90's Readability. A
``Document`` wraps the page HTML; ``summary()`` returns the cleaned article markup, ``short_title()`` the page title,
and ``title()`` the raw ``<title>``. It scores candidate elements by text density and link ratio, strips navigation,
sidebars, and footers, and rewrites the surviving content into a fresh cleaned fragment. Construction takes tuning knobs
(``min_text_length``, ``retry_length``, ``positive_keywords``, ``negative_keywords``, ``url``), and it runs on lxml and
cssselect. It is the content-extraction step behind readers, scrapers, and pipelines that want just the article body off
a full page.

turbohtml covers that same ground with :meth:`~turbohtml.Node.article`, which runs the identical content-density
heuristic in C and returns an :class:`~turbohtml.Article` record. It selects the live scoring element unchanged rather
than rewriting the DOM into a cleaned fragment, and it harvests the page metadata beside the body: byline, date,
description, and language that readability-lxml leaves to you. Because the extraction sits on a full WHATWG parse, the
same page is also a DOM you can query and serialize.

*******************************
 turbohtml vs readability-lxml
*******************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - readability-lxml
    - - Scope
      - Full WHATWG HTML parser with DOM, query, serialize, and content extraction on top
      - Article extraction only, over an lxml parse of the page
    - - Feature breadth
      - One C scoring model plus article metadata (title, byline, date, description, lang)
      - Cleaned article body and title, with keyword and length tuning
    - - Performance
      - C scoring pass over the parsed tree, selects the live element (see below)
      - Python scoring over an lxml tree, then rewrites a cleaned fragment
    - - Typing
      - Fully annotated, ships PEP 561 stubs
      - Untyped pure-Python source
    - - Dependencies
      - Compiled C extension
      - lxml and cssselect
    - - Maintenance
      - Actively developed
      - Maintained community port of Arc90 Readability

Feature overlap
===============

The shared surface ports one call to one call:

- ``Document(html).summary()`` -> ``parse(html).article().element`` (its markup via :attr:`~turbohtml.Node.html`).
- ``Document(html).short_title()`` -> ``parse(html).article().title``.
- the article's plain text -> ``parse(html).article().text`` (or :meth:`~turbohtml.Node.main_text`).
- the scoring element itself -> :meth:`~turbohtml.Node.main_content`, the dominant content body or ``None``.

What turbohtml adds
===================

- :meth:`~turbohtml.Node.article` returns the page metadata beside the body in the same record: ``byline``, ``date``,
  ``description``, and ``lang``. readability-lxml exposes only the title.
- It selects the live element unchanged, so the scored body stays part of the DOM you can keep querying, mutating, and
  serializing. readability-lxml hands back a rewritten HTML string detached from the source tree.
- The extraction rides on a full WHATWG parse, so the same document is available as a queryable, serializable DOM, not
  only as an extracted fragment.
- :func:`turbohtml.parse` follows the WHATWG recovery rules and never raises on malformed markup.

What readability-lxml has that turbohtml does not
=================================================

- A cleaned, rewritten output fragment. ``summary()`` returns scrubbed markup with boilerplate elements removed;
  ``article().element`` is the unmodified scoring element from the live tree. For a scrubbed body, pair it with
  :class:`turbohtml.clean.Sanitizer`.
- Scoring knobs: ``positive_keywords`` and ``negative_keywords`` bias candidate scoring, and ``min_text_length`` /
  ``retry_length`` tune the retry pass. turbohtml's article scoring is not exposed as per-call keyword weights (the
  related :class:`turbohtml.extract.Extraction` thresholds tune :func:`~turbohtml.extract.boilerplate`, not
  :meth:`~turbohtml.Node.article`).
- Pure-Python install with no compiled extension, which can matter on platforms without a prebuilt turbohtml wheel.

Performance
===========

Scoring the content body of a full page -- navigation, a scored article, and a footer. :meth:`~turbohtml.Node.article`
runs the same content-density heuristic in C and selects the live element; readability-lxml builds an lxml tree and
rewrites it into a cleaned summary fragment. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/readability-lxml.json

****************
 How to migrate
****************

Swap the ``Document`` import for :func:`turbohtml.parse`, and pull the body, title, and metadata off one
:class:`~turbohtml.Article` record:

.. code-block:: python

    from turbohtml import parse

The call mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `readability-lxml <https://github.com/buriy/python-readability>`__
      - turbohtml
    - - ``Document(html).summary()``
      - ``parse(html).article().element`` (:attr:`~turbohtml.Node.html` for its markup, or ``None``)
    - - ``Document(html).short_title()``
      - ``parse(html).article().title``
    - - the article's plain text
      - ``parse(html).article().text`` (or :meth:`~turbohtml.Node.main_text`)
    - - the scoring element only
      - :meth:`~turbohtml.Node.main_content`
    - - (no equivalent)
      - ``parse(html).article().byline``, ``.date``, ``.description``, ``.lang``

Before and after, scoring the article off a full page:

.. testcode::

    from turbohtml import parse

    doc = parse(
        "<html lang=en><head><title>Comets</title></head>"
        "<body><nav><a href='/'>Home</a></nav>"
        "<article class=post><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>The tail always points away from the Sun, pushed out by the solar wind and radiation.</p>"
        "</article></body></html>"
    )
    art = doc.article()
    print(art.title, "|", art.element.tag)

.. testoutput::

    Comets | article

**********************
 Gotchas and pitfalls
**********************

- ``summary()`` returns cleaned HTML; ``article().element`` is the scored element from the live tree, unchanged. Read
  :attr:`~turbohtml.Node.html` for its markup, or sanitize it first with :class:`turbohtml.clean.Sanitizer` for a
  scrubbed fragment.
- readability-lxml extracts only the body and title; the byline, date, description, and language come for free in the
  same :class:`~turbohtml.Article` record.
- A page with no scoring article leaves ``element`` as ``None`` and ``text`` empty while still filling the metadata, so
  branch on ``art.element`` rather than assuming a body. readability-lxml raises instead of returning a partial result.
- readability-lxml's ``positive_keywords`` / ``negative_keywords`` reweight candidate scoring per call; turbohtml's
  article scoring has no per-call keyword bias, so pages you tuned by keyword may score differently.
