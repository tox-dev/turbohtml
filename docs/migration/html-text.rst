################
 From html-text
################

.. package-meta:: html-text zytedata/html-text

`html-text <https://github.com/zytedata/html-text>`_ (Zyte) extracts the visible text from a page. It strips
``<script>`` and ``<style>``, inserts newlines around block tags, and optionally guesses block layout so the output
reads like the rendered page rather than a raw token dump. Zyte ships it as a feature-extraction step for web scraping
and machine-learning pipelines, where the text feeds classifiers or search indexes. Under the hood it builds an lxml
tree and walks it in Python, with parsel-bound helpers for reading text off an already-selected node.

turbohtml covers the same ground with :meth:`~turbohtml.Node.to_text`: one fully type annotated C tree walk that renders
layout-aware plain text, plus the :attr:`~turbohtml.Node.strings` / :attr:`~turbohtml.Node.stripped_strings` iterators
for the collapsed word stream html-text returns with layout guessing off.

************************
 turbohtml vs html-text
************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - html-text
    - - Scope
      - Full WHATWG parser, DOM, CSS/XPath selection, and text/Markdown/HTML serialization
      - Visible-text extraction from an lxml tree
    - - Feature breadth
      - Layout profiles, link and image rendering, aligned tables, wrap width, bullet markers, annotated text
      - Layout guessing on/off, punctuation-space guessing, custom newline tag sets
    - - Performance
      - Single C tree walk (see :doc:`inscriptis <inscriptis>`-shared benchmark below)
      - lxml tree build plus a Python walk
    - - Typing
      - Fully annotated with shipped stubs
      - Untyped
    - - Dependencies
      - None (self-contained C extension)
      - lxml and parsel
    - - Maintenance
      - Active, broad HTML surface
      - Zyte-maintained, stable and narrow in scope

Feature overlap
===============

Port these 1:1:

- ``extract_text(html)`` (layout-guessed visible text) maps to ``parse(html).to_text()``.
- ``extract_text(html, guess_layout=False)`` (collapsed word stream) maps to ``" ".join(parse(html).stripped_strings)``.
- The raw word list html-text collects maps to the :attr:`~turbohtml.Node.strings` /
  :attr:`~turbohtml.Node.stripped_strings` iterators, or the :attr:`~turbohtml.Node.text` concatenation.

What turbohtml adds
===================

- Column-aligned table layout. :meth:`~turbohtml.Node.to_text` renders ``<table>`` as aligned columns; html-text emits
  the cells as a flat text run.
- A :class:`~turbohtml.PlainText` config that renders link targets (``links`` = ``"inline"`` / ``"footnote"``), image
  alt text (``images``), wrapped prose (``width``), and custom list markers (``bullet``), none of which html-text
  produces.
- Annotated text through :meth:`~turbohtml.Node.to_annotated_text`, pairing the text with labeled spans.
- WHATWG-conformant parsing of malformed markup, where html-text inherits lxml's non-spec recovery.
- No lxml or parsel dependency, full type annotations, and the surrounding DOM, selection, and serialization surface.

What html-text has that turbohtml does not
==========================================

- Parsel integration. ``cleaned_selector`` and ``selector_to_text`` read text off a ``parsel.Selector``. turbohtml has
  no parsel binding; select the node with turbohtml's own CSS/XPath and call :meth:`~turbohtml.Node.to_text` on it (see
  :doc:`parsel <parsel>`).
- Punctuation-space guessing on the word stream. html-text's ``guess_punct_space`` suppresses spaces before punctuation
  when joining inline text. Joining :attr:`~turbohtml.Node.stripped_strings` with a single space has no equivalent
  toggle; :meth:`~turbohtml.Node.to_text` handles inline concatenation correctly, so prefer it when the spacing matters.
- Per-tag newline control. html-text accepts ``newline_tags`` / ``double_newline_tags`` sets. turbohtml selects spacing
  through the ``layout`` profile (``"extended"`` / ``"strict"``) rather than a per-tag override; no equivalent for
  renaming which tags break lines.

Performance
===========

.. bench-table::
    :file: bench/html-text.json

The same text benchmark that backs the :doc:`inscriptis <inscriptis>` comparison also runs html-text's ``extract_text``:
:meth:`~turbohtml.Node.to_text` walks the tree once in C, where html-text builds an lxml tree and collects its text in
Python. html-text skips the column-aligned table layout :meth:`~turbohtml.Node.to_text` renders, so its margin behind
turbohtml narrows on table-heavy input but stays above 25x. The ``word stream`` row turns layout guessing off
(``extract_text(guess_layout=False)``) against joining the :attr:`~turbohtml.Node.stripped_strings` iterator, the
collapsed visible text both return without layout.

****************
 How to migrate
****************

Swap the import and the call:

.. code-block:: python

    # html-text
    import html_text

    html_text.extract_text(html)  # layout-guessed visible text
    html_text.extract_text(html, guess_layout=False)  # collapsed word stream

    # turbohtml
    import turbohtml

    turbohtml.parse(html).to_text()  # layout-aware text
    " ".join(turbohtml.parse(html).stripped_strings)  # collapsed word stream

API mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `html-text <https://github.com/zytedata/html-text>`__
      - turbohtml
    - - ``extract_text(html)``
      - ``parse(html).to_text()``
    - - ``extract_text(html, guess_layout=False)``
      - ``" ".join(parse(html).stripped_strings)``
    - - the ``guess_layout`` toggle
      - a :class:`~turbohtml.PlainText` config's ``layout`` (``"strict"`` / ``"extended"``)
    - - the raw word list
      - :attr:`~turbohtml.Node.strings` / :attr:`~turbohtml.Node.stripped_strings`
    - - the parsel-bound ``cleaned_selector`` / ``selector_to_text``
      - out of scope; see :doc:`parsel <parsel>`

.. testcode::

    doc = parse("<p>Hello <b>bold</b> world</p>")
    print(doc.to_text())
    print(list(doc.stripped_strings))

.. testoutput::

    Hello bold world
    ['Hello', 'bold', 'world']

**********************
 Gotchas and pitfalls
**********************

- ``extract_text``'s ``guess_layout`` toggle maps to the ``layout`` (``"extended"`` / ``"strict"``) profile on a
  :class:`~turbohtml.PlainText` config passed to :meth:`~turbohtml.Node.to_text`; the :doc:`inscriptis <inscriptis>`
  page covers the full ``PlainText`` option surface.
- For the collapsed word stream html-text returns with layout guessing off, join the
  :attr:`~turbohtml.Node.stripped_strings` iterator rather than reaching for ``to_text``.
- Joining stripped strings puts a space between every token, including before punctuation. html-text's
  ``guess_punct_space`` avoids that; when the spacing matters, call :meth:`~turbohtml.Node.to_text`, which concatenates
  inline text without the spurious spaces.
- html-text emits table cells as a flat run, so its output differs from the column-aligned block
  :meth:`~turbohtml.Node.to_text` renders on table-heavy pages.
- html-text's parsel-bound helpers (``cleaned_selector``, ``selector_to_text``) are out of scope; the :doc:`parsel
  <parsel>` page covers reading text off a selected node.
