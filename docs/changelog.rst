###########
 Changelog
###########

.. towncrier-draft-entries:: Unreleased

.. towncrier release notes start

*********************
 v1.0.0 (2026-07-05)
*********************

The 1.0 release finishes the native-C port, settles one canonical public API, and closes the feature gap against the
libraries turbohtml replaces. The notes below fold the whole 0.4.0 to 1.0.0 span into one overview; the anchor issues
point at the epics behind each theme.

Backward incompatible changes - 1.0.0
=====================================

- Give the public surface one name per concept. CSS matching folds from ``turbohtml.match`` into :mod:`turbohtml.query`;
  the sanitizer, linkifier, and every minifier gather under :mod:`turbohtml.clean`; a malformed selector raises one
  :class:`turbohtml.SelectorSyntaxError` from every parse path; the two ``Detector`` classes split into
  :class:`turbohtml.detect.EncodingDetector` and :class:`turbohtml.clean.LinkDetector`; and each surface with more than
  six arguments takes one frozen ``options`` config. (:issue:`478`)
- Select a serialization mode with a single ``layout`` argument in place of ``indent``, so ``serialize(indent=2)``
  becomes ``serialize(layout=Indent(2))`` and :class:`~turbohtml.Minify` selects minified output. (:issue:`171`)
- Report a valueless attribute (``<x a>``) as the empty string rather than ``None`` in :attr:`turbohtml.Element.attrs`,
  matching the WHATWG tokenizer and the DOM. (:issue:`87`)

Features - 1.0.0
================

- Query a tree with the full Selectors Level 4 grammar (``:is()``, ``:where()``, ``:has()``, ``:not()``,
  ``:nth-child(An+B of S)``, the structural, input, ``:lang()``, ``:dir()``, and ``:scope`` pseudo-classes) and an XPath
  1.0 engine: :meth:`~turbohtml.Node.xpath`, a compiled reusable :class:`turbohtml.XPath`, the EXSLT function set, and
  namespace, variable, and extension binding. A pyquery-style :class:`turbohtml.query.Query`, a soupsieve-shaped
  matcher, and :func:`turbohtml.convert.css_to_xpath` cover the BeautifulSoup, cssselect, parsel, and pyquery surfaces.
  (:issue:`179`)
- Serialize back to HTML with pretty-print, whitespace minification, and lazy streaming
  (:meth:`~turbohtml.Node.serialize_iter`), and minify HTML, CSS, and JavaScript through native engines under
  :mod:`turbohtml.clean`. Every transform is round-trip safe, replacing rcssmin, csscompressor, rjsmin, jsmin,
  minify-html, and htmlmin. (:issue:`343`, :issue:`346`)
- Convert a tree to GitHub-Flavored Markdown, layout-aware text, or annotated ``(start, end, label)`` spans, and extract
  a page's main article, boilerplate paragraphs, publication date, tables, links, and structured data (JSON-LD,
  Microdata, RDFa, Dublin Core, Open Graph) through :mod:`turbohtml.extract`, replacing markdownify, html2text,
  inscriptis, trafilatura, readability, htmldate, extruct, and microdata. (:issue:`273`, :issue:`276`)
- Detect a byte stream's encoding and a text's natural language from the standalone :mod:`turbohtml.detect` module,
  which reports confidence and a BOM label, covers 69 languages across nine scripts, and replaces chardet,
  charset-normalizer, and cchardet. (:issue:`315`, :issue:`474`)
- Parse incrementally from a stream with :class:`turbohtml.IncrementalParser`, read the WHATWG parse errors recovery
  swallows through :attr:`~turbohtml.Document.errors`, and locate each element in the source through
  :attr:`~turbohtml.Node.source_line` and :attr:`~turbohtml.Node.source_col`. (:issue:`210`, :issue:`212`)
- Sanitize untrusted HTML against a frozen allowlist :class:`turbohtml.clean.Policy` that is safe with no arguments and
  scrubs inline and embedded CSS, foreign namespaces, and media hosts, and auto-link URLs and email addresses, together
  replacing bleach. (:issue:`8`, :issue:`9`)
- Build and edit the tree in place: construct nodes and a whole HTML5 page with :data:`turbohtml.build.E` and
  :func:`turbohtml.build.document`, edit the class token set and inner HTML or text, wrap and unwrap subtrees, trim a
  document to a selector with :meth:`~turbohtml.Node.prune`, read and fill form fields, and compare two subtrees with
  :meth:`~turbohtml.Node.equals`. (:issue:`275`, :issue:`225`, :issue:`468`)
- Clean, canonicalize, and extract page URLs through :mod:`turbohtml.extract`, backed by a native URL pipeline (WHATWG
  splitting, percent-coding, relative-reference joining, UTS #46 IDNA, and Public Suffix List registrable-domain
  filtering), and run the toolkit from a ``turbohtml`` command line covering minify, detect, to-markdown, to-text, and
  sanitize. (:issue:`321`, :issue:`470`)

Bug fixes - 1.0.0
=================

- Bring tree construction to WHATWG conformance across foster parenting, foreign content, the select, table, and
  template insertion modes, doctype and quirks detection, duplicate and void-element handling, validated against the
  html5lib-tests corpus. (:issue:`32`)
- Resolve every label in the WHATWG Encoding Standard, decode the replacement and gb18030 families and the windows-1252
  C1 bytes, and honor the 1024-byte ``<meta>`` prescan. (:issue:`54`, :issue:`423`)
- Match the Selectors Level 4 corrections: forgiving ``:is()``/``:where()`` lists, quirks-mode case folding, CSS escape
  decoding, namespace prefixes, whitespace-only ``:empty``, and ``:scope`` resolution inside ``:has()``. (:issue:`174`)
- Fix XPath 1.0 semantics: shortest round-tripping number formatting, half-to-positive-infinity rounding, fixed function
  arity, node-set typing, and arena-safe predicate compilation. (:issue:`398`)
- Correct the Markdown and text exporters on nested and loose lists, link and image escaping, the ``<pre>`` leading
  newline, ordinal ``<li value>``, and raw-text suppression in every namespace. (:issue:`384`)
- Fix extraction on landmark-wrapped and list-structured articles, Microdata ``itemref`` merging, table ``rowspan``
  bounds, and the WHATWG form ``select``/``optgroup`` submission rules. (:issue:`385`, :issue:`408`)
- Keep the CSS and JavaScript minifiers value-safe: ``calc()`` type mixing, duplicate-declaration fallbacks,
  ``currentcolor``, conditional folding, and adjacent-string-literal concatenation. (:issue:`415`)
- Raise a precise, typed error from every public failure path in place of a silent wrong result or a leaked low-level
  type, and document each callable's exceptions. (:issue:`434`)

Improved documentation - 1.0.0
==============================

- Ship a migration guide for each library turbohtml replaces, every one carrying a monthly-downloads badge, a measured
  benchmark, and a speed-up multiplier, with the index ordered by adoption. (:issue:`313`)
- Restructure the reference, how-to, and migration trees around the eight-namespace taxonomy (parse/DOM, detect, query,
  clean, convert, extract, build, serialize), and add ``llms.txt`` maps, a sitemap, and per-page meta descriptions.
  (:issue:`478`)
- Add a written security policy, a "How turbohtml was built" page, and the seven design principles that shape the
  library. (:issue:`500`)

Packaging updates - 1.0.0
=========================

- Build the release wheels with profile-guided, link-time optimization trained offline over a real-world corpus of
  clean, malformed, legacy-encoded, and structured-data markup. (:issue:`481`)
- Pin every network-sourced C data table (IANA TLDs, the Public Suffix List, and the Unicode IDNA and NFC tables) to a
  named source commit and a SHA-256 checksum, so a rebuild is reproducible and aborts on a poisoned upstream.
  (:issue:`478`)

Miscellaneous internal changes - 1.0.0
======================================

- Finish the native-C port: URL splitting, percent-coding, relative joining, IDNA, registrable-domain lookup, and date
  parsing move from ``urllib``, ``re``, and ``datetime`` into the C extension, leaving Python a thin configure-and-wrap
  shim. (:issue:`478`)
- Speed the read path: a per-tree atom index runs a tag-pinned :meth:`~turbohtml.Node.find` several times faster,
  ``:has()`` evaluates in one amortized-linear pass, streaming serialization sizes its buffer up front, and link-time
  optimization re-inlines across a subsystem-first C layout. (:issue:`162`, :issue:`509`)
- Harden against untrusted input: ASan and UBSan fuzz gates on every entry point, a DOMPurify XSS oracle and an
  html5lib-tests tree-equality oracle over the sanitizer, mutation-XSS namespace checks, caps on element nesting and
  duplicate attributes, and one overflow-safe buffer-growth helper across the C core. (:issue:`503`, :issue:`511`)
- Validate free-threaded safety under pytest-run-parallel and ThreadSanitizer, and gate performance on every pull
  request with a per-operation CodSpeed benchmark over the real corpora. (:issue:`380`)

*********************
 v0.4.0 (2026-06-16)
*********************

Features - 0.4.0
================

- Build and edit the tree, not just read it: construct :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and
  :class:`~turbohtml.Comment` nodes and rearrange them with the full set of insert, wrap, extract, and normalize
  methods, with :attr:`~turbohtml.Element.attrs` and ``.text``/``.data`` as live setters. ``copy``, ``deepcopy``, and
  ``pickle`` duplicate a subtree - by :user:`gaborbernat`. (:issue:`19`)
- Round out the node model: :class:`~turbohtml.ProcessingInstruction` and :class:`~turbohtml.CData` join the hierarchy,
  :class:`~turbohtml.Doctype` exposes its :attr:`~turbohtml.Doctype.public_id` and :attr:`~turbohtml.Doctype.system_id`,
  and every node type supports structural pattern matching - by :user:`gaborbernat`. (:issue:`22`)

Improved documentation - 0.4.0
==============================

- Learn the write path through new tutorial, how-to, and explanation docs, backed by benchmarks showing turbohtml builds
  and rewrites trees about twice as fast as `lxml <https://lxml.de/>`__ and an order of magnitude faster than
  `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`__ - by :user:`gaborbernat`. (:issue:`19`)
- Port to turbohtml with migration guides from `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`__, `lxml
  <https://lxml.de/>`__, `selectolax <https://github.com/rushter/selectolax>`__, `html5lib
  <https://github.com/html5lib/html5lib-python>`__, and the standard library, each mapping the source library's idioms
  to their turbohtml equivalents and flagging behavior differences - by :user:`gaborbernat`. (:issue:`23`)

*********************
 v0.3.0 (2026-06-16)
*********************

Features - 0.3.0
================

- Query any node with CSS through :meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.select_one`, a native
  matcher covering type, universal, ``#id``, ``.class``, and attribute selectors (all operators plus the
  case-sensitivity flag) across the descendant, child, adjacent, and sibling combinators, returning comma groups in
  document order. An invalid selector raises ``ValueError`` - by :user:`gaborbernat`. (:issue:`14`)
- Search with a richer :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` filter grammar: match the tag
  and attributes by string, regex, bool, callable, or list (including ``class_`` and the ``attrs`` mapping), and choose
  the search direction with the ``axis`` keyword. ``find_all`` takes a ``limit`` and returns a ``list`` - by
  :user:`gaborbernat`. (:issue:`15`)
- Test a node against a selector with :meth:`~turbohtml.Node.matches` and :meth:`~turbohtml.Node.closest`: ``matches()``
  reports whether the node satisfies a CSS selector in context, and ``closest()`` returns the nearest matching ancestor
  (or the node itself), or ``None`` - by :user:`gaborbernat`. (:issue:`16`)
- Walk the tree by axis with new iterators: :attr:`~turbohtml.Node.next_siblings`,
  :attr:`~turbohtml.Node.previous_siblings`, document-order :attr:`~turbohtml.Node.following` and
  :attr:`~turbohtml.Node.preceding`, plus the :attr:`~turbohtml.Node.strings` and
  :attr:`~turbohtml.Node.stripped_strings` text iterators - by :user:`gaborbernat`. (:issue:`17`)
- Read HTML token-list attributes (``class``, ``rel``, ``headers``, ``sizes``, ``sandbox``, and the rest) as a
  ``list[str]`` in :attr:`turbohtml.Element.attrs`, split on ASCII whitespace; other attributes stay strings and
  valueless ones stay ``None`` - by :user:`gaborbernat`. (:issue:`18`)
- Control serialization on any node: :attr:`~turbohtml.Node.inner_html` returns the children, while
  :meth:`~turbohtml.Node.serialize` and :meth:`~turbohtml.Node.encode` take a ``formatter`` (the
  :class:`~turbohtml.Formatter` enum picks the escape policy) and an ``indent`` for pretty output. The default stays
  WHATWG-conformant HTML - by :user:`gaborbernat`. (:issue:`20`)
- Parse ``bytes`` directly: :func:`turbohtml.parse` sniffs the encoding with the WHATWG algorithm (BOM, ``encoding``
  argument, ``<meta>`` charset, then windows-1252), decodes with U+FFFD replacement, and reports the result in
  :attr:`~turbohtml.Document.encoding` - by :user:`gaborbernat`. (:issue:`21`)

*********************
 v0.2.0 (2026-06-11)
*********************

Features - 0.2.0
================

- Tokenize HTML directly with a WHATWG-conformant tokenizer: :func:`turbohtml.tokenize` for whole strings, the streaming
  :class:`turbohtml.Tokenizer`, and the :class:`turbohtml.Token` / :class:`turbohtml.TokenType` types, validated against
  the html5lib-tests tokenizer conformance suite. (:issue:`6`)
- Run :func:`turbohtml.escape` and :func:`turbohtml.unescape` faster: vectorized scanning and bulk copying speed up both
  calls, with unescaping of real escaped HTML about three times faster than the general lookup path. The benchmark now
  uses `pyperf <https://pyperf.readthedocs.io>`_ over multi-MiB real documents - by :user:`gaborbernat`. (:issue:`7`)

*********************
 v0.1.1 (2026-06-09)
*********************

Packaging updates - 0.1.1
=========================

- Install reliably from PyPI again: publishing each wheel in its own job keeps PEP 740 attestations within the Sigstore
  identity's lifetime, fixing the ``sigstore.oidc.ExpiredIdentity`` failure that blocked the first upload - by
  :user:`gaborbernat`. (:issue:`4`)

*********************
 v0.1.0 (2026-06-09)
*********************

Features - 0.1.0
================

- Speed up entity handling with C-accelerated :func:`turbohtml.escape` and :func:`turbohtml.unescape`, drop-in
  replacements for :func:`python:html.escape` and :func:`python:html.unescape`, shipped as wheels for CPython 3.10
  through 3.15 - by :user:`gaborbernat`. (:issue:`1`)
- Escape non-ASCII text that needs no escaping several times faster with a vectorized special-character scan, ahead of
  :func:`python:html.escape` - by :user:`gaborbernat`. (:issue:`3`)

Improved documentation - 0.1.0
==============================

- See the measured :func:`turbohtml.escape`/:func:`turbohtml.unescape` speedups in the README and docs, reproduce them
  with ``tox -e bench``, and browse a typed API reference with intersphinx links - by :user:`gaborbernat`. (:issue:`2`)

Miscellaneous internal changes - 0.1.0
======================================

- Automate releases with git-tag-derived versioning, a towncrier-managed changelog, and a prepare-release workflow that
  tags and triggers the trusted-publishing wheel build - by :user:`gaborbernat`. (:issue:`1`)
