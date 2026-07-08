################################
 Parsing a document into a tree
################################

A token stream is flat. To see which element contains which, you need the *structure*: a tree. Go from a string of HTML
to a navigable tree of nodes.

.. important::

    The one rule worth learning first: turbohtml models text as real **child nodes** (the WHATWG DOM shape), not `lxml
    <https://lxml.de>`_'s ``text``/``tail`` or `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_'s
    ``.string``. So ``node[i]`` indexes a node's children, and attributes are reached through ``node.attrs``, never
    ``node["attr"]``.

Hand a whole document to :func:`turbohtml.parse`. It applies the full WHATWG tree-construction algorithm (the same one
browsers run, including the error recovery that inserts the missing ``html``, ``head`` and ``body``) and returns a
:class:`turbohtml.Document`:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<h1>Hello</h1><p>Tom &amp; <a href='/x'>Jerry</a></p>")
    print(doc.root)

.. testoutput::

    Element('html')

The recovery is not silent: each WHATWG parse error turbohtml recovered from is on :attr:`~turbohtml.Document.errors`, a
list of :class:`~turbohtml.ParseError` with the spec ``code`` and source position. A clean document leaves it empty;
malformed input fills it (and ``parse(..., strict=True)`` raises :class:`~turbohtml.HTMLParseError` on the first one):

.. testcode::

    print(doc.errors)
    print(turbohtml.parse("<a b b>").errors[0].code)

.. testoutput::

    []
    duplicate-attribute

By default turbohtml parses ``<noscript>`` content as markup, so you can walk into it. A scripting browser treats that
content as raw text instead. Pass ``scripting=True`` to build that tree, where the inner tags become one text run:

.. testcode::

    noscript = turbohtml.parse("<noscript><a href='/no-js'>plain</a></noscript>", scripting=True).find("noscript")
    print(noscript.text)

.. testoutput::

    <a href='/no-js'>plain</a>

:meth:`~turbohtml.Node.find` returns the first descendant matching a tag (and any attributes you pass), or ``None``:

.. testcode::

    print(doc.find("a"))
    print(doc.find("a").attrs)

.. testoutput::

    Element('a')
    {'href': '/x'}

Every node exposes its text and its markup. :attr:`~turbohtml.Node.text` is the concatenated character data of the
subtree, with references decoded; :attr:`~turbohtml.Node.html` re-serializes the subtree:

.. testcode::

    paragraph = doc.find("p")
    print(paragraph.text)
    print(paragraph.html)

.. testoutput::

    Tom & Jerry
    <p>Tom &amp; <a href="/x">Jerry</a></p>

turbohtml models text as real child nodes (the WHATWG DOM shape), so a paragraph's children are its text runs and its
elements interleaved, in order. A node is a sequence of its children: iterate it, take its length, index into it:

.. testcode::

    print(list(paragraph))
    print(len(paragraph))
    print(paragraph[1])

.. testoutput::

    [Text('Tom & '), Element('a')]
    2
    Element('a')

From any node you can walk outward as well as inward: :attr:`~turbohtml.Node.parent`,
:attr:`~turbohtml.Node.next_sibling`, and the lazy :attr:`~turbohtml.Node.ancestors` and
:attr:`~turbohtml.Node.descendants` iterators:

.. testcode::

    link = doc.find("a")
    print(link.parent)
    print([node.tag for node in link.ancestors if isinstance(node, turbohtml.Element)])

.. testoutput::

    Element('p')
    ['p', 'body', 'html']

For richer queries, :meth:`~turbohtml.Node.select` takes a CSS selector and returns every matching descendant in
document order. The negation pseudo-class ``:not()`` keeps the elements that match none of its arguments; here, the
descendants of ``body`` that are not links:

.. testcode::

    print([node.tag for node in doc.select("body :not(a)")])

.. testoutput::

    ['h1', 'p']

Selectors also reach the form and UI pseudo-classes the markup determines, such as ``:checked`` for a checked control:

.. testcode::

    form = turbohtml.parse("<input type=checkbox checked><input type=checkbox>")
    print(len(form.select(":checked")))

.. testoutput::

    1

``:is()`` and ``:where()`` are forgiving, so an arm they cannot parse is dropped and the rest still select; a typo in
one alternative does not break the query:

.. testcode::

    print([node.tag for node in doc.select(":is(h1, :oops)")])

.. testoutput::

    ['h1']

Structural pseudo-classes count positions, and ``:nth-child(An+B of S)`` counts only the siblings matching ``S``; here
the first checked box, ignoring the unchecked ones in between:

.. testcode::

    boxes = turbohtml.parse("<p><input checked><input><input checked></p>")
    print([e.attrs.get("checked") for e in boxes.select("input:nth-child(1 of [checked])")])

.. testoutput::

    ['']

If you are coming from pyquery's jQuery-style chaining, :class:`turbohtml.query.Query` wraps these primitives in a
fluent, chainable surface where each call returns a new wrapper.

Because the node types are a sealed hierarchy, structural pattern matching works: each subtype unpacks its defining
field:

.. testcode::

    for node in paragraph:
        match node:
            case turbohtml.Element(tag):
                print("element", tag)
            case turbohtml.Text(data):
                print("text", repr(data))

.. testoutput::

    text 'Tom & '
    element a

******************
 Query with XPath
******************

When you are porting a scraper written against ``lxml`` or ``elementpath``, :meth:`~turbohtml.Node.xpath` runs the
expression as-is. Beyond the XPath 1.0 core it also answers the string subset of XPath 2.0, so ``upper-case`` folds a
result's case and ``ends-with`` filters inside a predicate:

.. testcode::

    print(doc.xpath("upper-case(//a)"))
    print([a.attrs["href"] for a in doc.xpath("//a[ends-with(@href, '/x')]")])

.. testoutput::

    JERRY
    ['/x']

*******************
 Scrape every link
*******************

Those primitives compose into the first job most scraping scripts need: collect every link on the page with its text and
target. :meth:`~turbohtml.Node.find_all` returns all matching descendants in document order, so one comprehension over
the parsed tree gives you a table of anchors:

.. testcode::

    page = turbohtml.parse(
        "<nav><a href='/'>Home</a><a href='/about'>About</a></nav>"
        "<article><a href='https://example.com'>Example</a></article>"
    )
    for anchor in page.find_all("a"):
        print(anchor.text, "->", anchor.attrs["href"])

.. testoutput::

    Home -> /
    About -> /about
    Example -> https://example.com

************************
 Chain queries fluently
************************

When you would rather chain than write a comprehension, wrap the same tree in :class:`turbohtml.query.Query`. Each call
narrows the selection and returns a new wrapper, so you reach the link's text and target without an intermediate
variable:

.. testcode::

    from turbohtml.query import Query

    anchors = Query(doc).find("a")
    print(anchors.text())
    print(anchors.attr("href"))

.. testoutput::

    Jerry
    /x

*****************************
 Walk with a filtered cursor
*****************************

The :attr:`~turbohtml.Node.descendants` iterator visits every node once, front to back. When you want a cursor you can
steer -- step to a child, a sibling, or back to the parent, and point at a filtered subset -- reach for the DOM
:class:`turbohtml.TreeWalker`. Its ``what_to_show`` bitmask picks node types (here
:attr:`~turbohtml.NodeFilter.SHOW_ELEMENT`, so text and comments are stepped over), and each move returns the node it
landed on:

.. testcode::

    from turbohtml import NodeFilter, TreeWalker

    walker = TreeWalker(paragraph, NodeFilter.SHOW_ELEMENT)
    print(walker.first_child())
    print(walker.next_node())

.. testoutput::

    Element('a')
    None

A ``filter`` callback refines the walk further. It returns one of three verdicts, and the difference between two of them
is what makes it a *tree* walker: :attr:`~turbohtml.NodeFilter.FILTER_REJECT` drops a node **and its subtree**, while
:attr:`~turbohtml.NodeFilter.FILTER_SKIP` drops only the node, so the walk still descends into its children:

.. testcode::

    tree = turbohtml.parse("<section><aside><b>x</b></aside><i>y</i></section>").find("section")


    def hide_aside(verdict):
        return lambda node: verdict if node.tag == "aside" else NodeFilter.FILTER_ACCEPT


    skip = TreeWalker(tree, NodeFilter.SHOW_ELEMENT, hide_aside(NodeFilter.FILTER_SKIP))
    reject = TreeWalker(tree, NodeFilter.SHOW_ELEMENT, hide_aside(NodeFilter.FILTER_REJECT))
    print([node.tag for node in iter(skip.next_node, None)])
    print([node.tag for node in iter(reject.next_node, None)])

.. testoutput::

    ['b', 'i']
    ['i']

For a flat, front-to-back view of the same filtered nodes, :class:`turbohtml.NodeIterator` iterates directly; it has no
subtree to prune, so it treats reject and skip alike. See :doc:`/how-to/traversing` for the full cursor and
:doc:`/explanation/traversal` for why the reject/skip split matters.

***************************
 Copy a slice with a Range
***************************

To grab a run of siblings rather than a single node, mark it with a :class:`turbohtml.Range`. A range holds two boundary
points -- each a ``(container, offset)`` pair -- and :meth:`~turbohtml.Range.clone_contents` returns a fragment copying
everything between them, leaving the tree untouched. Span the paragraph's second child (the link) and copy it:

.. testcode::

    from turbohtml import Range

    para = doc.find("p")
    span = Range(para, 1)
    span.set_end(para, 2)
    print(span.clone_contents().html)

.. testoutput::

    <a href="/x">Jerry</a>

***********************************
 Compose a slot with a shadow root
***********************************

An element can host a *shadow tree*: a private subtree, kept off to the side, whose ``<slot>`` elements pull in the
host's own children. :meth:`~turbohtml.Element.attach_shadow` returns the :class:`turbohtml.ShadowRoot`; fill it with
slots, and each light-DOM child is assigned to the slot whose name matches its ``slot`` attribute, with the unnamed
default slot taking the rest. :attr:`~turbohtml.Node.flattened_children` then shows the composed result -- the shadow
tree with every slot replaced by what it received:

.. testcode::

    from turbohtml import Element, Text

    card = Element("article")
    card.append(Element("h2", {"slot": "title"}, [Text("Shadow DOM")]))
    card.append(Element("p", None, [Text("Composed at render time.")]))

    root = card.attach_shadow("open")
    root.set_inner_html('<header><slot name="title"></slot></header><main><slot></slot></main>')

    title = root.select_one('slot[name="title"]')
    print([node.tag for node in title.assigned_nodes()])
    print([node.tag for node in card.flattened_children])

.. testoutput::

    ['h2']
    ['header', 'main']

The shadow tree stays out of the host's own children and serialization; it is reachable only through
:attr:`~turbohtml.Element.shadow_root` (or the reference :meth:`~turbohtml.Element.attach_shadow` returned). See
:doc:`/how-to/shadow-dom` for the slot recipes and :doc:`/explanation/shadow-dom` for the flattened-tree model.

Continue to :doc:`editing` to build and change trees of your own.
