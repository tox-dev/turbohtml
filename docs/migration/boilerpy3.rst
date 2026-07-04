################
 From boilerpy3
################

.. package-meta:: boilerpy3 jmriebold/BoilerPy3

`boilerpy3 <https://github.com/jmriebold/BoilerPy3>`_ is the pure-Python port of boilerpipe, the original
boilerplate-removal library. An extractor (``ArticleExtractor``, ``CanolaExtractor``, ``NumWordsRulesExtractor``, ...)
parses a page with a built-in SAX handler, segments it into text blocks, classifies each block as content or boilerplate
from word counts and link densities, and returns either the surviving text or the classified blocks. It runs on the
standard library alone and is used to strip navigation, sidebars, and footers off article pages before indexing,
summarization, or NLP.

turbohtml covers that same ground with :func:`turbohtml.extract.boilerplate` and the C main-content scoring behind
:meth:`~turbohtml.Node.main_content`: the scoring picks the content body, and each paragraph unit comes back as a
:class:`~turbohtml.extract.Paragraph` carrying the same text-and-classification shape as boilerpy3's ``text_blocks``.
Because the extraction sits on top of a full WHATWG parser, the same page is also available as a DOM you can query and
serialize.

************************
 turbohtml vs boilerpy3
************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - boilerpy3
    - - Scope
      - Full WHATWG HTML parser with DOM, query, serialize, and content extraction on top
      - Boilerplate/content extraction only, over its own SAX pass
    - - Feature breadth
      - One C scoring model plus a threshold config (:class:`~turbohtml.extract.Extraction`) and article metadata
      - A family of tuned extractors (``ArticleExtractor``, ``ArticleSentencesExtractor``, ``LargestContentExtractor``,
        ``CanolaExtractor``, ...)
    - - Performance
      - C scoring pass over the parsed tree (see below)
      - Python classification over a Python SAX parse
    - - Typing
      - Fully annotated, ships PEP 561 stubs
      - Annotated pure-Python source
    - - Dependencies
      - Compiled C extension
      - Standard library only, no compiled extension
    - - Maintenance
      - Actively developed
      - Maintained port of the boilerpipe algorithm

Feature overlap
===============

The shared surface ports one call to one call:

- ``ArticleExtractor().get_content(html)`` -> :meth:`~turbohtml.Node.main_text` (or ``article().text``).
- ``get_doc(html).text_blocks`` -> :func:`extract.boilerplate(html) <turbohtml.extract.boilerplate>`, a list of
  :class:`~turbohtml.extract.Paragraph`.
- ``block.text`` -> ``paragraph.text``, the same whitespace-normalized visible text.
- ``block.is_content`` -> ``not paragraph.is_boilerplate``.
- ``block.link_density`` and ``block.num_words`` cutoffs -> the ``max_link_density`` and ``min_length`` fields of
  :class:`~turbohtml.extract.Extraction`.
- ``KeepEverythingExtractor().get_content(html)`` -> :meth:`~turbohtml.Node.to_text`, the full visible text.

What turbohtml adds
===================

- The extraction rides on a full WHATWG parse, so the same page is a DOM you can query, mutate, and serialize, not just
  a text stream.
- :meth:`~turbohtml.Node.article` harvests page metadata beside the body text: ``title``, ``byline``, ``date``,
  ``description``, and ``lang``. boilerpy3's ``get_doc(html).title`` is the only metadata it exposes, and usually
  ``None`` unless a title block was marked.
- :class:`~turbohtml.extract.Extraction` tunes the per-paragraph thresholds directly, including a
  :meth:`~turbohtml.extract.Extraction.justext` preset (a 70-character floor at 0.2 link density) and a
  ``keep_headings`` switch.
- :func:`turbohtml.parse` follows the WHATWG recovery rules and never raises on malformed markup, where boilerpy3 raises
  ``HTMLExtractionError`` unless you pass ``raise_on_failure=False``.

What boilerpy3 has that turbohtml does not
==========================================

- Several distinct, separately tuned extractor algorithms. ``ArticleExtractor`` maps to turbohtml's defaults,
  ``NumWordsRulesExtractor``-style floors to ``min_length``, and ``KeepEverythingExtractor`` to
  :meth:`~turbohtml.Node.to_text`, but there is no equivalent of ``CanolaExtractor``'s trained rule set or
  ``ArticleSentencesExtractor``'s sentence-level shaping.
- Multiple surviving content islands. boilerpy3 classifies each block from its neighbors, so two separate content
  regions both survive; turbohtml first picks the single best-scoring content body, and only units inside it can be
  content. If you need every island, walk :func:`~turbohtml.extract.boilerplate` output yourself.
- Fetch convenience: ``get_content_from_url(url)`` and ``get_content_from_file(path)``. turbohtml has no fetcher; read
  the page with ``urllib`` or ``httpx`` (or open the file) and pass the markup to :func:`~turbohtml.parse`.
- Pure-Python install with no compiled extension, which can matter on platforms without a prebuilt turbohtml wheel.

Performance
===========

turbohtml scores the tree in one C pass and classifies the units in a thin Python layer; boilerpy3 parses with its own
SAX handler and classifies each block in Python. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/boilerpy3.json

****************
 How to migrate
****************

Swap the extractor import for :func:`turbohtml.parse` and, when you want the classified blocks, the
:func:`turbohtml.extract.boilerplate` helper:

.. code-block:: python

    from turbohtml import parse
    from turbohtml.extract import boilerplate, Extraction

The call mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `boilerpy3 <https://github.com/jmriebold/BoilerPy3>`__
      - turbohtml
    - - ``ArticleExtractor().get_content(html)``
      - ``parse(html).main_text()`` (or ``article().text``)
    - - ``ArticleExtractor().get_doc(html).text_blocks``
      - :func:`extract.boilerplate(html) <turbohtml.extract.boilerplate>`
    - - ``block.text``
      - ``paragraph.text``, the same field on :class:`~turbohtml.extract.Paragraph`
    - - ``block.is_content``
      - ``not paragraph.is_boilerplate``
    - - ``block.link_density`` cutoffs
      - :class:`Extraction(max_link_density=...) <turbohtml.extract.Extraction>`
    - - ``block.num_words`` floors (``NumWordsRulesExtractor``)
      - :class:`Extraction(min_length=...) <turbohtml.extract.Extraction>`, a character floor
    - - ``get_doc(html).title``
      - ``parse(html).article().title``
    - - ``KeepEverythingExtractor().get_content(html)``
      - :meth:`~turbohtml.Node.to_text`, the full visible text
    - - ``get_content_from_url(url)``
      - fetch the page yourself (``urllib`` or ``httpx``), then parse the markup

Before and after, extracting the article body from a full page:

.. testcode::

    from turbohtml import parse

    page = (
        "<body><nav><ul><li><a href='/'>Home</a></li><li><a href='/faq'>FAQ</a></li></ul></nav>"
        "<article class='post'><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "</article></body>"
    )
    print(parse(page).main_text())

.. testoutput::

    Comets

    A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.

**********************
 Gotchas and pitfalls
**********************

- boilerpy3 segments at inline/block transitions of its SAX stream, so a navigation list, a heading, and the first
  paragraph can fuse into one block; turbohtml emits one :class:`~turbohtml.extract.Paragraph` per block element, so
  expect more, smaller units at the same total text.
- The ``min_length`` floor is a character count of normalized text, not a word count. boilerpy3's
  ``NumWordsRulesExtractor`` thresholds are word counts, so a direct number carries over only loosely; tune against your
  own pages.
- ``get_doc(html).title`` is usually ``None`` (it needs a marked title block); ``article().title`` harvests the first
  ``<h1>``, ``og:title``, or ``<title>`` instead.
- boilerpy3 raises ``HTMLExtractionError`` on markup its parser rejects unless ``raise_on_failure=False``;
  :func:`turbohtml.parse` follows the WHATWG recovery rules and never raises on malformed input.
