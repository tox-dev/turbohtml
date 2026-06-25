#################################
 Extract content and drop chrome
#################################

*****************************
 Get the article in one call
*****************************

:meth:`~turbohtml.Node.article` scores the page and returns the content body plus the metadata harvested beside it --
title, byline, date, description, and language -- as one :class:`~turbohtml.Article` record. Read
:attr:`~turbohtml.Article.text` for the plain-text body, or :attr:`~turbohtml.Article.element` for the live element:

.. testcode::

    doc = parse(
        "<html lang=en><head><title>Comets</title></head>"
        "<body><nav><a href='/'>Home</a></nav>"
        "<article class=post><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a glowing tail as it nears the Sun.</p>"
        "</article></body></html>"
    )
    art = doc.article()
    print(art.title, "|", art.element.tag)

.. testoutput::

    Comets | article

**********************************************
 Label every block as content or boilerplate
**********************************************

When you need the finer ``justext`` / ``boilerpy3`` view -- a verdict for *every* block of text, not just the single
winning element -- use :func:`turbohtml.extract.boilerplate`. It reuses the same content scoring to find the article
region, then returns one :class:`~turbohtml.extract.Paragraph` per leaf text block, each carrying ``is_boilerplate`` and
``is_heading``:

.. testcode::

    from turbohtml.extract import boilerplate

    html = (
        "<article class=post><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a glowing tail as it nears the Sun.</p>"
        "</article>"
        "<footer><p>Copyright notice, all rights reserved here forever and ever.</p></footer>"
    )
    for para in boilerplate(html):
        kind = "boilerplate" if para.is_boilerplate else "content"
        print(kind, "|", para.text[:24])

.. testoutput::

    content | Comets
    content | A comet is an icy body t
    boilerplate | Copyright notice, all ri

Keep only the article text by filtering on the flag, dropping headings too if you want body prose alone:

.. testcode::

    body = "\n".join(p.text for p in boilerplate(html) if not p.is_boilerplate and not p.is_heading)
    print(body)

.. testoutput::

    A comet is an icy body that releases gas, forming a glowing tail as it nears the Sun.

************************
 Tune the thresholds
************************

Pass an :class:`~turbohtml.extract.Extraction` config to change how strict the verdict is: ``min_text_length`` sets the
shortest block that can count as content, ``max_link_density`` the largest fraction of a block that may sit inside links
before it reads as a menu, and ``headings_are_content`` whether to keep section titles. The
:meth:`~turbohtml.extract.Extraction.justext` and :meth:`~turbohtml.extract.Extraction.goose3` presets approximate those
libraries' stock settings:

.. testcode::

    from turbohtml.extract import Extraction

    mixed = (
        "<article class=post>"
        "<p>A comet is an icy body that releases gas, forming a glowing tail as it nears the Sun.</p>"
        "<p>See the full photo gallery in the section below.</p>"
        "</article>"
    )
    default_kept = [p.text for p in boilerplate(mixed) if not p.is_boilerplate]
    justext_kept = [p.text for p in boilerplate(mixed, Extraction.justext()) if not p.is_boilerplate]
    print(len(default_kept), "->", len(justext_kept))

.. testoutput::

    2 -> 1

The scoring this builds on is described in :doc:`/explanation/main-content`.
