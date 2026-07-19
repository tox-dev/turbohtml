################
 From soupsieve
################

.. package-meta:: soupsieve facelessuser/soupsieve

`soupsieve <https://facelessuser.github.io/soupsieve/>`_ is BeautifulSoup's CSS selector engine and the library behind
every ``Tag.select`` call. ``soupsieve.compile(selector)`` returns a reusable ``SoupSieve`` matcher, and the
module-level ``select`` / ``select_one`` / ``iselect`` / ``match`` / ``filter`` / ``closest`` helpers run one-shot
queries over bs4 tags. Its scope is deliberately narrow: it interprets a CSS selector against an already-parsed tree of
BeautifulSoup ``Tag`` objects, one element at a time, in Python. It covers a broad slice of Selectors Level 3 and 4 and
adds a few proprietary extensions (``:-soup-contains()``, ``[attr!=value]``, custom pseudo-class aliases). Because it is
the default engine that ``bs4`` reaches for, it ships with nearly every project that scrapes or queries HTML in Python.

:mod:`turbohtml.query` covers that same ground with the same call shapes over turbohtml's native selector engine. A port
swaps ``import soupsieve`` for ``from turbohtml import query`` and keeps its structure: the matcher methods and module
helpers keep soupsieve's names, and turbohtml parses the HTML itself rather than depending on a separate BeautifulSoup
tree.

************************
 turbohtml vs soupsieve
************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - soupsieve
    - - Scope
      - Full WHATWG HTML parser with a native CSS selector engine, an XPath engine, and serialization in one package.
      - CSS selector engine only; relies on BeautifulSoup to parse and hold the tree it queries.
    - - Feature breadth
      - Selectors Level 4 subset over the native tree, plus :meth:`~turbohtml.Node.find_all` keyword filters and
        :meth:`~turbohtml.Node.xpath`.
      - Broad Selectors 3/4 support plus proprietary ``:-soup-contains()``, ``[attr!=value]``, ``custom=`` pseudo-class
        aliases, and namespace discrimination.
    - - Performance
      - Selector compiled against the tree once, matched by comparing interned integer atoms in C.
      - Each selector interpreted in Python per element on every call.
    - - Typing
      - Ships ``py.typed`` and full stubs; every public name is annotated.
      - Ships ``py.typed`` and type hints.
    - - Dependencies
      - One self-contained package with a bundled C extension; the parser is built in.
      - No hard runtime dependency, but it operates on BeautifulSoup trees, so bs4 is the practical runtime.
    - - Maintenance
      - Actively maintained.
      - Actively maintained; the canonical selector engine shipped with BeautifulSoup.

Feature overlap
===============

These port 1:1 -- same names, turbohtml nodes in place of bs4 tags:

- :func:`~turbohtml.query.compile` returning a reusable :class:`~turbohtml.query.Matcher`, the analog of ``SoupSieve``.
- The module helpers :func:`~turbohtml.query.select`, :func:`~turbohtml.query.select_one`,
  :func:`~turbohtml.query.iselect`, :func:`~turbohtml.query.match`, :func:`~turbohtml.query.filter`, and
  :func:`~turbohtml.query.closest`.
- The same six methods on :class:`~turbohtml.query.Matcher`, plus its ``pattern`` / ``namespaces`` / ``flags``
  properties.
- ``limit=`` on ``select`` / ``iselect`` to cap the number of matches.
- :func:`turbohtml.query.escape_identifier` for building a selector around untrusted class or id text.
- :class:`turbohtml.SelectorSyntaxError` (a :class:`ValueError`) raised on a malformed selector.

What turbohtml adds
===================

- A compiled matcher runs in C against interned atoms instead of interpreting the selector in Python per element (see
  Performance).
- Parsing is built in: :func:`turbohtml.parse` produces the tree the selector runs over, so there is no separate
  BeautifulSoup dependency to install and keep in sync.
- :func:`~turbohtml.query.css` is a readable alias of :func:`~turbohtml.query.compile` for call sites that read as
  ``css(selector)``.
- Beyond CSS, the same tree answers :meth:`~turbohtml.Node.find_all` keyword filters (``attrs=``, ``class_=``,
  ``text=``) and :meth:`~turbohtml.Node.xpath` queries.
- Spec-conformant matching where soupsieve 2.8 diverges: an only child matches a functional ``:nth-child(An+B)``, and
  ``input[type=hidden]`` counts as ``:enabled`` (see Gotchas).
- No global compile cache to manage, so ``soupsieve.purge()`` has no analog and needs none -- a
  :class:`~turbohtml.query.Matcher` is the reusable artifact you hold yourself.

What soupsieve has that turbohtml does not
==========================================

- ``:-soup-contains()`` / ``:-soup-contains-own()`` text selectors. No CSS equivalent; query text through
  :meth:`~turbohtml.Node.find_all` with ``text=`` instead.
- The proprietary ``[attr!=value]`` selector. Write ``:not([attr=value])``.
- ``custom={":--name": "..."}`` pseudo-class aliases. No equivalent; inline the aliased selector, with ``:is(...)``
  covering most uses.
- Namespace discrimination: soupsieve honors the ``namespaces`` map for prefixed type selectors. turbohtml selects by
  local name, so ``svg|rect`` matches every ``rect`` regardless of prefix; ``namespaces`` is carried for parity but
  inert.
- Three tracked selector gaps where the engines still disagree on real documents: `#349
  <https://github.com/tox-dev/turbohtml/issues/349>`__, `#350 <https://github.com/tox-dev/turbohtml/issues/350>`__,
  `#351 <https://github.com/tox-dev/turbohtml/issues/351>`__.

Performance
===========

soupsieve interprets each selector in Python per element; turbohtml compiles it against the tree once and matches by
comparing interned integer atoms in C, so a compiled ``select`` runs 196 to 1,480 times faster across real pages and
per-element ``match`` 78 to 145 times faster:

.. bench-table::
    :file: bench/soupsieve.json

The engines agree on what they select: on a corpus of 247 selectors harvested from soupsieve's own test suite, both pick
identical elements for every comparable selector over real documents, and the disagreements are the three tracked
turbohtml gaps above plus two spots where soupsieve itself strays from the spec (see Gotchas).

****************
 How to migrate
****************

Swap the import and parse with turbohtml; the matcher and helper names carry over:

.. code-block:: python

    # soupsieve
    import soupsieve as sv
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    matcher = sv.compile("li.on a[href]")
    matcher.select(soup, limit=5)

    # turbohtml
    from turbohtml import parse, query

    doc = parse(html)
    matcher = query.compile("li.on a[href]")
    matcher.select(doc, limit=5)

Only the trees and the keyword bundle differ; every call name stays.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - soupsieve
      - turbohtml :mod:`~turbohtml.query`
    - - ``sv.compile(sel, namespaces, flags)``
      - :func:`~turbohtml.query.compile` (``namespaces``/``flags`` ride on a frozen :class:`~turbohtml.query.Matching`:
        ``compile(sel, Matching.soupsieve(namespaces, flags))``)
    - - ``sv.select(sel, tag, limit=n)`` and the other module helpers
      - the same names taking turbohtml nodes: ``query.select(sel, node, limit=n)``
    - - ``SoupSieve.select`` / ``select_one`` / ``iselect`` / ``match`` / ``filter`` / ``closest``
      - the same methods on :class:`~turbohtml.query.Matcher`
    - - ``SoupSieve.pattern`` / ``.namespaces`` / ``.flags``
      - the same properties on :class:`~turbohtml.query.Matcher`
    - - ``sv.escape(ident)``
      - :func:`turbohtml.query.escape_identifier`
    - - ``soupsieve.SelectorSyntaxError``
      - :class:`turbohtml.SelectorSyntaxError` (a :class:`ValueError`, matching the native engine)
    - - ``sv.purge()`` (drop the global compile cache)
      - not needed; there is no global cache, a :class:`~turbohtml.query.Matcher` is the reusable artifact
    - - ``custom={":--name": "..."}`` pseudo-class aliases
      - not supported; inline the aliased selector (``:is(...)`` covers most uses)

A full round-trip, from parse to selection to matching:

.. testcode::

    from turbohtml import parse, query

    doc = parse('<ul><li class="on"><a href="/a">a</a></li><li><a href="/b">b</a></li></ul>')
    print([a.attr("href") for a in query.select("li.on a[href]", doc)])
    print(query.match("li.on", query.compile("li").closest(query.select_one("a", doc))))

.. testoutput::

    ['/a']
    True

**********************
 Gotchas and pitfalls
**********************

- The nodes are turbohtml's, so parse with :func:`turbohtml.parse` and pass the :class:`~turbohtml.Document` or an
  :class:`~turbohtml.Element` where soupsieve took a bs4 ``Tag``; matches come back as turbohtml elements with
  :meth:`~turbohtml.Element.attr` / :attr:`~turbohtml.Node.text` instead of ``tag["attr"]`` / ``tag.get_text()``.
- soupsieve's proprietary selectors raise :class:`~turbohtml.SelectorSyntaxError`: ``[attr!=value]`` (write
  ``:not([attr=value])``) and ``:-soup-contains()`` / ``:-soup-contains-own()`` (query text through
  :meth:`~turbohtml.Node.find_all` with ``text=`` instead).
- ``namespaces`` and ``flags`` are carried for parity but never change which elements match: turbohtml selects by local
  name, so ``svg|rect`` matches every ``rect`` whatever the mapping says, and ``DEBUG`` prints nothing.
- Two soupsieve 2.8 behaviors are off spec and not reproduced: an only child never matches a functional
  ``:nth-child(An+B)`` in soupsieve (an only child sits at position 1, so ``:nth-child(-n+3)`` must match it here), and
  ``input[type=hidden]`` is excluded from ``:enabled`` in soupsieve (the current HTML spec no longer carves out hidden
  inputs).
- Pseudo-classes soupsieve accepts and matches never (``:current``, ``:paused``, ``:local-link``, ``:host``, the removed
  ``:matches()``, ...) raise here instead, turning a selector that silently returned nothing into a loud error.
