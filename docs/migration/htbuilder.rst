################
 From htbuilder
################

.. package-meta:: htbuilder tvst/htbuilder

`htbuilder <https://github.com/tvst/htbuilder>`_ assembles HTML in Python from the other direction than a parser: each
element takes its attributes as keyword arguments in one call and its children in a second (``div(_class="card")(...)``,
with ``_class`` mangled to ``class`` and ``data_i`` to ``data-i``), and stringifying the result renders it. turbohtml
replaces it with the terse :data:`turbohtml.build.E` builder.

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

``E`` assembles the fragment in turbohtml's arena and serializes it in C; htbuilder stays in Python. The same ``<ul>``
of rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. bench-table::
    :file: bench/htbuilder.json

``E`` is about one and a half times faster than htbuilder, and the decisive difference is the result type: ``E`` hands
back a real :class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree you can
query, edit, and re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

htbuilder splits an element across two calls, attributes first and children second; turbohtml passes both to one call:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `htbuilder <https://github.com/tvst/htbuilder>`__
      - turbohtml
    - - ``div(_class="card")(h1("Title"))``
      - :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`; attributes are a leading mapping, children
        follow in the same call
    - - ``li(_class="item", data_i="1")("text")``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`
    - - ``classes("card", "lg")`` / ``styles(...)`` helpers
      - a plain string, or a list value that joins on a space: ``{"class": ["card", "lg"]}``

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
- htbuilder mangles keyword arguments (``_class`` to ``class``, ``data_i`` to ``data-i``); ``E`` takes a plain mapping,
  so write the real attribute name as the dict key -- no underscore convention to remember.
- htbuilder renders lazily, escaping nothing: ``str(div("<b>"))`` emits the ``<b>`` markup verbatim. ``E`` builds text
  nodes, so the same call serializes as ``&lt;b&gt;``; markup belongs in child elements, not strings.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
