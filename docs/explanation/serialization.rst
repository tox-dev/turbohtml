##########################
 Serialization and export
##########################

One parsed tree fans out into several output forms. The same arena tree serializes back to conformant HTML, folds into
minified HTML, or walks into Markdown, plain text, and annotated text. The purple source is the tree; every green node
is one rendering of it.

.. mermaid::

    flowchart LR
        tree([parsed tree])
        tree --> html["html / serialize / encode"]
        tree --> minify["Minify"]
        tree --> md["to_markdown"]
        tree --> text["text"]
        tree --> annotated["to_annotated_text"]

        classDef src fill:#ede7f6,stroke:#4527a0,color:#311b92
        classDef out fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

        class tree src
        class html,minify,md,text,annotated out

********************************
 Serialization modes and minify
********************************

A :class:`~turbohtml.Minify` is a serialization mode on that same conformant tree, and its design rule is that the
minified bytes must reparse to the same tree: the hard part, a spec-correct parse, is already done, so minifying is only
allowed to drop or fold what the parser reconstructs on the way back in. That gives a single correctness gate:
``minify(parse(minify(parse(src))))`` equals ``minify(parse(src))``, checked across the html5lib-tests corpus and real
pages. Whitespace folds to one space rather than disappearing (a single space reparses in place, so the fold is
idempotent); optional tags are omitted only away from open formatting elements, because the adoption agency would
otherwise reconstruct one across the gap and shift the tree; and a value is unquoted only when no character could end or
re-open it. The transforms that would *not* round-trip (deleting whitespace between block elements, or omitting a tag
whose reparse changes nesting) are exactly the ones turbohtml declines to make.

***************************
 HTML vs XML serialization
***************************

The WHATWG DOM standard defines two ways to turn a node tree into markup: *HTML fragment serialization* and *XML
serialization*. They are different algorithms, not two escaping levels of one, and the same tree lands in both. The
``xml`` field on :class:`~turbohtml.Html` selects the XML one -- lxml's ``method="xml"``, libxml2's serializer, and the
``XMLSerializer`` a browser exposes all target the same form. turbohtml keeps a single tree walk and branches on a flag
at the four points the algorithms diverge, so the HTML fast path is untouched.

The first divergence is empty elements. HTML has a closed list of *void* elements (``br``, ``img``, ``input``, ...) that
take a start tag and never an end tag, and every other empty element still writes ``<div></div>``. XML has no such list:
any element with no children self-closes as ``<div/>`` and a childless ``<br>`` becomes ``<br/>`` for the same reason a
``<div>`` does, not a special case. The second is raw text. HTML copies a ``<script>`` or ``<style>`` body verbatim,
because those elements switch the tokenizer into a raw-text state on the way back in; XML has no raw-text elements, so a
``<`` inside a script escapes like any other text and reparses to the same character. The third is escaping. XML
predefines only ``&amp; &lt; &gt; &quot; &apos;``, so the HTML no-break-space shortcut ``&nbsp;`` -- undefined without a
DTD -- cannot appear; turbohtml writes a literal U+00A0 (the output is Unicode), while the whitespace characters XML
would otherwise normalize away inside an attribute (tab, newline, carriage return) become numeric references so the
value round-trips.

The fourth is namespaces. turbohtml parses HTML, so an inline ``<svg>`` or ``<math>`` lands in the SVG or MathML
namespace inside an otherwise-HTML tree, with no namespace prefixes -- HTML has none. XML has no such implicit
namespaces, so serializing that subtree well-formed means declaring them: the root of a foreign subtree gets the default
``xmlns`` for its namespace, and an element carrying an ``xlink:``-prefixed attribute gets the ``xlink`` prefix declared
on itself. Declaring the prefix on the element that uses it (rather than tracking which ancestor already declared it)
keeps any subtree well-formed on its own, at the cost of a redundant-but-legal redeclaration on a nested user. The
result parses with any XML reader, which is the whole point: an HTML tree becomes input for an XSLT or XPath 2.0
pipeline that would reject HTML's unclosed tags. A :class:`~turbohtml.Minify` layout is inherently HTML (its
optional-tag and unquoted-attribute rules are HTML-parser rules), so it stays HTML even under ``xml=True``.

**********************
 Minifying JavaScript
**********************

Markup minification copies inline ``<script>`` text verbatim, because shrinking JavaScript is a different problem with a
different correctness rule. :func:`~turbohtml.clean.minify_js` is turbohtml's own minifier, a native-C subsystem built
on the same infrastructure as the rest of the library, not a port of any existing tool. It is a real front end: lex,
parse to a flat arena AST, run the size-reducing passes, print back as code. A tokenizer-only minifier of the ``jsmin``
school cannot tell a ``/`` that starts a regular expression from a ``/`` that means division (that needs grammar
position, not just the token stream), so it is *known-incorrect*, and turbohtml's spec-authoritative rule rules it out.
The same lex-parse-optimize-print shape already runs, correctly and fast, in the XPath engine.

The passes split the way ``esbuild`` and ``terser`` split them. Whitespace, comment and number-literal minification is
unconditional; ``mangle`` renames bindings and ``fold`` runs the structural rewrites and dead-code elimination, each
toggled by :class:`~turbohtml.clean.JSMinify`. Renaming is scope-aware: a script's top level is the global scope, whose
names are observable, so only bindings local to a function are renamed, by reference count (the most-used binding in a
scope gets the shortest base-54 name, the frequency model ``esbuild`` uses). The correctness gate is idempotence plus
AST equivalence: the un-mangled output must reparse to the same tree, and ``minify(minify(x))`` must equal
``minify(x)``, checked across the competitors' own conformance corpora rather than a homegrown oracle.

Every transform names the ECMA-262 clause that makes it behavior-preserving; the spec is the authority, not parity with
another tool. ``§`` numbers below link into the `ECMA-262 standard <https://tc39.es/ecma262/>`_.

.. list-table:: JavaScript minification transforms
    :header-rows: 1
    :widths: 30 44 26

    - - transform
      - example
      - spec
    - - whitespace / comment removal, explicit ``;``
      - ``a ;\n b`` to ``a;b``
      - `§12.2 <https://tc39.es/ecma262/#sec-white-space>`_, `§12.10 ASI
        <https://tc39.es/ecma262/#sec-automatic-semicolon-insertion>`_
    - - number canonicalisation
      - ``0x0d`` to ``13``, ``1000`` to ``1e3``, ``0.5`` to ``.5``
      - `§12.9.3 <https://tc39.es/ecma262/#sec-literals-numeric-literals>`_
    - - BigInt separator removal
      - ``1_000n`` to ``1000n``
      - `§12.9.3 <https://tc39.es/ecma262/#sec-literals-numeric-literals>`_
    - - ``true`` / ``false`` to ``!0`` / ``!1``
      - ``x=true`` to ``x=!0``
      - `§13.5.7 <https://tc39.es/ecma262/#sec-logical-not-operator>`_
    - - ``undefined`` to ``void 0`` (unless shadowed)
      - ``x=undefined`` to ``x=void 0``
      - `§13.4.2 <https://tc39.es/ecma262/#sec-void-operator>`_
    - - constant fold / dead-code elimination
      - ``if(0)a;else b`` to ``b``; ``1&&x`` to ``x``
      - `§13.13 <https://tc39.es/ecma262/#sec-binary-logical-operators>`_, `§14.6
        <https://tc39.es/ecma262/#sec-if-statement>`_
    - - member ``a["x"]`` to ``a.x``, key ``{"x":1}`` to ``{x:1}``
      - ``a["x"]`` to ``a.x``
      - `§13.3.2 <https://tc39.es/ecma262/#sec-property-accessors>`_, `§13.2.5
        <https://tc39.es/ecma262/#sec-object-initializer>`_
    - - identifier mangling (locals, nested function/class names, labels)
      - ``function f(){function g(){}}`` to ``function f(){function a(){}}``
      - `§8 <https://tc39.es/ecma262/#sec-syntax-directed-operations-scope-analysis>`_, `§14.13
        <https://tc39.es/ecma262/#sec-labelled-statements>`_
    - - ``if`` to logical / conditional
      - ``if(c)a()`` to ``c&&a()``; ``if(c)a;else b`` to ``c?a:b``
      - `§14.6 <https://tc39.es/ecma262/#sec-if-statement>`_, `§13.14
        <https://tc39.es/ecma262/#sec-conditional-operator>`_
    - - guard clause to conditional return
      - ``if(c)return a;return b`` to ``return c?a:b``
      - `§14.10 <https://tc39.es/ecma262/#sec-return-statement>`_, `§13.14
        <https://tc39.es/ecma262/#sec-conditional-operator>`_
    - - statement to comma sequence
      - ``a();b();return c`` to ``return a(),b(),c``
      - `§13.16 <https://tc39.es/ecma262/#sec-comma-operator>`_
    - - negation flip
      - ``!x?a:b`` to ``x?b:a``; ``!!x?a:b`` to ``x?a:b``
      - `§13.5.7 <https://tc39.es/ecma262/#sec-logical-not-operator>`_, `§13.14
        <https://tc39.es/ecma262/#sec-conditional-operator>`_
    - - declaration merge
      - ``var a=1;var b=2`` to ``var a=1,b=2``
      - `§14.3 <https://tc39.es/ecma262/#sec-let-and-const-declarations>`_

Each transform carries the guard its clause forces: a constant branch drops only when it hoists no ``var`` or function
(`§B.3.3 <https://tc39.es/ecma262/#sec-block-level-function-declarations-web-legacy-compatibility-semantics>`_), a
function declaration renames only in a function scope (a block declaration keeps its Annex-B hoisted twin), ``with`` and
direct ``eval`` (`§19.2.1 <https://tc39.es/ecma262/#sec-eval-x>`_) leave the whole program unrenamed, and a
``__proto__`` data key keeps its quotes. Arithmetic folding (``1+2`` to ``3``) is deliberately omitted: an equivalence
proof across ``-0``, ``NaN``, IEEE-754 rounding and ``valueOf`` coercion is where minifiers ship miscompiles.

Inside HTML the pass is opt-in (:class:`~turbohtml.Minify`'s ``minify_js``) and conservative. A ``<script>`` is
rewritten only when its ``type`` marks it as JavaScript - absent, empty, ``module``, or a WHATWG JavaScript MIME essence
- so a ``type="application/json"`` or ``importmap`` block, which is data that merely resembles code, is never handed to
the JS parser; minifying it as JavaScript could change its quoting or numbers and break it. And a script the parser
cannot handle is emitted byte-for-byte, so a single malformed or not-yet-supported ``<script>`` degrades to verbatim
rather than breaking the surrounding document. The standalone :func:`~turbohtml.clean.minify_js`, by contrast, raises on
such input, because a caller asking to minify one script wants to hear that it could not.

***********************
 Exporting to Markdown
***********************

:meth:`~turbohtml.Node.to_markdown` is a second serializer that walks the same arena tree but emits GitHub-Flavored
Markdown instead of HTML. The survey of the field (the Python ``html2text``, ``markdownify``, and ``inscriptis``, Go's
``html-to-markdown``, and Rust's ``htmd``) converged on one architecture: a recursive visit over a real DOM (not a
streaming parse), with the block context threaded through the recursion rather than re-derived by walking parent
pointers. turbohtml already has the tree, so the exporter is a single pass over it into one growing buffer, classifying
each element as block (its own line, with collapsed blank-line margins) or inline (wrapped in a marker), the CSS
normal-flow distinction.

The one subtle part is whitespace, and it is where the reference libraries differ. turbohtml never emits a space from
text eagerly: a run of whitespace sets a pending flag, and the owed space is written only just before the next visible
character, and dropped at a line or block start. Because a closing emphasis marker does not flush that pending space, a
trailing space inside ``<b>bold </b>`` lands *after* the ``**`` rather than producing the invalid ``**bold **``; because
the opening marker is itself deferred until the first visible character, a leading space moves out the same way. The
common case (a run of plain prose with nothing to escape) is bulk-copied in one ``memcpy`` after its first character,
the borrow-or-copy fast path Rust's ``htmd`` uses.

Three places where the field is inconsistent, turbohtml does the correct thing: an inline code span is fenced with one
more backtick than the longest run inside it (so ``` `a``b` ``` never splits), a ``|`` inside a table cell is escaped,
and a nested ordered list keeps its own counter through the recursion stack rather than a single mutable field that a
naive implementation corrupts on nesting. The output is opinionated GFM with no options, validated both by golden cases
and by rendering it back to HTML with a reference Markdown engine and checking that no visible text was lost.

The walk holds no state outside its stack frame (no module-level buffers, no per-converter object), so two threads
exporting two trees never interfere, and the binding takes the same per-tree critical section
:attr:`~turbohtml.Node.text` and :attr:`~turbohtml.Node.html` use so a concurrent mutation cannot rewire the tree
mid-walk (a no-op under the GIL build). Where Go's ``html-to-markdown`` reaches for a mutex, the stateless visitor needs
none.

Where ``markdownify`` makes extensibility a subclass with a ``convert_<tag>`` method per tag, turbohtml exposes the same
power as a ``converters`` mapping: tag name to ``callable(element, content) -> str``. The C walk checks it only on an
element and only when the mapping is present (one ``NULL`` test on the no-hook path), so the dispatch is free unless a
tag is actually registered. When one matches, the engine renders that element's children into a sub-buffer (sharing the
document's reference-link accumulator), hands the callable a real :class:`~turbohtml.Element` and that inner Markdown,
and splices the result back into the stream with block or inline framing from the tag. The callable runs inside the
walk's critical section, so reading the element is safe; CPython suspends and resumes the section around any reentrant
tree access the callable makes, so it cannot deadlock.

A lighter knob unwraps whole tags without a callable: ``strip`` (a denylist) and ``convert`` (an allowlist), the same
pair ``markdownify`` carries. Both compile to one 256-bit set indexed by the interned tag atom, so the per-element test
is a constant-time bit lookup with no bound check: a stripped element renders its children in place of its own markup.
The interning is what makes a name the tag table does not know fold to no entry, mirroring how ``markdownify`` ignores a
tag it has no converter for.

******************************
 Annotation output processors
******************************

:meth:`~turbohtml.Node.to_annotated_text` walks the tree once and returns the rendered text together with a list of
``(start, end, label)`` spans over its code points. inscriptis pairs that extraction step with a separate set of *output
processors* that turn the spans into a usable artifact, and turbohtml keeps the same split:
:func:`~turbohtml.annotation_surface` and :func:`~turbohtml.annotation_tags` are pure transforms over the ``(text,
spans)`` pair, never the tree. They take no node, no arena, and no shared handle, so unlike the serializers they need no
critical section at all: the input string is immutable and the spans sequence is only read, which makes them
free-threading safe by construction rather than by locking. Keeping extraction (the tree walk) and rendering (the span
transform) apart means one walk can feed several renderings, and the renderings compose with any spans of that shape,
not only the ones :meth:`~turbohtml.Node.to_annotated_text` happens to emit.

The surface extractor is the easy half: bucket each span's ``text[start:end]`` slice under its label, in document order.
The inline-tagged exporter is where nesting has to be handled, because two spans can share a boundary. It expands each
span into an open and a close event and sorts them so the result is always well-formed: at one position a non-zero-width
span closes before any opens, an outer span opens before an inner one and closes after it (the innermost always closes
first), and a zero-width span emits its own ``<label></label>`` intact rather than splitting it across a neighbor's
boundary. The sort key carries the span's other endpoint and its original index, so the order is total and the output
deterministic even when several spans coincide.
