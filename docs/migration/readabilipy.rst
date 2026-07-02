##################
 From readabilipy
##################

.. package-meta:: readabilipy alan-turing-institute/ReadabiliPy

`readabilipy <https://readabilipy.readthedocs.io>`_ wraps Mozilla's Readability.js: with Node.js available,
``simple_json_from_html_string(html, use_readability=True)`` shells out to the reference article extractor and returns a
JSON record (``title``, ``byline``, ``date``, ``content``, ``plain_text``); without Node it falls back to a pure-Python
cleaner that keeps the whole page.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.article` runs the readability-style content scoring in-process, in one C pass over the parsed
tree, so you get the extraction Readability.js provides without a Node.js runtime in the pipeline. The
:class:`~turbohtml.Article` record carries the same fields the JSON does (``text``, ``title``, ``byline``, ``date``,
plus ``description`` and ``lang``), and the scored ``element`` replaces the ``content`` HTML.

Extracting from a full page -- navigation, a scored article, and a footer. :meth:`~turbohtml.Node.article` scores and
harvests in one C pass; readabilipy's Python mode parses with html5lib into BeautifulSoup and cleans the whole page
without scoring it, so it does strictly less and still costs orders of magnitude more. Numbers vary with input and
hardware.

.. bench-table::
    :file: bench/readabilipy.json

*************
 The renames
*************

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

**********
 Pitfalls
**********

- readabilipy's pure-Python mode does not score content: its ``plain_text`` is the whole cleaned page, navigation and
  footer included. Ports that never had Node.js gain the actual article extraction by switching to
  :meth:`~turbohtml.Node.article`.
- ``article().title`` prefers the first ``<h1>``, then ``og:title``, then ``<title>``; readabilipy leans on the
  ``<title>`` tag and its separator splitting, so the two disagree on pages whose heading and title differ.
- ``article().date`` reports the value the page declares; readabilipy normalizes what it finds toward ISO form, so
  compare parsed dates, not strings.
- The JSON's per-block ``plain_text`` list has a closer cousin in :func:`turbohtml.extract.boilerplate`, which returns
  one classified :class:`~turbohtml.extract.Paragraph` per block unit instead of unclassified text chunks.
