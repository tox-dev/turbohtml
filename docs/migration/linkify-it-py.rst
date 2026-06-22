####################
 From linkify-it-py
####################

`linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`_ scans plain text and returns the link spans it finds;
turning them into ``<a>`` tags, and skipping text that is already markup, is left to the caller. turbohtml does both,
through two entry points. To rewrite HTML, use :func:`~turbohtml.linkify.linkify`. To match spans the way
linkify-it-py's ``match`` does, use :class:`~turbohtml.linkify.Detector`; where linkify-it-py returns ``Match`` objects
with ``index``/``last_index``/``url``/``schema``, turbohtml returns :class:`~turbohtml.linkify.LinkSpan` objects with
``start``/``end``/``url``/``text``:

.. code-block:: python

    # linkify-it-py
    from linkify_it import LinkifyIt

    matches = LinkifyIt().match("see https://example.com")
    # [Match(url="https://example.com", index=4, last_index=23, ...)] or None

.. testcode::

    from turbohtml.linkify import Detector

    span = Detector().find("see https://example.com")[0]
    print(span.start, span.end, span.url)

.. testoutput::

    4 23 https://example.com

linkify-it-py's ``test`` becomes :meth:`~turbohtml.linkify.Detector.has_link`, ``add(schema, rule)`` becomes the
``schemes`` argument for scheme-less schemes, and ``tlds(...)`` becomes the ``tlds`` argument. Because
:func:`~turbohtml.linkify.linkify` parses the input as HTML, it also leaves alone a URL already inside an ``<a>`` or a
``<script>`` that linkify-it-py, working on the raw string, would match again. linkify-it-py still reaches further into
fuzzy IP and email heuristics; turbohtml covers the common web, ``mailto:``, bare-domain, and registered-scheme cases
and trades that breadth for being HTML-aware and several times faster.
