##################
 Source locations
##################

.. currentmodule:: turbohtml

Every parsed element already remembers where its start tag began -- :attr:`Node.source_line` and
:attr:`Node.source_col`, the (1-based line, 0-based column) pair a diagnostic points back at. That is enough to say
*where* an element is, but not enough to *slice* it: a formatter rewriting an attribute, a linter underlining a tag, or
a source-mapping tool needs the exact extent of the start tag, the end tag, and each attribute.
``source_locations=True`` adds that, the model parse5 calls ``sourceCodeLocationInfo``.

*****************************
 Why it is a separate opt-in
*****************************

Position tracking is on by default because it costs two words per element and nothing else. The granular spans cost more
-- a record per element, a span per attribute, and the tokenizer bookkeeping to stamp them -- so they stay off unless
asked for. The design keeps the common path untouched: with ``source_locations`` off the tokenizer takes no extra work
and the element node reserves no extra slot, so a parse that does not want spans pays for none of them. ``parse`` still
defaults ``positions=True``; ``source_locations`` layers on top and, because a span subsumes a line and column, implies
it (asking for spans with ``positions=False`` still tracks positions).

This mirrors parse5, where ``sourceCodeLocationInfo`` is a separate option off by default for the same reason, rather
than folding the cost into every parse.

*******************
 What a span means
*******************

A :class:`SourceSpan` is six numbers: the start and end line, the start and end column, and the start and end code-point
offset. The offsets are the useful part -- ``source[start_offset:end_offset]`` slices the construct straight out of the
input -- and the line/column pairs address the same two endpoints for a human-facing report. Offsets and columns count
code points into the *newline-normalized* source, the same convention :attr:`Node.source_line` uses: a ``\r\n``
collapses to one line break and counts once, so the numbers match what the tokenizer and the parse-error positions
report rather than raw byte offsets.

A :class:`SourceLocation` gathers the spans of one element: its ``start_tag``, its ``end_tag`` (``None`` when the source
never closed it -- a void element, a self-closed one, or one closed implicitly or at end of input), and an ``attrs`` map
from each attribute name to the span of its whole ``name="value"``. A duplicate attribute keeps the first occurrence's
span, matching the tokenizer's first-wins recovery, so the map has one entry per name the element actually carries.

**********************
 Where the work lives
**********************

The heavy lifting is in the C core, not the Python layer. The tokenizer already tracks a running line, column, and
offset as it consumes the input; with locations on it stamps those counters at the grammar boundaries -- the first code
point of each attribute name, the delimiter that ends each value, and the ``>`` that closes each tag -- onto the token.
The tree builder copies the stamps into an arena-allocated record hung off the element, and reads the matching end-tag
span when the source closes the element. The Python side is only the :class:`SourceLocation` and :class:`SourceSpan`
record types the core fills. Because the stamping sits behind a single flag the tokenizer checks once per boundary,
turning the feature off restores the original hot path exactly.
