############
 From jsdom
############

.. package-meta:: npm jsdom jsdom/jsdom

`jsdom <https://github.com/jsdom/jsdom>`_ is a JavaScript implementation of the WHATWG DOM and HTML standards, used to
run browser-shaped code under Node. Its ``document.createTreeWalker`` and ``document.createNodeIterator`` are the DOM
Living Standard traversal objects: a movable cursor and a flat filtered view over a subtree, each driven by a
``whatToShow`` bitmask and a ``NodeFilter`` callback.

turbohtml ships those same two objects for Python, built on the same spec, so a scraper or transform ported from jsdom
keeps its traversal logic. The surface is the DOM's, respelled to turbohtml's conventions: methods are snake_case
(``next_node``, ``current_node``), the objects are constructed directly rather than through a ``document`` factory, and
the filter is a plain callable returning a :class:`turbohtml.NodeFilter` verdict. The state machine and the
``whatToShow`` test run in turbohtml's C core; the filter callback is the one step that calls back into Python.

This guide covers the traversal, range, and Shadow DOM APIs. The rest of the DOM turbohtml exposes -- parsing, the node
model, queries, mutation, serialization -- is in :doc:`/reference/nodes` and the :doc:`beautifulsoup` guide.

********************
 turbohtml vs jsdom
********************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - jsdom
    - - Language
      - Python, C-accelerated
      - JavaScript (Node)
    - - Construct a walker
      - ``TreeWalker(root, what_to_show, filter)``
      - ``document.createTreeWalker(root, whatToShow, filter)``
    - - Construct an iterator
      - ``NodeIterator(root, what_to_show, filter)``
      - ``document.createNodeIterator(root, whatToShow, filter)``
    - - Filter
      - a callable ``node -> int``
      - a function or ``{ acceptNode }`` object
    - - Method names
      - snake_case (``next_node``, ``parent_node``)
      - camelCase (``nextNode``, ``parentNode``)
    - - Constants
      - :class:`turbohtml.NodeFilter` attributes
      - ``NodeFilter`` constants

********************
 Map the vocabulary
********************

The constructor arguments line up one-to-one. jsdom's ``document.createTreeWalker(root, whatToShow, filter)`` becomes
``TreeWalker(root, what_to_show, filter)``, with ``what_to_show`` defaulting to :attr:`~turbohtml.NodeFilter.SHOW_ALL`
and ``filter`` to ``None``:

.. testcode::

    import turbohtml
    from turbohtml import NodeFilter, TreeWalker

    doc = turbohtml.parse("<main><h1>Title</h1><p>Body <a href='/x'>link</a></p></main>")
    walker = TreeWalker(doc.find("main"), NodeFilter.SHOW_ELEMENT)
    print(walker.first_child())
    print(walker.next_node())
    print(walker.next_node())

.. testoutput::

    Element('h1')
    Element('p')
    Element('a')

The ``whatToShow`` bits carry over unchanged -- ``NodeFilter.SHOW_ELEMENT``, ``SHOW_TEXT``, ``SHOW_COMMENT``, and the
rest keep their DOM values -- so a mask ported verbatim selects the same node types.

*******************
 Port a NodeFilter
*******************

In jsdom a filter is a function (or an object with an ``acceptNode`` method) returning ``NodeFilter.FILTER_ACCEPT``,
``FILTER_REJECT``, or ``FILTER_SKIP``. In turbohtml it is a plain callable taking the node and returning the same
verdict off :class:`turbohtml.NodeFilter`. The reject/skip distinction is the spec's and turbohtml honors it exactly:
``FILTER_REJECT`` drops a node and its entire subtree, while ``FILTER_SKIP`` drops only the node:

.. testcode::

    page = turbohtml.parse("<section><figure><img></figure><p>keep</p></section>").find("section")


    def drop_figures(node):
        if node.tag == "figure":
            return NodeFilter.FILTER_REJECT
        return NodeFilter.FILTER_ACCEPT


    walker = TreeWalker(page, NodeFilter.SHOW_ELEMENT, drop_figures)
    print([node.tag for node in iter(walker.next_node, None)])

.. testoutput::

    ['p']

Swap ``FILTER_REJECT`` for ``FILTER_SKIP`` and the ``<img>`` inside the figure reappears, because a skip keeps the
subtree; that mirrors jsdom, and is why a :class:`~turbohtml.NodeIterator` -- which has no subtree to prune -- treats
the two verdicts alike.

***********************
 NodeIterator and iter
***********************

``document.createNodeIterator`` becomes :class:`turbohtml.NodeIterator`. Its ``nextNode``/``previousNode`` become
``next_node``/``previous_node``, and because it is a Python iterator you can also drop it straight into a ``for`` loop
for the forward walk:

.. testcode::

    from turbohtml import NodeIterator

    iterator = NodeIterator(doc.find("main"), NodeFilter.SHOW_ELEMENT)
    print([node.tag for node in iterator])

.. testoutput::

    ['main', 'h1', 'p', 'a']

``referenceNode`` and ``pointerBeforeReferenceNode`` are exposed as :attr:`~turbohtml.NodeIterator.reference_node` and
:attr:`~turbohtml.NodeIterator.pointer_before_reference_node`, so code that inspects the iterator's position ports
directly. jsdom's legacy no-op ``detach()`` has no counterpart; drop the call.

*******************
 What is different
*******************

turbohtml keeps ``current_node`` assignable, as in the DOM, but restricts the assigned node to the walker's own tree, so
the cursor can never dangle into a detached document. A filter that re-enters the walker (calls a traversal method from
inside ``acceptNode``) raises :class:`ValueError`, the Python spelling of the DOM's ``InvalidStateError``. And there is
no ``document.createTreeWalker`` factory: construct :class:`~turbohtml.TreeWalker` and :class:`~turbohtml.NodeIterator`
directly from the root node.

****************************
 Ranges: turbohtml vs jsdom
****************************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - jsdom
    - - Language
      - Python, over a C engine
      - JavaScript (Node)
    - - Construction
      - ``Range(container, offset=0)`` collapsed at a node
      - ``new Range()`` collapsed at the document, or ``document.createRange()``
    - - Boundary offsets
      - Code points (aligns with Python string indexing)
      - UTF-16 code units
    - - Liveness
      - The range's own content operations move its boundaries; other edits do not
      - Fully live: any tree mutation shifts open ranges
    - - Fragments
      - :meth:`~turbohtml.Range.extract_contents` / :meth:`~turbohtml.Range.clone_contents` return a fragment node
      - Return a ``DocumentFragment``
    - - Compare modes
      - ``Range.START_TO_START`` and friends, as class constants
      - ``Range.START_TO_START`` and friends, as constants

The one behavioral difference to plan around is liveness. jsdom keeps every open range in sync as the tree changes;
turbohtml moves a range's boundaries only through the range's own content operations, so a range is a cursor you drive
rather than an observer that follows edits made elsewhere. Drive each range to completion before mutating the same
region another way.

****************
 Boundary setup
****************

The boundary setters map one to one. jsdom:

.. code-block:: javascript

    const range = document.createRange();
    range.setStart(container, 0);
    range.setEnd(container, 2);
    range.selectNodeContents(node);
    range.collapse(true);

turbohtml:

.. testcode::

    from turbohtml import Range

    doc = turbohtml.parse("<ul><li>a</li><li>b</li><li>c</li></ul>")
    ul = doc.find("ul")
    span = Range(ul, 0)
    span.set_start(ul, 0)
    span.set_end(ul, 2)
    print(span.start_offset, span.end_offset, span.collapsed)

.. testoutput::

    0 2 False

********************
 Content operations
********************

The extract, clone, delete, insert, and surround operations keep their meaning; only the spelling changes.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - jsdom
      - turbohtml
    - - ``range.startContainer`` / ``range.startOffset``
      - :attr:`~turbohtml.Range.start_container` / :attr:`~turbohtml.Range.start_offset`
    - - ``range.collapsed`` / ``range.commonAncestorContainer``
      - :attr:`~turbohtml.Range.collapsed` / :attr:`~turbohtml.Range.common_ancestor_container`
    - - ``range.setStartBefore(node)`` / ``range.setEndAfter(node)``
      - :meth:`~turbohtml.Range.set_start_before` / :meth:`~turbohtml.Range.set_end_after`
    - - ``range.selectNode(node)`` / ``range.selectNodeContents(node)``
      - :meth:`~turbohtml.Range.select_node` / :meth:`~turbohtml.Range.select_node_contents`
    - - ``range.compareBoundaryPoints(how, other)``
      - :meth:`~turbohtml.Range.compare_boundary_points`
    - - ``range.comparePoint(node, offset)`` / ``range.isPointInRange(node, offset)``
      - :meth:`~turbohtml.Range.compare_point` / :meth:`~turbohtml.Range.is_point_in_range`
    - - ``range.intersectsNode(node)``
      - :meth:`~turbohtml.Range.intersects_node`
    - - ``range.cloneContents()`` / ``range.extractContents()`` / ``range.deleteContents()``
      - :meth:`~turbohtml.Range.clone_contents` / :meth:`~turbohtml.Range.extract_contents` /
        :meth:`~turbohtml.Range.delete_contents`
    - - ``range.insertNode(node)`` / ``range.surroundContents(parent)``
      - :meth:`~turbohtml.Range.insert_node` / :meth:`~turbohtml.Range.surround_contents`
    - - ``range.cloneRange()``
      - :meth:`~turbohtml.Range.clone_range`

Extracting a run of siblings reads the same in both. jsdom returns a ``DocumentFragment``; turbohtml returns a fragment
node whose ``children`` are the moved nodes:

.. testcode::

    from turbohtml import Element

    doc = turbohtml.parse("<p>one two three</p>")
    text = doc.find("p").children[0]
    span = Range(text, 4)
    span.set_end(text, 7)
    span.surround_contents(Element("em"))
    print(doc.find("p").html)

.. testoutput::

    <p>one <em>two</em> three</p>

*************
 StaticRange
*************

jsdom's ``new StaticRange({startContainer, startOffset, endContainer, endOffset})`` init dict becomes positional
arguments; the immutable snapshot and its :attr:`~turbohtml.StaticRange.collapsed` flag are otherwise identical.

.. testcode::

    from turbohtml import StaticRange

    box = doc.find("p")
    snapshot = StaticRange(box, 0, box, len(box.children))
    print(snapshot.start_offset, snapshot.end_offset, snapshot.collapsed)

.. testoutput::

    0 3 False

********************************
 Shadow DOM: turbohtml vs jsdom
********************************

jsdom implements the DOM shadow-tree model -- ``element.attachShadow({ mode })``, ``ShadowRoot``, ``<slot>`` assignment,
and ``assignedNodes`` / ``assignedElements`` / ``assignedSlot``. turbohtml ships the same model for Python, respelled to
its conventions: ``attachShadow`` takes the mode as a plain string, the accessors are snake_case, and the assignment
runs in the C core on demand rather than through the browser's microtask bookkeeping.

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - jsdom
    - - Attach
      - ``element.attach_shadow("open")``
      - ``element.attachShadow({ mode: "open" })``
    - - Open root accessor
      - :attr:`~turbohtml.Element.shadow_root` (``None`` when closed)
      - ``element.shadowRoot`` (``null`` when closed)
    - - Root properties
      - :attr:`~turbohtml.ShadowRoot.mode` / :attr:`~turbohtml.ShadowRoot.host`
      - ``root.mode`` / ``root.host``
    - - Fill the shadow tree
      - :meth:`~turbohtml.ShadowRoot.set_inner_html` / :meth:`~turbohtml.ShadowRoot.append`
      - ``root.innerHTML = ...`` / ``root.append(...)``
    - - A slot's assignment
      - :meth:`~turbohtml.Element.assigned_nodes` / :meth:`~turbohtml.Element.assigned_elements`
      - ``slot.assignedNodes()`` / ``slot.assignedElements()``
    - - A child's slot
      - :attr:`~turbohtml.Node.assigned_slot`
      - ``node.assignedSlot``
    - - Flattened tree
      - :attr:`~turbohtml.Node.flattened_children`
      - (no direct property; assemble from ``assignedNodes({ flatten: true })``)

The one call that reshapes is ``attachShadow``: jsdom takes an init dictionary, turbohtml takes the mode string
directly. Everything else maps name-for-name.

.. testcode::

    from turbohtml import Element, Text

    host = Element("my-card")
    host.append(Element("h2", {"slot": "title"}, [Text("Hi")]))
    host.append(Element("p", None, [Text("body")]))

    root = host.attach_shadow("open")
    root.set_inner_html('<slot name="title"></slot><slot></slot>')

    print([node.tag for node in root.select_one('slot[name="title"]').assigned_nodes()])
    print([node.tag for node in host.flattened_children])

.. testoutput::

    ['h2']
    ['h2', 'p']

turbohtml computes ``assignedNodes`` lazily, so there is no ``slotchange`` event to listen for and no observer to
detach; ask for the assignment and it reflects the tree as it stands. The flattened tree is a property
(:attr:`~turbohtml.Node.flattened_children`) rather than something you assemble by hand: it returns the composed
children with each slot already replaced by what it received, which you walk recursively for the whole subtree.

****************************************
 Mutation observers: turbohtml vs jsdom
****************************************

jsdom implements the DOM ``MutationObserver`` faithfully, microtask timing and all: ``observer.observe(target,
options)`` queues records as the tree changes, and the callback fires later, on a microtask, once the current script
settles. turbohtml records the same changes with the same record shape but delivers them synchronously, because a
library has no event loop to drain a microtask queue. Instead of a callback that fires on its own, you pull the batch
with :meth:`~turbohtml.MutationObserver.take_records`, or push it to the callback yourself with
:meth:`~turbohtml.MutationObserver.deliver`.

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - jsdom
    - - Construct
      - ``MutationObserver(callback=None)`` (callback optional)
      - ``new MutationObserver(callback)`` (callback required)
    - - Register
      - ``observer.observe(target, child_list=True, subtree=True, ...)`` keyword options
      - ``observer.observe(target, { childList: true, subtree: true, ... })`` init dict
    - - Old-value / filter options
      - ``attribute_old_value`` / ``character_data_old_value`` / ``attribute_filter``
      - ``attributeOldValue`` / ``characterDataOldValue`` / ``attributeFilter``
    - - Delivery
      - Synchronous: :meth:`~turbohtml.MutationObserver.take_records`, or :meth:`~turbohtml.MutationObserver.deliver` to
        call the callback
      - Asynchronous: the callback runs on a microtask; ``takeRecords()`` drains early
    - - Record fields
      - ``type`` / ``target`` / ``added_nodes`` / ``removed_nodes`` / ``previous_sibling`` / ``next_sibling`` /
        ``attribute_name`` / ``old_value``
      - ``type`` / ``target`` / ``addedNodes`` / ``removedNodes`` / ``previousSibling`` / ``nextSibling`` /
        ``attributeName`` / ``oldValue``

The one behavioral difference to plan around is timing. jsdom's callback fires by itself, after your code returns;
turbohtml's does not fire until you drain the queue, so there is no window where a half-finished edit is visible to an
observer. jsdom:

.. code-block:: javascript

    const observer = new MutationObserver(records => {
      console.log(records[0].type, records[0].addedNodes.length);
    });
    observer.observe(list, { childList: true });
    list.append(document.createElement("li"));
    // callback runs later, on a microtask

turbohtml:

.. testcode::

    from turbohtml import Element, MutationObserver

    doc = turbohtml.parse("<ul></ul>")
    listing = doc.find("ul")
    observer = MutationObserver()
    observer.observe(listing, child_list=True)
    listing.append(Element("li"))
    (record,) = observer.take_records()  # ready now, not on a later microtask
    print(record.type, len(record.added_nodes))

.. testoutput::

    childList 1

.. seealso::

    :doc:`/how-to/ranges`, :doc:`/how-to/shadow-dom`, and :doc:`/how-to/observing-mutations` for task-focused recipes,
    and :doc:`/explanation/ranges`, :doc:`/explanation/shadow-dom`, and :doc:`/explanation/mutation` for the
    boundary-point, flattened-tree, and synchronous-observation models.
