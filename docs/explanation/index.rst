#############
 Explanation
#############

These pages explain how turbohtml is built and why it makes the choices it does: where the C core earns its keep, how
the parser, tree model, query engines, serializers, and the free-threaded build fit together, and which trade-offs each
one accepts. Start here for the *why*; the :doc:`/reference` has the *what*.

*******************************************
 When to reach for turbohtml, and when not
*******************************************

turbohtml parses, queries, edits, and serializes HTML through a fast, typed, WHATWG-conformant core. Reach for it when
you parse real-world markup and want the tree a browser builds (the `html5lib
<https://github.com/html5lib/html5lib-python>`_ suite passes, so malformed input recovers the way it does in a browser
rather than the way libxml2 guesses); when speed matters (the :doc:`/development/performance` page has the figures);
when you want a modern typed API with one name per concept, ``__match_args__`` on every node, and full type stubs,
alongside the free-threaded build; or when you escape, unescape, or tokenize on a hot path and want a drop-in several
times faster than the standard library.

It is the wrong tool in a few honest cases:

- **You need DTD or Schematron validation.** turbohtml gives CSS selectors, the ``find`` filter grammar, an XPath 1.0
  engine, an :doc:`XSLT 1.0 processor <xslt>`, C14N, and :doc:`XSD / RELAX NG validation <validation>`, but not `lxml
  <https://lxml.de>`_'s DTD or Schematron validation. Code that leans on those should stay on lxml.
- **You depend on `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_'s ecosystem or its forgiving,
  duck-typed API.** ``bs4`` swaps parser backends, integrates with a long tail of tools, and accepts almost any shape;
  turbohtml is one conformant parser with a sealed, typed hierarchy. Code written to ``bs4``'s contract needs the
  :doc:`/migration/index` guide, not a drop-in import.
- **You need a decades-hardened dependency.** lxml and BeautifulSoup have been battle-tested for years across every
  platform and corner case; turbohtml is young.
- **HTML is not your bottleneck.** If parsing is a rounding error in your workload, the library you already use is fine.
  turbohtml's advantage is speed and a typed API; if you need neither, switching costs more than it saves.

.. toctree::
    :maxdepth: 1

    c-core
    stdlib-parity
    parsing
    xml
    validation
    sax
    streaming
    source-locations
    tree-model
    traversal
    ranges
    shadow-dom
    queries
    cssom
    xslt
    structured-data
    serialization
    canonicalization
    main-content
    mutation
    sanitizing
    free-threading
