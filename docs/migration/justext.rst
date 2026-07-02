##############
 From justext
##############

.. package-meta:: justext miso-belica/jusText

`justext <https://github.com/miso-belica/jusText>`_ removes boilerplate from a web page paragraph by paragraph: it
segments the document at block-tag boundaries and classifies each paragraph good or bad from its length, link density,
and the density of stopwords for a chosen language, returning the paragraph objects rather than an assembled article.

***************
 Why turbohtml
***************

:func:`turbohtml.extract.boilerplate` answers the same per-paragraph question over the C main-content scoring
:meth:`~turbohtml.Node.main_content` runs: the scoring picks the content body, every paragraph unit outside it is
boilerplate, and a unit inside it must still clear the length and link-density thresholds of an
:class:`~turbohtml.extract.Extraction` config. The result is one :class:`~turbohtml.extract.Paragraph` per unit with
justext's ``text`` / ``is_boilerplate`` / ``is_heading`` shape, and no stoplist to pick: the scoring reads structure and
prose density, so it needs no language input. When you only want the surviving text, :meth:`~turbohtml.Node.main_text`
skips the per-paragraph records entirely.

Classifying every paragraph of a full page -- navigation, a scored article, and a footer. turbohtml scores the tree in
one C pass and classifies the units in a thin Python layer; justext builds an lxml tree, then segments and scores each
paragraph in Python. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/justext.json

*************
 The renames
*************

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

**********
 Pitfalls
**********

- There is no stoplist and no stopword thresholds (``stopwords_low`` / ``stopwords_high``): turbohtml classifies from
  the content scoring plus length and link density, so ports drop the ``get_stoplist`` call and any language plumbing.
  Pages in any language classify the same way.
- justext has no notion of *where* the article is -- each paragraph stands alone -- while turbohtml first picks the
  content body and only paragraphs inside it can be good. On a page with several disjoint content islands, only the
  best-scoring island survives.
- There are no ``neargood`` / ``short`` intermediate classes and no context-sensitive revision pass: ``is_boilerplate``
  is a final yes or no.
- justext's defaults are tuned stricter than turbohtml's: pass :meth:`Extraction.justext()
  <turbohtml.extract.Extraction.justext>` to keep its 70-character floor and 0.2 link density when you want output close
  to a justext port, or stay with the defaults to keep shorter prose paragraphs.
