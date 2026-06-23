###########
 turbohtml
###########

``turbohtml`` is a fast, fully typed HTML toolkit for Python built on a C-accelerated core. It provides spec-correct
HTML escaping and unescaping that match the standard library byte for byte, a WHATWG-conformant streaming tokenizer, and
a WHATWG-conformant parser that builds a navigable, lazily-wrapped tree you query with CSS selectors, edit in place,
build from scratch, and serialize back to conformant HTML. Each runs several times faster than its pure-Python
counterpart and supports the free-threaded build.

.. testcode::

    import turbohtml

    print(turbohtml.escape('<a href="?x=1&y=2">Tom & Jerry</a>'))
    print(turbohtml.unescape("caf&eacute; &amp; r&eacute;sum&eacute;"))
    print([token.tag or token.data for token in turbohtml.tokenize("<p>Tom &amp; Jerry</p>")])
    doc = turbohtml.parse("<p>Tom &amp; <a href='/j'>Jerry</a></p>")
    print([link.attrs["href"] for link in doc.find_all("a")])
    print(doc.find("p").text)

.. testoutput::

    &lt;a href=&quot;?x=1&amp;y=2&quot;&gt;Tom &amp; Jerry&lt;/a&gt;
    café & résumé
    ['p', 'Tom & Jerry', 'p']
    ['/j']
    Tom & Jerry

.. important::

    Learn this rule first: turbohtml models text as real **child nodes** following the WHATWG DOM shape, where `lxml
    <https://lxml.de>`_ uses ``text``/``tail`` and `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_
    uses ``.string``. So ``node[i]`` indexes a node's children, and attributes are reached through ``node.attrs``, never
    ``node["attr"]``.

*******************
 Design principles
*******************

These rules shape every part of turbohtml, from the C core to the typed surface you import.

1. **Speed over ease of maintenance.** The hot path is C: the tokenizer, the WHATWG tree builder, the CSS and XPath
   engines, escaping, and serialization run over a single bump-allocated arena that holds no Python objects. Python
   appears only at the typed API edge, where a thin facade wraps the nodes you actually touch.
2. **A modern, fully-typed API.** Every concept carries one name and the whole surface is type-annotated. turbohtml is
   not a drop-in for the libraries it replaces; the ``turbohtml.migration`` modules and guides translate code from
   BeautifulSoup, lxml, html5lib, markupsafe, and the standard library rather than aliasing their APIs.
3. **Still maintainable.** The C sources are split by subsystem and the code is written to read as its own
   documentation. Both Python and C coverage gates require 100% line and branch coverage, on the gcc and llvm-cov
   toolchains alike, before a change lands.
4. **WHATWG conformance first.** The tokenizer and tree builder follow the WHATWG HTML standard state by state,
   validated against the shared html5lib-tests suite browsers use. turbohtml matches a competitor's behavior only where
   the spec leaves it open.
5. **Free-threading ready.** The extension holds no shared mutable state and declares free-threading support, and every
   tree edit and string read runs under a per-tree critical section. A read snapshots the arena before any Python
   callback runs, so a concurrent mutation can never tear a walk.
6. **Native and dependency-free.** The core is pure C with no libxml2 or lxml underneath, accelerated with SIMD, SWAR,
   and an incremental codec. It reuses the standard library for solved problems such as URL resolution and regex
   matching instead of reimplementing them.
7. **Benchmark-driven and competitor-informed.** Designs are measured with pyperf against the fastest implementations
   across C, Rust, and Go, and adopt their proven techniques: the lexbor and html5ever arena layout, html5ever's bulk
   text scan, the Rust ``linkify`` scanner. A change that regresses the benchmarks does not ship.

The documentation follows the `Diátaxis <https://diataxis.fr>`_ framework.

.. toctree::
    :maxdepth: 1

    tutorials/index
    how-to/index
    migration/index
    reference
    explanation/index
    development/index
    changelog
    license
