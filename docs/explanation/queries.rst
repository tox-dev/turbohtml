###################
 Querying the tree
###################

Three engines branch off any node, each answering the same question (which nodes do I want?) in a different language: a
native CSS matcher, an XPath 1.0 engine, and the ``find`` filter grammar. They share the node model and the interned
atoms underneath, so all three resolve names to the same integer and return plain lists of nodes.

.. mermaid::

    flowchart LR
        node([a Node])
        node --> css["select / select_one<br/>matches / closest<br/>(CSS)"]
        node --> xpath["xpath / xpath_one<br/>xpath_iter<br/>(XPath 1.0)"]
        node --> find["find / find_all<br/>(filter grammar)"]
        css --> result([matched nodes])
        xpath --> result
        find --> result

        classDef src fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
        classDef engine fill:#fff3e0,stroke:#e65100,color:#bf360c
        classDef out fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

        class node src
        class css,xpath,find engine
        class result out

The query surface builds on that node model. Navigation covers parents, siblings, and the lazy
:attr:`~turbohtml.Node.descendants`, :attr:`~turbohtml.Node.ancestors`, and document-order
:attr:`~turbohtml.Node.following` / :attr:`~turbohtml.Node.preceding` iterators, plus the sequence protocol over a
node's children. :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` filter a chosen
:class:`~turbohtml.Axis` by tag and attributes, where a filter is a string, regex, callable, or list.
:meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.select_one` run a native CSS matcher covering type, id, class,
attribute, the four combinators, the structural pseudo-classes (including ``:nth-child(An+B of S)``, which indexes only
the siblings matching ``S``), the ``:is()``/``:where()``/``:has()``/``:not()`` functional pseudo-classes, and the
``:scope``, form/UI (``:checked``, ``:disabled``, ``:default``, ...), ``:lang()`` and ``:dir()`` pseudo-classes a static
tree can determine. :meth:`~turbohtml.Node.matches` and :meth:`~turbohtml.Node.closest` test a node in place. ``:is()``
and ``:where()`` parse their argument as a forgiving selector list (a bad arm is dropped, the rest stay usable), while
``:not()`` and ``:has()`` take a real list where any bad arm is an error, as the Selectors standard specifies. The
pseudo-classes that depend on live interaction or navigation state (``:hover``, ``:focus``, ``:target``, ``:visited``,
``:link``, ...) parse but match nothing, since a parsed document has no such state. Selectors compile against the tree,
so a tag or attribute name resolves to the same interned atom the parser assigned and each match is an integer compare.
Compiling against the tree also captures its document mode, so ``#id`` and ``.class`` fold ASCII case in a quirks-mode
document and compare exactly otherwise, as the Selectors standard requires. :meth:`~turbohtml.Node.xpath`,
:meth:`~turbohtml.Node.xpath_one`, and :meth:`~turbohtml.Node.xpath_iter` evaluate XPath 1.0 over the same model: a
native-C engine compiles each expression once into an immutable, per-tree-cached program, resolves name tests to
interned atoms, and collapses the ``//`` abbreviation to a single ``descendant`` walk, so the structural axes,
predicates, operators, unions, and the core function library run at lxml's speed. The core API stays
one-name-per-concept and returns plain lists, so the jQuery-style chaining pyquery users expect lives in an optional
Python-side wrapper, :class:`turbohtml.query.Query`, whose traversal and mutation methods each return a wrapper. Output
runs back through :attr:`~turbohtml.Node.html`, :meth:`~turbohtml.Node.serialize`, and :meth:`~turbohtml.Node.encode`,
WHATWG-conformant by default with the escaping selectable through :class:`~turbohtml.Formatter`.

*****************************
 Extracting strings (parsel)
*****************************

Scraping is string work: the caller wants ``"/p/42"`` or ``"42"``, not a node to read an attribute off. Scrapy's
``parsel`` made that the center of its API with ``::text`` / ``::attr()`` pseudo-elements and ``Selector.re()`` /
``.re_first()``, and the migration path needs the same primitives without bolting non-standard pseudo-elements onto the
CSS engine. turbohtml keeps the selector pure and adds the extraction step as three node methods instead.

:meth:`~turbohtml.Element.attr` returns the *raw* attribute value as one string: ``class="a b c"`` reads back as ``"a b
c"`` rather than the token list :attr:`Element.attrs <turbohtml.Element.attrs>` exposes, a valueless attribute as
``""``, and an absent one as the supplied default. It is the single-string counterpart to the live mapping, the one
``parsel``'s ``::attr(name)`` translates to. :meth:`~turbohtml.Node.re` and :meth:`~turbohtml.Node.re_first` run a
pattern (a ``str`` compiled once through :func:`re.compile`, or a pattern you compiled yourself) over the node's
:attr:`~turbohtml.Node.text`, or over an attribute value when ``attr=`` is given. They follow ``parsel``'s group rule
(yield the lone capturing group when the pattern has exactly one, else the whole match) because that is what makes a
single pattern pull just the digits out of ``Order 1138``. The regex itself stays in Python's battle-tested :mod:`re`;
only the source string is produced in C, under the same per-tree critical section :attr:`~turbohtml.Node.text` takes so
a concurrent mutation cannot rewire the subtree mid-read. Unlike ``parsel``, these run on one node rather than a whole
``SelectorList``, so a comprehension over :meth:`~turbohtml.Node.select` covers a page: the explicit loop the rest of
the query API also asks for, rather than a hidden fan-out.
