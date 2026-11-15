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
quoting, and whitespace are preserved exactly -- so a rewrite that edits nothing returns its input unchanged. The
:doc:`lol-html migration guide </migration/lol-html>` benchmarks this against parse-first peers, running the same edits
through a full parse, mutate, and serialize and reporting each one's peak resident memory -- the tree the streaming pass
never builds.

One part that can grow is what a handler keeps. ``after`` on an open element buffers its content until the element
closes; a handler that accumulates state across the document keeps that state. The engine bounds only its own footprint,
including the tree builder it runs for some markup (see `Foreign content follows the tree builder`_). It also caps the
open-element stack at 512 elements, so a pathologically deep or unclosed input cannot exhaust memory or the C stack the
selector matcher walks. Start tags past the cap, formatting elements included, attach at the capped depth as siblings.

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

One edit writes tags the source never had. Inside ``<svg>`` or ``<math>`` a browser ends the foreign content at an HTML
start tag such as ``<p>``, ``<img>`` or ``<font color>``, and at the end tag of an HTML element around it, with no
``</svg>`` in the source. When a handler removes, replaces or unwraps such a tag, the rewriter writes the end tags it
stood for in its place, so ``<svg><font color=red><title>...`` with the ``<font>`` unwrapped becomes
``<svg></svg><title>...`` and the markup after it is still parsed as the HTML it was.

******************************************
 Foreign content follows the tree builder
******************************************

Three decisions about a tag belong to the tree, not to the tokenizer. ``<title>``, ``<style>``, ``<xmp>`` and the other
raw-text elements switch the tokenizer to text only as HTML elements: inside ``<svg>`` or ``<math>`` the same tags hold
markup. A trailing ``/>`` closes an SVG or MathML element and is ignored on an HTML one, so ``<div/>`` stays open and
``<xmp/>`` still starts raw text. ``<![CDATA[`` opens a section only in foreign content. A full parse answers all three
from its stack of open elements. A CDATA section reaches the text handler as one node, and an untouched one is copied
through verbatim.

The rewriter answers them from the spine while the spine matches that stack: in HTML outside ``<svg>`` and ``<math>``,
and inside them until a tag needs a rule the spine does not keep. Such a tag is one that breaks out of foreign content,
such as ``<p>`` or ``<div>``, an HTML tag inside an integration point such as ``<foreignObject>`` or ``<desc>``, an end
tag that names no open SVG or MathML element, or a ``<template>`` or ``<frameset>`` anywhere. At such a tag the rewriter
runs the tree builder beside it, so each tag is read as a parse reads it, with the end tags a browser implies, the
formatting elements it rebuilds, and the table, template and frameset insertion modes. Once the spine matches the stack
again, outside templates, framesets and integration points, the tree builder waits, and at the next such tag it first
reads the input it missed, so the cost stays linear and a page whose ``<svg>`` and ``<math>`` hold only their own
elements and text, as inline icons and formulas do, never starts it.

The tree builder keeps only what can still change how a later tag is read: the open elements, the active formatting
elements, and the template insertion modes. Whenever the rest of its tree outgrows that part, it copies that part into a
fresh tree and drops the old one, so its memory follows the open-element depth and the formatting list, not the
document.

Handlers can still change how a browser reads what follows. Unwrapping a ``<foreignObject>``, adding ``color`` to a
``<font>`` inside ``<svg>``, changing the ``encoding`` of an ``<annotation-xml>``, or removing an element that decided
what a later end tag closes can turn the raw text after it into markup or back. Such an edit is one that drops or
replaces tags, writes around an element or comment or into an element that is not raw text, edits text outside raw text
or the doctype, or changes an attribute a parse reads: the ``type`` of an ``<input>``, or a ``<font>`` or
``<annotation-xml>`` in foreign content. After one, the rewriter runs a second tree builder over its own output,
starting at the edit when it is inside ``<svg>`` or ``<math>``, or else at the next ``<svg>``, ``<math>``,
``<template>`` or ``<frameset>``, since only there can the difference change how a token is read. From then on it writes
each token so that the output reads it as the source did: it ends raw text only the output opened, escapes raw text the
output would read as markup, turns a CDATA section into text or a ``<![CDATA[`` bogus comment into a comment where the
two disagree, and ends an SVG or MathML element that an end tag closed in the source but leaves open in the output. A
rewrite with no such edit, or with one and no foreign content, template or frameset after it, never starts the second
tree builder and writes what it always wrote.

Text that ends in ``<``, or inside ``<title>`` or ``<textarea>`` in an end tag cut short, reads as text because of what
follows it in the source. When a handler drops or changes what follows, the rewriter escapes that ``<`` if the next
thing it writes would turn it into a tag, a comment or an end tag; otherwise it leaves the ``<`` as written.

A guarantee that has to hold against hostile markup, such as no script surviving, still belongs to
:func:`turbohtml.clean.sanitize`, which works on the parsed tree.
