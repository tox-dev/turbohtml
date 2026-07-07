##########################
 Parse with SAX callbacks
##########################

Process a document as a stream of events without building or keeping a tree, the way you would with
:class:`python:html.parser.HTMLParser` or expat -- but over a spec-correct tree builder. Use :mod:`turbohtml.saxparse`
when you want to pull a few facts out of a page and move on, not navigate it afterwards.

*******************
 The callback form
*******************

Subclass :class:`~turbohtml.saxparse.SaxHandler`, override only the events you care about, and pass an instance to
:func:`~turbohtml.saxparse.sax_parse`. Every method defaults to a no-op, so a link collector overrides just
``start_element``:

.. testcode::

    from turbohtml.saxparse import SaxHandler, sax_parse


    class LinkCollector(SaxHandler):
        def __init__(self):
            self.links = []

        def start_element(self, tag, attrs):
            if tag == "a":
                self.links += [value for name, value in attrs if name == "href" and value]


    collector = LinkCollector()
    sax_parse('<ul><li><a href="/x">x</a><li><a href="/y">y</a></ul>', collector)
    print(collector.links)

.. testoutput::

    ['/x', '/y']

``attrs`` is a tuple of ``(name, value)`` pairs in source order; a valueless attribute (``<input disabled>``) has
``None`` for its value. Every element fires ``end_element`` when it closes, including empty and void ones.

*****************
 The stream form
*****************

If you would rather drive the loop than invert control into callbacks, iterate :func:`~turbohtml.saxparse.iter_events`.
It yields typed records -- :class:`~turbohtml.saxparse.StartElement`, :class:`~turbohtml.saxparse.Characters`, and the
rest -- one at a time:

.. testcode::

    from turbohtml.saxparse import Characters, iter_events

    text = "".join(event.data for event in iter_events("<p>Hello <b>there</b></p>") if isinstance(event, Characters))
    print(text)

.. testoutput::

    Hello there

*******************************
 The events are the built tree
*******************************

Unlike ``html.parser``, the stream reflects the tree the WHATWG algorithm constructs, not the raw tags. Implied elements
appear, and misplaced content is foster-parented into place:

.. testcode::

    from turbohtml.saxparse import StartElement, iter_events

    tags = [event.tag for event in iter_events("<table><td>cell") if isinstance(event, StartElement)]
    print(tags)

.. testoutput::

    ['html', 'head', 'body', 'table', 'tbody', 'tr', 'td']

Nobody wrote ``<html>``, ``<head>``, ``<body>``, ``<tbody>``, or ``<tr>``; the parser did, and the events say so.

**********
 Doctypes
**********

A :class:`~turbohtml.saxparse.Doctype` event carries the ``name`` and, when the source supplied them, the ``public_id``
and ``system_id`` (each ``None`` otherwise). A ``<?...>`` construct -- a WHATWG bogus comment, since HTML has no
processing instructions -- arrives as a :class:`~turbohtml.saxparse.ProcessingInstruction`, matching
:meth:`html.parser.HTMLParser.handle_pi`.

For the memory model and why this is not a way to parse a document larger than memory, see :doc:`/explanation/sax`.
