########################
 From the HTML builders
########################

.. image:: https://static.pepy.tech/badge/dominate
    :alt: dominate downloads
    :target: https://pepy.tech/project/dominate

The HTML *generators* -- `dominate <https://github.com/Knio/dominate>`_, `yattag <https://www.yattag.org>`_, `htpy
<https://htpy.dev>`_, `airium <https://gitlab.com/kamichal/airium>`_, and `lxml.builder
<https://lxml.de/api/lxml.builder.html>`_'s ``E`` -- all do the same job from the other direction than a parser: they
assemble a tree in code and stringify it. They differ mainly in how they spell nesting, from dominate's ``with`` blocks
to htpy's ``[...]`` subscripts to ``lxml.builder``'s nested calls.

***************
 Why turbohtml
***************

turbohtml builds HTML with :class:`~turbohtml.Element` plus :meth:`~turbohtml.Node.serialize`, and
:data:`turbohtml.build.E` is a terse front end for it that reads like ``lxml.builder``: ``E.<tag>(attrs, *children)``,
where a leading mapping is the attributes and each child is a node or a string that becomes text. The result is a real
turbohtml tree, so the whole edit, query, and serialize surface stays available and the markup you generate serializes
by exactly the rules that parse it back:

.. testcode::

    from turbohtml.build import E

    card = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    print(card.serialize())

.. testoutput::

    <div class="card"><h1>Title</h1><p>body</p></div>

*************
 The mapping
*************

The builders differ mainly in how they spell nesting; the translation is mechanical:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - generator
      - turbohtml
    - - ``lxml.builder``'s ``E.div(E.p("x"))``
      - :data:`E.div(E.p("x")) <turbohtml.build.E>`, the same factory shape
    - - dominate's ``div(p("x"), cls="card")`` / ``with`` blocks
      - ``E.div({"class": "card"}, E.p("x"))``; nest by passing children, not a context manager
    - - yattag's ``doc, tag, text`` and ``with tag("div"):``
      - ``E.div(...)`` returns the element; build children inline instead of opening a tag scope
    - - htpy's ``div(".card")[h1("Title")]``
      - ``E.div({"class": "card"}, E.h1("Title"))``; attributes are a mapping, children are arguments
    - - airium's ``a("div", klass="card")`` with indentation tracking
      - ``E.div({"class": "card"}, ...)``; turbohtml tracks structure in the tree, not by call depth

``E("tag", ...)`` is the call form for a tag that is not a Python identifier (a custom element, say), and a list-valued
attribute joins on a space so a class list reads naturally:

.. testcode::

    from turbohtml.build import E

    print(E("my-card", {"class": ["card", "lg"]}, "hi").serialize())

.. testoutput::

    <my-card class="card lg">hi</my-card>

**********
 Pitfalls
**********

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype.
  Serialize the element you built, or append it under a parsed document when you need the full page shell.
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``).
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
