###############
 From dominate
###############

.. image:: https://static.pepy.tech/badge/dominate/month
    :alt: dominate monthly downloads
    :target: https://pepy.tech/project/dominate

`dominate <https://github.com/Knio/dominate>`_ assembles HTML in Python from the other direction than a parser: you open
a tag as a ``with`` block and nest children inside it (or pass them as arguments), then render the tree to a string.
turbohtml replaces it with the terse :data:`turbohtml.build.E` builder.

***************
 Why turbohtml
***************

turbohtml builds HTML with :class:`~turbohtml.Element` plus :meth:`~turbohtml.Node.serialize`, and
:data:`turbohtml.build.E` is a terse front end for it: ``E.<tag>(attrs, *children)``, where a leading mapping is the
attributes and each child is a node or a string that becomes text. The result is a real turbohtml tree, so the whole
edit, query, and serialize surface stays available and the markup you generate serializes by exactly the rules that
parse it back:

.. testcode::

    from turbohtml.build import E

    card = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    print(card.serialize())

.. testoutput::

    <div class="card"><h1>Title</h1><p>body</p></div>

``E`` assembles the fragment in turbohtml's arena and serializes it in C; dominate stays in Python. The same ``<ul>`` of
rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - build a list
      - :data:`E <turbohtml.build.E>`
      - dominate
    - - 100 rows
      - 139 µs
      - 466 µs (3.3x)
    - - 1000 rows
      - 1.41 ms
      - 4.59 ms (3.3x)

``E`` is about three times faster than dominate, and the decisive difference is the result type: ``E`` hands back a real
:class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree you can query, edit,
and re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

dominate spells nesting with ``with`` blocks or positional children; the translation is mechanical:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - dominate
      - turbohtml
    - - ``div(p("x"), cls="card")`` / ``with`` blocks
      - :data:`E.div({"class": "card"}, E.p("x")) <turbohtml.build.E>`; nest by passing children, not a context manager
    - - ``tag(...)`` for a non-identifier tag name
      - :data:`E("tag", ...) <turbohtml.build.E>`, the call form

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
