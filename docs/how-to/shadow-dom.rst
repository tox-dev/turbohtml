####################
 Use the Shadow DOM
####################

.. currentmodule:: turbohtml

The Shadow DOM lets an element host a private subtree -- a *shadow tree* -- that composes with the element's own
children through ``<slot>`` elements. turbohtml models the DOM Living Standard tree: :meth:`Element.attach_shadow`,
:class:`ShadowRoot`, slot assignment, and the flattened tree. Every algorithm runs in the C core under the per-tree
critical section.

**********************
 Attach a shadow root
**********************

Call :meth:`Element.attach_shadow` with a mode of ``"open"`` or ``"closed"``. It returns the :class:`ShadowRoot`, a
document-fragment-like container held off the light tree -- it never appears among the host's children or in its
serialization. Populate it with :meth:`ShadowRoot.set_inner_html` or :meth:`ShadowRoot.append`:

.. testcode::

    from turbohtml import Element

    host = Element("my-card")
    root = host.attach_shadow("open")
    root.set_inner_html("<section><slot></slot></section>")
    print(root.mode, root.host.tag)

.. testoutput::

    open my-card

An open shadow root is also reachable through :attr:`Element.shadow_root`; a closed one reads ``None`` there, so only
the reference ``attach_shadow`` returned can reach it. An element can host one shadow root -- a second ``attach_shadow``
raises :class:`ValueError`.

****************
 Fill the slots
****************

A ``<slot>`` in the shadow tree pulls in the host's direct children. An element is assigned to the slot whose ``name``
matches its ``slot`` attribute; the unnamed default slot receives everything else, in order.
:meth:`Element.assigned_nodes` lists what a slot received, and :meth:`Element.assigned_elements` drops the text nodes:

.. testcode::

    from turbohtml import Element, Text

    host = Element("my-card")
    host.append(Element("h2", {"slot": "title"}, [Text("Hello")]))
    host.append(Element("p", None, [Text("body")]))
    host.append(Text("loose"))

    root = host.attach_shadow("open")
    root.set_inner_html('<header><slot name="title"></slot></header><main><slot></slot></main>')

    print([node.tag for node in root.select_one('slot[name="title"]').assigned_nodes()])
    print([type(node).__name__ for node in root.select_one("main slot").assigned_nodes()])
    print([node.tag for node in root.select_one("main slot").assigned_elements()])

.. testoutput::

    ['h2']
    ['Element', 'Text']
    ['p']

Going the other way, :attr:`Node.assigned_slot` gives the slot a light-DOM child landed in (or ``None`` when it is
unassigned or the host's shadow root is closed):

.. testcode::

    print(host.children[0].assigned_slot.attr("name"))
    print(host.children[1].assigned_slot.tag)

.. testoutput::

    title
    slot

The named child lands in the ``title`` slot; the plain paragraph lands in the unnamed default slot. An empty slot falls
back to its own children instead, and ``assigned_nodes(flatten=True)`` returns that fallback (expanding any nested
shadow slots along the way).

*********************************
 Parse a declarative shadow root
*********************************

A server can ship a shadow tree in the markup itself with a ``<template shadowrootmode>``. When :func:`parse` meets one,
it attaches a shadow root to the template's parent and parses the template's content into it, exactly as a browser does
on navigation -- so the shadow tree is there before any script runs. The template element is consumed: it never joins
the light tree. ``shadowrootmode`` is ``"open"`` or ``"closed"``, and ``shadowrootdelegatesfocus`` /
``shadowrootclonable`` set the matching flags:

.. testcode::

    from turbohtml import parse

    doc = parse(
        "<article id=post>"
        '<template shadowrootmode="open" shadowrootclonable>'
        "<h1><slot name=title></slot></h1><slot></slot>"
        "</template>"
        '<span slot="title">Hello</span><p>World</p>'
        "</article>"
    )
    post = doc.find(id="post")
    root = post.shadow_root
    print(root.mode, root.clonable)
    print(root.html)
    print(post.html)

.. testoutput::

    open True
    <h1><slot name="title"></slot></h1><slot></slot>
    <article id="post"><span slot="title">Hello</span><p>World</p></article>

The shadow root's slots compose the host's light children just like a scripted one, so ``assigned_nodes`` and
``flattened_children`` work unchanged. A closed root reads ``None`` from :attr:`Element.shadow_root`, but its content is
still reachable through :attr:`Node.flattened_children`.

Only a valid shadow host (a flow-content element such as ``div``, ``section``, ``span``, or a custom element with a
hyphenated name) takes a declarative root; on any other parent the template stays an ordinary template. Whole-document
:func:`parse` allows declarative shadow roots by default; :func:`parse_fragment` does not -- pass
``allow_declarative_shadow_roots=True`` to opt in, matching the browser's ``setHTMLUnsafe`` rather than ``innerHTML``.

*************************
 Read the flattened tree
*************************

:attr:`Node.flattened_children` returns the composed children a browser would render: a shadow host yields its shadow
tree with each slot replaced by its assigned nodes.

.. testcode::

    top = Element("my-list")
    top.append(Element("li", None, [Text("one")]))
    top.append(Element("li", None, [Text("two")]))
    top.attach_shadow("open").set_inner_html("<ul><slot></slot></ul>")

    ul = top.flattened_children[0]
    print(ul.tag, [node.tag for node in ul.flattened_children])

.. testoutput::

    ul ['li', 'li']

Walk ``flattened_children`` recursively to render the whole composed subtree.

.. seealso::

    :doc:`/explanation/shadow-dom` for the flattened-tree model and :doc:`/migration/jsdom` for the mapping from
    ``attachShadow`` and ``assignedNodes``.
