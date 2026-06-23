################
 From html-text
################

.. image:: https://static.pepy.tech/badge/html-text/month
    :alt: html-text monthly downloads
    :target: https://pepy.tech/project/html-text

`html-text <https://github.com/zytedata/html-text>`_ (Zyte) pulls the visible text out of a page, optionally guessing
block layout from the tags. It builds an lxml tree and walks it in Python.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.to_text` is the same call in one fully type annotated C walk: ``extract_text`` maps to
:meth:`~turbohtml.Node.to_text`, and the raw word stream html-text returns with layout guessing off maps to the
:attr:`~turbohtml.Node.strings` / :attr:`~turbohtml.Node.stripped_strings` iterators (or the
:attr:`~turbohtml.Node.text` concatenation).

.. code-block:: python

    # html-text
    import html_text

    html_text.extract_text(html)  # layout-guessed visible text
    html_text.extract_text(html, guess_layout=False)  # collapsed word stream

    # turbohtml
    import turbohtml

    turbohtml.parse(html).to_text()  # layout-aware text
    " ".join(turbohtml.parse(html).stripped_strings)  # collapsed word stream

The same text benchmark that backs the :doc:`inscriptis <inscriptis>` comparison also runs html-text's ``extract_text``:
:meth:`~turbohtml.Node.to_text` walks the tree once in C, where html-text builds an lxml tree and collects its text in
Python.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - to text
      - turbohtml
      - html-text
      - speed-up
    - - article (2 KiB)
      - 7 µs
      - 102 µs
      - 14.5x
    - - table (4 KiB)
      - 28 µs
      - 258 µs
      - 9.2x
    - - word stream (2 KiB)
      - 7 µs
      - 101 µs
      - 14.0x

html-text skips the column-aligned table layout :meth:`~turbohtml.Node.to_text` renders, so its margin behind turbohtml
narrows on table-heavy input while staying near an order of magnitude. The ``word stream`` row turns layout guessing off
(``extract_text(guess_layout=False)``) against joining the :attr:`~turbohtml.Node.stripped_strings` iterator, the
collapsed visible text both return without layout.

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html-text
      - turbohtml
    - - ``extract_text(html)``
      - ``parse(html).to_text()``
    - - ``extract_text(html, guess_layout=False)``
      - ``" ".join(parse(html).stripped_strings)``
    - - the ``guess_layout`` toggle
      - :meth:`~turbohtml.Node.to_text`'s ``layout`` (``"strict"``/``"extended"``)
    - - the raw word list
      - :attr:`~turbohtml.Node.strings` / :attr:`~turbohtml.Node.stripped_strings`
    - - the parsel-bound ``cleaned_selector`` / ``selector_to_text``
      - out of scope -- see :doc:`parsel <parsel>`

.. testcode::

    doc = parse("<p>Hello <b>bold</b> world</p>")
    print(doc.to_text())
    print(list(doc.stripped_strings))

.. testoutput::

    Hello bold world
    ['Hello', 'bold', 'world']

**********
 Pitfalls
**********

- ``extract_text``'s ``guess_layout`` toggle corresponds to :meth:`~turbohtml.Node.to_text`'s ``layout``
  (``"extended"``/``"strict"``) profile; the :doc:`inscriptis <inscriptis>` page covers the full ``to_text`` option
  surface.
- For the collapsed word stream html-text returns with layout guessing off, join the
  :attr:`~turbohtml.Node.stripped_strings` iterator rather than reaching for ``to_text``.
- html-text's parsel-bound helpers (``cleaned_selector``, ``selector_to_text``) are out of scope; the :doc:`parsel
  <parsel>` page covers reading text off a selected node.
