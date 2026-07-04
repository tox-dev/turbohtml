##############
 From justext
##############

.. package-meta:: justext miso-belica/jusText

`justext <https://github.com/miso-belica/jusText>`_ removes boilerplate from a web page paragraph by paragraph. It
parses the page with lxml, segments the document at block-tag boundaries, and classifies each paragraph from its length,
link density, and the density of stopwords for a chosen language, then runs a context-sensitive revision pass that
reclassifies borderline paragraphs from their neighbors. It returns the paragraph objects rather than an assembled
article, so callers keep the per-paragraph classification and diagnostics. It is a common front end for search indexing,
corpus building, and NLP pipelines that need clean prose off article pages.

turbohtml covers that same per-paragraph question with :func:`turbohtml.extract.boilerplate` on top of the C
main-content scoring behind :meth:`~turbohtml.Node.main_content`: the scoring picks the content body, every unit outside
it is boilerplate, and a unit inside it must still clear the length and link-density thresholds of an
:class:`~turbohtml.extract.Extraction` config. Each unit comes back as a :class:`~turbohtml.extract.Paragraph` with
justext's ``text`` / ``is_boilerplate`` / ``is_heading`` shape, and because the scoring reads structure and prose
density it needs no stoplist and no language input.

**********************
 turbohtml vs justext
**********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - justext
    - - Scope
      - Full WHATWG HTML parser with DOM, query, serialize, and content extraction on top
      - Per-paragraph boilerplate classification only, over an lxml parse
    - - Feature breadth
      - One C scoring model plus a threshold config (:class:`~turbohtml.extract.Extraction`) and article metadata
      - Length, link-density, and per-language stopword-density classifier with a context-sensitive revision pass
    - - Performance
      - C scoring pass over the parsed tree (see below)
      - Python classification over an lxml parse
    - - Typing
      - Fully annotated, ships PEP 561 stubs
      - Pure-Python source, no shipped type stubs
    - - Dependencies
      - Compiled C extension
      - lxml plus the bundled per-language stoplists
    - - Maintenance
      - Actively developed
      - Maintained by its author

Feature overlap
===============

The shared surface ports one call to one call:

- ``justext.justext(html, get_stoplist("English"))`` -> :func:`extract.boilerplate(html)
  <turbohtml.extract.boilerplate>`, a list of :class:`~turbohtml.extract.Paragraph`; no stoplist argument.
- ``paragraph.text`` -> ``paragraph.text``, the same whitespace-normalized visible text.
- ``paragraph.is_boilerplate`` -> ``paragraph.is_boilerplate``.
- ``paragraph.is_heading`` -> ``paragraph.is_heading``.
- ``length_low`` and ``max_link_density`` cutoffs -> the ``min_length`` and ``max_link_density`` fields of
  :class:`~turbohtml.extract.Extraction`, with :meth:`~turbohtml.extract.Extraction.justext` carrying justext's
  70-character floor and 0.2 link density.
- ``no_headings=True`` -> :class:`Extraction(keep_headings=False) <turbohtml.extract.Extraction>`.
- joining the good paragraphs into the article text -> :meth:`~turbohtml.Node.main_text` (or ``article().text``), the
  one-call form.

What turbohtml adds
===================

- The classification is language-independent: the scoring reads structure and prose density, so ports drop the
  ``get_stoplist`` call and every language and stopword-threshold argument. Pages in any language classify the same way.
- The extraction rides on a full WHATWG parse, so the same page is a DOM you can query, mutate, and serialize, not just
  a paragraph stream.
- :meth:`~turbohtml.Node.article` harvests page metadata beside the body text: ``title``, ``byline``, ``date``,
  ``description``, and ``lang``. justext returns paragraphs only.
- :meth:`~turbohtml.Node.main_text` assembles the surviving prose in one call, skipping the per-paragraph records when
  you only want the article body.
- :func:`turbohtml.parse` follows the WHATWG recovery rules over any malformed markup rather than lxml's HTML parser.

What justext has that turbohtml does not
========================================

- Per-language stopword density. justext ships stoplists (``get_stoplist("English")``, ``get_stoplists()``) and scores
  paragraphs partly on stopword ratio; turbohtml has no stopword signal at all, so the two disagree on prose that is
  short but stopword-rich. No equivalent, by design the scoring is structural.
- Intermediate classes. justext labels each paragraph ``good`` / ``bad`` / ``short`` / ``neargood`` (via
  ``paragraph.class_type`` and the context-free ``cf_class``); turbohtml's ``is_boilerplate`` is a final yes or no.
  There is no equivalent of the ``short`` and ``neargood`` middle states.
- The context-sensitive revision pass. justext reclassifies ``neargood`` and ``short`` paragraphs from their neighbors
  within ``max_heading_distance``; turbohtml classifies each unit against the single scored content body with no
  neighbor pass. No equivalent.
- Per-paragraph diagnostics: ``paragraph.xpath`` / ``dom_path``, ``word_count``, ``stopwords_density``, and
  ``links_density``. :class:`~turbohtml.extract.Paragraph` carries only ``text``, ``is_boilerplate``, and
  ``is_heading``; query the parsed DOM yourself for anything more.

Performance
===========

turbohtml scores the tree in one C pass and classifies the units in a thin Python layer; justext builds an lxml tree,
then segments and scores each paragraph in Python. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/justext.json

****************
 How to migrate
****************

Swap the ``justext`` import for the :func:`turbohtml.extract.boilerplate` helper and, when you want to tune the
thresholds, :class:`~turbohtml.extract.Extraction`:

.. code-block:: python

    from turbohtml.extract import boilerplate, Extraction

The call mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `justext <https://github.com/miso-belica/jusText>`__
      - turbohtml
    - - ``justext.justext(html, get_stoplist("English"))``
      - :func:`extract.boilerplate(html) <turbohtml.extract.boilerplate>`; no stoplist, the scoring is
        language-independent
    - - ``paragraph.text``
      - ``paragraph.text``, the same field on :class:`~turbohtml.extract.Paragraph`
    - - ``paragraph.is_boilerplate``
      - ``paragraph.is_boilerplate``
    - - ``paragraph.is_heading``
      - ``paragraph.is_heading``
    - - ``justext(html, stoplist, length_low=70, max_link_density=0.2)``
      - ``boilerplate(html, Extraction.justext())`` -- the preset carries justext's two structural defaults
    - - ``justext(html, stoplist, no_headings=True)``
      - ``boilerplate(html, Extraction(keep_headings=False))``
    - - joining the good paragraphs into the article text
      - ``doc.main_text()`` (or ``article().text``), the one-call form

Before and after, classifying every paragraph of a full page -- navigation, a scored article, and a footer:

.. testcode::

    from turbohtml.extract import boilerplate

    page = (
        "<body><nav><ul><li><a href='/'>Home</a></li><li><a href='/faq'>FAQ</a></li></ul></nav>"
        "<article class='post'><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>Share this!</p>"
        "</article></body>"
    )
    for paragraph in boilerplate(page):
        print(paragraph.is_boilerplate, paragraph.is_heading, paragraph.text)

.. testoutput::

    True False Home
    True False FAQ
    False True Comets
    False False A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.
    True False Share this!

**********************
 Gotchas and pitfalls
**********************

- There is no stoplist and no stopword thresholds (``stopwords_low`` / ``stopwords_high``): turbohtml classifies from
  the content scoring plus length and link density, so ports drop the ``get_stoplist`` call and any language plumbing.
- justext has no notion of *where* the article is -- each paragraph stands alone -- while turbohtml first picks the
  content body and only paragraphs inside it can be good. On a page with several disjoint content islands, only the
  best-scoring island survives.
- There are no ``neargood`` / ``short`` intermediate classes and no context-sensitive revision pass: ``is_boilerplate``
  is a final yes or no, so paragraphs justext would rescue from a good neighbor stay boilerplate here.
- justext's defaults are tuned stricter than turbohtml's. Pass :meth:`Extraction.justext()
  <turbohtml.extract.Extraction.justext>` to keep its 70-character floor and 0.2 link density when you want output close
  to a justext port, or stay with the defaults to keep shorter prose paragraphs.
- ``min_length`` is a character count of normalized text, not the word count justext's ``length_low`` implies, so a
  direct number carries over only loosely; tune it against your own pages.
- justext decodes bytes through ``encoding`` / ``default_encoding`` / ``enc_errors``.
  :func:`turbohtml.extract.boilerplate` takes a decoded ``str``; when you hold bytes of unknown encoding, decode them
  with :func:`turbohtml.parse` under ``detect_encoding=True`` (or an explicit ``encoding=``) and read the body through
  :meth:`~turbohtml.Node.main_text` or :meth:`~turbohtml.Node.article`, which run on the parsed document.
