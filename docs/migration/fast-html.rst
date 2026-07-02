################
 From fast-html
################

.. package-meta:: fast-html pcarbonn/fast_html

`fast-html <https://github.com/pcarbonn/fast_html>`_ assembles HTML in Python from the other direction than a parser:
each tag function takes its children first (one node or a list) and attributes as trailing keywords, yields the markup
lazily as a generator of string fragments, and ``render`` joins them. turbohtml replaces it with the terse
:data:`turbohtml.build.E` builder.

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

``E`` assembles the fragment in turbohtml's arena and serializes it in C; fast-html stays in Python. The same ``<ul>``
of rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. bench-table::
    :file: bench/fast-html.json

``E`` is about twice as fast as fast-html, and the decisive difference is the result type: ``E`` hands back a real
:class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree you can query, edit,
and re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

fast-html takes children first and attributes last; turbohtml leads with the attributes:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `fast-html <https://github.com/pcarbonn/fast_html>`__
      - turbohtml
    - - ``render(div([h1("Title"), p("body")], class_="card"))``
      - :data:`E.div({"class": "card"}, E.h1("Title"), E.p("body")) <turbohtml.build.E>` ``.serialize()``; children are
        plain arguments, no list wrapper
    - - ``li("text", class_="item", data_i="1")``
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
- fast-html mangles keyword arguments (``class_`` to ``class``, ``data_i`` to ``data-i``); ``E`` takes a plain mapping,
  so write the real attribute name as the dict key -- no underscore convention to remember.
- fast-html never escapes: ``render(div("<b>"))`` emits the markup verbatim. ``E`` builds text nodes, so the same call
  serializes as ``&lt;b&gt;``; markup belongs in child elements, not strings.
- A fast-html tag is a generator, consumed by the ``render`` that joins it; an :class:`~turbohtml.Element` is a tree you
  can serialize as many times as you like.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
