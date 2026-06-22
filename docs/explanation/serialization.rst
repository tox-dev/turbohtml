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
