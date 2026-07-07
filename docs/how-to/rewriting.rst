######################################
 Rewrite HTML without building a tree
######################################

Transform a document on the wire -- retag links, lazy-load images, strip trackers, redact text -- in a single pass that
never builds a tree and keeps only the open-element stack in memory. Reach for :func:`turbohtml.rewrite.rewrite` when
you want to *change* markup as it streams, the way Cloudflare's lol-html rewrites at the edge, rather than parse a whole
document to edit a few nodes. For observing without editing, use :doc:`sax`; to load a document you will navigate and
mutate freely, use :func:`turbohtml.parse`.

*****************
 Edit attributes
*****************

Register a ``(selector, handler)`` pair under ``elements``. The handler is called with an
:class:`~turbohtml.rewrite.Element` for each match; set or remove attributes on it and the start tag is rebuilt, while
everything you do not touch is copied through verbatim:

.. testcode::

    from turbohtml.rewrite import rewrite


    def lazy(img):
        img.set_attribute("loading", "lazy")


    print(rewrite("<img src=hero.jpg><img src=x.png loading=eager>", elements=[("img", lazy)]))

.. testoutput::

    <img src="hero.jpg" loading="lazy"><img src="x.png" loading="lazy">

Read the current value with ``get`` (it takes a default) or test presence with ``has_attribute``; ``tag`` and ``attrs``
expose the name and the ``(name, value)`` pairs.

**********************
 Insert markup around
**********************

``before`` and ``after`` insert content just outside the element's tags. Content is HTML-escaped by default; pass
``html=True`` to insert raw markup:

.. testcode::

    def spoiler(element):
        element.before("<details><summary>spoiler</summary>", html=True)
        element.after("</details>", html=True)


    print(rewrite("<p class=spoiler>ending</p>", elements=[("p.spoiler", spoiler)]))

.. testoutput::

    <details><summary>spoiler</summary><p class=spoiler>ending</p></details>

``prepend`` and ``append`` insert *inside* the element, as its first and last content.

*************************
 Replace, redact, unwrap
*************************

``remove`` drops an element and its whole subtree; ``replace`` swaps it for other markup. Removing every ``<script>``
strips it and its content in one pass:

.. testcode::

    def strip_tracker(element):
        element.remove()


    print(rewrite("<article>text<script src=track.js></script></article>", elements=[("script", strip_tracker)]))

.. testoutput::

    <article>text</article>

``set_content`` keeps the element's tags but replaces what is between them -- redacting a value without losing the
wrapper:

.. testcode::

    def redact(element):
        element.set_content("[redacted]")


    print(rewrite("<span class=ssn>123-45-6789</span>", elements=[("span.ssn", redact)]))

.. testoutput::

    <span class=ssn>[redacted]</span>

``remove_and_keep_content`` does the opposite -- it drops the element's own tags but keeps its children -- to unwrap a
legacy element:

.. testcode::

    def unfont(element):
        element.remove_and_keep_content()


    print(rewrite("<p><font color=red>keep</font></p>", elements=[("font", unfont)]))

.. testoutput::

    <p>keep</p>

*****************************
 Text, comments, and doctype
*****************************

Beyond elements, pass ``text``, ``comments``, or ``doctype`` handlers. A text handler reads ``text`` and rewrites it
with ``set_text``:

.. testcode::

    def shout(text):
        text.set_text(text.text.upper())


    print(rewrite("<h1>title</h1>", text=shout))

.. testoutput::

    <h1>TITLE</h1>

A comment handler can drop comments the same way an element handler removes elements:

.. testcode::

    def nocomment(comment):
        comment.remove()


    print(rewrite("<p>hi<!-- TODO --></p>", comments=nocomment))

.. testoutput::

    <p>hi</p>

***************************
 What selectors can stream
***************************

Because the rewriter never looks ahead, only selectors decidable from an element and its ancestors match: type,
universal, id, class, and attribute selectors, the descendant (space) and child (``>``) combinators, ``:root``, and
:is()/:where()/:not() over that subset. A sibling combinator (``+``, ``~``), a positional or structural pseudo-class
(``:nth-child``, ``:last-of-type``, ``:only-child``, ``:empty``), or ``:has()`` needs content the stream has not reached
yet and raises :class:`~turbohtml.SelectorSyntaxError`. :doc:`/explanation/streaming` explains why, and what to do when
you need one of them.
