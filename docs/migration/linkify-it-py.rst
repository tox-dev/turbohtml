####################
 From linkify-it-py
####################

.. package-meta:: linkify-it-py tsutsu3/linkify-it-py

`linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`_ is the pure-Python link scanner that `markdown-it-py
<https://github.com/executablebooks/markdown-it-py>`_ pulls in. It is a faithful port of the JavaScript ``linkify-it``:
given a run of plain text, ``LinkifyIt().match(text)`` returns the link spans it finds and ``LinkifyIt().test(text)``
reports whether any exist. It knows URLs with a ``scheme://`` authority, bare domains gated by an IANA TLD table, and a
set of fuzzy heuristics for bare IPs, ``@`` mentions, and email addresses. Its scope stops at *locating* links: turning
a span into an ``<a>`` tag and skipping text that is already markup are the caller's job, which is why markdown-it-py
wraps it in its own autolink rule.

turbohtml covers the same ground with an HTML-aware, fully type-annotated API. :class:`~turbohtml.clean.LinkDetector`
locates spans the way ``match``/``test`` do, and :func:`~turbohtml.clean.linkify` goes one step further: it parses the
HTML, rewrites eligible text runs into ``<a>`` tags, and leaves URLs that already sit inside an ``<a>``, ``<script>``,
or a skipped tag alone. The candidate scan runs in C, so the detection primitives outrun the Python scanner even though
they do strictly more work per call.

****************************
 turbohtml vs linkify-it-py
****************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - linkify-it-py
    - - Scope
      - Detect spans *and* rewrite HTML into ``<a>`` tags, markup-aware
      - Detect spans in plain text only; caller rewrites and skips markup
    - - Feature breadth
      - URLs, bare domains, ``mailto:`` emails, registered scheme-less schemes (``tel:``, ``bitcoin:``); HTML walk with
        skip tags and existing-anchor handling
      - URLs, bare domains, emails, plus fuzzy IP / ``@``-mention / fuzzy-email heuristics and pluggable custom schemes
    - - Performance
      - C candidate scan; several times faster on rewrite and detection (see below)
      - Pure-Python scanner
    - - Typing
      - Fully annotated, ``py.typed``
      - Typed public surface
    - - Dependencies
      - Single package, C extension bundled
      - Pure Python, depends on ``uc-micro-py``
    - - Maintenance
      - Active, part of the turbohtml project
      - Active, tracks the JS ``linkify-it`` upstream

Feature overlap
===============

The plain-text detection surface ports one-to-one:

- ``LinkifyIt().match(text)`` -> :meth:`LinkDetector().find(text) <turbohtml.clean.LinkDetector.find>`, returning a list
  of spans (or an empty list rather than ``None``).
- A ``Match`` (``index`` / ``last_index`` / ``url`` / ``text`` / ``schema``) -> a :class:`~turbohtml.clean.LinkSpan`
  (``start`` / ``end`` / ``url`` / ``text`` / ``is_email``).
- ``LinkifyIt().test(text)`` -> :meth:`~turbohtml.clean.LinkDetector.has_link`.
- ``LinkifyIt().tlds(list)`` -> the ``tlds`` argument to :class:`~turbohtml.clean.LinkDetector` (added on top of the
  built-in IANA table).
- ``LinkifyIt().add(schema, rule)`` for opaque scheme-less schemes -> the ``schemes`` argument, which registers schemes
  such as ``tel:`` or ``bitcoin:`` both as opaque and as ``scheme://`` URLs.

What turbohtml adds
===================

- :func:`~turbohtml.clean.linkify` rewrites HTML directly. linkify-it-py only reports spans; you would write the anchor
  construction, the entity escaping, and the "is this already inside a link" walk yourself.
- Markup awareness: the rewrite leaves URLs inside an existing ``<a>``, inside raw-text elements like ``<script>`` and
  ``<style>``, and inside caller-named skip tags (``pre``, ``code``) untouched.
- A :class:`~turbohtml.clean.Linkify` configuration object with callbacks that can adjust or veto each link (for
  example, add ``rel="nofollow"``), and ``process_existing`` to rerun those callbacks over anchors already in the input.
- ``mailto:`` normalization and ``http://`` scheme-fill for bare domains are done for you on both the ``LinkSpan.url``
  and the rewritten ``href``.
- The scan runs in a C extension.

What linkify-it-py has that turbohtml does not
==============================================

- Fuzzy heuristics. linkify-it-py has opt-in ``fuzzy_link``, ``fuzzy_ip``, and ``fuzzy_email`` modes that catch bare
  IPv4 addresses and looser ``user@host`` shapes. turbohtml covers the common web, ``mailto:``, bare-domain, and
  registered-scheme cases and does not attempt the fuzzy set. No equivalent; keep linkify-it-py where those matches
  matter.
- Arbitrary custom link rules. ``add(schema, rule)`` accepts a validator object with a ``validate`` callback for bespoke
  grammars. turbohtml's ``schemes`` argument registers additional schemes but does not take a custom matcher.
  Workaround: post-filter :meth:`LinkDetector.find <turbohtml.clean.LinkDetector.find>` results, or handle the bespoke
  shape before the scan.
- ``schema`` on the match tells you which rule fired (``"http:"``, ``"mailto:"``, ``""`` for a bare domain).
  :class:`~turbohtml.clean.LinkSpan` exposes ``is_email`` and the normalized ``url`` instead of the raw schema label;
  read the scheme off ``url`` if you need it.

Performance
===========

.. bench-table::
    :file: bench/linkify-it-py.json

Both the full rewrite and the bare detection primitives (:meth:`LinkDetector.find <turbohtml.clean.LinkDetector.find>`
against ``LinkifyIt().match``, and :meth:`~turbohtml.clean.LinkDetector.has_link` against ``LinkifyIt().test``) outrun
the Python scanner. The one close row is ``has_link`` on prose, where ``test`` short-circuits on the first link near the
start.

****************
 How to migrate
****************

Swap the import and construct a reusable :class:`~turbohtml.clean.LinkDetector` instead of a ``LinkifyIt``:

.. code-block:: python

    # linkify-it-py
    from linkify_it import LinkifyIt

    matches = LinkifyIt().match("see https://example.com")
    # [Match(url="https://example.com", index=4, last_index=23, ...)] or None

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`__
      - turbohtml
    - - ``LinkifyIt().match(text)``
      - :meth:`LinkDetector().find(text) <turbohtml.clean.LinkDetector.find>`
    - - ``Match`` (``index`` / ``last_index`` / ``url`` / ``schema``)
      - :class:`~turbohtml.clean.LinkSpan` (``start`` / ``end`` / ``url`` / ``text`` / ``is_email``)
    - - ``LinkifyIt().test(text)``
      - :meth:`~turbohtml.clean.LinkDetector.has_link`
    - - ``add(schema, rule)`` (scheme-less schemes)
      - the ``schemes`` argument
    - - ``tlds(...)``
      - the ``tlds`` argument
    - - (rewrite HTML yourself)
      - :func:`~turbohtml.clean.linkify`

.. testcode::

    from turbohtml.clean import LinkDetector

    span = LinkDetector().find("see https://example.com")[0]
    print(span.start, span.end, span.url)

.. testoutput::

    4 23 https://example.com

To rewrite HTML rather than list spans, reach for :func:`~turbohtml.clean.linkify`, which has no linkify-it-py
counterpart:

.. testcode::

    from turbohtml.clean import linkify

    print(linkify("visit example.com for more"))

.. testoutput::

    visit <a href="http://example.com" rel="nofollow">example.com</a> for more

**********************
 Gotchas and pitfalls
**********************

- ``match`` returns ``None`` when nothing is found; :meth:`LinkDetector.find <turbohtml.clean.LinkDetector.find>`
  returns an empty list. Replace ``if matches is not None`` with a plain truthiness or length check.
- Offsets differ in name only: linkify-it-py's ``index`` / ``last_index`` are turbohtml's ``start`` / ``end``, both
  half-open into the scanned string.
- linkify-it-py hands back the URL exactly as matched and leaves scheme-filling to you via ``Match.url`` versus
  ``Match.text``. turbohtml normalizes ``LinkSpan.url`` up front: a bare domain gets ``http://``, an email gets
  ``mailto:``, and ``LinkSpan.text`` keeps the original substring.
- Scheme allowlisting is stricter by default. turbohtml autolinks ``http`` / ``https`` / ``ftp`` and whatever you pass
  in ``schemes``; a typo scheme or a ``javascript://`` payload stays plain text. If you relied on linkify-it-py
  registering an extra scheme through ``add``, pass it in ``schemes`` instead.
- :func:`~turbohtml.clean.linkify` parses its input as HTML, so it escapes text and skips ``<script>`` / ``<style>`` /
  ``<a>`` content. Feeding it plain text is fine, but any ``<`` and ``&`` are treated as markup, not literal characters.
  For plain-text-only work that must not be HTML-parsed, use :class:`~turbohtml.clean.LinkDetector`.
