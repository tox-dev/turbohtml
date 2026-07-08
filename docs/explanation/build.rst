#################
 Building a tree
#################

Most of turbohtml starts from markup a parser hands it. Sometimes you have no markup, only data, and want the tree
directly: a fragment to splice into a page, a table assembled from rows, a document generated from a template.
:data:`turbohtml.build.E` and :class:`turbohtml.build.ElementMaker` build that tree in code.

***********************
 A real tree, not text
***********************

The builders that turbohtml replaces -- `dominate <https://github.com/Knio/dominate>`_, `htpy <https://htpy.dev/>`_,
`yattag <https://www.yattag.org/>`_ -- produce a string of HTML. That is enough when the next step is to write the
markup out, and nothing more. ``E`` produces a :class:`turbohtml.Element` instead: the same node a parse would have
built. So the result is not a dead end. You can query it with a CSS selector, edit it in place, fold it into a parsed
document, and serialize it with the same escaping controls a parsed tree uses. Building and parsing converge on one
model, and every tool that works on a parsed node works on a built one.

Because the output is a tree rather than a string, ``E`` escapes as it builds. Text passed as a child becomes a real
text node, so a ``<`` in the data is stored as data and serialized as ``&lt;``; there is no interpolation step where an
unescaped value could slip into the markup. The builder cannot emit an unbalanced tag, because it is placing nodes, not
concatenating strings.

*******************
 The factory shape
*******************

``E`` is an :class:`~turbohtml.build.ElementMaker` instance: calling ``E.div(...)`` builds a ``<div>``, ``E.a(...)``
builds an ``<a>``, and so on for any tag name, so the code reads as the tree it makes. A call takes attributes and
children in the shape that keeps both unambiguous -- a mapping for attributes, the remaining arguments as children --
and nests, because a child is just another built element. Building an :class:`~turbohtml.build.ElementMaker` of your own
lets you fix a default namespace once, which is what makes ``E`` usable for SVG or XML rather than HTML alone.

Reach for the builder when the structure originates in your program: rendering records into a fragment, wrapping
untrusted text safely, or standing up a small document a test or a template needs. When the structure already exists as
markup, parse it; the builder is for the case where it does not yet. The :doc:`/how-to/building` guide walks through the
calls.
