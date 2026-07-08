###############
 From lol-html
###############

.. package-meta:: crates lol-html cloudflare/lol-html

`lol-html <https://github.com/cloudflare/lol-html>`_ is Cloudflare's streaming HTML rewriter -- the engine behind
Workers' ``HTMLRewriter`` that transforms responses at the edge. It is written in Rust (with a WebAssembly/JavaScript
binding) and, like turbohtml's rewriter, never builds a DOM: it runs the tokenizer over the input, matches CSS selectors
against the stack of open elements, and calls a handler for each match, emitting the rewritten bytes incrementally.
Because it is JavaScript/Rust rather than Python, this guide is a cross-language reference for teams moving an edge
rewriting pipeline onto turbohtml.

Both share the same design and the same non-negotiable constraint: a single forward pass with no lookahead, so working
memory stays proportional to the open-element depth rather than the document size. The port is mostly renaming --
``element!("a", |el| ...)`` becomes a ``(selector, handler)`` pair, and lol-html's ``Element`` methods have one-to-one
turbohtml counterparts.

***********************
 turbohtml vs lol-html
***********************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - lol-html
    - - Language
      - Python, over a C engine
      - Rust, with a WebAssembly/JavaScript binding
    - - Model
      - Single-pass, DOM-less rewrite over the open-element stack
      - Single-pass, DOM-less rewrite over the open-element stack
    - - Handlers
      - One :func:`~turbohtml.rewrite.rewrite` call taking element, text, comment, and doctype handlers
      - ``element!``/``text!``/``comments!``/``doctype!`` handler lists in a ``Settings``
    - - Streamable selectors
      - Type, universal, id, class, attribute; descendant and child combinators; ``:root``;
        ``:is()``/``:where()``/``:not()`` over that subset
      - Type, universal, id, class, attribute; descendant and child combinators; ``:nth-child``/``:nth-of-type``
    - - Output
      - Returns the rewritten string; untouched constructs copied verbatim
      - Writes rewritten byte chunks to an output sink
    - - Typing
      - Fully type annotated with bundled stubs
      - Rust types; TypeScript types on the WASM binding

Both restrict selectors to the subset a no-lookahead stream can decide, and the restrictions nearly coincide: neither
supports the sibling combinators (``+``, ``~``) or ``:has()``. They differ at the edges -- lol-html adds the positional
``:nth-child``/``:nth-of-type`` by bookkeeping sibling counts, which turbohtml's streamer does not; turbohtml adds
``:root`` and the functional ``:is()``/``:where()``/``:not()`` over the streamable subset. Where you need a positional
match on turbohtml, do the counting in the handler or :func:`turbohtml.parse` the region.

****************
 How to migrate
****************

An ``element_content_handlers`` list of ``element!`` closures becomes the ``elements`` argument -- a list of
``(selector, handler)`` pairs. The handler receives an :class:`~turbohtml.rewrite.Element` with the same edit methods:

.. code-block:: rust

    // lol-html (Rust)
    let mut output = vec![];
    let mut rewriter = HtmlRewriter::new(
        Settings {
            element_content_handlers: vec![element!("a[href]", |el| {
                el.set_attribute("rel", "noopener")?;
                el.set_attribute("target", "_blank")?;
                Ok(())
            })],
            ..Settings::default()
        },
        |chunk: &[u8]| output.extend_from_slice(chunk),
    );
    rewriter.write(html.as_bytes())?;
    rewriter.end()?;

.. testcode::

    from turbohtml.rewrite import rewrite


    def open_new_tab(link):
        link.set_attribute("rel", "noopener")
        link.set_attribute("target", "_blank")


    print(rewrite('<a href="https://x.test">x</a>', elements=[("a[href]", open_new_tab)]))

.. testoutput::

    <a href="https://x.test" rel="noopener" target="_blank">x</a>

The ``Element`` API maps method for method:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - lol-html ``Element``
      - turbohtml :class:`~turbohtml.rewrite.Element`
    - - ``get_attribute(name)`` / ``has_attribute(name)``
      - ``get`` / ``has_attribute``
    - - ``set_attribute(name, value)`` / ``remove_attribute(name)``
      - ``set_attribute`` / ``remove_attribute``
    - - ``before(content, ContentType)`` / ``after(content, ContentType)``
      - ``before`` / ``after`` (``html=True`` for raw)
    - - ``prepend(...)`` / ``append(...)``
      - ``prepend`` / ``append``
    - - ``set_inner_content(...)``
      - ``set_content``
    - - ``replace(...)`` / ``remove()`` / ``remove_and_keep_content()``
      - ``replace`` / ``remove`` / ``remove_and_keep_content``
    - - ``tag_name`` / ``attributes``
      - ``tag`` / ``attrs``

lol-html's ``ContentType::Html`` vs ``ContentType::Text`` distinction becomes the ``html`` keyword on every insertion
method: turbohtml HTML-escapes inserted content by default (the ``Text`` behavior) and inserts raw markup when you pass
``html=True`` (the ``Html`` behavior). The ``document_content_handlers`` -- ``text!``, ``comments!``, ``doctype!`` --
become the ``text``, ``comments``, and ``doctype`` keyword arguments, each a callable taking the same
:class:`~turbohtml.rewrite.Element` handle specialized to that node kind.

*************
 Performance
*************

turbohtml has no fair in-process peer for lol-html to benchmark against -- lol-html runs in Rust or WebAssembly, not the
CPython process. Both rewriters share the streaming design that matters most: the transform runs in one pass with a
working set proportional to the open-element depth, not the document size, so a page far larger than memory rewrites in
a fixed footprint. turbohtml's rewriter is a thin typed shim over the same C tokenizer and native CSS selector engine
that power :func:`turbohtml.parse` and :meth:`~turbohtml.Node.select`, so a rewrite pays for tokenization and
per-element selector matching only, never for tree construction.

See :doc:`/how-to/rewriting` for the full set of recipes and :doc:`/explanation/streaming` for the memory model and the
no-lookahead selector constraint the two rewriters share.
