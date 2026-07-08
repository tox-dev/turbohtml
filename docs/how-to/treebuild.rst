##########################
 Parse into your own tree
##########################

Point the conformant parser at a structure you control -- an index, a diff tree, an extraction record, or another
library's node type -- instead of a :class:`~turbohtml.Document`. :func:`turbohtml.treebuild.parse_into` runs the same
WHATWG tree builder as :func:`turbohtml.parse`, then hands each constructed node to a *builder* object you supply, so
the tree is materialized straight into your representation with no navigable node created and no second walk. This is
Rust html5ever's ``TreeSink`` and Node parse5's ``TreeAdapter``, in turbohtml shape.

*****************
 The builder API
*****************

A builder is any object with seven methods: one ``create_*`` per node kind, returning an opaque *handle*, and an
``append`` that links a child handle under its parent. turbohtml never inspects a handle -- it only threads it back into
a later ``append`` -- so a handle can be a node in your tree, an integer id, or ``None`` when you only want the calls.
The handle :meth:`~turbohtml.treebuild.TreeBuilder.create_document` returns is what
:func:`~turbohtml.treebuild.parse_into` gives back.

****************************
 Build an index in one pass
****************************

When you only need a fact per node, return ``None`` for every handle and record what you want as the calls arrive. This
tag histogram never allocates a tree at all -- the whole document is summarized in the single parse:

.. testcode::

    from collections import Counter

    from turbohtml.treebuild import parse_into


    class TagIndex:
        def __init__(self):
            self.counts = Counter()

        def create_document(self):
            return None

        def create_doctype(self, name, public_id, system_id):
            return None

        def create_element(self, name, namespace, attrs):
            self.counts[name] += 1

        def create_text(self, data):
            return None

        def create_comment(self, data):
            return None

        def create_pi(self, data):
            return None

        def append(self, parent, child):
            pass


    index = TagIndex()
    parse_into("<ul><li>a<li>b</ul><ul><li>c</ul>", index)
    print(sorted(index.counts.items()))

.. testoutput::

    [('body', 1), ('head', 1), ('html', 1), ('li', 3), ('ul', 2)]

The implied ``<html>``, ``<head>``, and ``<body>`` are counted and the auto-closed ``<li>`` items are all there, because
the calls reflect the tree the WHATWG algorithm builds, not the tags as written.

**********************
 Build an actual tree
**********************

To construct a tree, return a node from each ``create_*`` and link it in ``append``. Here the handles are plain ``[name,
children]`` lists:

.. testcode::

    from turbohtml.treebuild import parse_into


    class ListTree:
        def create_document(self):
            return ["#document", []]

        def create_doctype(self, name, public_id, system_id):
            return ["#doctype", []]

        def create_element(self, name, namespace, attrs):
            return [name, []]

        def create_text(self, data):
            return ["#text:" + data, []]

        def create_comment(self, data):
            return ["#comment", []]

        def create_pi(self, data):
            return ["#pi", []]

        def append(self, parent, child):
            parent[1].append(child)


    tree = parse_into("<p>x</p>", ListTree())
    body = tree[1][0][1][1]
    print(body)

.. testoutput::

    ['body', [['p', [['#text:x', []]]]]]

Every element also carries its namespace URI and its attributes. ``create_element`` receives ``namespace`` --
``http://www.w3.org/1999/xhtml`` for HTML, and the SVG and MathML URIs for foreign content -- and ``attrs`` as a tuple
of ``(name, value)`` pairs, a valueless attribute pairing with ``None``. A ``<template>``'s content is appended straight
under the template handle, and a bogus ``<?...>`` construct -- WHATWG HTML has no processing instructions -- arrives
through ``create_pi`` so you can keep it distinct from a comment.

*******************
 Errors and typing
*******************

If a builder method raises, :func:`~turbohtml.treebuild.parse_into` stops and propagates the exception; a builder
missing a method raises :exc:`AttributeError` before parsing begins. For static typing, implement
:class:`turbohtml.treebuild.TreeBuilder`, a :class:`~typing.Protocol` generic over your handle type -- it is structural,
so no base class is needed.

See :doc:`/explanation/treebuild` for how this differs from the SAX event stream and what it costs, and the
:doc:`/reference/parsing` page for the full signatures.
