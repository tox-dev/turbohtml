###########
 From htpy
###########

.. package-meta:: htpy pelme/htpy

`htpy <https://htpy.dev>`_ assembles HTML in Python from the other direction than a parser: each element is an object
whose attributes come from a call (``li(class_="item")``) and whose children sit in a ``[...]`` subscript (``ul[li("a"),
li("b")]``), and stringifying the root renders the tree. Attributes can also be a leading CSS-selector string
(``div(".card")``) or a mapping, children accept strings, other elements, and iterables or generators, and it escapes
through markupsafe so a :class:`markupsafe.Markup` value passes through unescaped. It is a typed alternative to a
template language, used to render server-side HTML in Django, Flask, Starlette, and FastAPI, and it can stream a page
out as chunks for async responses.

turbohtml covers the same ground with the terse :data:`turbohtml.build.E` builder, but the value it hands back is a real
:class:`~turbohtml.Element` in a parsed tree rather than a render-only object, so the whole query, edit, and serialize
surface stays available on what you just built.

*******************
 turbohtml vs htpy
*******************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - htpy
    - - Scope
      - Full WHATWG parser, serializer, builder, selectors, sanitizer, converters
      - HTML generation only, from Python
    - - Feature breadth
      - ``E`` builder over a real tree: CSS/XPath query, edit, ``to_markdown``, compact/indent/minify layouts, chunked
        streaming
      - Element objects, selector-string and conditional-class attributes, iterable children, markupsafe escaping,
        ``fragment`` grouping, ``html2htpy`` CLI
    - - Performance
      - Builds and serializes in C
      - Pure Python over markupsafe
    - - Typing
      - Fully annotated, ``py.typed``
      - Fully annotated, ``py.typed``
    - - Dependencies
      - None (self-contained C extension)
      - markupsafe
    - - Maintenance
      - Active
      - Active

Feature overlap
===============

The shared surface ports one-to-one:

- ``div(class_="card")[h1("Title")]`` -> :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`; attributes
  are a leading mapping, children follow in the same call instead of a subscript.
- ``li(class_="item", data_i="1")["text"]`` -> :data:`E.li({"class": "item", "data-i": "1"}, "text")
  <turbohtml.build.E>`; write the real attribute name as the dict key, with no ``class_`` alias or
  ``data_``-to-``data-`` rewrite to remember.
- A string child becomes escaped text in both: htpy escapes through markupsafe, ``E`` builds a :class:`~turbohtml.Text`
  node.
- A non-identifier tag such as a custom element -> :data:`E("my-card", ...) <turbohtml.build.E>`, the call form.
- A class token list joins on a space: ``div(class_="card lg")`` -> ``E.div({"class": ["card", "lg"]})``.

What turbohtml adds
===================

- The result is an ordinary :class:`~turbohtml.Element`, so :meth:`~turbohtml.Node.select`,
  :meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.find`, :meth:`~turbohtml.Node.to_markdown`, and the full edit
  API are available. htpy's element is a render-only object with no query surface.
- The markup serializes by exactly the WHATWG rules that parse it back, so a fragment round-trips; htpy renders its own
  string and cannot read it back into a tree.
- ``E`` assembles the fragment in turbohtml's arena and serializes it in C, about two and a half times faster than htpy.
- A :class:`~turbohtml.Minify` layout (whitespace collapse, optional-tag omission, JS/CSS minify) on top of the compact
  and :class:`~turbohtml.Indent` layouts; htpy renders compact only.
- Chunked streaming: :meth:`~turbohtml.Node.serialize_iter` yields the markup in bounded ``str`` chunks, so a large page
  streams to an async response without ever building the whole string -- the same shape as iterating an htpy element,
  and ``''.join(node.serialize_iter())`` equals :meth:`~turbohtml.Node.serialize`.
- No third-party dependency: turbohtml is a self-contained C extension, where htpy pulls in markupsafe.
- You can :func:`turbohtml.parse` real markup and append an ``E`` fragment under it, mixing parsing and building in one
  tree.

What htpy has that turbohtml does not
=====================================

- Selector-string attributes: ``div("#main.card.lg")`` sets id and classes from one CSS-selector string. ``E`` takes an
  explicit mapping. Workaround: write ``E.div({"id": "main", "class": ["card", "lg"]})``.
- Conditional-class mapping: ``div(class_={"active": is_active, "hidden": False})`` keeps only the true keys. ``E``
  takes a plain class value. Workaround: build the list yourself, ``E.div({"class": [name for name, on in flags if
  on]})``.
- Iterable and generator children in the subscript: ``ul[(li(x) for x in items)]`` consumes the generator, and ``None``
  or ``False`` children are dropped. ``E`` takes positional arguments. Workaround: splat and filter first,
  ``E.ul(*(E.li(x) for x in items if x is not None))``.
- markupsafe pass-through: a :class:`markupsafe.Markup` child is emitted unescaped without reparsing. ``E`` always
  escapes a string into a :class:`~turbohtml.Text` node. Workaround: :func:`turbohtml.parse` the trusted markup and
  append the resulting nodes, since turbohtml is a real parser.
- ``fragment[...]`` groups several children with no wrapper element and renders them as adjacent siblings. ``E.<tag>``
  always produces one rooted element. Workaround: serialize each child and concatenate, or parse the concatenation into
  a fragment.
- The ``html2htpy`` CLI converts existing HTML into htpy builder source. turbohtml has no code generator. Workaround:
  :func:`turbohtml.parse` the HTML at runtime instead of generating builder code.

Performance
===========

``E`` is about two and a half times faster than htpy. The same ``<ul>`` of rows -- a class, a ``data`` attribute, and a
text child apiece -- built both ways:

.. bench-table::
    :file: bench/htpy.json

The decisive difference is the result type: ``E`` hands back a real :class:`~turbohtml.Element`, not a string, so the
call that builds the markup also leaves a tree you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the per-tag imports for the single ``E`` builder:

.. code-block:: python

    # htpy
    from htpy import div, h1, p

    # turbohtml
    from turbohtml.build import E

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `htpy <https://htpy.dev/>`__
      - turbohtml
    - - ``div(".card")[h1("Title")]``
      - :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`; attributes are a mapping, children are
        arguments
    - - ``li(class_="item", data_i="1")["text"]``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`
    - - ``div(class_={"active": on})`` conditional classes
      - :data:`E.div({"class": [...]}) <turbohtml.build.E>`; build the token list before the call
    - - ``ul[(li(x) for x in items)]`` iterable children
      - :data:`E.ul(*(E.li(x) for x in items)) <turbohtml.build.E>`; splat the iterable into arguments
    - - ``Markup(raw)`` child
      - :func:`turbohtml.parse` the raw markup, then append the nodes
    - - ``str(element)``
      - :meth:`~turbohtml.Node.serialize` (compact) or ``serialize(Html(layout=Indent()))``

htpy carries attributes in the call and children in a subscript; turbohtml passes both to one call:

.. testcode::

    from turbohtml.build import E

    card = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    print(card.serialize())

.. testoutput::

    <div class="card"><h1>Title</h1><p>body</p></div>

``E("tag", ...)`` is the call form for a tag that is not a Python identifier (a custom element, say), and a list-valued
attribute joins on a space so a class list reads naturally:

.. testcode::

    from turbohtml.build import E

    print(E("my-card", {"class": ["card", "lg"]}, "hi").serialize())

.. testoutput::

    <my-card class="card lg">hi</my-card>

**********************
 Gotchas and pitfalls
**********************

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype,
  where importing htpy's ``html`` element renders a leading ``<!doctype html>``. Serialize the element you built, or
  append it under a parsed document when you need the full page shell.
- Attribute names are plain dict keys (``{"data-i": "1"}``), not htpy's keyword conventions (``class_``, the trailing
  underscore for reserved words, the ``data_``/``aria_`` underscore-to-dash rewrite, or the ``".card"`` selector
  string).
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``).
- ``E`` takes children as positional arguments, so a generator must be splatted (``E.ul(*(E.li(x) for x in items))``);
  htpy consumes an iterable placed directly in the ``[...]`` subscript.
- htpy drops ``None`` and ``False`` children; ``E`` appends every argument, so filter conditionals out before the call.
- ``E`` escapes strings into text nodes, so ``E.div("<b>")`` serializes as ``&lt;b&gt;``. htpy emits a
  :class:`markupsafe.Markup` value unescaped; to inject real markup here, :func:`turbohtml.parse` it and append the
  nodes.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
