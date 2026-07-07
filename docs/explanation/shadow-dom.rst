############
 Shadow DOM
############

.. currentmodule:: turbohtml

The Shadow DOM gives an element a second, private subtree that composes with its ordinary children at render time. This
page explains the tree model turbohtml builds for it and the choices behind it. For recipes see
:doc:`/how-to/shadow-dom`; for the API see :doc:`/reference/nodes`.

*********************
 Two trees, one host
*********************

An element that calls :meth:`Element.attach_shadow` gains a **shadow tree** rooted at a :class:`ShadowRoot`. The host's
own children stay where they are -- the DOM calls them the *light tree* -- and the shadow tree lives beside them, not
inside them. That separation is the whole point: the shadow tree is encapsulated, so a document-wide :meth:`Node.select`
or :meth:`Node.find` never descends into it, and serializing the host emits only the light tree. The shadow root is
reachable just two ways: the reference ``attach_shadow`` returns, and -- for an open root only --
:attr:`Element.shadow_root`. A closed root reads ``None`` there, matching the browser rule that closed shadows hide
their internals from the page.

turbohtml holds the shadow root as a document-fragment-like node kept off the light tree entirely: it is never a child
of the host, so every existing walk (queries, traversal, serialization) skips it for free, and a per-tree table records
the host-to-root link in both directions. The shadow root and its content share the host's tree and arena, so building,
querying, and editing a shadow tree take the same C code paths -- and the same critical section -- as the light tree.

**********************
 Slots and assignment
**********************

A shadow tree exposes ``<slot>`` elements as the seams the host's children flow into. Assignment is by name: a light-DOM
child is assigned to the first slot, in tree order, whose ``name`` attribute equals the child's ``slot`` attribute (both
default to the empty string, so the unnamed default slot collects everything unlabeled). turbohtml computes this on
demand rather than caching an assignment on each edit -- :meth:`Element.assigned_nodes`,
:meth:`Element.assigned_elements`, and :attr:`Node.assigned_slot` each run the spec's *find a slot* / *find slotables*
walk when you ask -- so an assignment is always current with the tree, with no observer bookkeeping to keep in sync.

The **flattened tree** is what a browser would actually render: the composed view in which every slot is replaced by the
nodes assigned to it (or, when a slot got nothing, by its own fallback children). :attr:`Node.flattened_children`
produces one level of that view -- a shadow host yields its shadow tree with its slots filled, a slot yields its
assigned (or fallback) nodes, and an ordinary node yields its own children with any child slot expanded. Walk it
recursively to compose a whole subtree. Nested shadow slots forward correctly: a slot whose fallback is another shadow
slot expands through both.

**************************
 Declarative shadow roots
**************************

Shadow trees also arrive in the markup. A ``<template shadowrootmode>`` is *declarative shadow DOM*: the server writes
the shadow tree inline, and the parser -- not a script -- attaches it, so the encapsulation is in place the moment the
document is parsed. turbohtml implements the WHATWG tree-construction steps directly in the C tree builder. When a
``<template>`` start tag carries a ``shadowrootmode`` of ``open`` or ``closed`` and its parent is a valid shadow host,
the builder attaches a shadow root to that parent (reusing the same off-tree shadow node and host table as
:meth:`Element.attach_shadow`) and redirects the template's content into it. The template element is created only on the
stack of open elements, never inserted into the light tree, so it vanishes once its content is parsed -- leaving just
the populated shadow root. ``shadowrootdelegatesfocus`` and ``shadowrootclonable`` set the corresponding flags, readable
as :attr:`ShadowRoot.delegates_focus` and :attr:`ShadowRoot.clonable`.

The spec gates this on a per-document *allow declarative shadow roots* flag, and turbohtml follows it: whole-document
:func:`parse` sets it on, matching a browser navigating to the page, while :func:`parse_fragment` leaves it off,
matching an ``innerHTML`` assignment. The ``allow_declarative_shadow_roots`` argument on each flips that default --
turning it off for a document treats a stray ``shadowrootmode`` as an ordinary template, and turning it on for a
fragment matches the browser's ``setHTMLUnsafe``. Two guards keep the transform faithful: the host must be a valid
shadow host (a flow-content element or a hyphenated custom-element name, the DOM's *valid shadow host name*), and a
parent that already hosts a shadow root keeps its first one, so a second declarative template stays a plain template
rather than replacing it.

****************
 What it is not
****************

This is the tree model, not a rendering or scripting engine. There are no lifecycle callbacks, no ``slotchange`` events,
and no style scoping -- turbohtml has no layout or CSS cascade across the boundary to scope. What you get is the
structural core: attach open and closed roots (imperatively or declaratively from markup), assign named and default
slots, read the assignment both ways, and flatten the composed tree -- enough to build, inspect, and transform
shadow-DOM markup with the same typed, C-backed API as the rest of the tree.
