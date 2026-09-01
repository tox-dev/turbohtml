#################
 From urlextract
#################

.. package-meta:: urlextract lipoja/URLExtract

`urlextract <https://github.com/lipoja/URLExtract>`_ pulls the URLs out of a run of plain text. It works from IANA's
top-level-domain list. A regular expression locates every TLD in the text, then each hit grows left and right to the
stop characters that bound a URL and the result has to pass a host check. urlextract downloads the list on first use and
caches it on disk, and ``update()`` or ``update_when_older(days)`` refresh that cache. ``find_urls`` returns the matched
strings, ``only_unique`` de-duplicates them, ``get_indices`` pairs each with its offsets, and ``has_urls`` answers the
yes/no question. Its scope stops at *locating* URLs in text: it has no opinion about HTML.

turbohtml covers the same ground with :class:`~turbohtml.clean.LinkDetector`, whose ``find`` hands back a
:class:`~turbohtml.clean.LinkSpan` per match with the offsets, the matched text and a normalized ``url``. turbohtml
compiles the TLD table into the extension instead of fetching one at runtime, and runs the scan in C, so there is no
cache directory, no first-call network round trip and no per-process warm-up. :func:`~turbohtml.clean.linkify` goes one
step further and rewrites HTML into ``<a>`` tags, which urlextract does not attempt.

*************************
 turbohtml vs urlextract
*************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - urlextract
    - - Scope
      - Locate links in text *and* rewrite HTML into ``<a>`` tags, markup-aware
      - Locate URLs in text only
    - - Result
      - A :class:`~turbohtml.clean.LinkSpan` per match: offsets, matched text, normalized ``url``, ``is_email``
      - The matched string, with its offsets when you ask for them
    - - TLD list
      - IANA's, compiled into the extension and versioned with the release; ``tlds=`` adds your own
      - IANA's, downloaded on first use and cached on disk; ``update()`` refreshes it
    - - Network and state
      - None; the same input gives the same answer on every machine
      - Downloads and caches the TLD list; the answer follows whatever the cache holds
    - - Phone numbers
      - ``tel:`` links from the numbering plans via :class:`~turbohtml.clean.PhoneNumbers`
      - None
    - - Performance
      - C scan (see below)
      - Pure-Python regex plus per-match validation
    - - Typing
      - Fully annotated, ``py.typed``
      - Typed public surface
    - - Dependencies
      - Single package, C extension bundled
      - ``platformdirs``, ``uritools``, ``idna``, ``filelock``
    - - Maintenance
      - Active, part of the turbohtml project
      - Active

Feature overlap
===============

The detection surface ports one-to-one:

- ``URLExtract().find_urls(text)`` -> :meth:`LinkDetector().find(text) <turbohtml.clean.LinkDetector.find>`, returning a
  list of spans rather than a list of strings.
- ``find_urls(text, only_unique=True)`` -> :meth:`find(text, unique=True) <turbohtml.clean.LinkDetector.find>`.
- ``find_urls(text, get_indices=True)`` -> nothing to ask for: every :class:`~turbohtml.clean.LinkSpan` carries
  ``start`` and ``end``.
- ``find_urls(text, with_schema_only=True)`` -> ``LinkDetector(bare_domains=False)``, which detects only URLs whose
  scheme is written.
- ``has_urls(text)`` -> :meth:`~turbohtml.clean.LinkDetector.has_link`.
- ``extract_email`` -> the ``emails`` argument, on by default.
- ``gen_urls(text)`` -> :meth:`~turbohtml.clean.LinkDetector.find` returns a list; iterate it. The C scan is a single
  linear pass with no per-match allocation, so there is nothing for a generator to defer.

What turbohtml adds
===================

- :func:`~turbohtml.clean.linkify` rewrites HTML, leaving URLs that already sit inside an ``<a>``, inside
  ``<script>``/``<style>``, or inside caller-named skip tags untouched. urlextract reports strings; the anchor
  construction, the entity escaping and the "is this already a link" walk would be yours to write.
- A normalized ``url`` on every span: ``mailto:`` for an address, ``http://`` for a bare domain, the text itself for a
  URL that carries its own scheme. ``LinkSpan.text`` keeps the original substring.
- Phone numbers as ``tel:`` links through :class:`~turbohtml.clean.PhoneNumbers`, with the parsed
  :class:`~turbohtml.clean.PhoneNumber` on the span.
- A :class:`~turbohtml.clean.Linkify` configuration object with callbacks that can adjust or veto each link.
- No runtime download and no cache: the TLD table is part of the build, so a sandboxed or offline process behaves the
  same as a connected one, and two machines running the same release agree.

What urlextract has that turbohtml does not
===========================================

- ``check_dns=True``, which resolves each candidate host before reporting it. turbohtml never touches the network.
  Filter the spans yourself if you need this.
- Bare IP addresses. urlextract reports ``192.168.1.1/abc``; turbohtml links an IP only when its scheme is written, as
  in ``http://192.168.1.1/abc``. The same holds for a single-label host: ``http://localhost:8000/`` links,
  ``localhost:8000`` does not, because without a scheme there is nothing to tell the name apart from a word.
- ``ignore_list`` and ``permit_list``. Filter the returned spans on ``span.url``, which is the set membership test
  urlextract runs over its own results.
- A configurable result cap. ``_limit`` and ``URLExtractError`` guard against a pathological input; the C scan is a
  single linear pass, so there is no blow-up to cap.
- Tunable boundaries: ``set_stop_chars_left``/``set_stop_chars_right``, ``set_after_tld_chars`` and ``add_enclosure``.
  turbohtml's rules are fixed -- brackets, parentheses and braces balance, and trailing sentence punctuation is trimmed
  -- and are not configurable.
- ``update()`` and ``update_when_older()``. The table moves when you upgrade turbohtml. Pass ``tlds=`` for a suffix IANA
  does not list.
- A command-line entry point. urlextract ships a ``urlextract`` command; turbohtml's CLI has no link-extraction
  subcommand.

Performance
===========

.. bench-table::
    :file: bench/urlextract.json

urlextract only locates URLs in plain text, so the comparison is on detection alone. The gap on the presence test is the
widest: :meth:`~turbohtml.clean.LinkDetector.has_link` stops at the first match, while ``has_urls`` drives the generator
that scans for every top-level domain in the input, so a link near the start of a long document costs turbohtml the
bytes up to it and urlextract the whole document.

****************
 How to migrate
****************

Swap the import and build a reusable :class:`~turbohtml.clean.LinkDetector` instead of a ``URLExtract``:

.. code-block:: python

    # urlextract
    from urlextract import URLExtract

    urls = URLExtract().find_urls("see https://example.com")
    # -> a list of str, ["https://example.com"]

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `urlextract <https://github.com/lipoja/URLExtract>`__
      - turbohtml
    - - ``URLExtract().find_urls(text)``
      - :meth:`LinkDetector().find(text) <turbohtml.clean.LinkDetector.find>`
    - - ``find_urls(text, only_unique=True)``
      - :meth:`find(text, unique=True) <turbohtml.clean.LinkDetector.find>`
    - - ``find_urls(text, get_indices=True)``
      - ``span.start`` / ``span.end``, always present
    - - ``find_urls(text, with_schema_only=True)``
      - ``LinkDetector(bare_domains=False)``
    - - ``has_urls(text)``
      - :meth:`~turbohtml.clean.LinkDetector.has_link`
    - - ``extract_email``
      - the ``emails`` argument
    - - ``update()`` / ``update_when_older()``
      - nothing: the table is compiled in; ``tlds=`` extends it
    - - (rewrite HTML yourself)
      - :func:`~turbohtml.clean.linkify`

.. testcode::

    from turbohtml.clean import LinkDetector

    detector = LinkDetector()
    print([span.text for span in detector.find("see https://example.com")])
    print([span.url for span in detector.find("a.com, b.com, a.com", unique=True)])

.. testoutput::

    ['https://example.com']
    ['http://a.com', 'http://b.com']

To rewrite HTML rather than list matches, reach for :func:`~turbohtml.clean.linkify`, which has no urlextract
counterpart:

.. testcode::

    from turbohtml.clean import linkify

    print(linkify("visit example.com for more"))

.. testoutput::

    visit <a href="http://example.com" rel="nofollow">example.com</a> for more

**********************
 Gotchas and pitfalls
**********************

- ``find_urls`` returns strings; :meth:`~turbohtml.clean.LinkDetector.find` returns spans. Take ``span.text`` for the
  substring as written, or ``span.url`` for the href-ready form -- they differ for a bare domain and for an address.
- De-duplication keys differ. ``only_unique`` compares the matched strings, so ``example.com`` and
  ``http://example.com`` are two results; ``unique=True`` compares the normalized ``url``, so they are one.
- urlextract reports an address as a URL only with ``extract_email=True``; turbohtml detects addresses by default. Pass
  ``emails=False`` to turn them off.
- Build the detector once and reuse it. urlextract compiles its regex per instance and turbohtml compiles its
  configuration per instance, so constructing one per call wastes the same work in both.
- :func:`~turbohtml.clean.linkify` parses its input as HTML, so ``<`` and ``&`` are markup, not literal characters. For
  plain-text-only work use :class:`~turbohtml.clean.LinkDetector`.
