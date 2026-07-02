################
 From boilerpy3
################

.. package-meta:: boilerpy3 jmriebold/BoilerPy3

`boilerpy3 <https://github.com/jmriebold/BoilerPy3>`_ is the Python port of boilerpipe, the original boilerplate-removal
library: an extractor (``ArticleExtractor``, ``CanolaExtractor``, ...) segments a page into text blocks, classifies each
block content or boilerplate from word counts and link densities, and returns either the surviving text or the
classified blocks.

***************
 Why turbohtml
***************

:func:`turbohtml.extract.boilerplate` answers the same per-block question over the C main-content scoring
:meth:`~turbohtml.Node.main_content` runs: the scoring picks the content body, every paragraph unit outside it is
boilerplate, and a unit inside it must still clear the length and link-density thresholds of an
:class:`~turbohtml.extract.Extraction` config. Each unit comes back a :class:`~turbohtml.extract.Paragraph`, the shape
of boilerpy3's ``text_blocks`` entries. When you only want the surviving text (boilerpy3's ``get_content``),
:meth:`~turbohtml.Node.main_text` is the one-call form, and :meth:`~turbohtml.Node.article` adds the metadata harvest
beside it.

Classifying every block of a full page -- navigation, a scored article, and a footer. turbohtml scores the tree in one C
pass and classifies the units in a thin Python layer; boilerpy3 parses with its own SAX handler and classifies each
block in Python. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/boilerpy3.json

*************
 The renames
*************

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

**********
 Pitfalls
**********

- boilerpy3 segments at inline/block transitions of its SAX stream, so a navigation list, a heading, and the first
  paragraph can fuse into one block; turbohtml emits one :class:`~turbohtml.extract.Paragraph` per block element, so
  expect more, smaller units at the same total text.
- The extractor family collapses to configuration: ``ArticleExtractor`` maps to the defaults,
  ``NumWordsRulesExtractor``-style floors to ``min_length``, and ``KeepEverythingExtractor`` to
  :meth:`~turbohtml.Node.to_text`. There is no equivalent of ``CanolaExtractor``'s trained rule set.
- boilerpy3 classifies each block from its neighbors, so two content islands both survive; turbohtml first picks the
  single best-scoring content body and only units inside it can be good.
- ``get_doc(html).title`` is usually ``None`` (it needs a marked title block); ``article().title`` harvests the first
  ``<h1>``, ``og:title``, or ``<title>`` instead.
- boilerpy3 raises ``HTMLExtractionError`` on markup its parser rejects unless ``raise_on_failure=False``;
  :func:`turbohtml.parse` follows the WHATWG recovery rules and never raises on malformed input.
