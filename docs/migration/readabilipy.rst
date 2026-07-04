##################
 From readabilipy
##################

.. package-meta:: readabilipy alan-turing-institute/ReadabiliPy

`readabilipy <https://readabilipy.readthedocs.io>`_ is the Alan Turing Institute's article-extraction wrapper. Its fast
path shells out to Mozilla's Readability.js: with Node.js on the machine, ``simple_json_from_html_string(html,
use_readability=True)`` runs the reference extractor and returns a JSON record with ``title``, ``byline``, ``date``,
``content`` (the scored article HTML), ``plain_content``, and ``plain_text`` (the body as a list of text blocks).
Without Node it falls back to a pure-Python cleaner that parses the page with html5lib into BeautifulSoup, strips
scripts and styling, and returns the *whole* cleaned page rather than a scored article. It is used in research pipelines
that harvest readable text from crawled news and web pages.

turbohtml covers the same ground with :meth:`~turbohtml.Node.article`, which runs readability-style content scoring and
metadata harvesting in one C pass over the parsed tree, in-process and with no Node.js runtime in the pipeline.

**************************
 turbohtml vs readabilipy
**************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - readabilipy
    - - Scope
      - HTML5 parser, serializer, sanitizer, and content extraction in one library
      - Article extraction only; delegates parsing to Readability.js or html5lib/BeautifulSoup
    - - Feature breadth
      - Scored article plus ``title``, ``byline``, ``date``, ``description``, ``lang``, and the scored ``element``
      - Readability path yields the full JSON record; Python fallback does not score content at all
    - - Performance
      - One in-process C pass; no subprocess
      - Readability path forks a Node.js process per document; Python path parses with html5lib
    - - Typing
      - Typed API; :class:`~turbohtml.Article` is a ``NamedTuple``
      - Returns untyped ``dict`` records
    - - Dependencies
      - Self-contained C extension, no runtime dependencies
      - Needs Node.js + Readability.js for the fast path; html5lib, BeautifulSoup, regex for the fallback
    - - Maintenance
      - Actively developed alongside the parser
      - Thin wrapper tracking upstream Readability.js

Feature overlap
===============

The scored-extraction surface ports one to one:

- ``simple_json_from_html_string(html, use_readability=True)`` maps to :meth:`~turbohtml.Node.article` on a parsed
  document.
- ``result["title"]`` -> ``article().title``.
- ``result["byline"]`` -> ``article().byline``.
- ``result["date"]`` -> ``article().date``.
- ``result["plain_text"]`` (the list of body text blocks) -> ``article().text``, the scored body as layout-aware plain
  text.
- ``result["content"]`` / ``result["plain_content"]`` -> ``article().element``, whose :attr:`~turbohtml.Node.html` is
  the markup.
- ``simple_tree_from_html_string(html)`` -> :func:`turbohtml.parse`.

What turbohtml adds
===================

- No Node.js runtime and no subprocess: scoring runs in-process in C, so extraction works the same whether or not Node
  is installed.
- Two extra metadata fields beside the JSON's set: ``article().description`` and ``article().lang``.
- The scored ``element`` is a live :class:`~turbohtml.Node`, so you can walk, query, or re-serialize it rather than
  receive a fixed HTML string. Pair it with :class:`~turbohtml.clean.Sanitizer` when you need the markup scrubbed.
- A full HTML5 parser, serializer, and CSS-selector engine in the same library, so extraction is one call inside a
  document you already have parsed.
- :func:`turbohtml.extract.boilerplate` returns one classified :class:`~turbohtml.extract.Paragraph` per block unit, a
  closer analog to the JSON's per-block ``plain_text`` list than an unclassified text chunk.

What readabilipy has that turbohtml does not
============================================

- **Bit-for-bit Readability.js output.** readabilipy's fast path is Mozilla's reference extractor, so its scoring
  matches Firefox Reader View exactly. turbohtml scores independently and can disagree on borderline pages. If you must
  reproduce Readability.js decisions, keep readabilipy for that path; there is no exact-parity mode in turbohtml.
- **The article as a JSON dict.** readabilipy returns a plain ``dict`` ready to serialize. turbohtml returns a typed
  :class:`~turbohtml.Article`; build the dict yourself with ``art._asdict()`` (dropping or serializing ``element``).

Performance
===========

Extracting from a full page -- navigation, a scored article, and a footer. :meth:`~turbohtml.Node.article` scores and
harvests in one C pass; readabilipy's Python mode parses with html5lib into BeautifulSoup and cleans the whole page
without scoring it, so it does strictly less and still costs orders of magnitude more. Numbers vary with input and
hardware.

.. bench-table::
    :file: bench/readabilipy.json

****************
 How to migrate
****************

Swap the module-level call for a parse-then-extract pair:

.. code-block:: python

    # before
    from readabilipy import simple_json_from_html_string

    # after
    from turbohtml import parse

The API mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `readabilipy <https://readabilipy.readthedocs.io/>`__
      - turbohtml
    - - ``simple_json_from_html_string(html, use_readability=True)``
      - ``parse(html).article()``; the scoring runs in-process, no Node.js
    - - ``result["title"]``
      - ``article().title``
    - - ``result["byline"]``
      - ``article().byline``
    - - ``result["date"]``
      - ``article().date``
    - - ``result["plain_text"]`` (a list of text blocks)
      - ``article().text`` (the scored body) or :meth:`~turbohtml.Node.to_text` (the whole page)
    - - ``result["content"]`` / ``result["plain_content"]``
      - ``article().element`` -- its :attr:`~turbohtml.Node.html` is the markup; pair with
        :class:`~turbohtml.clean.Sanitizer` when you need it scrubbed
    - - ``simple_tree_from_html_string(html)``
      - :func:`turbohtml.parse`

.. testcode::

    from turbohtml import parse

    doc = parse(
        "<html lang=en><head><title>Comets</title></head>"
        "<body><nav><a href='/'>Home</a></nav><article class=post><h1>Comets</h1>"
        "<p>By <a rel=author href='/u'>Ada Lovelace</a></p>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "</article></body></html>"
    )
    art = doc.article()
    print(art.title, "|", art.byline, "|", art.element.tag)

.. testoutput::

    Comets | Ada Lovelace | article

**********************
 Gotchas and pitfalls
**********************

- readabilipy's pure-Python mode does not score content: its ``plain_text`` is the whole cleaned page, navigation and
  footer included. Ports that never had Node.js gain the actual article extraction by switching to
  :meth:`~turbohtml.Node.article`.
- ``article().title`` prefers the first ``<h1>``, then ``og:title``, then ``<title>``; readabilipy leans on the
  ``<title>`` tag and its separator splitting, so the two disagree on pages whose heading and title differ.
- ``article().date`` reports the value the page declares; readabilipy normalizes what it finds toward ISO form, so
  compare parsed dates, not strings.
- When nothing scores as content, ``article().element`` is ``None`` and ``article().text`` is empty; guard on it before
  reading :attr:`~turbohtml.Node.html`. readabilipy's Python fallback instead hands back the cleaned full page.
- The JSON's per-block ``plain_text`` list has a closer cousin in :func:`turbohtml.extract.boilerplate`, which returns
  one classified :class:`~turbohtml.extract.Paragraph` per block unit instead of unclassified text chunks.
