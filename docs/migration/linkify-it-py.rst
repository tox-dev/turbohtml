####################
 From linkify-it-py
####################

.. image:: https://static.pepy.tech/badge/linkify-it-py
    :alt: linkify-it-py downloads
    :target: https://pepy.tech/project/linkify-it-py

`linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`_ is the pure-Python link scanner `markdown-it-py
<https://github.com/executablebooks/markdown-it-py>`_ pulls in: it scans plain text and returns the link spans it finds,
leaving the caller to turn them into ``<a>`` tags and to skip text that is already markup.

***************
 Why turbohtml
***************

turbohtml does both jobs, fully type annotated: :func:`~turbohtml.linkify.linkify` rewrites HTML (and, being HTML-aware,
leaves a URL already inside an ``<a>`` or ``<script>`` alone), while :class:`~turbohtml.linkify.Detector` matches spans
the way linkify-it-py's ``match`` does. The candidate scan runs in C, so even pure detection is several times faster
than the Python scanner that does strictly less work:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - linkify
      - turbohtml
      - linkify-it-py
    - - comment (1 link, 1 email)
      - 2.6 µs
      - 29 µs
    - - prose (1 KiB)
      - 48 µs
      - 310 µs
    - - markup (4 KiB)
      - 120 µs
      - 723 µs

*************
 The renames
*************

.. code-block:: python

    # linkify-it-py
    from linkify_it import LinkifyIt

    matches = LinkifyIt().match("see https://example.com")
    # [Match(url="https://example.com", index=4, last_index=23, ...)] or None

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - linkify-it-py
      - turbohtml
    - - ``LinkifyIt().match(text)``
      - :meth:`Detector().find(text) <turbohtml.linkify.Detector.find>`
    - - ``Match`` (``index``/``last_index``/``url``/``schema``)
      - :class:`~turbohtml.linkify.LinkSpan` (``start``/``end``/``url``/``text``)
    - - ``LinkifyIt().test(text)``
      - :meth:`~turbohtml.linkify.Detector.has_link`
    - - ``add(schema, rule)`` (scheme-less schemes)
      - the ``schemes`` argument
    - - ``tlds(...)``
      - the ``tlds`` argument
    - - (rewrite HTML yourself)
      - :func:`~turbohtml.linkify.linkify`

.. testcode::

    from turbohtml.linkify import Detector

    span = Detector().find("see https://example.com")[0]
    print(span.start, span.end, span.url)

.. testoutput::

    4 23 https://example.com

**********
 Pitfalls
**********

- linkify-it-py reaches further into fuzzy IP and email heuristics; turbohtml covers the common web, ``mailto:``,
  bare-domain, and registered-scheme cases and trades that breadth for being HTML-aware and several times faster.
