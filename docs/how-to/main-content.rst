##########################
 Extract the main article
##########################

Isolate the article from the navigation, sidebars, and boilerplate with :meth:`~turbohtml.Node.main_content`, pull it
with its metadata, and classify paragraphs one at a time.

**************************
 Extract the main article
**************************

:meth:`~turbohtml.Node.main_content` returns the dominant content element (the article body with the navigation,
sidebars, advertising and comment boilerplate scored out), so you can work on just the prose. It is the role
`resiliparse <https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ fills with its main-content extractor.
:meth:`~turbohtml.Node.main_text` is the shortcut that renders that element with :meth:`~turbohtml.Node.to_text`:

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<body>"
        "<nav><a href='/'>Home</a> <a href='/about'>About</a></nav>"
        "<article class='post'><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>The tail always points away from the Sun, pushed out by the solar wind and radiation.</p>"
        "</article>"
        "<aside class='sidebar'><p>Related: meteors, asteroids, the Oort cloud, and more links here.</p></aside>"
        "</body>"
    )
    print(page.main_content().tag)
    print(page.main_text())

.. testoutput::

    article
    Comets

    A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.

    The tail always points away from the Sun, pushed out by the solar wind and radiation.

The score is a content-density heuristic: long paragraphs with prose punctuation raise their container, while a class or
id like ``sidebar``, ``comment`` or ``nav`` lowers it or drops the subtree outright. A page with no real article (only
short snippets or pure navigation) yields ``None`` from ``main_content`` and ``""`` from ``main_text``, so guard the
result:

.. testcode::

    stub = turbohtml.parse("<nav><a href='/'>Home</a></nav>")
    print(stub.main_content())

.. testoutput::

    None

***********************************
 Extract the article with metadata
***********************************

:meth:`~turbohtml.Node.article` returns an :class:`~turbohtml.Article` record: the scored content element and its plain
text, plus the page metadata harvested beside it -- ``title``, ``byline``, ``date``, ``description`` and ``lang``. This
is the one call that replaces trafilatura or newspaper3k, and folds in the publication-date lookup (the htmldate use
case):

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<html lang='en'>"
        "<head><title>Comets — Astronomy Today</title>"
        "<meta property='og:description' content='A short guide to comets and their tails.'>"
        "<meta property='article:published_time' content='2024-05-06'></head>"
        "<body><article class='post'>"
        "<h1>Comets</h1>"
        "<p>By <a rel='author' href='/u/ada'>Ada Lovelace</a></p>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>The tail always points away from the Sun, pushed out by the solar wind and radiation.</p>"
        "</article></body></html>"
    )
    art = page.article()
    print(art.title)
    print(art.byline)
    print(art.date)
    print(art.description)
    print(art.lang)
    print(art.element.tag)

.. testoutput::

    Comets
    Ada Lovelace
    2024-05-06
    A short guide to comets and their tails.
    en
    article

Each field is harvested from the first source that supplies it, so a partial page still yields what it can. ``title``
prefers the first ``<h1>``, then ``og:title``, then ``<title>``; ``byline`` a ``rel="author"`` link, then a ``author``
meta, then ``article:author``; ``date`` a ``<time>`` (its ``datetime`` or text), then ``article:published_time``, then a
common date meta; ``description`` ``og:description`` then a ``description`` meta; and ``lang`` the ``<html lang>``
attribute. A field with no source is ``None``, and a page with no article body leaves ``element`` ``None`` and ``text``
empty while the metadata is still filled:

.. testcode::

    bare = turbohtml.parse("<html lang='fr'><head><title>Sommaire</title></head><body><p>x</p></body></html>")
    art = bare.article()
    print(art.element, repr(art.text), art.title, art.lang)

.. testoutput::

    None '' Sommaire fr

**********************************
 Classify paragraphs individually
**********************************

:func:`turbohtml.extract.boilerplate` gives the per-paragraph view of the same scoring: it segments the page into
paragraph units and marks each one good or boilerplate, the call shape `justext
<https://github.com/miso-belica/jusText>`_ and `boilerpy3 <https://github.com/jmriebold/BoilerPy3>`_ expose. Units
outside the content body are boilerplate; units inside it must still clear the length and link-density thresholds a
:class:`~turbohtml.extract.Extraction` config carries:

.. testcode::

    from turbohtml.extract import Extraction, boilerplate

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

The good paragraphs concatenate to the article, so ``"\n".join(p.text for p in boilerplate(page) if not
p.is_boilerplate)`` is the justext extraction idiom. ``Extraction.justext()`` mirrors justext's stricter defaults (a
70-character floor and 0.2 link density), and ``Extraction(keep_headings=False)`` subjects headings to the length floor
like any prose, justext's ``no_headings`` mode.

To turn those spans into something printable, pass the returned ``(text, labels)`` pair to one of the two output
processors. :func:`turbohtml.annotation_surface` groups each label's matched substrings into a dict, in document order,
the surface forms an NLP or information-extraction pipeline consumes:

.. testcode::

    import turbohtml

    text, labels = turbohtml.parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text({
        "h1": ["heading"],
        "b": ["metric"],
    })
    print(turbohtml.annotation_surface(text, labels))

.. testoutput::

    {'heading': ['Q3'], 'metric': ['12%']}

:func:`turbohtml.annotation_tags` weaves the spans back into the text as inline ``<label>...</label>`` markup. The
innermost span always closes first, so properly nested spans stay well-formed:

.. testcode::

    text, labels = turbohtml.parse("<p>a <b><i>both</i></b> c</p>").to_annotated_text({"b": ["bold"], "i": ["italic"]})
    print(turbohtml.annotation_tags(text, labels))

.. testoutput::

    a <italic><bold>both</bold></italic> c
