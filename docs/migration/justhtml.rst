###############
 From JustHTML
###############

.. package-meta:: justhtml emilstenstrom/justhtml

`JustHTML <https://emilstenstrom.github.io/justhtml/>`_ is a dependency-free, pure-Python HTML5 toolkit. It combines
browser-style parsing, a Python DOM, CSS selectors, transforms, text and Markdown output, and sanitization enabled by
default. Its pure-Python implementation suits Pyodide, PyPy, and deployments where compiling an extension is not an
option.

turbohtml covers the same pipeline in a C extension. Parsing preserves input by default; use
:func:`turbohtml.clean.sanitize` at an untrusted-HTML boundary. Migrating ``JustHTML(source)`` to a bare
:func:`turbohtml.parse` would preserve markup that JustHTML removed.

***********************
 turbohtml vs JustHTML
***********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - JustHTML
    - - Parser
      - WHATWG tree builder in C, with document, fragment, incremental, SAX, and custom-tree entry points
      - WHATWG tree builder in Python, with document, fragment, and streaming entry points
    - - Published WPT comparison
      - 1,872/1,872 applicable cases (100%) in JustHTML's 1,880-case Python-parser comparison at WPT ``4830edb`` with
        the parser fixes in :issue:`716`, :issue:`717`, and :issue:`718`. TurboHTML matches 1,872/1,880 raw fixture
        trees. The other eight fixture expectations contradict the living standard's `foreign-content end-tag algorithm
        <https://html.spec.whatwg.org/multipage/parsing.html#parsing-main-inforeign>`__. They are the four ``</p>`` and
        four ``</br>`` expectations in `foreign-fragment.dat
        <https://github.com/web-platform-tests/wpt/blob/4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/foreign-fragment.dat#L565-L612>`__
        and `tests26.dat
        <https://github.com/web-platform-tests/wpt/blob/4830edb033cb486fd0cd6f85b5e937cfc718704d/html/syntax/parsing/resources/tests26.dat#L395-L453>`__;
        :issue:`719` tracks the broader living corpus, where TurboHTML matches all 1,916 applicable trees.
      - `1,880/1,880 (100%) <https://emilstenstrom.github.io/justhtml/comparison.html>`__ against the fixture text
    - - Safety default
      - Parsing preserves input; sanitization is an explicit :mod:`turbohtml.clean` operation
      - ``JustHTML(source)`` sanitizes; ``sanitize=False`` preserves trusted input
    - - Queries
      - CSS Selectors Level 4, including ``:has()``, plus ``find`` filters, XPath 1.0, and regex extraction
      - CSS selectors through ``query`` and ``query_one``; no XPath or regex query API
    - - Mutation
      - Typed DOM methods, bulk selector edits, mutation observers, ranges, and streaming rewrite
      - Python node methods plus a composable transform pipeline
    - - Output
      - Compact, pretty, minified, source-preserving, XML, canonical XML, text, and Markdown output
      - Compact or pretty HTML, text, and Markdown output
    - - Deployment
      - Self-contained C extension with platform wheels and no runtime dependencies
      - Pure Python with no runtime dependencies

Shared surface
==============

Both libraries parse documents and context-sensitive fragments into navigable HTML5 trees. Their overlapping public
surface covers CSS selection, element matching, mutation, sanitization, linkification, HTML serialization, text
collection, Markdown conversion, and incremental input.

Reasons to choose turbohtml
===========================

Choose turbohtml when native speed or a larger query surface matters. A node supports CSS ``:has()``,
:meth:`~turbohtml.Node.find`, XPath 1.0, and regex extraction. The same package handles XML, source-preserving edits,
browser-shaped DOM operations, native metadata extraction, SAX, custom-tree construction, and streaming rewrites.

Reasons to keep JustHTML
========================

Keep JustHTML when the runtime cannot load native extensions, such as Pyodide or some PyPy deployments. Its constructor
can sanitize and apply a declarative transform list in one step. turbohtml requires a supported wheel or a C toolchain
and keeps parsing, sanitization, and later edits as separate calls.

Performance
===========

The table compares raw parsing with ``sanitize=False`` so both parsers preserve the input. The sanitizer row uses each
library's default policy and includes serialization; policy differences mean that row measures the default secure
pipeline, not byte-identical output. Mutation rows parse outside the timed section and hand each library a fresh tree.
The cases follow `JustHTML's correctness harness
<https://github.com/EmilStenstrom/justhtml/blob/main/benchmarks/correctness.py>`__ where the APIs overlap. URL
discovery, URL rewriting, and the configured Markdown case stay out of the table because JustHTML does not expose
equivalent public operations.

.. bench-table::
    :file: bench/justhtml.json

***********
 Migration
***********

Choose the turbohtml entry point from the input's trust boundary:

.. testcode::

    from turbohtml import parse
    from turbohtml.clean import Policy, sanitize

    trusted = parse("<main><p class='intro'>Hello</p></main>")
    print(trusted.select_one("p.intro").text)

    untrusted = "<p>Hello<script>alert(1)</script></p>"
    print(sanitize(untrusted, Policy.relaxed()))

.. testoutput::

    Hello
    <p>Hello&lt;script&gt;alert(1)&lt;/script&gt;</p>

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - JustHTML
      - turbohtml
    - - ``JustHTML(source, sanitize=False).root``
      - :func:`turbohtml.parse`
    - - ``JustHTML(source)``
      - :func:`turbohtml.clean.sanitize` when the result is HTML, or sanitize then :func:`turbohtml.parse` for later
        tree operations
    - - ``JustHTML(source, fragment=True).root``
      - :func:`turbohtml.parse_fragment`
    - - ``JustHTML(source, fragment_context=FragmentContext("tbody")).root``
      - ``parse_fragment(source, "tbody")``
    - - ``node.query(selector)``
      - :meth:`turbohtml.Node.select`
    - - ``node.query_one(selector)``
      - :meth:`turbohtml.Node.select_one`
    - - ``matches(node, selector)``
      - :meth:`turbohtml.Node.matches`
    - - ``element.name``
      - :attr:`turbohtml.Element.tag`
    - - ``element.attrs``
      - :attr:`turbohtml.Element.attrs`
    - - ``node.children``, ``node.parent``
      - :attr:`turbohtml.Node.children`, :attr:`turbohtml.Node.parent`
    - - ``node.to_html(pretty=False)``
      - :attr:`turbohtml.Node.html`
    - - ``node.to_text(separator="", strip=False)``
      - :attr:`turbohtml.Node.text`
    - - ``node.to_markdown()``
      - :meth:`turbohtml.Node.to_markdown`
    - - ``parent.append_child(child)``
      - :meth:`turbohtml.Element.append`
    - - ``parent.remove_child(child)``
      - :meth:`turbohtml.Node.decompose` on ``child``
    - - ``Drop(selector)``
      - :meth:`turbohtml.Node.remove`
    - - ``Unwrap(selector)``
      - :meth:`turbohtml.Node.strip_tags`
    - - ``Linkify()``
      - :func:`turbohtml.clean.linkify` or a reusable :class:`turbohtml.clean.Linker`
    - - ``stream(...)``
      - :class:`turbohtml.IncrementalParser`, :class:`turbohtml.Tokenizer`, or :func:`turbohtml.rewrite.rewrite`,
        depending on whether the caller needs a DOM, tokens, or rewritten output

Behavioral differences
======================

- :func:`turbohtml.parse` does not sanitize. Replace a default ``JustHTML(source)`` with
  :func:`turbohtml.clean.sanitize` when ``source`` may contain attacker-controlled markup; a bare parse preserves it.
- JustHTML's selector engine does not accept ``:has()``. A selector kept within that subset ports as-is; turbohtml can
  simplify Python-side parent filtering by moving it into ``:has()``.
- JustHTML uses ``node.name`` for elements and special nodes. turbohtml uses :attr:`~turbohtml.Element.tag` on elements
  and distinct typed classes for text, comments, doctypes, processing instructions, documents, and fragments.
- JustHTML enables scripting in the tree builder by default. turbohtml's fragment and document parsers default to
  ``scripting=False``; pass ``scripting=True`` when ``noscript`` must follow browser behavior with scripting enabled.
- A JustHTML fragment context is a ``FragmentContext`` object; turbohtml accepts the HTML tag name as the second
  :func:`turbohtml.parse_fragment` argument. Both context APIs affect table foster parenting, raw-text elements, and the
  initial insertion mode, so omitting the context can change the tree.
- For ``<?foo bar?>``, JustHTML 3.11 keeps ``"foo bar"`` in the node's ``data`` and serializes the closing ``?>``.
  turbohtml exposes ``target == "foo"`` and ``data == "bar"`` as separate fields. HTML serialization follows the living
  HTML syntax and emits ``<?foo bar>``; XML serialization emits ``<?foo bar?>``. The reserved ``xml`` and
  ``xml-stylesheet`` targets remain comments in the HTML parser.
- JustHTML's general ``Node`` API exposes ``name``, ``data``, ``attrs``, and ``children`` according to node kind.
  turbohtml uses typed node classes, so type narrowing determines whether ``tag``, ``data``, ``target``, or ``attrs`` is
  available. Static type checkers catch element access once code narrows to :class:`turbohtml.Element`.
- ``to_text`` can insert separators and strip each text node in one call. Use :attr:`~turbohtml.Node.text` for DOM
  ``textContent`` semantics, :attr:`~turbohtml.Node.stripped_strings` with ``join`` for custom separators, or
  :meth:`~turbohtml.Node.to_text` for layout-aware rendering.
- JustHTML transform order is part of its result. Preserve that order when translating a transform list into explicit
  turbohtml node operations; use :func:`turbohtml.rewrite.rewrite` when the pipeline should stay DOM-less.
