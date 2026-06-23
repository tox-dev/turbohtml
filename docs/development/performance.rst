#############
 Performance
#############

Every number here comes from `pyperf <https://pyperf.readthedocs.io>`_ on CPython 3.14.6 (a release build) on an Apple
M4 running macOS 26. pyperf runs each case in isolated worker processes and reports the mean. The corpora are real
documents: `Project Gutenberg's War and Peace <https://www.gutenberg.org/ebooks/2600>`_, the `WHATWG HTML specification
source <https://github.com/whatwg/html/blob/main/source>`_, the `ECMAScript specification
<https://github.com/tc39/ecma262>`_, and a size-weighted sample of `web-platform-tests
<https://github.com/web-platform-tests/wpt>`_ pages. Reproduce any section with ``tox -e bench <suite>``, where the
suite is one of ``escape``, ``unescape``, ``tokenize``, ``parse``, ``fragment``, ``query``, ``text``, ``xpath``,
``path``, ``serialize``, ``build``, ``edit``, ``navigate``, ``links``, ``extract``, ``chain``, ``htmlparser``,
``markup``, ``minify``, ``tables``, ``linkify``, ``markdown``, ``sanitize``, ``structured``, or ``article``. Numbers
vary with input and hardware.

**********
 Escaping
**********

:func:`turbohtml.escape` against the standard library's :func:`python:html.escape`. It gains the most on text that needs
little escaping, where the SIMD scan classifies sixteen bytes at a time and copies clean stretches in bulk; the gap
narrows on tiny strings, where call overhead dominates.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - html.escape
      - speed-up
    - - tiny plain (64 B)
      - 0.04 µs
      - 0.11 µs
      - 2.8x
    - - medium markup (4 KiB)
      - 2.27 µs
      - 7.19 µs
      - 3.2x
    - - no-op prose (4 MiB)
      - 0.11 ms
      - 2.53 ms
      - 23.0x
    - - book text (3 MiB)
      - 0.66 ms
      - 2.59 ms
      - 3.9x
    - - book HTML (4 MiB)
      - 1.26 ms
      - 4.58 ms
      - 3.6x
    - - spec HTML, dense (4 MiB)
      - 4.98 ms
      - 12.7 ms
      - 2.5x
    - - UCS-2 plain (4 MiB)
      - 0.70 ms
      - 2.44 ms
      - 3.5x
    - - UCS-2 markup (4 MiB)
      - 3.36 ms
      - 11.1 ms
      - 3.3x
    - - UCS-4 plain (4 MiB)
      - 0.92 ms
      - 5.29 ms
      - 5.8x
    - - UCS-4 markup (4 MiB)
      - 3.93 ms
      - 19.4 ms
      - 4.9x

*******************
 Markup (escaping)
*******************

:func:`turbohtml.migration.markupsafe.escape` against `markupsafe <https://markupsafe.palletsprojects.com>`_'s own C
escape, both returning a ``Markup``. The inputs are the small, mostly-clean strings a template engine interpolates under
autoescape, markupsafe's hottest path. turbohtml builds the safe string in C in a single call, where markupsafe pays a
Python ``escape`` frame and ``Markup`` construction per call, so it runs roughly two to three times faster.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - markupsafe
      - speed-up
    - - clean (8 B)
      - 61 ns
      - 185 ns
      - 3.0x
    - - clean (32 B)
      - 67 ns
      - 203 ns
      - 3.0x
    - - clean (256 B)
      - 115 ns
      - 447 ns
      - 3.9x
    - - name with ``'`` and ``&``
      - 84 ns
      - 213 ns
      - 2.5x
    - - escape-heavy markup
      - 141 ns
      - 338 ns
      - 2.4x

The other ``Markup`` operations race markupsafe's own ``Markup`` of the same method. ``striptags`` and ``unescape`` run
on turbohtml's tokenizer and HTML5 reference resolution where markupsafe scans with a regex, and ``format`` and ``join``
escape each untrusted operand through the same C ``escape``.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - operation
      - turbohtml
      - markupsafe
      - speed-up
    - - ``striptags``
      - 1368 ns
      - 2483 ns
      - 1.8x
    - - ``unescape``
      - 273 ns
      - 1114 ns
      - 4.1x
    - - ``format`` (escapes operands)
      - 1662 ns
      - 1973 ns
      - 1.2x
    - - ``join`` (escapes operands)
      - 609 ns
      - 1217 ns
      - 2.0x

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
      - 2.9 µs
      - 52 µs (17.9x)
      - 29 µs (10.0x)
    - - prose (1 KiB)
      - 51 µs
      - 272 µs (5.3x)
      - 310 µs (6.1x)
    - - markup (4 KiB)
      - 127 µs
      - 1562 µs (12.3x)
      - 708 µs (5.6x)

The detection primitive on its own, :meth:`turbohtml.linkify.Detector.find` against ``LinkifyIt().match`` and
:meth:`~turbohtml.linkify.Detector.has_link` against ``LinkifyIt().test``, scans a run of plain text and returns the
spans or a boolean without rewriting any HTML, so this isolates the C scan from the full linkify rewrite above. The
``has_link`` prose row is close because ``test`` short-circuits on the first link near the start.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - detect
      - turbohtml
      - linkify-it-py
      - speed-up
    - - ``find`` comment (1 link, 1 email)
      - 0.6 µs
      - 29.2 µs
      - 48.7x
    - - ``find`` prose (1 KiB)
      - 8.8 µs
      - 309.9 µs
      - 35.2x
    - - ``has_link`` comment
      - 0.3 µs
      - 21.5 µs
      - 71.7x
    - - ``has_link`` prose (1 KiB)
      - 2.7 µs
      - 4.9 µs
      - 1.8x

**********
 Sanitize
**********

:func:`turbohtml.sanitizer.sanitize` against four sanitizers. Three share its allowlist model, where only listed tags
and attributes survive, so a vector nobody anticipated is dropped by default: `nh3 <https://nh3.readthedocs.io>`_ (the
Rust ammonia binding), `bleach <https://bleach.readthedocs.io>`_ (its end-of-life predecessor, on html5lib), and
`html-sanitizer <https://github.com/matthiask/html-sanitizer>`_ (an allowlist over lxml). The fourth, `lxml-html-clean
<https://github.com/fedora-python/lxml_html_clean>`_ (the externalized ``lxml.html.clean.Cleaner``), is a blocklist: it
strips the constructs it knows are dangerous and lets the rest through, a model lxml itself flagged as hard to keep
safe. The inputs are realistic user content with a few disallowed tags and a dangerous attribute mixed in. turbohtml
runs the whole filtering walk in C and leads every alternative, but the model matters more than the microseconds. Prefer
an allowlist, since a blocklist passes anything it did not think to name.

.. list-table::
    :header-rows: 1
    :widths: 22 12 11 11 18 18

    - - input
      - turbohtml
      - nh3
      - bleach
      - lxml-html-clean
      - html-sanitizer
    - - comment (1 link, 1 script)
      - 1.5 µs
      - 5.5 µs (3.7x)
      - 78.1 µs (52.1x)
      - 19.4 µs (12.9x)
      - 45.3 µs (30.2x)
    - - post (4 KiB)
      - 42.1 µs
      - 120.1 µs (2.9x)
      - 1921 µs (45.6x)
      - 497 µs (11.8x)
      - 1504 µs (35.7x)

**********
 Markdown
**********

:meth:`turbohtml.Node.to_markdown` against `markdownify <https://github.com/matthewwithanm/python-markdownify>`_ (on
BeautifulSoup) and `html2text <https://github.com/Alir3z4/html2text>`_ (a streaming ``HTMLParser`` subclass). All three
take an HTML string and return Markdown, so each parses first; turbohtml parses to the WHATWG tree and walks it in C,
where the others build and convert in Python. The single C pass converts a page in tens of microseconds, two orders of
magnitude ahead of markdownify. The ``configured`` row turns the option surface on in all three (underscore emphasis,
reference links, padded tables, full escaping), and turbohtml stays ahead by the same margin.

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    - - input
      - turbohtml
      - markdownify
      - html2text
    - - article (2 KiB)
      - 13 µs
      - 1185 µs (91.2x)
      - 542 µs (41.7x)
    - - list (4 KiB)
      - 23 µs
      - 2381 µs (103.5x)
      - 1143 µs (49.7x)
    - - table (4 KiB)
      - 26 µs
      - 2825 µs (108.7x)
      - 1017 µs (39.1x)
    - - configured (4 KiB)
      - 28 µs
      - 2560 µs (91.4x)
      - 1118 µs (39.9x)
    - - google_doc (4 KiB)
      - 18 µs
      - —
      - 560 µs (31.1x)

The ``google_doc`` row reads the inline-CSS styling a Google Docs export carries (html2text's google_doc mode);
markdownify has no equivalent.

*****************
 Structured data
*****************

:meth:`turbohtml.Document.structured_data` against `extruct <https://github.com/scrapinghub/extruct>`_, the scraper
toolkit it succeeds, extracting JSON-LD, Microdata, and OpenGraph from a product page that carries all three. Both start
from the raw HTML string, so each parses first; extruct builds an lxml tree and runs a separate extractor per syntax,
where turbohtml parses to the WHATWG tree and gathers every format in one C walk, handing back the typed
:class:`~turbohtml.StructuredData` record. The single pass runs roughly nine to eleven times faster.

.. list-table::
    :header-rows: 1
    :widths: 40 28 28 28

    - - input
      - turbohtml
      - extruct
      - speed-up
    - - product page
      - 5.4 µs
      - 59.0 µs
      - 10.9x
    - - catalog (8 KiB)
      - 54.5 µs
      - 494.9 µs
      - 9.1x

********
 Tables
********

:meth:`turbohtml.Node.tables` and :meth:`turbohtml.Element.records` against `pandas <https://pandas.pydata.org>`_'s
``read_html``, the one-call table reader scrapers reach for. Both parse the HTML and extract every ``<table>``,
resolving ``rowspan`` and ``colspan`` into a rectangular grid; ``read_html`` returns a ``DataFrame`` per table and pulls
in NumPy, where turbohtml runs the cell-grid walk in C and hands back plain ``list`` and ``dict`` objects with no added
dependency. The ``rows`` row times :meth:`~turbohtml.Node.tables` (every table as ``list[list[str]]``) and the
``records`` row times :meth:`~turbohtml.Element.records` (the first table keyed by its header), each over a four-column
table of the given height. The single C pass leads from roughly twelve times on the largest table to nearly ninety on
the smallest, where pandas pays its fixed per-frame construction cost.

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    - - input
      - turbohtml
      - pandas
      - speed-up
    - - rows (10 rows)
      - 10.8 µs
      - 943 µs
      - 87.3x
    - - records (10 rows)
      - 15.8 µs
      - 970 µs
      - 61.4x
    - - rows (100 rows)
      - 99.7 µs
      - 2178 µs
      - 21.8x
    - - records (100 rows)
      - 98.5 µs
      - 2165 µs
      - 22.0x
    - - rows (1000 rows)
      - 853 µs
      - 10.6 ms
      - 12.4x
    - - records (1000 rows)
      - 893 µs
      - 13.0 ms
      - 14.6x

********************
 Article extraction
********************

:meth:`turbohtml.Node.article` against `trafilatura <https://trafilatura.readthedocs.io>`_, `readability-lxml
<https://github.com/buriy/python-readability>`_, and `newspaper3k <https://newspaper.readthedocs.io>`_, the article
extractors it succeeds. Each scores the dominant content body and (trafilatura and newspaper3k) harvests the page
metadata beside it; the others build an lxml tree in Python first, where turbohtml does the scoring and the harvest in
one C pass over the parsed tree. The inputs are full pages -- navigation, a scored article, and a footer -- so the
boilerplate the heuristic discounts is part of the measured cost.

.. list-table::
    :header-rows: 1
    :widths: 28 16 18 22 18

    - - input
      - turbohtml
      - trafilatura
      - readability-lxml
      - newspaper3k
    - - post (4 KiB)
      - 23 µs
      - 1.34 ms (58.3x)
      - 1.26 ms (54.8x)
      - 3.52 ms (153.0x)
    - - longform (16 KiB)
      - 70 µs
      - 3.13 ms (44.7x)
      - 2.54 ms (36.3x)
      - 8.97 ms (128.1x)

************
 Unescaping
************

:func:`turbohtml.unescape` against :func:`python:html.unescape` and `w3lib <https://github.com/scrapy/w3lib>`_'s
``replace_entities``, the Scrapy helper that resolves the same references. It gains the most on entity-heavy input,
where the standard library pays a Python call per match and w3lib runs a regular-expression substitution with a Python
callback per match; turbohtml hops between ``&`` occurrences in C and bulk-copies the clean spans between references.

.. list-table::
    :header-rows: 1
    :widths: 38 20 21 21

    - - input
      - turbohtml
      - html.unescape
      - w3lib
    - - tiny plain (64 B)
      - 0.02 µs
      - 0.03 µs (1.5x)
      - 0.25 µs (12.5x)
    - - medium dense refs (4 KiB)
      - 8.10 µs
      - 69.3 µs (8.6x)
      - 116 µs (14.3x)
    - - numeric refs (4 KiB)
      - 5.91 µs
      - 78.9 µs (13.4x)
      - 93.1 µs (15.8x)
    - - book HTML, real refs (4 MiB)
      - 2.51 ms
      - 8.05 ms (3.2x)
      - 13.5 ms (5.4x)
    - - escaped book HTML (5 MiB)
      - 1.86 ms
      - 19.9 ms (10.7x)
      - 35.7 ms (19.2x)
    - - dense refs (4 MiB)
      - 9.91 ms
      - 73.8 ms (7.4x)
      - 119 ms (12.0x)
    - - UCS-2 refs (4 MiB)
      - 2.67 ms
      - 18.4 ms (6.9x)
      - 27.6 ms (10.3x)

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
      - 32.1 µs
      - 437 µs (13.6x)
      - 815 µs (25.4x)
    - - text-heavy prose
      - 0.61 µs
      - 2.9 µs (4.8x)
      - 144 µs (236.1x)
    - - attribute-heavy
      - 20.4 µs
      - 304 µs (14.9x)
      - 807 µs (39.6x)
    - - script-heavy
      - 12.8 µs
      - 158 µs (12.3x)
      - 489 µs (38.2x)
    - - entity-heavy
      - 21.7 µs
      - 198 µs (9.1x)
      - 1.21 ms (55.8x)
    - - wpt page (0.6 kB)
      - 1.6 µs
      - 17.7 µs (11.1x)
      - 48.0 µs (30.0x)
    - - wpt page (4 kB)
      - 13.1 µs
      - 168 µs (12.8x)
      - 420 µs (32.1x)
    - - wpt page (9.6 kB)
      - 31.0 µs
      - 361 µs (11.6x)
      - 1.16 ms (37.4x)
    - - wpt page (92 kB)
      - 354 µs
      - 4.05 ms (11.4x)
      - 8.96 ms (25.3x)
    - - wpt page, CJK (124 kB)
      - 609 µs
      - 8.38 ms (13.8x)
      - 22.4 ms (36.8x)
    - - whatwg spec (235 kB)
      - 687 µs
      - 7.46 ms (10.9x)
      - 19.2 ms (28.0x)
    - - ecmascript spec (3 MB)
      - 6.26 ms
      - 55.5 ms (8.9x)
      - 180 ms (28.8x)
    - - whatwg spec source (7.9 MB)
      - 37.8 ms
      - 390 ms (10.3x)
      - 847 ms (22.4x)

*********
 Parsing
*********

:func:`turbohtml.parse` builds a full WHATWG document tree, against the other Python tree builders: `lxml
<https://lxml.de>`_, `selectolax <https://github.com/rushter/selectolax>`_ and `resiliparse
<https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ (both wrapping `lexbor <https://lexbor.com>`_), `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/bs4/doc/>`_ over ``html.parser``, and html5lib. turbohtml runs on par
with resiliparse, two to four times faster than lxml and selectolax, and 30 to 80 times faster than the pure-Python
builders, while building the WHATWG tree that lxml's libxml2 does not.

resiliparse reaches turbohtml's throughput because its ``HTMLTree.parse`` is a thin call straight into lexbor's native
tree, while selectolax wraps that same engine behind a heavier object layer; the comparison here is parsing only.
resiliparse's wider toolkit, boilerplate and main-content extraction, language detection, and the encoding and archive
utilities it ships for large-scale web-crawl processing, sits outside turbohtml's scope. `gumbo
<https://github.com/google/gumbo-parser>`_, the C WHATWG parser Google released and the engine behind `html5-parser
<https://html5-parser.readthedocs.io>`_, has no row: it is read-oriented, archived upstream, and its Python binding no
longer builds on a current toolchain. turbohtml is the maintained, mutable, typed alternative to that lineage.

.. list-table::
    :header-rows: 1
    :widths: 26 12 11 11 12 13 11

    - - input
      - turbohtml
      - lxml
      - selectolax
      - resiliparse
      - BeautifulSoup
      - html5lib
    - - wpt page (0.6 kB)
      - 1.4 µs
      - 3.3 µs (2.4x)
      - 6.8 µs (4.9x)
      - 3.8 µs (2.7x)
      - 61.3 µs (43.8x)
      - 101 µs (72.1x)
    - - wpt page (4 kB)
      - 11.4 µs
      - 27.1 µs (2.4x)
      - 42.2 µs (3.7x)
      - 12.7 µs (1.1x)
      - 438 µs (38.4x)
      - 620 µs (54.4x)
    - - wpt page (9.6 kB)
      - 29.2 µs
      - 72.9 µs (2.5x)
      - 107 µs (3.7x)
      - 28.1 µs (1.0x)
      - 839 µs (28.7x)
      - 1.45 ms (49.7x)
    - - wpt page (92 kB)
      - 272 µs
      - 631 µs (2.3x)
      - 917 µs (3.4x)
      - 282 µs (1.0x)
      - 15.3 ms (56.3x)
      - 16.7 ms (61.4x)
    - - wpt page, CJK (124 kB)
      - 540 µs
      - 1.43 ms (2.6x)
      - 2.30 ms (4.3x)
      - 543 µs (1.0x)
      - 21.8 ms (40.4x)
      - 29.7 ms (55.0x)
    - - whatwg spec (235 kB)
      - 518 µs
      - 1.22 ms (2.4x)
      - 1.78 ms (3.4x)
      - 505 µs (1.0x)
      - 25.3 ms (48.8x)
      - 30.9 ms (59.7x)
    - - ecmascript spec (3 MB)
      - 4.54 ms
      - 17.4 ms (3.8x)
      - 15.7 ms (3.5x)
      - 5.35 ms (1.2x)
      - 193 ms (42.5x)
      - 262 ms (57.7x)
    - - whatwg spec source (7.9 MB)
      - 28.9 ms
      - 83.1 ms (2.9x)
      - 94.0 ms (3.3x)
      - 30.1 ms (1.0x)
      - 1.62 s (56.1x)
      - 1.53 s (52.9x)

******************
 Fragment parsing
******************

:func:`turbohtml.parse_fragment` parses an ``innerHTML``-style snippet in a container's context rather than a whole
document, against lxml's ``lxml.html.fromstring`` and html5lib's ``parseFragment``. The input is a table-row fragment
parsed in its ``<tbody>`` context, where the WHATWG algorithm's table rules apply. turbohtml runs the same C engine it
uses for whole documents, so it parses the fragment three times faster than lxml and roughly seventy times faster than
the pure-Python html5lib.

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    - - input
      - turbohtml
      - lxml
      - html5lib
    - - table-row fragment (2 kB)
      - 12.6 µs
      - 39.6 µs (3.1x)
      - 867 µs (68.8x)

**********
 Querying
**********

Each library parses the document once, then the timed call runs one query. ``find`` collects every ``<a>`` element the
way each library reaches for it (turbohtml's :meth:`~turbohtml.Node.find_all`, lxml's XPath ``findall``, selectolax's
and BeautifulSoup's selectors). A tag-only query resolves the name to an interned atom and walks the subtree comparing
integers, with no per-element string built and no matcher dispatch, so it runs ahead of lxml's C XPath engine and many
times ahead of selectolax, parsel (Scrapy's cssselect-over-libxml2 selector library), and BeautifulSoup.

.. list-table::
    :header-rows: 1
    :widths: 26 15 15 15 15 15

    - - find every <a>
      - turbohtml
      - lxml
      - selectolax
      - parsel
      - BeautifulSoup
    - - wpt page (4 kB)
      - 0.1 µs
      - 0.5 µs (5.0x)
      - 2.1 µs (21.0x)
      - 3.8 µs (38.0x)
      - 5.7 µs (57.0x)
    - - wpt page (9.6 kB)
      - 0.1 µs
      - 0.5 µs (5.0x)
      - 2.6 µs (26.0x)
      - 4.2 µs (42.0x)
      - 9.3 µs (93.0x)
    - - wpt page (92 kB)
      - 1.9 µs
      - 23.3 µs (12.3x)
      - 45.3 µs (23.8x)
      - 79.5 µs (41.8x)
      - 207 µs (108.9x)

``select`` runs the CSS selector ``div a[href]`` (turbohtml's :meth:`~turbohtml.Node.select`, lxml's `cssselect
<https://github.com/scrapy/cssselect>`_, selectolax's ``css``, parsel's ``css``, BeautifulSoup's `soupsieve
<https://github.com/facelessuser/soupsieve>`_). Because turbohtml compiles the selector against the tree once and then
compares interned integer atoms, a reused query costs tens of nanoseconds: hundreds of times faster than lxml and parsel
(which re-translate the selector to XPath on every call) and over a thousand times faster than BeautifulSoup, narrowing
to roughly twenty times on the largest page where the document walk dominates.

.. list-table::
    :header-rows: 1
    :widths: 26 15 15 15 15 15

    - - select ``div a[href]``
      - turbohtml
      - lxml
      - selectolax
      - parsel
      - BeautifulSoup
    - - wpt page (4 kB)
      - 0.04 µs
      - 15.2 µs (380.0x)
      - 2.5 µs (62.5x)
      - 7.0 µs (175.0x)
      - 41.8 µs (1045.0x)
    - - wpt page (9.6 kB)
      - 0.04 µs
      - 16.2 µs (405.0x)
      - 2.9 µs (72.5x)
      - 8.0 µs (200.0x)
      - 62.8 µs (1570.0x)
    - - wpt page (92 kB)
      - 2.0 µs
      - 33.1 µs (16.6x)
      - 44.8 µs (22.4x)
      - 25.4 µs (12.7x)
      - 2.05 ms (1025.0x)

The relational ``:has()`` pseudo-class is the costliest selector to evaluate, since a naive matcher rescans each
candidate's subtree. turbohtml runs ``div:has(a)`` against the same pages and stays ahead of every alternative: tens of
times faster than lxml and selectolax on the smaller pages, widening to hundreds of times on the large page, and
hundreds to tens of thousands of times faster than BeautifulSoup. The matcher walks each anchor's descendants once and
skips the sibling scan for descendant and child relationships, so the relational lookup keeps the same interned-atom
comparison the flat selectors use.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18 18

    - - select ``div:has(a)``
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
    - - wpt page (4 kB)
      - 0.5 µs
      - 16.3 µs (32.6x)
      - 4.6 µs (9.2x)
      - 128 µs (256.0x)
    - - wpt page (9.6 kB)
      - 0.6 µs
      - 18.1 µs (30.2x)
      - 5.5 µs (9.2x)
      - 152 µs (253.3x)
    - - wpt page (92 kB)
      - 0.04 µs
      - 32.9 µs (822.5x)
      - 37.2 µs (930.0x)
      - 1.38 ms (34500.0x)

A text-content search runs through :meth:`~turbohtml.Node.find_all` with ``text=`` (a regex matched against each
element's collected subtree text), raced against ``BeautifulSoup.find_all(string=...)``; lxml, selectolax, and parsel
expose no equivalent, so this is a two-way race. The predicate runs Python mid-walk, so turbohtml gathers each
candidate's text in C and searches once, staying roughly two to three times ahead of BeautifulSoup and narrowing on the
largest page where the text gathering dominates both sides.

.. list-table::
    :header-rows: 1
    :widths: 44 14 14 14

    - - find ``text=`` regex
      - turbohtml
      - BeautifulSoup
      - speed-up
    - - wpt page (4 kB)
      - 9.3 µs
      - 19.7 µs
      - 2.1x
    - - wpt page (9.6 kB)
      - 13.9 µs
      - 38.2 µs
      - 2.7x
    - - wpt page (92 kB)
      - 741 µs
      - 989 µs
      - 1.3x

XPath 1.0 evaluation runs through :meth:`~turbohtml.Node.xpath`, raced against lxml's libxml2 engine, the XPath that
parsel, pyquery, and html5-parser all wrap (selectolax and BeautifulSoup have none). One expression per feature class
(name tests, the ``//`` abbreviation, attribute, positional, and arithmetic predicates, string and aggregate functions,
a reverse axis, a union, and a computed name test) runs over the 9.6 kB wpt page below; ``tox -e bench xpath`` repeats
the sweep across every page size. turbohtml compiles each expression against the tree once, resolves name tests to
interned atoms, and folds ``//`` to a single ``descendant`` walk, so it leads across the surface. The exception is a
predicate that references ``position()`` (``[1]`` or ``position() <= 3``): it pins the result to proximity order and
disables the ``//`` collapse, so on the largest pages lxml's streaming evaluation closes the gap. The last eight rows
are the lxml/parsel options the parity work added: a ``$variable`` binding, an EXSLT ``re:test`` predicate (turbohtml's
Python :mod:`re` against lxml's C libexslt), an EXSLT ``set:distinct`` node-set reduction (built-in C dispatch on both
sides, so it races C against C), a ``smart_strings`` attribute read, a custom ``extensions=`` function, an
``extensions=`` function whose return becomes a node-set feeding a later ``/@href`` step, a ``namespaces=`` prefix
binding that resolves ``//svg:rect`` against ``{"svg": ".../2000/svg"}`` over a page carrying an SVG block, and a
node-set ``$variable`` bound from a prior result (``$rows/div``, with ``rows`` reused from an earlier ``//div`` query)
fed into a later path step. turbohtml still leads, since lxml resolves the namespace map and option set on every call.
The last row precompiles the expression once with :class:`~turbohtml.XPath` and re-evaluates it, lxml's ``etree.XPath``
doing the same: both skip the per-call parse :meth:`~turbohtml.Node.xpath` pays, and turbohtml's compiled program stays
ahead per evaluation.

.. list-table::
    :header-rows: 1
    :widths: 44 14 14 14

    - - xpath (9.6 kB page)
      - turbohtml
      - lxml
      - speed-up
    - - ``//div``
      - 2.5 µs
      - 11.5 µs
      - 4.6x
    - - ``//a[@href]``
      - 0.6 µs
      - 4.1 µs
      - 6.8x
    - - ``//div//a[@href]``
      - 2.1 µs
      - 10.1 µs
      - 4.8x
    - - ``/html/body/div``
      - 1.1 µs
      - 6.4 µs
      - 5.8x
    - - ``//div//a[1]``
      - 11.1 µs
      - 10.1 µs
      - 0.9x
    - - ``//a[contains(@href, '/')]``
      - 0.5 µs
      - 4.3 µs
      - 8.6x
    - - ``//div[position() <= 3]``
      - 6.6 µs
      - 13.7 µs
      - 2.1x
    - - ``//a/ancestor::div``
      - 0.6 µs
      - 2.6 µs
      - 4.3x
    - - ``//a | //span``
      - 1.0 µs
      - 3.4 µs
      - 3.4x
    - - ``//*[local-name() = 'a']``
      - 5.5 µs
      - 14.3 µs
      - 2.6x
    - - ``count(//a)``
      - 0.6 µs
      - 2.7 µs
      - 4.5x
    - - ``//a[@href=$x]`` (variable)
      - 0.6 µs
      - 4.3 µs
      - 7.2x
    - - ``//a[re:test(@href, ...)]`` (EXSLT)
      - 0.5 µs
      - 4.6 µs
      - 9.2x
    - - ``set:distinct(//a)`` (EXSLT)
      - 0.6 µs
      - 4.0 µs
      - 6.7x
    - - ``//a/@href`` (smart_strings)
      - 0.6 µs
      - 2.6 µs
      - 4.3x
    - - ``ext(//a)`` (extensions)
      - 1.0 µs
      - 3.0 µs
      - 3.0x
    - - ``ext(//a)/@href`` (node-set extension)
      - 1.1 µs
      - 3.2 µs
      - 2.9x
    - - ``//svg:rect`` (namespaces=)
      - 0.9 µs
      - 3.7 µs
      - 4.1x
    - - ``$rows/div`` (node-set variable)
      - 3.9 µs
      - 6.1 µs
      - 1.6x
    - - ``//a[@href]`` (precompiled, reused)
      - 0.5 µs
      - 2.8 µs
      - 5.6x

************
 Node paths
************

:meth:`turbohtml.Element.css_path` and :meth:`~turbohtml.Element.xpath_path` return the unique locator that re-finds an
element from the document root -- a CSS selector and a positional XPath -- against lxml's ``getroottree().getpath()``,
the libxml2 path builder devtools' "copy selector" mirrors. lxml emits only the positional XPath, so ``getpath`` pairs
with both turbohtml methods. Each timed call walks every element in a pre-parsed page and serializes its path, where
turbohtml's ancestor-chain walk under the per-tree lock leads ``getpath`` by roughly three to five times across page
sizes.

.. list-table::
    :header-rows: 1
    :widths: 30 20 20 20 16

    - - input
      - turbohtml css_path
      - turbohtml xpath_path
      - lxml getpath
      - speed-up
    - - wpt page (4 kB)
      - 9.0 µs
      - 7.9 µs
      - 40.3 µs
      - 5.1x
    - - wpt page (9.6 kB)
      - 15.6 µs
      - 14.3 µs
      - 55.2 µs
      - 3.9x
    - - wpt page (92 kB)
      - 701.3 µs
      - 526.4 µs
      - 2539.8 µs
      - 4.8x

**************
 Text content
**************

The ``text`` suite collects the visible text two ways. First, the raw text join off a pre-parsed tree, the ``get_text``
pass: turbohtml's :attr:`~turbohtml.Node.text` property concatenates every descendant text run, against lxml's
``text_content()``, selectolax's ``text()``, and BeautifulSoup's ``get_text()``. turbohtml gathers the runs in one C
walk into a buffer reserved up front, so it leads lxml by a small margin and selectolax and BeautifulSoup by roughly an
order of magnitude. parsel exposes no node-level text collector, so it sits out.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18 18

    - - text content
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
    - - wpt page (4 kB)
      - 0.8 µs
      - 1.2 µs (1.5x)
      - 5.2 µs (6.5x)
      - 6.8 µs (8.5x)
    - - wpt page (9.6 kB)
      - 1.1 µs
      - 1.6 µs (1.5x)
      - 12.1 µs (11.0x)
      - 13.3 µs (12.1x)
    - - wpt page (92 kB)
      - 36.9 µs
      - 47.2 µs (1.3x)
      - 488 µs (13.2x)
      - 368 µs (10.0x)

Second, the layout-aware string-to-text extraction: :meth:`turbohtml.Node.to_text` against `inscriptis
<https://github.com/weblyzard/inscriptis>`_, the layout-aware HTML-to-text renderer it succeeds, `html-text
<https://github.com/zytedata/html-text>`_, Zyte's plainer visible-text extractor, and `resiliparse
<https://github.com/chatnoir-eu/chatnoir-resiliparse>`_'s ``extract_plain_text``. inscriptis and html-text both build an
lxml tree in Python and resiliparse renders text off the lexbor tree it parses to, where turbohtml does the whole layout
in one C walk; inscriptis additionally lays tables out as aligned columns, which html-text and resiliparse skip.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18 18

    - - input
      - turbohtml
      - inscriptis
      - html-text
      - resiliparse
    - - article (2 KiB)
      - 7 µs
      - 163 µs (23.3x)
      - 102 µs (14.6x)
      - 23 µs (3.3x)
    - - table (4 KiB)
      - 28 µs
      - 839 µs (30.0x)
      - 258 µs (9.2x)
      - 52 µs (1.9x)
    - - collapsed (2 KiB)
      - 7 µs
      - --
      - 101 µs (14.4x)
      - --
    - - main (4 KiB)
      - 7 µs
      - --
      - --
      - 21 µs (3.0x)
    - - annotated (4 KiB)
      - 10 µs
      - 202 µs (20.2x)
      - --
      - --

The ``collapsed`` row turns layout guessing off: turbohtml joins the :attr:`~turbohtml.Node.stripped_strings` word
stream against html-text's ``extract_text(guess_layout=False)``; inscriptis and resiliparse have no comparable collapsed
mode. The ``main`` row strips page boilerplate first, :meth:`~turbohtml.Node.main_text` against resiliparse's
``extract_plain_text(main_content=True)``. The ``annotated`` row labels matching elements with spans through
:meth:`~turbohtml.Node.to_annotated_text` against inscriptis's ``get_annotated_text``; html-text and resiliparse have no
annotation surface, so they sit out that row.

*****************
 Tree navigation
*****************

Walking every descendant of a parsed tree: turbohtml's :attr:`~turbohtml.Node.descendants` iterator against lxml's
``iterdescendants()`` and BeautifulSoup's ``descendants``. The ``list(el)``, ``iterdescendants()``, and
``iterancestors()`` family ports to :attr:`~turbohtml.Node.children`, :attr:`~turbohtml.Node.descendants`, and
:attr:`~turbohtml.Node.ancestors`; the descendant walk is the dominant case. Each timed call consumes the whole
iterator, where turbohtml yields interned nodes straight from the arena faster than lxml's libxml2 proxy objects and
BeautifulSoup's Python ``NavigableString`` chain. The descendant walk is one of BeautifulSoup's leaner paths, so the
margin is narrower here than on the query and serialize suites. selectolax exposes no document-wide descendant iterator,
so it has no entry.

.. list-table::
    :header-rows: 1
    :widths: 28 24 24 24

    - - descendant walk
      - turbohtml
      - lxml
      - BeautifulSoup
    - - wpt page (4 kB)
      - 2.0 µs
      - 8.3 µs (4.2x)
      - 2.9 µs (1.5x)
    - - wpt page (9.6 kB)
      - 3.6 µs
      - 12.1 µs (3.4x)
      - 5.0 µs (1.4x)
    - - wpt page (92 kB)
      - 101 µs
      - 295 µs (2.9x)
      - 125 µs (1.2x)

*************
 Serializing
*************

Serializing a parsed document back to HTML: turbohtml's :attr:`~turbohtml.Node.html`, lxml's ``tostring``, selectolax's
``html``, and BeautifulSoup's ``decode``. turbohtml scans each text run for the next character that needs escaping (two
code points at a time with the same SWAR lane probes :func:`~turbohtml.escape` uses) and bulk-copies the clean spans,
recovering each special's position from the lane mask, and reserves the whole-document buffer up front so the output
grows in one allocation. It serializes three to six times faster than lxml, over three times faster than selectolax, and
fifty to sixty times faster than BeautifulSoup.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18 18

    - - serialize to HTML
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
    - - wpt page (4 kB)
      - 3.8 µs
      - 18.7 µs (4.9x)
      - 12.4 µs (3.3x)
      - 198 µs (52.1x)
    - - wpt page (9.6 kB)
      - 9.8 µs
      - 50.7 µs (5.2x)
      - 29.8 µs (3.0x)
      - 478 µs (48.8x)
    - - wpt page (92 kB)
      - 105 µs
      - 381 µs (3.6x)
      - 339 µs (3.2x)
      - 5.95 ms (56.7x)

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
      - 59.6 µs
      - 135 µs (2.3x)
      - 753 µs (12.6x)
    - - 1000 rows
      - 580 µs
      - 1.34 ms (2.3x)
      - 7.40 ms (12.8x)
    - - 10000 rows
      - 5.71 ms
      - 13.5 ms (2.4x)
      - 79.0 ms (13.8x)

The terse :data:`turbohtml.build.E` builder spells the same ``<ul>`` declaratively, raced against the dedicated HTML
generators `dominate <https://github.com/Knio/dominate>`_ and `yattag <https://www.yattag.org>`_. ``E`` is roughly three
times faster than dominate and on par with yattag, and unlike either it returns a real, queryable turbohtml tree rather
than a string. That tree costs about three-quarters again the raw :class:`~turbohtml.Element` constructor above -- the
price of the leading-mapping and per-child dispatch the sugar runs in Python.

.. list-table::
    :header-rows: 1
    :widths: 28 24 24 24

    - - build with ``E``
      - turbohtml
      - dominate
      - yattag
    - - 100 rows
      - 104 µs
      - 320 µs (3.1x)
      - 94 µs (0.9x)
    - - 1000 rows
      - 1.08 ms
      - 3.34 ms (3.1x)
      - 1.06 ms (1.0x)

*********
 Editing
*********

Editing a parsed tree: tag every ``<a>`` with ``rel="nofollow"``, a link-rewriting pass. Each library parses once
outside the timed region, then the timed call walks its links and sets the attribute (turbohtml through the live
:attr:`~turbohtml.Element.attrs` mapping, lxml through ``Element.set``, BeautifulSoup through item assignment).
selectolax mutation is limited, so it has no entry. The last row is a content-setter pass:
:meth:`~turbohtml.Element.set_inner_html` reparses a fixed fragment in the ``<body>``'s context and replaces its
children, against lxml clearing the body and appending ``fragments_fromstring`` and BeautifulSoup clearing it and
appending a reparsed soup. turbohtml does the parse and splice in one C call.

The last row is a second pass: a classList churn that adds then drops a token on every link (turbohtml's
:meth:`~turbohtml.Element.add_class`/:meth:`~turbohtml.Element.remove_class` against lxml's ``classes`` set). The
add-then-remove is a net no-op, so each repeat does equal work. BeautifulSoup has no class-token mutator, so it has no
entry.

.. list-table::
    :header-rows: 1
    :widths: 28 24 24 24

    - - tag every link
      - turbohtml
      - lxml
      - BeautifulSoup
    - - wpt page (4 kB)
      - 70 ns
      - 700 ns (10.0x)
      - 5.7 µs (81.4x)
    - - wpt page (9.6 kB)
      - 72 ns
      - 800 ns (11.1x)
      - 9.3 µs (129.2x)
    - - wpt page (92 kB)
      - 8.4 µs
      - 41.6 µs (5.0x)
      - 212 µs (25.2x)
    - - add/remove a class (92 kB)
      - 11.2 µs
      - 163 µs (14.6x)
      - —
    - - set inner html (9.6 kB)
      - 1.3 µs
      - 5.1 µs (3.9x)
      - 60.6 µs (46.6x)

A third pass is a bulk tag edit over the 92 kB page (839 ``<code>``/``<a>``/``<q>`` elements):
:meth:`~turbohtml.Node.remove` drops each match with its subtree, and :meth:`~turbohtml.Node.strip_tags` unwraps each
match but keeps its content. Both rewrites are destructive, so the timed call parses the page afresh -- the
string-to-result transform these helpers perform -- and races each library's own bulk tag helper. selectolax's
``strip_tags`` drops the matches with their content, so it pairs with ``remove``; w3lib's regex ``remove_tags`` keeps
the content, so it pairs with ``strip_tags`` (pyquery's ``.remove()``/``.unwrap()`` numbers are in its :doc:`migration
page </migration/pyquery>`).

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - bulk strip/remove (92 kB)
      - turbohtml
      - library
      - speed-up
    - - ``remove`` vs selectolax ``strip_tags``
      - 554 µs
      - 1.73 ms
      - 3.1x
    - - ``strip_tags`` vs w3lib ``remove_tags``
      - 607 µs
      - 1.11 ms
      - 1.8x

*******
 Links
*******

The link surface: extract every in-document link, resolve them against a base URL, and rewrite them through a callback.
turbohtml's :meth:`~turbohtml.Node.links`, :meth:`~turbohtml.Node.resolve_links`, and
:meth:`~turbohtml.Node.rewrite_links` against lxml.html's ``iterlinks()``, ``make_links_absolute()``, and
``rewrite_links()`` -- the only other library that walks the link-bearing attributes (``href``, ``src``, ``srcset``,
...) as a set. Each timed call runs one operation over the 92 kB wpt page; extraction is read-only, while absolutize and
rewrite are idempotent once applied. turbohtml walks the attribute set in C where lxml re-resolves each URL in Python,
so it leads by ten to over a hundred times.

.. list-table::
    :header-rows: 1
    :widths: 46 16 16 16

    - - links (92 kB page)
      - turbohtml
      - lxml
      - speed-up
    - - extract (``links`` / ``iterlinks``)
      - 60.6 µs
      - 2.28 ms
      - 37.6x
    - - absolutize (``resolve_links`` / ``make_links_absolute``)
      - 251 µs
      - 2.76 ms
      - 11.0x
    - - rewrite (``rewrite_links``)
      - 22.3 µs
      - 2.38 ms
      - 106.7x

************
 Extraction
************

Pulling values out of a document, the idioms the parsel, pyquery, and w3lib migrations center on. First, reading every
matched node's ``@href`` and visible text off a pre-parsed page: parsel's ``::attr``/``::text`` ``getall`` and a pyquery
``.items()`` read against turbohtml selecting once and reading :meth:`~turbohtml.Element.attr` and
:attr:`~turbohtml.Node.text` off each node. turbohtml compiles the selector once and reads interned atoms, where parsel
re-translates the CSS to XPath on libxml2 per call and pyquery boxes every match in a wrapper object, so it leads by
twenty-five to nearly a hundred times.

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18

    - - extract ``@href`` (per match)
      - turbohtml
      - parsel
      - pyquery
    - - wpt page (4 kB)
      - 0.1 µs
      - 3.9 µs (39.0x)
      - 4.4 µs (44.0x)
    - - wpt page (9.6 kB)
      - 0.1 µs
      - 4.3 µs (43.0x)
      - 4.8 µs (48.0x)
    - - wpt page (92 kB)
      - 8.2 µs
      - 222 µs (27.1x)
      - 542 µs (66.1x)

.. list-table::
    :header-rows: 1
    :widths: 28 18 18 18

    - - extract text (per match)
      - turbohtml
      - parsel
      - pyquery
    - - wpt page (4 kB)
      - 0.1 µs
      - 4.0 µs (40.0x)
      - 4.5 µs (45.0x)
    - - wpt page (9.6 kB)
      - 0.1 µs
      - 4.3 µs (43.0x)
      - 4.9 µs (49.0x)
    - - wpt page (92 kB)
      - 8.0 µs
      - 214 µs (26.8x)
      - 297 µs (37.1x)

Second, reading a document's own URL hints: w3lib's ``get_base_url`` and ``get_meta_refresh`` against turbohtml's
:meth:`~turbohtml.Document.base_url` and :meth:`~turbohtml.Document.meta_refresh`. Both parse the string each call;
w3lib runs a regular-expression pass while turbohtml runs the WHATWG tree builder and reads the hint off the parsed
``<head>``, so the tree builder still comes out ahead of the regex on this small document.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - url hint
      - turbohtml
      - w3lib
      - speed-up
    - - ``base_url`` / ``get_base_url``
      - 2.9 µs
      - 7.4 µs
      - 2.6x
    - - ``meta_refresh`` / ``get_meta_refresh``
      - 3.0 µs
      - 6.5 µs
      - 2.2x

*****************
 Fluent chaining
*****************

A pyquery-style fluent chain over a pre-parsed tree: select every ``<a>``, keep the linked ones, take the first, tag it,
and read its ``href`` (turbohtml's :class:`turbohtml.query.Query` against `pyquery <https://github.com/gawel/pyquery>`_,
whose wrapper delegates to lxml). Both wrappers are thin Python over the underlying engine, so the gap is the engine's:
turbohtml's selector and attribute primitives run in C, and the wrapper avoids a redundant de-duplication when the chain
starts from one node, so it runs roughly ten times faster.

.. list-table::
    :header-rows: 1
    :widths: 28 28 28 16

    - - select, filter, tag, read
      - turbohtml
      - pyquery
      - speed-up
    - - wpt page (4 kB)
      - 0.9 µs
      - 16.0 µs
      - 17.8x
    - - wpt page (9.6 kB)
      - 1.0 µs
      - 16.5 µs
      - 16.5x
    - - wpt page (92 kB)
      - 21.8 µs
      - 278 µs
      - 12.8x

*********************
 html.parser adapter
*********************

:class:`turbohtml.migration.stdlib.HTMLParser` against the standard library's :class:`python:html.parser.HTMLParser`,
both subclassed with the same minimal handler so the comparison is the parser and dispatch cost for the identical
callback-driven programming model. The per-tag Python handler call is a floor both pay, so the margin is narrower than
raw tokenization, but turbohtml's C tokenizer feeding the dispatch still runs it three to four times faster.

.. list-table::
    :header-rows: 1
    :widths: 28 28 28 16

    - - feed and dispatch a page
      - turbohtml
      - html.parser
      - speed-up
    - - wpt page (4 kB)
      - 39.0 µs
      - 168 µs
      - 4.3x
    - - wpt page (9.6 kB)
      - 82.1 µs
      - 362 µs
      - 4.4x
    - - wpt page (92 kB)
      - 1.38 ms
      - 3.99 ms
      - 2.9x
