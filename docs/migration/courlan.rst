##############
 From courlan
##############

.. package-meta:: courlan adbar/courlan

`courlan <https://github.com/adbar/courlan>`_ is the URL cleaner and filter underneath ``trafilatura``:
``clean_url``/``scrub_url`` strip markup damage, ``normalize_url`` canonicalizes, and ``extract_links`` pulls the
followable links out of a page with regexes. turbohtml covers that surface in :mod:`turbohtml.extract`, sharing one
frozen :class:`~turbohtml.extract.UrlCleaning` config across the three calls.

***************
 Why turbohtml
***************

:func:`turbohtml.extract.clean_url` and :func:`~turbohtml.extract.normalize_url` produce the canonical form the `WHATWG
URL standard <https://url.spec.whatwg.org/>`_ defines -- the serialization a browser's address bar would show -- and
layer courlan's crawl canonicalization (query sorting, tracker removal, the strict allowlist, the language filter) on
top, about 2x faster per URL:

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

*************
 The renames
*************

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

**************************************
 Deliberate divergences and omissions
**************************************

turbohtml follows the WHATWG URL standard where courlan makes its own choices; each of these shows up in the 10% of
courlan's suite where outputs differ beyond the root slash:

- **Root slash.** A host-only URL serializes as ``http://test.org/`` (the URL standard's serializer always emits the
  path of a special URL); courlan strips it to ``http://test.org``.
- **Punycode direction.** A Unicode host converts *to* ASCII (``münchen.de`` becomes ``xn--mnchen-3ya.de``, what a
  browser's ``href`` getter returns); courlan decodes punycode to Unicode.
- **Repeated slashes.** ``//404.html`` stays ``//404.html``; the standard's path parser preserves empty segments, and
  servers may distinguish them. courlan collapses them.
- **Junk stays rejected.** ``http://1234`` or ``http://ab`` return ``None`` from :func:`~turbohtml.extract.clean_url`;
  courlan's ``clean_url`` passes them through and only ``check_url`` rejects.

The content-type, spam/adult, navigation-page, and site-structure filters of ``check_url``/``filter_links``, the
``with_redirects`` HTTP probe, the domain blocklist, and the embedded-URL salvage of ``scrub_url`` (recovering a target
from a ``twitter.com/share?url=...`` wrapper) are deliberately out of scope: they are crawl policy, not URL hygiene. The
language filter is the URL-based subset (path segment, ``lang`` parameter, and in strict mode a language subdomain);
courlan's Babel-backed locale scoring is not consulted.

**********
 Pitfalls
**********

- ``extract_links`` returns every surviving link by default; ``external_only=True`` restricts to other sites. courlan's
  ``external_bool`` flag instead *splits* the set: ``False`` means internal links only. Filter the result by host when
  you need the internal half.
- The site boundary for ``external_only`` is the ``www.``-less host, with subdomains counting as internal; courlan
  compares registrable domains through the public-suffix list, so ``spam.example.co.uk`` versus ``example.co.uk`` can
  classify differently.
- ``UrlCleaning(language=...)`` rejects in :func:`~turbohtml.extract.clean_url` and
  :func:`~turbohtml.extract.extract_links` but never in :func:`~turbohtml.extract.normalize_url`, which is total;
  courlan's ``normalize_url`` raises ``ValueError`` on a language mismatch.
- turbohtml keeps a non-tracker fragment by default (``#page2``, text fragments) like courlan, but
  ``UrlCleaning(strict=True)`` or ``strip_fragment=True`` drops it; courlan couples fragment removal to ``strict`` only.
