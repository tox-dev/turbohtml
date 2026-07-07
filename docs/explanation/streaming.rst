#############################
 The streaming rewrite model
#############################

:func:`turbohtml.rewrite.rewrite` transforms a document as the tokenizer produces it, without ever building a tree. It
is the editing cousin of :doc:`the event-driven parse <sax>`: where :mod:`turbohtml.saxparse` lets you *observe* a
stream of constructs, the rewriter lets you *change* them and emits the result as it goes. This note explains the two
properties that shape the API -- the memory bound and the no-lookahead selector constraint -- and why they are linked.

****************************************
 O(open-element depth), not O(document)
****************************************

A full parse holds the whole tree until you are done with it, because a query or an edit may touch any node. The
rewriter holds only the *stack of currently-open elements*: when a start tag streams it pushes a lightweight node whose
sole live link is its parent, and when the matching end tag streams it pops and frees that node. At any instant the
retained state is one entry per element you are currently *inside*, plus whatever a handler has chosen to buffer. So a
flat list of a million siblings costs one stack entry at a time, and a document that nests *d* levels deep costs *d*
entries -- the working set is a function of nesting depth, never of document size.

This is the property that makes the rewriter usable on inputs a full parse cannot afford: a multi-gigabyte log of
records, a response streamed through a proxy, a page far larger than memory. The output is produced incrementally as
each construct is decided, and an untouched construct is copied through verbatim -- character references, attribute
quoting, and whitespace are preserved exactly -- so a rewrite that edits nothing returns its input unchanged.

The one part that can grow is what a handler keeps. ``after`` on an open element buffers its content until the element
closes; a handler that accumulates state across the document keeps that state. The engine bounds only its own footprint.
It also caps the open-element stack at a fixed depth, so a pathologically deep or unclosed input cannot exhaust memory
or the C stack the selector matcher walks.

***********************************
 Selectors match against the spine
***********************************

The open-element stack *is* the context a selector matches against. When an element streams, its ancestors are exactly
the entries below it on the stack, so a selector reads them by walking parent links -- the same native CSS engine that
powers :meth:`~turbohtml.Node.select`, pointed at a spine instead of a whole tree. ``main article > h1`` matches an
``<h1>`` whose parent is an ``<article>`` inside a ``<main>``: every compound to the left of the subject names an
ancestor, and every ancestor is on the stack. Type, universal, id, class, and attribute selectors read the element
itself; ``:is()``, ``:where()``, and ``:not()`` compose those; ``:root`` asks whether the element's parent is the
document. All of it is decidable the instant the start tag arrives.

**********************************
 Why some selectors cannot stream
**********************************

The stack is the past and the present, never the future. A streaming pass has seen an element's ancestors and its
earlier siblings' *tags*, but it has not seen the element's children, its following siblings, or anything after the
current point -- and it cannot rewind to look. Every selector construct that needs that unseen content is therefore
rejected at compile time with :class:`~turbohtml.SelectorSyntaxError`, rather than silently matching wrong:

- **Sibling combinators** (``a + p``, ``h2 ~ p``). The subject's previous siblings have already been popped and freed to
  hold the memory bound, so ``+`` and ``~`` have nothing to reach back to.
- **Positional and structural pseudo-classes** (``:nth-child``, ``:first-of-type``, ``:only-child``, ``:last-child``).
  Deciding an element's position among its siblings needs the *count* of siblings, and the ones after it have not
  streamed yet; ``:last-child`` and ``:only-child`` are unknowable until the parent closes.
- **The emptiness and relational pseudo-classes** (``:empty``, ``:has(...)``). Both ask about descendants -- whether
  there are none, or whether a matching one exists -- which a start tag cannot answer, because the descendants stream
  *after* it.

A full-tree :meth:`~turbohtml.Node.select` supports every one of these precisely because it has the finished tree in
hand: it can count siblings, scan descendants, and look in any direction. That capability is exactly what the streaming
memory bound trades away. When you need one of these selectors, the tree is the right tool: :func:`turbohtml.parse` the
region and query it, or narrow the rewriter to a streamable ancestor selector and do the positional test inside the
handler against state you accumulate yourself.

*******************************
 No rewind, so edits are local
*******************************

The same no-rewind rule shapes what an edit can do. A handler acts when its element's start tag is emitted, so it can
change that tag (attributes, name), inject content at the element's boundaries, or decide to suppress the element's
content and end tag -- all decisions the engine can carry forward. It cannot reach back to bytes already emitted or
forward to bytes not yet produced. Removing or replacing an element works by *suppressing* its content as the inner
tokens stream past, not by deleting something already written; a nested handler inside a removed region does not run,
because its output would be discarded anyway. This keeps every edit a forward, local decision -- the property that lets
the whole transform run in one pass over a stream.
