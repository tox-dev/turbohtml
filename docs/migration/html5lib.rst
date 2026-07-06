###############
 From html5lib
###############

.. package-meta:: html5lib html5lib/html5lib-python

`html5lib <https://html5lib.readthedocs.io>`_ is the reference pure-Python implementation of the WHATWG parsing
algorithm. It tokenizes a byte or character stream and builds a tree through a *treebuilder* you select at call time: an
:mod:`xml.etree.ElementTree` element by default, or ``dom``, or ``lxml``. On top of the tree it ships treewalkers that
convert between representations, a configurable serializer, a filter chain (whitespace collapsing, optional-tag
omission, alphabetical attributes, meta-charset injection), and a now-deprecated sanitizer. Because it tracks the spec
closely, it is the conformance baseline other parsers are checked against, and it is the ``html5lib`` backend
BeautifulSoup and others parse through.

turbohtml runs the same WHATWG algorithm in C and covers that ground with one engine. :func:`turbohtml.parse` returns a
single fully type annotated :class:`~turbohtml.Document` with navigation, ``find``/``select``/XPath querying, and WHATWG
serialization built in, so there is no foreign tree behind a treebuilder choice and no separate walk-and-serialize step.

***********************
 turbohtml vs html5lib
***********************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - html5lib
    - - Scope
      - WHATWG HTML: parse, tokenize, query, mutate, and serialize in one C engine
      - WHATWG HTML parse and tokenize into a pluggable third-party tree, plus treewalkers and a serializer
    - - Feature breadth
      - One typed tree with :meth:`~turbohtml.Node.find` grammar, CSS :meth:`~turbohtml.Node.select`, XPath, and WHATWG
        serialization formatters
      - etree/DOM/lxml treebuilders, treewalkers, serializer filters, deprecated sanitizer; no built-in query API
    - - Performance
      - C core; parse, tokenize, and fragment parse run 23x to 270x faster
      - Pure Python; the reference implementation, tuned for correctness not speed
    - - Typing
      - Ships ``py.typed``; every public method annotated
      - No inline types and no published stubs
    - - Dependencies
      - Zero runtime dependencies (self-contained C extension)
      - Requires ``webencodings`` and ``six``; ``lxml`` optional for that treebuilder, ``chardet`` optional for sniffing
    - - Maintenance
      - Actively developed, single-engine
      - Mature and stable; the conformance reference, with an infrequent release cadence

Feature overlap
===============

These port one-to-one; the calls differ only in name (see the mapping table under `How to migrate`_):

- Parsing a full document with the WHATWG algorithm: ``html5lib.parse`` maps to :func:`turbohtml.parse`.
- Fragment parsing in a container context: ``html5lib.parseFragment`` maps to :func:`turbohtml.parse_fragment`, the same
  WHATWG ``innerHTML`` fragment algorithm.
- Tokenizing without building a tree: the html5lib tokenizer maps to :func:`turbohtml.tokenize` and
  :class:`turbohtml.Tokenizer`, yielding :class:`~turbohtml.Token` values tagged by :class:`~turbohtml.TokenType`.
- Serializing a tree back to markup: ``html5lib.serialize`` maps to :meth:`~turbohtml.Node.serialize` and
  :attr:`~turbohtml.Node.html`.

What turbohtml adds
===================

- **A C engine.** Parsing, tokenizing, fragment parsing, and serialization all run in C, 23x to 270x faster than the
  pure-Python reference.
- **A built-in query API.** html5lib hands back a tree and stops; you navigate etree/DOM yourself. turbohtml carries
  :meth:`~turbohtml.Node.find` / :meth:`~turbohtml.Node.find_all`, CSS :meth:`~turbohtml.Node.select`, and
  :meth:`~turbohtml.Node.xpath` on the tree it returns.
- **One typed tree.** :class:`~turbohtml.Document`, :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and the other
  node types are sealed, pattern-matchable, and annotated, instead of a foreign tree chosen through a treebuilder.
- **Plain tag names with a separate namespace.** :attr:`~turbohtml.Element.tag` stays plain (``div``) and the namespace
  rides on :attr:`~turbohtml.Element.namespace` as a :class:`~turbohtml.Namespace`, rather than being folded into a
  Clark-notation name (``{http://www.w3.org/1999/xhtml}div``).
- **WHATWG-conformant serialization by default** through :class:`~turbohtml.Formatter` selection, with no serializer
  object to construct.
- **Zero runtime dependencies and full typing.** No ``webencodings`` or ``six`` install, and ``py.typed`` ships.

What html5lib has that turbohtml does not
=========================================

- **Pluggable treebuilders.** ``html5lib.parse(s, treebuilder="dom")`` (or ``"lxml"``, or the default ``"etree"``)
  returns whichever tree you name. turbohtml always returns its own typed tree; this is a deliberate clean-break
  omission. Workaround: if you need an ``ElementTree`` or ``lxml`` tree specifically, keep html5lib for that call.
- **Treewalkers.** html5lib converts one tree representation into another through ``html5lib.getTreeWalker``. turbohtml
  has a single representation, so there is nothing to walk between. No equivalent, and none needed.
- **Serializer filters.** html5lib's serializer chains filters for optional-tag omission, alphabetical attribute order,
  meta-charset injection, and whitespace. turbohtml serializes WHATWG-conformant output selected by
  :class:`~turbohtml.Formatter`; it does not expose that filter registry. Workaround: pick the closest ``Formatter`` and
  layout; for optional-tag omission there is no equivalent.
- **A (deprecated) sanitizer.** html5lib ships ``html5lib.filters.sanitizer``, deprecated since 1.1. turbohtml has no
  sanitizer. Workaround: use a dedicated sanitizer such as ``nh3`` or ``bleach`` (see :doc:`nh3` and :doc:`bleach`).
- **Optional statistical encoding detection.** With ``chardet`` installed, html5lib's input stream can guess an encoding
  from byte frequency when there is no BOM or ``<meta charset>``. turbohtml sniffs only what the WHATWG algorithm reads,
  then falls back to ``windows-1252``. Workaround: detect with ``charset-normalizer`` first and hand turbohtml the
  decoded ``str`` (or bytes with an explicit ``encoding=``).

Performance
===========

The algorithm runs in C, so parsing, tokenizing, and fragment parsing run 23x to 270x faster than the pure-Python
implementation (:func:`turbohtml.parse_fragment` parses an ``innerHTML``-style snippet in its container context, the
same WHATWG fragment algorithm html5lib's :func:`~html5lib.html5parser.parseFragment` runs):

.. bench-table::
    :file: bench/html5lib.json

****************
 How to migrate
****************

Swap the import. There is no treebuilder to name, since turbohtml always returns its own typed tree:

.. code-block:: python

    # html5lib
    import html5lib

    doc = html5lib.parse("<table><tr><td>x", treebuilder="etree")

.. testcode::

    from turbohtml import parse

    doc = parse("<table><tr><td>x")  # the same tree html5lib and a browser build
    print(doc.find("td").text)

.. testoutput::

    x

API mapping
===========

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `html5lib <https://html5lib.readthedocs.io/>`__
      - turbohtml
    - - :func:`html5lib.parse() <html5lib.html5parser.parse>`
      - :func:`turbohtml.parse`
    - - ``html5lib.parse(s, treebuilder="dom")``
      - one typed tree, no treebuilder choice
    - - :func:`html5lib.parseFragment() <html5lib.html5parser.parseFragment>`
      - :func:`turbohtml.parse_fragment`
    - - the html5lib tokenizer
      - :func:`turbohtml.tokenize`, :class:`turbohtml.Tokenizer`
    - - ``html5lib.serialize(tree)``
      - :meth:`~turbohtml.Node.serialize`, :attr:`~turbohtml.Node.html`
    - - ``el.tag`` namespaced (``{http://www.w3.org/1999/xhtml}div``)
      - :attr:`~turbohtml.Element.tag` plus :class:`~turbohtml.Namespace` on :attr:`~turbohtml.Element.namespace`
    - - the treebuilder's own walk and ``el.attrib``
      - :attr:`~turbohtml.Node.children`, :meth:`~turbohtml.Node.find` / :meth:`~turbohtml.Node.select`,
        :attr:`~turbohtml.Element.attrs`

Because turbohtml returns a queryable tree, the walk-the-etree step after parsing collapses into a ``find`` or
``select`` call:

.. testcode::

    doc = parse('<ul><li class="x">a</li><li>b</li></ul>')
    print([li.text for li in doc.find_all("li")])
    print(doc.select_one("li.x").text)

.. testoutput::

    ['a', 'b']
    a

**********************
 Gotchas and pitfalls
**********************

- **One tree, no treebuilder.** html5lib's output shape depends on the ``treebuilder`` you pass; turbohtml has one
  sealed typed tree, so the node types are fixed and pattern-matchable and there is nothing to select.
- **Tag names are plain, namespaces are separate.** html5lib's etree treebuilder namespaces element names in Clark
  notation (``{http://www.w3.org/1999/xhtml}div``, and default ``namespaceHTMLElements=True``). turbohtml keeps
  :attr:`~turbohtml.Element.tag` plain and carries the namespace on :attr:`~turbohtml.Element.namespace`:

  .. testcode::

      from turbohtml import Namespace

      svg = parse("<svg><rect/></svg>").find("rect")
      print((svg.tag, svg.namespace is Namespace.SVG))

  .. testoutput::

      ('rect', True)

- **Attributes read through ``attrs``, not ``attrib``.** html5lib's etree tree exposes ``el.attrib``; turbohtml uses
  :attr:`~turbohtml.Element.attrs`, and multi-valued attributes (``class``, ``rel``, ...) read back as a ``list[str]``.
- **Encoding sniffing stops at the markup.** ``parse`` runs the WHATWG byte path — BOM, then a ``<meta charset>``
  prescan, then a ``windows-1252`` fallback. html5lib with ``chardet`` installed can additionally guess from byte
  frequency; where that matters, detect the encoding first and hand turbohtml the decoded ``str``.
- **No serializer object or filter chain.** html5lib builds a serializer and threads filters through it; turbohtml
  serializes directly with :meth:`~turbohtml.Node.serialize` and a :class:`~turbohtml.Formatter`, and does not offer
  optional-tag omission or attribute reordering.
