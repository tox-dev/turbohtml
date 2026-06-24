#############
 From airium
#############

.. image:: https://static.pepy.tech/badge/airium/month
    :alt: airium monthly downloads
    :target: https://pepy.tech/project/airium

`airium <https://gitlab.com/kamichal/airium>`_ assembles HTML in Python from the other direction than a parser: you open
each element as a ``with a.tag(...)`` block on an ``Airium`` instance, call the instance for text, and let it track
indentation as you nest, then stringify it. turbohtml replaces it with the terse :data:`turbohtml.build.E` builder.

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

``E`` assembles the fragment in turbohtml's arena and serializes it in C; airium stays in Python and pretty-prints with
indentation as it goes. The same ``<ul>`` of rows -- a class, a ``data`` attribute, and a text child apiece -- built
both ways:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - build a list
      - :data:`E <turbohtml.build.E>`
      - airium
    - - 100 rows
      - 139 µs
      - 772 µs (5.5x)
    - - 1000 rows
      - 1.41 ms
      - 7.75 ms (5.5x)

``E`` is roughly five times faster than airium, and the decisive difference is the result type: ``E`` hands back a real
:class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree you can query, edit,
and re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

airium tracks structure by call depth inside ``with`` blocks; turbohtml tracks it in the tree:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - airium
      - turbohtml
    - - ``with a.div(klass="card"):`` then ``a("text")``
      - :data:`E.div({"class": "card"}, "text") <turbohtml.build.E>`; nest by passing children, not by call depth
    - - ``a.li(klass="item", **{"data-i": "1"})``
      - :data:`E.li({"class": "item", "data-i": "1"}) <turbohtml.build.E>`

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
- airium pretty-prints with newlines and indentation; ``E`` serializes compact markup. Parse and re-serialize, or use a
  formatter, when you need indented output.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
