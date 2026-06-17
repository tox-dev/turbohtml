#############
 Performance
#############

Every number here comes from `pyperf <https://pyperf.readthedocs.io>`_ on CPython 3.14.6 (a release build) on an Apple
M4 running macOS 26. pyperf runs each case in isolated worker processes and reports the mean. The corpora are real
documents: `Project Gutenberg's War and Peace <https://www.gutenberg.org/ebooks/2600>`_, the `WHATWG HTML specification
source <https://github.com/whatwg/html/blob/main/source>`_, the `ECMAScript specification
<https://github.com/tc39/ecma262>`_, and a size-weighted sample of `web-platform-tests
<https://github.com/web-platform-tests/wpt>`_ pages. Reproduce any section with ``tox -e bench <suite>``, where the
suite is one of ``escape``, ``unescape``, ``tokenize``, ``parse``, ``query``, ``serialize``, ``build``, or ``edit``.
Numbers vary with input and hardware.

**********
 Escaping
**********

:func:`turbohtml.escape` against the standard library's :func:`python:html.escape`. It gains the most on text that needs
little escaping, where the SIMD scan classifies sixteen bytes at a time and copies clean stretches in bulk; the gap
narrows on tiny strings, where call overhead dominates.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20

    - - input
      - turbohtml
      - html.escape
    - - tiny plain (64 B)
      - 0.04 µs
      - 0.11 µs
    - - medium markup (4 KiB)
      - 2.18 µs
      - 7.19 µs
    - - no-op prose (4 MiB)
      - 0.11 ms
      - 2.52 ms
    - - book text (3 MiB)
      - 0.60 ms
      - 2.55 ms
    - - book HTML (4 MiB)
      - 1.20 ms
      - 4.52 ms
    - - spec HTML, dense (4 MiB)
      - 4.87 ms
      - 12.7 ms
    - - UCS-2 plain (4 MiB)
      - 0.70 ms
      - 2.68 ms
    - - UCS-2 markup (4 MiB)
      - 3.59 ms
      - 11.3 ms
    - - UCS-4 plain (4 MiB)
      - 0.93 ms
      - 5.33 ms
    - - UCS-4 markup (4 MiB)
      - 3.91 ms
      - 19.7 ms

*******************
 Markup (escaping)
*******************

:func:`turbohtml.markup.escape` against `markupsafe <https://markupsafe.palletsprojects.com>`_'s own C escape, both
returning a ``Markup``. The inputs are the small, mostly-clean strings a template engine interpolates under autoescape,
markupsafe's hottest path. turbohtml builds the safe string in C in a single call, where markupsafe pays a Python
``escape`` frame and ``Markup`` construction per call, so it runs roughly two to three times faster.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20

    - - input
      - turbohtml
      - markupsafe
    - - clean (8 B)
      - 63 ns
      - 188 ns
    - - clean (32 B)
      - 71 ns
      - 207 ns
    - - clean (256 B)
      - 138 ns
      - 458 ns
    - - name with ``'`` and ``&``
      - 87 ns
      - 218 ns
    - - escape-heavy markup
      - 147 ns
      - 358 ns

*********
 Linkify
*********

:func:`turbohtml.linkify.linkify` against `bleach <https://bleach.readthedocs.io>`_'s ``linkify``, the HTML-aware
linkifier it succeeds, and `linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`_, the pure-Python scanner
markdown-it-py pulls in. bleach and turbohtml both parse the HTML and rewrite it; linkify-it-py only finds the matches
and does not rewrite, so it does strictly less work, yet turbohtml is faster than both. The C candidate scan and
turbohtml's own tree carry it past bleach's html5lib pass by five to twenty times.

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    - - input
      - turbohtml
      - bleach
      - linkify-it-py
    - - comment (1 link, 1 email)
      - 2.6 µs
      - 53 µs
      - 29 µs
    - - prose (1 KiB)
      - 48 µs
      - 269 µs
      - 310 µs
    - - markup (4 KiB)
      - 120 µs
      - 1580 µs
      - 723 µs

**********
 Sanitize
**********

:func:`turbohtml.sanitizer.sanitize` against `bleach <https://bleach.readthedocs.io>`_'s ``clean`` (its end-of-life
predecessor, on html5lib) and `nh3 <https://nh3.readthedocs.io>`_ (the Rust ammonia binding). All three parse, filter to
an allowlist, and reserialize; the inputs are realistic user-generated content with a few disallowed tags and a
dangerous attribute mixed in. turbohtml runs the whole filtering walk in C, so it beats even nh3, and it leaves bleach
far behind.

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    - - input
      - turbohtml
      - nh3
      - bleach
    - - comment (1 link, 1 script)
      - 1.8 µs
      - 6.9 µs
      - 107 µs
    - - post (4 KiB)
      - 52 µs
      - 152 µs
      - 2570 µs

************
 Unescaping
************

:func:`turbohtml.unescape` against :func:`python:html.unescape`. It gains the most on entity-heavy input, where the
standard library pays a Python call per match; turbohtml hops between ``&`` occurrences and bulk-copies the clean spans
between references.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20

    - - input
      - turbohtml
      - html.unescape
    - - tiny plain (64 B)
      - 0.02 µs
      - 0.03 µs
    - - medium dense refs (4 KiB)
      - 8.26 µs
      - 69.0 µs
    - - numeric refs (4 KiB)
      - 6.00 µs
      - 78.9 µs
    - - book HTML, real refs (4 MiB)
      - 2.50 ms
      - 7.91 ms
    - - escaped book HTML (5 MiB)
      - 1.87 ms
      - 19.3 ms
    - - dense refs (4 MiB)
      - 10.1 ms
      - 73.2 ms
    - - UCS-2 refs (4 MiB)
      - 2.67 ms
      - 18.0 ms

************
 Tokenizing
************

:func:`turbohtml.tokenize` against :class:`python:html.parser.HTMLParser` (driven with no-op handlers) and `html5lib
<https://html5lib.readthedocs.io>`_'s pure-Python tokenizer. The closest case is a document dominated by a single text
node, where the standard library's regex performs one C scan; wherever markup appears, the state machine is roughly ten
times faster.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18

    - - input
      - turbohtml
      - html.parser
      - html5lib
    - - typical markup
      - 31.6 µs
      - 441 µs
      - 815 µs
    - - text-heavy prose
      - 0.54 µs
      - 2.8 µs
      - 143 µs
    - - attribute-heavy
      - 19.4 µs
      - 298 µs
      - 809 µs
    - - script-heavy
      - 12.2 µs
      - 156 µs
      - 486 µs
    - - entity-heavy
      - 20.8 µs
      - 196 µs
      - 1.21 ms
    - - wpt page (0.6 kB)
      - 1.5 µs
      - 17.7 µs
      - 47.5 µs
    - - wpt page (4 kB)
      - 12.9 µs
      - 169 µs
      - 421 µs
    - - wpt page (9.6 kB)
      - 30.0 µs
      - 359 µs
      - 1.16 ms
    - - wpt page (92 kB)
      - 336 µs
      - 4.02 ms
      - 9.02 ms
    - - wpt page, CJK (124 kB)
      - 586 µs
      - 8.43 ms
      - 22.4 ms
    - - whatwg spec (235 kB)
      - 685 µs
      - 7.46 ms
      - 19.3 ms
    - - ecmascript spec (3 MB)
      - 6.10 ms
      - 54.9 ms
      - 180 ms
    - - whatwg spec source (7.9 MB)
      - 36.6 ms
      - 390 ms
      - 856 ms

*********
 Parsing
*********

:func:`turbohtml.parse` builds a full WHATWG document tree, against the other Python tree builders: `lxml
<https://lxml.de>`_, `selectolax <https://github.com/rushter/selectolax>`_ (`lexbor <https://lexbor.com>`_),
`BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/bs4/doc/>`_ over ``html.parser``, and html5lib. turbohtml
runs roughly two to five times faster than the C parsers and 30 to 80 times faster than the pure-Python ones, while
building the WHATWG tree that lxml's libxml2 does not.

.. list-table::
    :header-rows: 1
    :widths: 26 13 12 12 14 12

    - - input
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
      - html5lib
    - - wpt page (0.6 kB)
      - 1.3 µs
      - 3.3 µs
      - 6.8 µs
      - 61.3 µs
      - 101 µs
    - - wpt page (4 kB)
      - 10.6 µs
      - 27.1 µs
      - 42.0 µs
      - 438 µs
      - 615 µs
    - - wpt page (9.6 kB)
      - 27.5 µs
      - 73.2 µs
      - 106 µs
      - 836 µs
      - 1.45 ms
    - - wpt page (92 kB)
      - 254 µs
      - 633 µs
      - 917 µs
      - 15.1 ms
      - 16.6 ms
    - - wpt page, CJK (124 kB)
      - 505 µs
      - 1.43 ms
      - 2.29 ms
      - 20.7 ms
      - 29.5 ms
    - - whatwg spec (235 kB)
      - 498 µs
      - 1.23 ms
      - 1.78 ms
      - 25.3 ms
      - 31.0 ms
    - - ecmascript spec (3 MB)
      - 4.31 ms
      - 17.4 ms
      - 15.7 ms
      - 193 ms
      - 260 ms
    - - whatwg spec source (7.9 MB)
      - 26.8 ms
      - 83.1 ms
      - 94.0 ms
      - 1.63 s
      - 1.55 s

**********
 Querying
**********

Each library parses the document once, then the timed call runs one query. ``find`` collects every ``<a>`` element the
way each library reaches for it (turbohtml's :meth:`~turbohtml.Node.find_all`, lxml's XPath ``findall``, selectolax's
and BeautifulSoup's selectors). A tag-only query resolves the name to an interned atom and walks the subtree comparing
integers, with no per-element string built and no matcher dispatch, so it runs ahead of lxml's C XPath engine and
several times ahead of selectolax and BeautifulSoup.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18 18

    - - find every <a>
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
    - - wpt page (4 kB)
      - 0.2 µs
      - 0.5 µs
      - 2.4 µs
      - 5.9 µs
    - - wpt page (9.6 kB)
      - 0.3 µs
      - 0.6 µs
      - 2.8 µs
      - 9.5 µs
    - - wpt page (92 kB)
      - 15.7 µs
      - 24.6 µs
      - 46.3 µs
      - 206 µs

``select`` runs the CSS selector ``div a[href]`` (turbohtml's :meth:`~turbohtml.Node.select`, lxml's `cssselect
<https://github.com/scrapy/cssselect>`_, selectolax's ``css``, BeautifulSoup's `soupsieve
<https://github.com/facelessuser/soupsieve>`_). Because turbohtml compiles the selector against the tree once and then
compares interned integer atoms, it runs from twice to over forty times faster than lxml and over a hundred times faster
than BeautifulSoup.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18 18

    - - select ``div a[href]``
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
    - - wpt page (4 kB)
      - 0.3 µs
      - 15.1 µs
      - 2.5 µs
      - 41.9 µs
    - - wpt page (9.6 kB)
      - 0.4 µs
      - 16.9 µs
      - 3.0 µs
      - 64.3 µs
    - - wpt page (92 kB)
      - 12.7 µs
      - 33.2 µs
      - 47.4 µs
      - 2.12 ms

*************
 Serializing
*************

Serializing a parsed document back to HTML: turbohtml's :attr:`~turbohtml.Node.html`, lxml's ``tostring``, selectolax's
``html``, and BeautifulSoup's ``decode``. turbohtml scans each text run for the next character that needs escaping --
two code points at a time with the same SWAR lane probes :func:`~turbohtml.escape` uses -- and bulk-copies the clean
spans, recovering each special's position from the lane mask, and reserves the whole-document buffer up front so the
output grows in one allocation. It serializes three to six times faster than lxml, over three times faster than
selectolax, and fifty to sixty times faster than BeautifulSoup.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18 18

    - - serialize to HTML
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
    - - wpt page (4 kB)
      - 3.7 µs
      - 19.1 µs
      - 12.6 µs
      - 204 µs
    - - wpt page (9.6 kB)
      - 9.5 µs
      - 53.2 µs
      - 30.6 µs
      - 481 µs
    - - wpt page (92 kB)
      - 105 µs
      - 385 µs
      - 339 µs
      - 6.30 ms

**********
 Building
**********

The write path: construct a ``<ul>`` of ``N`` ``<li>`` rows from scratch (each with a ``class``, a ``data`` attribute,
and a text child), then serialize it, the work an editor or template engine does. turbohtml's arena allocation and
interned attribute names make construction cheaper than lxml's libxml2 nodes and far cheaper than BeautifulSoup's Python
objects. selectolax is parse-only, so it has no entry.

.. list-table::
    :header-rows: 1
    :widths: 28 24 24 24

    - - build a list
      - turbohtml
      - lxml
      - BeautifulSoup
    - - 100 rows
      - 56.2 µs
      - 133 µs
      - 782 µs
    - - 1000 rows
      - 556 µs
      - 1.36 ms
      - 7.55 ms
    - - 10000 rows
      - 5.27 ms
      - 13.7 ms
      - 83.0 ms

*********
 Editing
*********

Editing a parsed tree: tag every ``<a>`` with ``rel="nofollow"``, a link-rewriting pass. Each library parses once
outside the timed region, then the timed call walks its links and sets the attribute (turbohtml through the live
:attr:`~turbohtml.Element.attrs` mapping, lxml through ``Element.set``, BeautifulSoup through item assignment).
selectolax mutation is limited, so it has no entry.

.. list-table::
    :header-rows: 1
    :widths: 28 24 24 24

    - - tag every link
      - turbohtml
      - lxml
      - BeautifulSoup
    - - wpt page (4 kB)
      - 195 ns
      - 688 ns
      - 5.82 µs
    - - wpt page (9.6 kB)
      - 295 ns
      - 760 ns
      - 9.58 µs
    - - wpt page (92 kB)
      - 18.4 µs
      - 40.9 µs
      - 215 µs
