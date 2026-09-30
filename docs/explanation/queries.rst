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
node's children.

:meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` filter a chosen :class:`~turbohtml.Axis` by tag and
attributes, where a filter is a string, regex, callable, or list; a ``text`` predicate applies the same grammar to each
element's collected text. A regex or callable ``text`` predicate runs Python mid-walk, which suspends the per-tree lock,
so the C side snapshots the candidate elements and their text under the lock first and runs the predicate over that
snapshot. A concurrent mutation cannot tear the walk.

:meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.select_one` run a native CSS matcher covering type, id, class,
attribute, the four combinators, the structural pseudo-classes (including ``:nth-child(An+B of S)``, which indexes only
the siblings matching ``S``), ``:is()``/``:where()``/``:has()``/``:not()``, and the ``:scope``, form/UI (``:checked``,
``:disabled``, ``:default``, ...), ``:lang()`` and ``:dir()`` pseudo-classes a static tree can determine.
:meth:`~turbohtml.Node.matches` and :meth:`~turbohtml.Node.closest` test a node in place.

``:is()`` and ``:where()`` parse their argument as a forgiving selector list (the parser drops a bad arm and keeps the
rest), while ``:not()`` and ``:has()`` take a real list where any bad arm is an error. The pseudo-classes that depend on
live interaction or navigation state (``:hover``, ``:focus``, ``:target``, ``:visited``, ``:link``, ...) parse but match
nothing, since a parsed document has no such state.

Selectors compile against the tree, so a tag or attribute name resolves to the interned atom the parser assigned and
each match is an integer compare. The compiled selector captures the document mode too: ``#id`` and ``.class`` fold
ASCII case in a quirks-mode document and match case otherwise.

:meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.xpath_one`, and :meth:`~turbohtml.Node.xpath_iter` evaluate XPath
1.0 over the same model. A native-C engine compiles each expression once into an immutable, per-tree-cached program,
resolves name tests to interned atoms, and collapses the ``//`` abbreviation to a single ``descendant`` walk.

A ``$name`` variable bound through a keyword argument carries a scalar or a node-set. An :class:`~turbohtml.Element` or
an iterable of them becomes an ordered, de-duplicated node-set, so a prior result can feed a later expression
(``doc.xpath("$rows/td", rows=doc.xpath("//tr"))``) without re-walking the tree. The engine rejects elements wrapped
against a different document rather than dereferencing them into a foreign arena.

The compiled program holds no tree pointers and no mutable state, so :class:`turbohtml.XPath` exposes it: a hot
expression compiles once, and one re-entrant, thread-shareable object evaluates against many context nodes.

The EXSLT ``re:``, ``set:``, ``str:``, ``math:``, and ``date:`` namespaces dispatch in the same C engine and work
without registering a namespace. So does a string subset of XPath 2.0 (``ends-with``, ``string-join``, ``lower-case``,
``upper-case``, ``matches`` and ``replace``), without the 2.0 sequence and type machinery behind it.

The test suite runs that subset against the W3C QT3 (XQuery/XPath 3.1) suite: 282 ``fn:*`` cases the engine can express
pass. It skips or xfails the families the engine omits by design (``xs:`` schema types, sequences, XPath 3.x,
XSD-dialect regex, and the typed error-code taxonomy), each with a per-case reason.

The regex functions take :mod:`re` pattern syntax but match in time linear in the input, so an untrusted expression or
stylesheet cannot hang the process with a pattern such as ``(a+)+$``. They pick the match and groups :mod:`re` would.
Back-references need backtracking, so a pattern with one draws on a step budget shared by the whole evaluation and
raises :class:`ValueError` once it runs out; :doc:`/how-to/xpath` lists the limits.

A prefix-to-URI mapping passed as ``namespaces`` resolves during evaluation rather than in the compiled program, so one
cached program serves every mapping. A prefixed name test matches the foreign-content namespace the tree builder tagged
(SVG or MathML); unprefixed tests stay namespace-agnostic over the null-namespace HTML tree.

The core API keeps one name per concept and returns plain lists. jQuery-style chaining lives in an optional Python-side
wrapper, :class:`turbohtml.query.Query`, whose traversal and mutation methods each return a wrapper.

Output runs back through :attr:`~turbohtml.Node.html`, :meth:`~turbohtml.Node.serialize`, and
:meth:`~turbohtml.Node.encode`, WHATWG-conformant by default with the escaping selectable through
:class:`~turbohtml.Formatter`.

A registered ``extensions=`` function crosses the same value boundary in both directions: the four XPath value types
marshal to and from Python, so a node-set argument arrives as a list of elements and a returned element or iterable of
elements becomes a node-set later steps consume. The function sees only live wrappers bound to the queried tree, never
the C node model, so the engine rejects an element returned from another document rather than mixing arenas.

:meth:`~turbohtml.Element.css_path` and :meth:`~turbohtml.Element.xpath_path` invert the query surface: given a node,
they return the locator that finds it again, the way browser devtools "copy selector" and lxml's ``getpath`` do. The
design rule is round-trip identity -- feeding ``css_path()`` back to :meth:`~turbohtml.Node.select` or ``xpath_path()``
to :meth:`~turbohtml.Node.xpath` on the document returns exactly the original node -- which dictates the form. The CSS
path anchors at the nearest ancestor (or the element itself) carrying a document-unique ``id`` so the result stays short
and survives reordering, falling back to ``:nth-of-type()`` steps from the root; an ``id`` is used only when it is a
bare identifier the selector parser reads back verbatim and is unique under the document's own case-folding mode, so the
shortcut can never resolve to a different node. The XPath form is always positional (``/html/body/div[2]/p[3]``), the
shape ``getpath`` produces. Both walk only the ancestor chain, a pure read snapshotted under the per-tree lock, so no
mutation can rewire the path mid-build.

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

*****************
 Reading a table
*****************

A ``<table>`` is the one structure where a node tree is the *wrong* shape for the caller. A scraper wants a grid of
strings, and HTML's ``rowspan`` / ``colspan`` mean the cell at visual position ``(row, col)`` is not the *n*-th ``<td>``
of the *n*-th ``<tr>`` -- a cell can cover several columns, and a row above can reach down into the row below.
``pandas.read_html`` is the tool everyone reaches for, and it pulls in NumPy and pandas to return a ``DataFrame``.
turbohtml resolves the spans itself, in C, and hands back plain lists and dicts, so the dependency is gone but the
``DataFrame`` is one call away: ``pandas.DataFrame(table.records())``.

:meth:`~turbohtml.Element.rows` builds a dense grid. It walks the ``<tr>`` elements that belong to the table -- skipping
any nested table's subtree, whose rows belong to *that* table -- and places each ``<td>``/``<th>`` at the next free
column, filling every slot a ``rowspan`` or ``colspan`` covers with a copy of the cell's text. It follows the WHATWG
table model for row order and span limits: ``<tfoot>`` rows come after every other row wherever the footer sits in the
markup, and a ``rowspan`` stops at the end of its ``<thead>``/``<tbody>``/``<tfoot>``. Rows are padded to a rectangle,
so a ragged table reads back uniform and an empty cell is ``""``. The whole grid is snapshotted into C memory under the
per-tree critical section *before* any Python object is built, the same free-threading discipline the link and text
walks follow: the read never dereferences a live ``first_child``/``next_sibling`` pointer across an allocation that
could let another thread rewire the subtree. :meth:`~turbohtml.Element.records` keys the first row (the header, normally
the ``thead`` row, which the parser emits first) over each later row; a duplicated header keeps the rightmost column's
value, the way a ``dict`` does. :meth:`~turbohtml.Node.tables` runs the same grid build for every table in a subtree,
nested tables included as their own entries.
