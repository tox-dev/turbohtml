###########
 From htpy
###########

.. image:: https://static.pepy.tech/badge/htpy/month
    :alt: htpy monthly downloads
    :target: https://pepy.tech/project/htpy

`htpy <https://htpy.dev>`_ assembles HTML in Python from the other direction than a parser: each element is an object
whose attributes come from a call (``li(class_="item")``) and whose children sit in a ``[...]`` subscript (``ul[li("a"),
li("b")]``), and stringifying the root renders the tree. turbohtml replaces it with the terse :data:`turbohtml.build.E`
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

``E`` assembles the fragment in turbohtml's arena and serializes it in C; htpy stays in Python. The same ``<ul>`` of
rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways, from ``tox -e bench build-e`` on the
reference machine in :doc:`/development/performance`:

.. list-table::
    :header-rows: 1
    :widths: 34 33 33

    - - build a list
      - :data:`E <turbohtml.build.E>`
      - htpy
    - - 100 rows
      - 139 µs
      - 361 µs (2.6x)
    - - 1000 rows
      - 1.41 ms
      - 3.73 ms (2.6x)

``E`` is about two and a half times faster than htpy, and the decisive difference is the result type: ``E`` hands back a
real :class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree you can query,
edit, and re-:meth:`~turbohtml.Node.serialize`.

*************
 The mapping
*************

htpy carries attributes in the call and children in a subscript; turbohtml passes both to one call:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - htpy
      - turbohtml
    - - ``div(".card")[h1("Title")]``
      - :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`; attributes are a mapping, children are
        arguments
    - - ``li(class_="item", data_i="1")["text"]``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`

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
- htpy spells attributes with keyword arguments (``class_``, ``data_i``); ``E`` takes a plain mapping, so a hyphenated
  name like ``data-i`` is written as the dict key directly rather than an underscore the builder rewrites.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
