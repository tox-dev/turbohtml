#############
 From yattag
#############

.. image:: https://static.pepy.tech/badge/yattag/month
    :alt: yattag monthly downloads
    :target: https://pepy.tech/project/yattag

`yattag <https://www.yattag.org>`_ assembles HTML in Python from the other direction than a parser: you unpack ``doc,
tag, text = Doc().tagtext()`` and open each element as a ``with tag(...)`` block, calling ``text(...)`` for content,
then read the string back with ``doc.getvalue()``. turbohtml replaces it with the terse :data:`turbohtml.build.E`
builder.

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

``E`` assembles the fragment in turbohtml's arena and serializes it in C; yattag stays in Python. The same ``<ul>`` of
rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - build a list
      - :data:`E <turbohtml.build.E>`
      - yattag
    - - 100 rows
      - 139 µs
      - 145 µs (1.0x)
    - - 1000 rows
      - 1.41 ms
      - 1.50 ms (1.1x)

``E`` runs on par with yattag, and the decisive difference is the result type: ``E`` hands back a real
:class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree you can query, edit,
and re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

yattag opens a tag scope with a context manager; turbohtml builds children inline:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - yattag
      - turbohtml
    - - ``doc, tag, text`` with ``with tag("div"):`` and ``text("x")``
      - :data:`E.div("x") <turbohtml.build.E>` returns the element; build children inline instead of opening a tag scope
    - - ``with tag("div", ("class", "card")):``
      - :data:`E.div({"class": "card"}, ...) <turbohtml.build.E>`; attributes are a leading mapping

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
