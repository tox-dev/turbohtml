#############
 Performance
#############

Every number here comes from `pyperf <https://pyperf.readthedocs.io>`_ on CPython 3.14.6 (a release build) on an Apple
M4 running macOS 26. pyperf runs each case in isolated worker processes and reports the mean; the harness prints the
run-to-run standard deviation beside it as ``±N%`` so a real gap reads apart from noise, and these tables quote the
mean. For the lowest-noise figures, tune the machine with ``pyperf system tune`` first (and ``sudo pyperf system reset``
after); pyperf options like ``--rigorous`` or ``--affinity`` pass straight through after the ``tox -e bench`` command.
Operations that mutate the tree they are handed -- the edits, the content setters, link absolutization -- are timed on a
fresh parse rebuilt before each iteration (the rebuild itself untimed), so the figure is the repeatable cost of the
mutation alone rather than a tree a prior iteration already changed; read-path operations reuse one cached parse and
time only the query. The corpora are real documents: `Project Gutenberg's War and Peace
<https://www.gutenberg.org/ebooks/2600>`_, the `WHATWG HTML specification source
<https://github.com/whatwg/html/blob/main/source>`_, the `ECMAScript specification <https://github.com/tc39/ecma262>`_,
a size-weighted sample of `web-platform-tests <https://github.com/web-platform-tests/wpt>`_ pages for the parse and
tokenize suites, and, for the read-path suites, real saved web pages -- a blog, a news article, and a product blog from
the `mozilla/readability <https://github.com/mozilla/readability>`_ test corpus -- so the selector, link, and edit
operations run against genuine nested structure rather than the layout fixtures, which carry none. The harness
benchmarks each competitor in its own isolated ``uv`` venv -- turbohtml in a venv of its own as the shared baseline --
so one library's dependency pins never perturb another's. Every table below is one harness operation, so each is
reproducible with ``tox -e bench <command>``, where the command is ``core`` (turbohtml's own baseline for every
operation), an operation name (the cross-competitor table), a package name (that competitor's own report), or ``all``.
Most operations are a single call; a few aggregate workloads (``build``, ``build-e``) sweep a size, and the
``construct`` and ``emit`` breakdowns decompose that write path into the constructor and the serializer in isolation.
Numbers vary with input and hardware.

**********
 Escaping
**********

:func:`turbohtml.escape` against the standard library's :func:`python:html.escape`. It gains the most on text that needs
little escaping, where the SIMD scan classifies sixteen bytes at a time and copies clean stretches in bulk; the gap
narrows on tiny strings, where call overhead dominates.

.. bench-table::
    :file: bench/escaping.json

*******************
 Markup (escaping)
*******************

:func:`turbohtml.migration.markupsafe.escape` against `markupsafe <https://markupsafe.palletsprojects.com>`_'s own C
escape, both returning a ``Markup``. The inputs are the small, mostly-clean strings a template engine interpolates under
autoescape, markupsafe's hottest path. turbohtml builds the safe string in C in a single call, where markupsafe pays a
Python ``escape`` frame and ``Markup`` construction per call, so it runs roughly two to three times faster.

.. bench-table::
    :file: bench/markup-escaping.json

The other ``Markup`` operations race markupsafe's own ``Markup`` of the same method. ``striptags`` and ``unescape`` run
on turbohtml's tokenizer and HTML5 reference resolution where markupsafe scans with a regex, and ``format`` and ``join``
escape each untrusted operand through the same C ``escape``.

.. bench-table::
    :file: bench/markup-escaping-2.json

*********
 Linkify
*********

:func:`turbohtml.clean.linkify` against `bleach <https://bleach.readthedocs.io>`_'s ``linkify``, the HTML-aware
linkifier it succeeds, and `linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`_, the pure-Python scanner
markdown-it-py pulls in. bleach and turbohtml both parse the HTML and rewrite it; linkify-it-py only finds the matches
and does not rewrite, so it does strictly less work, yet turbohtml is faster than both. The C candidate scan and
turbohtml's own tree carry it past bleach's html5lib pass by five to twenty times.

.. bench-table::
    :file: bench/linkify.json

The detection primitive on its own, :meth:`turbohtml.clean.Detector.find` against ``LinkifyIt().match`` and
:meth:`~turbohtml.clean.Detector.has_link` against ``LinkifyIt().test``, scans a run of plain text and returns the spans
or a boolean without rewriting any HTML, so this isolates the C scan from the full linkify rewrite above. The
``has_link`` prose row is close because ``test`` short-circuits on the first link near the start.

.. bench-table::
    :file: bench/linkify-2.json

**********
 Sanitize
**********

:func:`turbohtml.clean.sanitize` against four sanitizers. Three share its allowlist model, where only listed tags and
attributes survive, so a vector nobody anticipated is dropped by default: `nh3 <https://nh3.readthedocs.io>`_ (the Rust
ammonia binding), `bleach <https://bleach.readthedocs.io>`_ (its end-of-life predecessor, on html5lib), and
`html-sanitizer <https://github.com/matthiask/html-sanitizer>`_ (an allowlist over lxml). The fourth, `lxml-html-clean
<https://github.com/fedora-python/lxml_html_clean>`_ (the externalized ``lxml.html.clean.Cleaner``), is a blocklist: it
strips the constructs it knows are dangerous and lets the rest through, a model lxml itself flagged as hard to keep
safe. The inputs are realistic user content with a few disallowed tags and a dangerous attribute mixed in. turbohtml
runs the whole filtering walk in C and leads every alternative, but the model matters more than the microseconds. Prefer
an allowlist, since a blocklist passes anything it did not think to name.

.. bench-table::
    :file: bench/sanitize.json

**********
 Markdown
**********

:meth:`turbohtml.Node.to_markdown` against `markdownify <https://github.com/matthewwithanm/python-markdownify>`_ (on
BeautifulSoup) and `html2text <https://github.com/Alir3z4/html2text>`_ (a streaming ``HTMLParser`` subclass). All three
take an HTML string and return Markdown, so each parses first; turbohtml parses to the WHATWG tree and walks it in C,
where the others build and convert in Python. The single C pass converts a page in tens of microseconds, two orders of
magnitude ahead of markdownify. The ``configured`` row turns the option surface on in all three (underscore emphasis,
reference links, padded tables, full escaping), and turbohtml stays ahead by the same margin.

.. bench-table::
    :file: bench/markdown.json

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

.. bench-table::
    :file: bench/structured-data.json

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

.. bench-table::
    :file: bench/tables.json

********************
 Article extraction
********************

:meth:`turbohtml.Node.article` against `trafilatura <https://trafilatura.readthedocs.io>`_, `readability-lxml
<https://github.com/buriy/python-readability>`_, `newspaper3k <https://newspaper.readthedocs.io>`_, `goose3
<https://goose3.readthedocs.io>`_, `readabilipy <https://readabilipy.readthedocs.io>`_, and `news-please
<https://github.com/fhamborg/news-please>`_, the article extractors it succeeds. Each scores the dominant content body
and (trafilatura, newspaper3k, goose3, and news-please) harvests the page metadata beside it; the lxml-backed four build
their tree in Python first, readabilipy's Python mode parses with html5lib into BeautifulSoup and cleans without
scoring, news-please merges the votes of several such extractors, and turbohtml does the scoring and the harvest in one
C pass over the parsed tree. The inputs are full pages -- navigation, a scored article, and a footer -- so the
boilerplate the heuristic discounts is part of the measured cost.

.. bench-table::
    :file: bench/article-extraction.json

****************************
 Boilerplate classification
****************************

:func:`turbohtml.extract.boilerplate` against `justext <https://github.com/miso-belica/jusText>`_ and `boilerpy3
<https://github.com/jmriebold/BoilerPy3>`_, the per-block boilerplate classifiers. All three segment the page into units
and mark each good or boilerplate; justext scores every paragraph in Python over an lxml tree (length, link density,
stopword density), boilerpy3 classifies the blocks of its own SAX stream with boilerpipe's rules, and turbohtml scores
the tree once in C and classifies the units in a thin Python layer. The inputs are the article-extraction pages, so the
navigation and footer each classifier must reject are part of the measured cost.

.. bench-table::
    :file: bench/boilerplate-classification.json

************
 Unescaping
************

:func:`turbohtml.unescape` against :func:`python:html.unescape` and `w3lib <https://github.com/scrapy/w3lib>`_'s
``replace_entities``, the Scrapy helper that resolves the same references. It gains the most on entity-heavy input,
where the standard library pays a Python call per match and w3lib runs a regular-expression substitution with a Python
callback per match; turbohtml hops between ``&`` occurrences in C and bulk-copies the clean spans between references.

.. bench-table::
    :file: bench/unescaping.json

************
 Tokenizing
************

:func:`turbohtml.tokenize` against :class:`python:html.parser.HTMLParser` (driven with no-op handlers) and `html5lib
<https://html5lib.readthedocs.io>`_'s pure-Python tokenizer. The closest case is a document dominated by a single text
node, where the standard library's regex performs one C scan; wherever markup appears, the state machine is roughly ten
times faster.

.. bench-table::
    :file: bench/tokenizing.json

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

.. bench-table::
    :file: bench/parsing.json

******************
 Fragment parsing
******************

:func:`turbohtml.parse_fragment` parses an ``innerHTML``-style snippet in a container's context rather than a whole
document, against lxml's ``lxml.html.fromstring`` and html5lib's ``parseFragment``. The input is a table-row fragment
parsed in its ``<tbody>`` context, where the WHATWG algorithm's table rules apply. turbohtml runs the same C engine it
uses for whole documents, so it parses the fragment three times faster than lxml and roughly seventy times faster than
the pure-Python html5lib.

.. bench-table::
    :file: bench/fragment-parsing.json

**********
 Querying
**********

Each library parses the document once, then the timed call runs one query. ``find`` collects every ``<a>`` element the
way each library reaches for it (turbohtml's :meth:`~turbohtml.Node.find_all`, lxml's XPath ``findall``, selectolax's
and BeautifulSoup's selectors). A tag-only query resolves the name to an interned atom and walks the subtree comparing
integers, with no per-element string built and no matcher dispatch, so it runs ahead of lxml's C XPath engine and many
times ahead of selectolax, parsel (Scrapy's cssselect-over-libxml2 selector library), and BeautifulSoup.

.. bench-table::
    :file: bench/querying.json

``select`` runs the CSS selector ``div a[href]`` (turbohtml's :meth:`~turbohtml.Node.select`, lxml's `cssselect
<https://github.com/scrapy/cssselect>`_, selectolax's ``css``, parsel's ``css``, BeautifulSoup's `soupsieve
<https://github.com/facelessuser/soupsieve>`_). Because turbohtml compiles the selector against the tree once and then
matches by comparing interned integer atoms, it stays in the low microseconds across these pages. lxml and parsel
re-translate the selector to XPath through cssselect on every call, which scales with the document and trails by tens of
times on the small blog up to roughly seven hundred times on the spec; BeautifulSoup's soupsieve is hundreds to more
than fifteen hundred times behind, while selectolax, the other compiled engine, stays closest at roughly eleven to forty
times.

.. bench-table::
    :file: bench/querying-2.json

The relational ``:has()`` pseudo-class is the costliest selector to evaluate, since a naive matcher rescans each
candidate's subtree. turbohtml runs ``div:has(a)`` against the same pages and leads every alternative: tens of times
faster than lxml and selectolax on the smaller pages, narrowing to single digits on the link-dense mozilla blog where
the relational match itself does real work, while BeautifulSoup trails by hundreds of times throughout. The matcher
walks each anchor's descendants once and skips the sibling scan for descendant and child relationships, so the
relational lookup keeps the same interned-atom comparison the flat selectors use.

.. bench-table::
    :file: bench/querying-3.json

Per-element matching runs each anchor on the page through a compiled ``div a[href]`` matcher -- the shape a soupsieve
port hits through :mod:`turbohtml.match` and its :meth:`Matcher.match <turbohtml.match.Matcher.match>` (soupsieve is the
only competitor with a compiled per-element match). turbohtml answers each test with the same interned-atom comparison
its ``select`` uses, walking the ancestor chain once per candidate, where soupsieve re-interprets the parsed selector in
Python per element, so the sweep runs 94 to 155 times faster across these pages.

.. bench-table::
    :file: bench/matching.json

A text-content search runs through :meth:`~turbohtml.Node.find_all` with ``text=`` (a regex matched against each
element's collected subtree text), raced against ``BeautifulSoup.find_all(string=...)``; lxml, selectolax, and parsel
expose no equivalent, so this is a two-way race. When the ``text=`` filter is a plain string or a literal (no regex
metacharacters, case-sensitive) compiled pattern, turbohtml gathers each candidate's collected text and matches it in C
-- no Python ``str`` built, no per-element ``re.search`` call -- where BeautifulSoup's ``find_all(string=...)`` walks
the tree in Python, so turbohtml now leads on every page (it previously trailed on the larger ones). A case-insensitive
or otherwise non-literal pattern keeps the per-element Python path.

.. bench-table::
    :file: bench/querying-4.json

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

.. bench-table::
    :file: bench/querying-5.json

************
 Node paths
************

:meth:`turbohtml.Element.css_path` and :meth:`~turbohtml.Element.xpath_path` return the unique locator that re-finds an
element from the document root -- a CSS selector and a positional XPath -- against lxml's ``getroottree().getpath()``,
the libxml2 path builder devtools' "copy selector" mirrors. lxml emits only the positional XPath, so ``getpath`` pairs
with both turbohtml methods. Each timed call walks every element in a pre-parsed page and serializes its path. Both
methods lead ``getpath`` by roughly five times across these pages, narrowing to under threefold on the spec.
:meth:`~turbohtml.Element.css_path` previously rescanned the whole document to test each element's id uniqueness, an
O(N\ :sup:`2`) cost over a page that made it slower than ``getpath`` on id-heavy pages; a cached per-tree id-occurrence
map (dropped with the element index on any mutation) now answers that test in O(1), so ``css_path`` keeps pace with the
positional ``xpath_path``. The ratio on ``getpath`` is against ``xpath_path``, the like-for-like locator.

.. bench-table::
    :file: bench/node-paths.json

**************
 Text content
**************

The ``text`` suite collects the visible text two ways. First, the raw text join off a pre-parsed tree, the ``get_text``
pass: turbohtml's :attr:`~turbohtml.Node.text` property concatenates every descendant text run, against lxml's
``text_content()``, selectolax's ``text()``, and BeautifulSoup's ``get_text()``. turbohtml gathers the runs in one C
walk into a buffer reserved up front, so it leads lxml by a small margin and selectolax and BeautifulSoup by roughly an
order of magnitude. parsel exposes no node-level text collector, so it sits out.

.. bench-table::
    :file: bench/text-content.json

Second, the layout-aware string-to-text extraction: :meth:`turbohtml.Node.to_text` against `inscriptis
<https://github.com/weblyzard/inscriptis>`_, the layout-aware HTML-to-text renderer it succeeds, `html-text
<https://github.com/zytedata/html-text>`_, Zyte's plainer visible-text extractor, and `resiliparse
<https://github.com/chatnoir-eu/chatnoir-resiliparse>`_'s ``extract_plain_text``. inscriptis and html-text both build an
lxml tree in Python and resiliparse renders text off the lexbor tree it parses to, where turbohtml does the whole layout
in one C walk; inscriptis additionally lays tables out as aligned columns, which html-text and resiliparse skip.

.. bench-table::
    :file: bench/text-content-2.json

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

.. bench-table::
    :file: bench/tree-navigation.json

*************
 Serializing
*************

Serializing a parsed document back to HTML: turbohtml's :attr:`~turbohtml.Node.html`, lxml's ``tostring``, selectolax's
``html``, and BeautifulSoup's ``decode``. turbohtml scans each text run for the next character that needs escaping (two
code points at a time with the same SWAR lane probes :func:`~turbohtml.escape` uses) and bulk-copies the clean spans,
recovering each special's position from the lane mask, and reserves the whole-document buffer up front so the output
grows in one allocation. It serializes three to six times faster than lxml, over three times faster than selectolax, and
fifty to sixty times faster than BeautifulSoup.

.. bench-table::
    :file: bench/serializing.json

***********
 Minifying
***********

Minifying a document with :func:`turbohtml.clean.minify`: parse, then serialize once with every fold engaged (collapsing
insignificant whitespace, omitting the WHATWG-optional tags, unquoting attributes, and stripping comments), against
`minify-html <https://github.com/wilsonzlin/minify-html>`_'s Rust minifier on the same folds (its CSS and JS
minification left off for a like-for-like comparison) and `htmlmin <https://github.com/mankyd/htmlmin>`_'s pure-Python
``HTMLParser`` walk. turbohtml parses and emits in C through one preallocated buffer, so with the parse included it runs
about twice as fast as minify-html and fourteen to twenty times faster than htmlmin.

.. bench-table::
    :file: bench/minifying.json

**********
 Building
**********

The write path: construct a ``<ul>`` of ``N`` ``<li>`` rows from scratch (each with a ``class``, a ``data`` attribute,
and a text child), then serialize it, the work an editor or template engine does. turbohtml's arena allocation and
interned attribute names make construction cheaper than lxml's libxml2 nodes and far cheaper than BeautifulSoup's Python
objects. selectolax is parse-only, so it has no entry.

.. bench-table::
    :file: bench/building.json

The ``construct`` and ``emit`` commands split that aggregate into its two halves over the same tree: ``construct``
builds the rows and stops before serialization, and ``emit`` serializes a tree built once outside the timed region. The
split shows where each library spends the time -- turbohtml's arena keeps construction roughly twice as fast as lxml,
and its SWAR serializer pulls emit ahead by nearly six times, while BeautifulSoup pays its Python object cost on both
halves.

.. bench-table::
    :file: bench/building-2.json

.. bench-table::
    :file: bench/building-3.json

The terse :data:`turbohtml.build.E` builder spells the same ``<ul>`` declaratively, raced against the dedicated HTML
generators `dominate <https://github.com/Knio/dominate>`_ and `yattag <https://www.yattag.org>`_. ``E`` is two to three
times faster than dominate and on par with yattag -- a touch behind it on the larger sweeps -- and unlike either it
returns a real, queryable turbohtml tree rather than a string. That tree costs a little over twice the raw
:class:`~turbohtml.Element` constructor above -- the price of the leading-mapping and per-child dispatch the sugar runs
in Python.

.. bench-table::
    :file: bench/building-4.json

*********
 Editing
*********

Editing a parsed tree: tag every ``<a>`` with ``rel="nofollow"``, a link-rewriting pass. Because the pass mutates the
tree, each library rebuilds a fresh parse before every iteration outside the timed region, then the timed call walks its
links and sets the attribute (turbohtml through the live :attr:`~turbohtml.Element.attrs` mapping, lxml through
``Element.set``, BeautifulSoup through item assignment). selectolax mutation is limited, so it has no entry.

.. bench-table::
    :file: bench/editing.json

A second pass churns the class list: add then drop a token on every link (turbohtml's
:meth:`~turbohtml.Element.add_class`/:meth:`~turbohtml.Element.remove_class` against lxml's ``classes`` set). The
add-then-remove is a net no-op, so each repeat does equal work. BeautifulSoup has no class-token mutator, so it has no
entry.

.. bench-table::
    :file: bench/editing-2.json

Two content setters replace the body's children on a freshly parsed tree. :meth:`~turbohtml.Element.set_inner_html`
reparses a fixed fragment in the ``<body>``'s context and splices it in one C call, against lxml clearing the body and
appending ``fragments_fromstring``, BeautifulSoup clearing it and appending a reparsed soup, and pyquery's ``.html()``.
:meth:`~turbohtml.Element.set_text` replaces the children with one verbatim text node, against pyquery's ``.text()``.

.. bench-table::
    :file: bench/editing-3.json

.. bench-table::
    :file: bench/editing-4.json

A bulk tag edit over each page's ``<code>``/``<a>``/``<q>`` elements: :meth:`~turbohtml.Node.remove` drops each match
with its subtree, and :meth:`~turbohtml.Node.strip_tags` unwraps each match but keeps its content. Both rewrites are
destructive, so the timed call parses the page afresh -- the string-to-result transform these helpers perform -- and
races each library's own bulk tag helper. ``remove`` pairs with selectolax's ``strip_tags`` (which drops matches with
their content) and pyquery's ``.remove()``; ``strip_tags`` pairs with w3lib's regex ``remove_tags`` (which keeps the
content) and pyquery's unwrap. turbohtml's single C pass leads by roughly two to six times, the margin widest on the
smaller pages where the competitors' per-match Python work has the least bulk text to hide behind.

.. bench-table::
    :file: bench/editing-5.json

.. bench-table::
    :file: bench/editing-6.json

*******
 Links
*******

The link surface: extract every in-document link, resolve them against a base URL, and rewrite them through a callback.
turbohtml's :meth:`~turbohtml.Node.links`, :meth:`~turbohtml.Node.resolve_links`, and
:meth:`~turbohtml.Node.rewrite_links` against lxml.html's ``iterlinks()``, ``make_links_absolute()``, and
``rewrite_links()`` -- the only other library that walks the link-bearing attributes (``href``, ``src``, ``srcset``,
...) as a set. Each operation runs over the three real saved pages and the 235 kB WHATWG spec; extraction is read-only
and rewrite applies an identity callback, so both reuse one cached parse, while absolutize rebuilds a fresh tree before
each iteration since ``make_links_absolute`` rewrites the hrefs in place. turbohtml walks the attribute set in C where
lxml re-resolves each URL in Python, so it leads from low single digits up to over a hundred times, the gap widening
with the link count on the page.

.. bench-table::
    :file: bench/links.json

.. bench-table::
    :file: bench/links-2.json

.. bench-table::
    :file: bench/links-3.json

************
 Extraction
************

Pulling values out of a document, the idioms the parsel, pyquery, and w3lib migrations center on. First, reading every
matched node's ``@href`` and visible text off a pre-parsed page: parsel's ``::attr``/``::text`` ``getall`` and a pyquery
``.items()`` read against turbohtml selecting once and reading :meth:`~turbohtml.Element.attr` and
:attr:`~turbohtml.Node.text` off each node. turbohtml compiles the selector once and reads interned atoms, where parsel
re-translates the CSS to XPath on libxml2 per call and pyquery boxes every match in a wrapper object, so it leads by
twenty-five to nearly a hundred times.

.. bench-table::
    :file: bench/extraction.json

.. bench-table::
    :file: bench/extraction-2.json

Second, reading a document's own URL hints: w3lib's ``get_base_url`` and ``get_meta_refresh`` against turbohtml's
:meth:`~turbohtml.Document.base_url` and :meth:`~turbohtml.Document.meta_refresh`. Both parse the string each call;
w3lib runs a regular-expression pass while turbohtml runs the WHATWG tree builder and reads the hint off the parsed
``<head>``, so the tree builder still comes out ahead of the regex on this small document.

.. bench-table::
    :file: bench/extraction-3.json

*****************
 Fluent chaining
*****************

A pyquery-style fluent chain over a pre-parsed tree: select every ``<a>``, keep the linked ones, take the first, tag it,
and read its ``href`` (turbohtml's :class:`turbohtml.query.Query` against `pyquery <https://github.com/gawel/pyquery>`_,
whose wrapper delegates to lxml). Both wrappers are thin Python over the underlying engine, so the gap is the engine's:
turbohtml's selector and attribute primitives run in C, and the wrapper avoids a redundant de-duplication when the chain
starts from one node, so it runs several to twenty times faster, depending on how much the page exercises the selector.

.. bench-table::
    :file: bench/fluent-chaining.json

*********************
 html.parser adapter
*********************

:class:`turbohtml.migration.stdlib.HTMLParser` against the standard library's :class:`python:html.parser.HTMLParser`,
both subclassed with the same minimal handler so the comparison is the parser and dispatch cost for the identical
callback-driven programming model. The per-tag Python handler call is a floor both pay, so the margin is narrower than
raw tokenization, but turbohtml's C tokenizer feeding the dispatch still runs it three to four times faster.

.. bench-table::
    :file: bench/html-parser-adapter.json

******************
 CSS minification
******************

:func:`turbohtml.clean.minify_css` against the CSS minifiers on PyPI, over the unminified source CSS these frameworks
publish. Each minifier column pairs its output size with the time to produce it; the ratio in each cell is against
turbohtml. Sizes are deterministic byte counts; times are the minimum of repeated runs.

.. bench-table::
    :file: bench/css-minification.json

``csscompressor`` (the YUI port) and ``cssmin`` (its BSD descendant) rewrite values to their shortest form the way
turbohtml does, but as pure-Python regex passes they turn quadratic on a large stylesheet and trail the C engine by 40x
to 800x. ``rcssmin`` is a C extension and faster than turbohtml, though it only strips comments and whitespace, so it
leaves a larger result everywhere except the custom-property-heavy ``bulma.css``. ``css-html-js-minify`` is the slowest
of the set. The three pure-Python tools and rcssmin also break value safety: each rewrites the internal whitespace of a
custom-property value, which `CSS Variables 1 §2 <https://www.w3.org/TR/css-variables-1/#defining-variables>`_ keeps as
the literal token stream that ``var()`` splices verbatim and ``getPropertyValue()`` reads back byte-exact, and
``cssmin`` and ``css-html-js-minify`` collapse whitespace inside strings, so their output can change the cascade where
turbohtml's round-trips. That rewrite is also the only reason ``rcssmin`` and ``cssmin`` end 0.1% ahead on
``bulma.css``, whose declarations are almost entirely custom properties.

`lightningcss <https://pypi.org/project/lightningcss/>`_, the Rust binding, is a cascade-aware optimizer: it drops
declarations overridden elsewhere in the sheet and rewrites syntax for a browser-target set, so it reaches a smaller
size than turbohtml on most of the corpus (turbohtml comes out ahead on ``normalize.css``). That target-dependent
optimization is the same idea as turbohtml's ``baseline`` option carried further, and it is in scope. The cost shows in
the time half of each cell: it runs about 3x slower than turbohtml, and it rejects ``foundation.css`` with a parse error
on a media query the WHATWG recovery rules accept, where turbohtml minifies all six. turbohtml gives the smallest output
that stays value-safe at the most compatible baseline and recovers from malformed input.

*************************
 JavaScript minification
*************************

:func:`turbohtml.minify_js` against the PyPI JavaScript minifiers it replaces -- `rjsmin
<https://opensource.perlig.de/rjsmin/>`_ (a regex substitution), `jsmin <https://github.com/tikitu/jsmin>`_ (Crockford's
character state machine), and `calmjs.parse <https://github.com/calmjs/calmjs.parse>`_ (a full ES5 parser with an
obfuscating printer) -- plus `terser <https://terser.org/>`_, the JavaScript ecosystem's reference minifier, run
in-process under Node as the size bar. The inputs are real un-minified libraries, a size ladder every tool parses.
turbohtml renames every local binding (function and class declarations included) and runs the structural folds, so it
beats calmjs.parse's heavier global obfuscation on size everywhere while running forty to eighty times faster, and it
lands within one percent of terser's size at thirteen to twenty-five times less time; it trails only rjsmin on time,
which buys its speed by doing far less and leaving output roughly twice the size. Each cell pairs a minifier's output
size with the time to produce it; both ratios are against turbohtml.

.. bench-table::
    :file: bench/js-minification.json

********************
 Encoding detection
********************

:func:`turbohtml.detect.detect` against the encoding detectors it replaces: `chardet <https://chardet.readthedocs.io/>`_
(the pure-Python prober ensemble), `charset-normalizer <https://charset-normalizer.readthedocs.io/>`_ (decode-and-score,
what ``requests`` uses), and `faust-cchardet <https://github.com/faust-streaming/cChardet>`_ (the maintained C binding
of uchardet; the original cchardet stops compiling at Python 3.11). turbohtml resolves certain input -- a byte-order
mark, a ``<meta>`` declaration, valid UTF-8, pure ASCII -- structurally before any scoring, which is where the 90x-2000x
rows come from, and its chardetng frequency scoring keeps declaration-less single-byte text about 3x ahead of chardet.
The one workload it loses is CJK-heavy bytes, where each CJK candidate drives a CPython incremental codec and uchardet's
native tables stay ahead.

.. bench-table::
    :file: bench/encoding-detection.json

********************************
 URL cleaning & link extraction
********************************

:func:`turbohtml.extract.clean_url`, :func:`~turbohtml.extract.normalize_url`, and
:func:`~turbohtml.extract.extract_links` against `courlan <https://github.com/adbar/courlan>`_, trafilatura's URL
cleaner, and `w3lib <https://w3lib.readthedocs.io/>`_'s ``safe_url_string``/``canonicalize_url``, Scrapy's URL
utilities. The per-URL pass wins 2x-6x by scanning each component once in C-backed regexes and percent-encoding only
when a scan finds something to encode, where both competitors re-encode unconditionally through urllib's per-character
quoters. Page-level extraction parses the real WHATWG DOM yet still finishes 1.4x-2.4x ahead of courlan's regex scan,
because each distinct href is cleaned once and absolute links skip resolution.

.. bench-table::
    :file: bench/url-cleaning.json

.. bench-table::
    :file: bench/link-filtering.json
