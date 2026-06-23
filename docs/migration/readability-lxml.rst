#######################
 From readability-lxml
#######################

.. image:: https://static.pepy.tech/badge/readability-lxml/month
    :alt: readability-lxml monthly downloads
    :target: https://pepy.tech/project/readability-lxml

`readability-lxml <https://github.com/buriy/python-readability>`_ is a Python port of Arc90's Readability: a
``Document`` wraps the HTML, ``summary()`` returns the cleaned article markup, and ``short_title()`` the page title. It
is the content-density heuristic turbohtml's :meth:`~turbohtml.Node.main_content` implements directly.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.article` scores the content body the same way and returns it as an :class:`~turbohtml.Article`
record, adding the byline, date, description and language readability-lxml leaves to you. It selects an existing element
unchanged rather than rewriting the DOM into a cleaned fragment, so pair it with :class:`~turbohtml.sanitizer.Sanitizer`
when you need a scrubbed body.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - readability-lxml
      - turbohtml
    - - ``Document(html).summary()``
      - ``doc.article().element`` (:attr:`~turbohtml.Node.html` for its markup, or ``None``)
    - - ``Document(html).short_title()``
      - ``doc.article().title``
    - - the article's plain text
      - ``doc.article().text`` (or :meth:`~turbohtml.Node.main_text`)
    - - (no equivalent)
      - ``doc.article().byline``, ``.date``, ``.description``, ``.lang``

.. testcode::

    doc = parse(
        "<html lang=en><head><title>Comets</title></head>"
        "<body><nav><a href='/'>Home</a></nav>"
        "<article class=post><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>The tail always points away from the Sun, pushed out by the solar wind and radiation.</p>"
        "</article></body></html>"
    )
    art = doc.article()
    print(art.title, "|", art.element.tag)

.. testoutput::

    Comets | article

Scoring the content body of a full page -- navigation, a scored article, and a footer -- measured with ``tox -e bench
article`` on CPython 3.14 (release build, Apple M4, macOS 26). :meth:`~turbohtml.Node.article` runs the same
content-density heuristic in C and selects the live element; readability-lxml builds an lxml tree and rewrites it into a
cleaned summary fragment. Numbers vary with input and hardware.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - readability-lxml
      - speed-up
    - - post (4 KiB)
      - 23 µs
      - 1.26 ms
      - 55x
    - - longform (16 KiB)
      - 70 µs
      - 2.54 ms
      - 36x

**********
 Pitfalls
**********

- ``Document.summary()`` returns cleaned HTML; ``doc.article().element`` is the scored element from the live tree,
  unchanged. Read :attr:`~turbohtml.Node.html` for its markup, or sanitize it first for a scrubbed fragment.
- readability-lxml extracts only the body and title; the byline, date, description and language come for free in the
  same :class:`~turbohtml.Article` record.
- A page with no scoring article leaves ``element`` ``None`` and ``text`` empty while still filling the metadata, so
  branch on ``art.element`` rather than assuming a body.
