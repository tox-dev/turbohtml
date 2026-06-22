###########
 Changelog
###########

.. towncrier-draft-entries:: Unreleased

.. towncrier release notes start

*********************
 v0.4.0 (2026-06-16)
*********************

Features - 0.4.0
================

- Build and edit the tree, not just read it: construct ``Element``, ``Text``, and ``Comment`` nodes and rearrange them
  with the full set of insert, wrap, extract, and normalize methods, with ``attrs`` and ``.text``/``.data`` as live
  setters. ``copy``, ``deepcopy``, and ``pickle`` duplicate a subtree - by :user:`gaborbernat`. (:issue:`19`)
- Round out the node model: :class:`~turbohtml.ProcessingInstruction` and :class:`~turbohtml.CData` join the hierarchy,
  :class:`~turbohtml.Doctype` exposes its ``public_id`` and ``system_id``, and every node type supports structural
  pattern matching - by :user:`gaborbernat`. (:issue:`22`)

Improved documentation - 0.4.0
==============================

- Learn the write path through new tutorial, how-to, and explanation docs, backed by benchmarks showing turbohtml builds
  and rewrites trees about twice as fast as lxml and an order of magnitude faster than BeautifulSoup - by
  :user:`gaborbernat`. (:issue:`19`)
- Port to turbohtml with migration guides from BeautifulSoup, lxml, selectolax, html5lib, and the standard library, each
  mapping the source library's idioms to their turbohtml equivalents and flagging behavior differences - by
  :user:`gaborbernat`. (:issue:`23`)

*********************
 v0.3.0 (2026-06-16)
*********************

Features - 0.3.0
================

- Query any node with CSS through ``select()`` and ``select_one()``, a native matcher covering type, universal, ``#id``,
  ``.class``, and attribute selectors (all operators plus the case-sensitivity flag) across the descendant, child,
  adjacent, and sibling combinators, returning comma groups in document order. An invalid selector raises ``ValueError``
  - by :user:`gaborbernat`. (:issue:`14`)
- Search with a richer ``find()`` and ``find_all()`` filter grammar: match the tag and attributes by string, regex,
  bool, callable, or list (including ``class_`` and the ``attrs`` mapping), and choose the search direction with the
  ``axis`` keyword. ``find_all`` takes a ``limit`` and returns a ``list`` - by :user:`gaborbernat`. (:issue:`15`)
- Test a node against a selector with ``matches()`` and ``closest()``: ``matches()`` reports whether the node satisfies
  a CSS selector in context, and ``closest()`` returns the nearest matching ancestor (or the node itself), or ``None`` -
  by :user:`gaborbernat`. (:issue:`16`)
- Walk the tree by axis with new iterators: ``next_siblings``, ``previous_siblings``, document-order ``following`` and
  ``preceding``, plus the ``strings`` and ``stripped_strings`` text iterators - by :user:`gaborbernat`. (:issue:`17`)
- Read HTML token-list attributes (``class``, ``rel``, ``headers``, ``sizes``, ``sandbox``, and the rest) as a
  ``list[str]`` in ``Element.attrs``, split on ASCII whitespace; other attributes stay strings and valueless ones stay
  ``None`` - by :user:`gaborbernat`. (:issue:`18`)
- Control serialization on any node: ``inner_html`` returns the children, while ``serialize()`` and ``encode()`` take a
  ``formatter`` (the ``Formatter`` enum picks the escape policy) and an ``indent`` for pretty output. The default stays
  WHATWG-conformant HTML - by :user:`gaborbernat`. (:issue:`20`)
- Parse ``bytes`` directly: ``parse()`` sniffs the encoding with the WHATWG algorithm (BOM, ``encoding`` argument,
  ``<meta>`` charset, then windows-1252), decodes with U+FFFD replacement, and reports the result in
  ``Document.encoding`` - by :user:`gaborbernat`. (:issue:`21`)

*********************
 v0.2.0 (2026-06-11)
*********************

Features - 0.2.0
================

- Tokenize HTML directly with a WHATWG-conformant tokenizer: :func:`turbohtml.tokenize` for whole strings, the streaming
  :class:`turbohtml.Tokenizer`, and the :class:`turbohtml.Token` / :class:`turbohtml.TokenType` types, validated against
  the html5lib-tests tokenizer conformance suite. (:issue:`6`)
- Run ``escape`` and ``unescape`` faster: vectorized scanning and bulk copying speed up both calls, with unescaping of
  real escaped HTML about three times faster than the general lookup path. The benchmark now uses `pyperf
  <https://pyperf.readthedocs.io>`_ over multi-MiB real documents - by :user:`gaborbernat`. (:issue:`7`)

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

- See the measured ``escape``/``unescape`` speedups in the README and docs, reproduce them with ``tox -e bench``, and
  browse a typed API reference with intersphinx links - by :user:`gaborbernat`. (:issue:`2`)

Miscellaneous internal changes - 0.1.0
======================================

- Automate releases with git-tag-derived versioning, a towncrier-managed changelog, and a prepare-release workflow that
  tags and triggers the trusted-publishing wheel build - by :user:`gaborbernat`. (:issue:`1`)
