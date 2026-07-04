##############
 From courlan
##############

.. package-meta:: courlan adbar/courlan

`courlan <https://github.com/adbar/courlan>`_ is the URL cleaner and filter underneath ``trafilatura``. It scrubs the
transport damage out of scraped URLs (``clean_url``/``scrub_url``), canonicalizes them for a crawl (``normalize_url``:
query sorting, tracker removal, a strict content-parameter allowlist, a path-based language filter), decides whether a
URL is worth fetching (``check_url``, ``is_navigation_page``, spam and content-type heuristics), pulls the followable
links out of a page by regex (``extract_links``/``filter_links``), and manages a crawl frontier in memory
(``UrlStore``). It is used wherever ``trafilatura`` scrapes: web-corpus building, focused crawling, deduplicating a link
set before fetching.

turbohtml covers the URL-hygiene half of that surface in :mod:`turbohtml.extract`. Three functions -- :func:`clean_url
<turbohtml.extract.clean_url>`, :func:`normalize_url <turbohtml.extract.normalize_url>`, :func:`extract_links
<turbohtml.extract.extract_links>` -- share one frozen :class:`~turbohtml.extract.UrlCleaning` config, produce the
canonical form the `WHATWG URL standard <https://url.spec.whatwg.org/>`_ defines, and read links from the real parsed
DOM rather than a markup regex. The crawl-policy half (frontier management, spam and navigation heuristics, HTTP
probing) stays out of scope by design.

**********************
 turbohtml vs courlan
**********************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - courlan
    - - Scope
      - WHATWG HTML parser with a URL-hygiene module (:mod:`turbohtml.extract`)
      - URL cleaning, filtering, and crawl-frontier management for ``trafilatura``
    - - Feature breadth
      - clean, normalize, and link-extract over the parsed DOM, one frozen config
      - clean/scrub/normalize/check, regex link extraction, ``UrlStore``, spam and navigation heuristics, a CLI
    - - Performance
      - ~2x per URL on cleaning, 1.4x-2.4x on link extraction including the parse (see below)
      - baseline
    - - Typing
      - fully typed, ships ``py.typed`` and stubs
      - ships type hints
    - - Dependencies
      - native C extension, no Python runtime deps
      - pure Python, depends on ``tld`` for public-suffix lookups
    - - Maintenance
      - actively developed
      - actively maintained under the ``trafilatura`` umbrella

Feature overlap
===============

The URL-hygiene surface ports 1:1:

- ``courlan.clean_url(url)`` -> :func:`turbohtml.extract.clean_url`, with ``scrub_url``'s markup-damage recovery folded
  in (whitespace and control-character stripping, ``<![CDATA[]]>`` unwrap, ``&amp;`` un-escape, truncation at a stray
  ``<``/``>``/``"``).
- ``courlan.normalize_url(url, strict=..., language=..., trailing_slash=...)`` ->
  :func:`~turbohtml.extract.normalize_url` with a :class:`~turbohtml.extract.UrlCleaning` config: query sorting, tracker
  removal, the strict content-parameter allowlist, trailing-slash folding.
- ``courlan.extract_links(page, url=..., external_bool=True, language=...)`` -> :func:`~turbohtml.extract.extract_links`
  with ``external_only=True``.
- ``courlan.is_external(url, reference)`` -> ``external_only=`` on :func:`~turbohtml.extract.extract_links`.
- The keyword-argument spread across courlan's signatures collapses onto one immutable
  :class:`~turbohtml.extract.UrlCleaning`; :meth:`UrlCleaning.w3lib <turbohtml.extract.UrlCleaning.w3lib>` reproduces
  ``w3lib.url.canonicalize_url``'s mode (drop the fragment, keep every non-tracker parameter).

What turbohtml adds
===================

- Link extraction reads anchors from the real WHATWG DOM, not a regex over the markup: links inside comments or scripts
  never leak in, a ``<base href>`` is honored per HTML spec 4.2.3, and repeated navigation targets are cleaned once and
  deduplicated across their ``http``/``https`` and trailing-slash twins.
- Output follows the WHATWG URL serializer, the form a browser's address bar or an ``href`` getter returns (root slash
  on special URLs, host punycoded to ASCII, empty path segments preserved).
- ``query_allow`` and ``query_deny`` on :class:`~turbohtml.extract.UrlCleaning` give a per-call keep-list and drop-list
  (``w3lib.url.url_query_cleaner``'s keep and ``remove=True`` modes), which courlan does not expose.
- ``external_only`` splits links on the registrable domain (eTLD+1), the same public-suffix boundary courlan reaches for
  through the ``tld`` package: ``spam.example.co.uk`` and ``example.co.uk`` count as one site, ``a.co.uk`` and
  ``b.co.uk`` as two. The suffix comes from the shipped IANA and `Public Suffix List <https://publicsuffix.org/>`_
  tables in C, so there is no ``tld`` dependency and no Python fallback.
- One frozen config drives all three functions, no per-call keyword drift, and no Python runtime dependency.

What courlan has that turbohtml does not
========================================

- **Crawl-frontier management.** ``UrlStore`` (deduplicating, host-bucketed, visit-tracked in-memory URL storage) has no
  equivalent; turbohtml cleans URLs but does not manage a crawl. Keep ``courlan.UrlStore`` for that layer.
- **Crawl-policy filters.** ``check_url``'s content-type, spam/adult, and site-structure filters,
  ``is_navigation_page``, ``is_not_crawlable``, ``filter_links``, and the ``with_redirects`` HTTP probe are out of
  scope. They are fetch policy, not URL hygiene; keep courlan where you need them.
- **Content-based language scoring.** courlan's language filter can fall back to locale scoring of the content;
  turbohtml consults only URL-based markers (a leading path segment, a ``lang``/``language`` query parameter, an
  anchor's ``hreflang``, and in strict mode a language subdomain). No equivalent for the content-scoring path.
- **Punycode-to-Unicode and slash collapsing.** courlan decodes punycode hosts to Unicode and collapses repeated
  slashes; turbohtml keeps the WHATWG forms (ASCII host, empty segments preserved). No flag toggles this.
- **A URL-cleaning command line.** courlan ships a CLI for its URL operations. turbohtml now ships ``python -m
  turbohtml`` (minify, convert, sanitize, detect), but none of its subcommands clean URLs; run the extract API from a
  short script for that job.

Performance
===========

:func:`~turbohtml.extract.clean_url` and :func:`~turbohtml.extract.normalize_url` produce the WHATWG canonical form (the
serialization a browser's address bar would show) and layer courlan's crawl canonicalization (query sorting, tracker
removal, the strict allowlist, the language filter) on top, about 2x faster per URL:

.. bench-table::
    :file: bench/courlan.json

:func:`~turbohtml.extract.extract_links` reads anchors from the real WHATWG DOM instead of regex-scanning the markup, so
links inside comments or scripts never leak in, a ``<base href>`` is honored, and pages whose navigation repeats the
same targets clean each spelling once -- 1.4x-2.4x ahead over real saved pages even with the parse in the loop:

.. bench-table::
    :file: bench/courlan-2.json

Over the 290 URLs in courlan's own test suite the two libraries return identical output for 66% of inputs and agree up
to the WHATWG root-slash serialization for 90%; every remaining divergence is deliberate and listed under
:ref:`courlan-divergences`.

****************
 How to migrate
****************

Swap the import: ``courlan``'s free functions move to :mod:`turbohtml.extract`, and the keyword arguments courlan
spreads across signatures collapse onto :class:`~turbohtml.extract.UrlCleaning`.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `courlan <https://github.com/adbar/courlan>`__
      - turbohtml
    - - ``courlan.clean_url(url)``
      - :func:`turbohtml.extract.clean_url`
    - - ``courlan.scrub_url(url)``
      - folded into :func:`~turbohtml.extract.clean_url`
    - - ``courlan.normalize_url(url, strict=..., language=..., trailing_slash=...)``
      - :func:`~turbohtml.extract.normalize_url` with :class:`~turbohtml.extract.UrlCleaning`
    - - ``courlan.check_url(url, ...)``
      - :func:`~turbohtml.extract.clean_url` (returns the URL, not a ``(url, domain)`` tuple)
    - - ``courlan.extract_links(page, url=..., external_bool=True, language=...)``
      - :func:`~turbohtml.extract.extract_links` with ``external_only=True``
    - - ``courlan.is_external(url, reference)``
      - ``external_only=`` on :func:`~turbohtml.extract.extract_links`

The keyword arguments courlan spreads across its functions live on one immutable config:

.. testcode::

    from turbohtml.extract import UrlCleaning, clean_url, normalize_url

    print(clean_url(" https://www.Example.ORG:443/dir/../page?utm_source=rss&id=7&amp;lang=en "))
    print(normalize_url("http://test.net/foo?post=abc&page=2&session=x", UrlCleaning(strict=True)))
    print(clean_url("https://example.org/de/beitrag", UrlCleaning(language="en")))

.. testoutput::

    https://www.example.org/page?id=7&lang=en
    http://test.net/foo?page=2&post=abc
    None

Link extraction takes the page markup plus the URL it was fetched from, and returns a set of cleaned absolute URLs;
``external_only=True`` keeps only the links that leave the site:

.. testcode::

    from turbohtml.extract import extract_links

    page = '<a href="/about">about</a> <a href="https://other.example/x?fbclid=1">out</a>'
    print(sorted(extract_links(page, "https://site.example/dir/")))
    print(sorted(extract_links(page, "https://site.example/dir/", external_only=True)))

.. testoutput::

    ['https://other.example/x', 'https://site.example/about']
    ['https://other.example/x']

.. _courlan-divergences:

**********************
 Gotchas and pitfalls
**********************

turbohtml follows the WHATWG URL standard where courlan makes its own choices. Each of these shows up in the 10% of
courlan's suite where outputs differ beyond the root slash:

- **Root slash.** A host-only URL serializes as ``http://test.org/`` (the URL standard's serializer always emits the
  path of a special URL); courlan strips it to ``http://test.org``.
- **Punycode direction.** A Unicode host converts *to* ASCII (``münchen.de`` becomes ``xn--mnchen-3ya.de``, what a
  browser's ``href`` getter returns); courlan decodes punycode to Unicode.
- **Repeated slashes.** ``//404.html`` stays ``//404.html``; the standard's path parser preserves empty segments, and
  servers may distinguish them. courlan collapses them.
- **Junk stays rejected.** ``http://1234`` or ``http://ab`` return ``None`` from :func:`~turbohtml.extract.clean_url`;
  courlan's ``clean_url`` passes them through and only ``check_url`` rejects.

Behavioral differences to watch when porting call sites:

- ``extract_links`` returns every surviving link by default; ``external_only=True`` restricts to other sites. courlan's
  ``external_bool`` flag instead *splits* the set: ``False`` means internal links only. Filter the result by host when
  you need the internal half.
- The site boundary for ``external_only`` is the registrable domain (eTLD+1), the same public-suffix rule courlan
  applies through the ``tld`` package, so ``spam.example.co.uk`` and ``example.co.uk`` are one site while sibling
  subdomains such as ``a.co.uk`` and ``b.co.uk`` are two.
- ``UrlCleaning(language=...)`` rejects in :func:`~turbohtml.extract.clean_url` and
  :func:`~turbohtml.extract.extract_links` but never in :func:`~turbohtml.extract.normalize_url`, which is total;
  courlan's ``normalize_url`` raises ``ValueError`` on a language mismatch.
- turbohtml keeps a non-tracker fragment by default (``#page2``, text fragments) like courlan, but
  ``UrlCleaning(strict=True)`` or ``strip_fragment=True`` drops it; courlan couples fragment removal to ``strict`` only.

The content-type, spam/adult, navigation-page, and site-structure filters of ``check_url``/``filter_links``, the
``with_redirects`` HTTP probe, the domain blocklist, and the embedded-URL salvage of ``scrub_url`` (recovering a target
from a ``twitter.com/share?url=...`` wrapper) are out of scope: they are crawl policy, not URL hygiene. The language
filter is the URL-based subset (path segment, ``lang`` parameter, and in strict mode a language subdomain); courlan's
fuller language filter, which can score the linked content, is not consulted.
