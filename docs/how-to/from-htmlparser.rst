#####################################
 Migrate from html.parser.HTMLParser
#####################################

Code that subclasses :class:`python:html.parser.HTMLParser` and overrides ``handle_starttag``, ``handle_data``, and
``handle_endtag`` -- Django's ``MLStripper``, Tornado's link seeker, agate's table reader -- maps directly onto
:mod:`turbohtml.saxparse`. You get the same event callbacks over a spec-correct tree builder, so malformed input
recovers the way a browser recovers rather than surfacing raw, unbalanced tags.

*******************
 Map the callbacks
*******************

Subclass :class:`~turbohtml.saxparse.SaxHandler` instead of ``HTMLParser`` and rename the three methods. The events line
up one for one:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - ``html.parser`` method
      - ``turbohtml.saxparse`` method
    - - ``handle_starttag(tag, attrs)``
      - ``start_element(tag, attrs)``
    - - ``handle_endtag(tag)``
      - ``end_element(tag)``
    - - ``handle_data(data)``
      - ``characters(data)``
    - - ``handle_comment(data)``
      - ``comment(data)``
    - - ``handle_decl(decl)``
      - ``doctype(name, public_id, system_id)``

A tag stripper -- the ``MLStripper`` recipe that collects text and drops markup -- becomes an override of
``characters``:

.. testcode::

    from turbohtml.saxparse import SaxHandler, sax_parse


    class TextExtractor(SaxHandler):
        def __init__(self):
            self.parts = []

        def characters(self, data):
            self.parts.append(data)


    handler = TextExtractor()
    sax_parse("<p>Hello <b>bold</b> and <a href='/x'>linked</a> text.</p>", handler)
    print("".join(handler.parts))

.. testoutput::

    Hello bold and linked text.

``attrs`` arrives as a tuple of ``(name, value)`` pairs in source order, the same shape ``HTMLParser`` passes, so a
``start_element`` that reads attributes needs no change beyond the method name.

****************************
 The events follow the tree
****************************

One behavior differs. ``HTMLParser`` reports the raw tags it reads; ``saxparse`` reports the tree the WHATWG algorithm
builds, so implied elements appear and misplaced content is foster-parented into place. A ``<td>`` with no table around
it still fires inside a generated ``<table><tbody><tr>``:

.. testcode::

    from turbohtml.saxparse import StartElement, iter_events

    tags = [event.tag for event in iter_events("<table><td>cell") if isinstance(event, StartElement)]
    print(tags)

.. testoutput::

    ['html', 'head', 'body', 'table', 'tbody', 'tr', 'td']

When you would rather drive the loop than invert control into callbacks, iterate :func:`~turbohtml.saxparse.iter_events`
for the same events as typed records. To edit markup on the way through rather than only observe it, use
:doc:`rewriting`; the :doc:`stdlib migration guide </migration/stdlib>` maps the rest of the ``HTMLParser`` surface.
