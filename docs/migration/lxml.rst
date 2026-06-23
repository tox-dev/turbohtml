.. _migration-lxml:

###########
 From lxml
###########

.. image:: https://static.pepy.tech/badge/lxml/month
    :alt: lxml monthly downloads
    :target: https://pepy.tech/project/lxml

`lxml <https://lxml.de>`_ is the libxml2/libxslt binding that most Python HTML and XML processing has been built on:
``lxml.html`` parses documents into ElementTree-style elements with ``.text``/``.tail`` strings, and the wider stack
adds XPath, XSLT, and schema validation.

***************
 Why turbohtml
***************

:func:`turbohtml.parse` builds the WHATWG document tree that libxml2's HTML parser does not, returns a fully type
annotated :class:`~turbohtml.Document`, and folds XPath, CSS, and the ``find``/``find_all`` grammar into one node API
instead of separate ``findall``/``xpath``/``cssselect`` entry points. It parses two to four times faster than lxml while
matching a browser on malformed input:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - parse
      - turbohtml
      - lxml
      - speed-up
    - - wpt page (4 kB)
      - 11.4 µs
      - 27.1 µs
      - 2.4x
    - - wpt page (92 kB)
      - 272 µs
      - 631 µs
      - 2.3x
    - - whatwg spec (235 kB)
      - 518 µs
      - 1.22 ms
      - 2.4x
    - - ecmascript spec (3 MB)
      - 4.54 ms
      - 17.4 ms
      - 3.8x

The :doc:`/development/performance` page also benchmarks turbohtml's serializer, builder, editor, CSS, and XPath 1.0
engine against lxml directly.

*************
 The renames
*************

:func:`turbohtml.parse` replaces ``lxml.html.document_fromstring`` and returns a :class:`~turbohtml.Document`;
:func:`turbohtml.parse_fragment` replaces ``lxml.html.fromstring`` for a fragment. The biggest change is the tree shape:
lxml stores text as an element's ``.text`` and ``.tail`` strings, while turbohtml models it as real child
:class:`~turbohtml.Text` nodes, so you iterate children instead of reading two string fields.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - lxml
      - turbohtml
    - - ``el.tag``
      - :attr:`~turbohtml.Element.tag` (same)
    - - ``el.get("x")``, ``el.attrib``, ``el.set("x", "v")``
      - :attr:`~turbohtml.Element.attrs` (``attrs.get("x")``, ``attrs["x"] = "v"``)
    - - ``el.classes.add("x")``, ``el.classes.discard("x")``, ``el.classes.toggle("x")``, ``"x" in el.classes``
      - :meth:`el.add_class("x") <turbohtml.Element.add_class>`, :meth:`el.remove_class("x")
        <turbohtml.Element.remove_class>`, :meth:`el.toggle_class("x") <turbohtml.Element.toggle_class>`,
        :meth:`el.has_class("x") <turbohtml.Element.has_class>`
    - - ``el.text``, ``el.tail``
      - child :class:`~turbohtml.Text` nodes; iterate :attr:`~turbohtml.Node.children`
    - - ``el.text_content()``
      - :attr:`~turbohtml.Node.text`
    - - ``el.getparent()``, ``el.getnext()``, ``el.getprevious()``
      - :attr:`~turbohtml.Node.parent`, :attr:`~turbohtml.Node.next_sibling`, :attr:`~turbohtml.Node.previous_sibling`
    - - ``list(el)``, ``el.iterdescendants()``, ``el.iterancestors()``
      - :attr:`~turbohtml.Node.children`, :attr:`~turbohtml.Node.descendants`, :attr:`~turbohtml.Node.ancestors`
    - - ``el.findall(".//a")``, ``el.xpath("//a[@href]")``
      - :meth:`~turbohtml.Node.find_all`, :meth:`~turbohtml.Node.xpath`
    - - ``etree.XPath("//a[@href=$u]")(el, u=v)``
      - :class:`~turbohtml.XPath` (``XPath("//a[@href=$u]")(el, u=v)``)
    - - ``el.xpath("$rows/td", rows=el.xpath("//tr"))``
      - :meth:`el.xpath("$rows/td", rows=el.xpath("//tr")) <turbohtml.Node.xpath>` (a ``$name`` variable binds a scalar,
        an :class:`~turbohtml.Element`, or an iterable of elements; :meth:`~turbohtml.Node.xpath_one` and
        :meth:`~turbohtml.Node.xpath_iter` take the same bindings)
    - - ``el.xpath("//svg:rect", namespaces={"svg": SVG})``
      - :meth:`~turbohtml.Node.xpath` with the same ``namespaces={"svg": SVG}`` (the prefix binds at evaluation time)
    - - ``el.cssselect("div a")``
      - :meth:`~turbohtml.Node.select`
    - - ``etree.FunctionNamespace(None)["f"] = fn``; ``el.xpath("f(//a)")``
      - :meth:`el.xpath("f(//a)", extensions={(None, "f"): fn}) <turbohtml.Node.xpath>` (the function may return a
        scalar, an :class:`~turbohtml.Element`, or an iterable of elements)
    - - ``el.getroottree().getpath(el)``
      - :meth:`el.xpath_path() <turbohtml.Element.xpath_path>` (or :meth:`el.css_path() <turbohtml.Element.css_path>`
        for a CSS selector)
    - - ``lxml.html.Element("div")``, ``etree.SubElement(p, "div")``
      - :class:`~turbohtml.Element`, :meth:`p.append(Element("div")) <turbohtml.Element.append>`
    - - ``el.drop_tag()``, ``el.drop_tree()``
      - :meth:`~turbohtml.Node.unwrap`, :meth:`~turbohtml.Node.decompose`
    - - ``el.sourceline``
      - :attr:`~turbohtml.Node.source_line` (1-based, like lxml; plus :attr:`~turbohtml.Node.source_col`)
    - - ``el.iterlinks()``
      - :meth:`~turbohtml.Node.links`
    - - ``el.make_links_absolute(base)``, ``el.rewrite_links(fn)``
      - :meth:`~turbohtml.Node.resolve_links`, :meth:`~turbohtml.Node.rewrite_links`
    - - ``lxml.html.tostring(el)``
      - :attr:`~turbohtml.Node.html`

.. testcode::

    doc = parse('<div><a href="/x">go</a></div>')
    print(doc.find_all("a", attrs={"href": True}))
    print(doc.select_one("div a").attrs["href"])

.. testoutput::

    [Element('a')]
    /x

**************************
 Tree and link operations
**************************

Beyond XPath and CSS, the operational renames above each beat lxml on the wpt pages from the
:doc:`/development/performance` benchmark. :func:`turbohtml.parse_fragment` (lxml's ``lxml.html.fromstring``) parses an
``innerHTML`` snippet in its container context; :attr:`~turbohtml.Node.text` (lxml's ``text_content()``) concatenates
the visible text; :attr:`~turbohtml.Node.descendants` (lxml's ``iterdescendants()``) walks the subtree; and the link
surface (:meth:`~turbohtml.Node.links`, :meth:`~turbohtml.Node.resolve_links`, :meth:`~turbohtml.Node.rewrite_links`
against ``iterlinks``/``make_links_absolute``/``rewrite_links``) extracts and rewrites every link-bearing attribute.
turbohtml runs each in C over its native tree (``tox -e bench fragment text navigate links``):

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - operation
      - turbohtml
      - lxml
      - speed-up
    - - parse fragment (2 kB)
      - 12.6 µs
      - 39.6 µs
      - 3.2x
    - - text content (92 kB)
      - 36.9 µs
      - 47.2 µs
      - 1.3x
    - - descendant walk (92 kB)
      - 101 µs
      - 295 µs
      - 2.9x
    - - extract links (92 kB)
      - 60.6 µs
      - 2.28 ms
      - 37.7x
    - - absolutize links (92 kB)
      - 251 µs
      - 2.76 ms
      - 11.0x
    - - rewrite links (92 kB)
      - 22.3 µs
      - 2.38 ms
      - 106.7x

*******
 XPath
*******

When one expression runs against many nodes, precompile it once with :class:`~turbohtml.XPath` instead of calling
:meth:`~turbohtml.Node.xpath`, which reparses the expression on every call. This is the same move as reaching for
``lxml.etree.XPath`` over a bare ``el.xpath``: the parse happens at construction, and the call site only supplies the
context node and any ``$name`` variables. turbohtml's compiled program is tree-independent, so a single object evaluates
against many documents, and it stays ahead of lxml per evaluation. The numbers below are for ``//a[@href]`` over the 9.6
kB wpt page from the :doc:`/development/performance` benchmark (``tox -e bench xpath``):

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - ``//a[@href]``
      - turbohtml
      - lxml
      - speed-up
    - - per call (reparsed each time)
      - 0.6 µs
      - 4.1 µs
      - 6.8x
    - - precompiled, reused
      - 0.5 µs
      - 2.8 µs
      - 5.6x

.. testcode::

    from turbohtml import XPath

    links = XPath("//a[@href=$u]")
    doc = parse('<div><a href="/x">go</a><a href="/y">stay</a></div>')
    print([link.attrs["href"] for link in links(doc, u="/x")])

.. testoutput::

    ['/x']

lxml registers custom XPath callables through ``etree.FunctionNamespace``; turbohtml passes them per call through the
``extensions=`` mapping of :meth:`~turbohtml.Node.xpath`. Both dispatch a Python callable per match, but lxml
re-resolves its namespace and function table on every evaluation while turbohtml binds the mapping once against the
compiled expression, and a callable that returns an :class:`~turbohtml.Element` (or an iterable of them) is marshaled
straight back into the evaluator's node-set so the next path step stays on the all-C fast path. Measured over the 9.6 kB
wpt page with ``tox -e bench xpath``:

.. list-table::
    :header-rows: 1
    :widths: 46 18 18 18

    - - extension call
      - turbohtml
      - lxml
      - speed-up
    - - scalar return (``ext_count(//a)``)
      - 1.1 µs
      - 4.1 µs
      - 3.6x
    - - node-set return (``ext_first_two(//a)/@href``)
      - 1.1 µs
      - 3.2 µs
      - 2.9x

Both engines accept a node-set ``$variable``, so a prior result feeds a later expression without re-querying:
:meth:`el.xpath("$rows/td", rows=el.xpath("//tr")) <turbohtml.Node.xpath>` binds the node-set lxml's
``tree.xpath("$rows/td", rows=tree.xpath("//tr"))`` would. turbohtml normalizes the bound node-set into the compiled
program once and walks the following step over interned atoms, so binding a prior result and reusing it stays ahead of
lxml across page sizes (``$rows/div`` reusing a prior ``//div`` result; see :doc:`/development/performance` for the full
sweep):

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - node-set variable reuse
      - turbohtml
      - lxml
      - speed-up
    - - wpt page (4 kB)
      - 4.3 µs
      - 6.3 µs
      - 1.5x
    - - wpt page (9.6 kB)
      - 3.9 µs
      - 6.1 µs
      - 1.5x

A namespace-prefixed name test ports unchanged: pass the same ``namespaces=`` mapping to :meth:`~turbohtml.Node.xpath`
that you give lxml. turbohtml binds the prefix at evaluation time against the per-tree cached program and resolves the
suffix to an interned atom, while lxml re-reads the namespace map on every call, so ``//svg:rect`` with
``namespaces={"svg": "http://www.w3.org/2000/svg"}`` runs several times faster across page sizes (``tox -e bench
xpath``, over a page carrying an SVG block):

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - ``//svg:rect`` (namespaces=)
      - turbohtml
      - lxml
      - speed-up
    - - wpt page (4 kB)
      - 0.8 µs
      - 3.9 µs
      - 4.8x
    - - wpt page (9.6 kB)
      - 0.9 µs
      - 3.7 µs
      - 3.9x
    - - wpt page (92 kB)
      - 15.5 µs
      - 22.5 µs
      - 1.5x

``el.getroottree().getpath(el)`` ports to :meth:`~turbohtml.Element.xpath_path` (a positional XPath) or
:meth:`~turbohtml.Element.css_path` (a unique CSS selector, which lxml has no equivalent for). Both walk only the
element's ancestor chain under the per-tree lock instead of indexing siblings from the root, so generating the locator
for every node in the 9.6 kB wpt page runs several times ahead of ``getpath`` (``tox -e bench path``):

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - node locator (9.6 kB page)
      - turbohtml
      - lxml ``getpath``
      - speed-up
    - - :meth:`~turbohtml.Element.css_path`
      - 15.6 µs
      - 55.2 µs
      - 3.5x
    - - :meth:`~turbohtml.Element.xpath_path`
      - 14.3 µs
      - 55.2 µs
      - 3.9x

The :doc:`/development/performance` page benchmarks the rest of the XPath surface against lxml, and sweeps the node-path
generators across every page size.

**********
 Pitfalls
**********

- No ``text``/``tail``. A node's children are its text runs and elements interleaved; read :attr:`~turbohtml.Node.text`
  for the concatenation.
- lxml parses with libxml2, which is not WHATWG-conformant, so malformed input lands in a different tree than the one
  turbohtml (and a browser) builds.
- For a document that arrives in pieces, ``etree.iterparse`` is replaced by :class:`turbohtml.IncrementalParser`: feed
  ``str`` or ``bytes`` chunks with ``feed`` and call ``close`` for the finished :class:`~turbohtml.Document`. The parser
  never holds the whole source at once, so you can parse a stream larger than the source buffer you would otherwise
  materialize for :func:`turbohtml.parse`.
- The wider libxml2 toolchain is a deliberate clean-break scope cut: XSLT, DTD/RelaxNG/XML-Schema validation, and C14N
  have no turbohtml equivalent. XPath is at parity, not a gap: both are XPath 1.0, and the EXSLT ``re:``, ``set:``,
  ``str:``, ``math:``, and ``date:`` namespaces ``libexslt`` adds are built into :meth:`~turbohtml.Node.xpath`,
  :meth:`~turbohtml.Node.xpath_one`, and :meth:`~turbohtml.Node.xpath_iter` (lxml has to register them, and has no XPath
  2.0/XQuery either), so an lxml ``el.xpath(...)`` call ports straight to :meth:`~turbohtml.Node.xpath` — only the
  node-synthesizing ``str:tokenize``/``str:split`` and the implicit current-date ``date:`` forms stay out of scope.

*******
 EXSLT
*******

turbohtml's EXSLT namespaces dispatch in the same compiled-C XPath engine as the core functions, so an EXSLT predicate
through :meth:`~turbohtml.Node.xpath` costs no registration: the prefix is built in. lxml resolves the namespace map and
routes each call through a ``libexslt`` function you register with ``namespaces=``, so the same expression carries a
per-call setup cost. lxml *can* run the same EXSLT, so this is a direct race, not a no-competitor note. Over the 9.6 kB
wpt page:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - EXSLT (9.6 kB page)
      - turbohtml
      - lxml
      - speed-up
    - - ``//a[re:test(@href, ...)]``
      - 0.5 µs
      - 4.6 µs
      - 9.2x
    - - ``set:distinct(//a)``
      - 0.6 µs
      - 4.0 µs
      - 6.7x

``re:`` dispatches to Python's :mod:`re` where lxml uses C libexslt, yet still leads because it skips the per-call
namespace resolution; ``set:distinct`` stays in C on both sides. The :doc:`/development/performance` page sweeps these
EXSLT cases — alongside the structural axes, predicates, and the core function library — across every page size, where
lxml's streaming evaluation narrows the node-set reductions on the multi-megabyte inputs.
