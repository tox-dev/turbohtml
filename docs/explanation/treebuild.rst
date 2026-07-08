########################
 Retargeting the parser
########################

:func:`turbohtml.treebuild.parse_into` runs the ordinary WHATWG tree builder and, instead of wrapping the result in a
:class:`~turbohtml.Document`, drives a *builder* object you supply to construct the tree in a representation of your
own. It is the pluggable tree-building surface Rust's html5ever exposes as its ``TreeSink`` trait and Node's parse5 as
its ``TreeAdapter``. This note explains where it sits among turbohtml's parse surfaces and what the pass does and does
not save.

***********************************
 A tree you build, not one you get
***********************************

turbohtml already offers two DOM-less parse surfaces. :func:`turbohtml.tokenize` hands back raw tokens that know nothing
about tree structure. :mod:`turbohtml.saxparse` hands back the *events* of the constructed tree -- start tag, end tag,
characters -- but no way to hold structure across them. :func:`~turbohtml.treebuild.parse_into` is the third: it hands
each constructed node to your builder with its parent, so you can assemble a tree, and the parser never materializes its
own navigable node graph on the way.

The distinction from SAX matters when the target *is* a tree. With events you rebuild the parent chain yourself -- a
stack you push on every start and pop on every end. With a builder the parent arrives as an argument: ``append(parent,
child)`` is called with the handle you returned for the enclosing node, so nesting is handed to you rather than
reconstructed. When the target is a flat summary (a count, a set of links) the event stream is the simpler tool; when it
is a shaped structure (an index keyed by ancestry, a diff tree, a foreign library's nodes) the builder removes the
bookkeeping.

*************************************
 What the single pass actually saves
*************************************

The capability is *skip generic DOM materialization and a second walk*. Both halves are real, and each removes a
distinct cost.

The materialization saved is the Python object graph. :func:`turbohtml.parse` returns a document backed by the C tree,
but every :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and :class:`~turbohtml.Comment` you touch is a Python
wrapper realized over it. A pipeline that parses only to walk the tree once and extract a projection pays for a
document-sized graph of wrappers it immediately discards. :func:`~turbohtml.treebuild.parse_into` creates none of them:
the walk reads the C nodes directly and calls your builder, so the only objects that survive are the ones your builder
chooses to keep.

The second walk saved is your own. The classic shape is parse, then traverse the returned tree to fill an index or a
transformed structure. Here the traversal *is* the construction: the parser's single document-order pass over its tree
drives your builder, so the index is populated as the parse completes rather than by a follow-up descent.

What is *not* saved is the parser's own working tree. turbohtml builds its compact, arena-allocated C tree first -- that
tree is the parser's scratch, cheap and freed the moment the pass ends -- and then walks it once into your builder. The
alternative, driving the builder live during construction the way html5ever does, is not what this does: the WHATWG
algorithm reaches backwards (the adoption agency relocates already-built subtrees, adjacent text coalesces, foster
parenting merges fostered text before a table), so a live sink must expose handle queries and re-parenting the way
html5ever's trait does. turbohtml keeps that machinery on its own fast C nodes and forwards the finished tree, which
keeps the conformant parser exactly as it is and the builder API small.

****************************
 Faithful to the built tree
****************************

Because the calls come from the same C tree builder :func:`turbohtml.parse` uses, everything the standard error-recovery
rules produce is reflected: the implied ``<html>``/``<head>``/``<body>``, auto-closed paragraphs and list items, foster
parenting out of tables, and the adoption agency's re-nesting of misplaced formatting elements. A ``<template>``'s
content fragment is flattened -- its children are appended under the template handle -- matching how the SAX walk
streams it. And a bogus ``<?...>`` construct, which WHATWG HTML parses as a comment, reaches a distinct ``create_pi``
method rather than ``create_comment``, the one place the builder is finer-grained than the parsed
:class:`~turbohtml.Document` (which wraps it as a :class:`~turbohtml.Comment`), matching the SAX stream's
:class:`~turbohtml.saxparse.ProcessingInstruction`.
