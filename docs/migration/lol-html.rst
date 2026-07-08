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

*********************************
 When you need a tree: to_source
*********************************

lol-html's byte-preserving guarantee -- untouched tokens re-emitted verbatim -- is a property of its single forward
pass. That pass is also its constraint: a handler sees only the current element and its open ancestors, never a later
sibling or a descendant, so an edit that depends on content further down the document (or on a second look at content
already streamed past) is off the table. turbohtml offers a second route to the same byte preservation for exactly that
case. Parse the document into a tree, run any query or mutation the DOM allows -- positional selectors, ``:has()``,
cross-subtree lookups, repeated passes -- then re-emit with :meth:`~turbohtml.Node.to_source`, which copies the verbatim
source of every element and text run the parse left untouched and reserializes only the nodes you changed:

.. testcode::

    import turbohtml

    source = '<!DOCTYPE html><html><head></head><body><a HREF="/x">go</a></body></html>'
    doc = turbohtml.parse(source, source_locations=True)
    print(doc.to_source() == source)  # an unedited round trip is byte for byte

    for link in doc.find_all("a"):
        link.attrs["rel"] = "noopener"
    print(doc.to_source())

.. testoutput::

    True
    <!DOCTYPE html><html><head></head><body><a href="/x" rel="noopener">go</a></body></html>

The trade is the streaming rewriter's fixed working set for the tree's random access: ``to_source`` holds the whole
document in memory, where ``rewrite`` holds only the open-element depth. Reach for the rewriter at the edge and for a
page larger than memory; reach for ``to_source`` when the edit needs a query the stream cannot decide. Because the tree
is the post-error-recovery image of the source, the byte-exact round trip covers input that parsed without implied
elements or content reordering; where every byte of an error-recovering parse must survive, the streaming rewriter,
which never discards the token stream, is the tool. See :doc:`/explanation/serialization` for the full round-trip
contract.

*************
 Performance
*************

lol-html runs in Rust or WebAssembly, never in the CPython process, so it cannot share a harness with turbohtml. The
fair in-process peer is the work the streaming rewriter skips: the same visible transform -- ``rel="nofollow"`` on every
``a[href]``, ``loading="lazy"`` on every ``img``, every comment dropped -- reached instead through a full parse into a
DOM, a mutation pass, and a reserialization. lxml and BeautifulSoup take that route; turbohtml streams the three edits
in one pass and builds no tree.

.. bench-table::
    :file: bench/rewrite.json

The throughput gap is the tree. turbohtml's rewriter pays for tokenization and per-element selector matching only, so it
clears lxml's parse-mutate-serialize round trip by roughly threefold and BeautifulSoup's by more than an order of
magnitude. Peak resident memory is the same cost measured a second way. The DOM peers hold the whole parsed tree, while
the streaming pass retains only the open-element stack. A fixed interpreter baseline dominates the absolute figures on
these modest pages, so the margin reads small here, though it widens with the input, because the streaming footprint
tracks nesting depth rather than document size while a peer's tree tracks the whole document. A page far larger than
memory rewrites in a footprint no parse-first peer can hold. turbohtml's rewriter is a thin typed shim over the same C
tokenizer and native CSS selector engine that power :func:`turbohtml.parse` and :meth:`~turbohtml.Node.select`.

See :doc:`/how-to/rewriting` for the full set of recipes and :doc:`/explanation/streaming` for the memory model and the
no-lookahead selector constraint the two rewriters share.
