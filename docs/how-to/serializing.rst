##########################
 Serialize a tree to HTML
##########################

Render a tree back to conformant HTML and control the result: pick the escape :class:`~turbohtml.Formatter`, choose an
:class:`~turbohtml.Indent` layout, stream the output in bounded chunks, and encode to bytes.

************************
 Serialize with control
************************

:attr:`~turbohtml.Node.html` and :attr:`~turbohtml.Node.inner_html` are the default WHATWG-conformant forms (outer and
children-only). :meth:`~turbohtml.Node.serialize` adds control through a single :class:`~turbohtml.Html` configuration
object: its ``formatter`` field selects the escaping through :class:`~turbohtml.Formatter`, and its ``layout`` field
selects the whitespace. The default ``layout`` of ``None`` gives the compact form; an :class:`~turbohtml.Indent` (an int
for that many spaces, or a string used verbatim) switches to a pretty form that adds whitespace and so does not preserve
meaning. :meth:`~turbohtml.Node.encode` is the same but returns bytes, with the target encoding as its first argument:

.. testcode::

    import turbohtml
    from turbohtml import Formatter, Html, Indent

    card = turbohtml.parse("<div><p>café &amp; co</p></div>").select_one("div")
    print(card.inner_html)
    print(card.serialize(Html(formatter=Formatter.NAMED_ENTITIES)))
    print(card.serialize(Html(layout=Indent(2))))
    print(card.encode("ascii", Html(formatter=Formatter.NAMED_ENTITIES)))

.. testoutput::

    <p>café &amp; co</p>
    <div><p>caf&eacute; &amp; co</p></div>
    <div>
      <p>
        café &amp; co
      </p>
    </div>
    b'<div><p>caf&eacute; &amp; co</p></div>'

***********************************
 Normalize attributes and encoding
***********************************

Two more :class:`~turbohtml.Html` fields normalize the output without touching the tree, and compose with any
``formatter`` or ``layout``. ``sort_attributes`` emits each start tag's attributes in ascending name order, so two
serializations of equal trees diff cleanly:

.. testcode::

    import turbohtml
    from turbohtml import Html

    node = turbohtml.parse("<p id=main class=lead data-x=1>hi</p>").select_one("p")
    print(node.serialize(Html(sort_attributes=True)))

.. testoutput::

    <p class="lead" data-x="1" id="main">hi</p>

``meta_charset`` makes the document ``<head>`` declare the output encoding: an existing ``<meta charset>`` (or ``<meta
http-equiv="content-type">``) is normalized in place, and a head that declares none gets a ``<meta charset>`` injected
as its first child, never a duplicate. ``serialize`` declares ``utf-8`` (the encoding of the returned ``str``), while
``encode`` declares the encoding it writes:

.. testcode::

    import turbohtml
    from turbohtml import Html

    doc = turbohtml.parse("<title>Hi</title>")
    print(doc.serialize(Html(meta_charset=True)))
    print(doc.encode("iso-8859-1", Html(meta_charset=True)))

.. testoutput::

    <html><head><meta charset="utf-8"><title>Hi</title></head><body></body></html>
    b'<html><head><meta charset="iso-8859-1"><title>Hi</title></head><body></body></html>'
