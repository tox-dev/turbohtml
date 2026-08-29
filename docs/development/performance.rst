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
The numbers in these tables come from the ``--pgo`` baseline: turbohtml built with the shipped profile-guided,
link-time-optimized release recipe (``tox -e bench -- --pgo all``), so each figure reads as what a release ships rather
than a plain build. The default baseline is a plain wheel, which builds quickly for iterating; the ``--pgo`` build costs
much more time. Most operations are a single call; a few aggregate workloads (``build``, ``build-e``) sweep a size, and
the ``construct`` and ``emit`` breakdowns decompose that write path into the constructor and the serializer in
isolation. Numbers vary with input and hardware.

To refresh these tables, run the sweep into a scratch directory and let the generators rewrite the committed feeds; the
harness names its output for the operation, which is not what this guide calls its tables, so never copy the files
across by hand:

::

    tox -e bench -- --pgo --table-json /tmp/feeds all
    python -m bench.docs_feeds /tmp/feeds docs/development/bench
    python -m bench.migration /tmp/feeds docs/migration/bench docs tools/bench/competitors

**********
 Escaping
**********

:func:`turbohtml.escape` against the standard library's :func:`python:html.escape` (the ``stdlib`` column), dominate's
text escape, and the nh3 ammonia binding. It gains the most on text that needs little escaping, where the SIMD scan
classifies sixteen bytes at a time and copies clean stretches in bulk: on 4 MiB of no-op prose it runs 22 times faster
than html.escape and 65 times faster than nh3. The gap narrows to two to four times on tiny strings and escape-dense
markup, where call overhead and the escaping itself dominate.

.. bench-table::
    :file: bench/escaping.json

*******************
 Markup (escaping)
*******************

:func:`turbohtml.migration.markupsafe.escape` against `markupsafe <https://markupsafe.palletsprojects.com>`_'s own C
escape, both returning a ``Markup``. The inputs are the small, mostly-clean strings a template engine interpolates under
autoescape, markupsafe's hottest path. turbohtml builds the safe string in C in a single call, where markupsafe pays a
Python ``escape`` frame and ``Markup`` construction per call, so it runs two and a half to nearly four times faster
across the clean and escape-heavy inputs.

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
linkifier it succeeds, and `lxml-html-clean <https://github.com/fedora-python/lxml_html_clean>`_'s ``autolink``. All
three parse the HTML and rewrite it. turbohtml's C candidate scan and its own tree carry it past bleach's html5lib pass
by six to twenty times. It leads lxml's autolink on the comment and 4 KiB markup inputs and trails it on the plain 1 KiB
prose row (0.6x), though that row is not a like-for-like comparison: ``autolink`` only rewrites URLs already inside
markup and never linkifies an email address, so on plain prose it produces no links at all where turbohtml produces
thirty. Its figure there is the cost of finding nothing.

.. bench-table::
    :file: bench/linkify.json

A single C walk creates wrappers only for eligible text and anchor targets; Python no longer visits every node. CodSpeed
tracks a text-heavy tree, 2,000 small text nodes, and 2,000 empty elements. Separate cases cover skip-tag pruning and
callback-heavy HTML. Callbacks run in document order after target collection releases the tree lock.

The detection primitive on its own, :meth:`turbohtml.clean.LinkDetector.find` against ``LinkifyIt().match`` and
:meth:`~turbohtml.clean.LinkDetector.has_link` against ``LinkifyIt().test``, scans a run of plain text without rewriting
HTML. Both libraries stop on the first valid match; turbohtml allocates no span list. The large-tail case starts with a
link followed by 220 KiB of prose to catch a return to full-input scanning.

.. bench-table::
    :file: bench/linkify-2.json

Phone-number detection, :class:`~turbohtml.clean.LinkDetector` with a :class:`~turbohtml.clean.PhoneNumbers` setting
against `phonenumbers <https://github.com/daviddrysdale/python-phonenumbers>`_'s ``PhoneNumberMatcher``, both with the
United States as the default region. The build compiles the numbering plans to automata, so a candidate is a table walk
per digit where the port runs the library's regular expressions over each one: 36x faster on the mixed corpus, 22x to
33x on prose with a few numbers, and 54x to 107x on the digit-heavy inputs where those expressions retry the most. The
eight-region rows give turbohtml eight fallback regions and the matcher its first; the adversarial rows are 21-group
digit runs that form no number, the shape that costs the most splits; the prose rows with no digits measure the trigger
scan alone (8x to 9x, the narrowest rows).

.. bench-table::
    :file: bench/linkify-3.json

:meth:`PhoneNumber.parse <turbohtml.clean.PhoneNumber.parse>` against ``phonenumbers.parse`` followed by
``is_valid_number`` (``is_possible_number`` on the possible row), over twenty held numbers from twenty regions, each in
a written form of its own: national with the prefix, international, with an extension, bracketed. The port normalizes
the string, strips prefixes and matches the plan's regular expressions one type at a time; turbohtml runs the same
recognizer the scanner uses over the one string, 7x faster (5x on the possible row).

.. bench-table::
    :file: bench/linkify-4.json

:meth:`PhoneNumber.format <turbohtml.clean.PhoneNumber.format>` against ``format_number`` over the same twenty numbers,
parsed once outside the timed loop, one layout per row. The port picks the number format by running its leading-digits
and pattern expressions and substitutes the groups with a regular-expression replacement; turbohtml walks one
leading-digits automaton per candidate format and splits the digits by the group bounds the generator recorded, 14x to
18x faster; E.164 joins a few strings on both sides and is the narrowest row at 2x.

.. bench-table::
    :file: bench/linkify-5.json

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

Template-safe sanitizing (``Policy.strip_template_markers``, collapsing ``{{ }}``/``${ }``/``<% %>`` so the output
cannot re-inject through a template engine) has no allowlist-sanitizer analog in Python; the reference is DOMPurify's
``SAFE_FOR_TEMPLATES``, which runs in JavaScript. Reaching it from Python means shelling out to Node, where each call
spins up a DOM before it sanitizes, so the figure below is that end-to-end per-document cost, not a pure-algorithm
comparison. turbohtml folds the same transform into its C walk and pays neither the process nor the DOM.

.. bench-table::
    :file: bench/sanitize-templates.json

**********
 Markdown
**********

:meth:`turbohtml.Node.to_markdown` against `markdownify <https://github.com/matthewwithanm/python-markdownify>`_ (on
BeautifulSoup) and `html2text <https://github.com/Alir3z4/html2text>`_ (a streaming ``HTMLParser`` subclass). All three
take an HTML string and return Markdown, so each parses first; turbohtml parses to the WHATWG tree and walks it in C,
where the others build and convert in Python. The single C pass converts a page in a few microseconds, two orders of
magnitude ahead of both. The ``configured`` row turns the option surface on in all three (underscore emphasis, reference
links, padded tables, full escaping), where turbohtml stays 48 times ahead of html2text and 125 times ahead of
markdownify.

.. bench-table::
    :file: bench/markdown.json

The ``google_doc`` row reads the inline-CSS styling a Google Docs export carries (html2text's google_doc mode) and runs
32 times faster; markdownify has no equivalent.

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
table of 10, 100, and 1,000 rows. The single C pass leads from roughly thirty times on the thousand-row table to over a
hundred and sixty on the ten-row table, where pandas pays its fixed per-frame construction cost.

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

*****************
 Date extraction
*****************

:func:`turbohtml.extract.dates` against `htmldate <https://htmldate.readthedocs.io>`_, the standalone publication-date
finder, and the article extractors trafilatura, newspaper3k, goose3, and news-please that surface a date beside the body
text. All read the same signals -- publication/modification ``<meta>`` tags, JSON-LD, ``<time>`` elements, and a date in
the URL -- and are parse-bound; htmldate builds an lxml tree, turbohtml the WHATWG tree. turbohtml's early-exit over the
structured signals runs 2.9 to 3.3 times faster than htmldate on the real pages and 20 to 24 times faster than
trafilatura. The synthetic ``100 meta candidates`` row -- a page stacked with a hundred date-like ``<meta>`` tags -- was
the one case turbohtml lost, since it weighed every candidate where htmldate and trafilatura stopped early; moving that
weighing into C turns it around, and turbohtml now leads the row too, 3.0 times over htmldate and 4.1 times over
trafilatura.

.. bench-table::
    :file: bench/date-extraction.json

************
 Unescaping
************

:func:`turbohtml.unescape` against :func:`python:html.unescape` (the ``stdlib`` column), `w3lib
<https://github.com/scrapy/w3lib>`_'s ``replace_entities``, the Scrapy helper that resolves the same references, and
dominate's ``util.unescape``. It gains the most on entity-heavy input, where the standard library pays a Python call per
match and w3lib runs a regular-expression substitution with a Python callback per match; turbohtml hops between ``&``
occurrences in C and bulk-copies the clean spans between references, so it leads html.unescape by up to 16 times and
w3lib by up to 22 times on the reference-dense inputs. dominate sits in that same range on the small strings, but its
scan turns multi-megabyte input into whole seconds, trailing by 525 times on the 4 MiB book and past 2,600 on the
escaped copy.

.. bench-table::
    :file: bench/unescaping.json

************
 Tokenizing
************

:func:`turbohtml.tokenize` against :class:`python:html.parser.HTMLParser` (the ``stdlib`` column, driven with no-op
handlers) and `html5lib <https://html5lib.readthedocs.io>`_'s pure-Python tokenizer. The closest case is a document
dominated by a single text node (the ``text-heavy prose`` row, 4.8x), where the standard library's regex performs one C
scan; wherever markup appears, the state machine runs roughly eight to sixteen times faster than html.parser and 22 to
240 times faster than html5lib.

.. bench-table::
    :file: bench/tokenizing.json

*********
 Parsing
*********

:func:`turbohtml.parse` builds a full WHATWG document tree, against the other Python tree builders: `lxml
<https://lxml.de>`_ and `parsel <https://parsel.readthedocs.io>`_ and `pyquery <https://github.com/gawel/pyquery>`_ (all
over libxml2), `selectolax <https://github.com/rushter/selectolax>`_ and `resiliparse
<https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ (both wrapping `lexbor <https://lexbor.com>`_), `html5-parser
<https://html5-parser.readthedocs.io>`_ (the C gumbo binding), `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/bs4/doc/>`_ over each of its tree builders, and html5lib. turbohtml leads
resiliparse by 1.2 to 3.4 times, runs 2.7 to 6.2 times faster than lxml, parsel, pyquery, and selectolax, 4.9 to 12.6
times faster than html5-parser, and 31 to 99 times faster than html5lib and BeautifulSoup, while building the WHATWG
tree that lxml's libxml2 does not.

resiliparse stays closest because its ``HTMLTree.parse`` is a thin call straight into lexbor's native tree, while
selectolax wraps that same engine behind a heavier object layer; the comparison here is parsing only. resiliparse's
wider toolkit, boilerplate and main-content extraction, language detection, and the encoding and archive utilities it
ships for large-scale web-crawl processing, sits outside turbohtml's scope. html5-parser wraps `gumbo
<https://github.com/google/gumbo-parser>`_, the C WHATWG parser Google released; it is read-oriented and archived
upstream, and it trails by five to thirteen times above. turbohtml is the maintained, mutable, typed alternative to that
lineage.

.. bench-table::
    :file: bench/parsing.json

******************
 Fragment parsing
******************

:func:`turbohtml.parse_fragment` parses an ``innerHTML``-style snippet in a container's context rather than a whole
document, against lxml's ``lxml.html.fromstring`` and html5lib's ``parseFragment``. The input is a table-row fragment
parsed in its ``<tbody>`` context, where the WHATWG algorithm's table rules apply. turbohtml runs the same C engine it
uses for whole documents, so it parses the fragment nearly four times faster than lxml and roughly eighty-eight times
faster than the pure-Python html5lib.

.. bench-table::
    :file: bench/fragment-parsing.json

**********
 Querying
**********

Each library parses the document once, then the timed call runs one query. ``find`` collects every ``<a>`` element the
way each library reaches for it (turbohtml's :meth:`~turbohtml.Node.find_all`, resiliparse's and selectolax's lexbor
selectors, lxml's XPath ``findall``, parsel's and pyquery's and BeautifulSoup's selectors, and soupsieve directly). A
tag-only query resolves the name to an interned atom and walks the subtree comparing integers, with no per-element
string built and no matcher dispatch. It stays ahead of resiliparse's lexbor pass by 1.3 times on the small blog
widening to 22 times on the spec, leads lxml's C XPath engine by 14 to 24 times, and runs 12 to over 1,400 times ahead
of pyquery, selectolax, parsel, BeautifulSoup, and soupsieve.

A first-result query walks until its match when the document has no tag index. An uncapped ``find_all`` builds the
whole-document index for later queries; ``find`` and ``find_all(..., limit=1)`` reuse it after that point. The cold-tree
benchmark records hit positions and misses. It measures uncapped and limited collection, plus peak resident memory.

.. bench-table::
    :file: bench/querying.json

``select`` runs the CSS selector ``div a[href]`` (turbohtml's :meth:`~turbohtml.Node.select`, resiliparse's and
selectolax's ``css``, lxml's `cssselect <https://github.com/scrapy/cssselect>`_, parsel's ``css``, pyquery, and
BeautifulSoup's `soupsieve <https://github.com/facelessuser/soupsieve>`_). Because turbohtml compiles the selector
against the tree once and then matches by comparing interned integer atoms, it stays in the low microseconds across
these pages. resiliparse's lexbor engine stays closest at 3.4 to 19 times, selectolax next at 13 to 45 times. lxml and
parsel re-translate the selector to XPath through cssselect on every call, which scales with the document and trails by
roughly fifty times on the small blog up to nearly eight hundred times on the spec, with pyquery tracking them;
soupsieve and BeautifulSoup are hundreds to more than fifteen hundred times behind.

.. bench-table::
    :file: bench/querying-2.json

The relational ``:has()`` pseudo-class is the costliest selector to evaluate, since a naive matcher rescans each
candidate's subtree. turbohtml runs ``div:has(a)`` against the same pages and leads every alternative: resiliparse and
selectolax by five to twenty times, lxml and parsel by tens of times on the smaller pages, narrowing to single digits on
the link-dense mozilla blog where the relational match itself does real work, while soupsieve and BeautifulSoup trail by
hundreds of times throughout. The matcher walks each anchor's descendants once and skips the sibling scan for descendant
and child relationships, so the relational lookup keeps the same interned-atom comparison the flat selectors use.

.. bench-table::
    :file: bench/querying-3.json

Per-element matching runs each anchor on the page through a compiled ``div a[href]`` matcher -- the shape a soupsieve
port hits through :mod:`turbohtml.query` and its :meth:`Matcher.match <turbohtml.query.Matcher.match>` -- raced against
selectolax's node match, soupsieve, BeautifulSoup, and pyquery. turbohtml answers each test with the same interned-atom
comparison its ``select`` uses, walking the ancestor chain once per candidate, where the others re-interpret the parsed
selector per element, so the sweep runs 34 to 44 times faster than selectolax, 75 to 145 times faster than soupsieve,
and over a hundred times faster than BeautifulSoup and pyquery.

.. bench-table::
    :file: bench/matching.json

:func:`turbohtml.query.escape_identifier` escapes a raw string into a CSS identifier per the CSSOM
serialize-an-identifier rules -- the safe way to drop an untrusted class or id into a selector -- against soupsieve's
``escape``. The workload escapes a thousand identifiers spanning leading digits, embedded specials, astral characters,
and a lone dash. turbohtml walks the code points and emits the escape in C, where soupsieve builds the result through a
per-character Python loop, so it runs 16 times faster.

.. bench-table::
    :file: bench/querying-6.json

A text-content search runs through :meth:`~turbohtml.Node.find_all` with ``text=`` (a regex matched against each
element's collected subtree text), raced against ``BeautifulSoup.find_all(string=...)`` and the equivalent text filters
on lxml, parsel, and pyquery. When the ``text=`` filter is a plain string or a literal (no regex metacharacters,
case-sensitive) compiled pattern, turbohtml gathers each candidate's collected text and matches it in C -- no Python
``str`` built, no per-element ``re.search`` call -- where the others walk the tree in Python, so it leads every
competitor by 1.2 to 3.0 times across these pages. A case-insensitive or otherwise non-literal pattern keeps the
per-element Python path.

.. bench-table::
    :file: bench/querying-4.json

:func:`turbohtml.convert.css_specificity` weighs a selector list's ``(a, b, c)`` specificity, raced against `cssselect
<https://github.com/scrapy/cssselect>`_'s ``Selector.specificity()``, the computation lxml, parsel, and pyquery inherit.
turbohtml parses the selector and sums the weights in one C pass, so it leads across the type, compound, structural,
complex, and grouped selectors below; cssselect parses in Python and builds a tree of selector objects first.

.. bench-table::
    :file: bench/css-specificity.json

XPath 1.0 evaluation runs through :meth:`~turbohtml.Node.xpath`, raced against lxml's libxml2 engine and parsel's
wrapper of it (selectolax and BeautifulSoup have no XPath). One expression per feature class (name tests, the ``//``
abbreviation, attribute, positional, and arithmetic predicates, string and aggregate functions, a reverse axis, a union,
and a computed name test) runs over the 9.6 kB wpt page below; ``tox -e bench xpath`` repeats the sweep across every
page size. turbohtml compiles each expression against the tree once, resolves name tests to interned atoms, and folds
``//`` to a single ``descendant`` walk, so it leads across the surface. The exception is a predicate that references
``position()`` (``[1]`` or ``position() <= 3``): it pins the result to proximity order and disables the ``//`` collapse,
so on the largest pages lxml's streaming evaluation closes the gap. Five rows exercise XPath 2.0 functions --
``ends-with``, ``matches``, ``replace``, ``lower-case``, and ``string-join`` -- that turbohtml answers but libxml2 does
not implement, so lxml and parsel show a gap there. Further rows are the lxml/parsel options the parity work added: a
``$variable`` binding, an EXSLT ``re:test`` predicate (turbohtml's Python :mod:`re` against lxml's C libexslt), an EXSLT
``set:distinct`` node-set reduction (built-in C dispatch on both sides, so it races C against C), a ``smart_strings``
attribute read, a custom ``extensions=`` function, an ``extensions=`` function whose return becomes a node-set feeding a
later ``/@href`` step, a ``namespaces=`` prefix binding that resolves ``//svg:rect`` against ``{"svg": ".../2000/svg"}``
over a page carrying an SVG block, and a node-set ``$variable`` bound from a prior result (``$rows/div``, with ``rows``
reused from an earlier ``//div`` query) fed into a later path step. turbohtml still leads, since lxml resolves the
namespace map and option set on every call. The last row precompiles the expression once with :class:`~turbohtml.XPath`
and re-evaluates it, lxml's ``etree.XPath`` doing the same: both skip the per-call parse :meth:`~turbohtml.Node.xpath`
pays, and turbohtml's compiled program stays ahead per evaluation.

.. bench-table::
    :file: bench/querying-5.json

******
 XSLT
******

:class:`turbohtml.transform.Transform` compiles one stylesheet into a native model with reusable XPath programs. Each
application allocates source-specific indexes and output state. Callers can use one ``Transform`` instance with
different documents and parameters across threads.

The first table measures construction. The other two measure a 120-row catalog and ten calls to a 300-template
stylesheet. That stylesheet has 299 unused templates and 24 static ``xsl:number`` patterns in its used template; the
repeated result includes any stylesheet analysis or XPath compilation left in the application path.

.. bench-table::
    :file: bench/xslt-compile.json

.. bench-table::
    :file: bench/xslt.json

.. bench-table::
    :file: bench/xslt-reuse.json

************
 Node paths
************

:meth:`turbohtml.Element.css_path` and :meth:`~turbohtml.Element.xpath_path` return the unique locator that re-finds an
element from the document root -- a CSS selector and a positional XPath -- against lxml's ``getroottree().getpath()``,
the libxml2 path builder devtools' "copy selector" mirrors, and (for the positional path) parsel's wrapper of it. Each
timed call walks every element in a pre-parsed page and serializes its path. Both methods lead ``getpath`` by roughly
six times across these pages, narrowing to under threefold on the spec. :meth:`~turbohtml.Element.css_path` previously
rescanned the whole document to test each element's id uniqueness, an O(N\ :sup:`2`) cost over a page that made it
slower than ``getpath`` on id-heavy pages; a cached per-tree id-occurrence map (dropped with the element index on any
mutation) now answers that test in O(1), so ``css_path`` keeps pace with the positional ``xpath_path``.

.. bench-table::
    :file: bench/node-paths.json

**************
 Text content
**************

The ``text`` suite collects the visible text two ways. First, the raw text join off a pre-parsed tree, the ``get_text``
pass: turbohtml's :attr:`~turbohtml.Node.text` property concatenates every descendant text run, against lxml's
``text_content()``, resiliparse's node text, selectolax's ``text()``, BeautifulSoup's ``get_text()``, and parsel's and
pyquery's text extraction. turbohtml gathers the runs in one C walk into a buffer reserved up front, so it stays level
with lxml and resiliparse, leads selectolax by 6.6 to 9.3 times and BeautifulSoup by 5.5 to 7.5, and runs 99 to 145
times ahead of parsel, which boxes each match in a wrapper first. pyquery trails by 33 to 57 times.

.. bench-table::
    :file: bench/text-content.json

Second, the layout-aware string-to-text extraction: :meth:`turbohtml.Node.to_text` against `inscriptis
<https://github.com/weblyzard/inscriptis>`_, the layout-aware HTML-to-text renderer it succeeds, `html-text
<https://github.com/zytedata/html-text>`_, Zyte's plainer visible-text extractor, and `resiliparse
<https://github.com/chatnoir-eu/chatnoir-resiliparse>`_'s ``extract_plain_text``. inscriptis and html-text both build an
lxml tree in Python and resiliparse renders text off the lexbor tree it parses to, where turbohtml does the whole layout
in one C walk; inscriptis additionally lays tables out as aligned columns, which html-text and resiliparse skip.
turbohtml leads resiliparse by 2.4 to 4.1 times, html-text by 12 to 18 times, and inscriptis by 34 to 47 times.

.. bench-table::
    :file: bench/text-content-2.json

The ``collapsed`` row turns layout guessing off: turbohtml joins the :attr:`~turbohtml.Node.stripped_strings` word
stream against html-text's ``extract_text(guess_layout=False)``, 17 times faster; inscriptis and resiliparse have no
comparable collapsed mode. The ``main`` row strips page boilerplate first, :meth:`~turbohtml.Node.main_text` against
resiliparse's ``extract_plain_text(main_content=True)``, four times faster. The ``annotated`` row labels matching
elements with spans through :meth:`~turbohtml.Node.to_annotated_text` against inscriptis's ``get_annotated_text``, 75
times faster; html-text and resiliparse have no annotation surface, so they sit out that row.

*****************
 Tree navigation
*****************

Walking every descendant of a parsed tree: turbohtml's :attr:`~turbohtml.Node.descendants` iterator against
resiliparse's node walk, BeautifulSoup's ``descendants``, lxml's ``iterdescendants()``, selectolax's node iteration,
pyquery, and html5lib. The ``list(el)``, ``iterdescendants()``, and ``iterancestors()`` family ports to
:attr:`~turbohtml.Node.children`, :attr:`~turbohtml.Node.descendants`, and :attr:`~turbohtml.Node.ancestors`; the
descendant walk is the dominant case. Each timed call consumes the whole iterator, where turbohtml yields interned nodes
straight from the arena faster than lxml's libxml2 proxy objects and BeautifulSoup's Python ``NavigableString`` chain.
resiliparse's lexbor walk stays closest at 1.4 to 2.0 times and BeautifulSoup's ``descendants`` -- one of its leaner
paths -- at about twice, while lxml and selectolax trail by five times, pyquery by twenty, and html5lib by ninety.

.. bench-table::
    :file: bench/tree-navigation.json

*************
 Serializing
*************

Serializing a parsed document back to HTML: turbohtml's :attr:`~turbohtml.Node.html` against resiliparse's, pyquery's,
selectolax's, parsel's, and lxml's serializers, BeautifulSoup's ``decode``, and html5lib. turbohtml scans each text run
for the next character that needs escaping (two code points at a time with the same SWAR lane probes
:func:`~turbohtml.escape` uses) and bulk-copies the clean spans, recovering each special's position from the lane mask,
and reserves the whole-document buffer up front so the output grows in one allocation. It serializes about twice as fast
as resiliparse and pyquery, four to six times faster than selectolax, parsel, and lxml, and 52 to 77 times faster than
BeautifulSoup and html5lib.

.. bench-table::
    :file: bench/serializing.json

***********
 Minifying
***********

Minifying a document with :func:`turbohtml.clean.minify`: parse, then serialize once with every fold engaged (collapsing
insignificant whitespace, omitting the WHATWG-optional tags, unquoting attributes, and stripping comments), against
`minify-html <https://github.com/wilsonzlin/minify-html>`_'s Rust minifier on the same folds (its CSS and JS
minification left off for a like-for-like comparison), the pure-Python `htmlmin <https://github.com/mankyd/htmlmin>`_
and ``css-html-js-minify``, and the native CLI minifiers `html-minifier-terser
<https://github.com/terser/html-minifier-terser>`_ and `tdewolff/minify <https://github.com/tdewolff/minify>`_.
turbohtml parses and emits in C through one preallocated buffer, so with the parse included it runs roughly two to three
times faster than minify-html and sixteen to seventy times faster than the pure-Python pair. html-minifier-terser and
tdewolff are invoked through their command line, so their millisecond timings are dominated by process startup; the
clean comparison against them is output size. turbohtml lands within about two percent of html-minifier-terser on the
structural folds both apply; minify-html folds more aggressively for roughly three to ten percent smaller, and tdewolff
goes further still by also minifying the inline CSS and JavaScript turbohtml leaves untouched here.

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

The ``construct`` and ``emit`` commands split that aggregate over the same builders: ``construct`` builds the rows and
stops before serialization, and ``emit`` serializes a tree built once outside the timed region. On construct turbohtml's
arena keeps it roughly twice as fast as lxml and ahead of most Python builders, though the leanest string builders
markyp and simple-html edge it out; on emit its SWAR serializer pulls ahead of every alternative, from 1.6 times over
simple-html to eight times over lxml and ninety times over BeautifulSoup.

.. bench-table::
    :file: bench/building-2.json

.. bench-table::
    :file: bench/building-3.json

The terse :data:`turbohtml.build.E` builder spells the same ``<ul>`` declaratively, raced against ten dedicated HTML
generators. The leanest string builders `simple-html <https://github.com/keithasaurus/simple_html>`_ and `markyp
<https://github.com/volfpeter/markyp-html>`_ build it faster (0.4x and roughly 0.9x), `yattag <https://www.yattag.org>`_
runs on par, and ``E`` leads the rest -- lxml.builder, htbuilder, fast-html, hyperpython, htpy, `dominate
<https://github.com/Knio/dominate>`_, and airium -- by 1.5 to 7 times, and unlike any of them it returns a real,
queryable turbohtml tree rather than a string. That tree costs a little over twice the raw :class:`~turbohtml.Element`
constructor above -- the price of the leading-mapping and per-child dispatch the sugar runs in Python.

.. bench-table::
    :file: bench/building-4.json

*********
 Editing
*********

Editing a parsed tree: tag every ``<a>`` with ``rel="nofollow"``, a link-rewriting pass. Because the pass mutates the
tree, each library rebuilds a fresh parse before every iteration outside the timed region, then the timed call walks its
links and sets the attribute (turbohtml through the live :attr:`~turbohtml.Element.attrs` mapping, resiliparse and
selectolax through their node setters, lxml through ``Element.set``, pyquery through its ``attr``, BeautifulSoup through
item assignment). turbohtml leads resiliparse by 1.4 to 2.1 times, lxml by 1.7 to 3.7, selectolax by 3.0 to 4.9, and
pyquery and BeautifulSoup by 2.8 to 12 times, the gap widening with the page as each reparse costs more.

.. bench-table::
    :file: bench/editing.json

A second pass churns the class list: add then drop a token on every link (turbohtml's
:meth:`~turbohtml.Element.add_class`/:meth:`~turbohtml.Element.remove_class` against resiliparse's, selectolax's, and
pyquery's class edits, lxml's ``classes`` set, and BeautifulSoup's attribute assignment). The add-then-remove is a net
no-op, so each repeat does equal work. turbohtml leads resiliparse by three to six times, selectolax and lxml by roughly
ten to eighteen times, and BeautifulSoup by up to thirty-seven times.

.. bench-table::
    :file: bench/editing-2.json

Two content setters replace the body's children on a freshly parsed tree. :meth:`~turbohtml.Element.set_inner_html`
reparses a fixed fragment in the ``<body>``'s context and splices it in one C call, against lxml clearing the body and
appending ``fragments_fromstring``, pyquery's ``.html()``, and BeautifulSoup clearing it and appending a reparsed soup;
it leads them by 6.6 to 9.4, 1.7 to 11, and 15 to 42 times. :meth:`~turbohtml.Element.set_text` replaces the children
with one verbatim text node, against the same three, leading by 6.8 to 9.9, 2.3 to 14, and 11 to 22 times. BeautifulSoup
trails furthest because it reparses the whole page on every iteration where the others splice into a live tree.

.. bench-table::
    :file: bench/editing-3.json

.. bench-table::
    :file: bench/editing-4.json

A bulk tag edit over each page's ``<code>``/``<a>``/``<q>`` elements: :meth:`~turbohtml.Node.remove` drops each match
with its subtree, and :meth:`~turbohtml.Node.strip_tags` unwraps each match but keeps its content. Both rewrites are
destructive, so the timed call parses the page afresh -- the string-to-result transform these helpers perform -- and
races each library's own bulk tag helper: w3lib's regex ``remove_tags``, resiliparse, lxml, pyquery, selectolax, and
BeautifulSoup. On ``strip_tags`` turbohtml's single C pass leads every alternative by roughly three to seven times, and
BeautifulSoup by nearly sixty. On ``remove`` it leads the tree libraries by the same margin but trails w3lib's
pure-regex strip on the larger pages, where deleting whole subtrees by regex skips the per-node work a real tree edit
does.

.. bench-table::
    :file: bench/editing-5.json

.. bench-table::
    :file: bench/editing-6.json

*******
 Links
*******

The link surface: extract every in-document link, resolve them against a base URL, and rewrite them through a callback.
turbohtml's :meth:`~turbohtml.Node.links`, :meth:`~turbohtml.Node.resolve_links`, and
:meth:`~turbohtml.Node.rewrite_links` walk the full set of link-bearing attributes (``href``, ``src``, ``srcset``, ...);
lxml.html's ``iterlinks()``, ``make_links_absolute()``, and ``rewrite_links()`` are the only like-for-like set. The
tables also carry the anchor collectors resiliparse, selectolax, BeautifulSoup, parsel, and pyquery, which read only
``<a href>`` and so do strictly less work. Each operation runs over the three real saved pages and the 235 kB WHATWG
spec; extraction is read-only and rewrite applies an identity callback, so both reuse one cached parse, while absolutize
rebuilds a fresh tree before each iteration since ``make_links_absolute`` rewrites the hrefs in place. turbohtml walks
the attribute set in C and leads lxml's like-for-like helpers from seven times up to over a hundred times, the gap
widening with the link count; against the anchor-only collectors it does more work per element, so resiliparse and
selectolax land near or just ahead on extraction while turbohtml pulls away on the rewrite.

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
matched node's ``@href`` and visible text off a pre-parsed page: turbohtml selects once and reads
:meth:`~turbohtml.Element.attr` and :attr:`~turbohtml.Node.text` off each node, against resiliparse, lxml, selectolax,
parsel, and pyquery selecting and reading, and BeautifulSoup. turbohtml compiles the selector once and reads interned
atoms, where the others re-translate the CSS per call or box every match in a wrapper object, so it leads resiliparse by
two to six times, lxml and selectolax by five to seventeen times, parsel and pyquery by twenty to seventy times, and
BeautifulSoup by up to 260 times.

.. bench-table::
    :file: bench/extraction.json

.. bench-table::
    :file: bench/extraction-2.json

Second, reading a document's own URL hints: turbohtml's :meth:`~turbohtml.Document.base_url` and
:meth:`~turbohtml.Document.meta_refresh` against w3lib's ``get_base_url`` and ``get_meta_refresh`` and the same read off
lxml, resiliparse, selectolax, parsel, pyquery, and BeautifulSoup trees. Every alternative parses the string each call;
turbohtml runs the WHATWG tree builder and reads the hint off the parsed ``<head>``, leading w3lib's regular-expression
pass by five to six times and the other parsers by four to forty-six times on this small document.

.. bench-table::
    :file: bench/extraction-3.json

*****************
 Fluent chaining
*****************

A pyquery-style fluent chain over a pre-parsed tree: select every ``<a>``, keep the linked ones, take the first, tag it,
and read its ``href`` (turbohtml's :class:`turbohtml.query.Query` against `pyquery <https://github.com/gawel/pyquery>`_,
whose wrapper delegates to lxml). Both wrappers are thin Python over the underlying engine, so the gap is the engine's:
turbohtml's selector and attribute primitives run in C, and the wrapper avoids a redundant de-duplication when the chain
starts from one node, so it runs four to nearly fifty times faster, depending on how much the page exercises the
selector.

.. bench-table::
    :file: bench/fluent-chaining.json

*********************
 html.parser adapter
*********************

:class:`turbohtml.migration.stdlib.HTMLParser` against the standard library's :class:`python:html.parser.HTMLParser` and
lxml's target-parser API, all driven with the same minimal handler so the comparison is the parser and dispatch cost for
the identical callback-driven programming model. The per-tag Python handler call is a floor both Python parsers pay.
Dispatch runs in C: the tokenizer calls the ``handle_*`` methods itself, binding them once per feed rather than building
a token object and walking its fields in Python for every token. Under Callgrind that removes 30.8% of the instructions
the whole workload executes, and nearly half its indirect branches, which is what the interpreter spends on dispatch.
turbohtml runs 7.5 to 11.2 times faster than html.parser and 1.7 to 2.7 times faster than lxml's fully native target
parser, which never crosses into Python per tag.

.. bench-table::
    :file: bench/html-parser-adapter.json

******************
 CSS minification
******************

:func:`turbohtml.clean.minify_css` against the CSS minifiers on PyPI, over the unminified source CSS these frameworks
publish. Each minifier column pairs its output size with the time to produce it; the ratio in each cell is against
turbohtml. Sizes are deterministic byte counts; times are the minimum of repeated runs. `esbuild
<https://esbuild.github.io/>`_ and `tdewolff/minify <https://github.com/tdewolff/minify>`_ are native minifiers invoked
through their command line, so their millisecond timings are dominated by process startup; against them the clean
comparison is output size, where turbohtml stays within a couple percent and comes out smaller on most of the corpus.

.. bench-table::
    :file: bench/css-minification.json

``csscompressor`` (the YUI port) and ``cssmin`` (its BSD descendant) rewrite values to their shortest form the way
turbohtml does, but as pure-Python regex passes they turn quadratic on a large stylesheet and trail the C engine by tens
to over four hundred times, ``cssmin`` and ``css-html-js-minify`` reaching roughly four seconds on the 745 kB
``bulma.css`` where turbohtml takes 9 ms. ``rcssmin`` is a C extension and faster than turbohtml, though it only strips
comments and whitespace, so it leaves a larger result everywhere except the custom-property-heavy ``bulma.css``.
``css-html-js-minify`` is among the slowest of the set. The three pure-Python tools and rcssmin also break value safety:
each rewrites the internal whitespace of a custom-property value, which `CSS Variables 1 §2
<https://www.w3.org/TR/css-variables-1/#defining-variables>`_ keeps as the literal token stream that ``var()`` splices
verbatim and ``getPropertyValue()`` reads back byte-exact, and ``cssmin`` and ``css-html-js-minify`` collapse whitespace
inside strings, so their output can change the cascade where turbohtml's round-trips. That rewrite is also the only
reason ``rcssmin`` and ``cssmin`` end 0.2% to 0.3% ahead on ``bulma.css``, whose declarations are almost entirely custom
properties.

`lightningcss <https://pypi.org/project/lightningcss/>`_, the Rust binding, is a cascade-aware optimizer: it drops
declarations overridden elsewhere in the sheet and rewrites syntax for a browser-target set, so it reaches a smaller
size than turbohtml on most of the corpus (turbohtml comes out ahead on ``normalize.css``). That target-dependent
optimization is the same idea as turbohtml's ``baseline`` option carried further, and it is in scope. Its Rust engine
runs 1.3 to 2.4 times slower than turbohtml across the corpus, its per-target cascade pass the added cost, and it
rejects ``foundation.css`` with a parse error on a media query the WHATWG recovery rules accept, where turbohtml
minifies all six. turbohtml gives the smallest value-safe output at the most compatible baseline and recovers from
malformed input.

*************************
 JavaScript minification
*************************

:func:`turbohtml.clean.minify_js` against the PyPI JavaScript minifiers it replaces -- `rjsmin
<https://opensource.perlig.de/rjsmin/>`_ (a regex substitution), `jsmin <https://github.com/tikitu/jsmin>`_ (Crockford's
character state machine), ``css-html-js-minify`` (another regex pass), and `calmjs.parse
<https://github.com/calmjs/calmjs.parse>`_ (a full ES5 parser with an obfuscating printer) -- and the industry's native
minifiers as the size bar: `terser <https://terser.org/>`_ (the JavaScript ecosystem's reference), `esbuild
<https://esbuild.github.io/>`_, and `tdewolff/minify <https://github.com/tdewolff/minify>`_, each invoked through its
command line. The inputs are real un-minified libraries, a size ladder every tool parses. turbohtml renames every local
binding (function and class declarations included) and runs the structural folds, so it beats calmjs.parse's heavier
global obfuscation on size everywhere while running fifty to a hundred times faster, and its output lands within one
percent of terser, esbuild, and tdewolff -- the best minifiers available. It runs in-process, where each native tool
pays its runtime's process startup per file, so it finishes ahead end to end; only rjsmin is faster, and it, jsmin, and
css-html-js-minify all do so by leaving output half again to twice the size. Each cell pairs a minifier's output size
with the time to produce it; both ratios are against turbohtml.

.. bench-table::
    :file: bench/js-minification.json

********************
 Encoding detection
********************

:func:`turbohtml.detect.detect` against the encoding detectors it replaces: `chardet <https://chardet.readthedocs.io/>`_
(the pure-Python prober ensemble), `charset-normalizer <https://charset-normalizer.readthedocs.io/>`_ (decode-and-score,
what ``requests`` uses), `faust-cchardet <https://github.com/faust-streaming/cChardet>`_ (the maintained C binding of
uchardet; the original cchardet stops compiling at Python 3.11), `resiliparse <https://resiliparse.chatnoir.eu/>`_'s
``detect_encoding``, and BeautifulSoup's ``UnicodeDammit``, benchmarked with the ``chardet`` backend it only sniffs
with. turbohtml resolves certain input -- a byte-order mark, a ``<meta>`` declaration, valid UTF-8, pure ASCII --
structurally before any scoring, which is where the tens-to-nearly-2000x rows on the ASCII and pre-declared pages come
from, and its chardetng frequency scoring keeps declaration-less single-byte text 3.9x-5.4x ahead of chardet.

resiliparse's native scan is quickest on the small and CJK inputs, ahead of turbohtml by 1.1 to 2.3 times where the
structural checks find nothing to short-circuit on; faust-cchardet (uchardet) leads only the Shift_JIS row, 1.6 times.
On everything else turbohtml's structural resolution runs away from the full-table scanners: uchardet spends 188
microseconds on the 4 kB UTF-8 stream turbohtml settles in five, and 30 milliseconds on the 95 kB pre-declared page it
settles in under one. CJK is the case both native detectors keep, since turbohtml decodes each candidate encoding to
score it and a CJK stream leaves several standing.

.. bench-table::
    :file: bench/encoding-detection.json

*****************
 Legacy decoding
*****************

The WHATWG decoders against the CPython codecs they replaced: ``cp932`` for Shift_JIS, ``cp1252``, ``gb18030`` and
``iso2022_jp``. None of those is the spec's decoder -- the tables differ, the error handling differs, and
:doc:`/how-to/encoding` explains why no rename could have reconciled them -- so the table prices the replacement rather
than claiming the codecs were a substitute. Each case wraps prose in tags, the shape of a real page, since a decoder
that walks markup one byte at a time pays for it: ASCII runs are copied out whole, and only ISO-2022-JP, whose escapes
can reinterpret an ASCII byte, has to step through them. The ``gb18030 astral`` row, dense with four-byte sequences, is
the one case where the CPython codec's table lookup edges ahead.

.. bench-table::
    :file: bench/legacy-decoding.json

********************************
 URL cleaning & link extraction
********************************

:func:`turbohtml.extract.clean_url`, :func:`~turbohtml.extract.normalize_url`, and
:func:`~turbohtml.extract.extract_links` against `courlan <https://github.com/adbar/courlan>`_, trafilatura's URL
cleaner, and `w3lib <https://w3lib.readthedocs.io/>`_'s ``safe_url_string``/``canonicalize_url``, Scrapy's URL
utilities. The per-URL pass wins 2.8x-7.5x by scanning each component once in C-backed regexes and percent-encoding only
when a scan finds something to encode, where both competitors re-encode unconditionally through urllib's per-character
quoters. Page-level filtered extraction parses the real WHATWG DOM and cleans each link, and finishes 2.2x-3.8x ahead of
courlan's regex scan, because each distinct href is cleaned once and absolute links skip resolution. Every tree-based
competitor here resolves each href against the base and deduplicates the result, the work
:func:`~turbohtml.extract.extract_links` does, so the row compares the same answer rather than a bare attribute read:
lxml trails by 1.3 to 2.1 times, selectolax by 1.6 to 3.5, parsel and pyquery by 2.2 to 3.8, and BeautifulSoup by 8.7 to
38.0 depending on its tree builder.

.. bench-table::
    :file: bench/url-cleaning.json

.. bench-table::
    :file: bench/link-filtering.json
