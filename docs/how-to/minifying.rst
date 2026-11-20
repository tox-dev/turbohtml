############################
 Minify HTML and JavaScript
############################

Shrink a document with the round-trip-safe :class:`~turbohtml.Minify` layout, and minify the inline ``<script>`` content
it carries with :func:`~turbohtml.clean.minify_js`.

*******************
 Minify the output
*******************

Set the :class:`~turbohtml.Html` config's ``layout`` field to a :class:`~turbohtml.Minify` to shrink the markup. Every
transform is round-trip safe: the minified bytes reparse to the same tree, so minifying never changes meaning. The one
exception is a carriage return in raw text such as ``script`` content, which has no character reference: it is written
as a line feed, the form a parse reads it as. The four flags fold insignificant whitespace, omit the start/end tags the
WHATWG rules make optional, drop redundant attribute quotes, and strip comments; all default on. Because ``layout``
holds one mode, a :class:`~turbohtml.Minify` and an :class:`~turbohtml.Indent` cannot be combined.

.. testcode::

    import turbohtml
    from turbohtml import Html, Minify

    doc = turbohtml.parse(
        "<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"
    )
    print(doc.serialize(Html(layout=Minify())))
    print(doc.serialize(Html(layout=Minify(omit_optional_tags=False, collapse_whitespace=False))))

.. testoutput::

    <title>Hi</title><p class=lead>one</p> <p>two
    <html><head><title>Hi</title></head><body><p class=lead>one</p>  <p>two</p></body></html>

Whitespace-significant elements (``pre``, ``textarea``, ``listing``) and raw-text elements (``script``, ``style``) keep
their content verbatim, and a tag is never dropped when omitting it would let the reparse reconstruct a formatting
element across the boundary.

When the input is a string rather than a built tree -- the ``minify-html`` and ``htmlmin`` use case --
:func:`turbohtml.clean.minify` parses and minifies in one call, taking the same :class:`~turbohtml.Minify` options:

.. testcode::

    from turbohtml.clean import minify

    print(minify("<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"))

.. testoutput::

    <title>Hi</title><p class=lead>one</p> <p>two

A browser that runs scripts reads ``<noscript>`` content as raw text, while the parse behind the tree builds elements
from it, so a tree alone cannot show what the minified markup means to that browser. :func:`turbohtml.clean.minify`
works from the source string instead: it copies each ``<noscript>``'s content as the source spelled it. When a
``<noscript>``'s content parses differently with scripting on, everything from that ``<noscript>`` to the end is copied
unminified, and when the markup before it cannot be minified without changing how that ``<noscript>`` parses -- for
example inside a table, a ``<template>``, or open formatting elements -- the whole document comes back unchanged.
Serializing a tree with :class:`~turbohtml.Minify` has no source to copy and minifies ``<noscript>`` content like any
other.

.. testcode::

    print(minify("<p>a  b</p><noscript>  <img  src=/pixel.png>  </noscript>"))

.. testoutput::

    <p>a b</p><noscript>  <img  src=/pixel.png>  </noscript>

*******************
 Minify JavaScript
*******************

:func:`~turbohtml.clean.minify_js` minifies a JavaScript string on its own. It always folds whitespace, comments and
number literals — ``/*! ... */`` bang comments and any ``@license`` / ``@preserve`` comment are the exception, kept
byte-exact as a leading banner so a license header survives, exactly as the CSS minifier keeps them. A
:class:`~turbohtml.clean.JSMinify` toggles the two heavier passes — ``mangle`` renames local bindings to short names and
``fold`` runs constant folding and dead-code elimination. Top-level names are global, so they are never renamed; only
bindings local to a function are. A construct the parser does not handle raises :class:`ValueError` rather than passing
through silently.

.. testcode::

    from turbohtml.clean import minify_js, JSMinify

    source = "function f(x) { var half = x / 2; return half * half; }"
    print(minify_js(source))
    print(minify_js(source, JSMinify(mangle=False)))

.. testoutput::

    function f(b){var a=b/2;return a*a}
    function f(x){var half=x/2;return half*half}

Inline ``<script>`` minification rides on HTML minification: pass a :class:`~turbohtml.clean.JSMinify` as
:class:`~turbohtml.Minify`'s ``minify_js`` (the default ``None`` leaves scripts untouched). The minifier rewrites only
scripts the ``type`` attribute marks as JavaScript and leaves a ``type="application/json"`` or ``importmap`` payload
byte-for-byte.

The minifier emits a script verbatim when the JS parser rejects it, so one bad ``<script>`` cannot break the document.
It does the same when the minified text would hold ``</script`` or ``<!--`` in any letter case, where the HTML parser
would end the element or search past it for the end, and when it would hold ``</noscript``, where a browser that runs
scripts ends an enclosing ``<noscript>``.

.. testcode::

    import turbohtml
    from turbohtml import Html, Minify
    from turbohtml.clean import JSMinify

    doc = turbohtml.parse("<p>hi<script>function plus(a, b) { return a + b; }</script>")
    print(doc.serialize(Html(layout=Minify(minify_js=JSMinify()))))

.. testoutput::

    <p>hi<script>function plus(a,b){return a+b}</script>
