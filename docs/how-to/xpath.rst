#########################
 Query a tree with XPath
#########################

Evaluate XPath 1.0 against a node with :meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.xpath_one`, and
:meth:`~turbohtml.Node.xpath_iter`: the axes and node tests, ``$name`` variables, namespaces, the EXSLT function sets,
custom extensions, and the compiled :class:`turbohtml.XPath` object.

:meth:`~turbohtml.Node.xpath` evaluates an XPath 1.0 expression relative to a node and returns a list for a node-set
(elements as nodes, attribute and ``text()`` values as ``str``, in document order), or the matching ``float`` / ``str``
/ ``bool`` for a scalar expression like ``count(...)`` or ``string(...)``. :meth:`~turbohtml.Node.xpath_one` returns the
first result or ``None``, and :meth:`~turbohtml.Node.xpath_iter` returns an iterator. The engine supports the structural
axes, the ``name`` / ``*`` / ``node()`` / ``text()`` / ``comment()`` / ``processing-instruction()`` node tests,
predicates, the boolean, relational, and arithmetic operators, unions, and the complete XPath 1.0 core function library:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>")
    print([td.text for td in doc.xpath("//td")])
    print(doc.xpath("//tr[2]/td[1]/text()"))
    print(doc.xpath("count(//td)"))
    print(doc.xpath_one("//td[. = '3']").text)

.. testoutput::

    ['1', '2', '3', '4']
    ['3']
    4.0
    3

An absolute path starts at the document root and a leading ``//`` rescans the whole document, so write ``.//`` for
descendants of the context node. Migrating from ``lxml``, ``parsel``, or ``pyquery`` keeps your existing expressions.

Two functions read the HTML document the way HTML means it, where ``lxml``'s legacy HTML parser returns nothing:
``lang()`` honors the HTML ``lang`` attribute (``lxml`` only consults ``xml:lang``), and ``namespace-uri()`` reports the
real SVG and MathML namespace for foreign content (``lxml`` leaves it empty). HTML elements report no namespace in both,
so an unprefixed name test keeps matching them.

To select foreign content by namespace, bind each prefix to a URI through ``namespaces`` (the same argument ``lxml``
takes). A prefixed name test then matches an element whose namespace equals the bound URI and whose local name equals
the suffix, so ``//svg:rect`` finds the SVG rectangle while the unprefixed ``//rect`` keeps matching by name alone:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<svg><rect/><circle/></svg>")
    rects = doc.xpath("//svg:rect", namespaces={"svg": "http://www.w3.org/2000/svg"})
    print(len(rects))

.. testoutput::

    1

Pass ``$name`` variables as keyword arguments instead of formatting values into the expression string, so a value with
quotes or special characters cannot break the query:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<a href='/in'>in</a><a href='/out'>out</a>")
    print([a.text for a in doc.xpath("//a[@href=$href]", href="/out")])

.. testoutput::

    ['out']

A variable can also hold a node-set: bind an :class:`~turbohtml.Element` or any iterable of elements (a prior
:meth:`~turbohtml.Node.xpath` result) and feed it back into a later expression instead of re-walking the tree. The
node-set joins path steps, ``count()``, unions, and predicates like any other node-set:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>")
    rows = doc.xpath("//tr")
    print([td.text for td in doc.xpath("$rows/td", rows=rows)])
    print(doc.xpath("count($rows)", rows=rows))

.. testoutput::

    ['a', 'b', 'c']
    2.0

The EXSLT ``re:test`` and ``re:replace`` functions ``parsel`` and ``scrapy`` rely on work without registering a
namespace; the ``re:`` prefix dispatches to Python's :mod:`re`:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<a href='/p/12'>a</a><a href='/q'>b</a>")
    print([a.attrs["href"] for a in doc.xpath(r"//a[re:test(@href, '\d')]")])

.. testoutput::

    ['/p/12']

A subset of the XPath 2.0 string functions ported ``lxml``, ``elementpath``, and ``htmlquery`` expressions reach for is
built in alongside the 1.0 core: ``ends-with``, ``string-join(seq, sep)``, ``lower-case`` and ``upper-case`` (Unicode
case mapping), and the regex ``matches(input, pattern[, flags])`` and ``replace(input, pattern, repl[, flags])``.
``replace`` uses ``$1``-style group references and rewrites every match:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<ul><li>One</li><li>Two</li><li>Three</li></ul>")
    print(doc.xpath("string-join(//li, ', ')"))
    print(doc.xpath("upper-case(//li[1])"))
    print(doc.xpath("matches('2024-05-06', '\\d{4}-\\d\\d-\\d\\d')"))
    print(doc.xpath("replace('2024-05-06', '(\\d+)-(\\d+)-(\\d+)', '$3/$2/$1')"))

.. testoutput::

    One, Two, Three
    ONE
    True
    06/05/2024

Register your own functions under ``extensions={(namespace, name): callable}`` (use ``None`` for the namespace to call
the function unprefixed). The callable receives a context whose ``context_node`` is the current element, then the
evaluated arguments: a node-set arrives as a ``list`` of :class:`~turbohtml.Element`, and a string, number, or boolean
as the matching Python scalar. Returning a ``str``, number, or ``bool`` produces a scalar; returning an element or an
iterable of elements produces a node-set that feeds later path steps and predicates, exactly as in ``lxml``:

.. testcode::

    import turbohtml
    from types import SimpleNamespace
    from turbohtml import Element


    def first_two(context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
        return nodes[:2]


    doc = turbohtml.parse("<ul><li>a</li><li>b</li><li>c</li></ul>")
    result = doc.xpath("first_two(//li)/text()", extensions={(None, "first_two"): first_two})
    print(result)

.. testoutput::

    ['a', 'b']

Every element in a returned node-set must belong to the document being queried; returning one from another parse raises
``ValueError``.

When one expression runs over many nodes or documents -- a scraper looping rows, or one query across a corpus -- compile
it once with :class:`turbohtml.XPath` instead of re-parsing it on every :meth:`~turbohtml.Node.xpath` call. Bind
``smart_strings`` and ``extensions`` at construction, then call the object with a context node and any ``$name``
variables; it returns exactly what the matching :meth:`~turbohtml.Node.xpath` call would. The compiled object is
immutable and re-entrant, so one instance can be shared across threads.

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<table><tr><td class='num'>1</td><td>x</td></tr><tr><td class='num'>2</td></tr></table>")
    cells = turbohtml.XPath(".//td[@class=$cls]")
    for row in doc.xpath("//tr"):
        print([td.text for td in cells(row, cls="num")])

.. testoutput::

    ['1']
    ['2']

The same built-in dispatch covers the EXSLT ``set:``, ``str:``, ``math:``, and ``date:`` namespaces, so the node-set,
string, numeric, and date helpers ``lxml`` gets from ``libexslt`` work without registering anything. ``set:`` operates
on node-sets (``difference``, ``intersection``, ``distinct``, ``has-same-node``, ``leading``, ``trailing``), ``str:``
builds strings (``replace`` literal-not-regex, ``concat``, ``padding``, ``align``), ``math:`` reduces node-sets and
numbers (``min``, ``max``, ``highest``, ``lowest``, ``abs``, ``power``), and ``date:`` reads fields out of an ISO
``YYYY-MM-DD`` string (``year``, ``month-in-year``, ``day-in-month``, ``day-in-week``, ``leap-year``):

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<ul><li>3</li><li>1</li><li>1</li></ul>")
    print([li.text for li in doc.xpath("set:distinct(//li)")])
    print(doc.xpath("math:max(//li)"))
    print(doc.xpath("str:padding(3, '-')"))
    print(doc.xpath("date:year('2024-06-22')"))

.. testoutput::

    ['3', '1']
    3.0
    ---
    2024.0

``str:tokenize`` and ``str:split`` are not built in: they would have to synthesize token nodes, and the engine's
node-sets only reference nodes that already exist in the tree. Likewise ``date:`` reads an explicit date string rather
than the implicit current date-time, so a query stays deterministic. Register either through ``extensions=`` if you need
it.
