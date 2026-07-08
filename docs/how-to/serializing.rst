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

******************
 Emit XML / XHTML
******************

Set ``xml=True`` to switch from HTML syntax to XML/XHTML -- the equivalent of lxml's ``tostring(method="xml")``. Every
empty element self-closes (``<br/>``, ``<div/>``), a foreign SVG or MathML subtree carries the namespace declaration
that makes it well-formed, and text and attribute values follow the XML escaping rules: ``&``, ``<`` and ``>`` in text,
plus ``"`` and the whitespace characters in attribute values, become references, while a no-break space stays literal
(XML predefines no ``&nbsp;``). The HTML void-element and raw-text special casing does not apply, so a ``<script>`` body
is escaped like any other text. ``xml`` composes with ``sort_attributes`` and an :class:`~turbohtml.Indent` layout; it
overrides ``formatter`` (the escaping is fixed by XML), and a :class:`~turbohtml.Minify` layout stays HTML:

.. testcode::

    import turbohtml
    from turbohtml import Html, Indent

    doc = turbohtml.parse("<div><br><p>if a < b</p><svg><rect></rect></svg></div>").select_one("div")
    print(doc.serialize(Html(xml=True)))
    print(doc.serialize(Html(xml=True, layout=Indent(2))))

.. testoutput::

    <div><br/><p>if a &lt; b</p><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg></div>
    <div>
      <br/>
      <p>
        if a &lt; b
      </p>
      <svg xmlns="http://www.w3.org/2000/svg">
        <rect/>
      </svg>
    </div>

The output parses with any XML reader, so it round-trips through :mod:`xml.etree.ElementTree` and hands cleanly to an
XSLT or XPath 2.0 pipeline that rejects HTML's unclosed tags.

***********************************
 Preserve the source byte for byte
***********************************

:meth:`~turbohtml.Node.serialize` normalizes the whole document: it re-quotes every attribute, lowercases tag names, and
rewrites character references to their canonical form. When you want the opposite -- a surgical edit that leaves every
other byte, including the author's own quoting and formatting, exactly as written -- :meth:`~turbohtml.Node.to_source`
re-emits the verbatim source of every element and text run the parse left untouched and reserializes only the parts you
changed. Parse with ``source_locations=True`` so the tree records the spans it re-emits, and an unedited round trip
reproduces the input:

.. testcode::

    import turbohtml

    source = '<!DOCTYPE html><html><head></head><body><a HREF="/x">go</a></body></html>'
    doc = turbohtml.parse(source, source_locations=True)
    print(doc.to_source() == source)

    link = doc.select_one("a")
    link.attrs["rel"] = "nofollow"
    print(doc.to_source())

.. testoutput::

    True
    <!DOCTYPE html><html><head></head><body><a href="/x" rel="nofollow">go</a></body></html>

Only the edited ``<a>`` start tag rebuilds (its ``HREF`` normalizing to ``href`` as the added ``rel`` joins it); the
doctype, the ``<head>``, and the link text stay the bytes the source held. Removing a node drops its span, and an
inserted node -- which carries no source location -- serializes canonically while its untouched siblings copy theirs.
This is the tree-based counterpart to the streaming :func:`turbohtml.rewrite.rewrite`; see
:doc:`/explanation/serialization` for what round-trips byte for byte and what a spec-mandated normalization changes.
