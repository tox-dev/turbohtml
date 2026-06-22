#############
 Performance
#############

Every number here comes from `pyperf <https://pyperf.readthedocs.io>`_ on CPython 3.14.6 (a release build) on an Apple
M4 running macOS 26. pyperf runs each case in isolated worker processes and reports the mean. The corpora are real
documents: `Project Gutenberg's War and Peace <https://www.gutenberg.org/ebooks/2600>`_, the `WHATWG HTML specification
source <https://github.com/whatwg/html/blob/main/source>`_, the `ECMAScript specification
<https://github.com/tc39/ecma262>`_, and a size-weighted sample of `web-platform-tests
<https://github.com/web-platform-tests/wpt>`_ pages. Reproduce any section with ``tox -e bench <suite>``, where the
suite is one of ``escape``, ``unescape``, ``tokenize``, ``parse``, ``query``, ``xpath``, ``serialize``, ``build``,
``edit``, ``chain``, ``htmlparser``, ``markup``, ``linkify``, ``markdown``, or ``sanitize``. Numbers vary with input and
hardware.

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
      - 2.27 µs
      - 7.19 µs
    - - no-op prose (4 MiB)
      - 0.11 ms
      - 2.53 ms
    - - book text (3 MiB)
      - 0.66 ms
      - 2.59 ms
    - - book HTML (4 MiB)
      - 1.26 ms
      - 4.58 ms
    - - spec HTML, dense (4 MiB)
      - 4.98 ms
      - 12.7 ms
    - - UCS-2 plain (4 MiB)
      - 0.70 ms
      - 2.44 ms
    - - UCS-2 markup (4 MiB)
      - 3.36 ms
      - 11.1 ms
    - - UCS-4 plain (4 MiB)
      - 0.92 ms
      - 5.29 ms
    - - UCS-4 markup (4 MiB)
      - 3.93 ms
      - 19.4 ms

*******************
 Markup (escaping)
*******************

:func:`turbohtml.migration.markupsafe.escape` against `markupsafe <https://markupsafe.palletsprojects.com>`_'s own C
escape, both returning a ``Markup``. The inputs are the small, mostly-clean strings a template engine interpolates under
autoescape, markupsafe's hottest path. turbohtml builds the safe string in C in a single call, where markupsafe pays a
Python ``escape`` frame and ``Markup`` construction per call, so it runs roughly two to three times faster.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20

    - - input
      - turbohtml
      - markupsafe
    - - clean (8 B)
      - 61 ns
      - 185 ns
    - - clean (32 B)
      - 67 ns
      - 203 ns
    - - clean (256 B)
      - 115 ns
      - 447 ns
    - - name with ``'`` and ``&``
      - 84 ns
      - 213 ns
    - - escape-heavy markup
      - 141 ns
      - 338 ns

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
      - 52 µs
      - 29 µs
    - - prose (1 KiB)
      - 51 µs
      - 272 µs
      - 310 µs
    - - markup (4 KiB)
      - 127 µs
      - 1562 µs
      - 708 µs

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
      - 5.5 µs
      - 78.1 µs
      - 19.4 µs
      - 45.3 µs
    - - post (4 KiB)
      - 42.1 µs
      - 120.1 µs
      - 1921 µs
      - 497 µs
      - 1504 µs

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
      - 1185 µs
      - 542 µs
    - - list (4 KiB)
      - 23 µs
      - 2381 µs
      - 1143 µs
    - - table (4 KiB)
      - 26 µs
      - 2825 µs
      - 1017 µs
    - - configured (4 KiB)
      - 28 µs
      - 2560 µs
      - 1118 µs
    - - google_doc (4 KiB)
      - 18 µs
      - —
      - 560 µs

The ``google_doc`` row reads the inline-CSS styling a Google Docs export carries (html2text's google_doc mode);
markdownify has no equivalent.

*************
 Layout text
*************

:meth:`turbohtml.Node.to_text` against `inscriptis <https://github.com/weblyzard/inscriptis>`_, the layout-aware
HTML-to-text renderer it succeeds. Both keep the visual structure and lay tables out as aligned columns; inscriptis
builds an lxml tree and a CSS model in Python, where turbohtml does the whole layout in one C walk.

.. list-table::
    :header-rows: 1
    :widths: 40 28 28

    - - input
      - turbohtml
      - inscriptis
    - - article (2 KiB)
      - 7 µs
      - 163 µs
    - - table (4 KiB)
      - 28 µs
      - 839 µs
    - - annotated (4 KiB)
      - 10 µs
      - 202 µs

The ``annotated`` row labels matching elements with spans through :meth:`~turbohtml.Node.to_annotated_text` against
inscriptis's ``get_annotated_text``.

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
      - 0.03 µs
      - 0.25 µs
    - - medium dense refs (4 KiB)
      - 8.10 µs
      - 69.3 µs
      - 116 µs
    - - numeric refs (4 KiB)
      - 5.91 µs
      - 78.9 µs
      - 93.1 µs
    - - book HTML, real refs (4 MiB)
      - 2.51 ms
      - 8.05 ms
      - 13.5 ms
    - - escaped book HTML (5 MiB)
      - 1.86 ms
      - 19.9 ms
      - 35.7 ms
    - - dense refs (4 MiB)
      - 9.91 ms
      - 73.8 ms
      - 119 ms
    - - UCS-2 refs (4 MiB)
      - 2.67 ms
      - 18.4 ms
      - 27.6 ms

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
      - 437 µs
      - 815 µs
    - - text-heavy prose
      - 0.61 µs
      - 2.9 µs
      - 144 µs
    - - attribute-heavy
      - 20.4 µs
      - 304 µs
      - 807 µs
    - - script-heavy
      - 12.8 µs
      - 158 µs
      - 489 µs
    - - entity-heavy
      - 21.7 µs
      - 198 µs
      - 1.21 ms
    - - wpt page (0.6 kB)
      - 1.6 µs
      - 17.7 µs
      - 48.0 µs
    - - wpt page (4 kB)
      - 13.1 µs
      - 168 µs
      - 420 µs
    - - wpt page (9.6 kB)
      - 31.0 µs
      - 361 µs
      - 1.16 ms
    - - wpt page (92 kB)
      - 354 µs
      - 4.05 ms
      - 8.96 ms
    - - wpt page, CJK (124 kB)
      - 609 µs
      - 8.38 ms
      - 22.4 ms
    - - whatwg spec (235 kB)
      - 687 µs
      - 7.46 ms
      - 19.2 ms
    - - ecmascript spec (3 MB)
      - 6.26 ms
      - 55.5 ms
      - 180 ms
    - - whatwg spec source (7.9 MB)
      - 37.8 ms
      - 390 ms
      - 847 ms

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
      - 3.3 µs
      - 6.8 µs
      - 3.8 µs
      - 61.3 µs
      - 101 µs
    - - wpt page (4 kB)
      - 11.4 µs
      - 27.1 µs
      - 42.2 µs
      - 12.7 µs
      - 438 µs
      - 620 µs
    - - wpt page (9.6 kB)
      - 29.2 µs
      - 72.9 µs
      - 107 µs
      - 28.1 µs
      - 839 µs
      - 1.45 ms
    - - wpt page (92 kB)
      - 272 µs
      - 631 µs
      - 917 µs
      - 282 µs
      - 15.3 ms
      - 16.7 ms
    - - wpt page, CJK (124 kB)
      - 540 µs
      - 1.43 ms
      - 2.30 ms
      - 543 µs
      - 21.8 ms
      - 29.7 ms
    - - whatwg spec (235 kB)
      - 518 µs
      - 1.22 ms
      - 1.78 ms
      - 505 µs
      - 25.3 ms
      - 30.9 ms
    - - ecmascript spec (3 MB)
      - 4.54 ms
      - 17.4 ms
      - 15.7 ms
      - 5.35 ms
      - 193 ms
      - 262 ms
    - - whatwg spec source (7.9 MB)
      - 28.9 ms
      - 83.1 ms
      - 94.0 ms
      - 30.1 ms
      - 1.62 s
      - 1.53 s

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
      - 0.5 µs
      - 2.1 µs
      - 3.8 µs
      - 5.7 µs
    - - wpt page (9.6 kB)
      - 0.1 µs
      - 0.5 µs
      - 2.6 µs
      - 4.2 µs
      - 9.3 µs
    - - wpt page (92 kB)
      - 1.9 µs
      - 23.3 µs
      - 45.3 µs
      - 79.5 µs
      - 207 µs

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
      - 15.2 µs
      - 2.5 µs
      - 7.0 µs
      - 41.8 µs
    - - wpt page (9.6 kB)
      - 0.04 µs
      - 16.2 µs
      - 2.9 µs
      - 8.0 µs
      - 62.8 µs
    - - wpt page (92 kB)
      - 2.0 µs
      - 33.1 µs
      - 44.8 µs
      - 25.4 µs
      - 2.05 ms

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
      - 16.3 µs
      - 4.6 µs
      - 128 µs
    - - wpt page (9.6 kB)
      - 0.6 µs
      - 18.1 µs
      - 5.5 µs
      - 152 µs
    - - wpt page (92 kB)
      - 0.04 µs
      - 32.9 µs
      - 37.2 µs
      - 1.38 ms

XPath 1.0 evaluation runs through :meth:`~turbohtml.Node.xpath`, raced against lxml's libxml2 engine, the XPath that
parsel, pyquery, and html5-parser all wrap (selectolax and BeautifulSoup have none). One expression per feature class
(name tests, the ``//`` abbreviation, attribute, positional, and arithmetic predicates, string and aggregate functions,
a reverse axis, a union, and a computed name test) runs over the 9.6 kB wpt page below; ``tox -e bench xpath`` repeats
the sweep across every page size. turbohtml compiles each expression against the tree once, resolves name tests to
interned atoms, and folds ``//`` to a single ``descendant`` walk, so it leads across the surface. The exception is a
predicate that references ``position()`` (``[1]`` or ``position() <= 3``): it pins the result to proximity order and
disables the ``//`` collapse, so on the largest pages lxml's streaming evaluation closes the gap. The last four rows are
the lxml/parsel options the parity work added: a ``$variable`` binding, an EXSLT ``re:test`` predicate (turbohtml's
Python :mod:`re` against lxml's C libexslt), a ``smart_strings`` attribute read, and a custom ``extensions=`` function.
turbohtml still leads, since lxml resolves the namespace map and option set on every call.

.. list-table::
    :header-rows: 1
    :widths: 44 14 14

    - - xpath (9.6 kB page)
      - turbohtml
      - lxml
    - - ``//div``
      - 2.5 µs
      - 11.5 µs
    - - ``//a[@href]``
      - 0.6 µs
      - 4.1 µs
    - - ``//div//a[@href]``
      - 2.1 µs
      - 10.1 µs
    - - ``/html/body/div``
      - 1.1 µs
      - 6.4 µs
    - - ``//div//a[1]``
      - 11.1 µs
      - 10.1 µs
    - - ``//a[contains(@href, '/')]``
      - 0.5 µs
      - 4.3 µs
    - - ``//div[position() <= 3]``
      - 6.6 µs
      - 13.7 µs
    - - ``//a/ancestor::div``
      - 0.6 µs
      - 2.6 µs
    - - ``//a | //span``
      - 1.0 µs
      - 3.4 µs
    - - ``//*[local-name() = 'a']``
      - 5.5 µs
      - 14.3 µs
    - - ``count(//a)``
      - 0.6 µs
      - 2.7 µs
    - - ``//a[@href=$x]`` (variable)
      - 0.6 µs
      - 4.3 µs
    - - ``//a[re:test(@href, ...)]`` (EXSLT)
      - 0.5 µs
      - 4.6 µs
    - - ``//a/@href`` (smart_strings)
      - 0.6 µs
      - 2.6 µs
    - - ``ext(//a)`` (extensions)
      - 1.0 µs
      - 3.0 µs

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
      - 18.7 µs
      - 12.4 µs
      - 198 µs
    - - wpt page (9.6 kB)
      - 9.8 µs
      - 50.7 µs
      - 29.8 µs
      - 478 µs
    - - wpt page (92 kB)
      - 105 µs
      - 381 µs
      - 339 µs
      - 5.95 ms

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
      - 135 µs
      - 753 µs
    - - 1000 rows
      - 580 µs
      - 1.34 ms
      - 7.40 ms
    - - 10000 rows
      - 5.71 ms
      - 13.5 ms
      - 79.0 ms

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
      - 70 ns
      - 700 ns
      - 5.7 µs
    - - wpt page (9.6 kB)
      - 72 ns
      - 800 ns
      - 9.3 µs
    - - wpt page (92 kB)
      - 8.4 µs
      - 41.6 µs
      - 212 µs

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
    :widths: 28 36 36

    - - select, filter, tag, read
      - turbohtml
      - pyquery
    - - wpt page (4 kB)
      - 0.9 µs
      - 16.0 µs
    - - wpt page (9.6 kB)
      - 1.0 µs
      - 16.5 µs
    - - wpt page (92 kB)
      - 21.8 µs
      - 278 µs

*********************
 html.parser adapter
*********************

:class:`turbohtml.migration.stdlib.HTMLParser` against the standard library's :class:`python:html.parser.HTMLParser`,
both subclassed with the same minimal handler so the comparison is the parser and dispatch cost for the identical
callback-driven programming model. The per-tag Python handler call is a floor both pay, so the margin is narrower than
raw tokenization, but turbohtml's C tokenizer feeding the dispatch still runs it three to four times faster.

.. list-table::
    :header-rows: 1
    :widths: 28 36 36

    - - feed and dispatch a page
      - turbohtml
      - html.parser
    - - wpt page (4 kB)
      - 39.0 µs
      - 168 µs
    - - wpt page (9.6 kB)
      - 82.1 µs
      - 362 µs
    - - wpt page (92 kB)
      - 1.38 ms
      - 3.99 ms
