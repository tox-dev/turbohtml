.. _migration-lxml:

###########
 From lxml
###########

.. package-meta:: lxml lxml/lxml

`lxml <https://lxml.de>`_ is the libxml2/libxslt binding that most Python HTML and XML processing has been built on.
``lxml.html`` parses documents into ElementTree-style elements with ``.text``/``.tail`` strings, and the wider stack
adds XPath, XSLT, RelaxNG/DTD/XML-Schema validation, and C14N. It is the default reach for scraping, feed parsing, and
XML pipelines because it wraps a fast, battle-tested C library and exposes the full ElementTree API.

turbohtml covers the HTML side of that ground with a native C core of its own. :func:`turbohtml.parse` builds the WHATWG
document tree libxml2's HTML parser does not, returns a fully type annotated :class:`~turbohtml.Document`, and folds
XPath 1.0 (with EXSLT), CSS selection, and the ``find``/``find_all`` grammar into one node API instead of separate
``findall``/``xpath``/``cssselect`` entry points, adds an XSLT 1.0 processor (:mod:`turbohtml.transform`), and validates
XML against XSD 1.0 and RELAX NG schemas. It does not attempt generic XML pipelines; it targets browser-accurate HTML
parsing and the query/edit/transform/validate/serialize surface around it.

*******************
 turbohtml vs lxml
*******************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - lxml
    - - Scope
      - WHATWG HTML5 parse, serialize, edit, CSS, XPath 1.0 + EXSLT, XSLT 1.0, link helpers
      - Generic XML and HTML via libxml2, plus XSLT, schema validation, C14N
    - - Feature breadth
      - Browser-accurate HTML tree, one node API for XPath/CSS/find, XSLT 1.0, streaming parse, builder
      - Full ElementTree API, XPath 1.0, XSLT 1.0, DTD/RelaxNG/XML-Schema, iterparse
    - - Performance
      - Parses two to four times faster than lxml; stays ahead across the operational surface
      - Mature libxml2 C core; streaming evaluation narrows on multi-megabyte inputs
    - - Typing
      - Fully type annotated, ships stubs
      - Partial; relies on third-party stub packages
    - - Dependencies
      - Self-contained native C extension
      - Bundles or links libxml2 and libxslt
    - - Maintenance
      - Newer, WHATWG-spec-driven
      - Long-established, widely deployed, actively maintained

Feature overlap
===============

These port one-to-one from ``lxml.html``/``lxml.etree`` to turbohtml:

- Parsing a document (``lxml.html.document_fromstring``) and a fragment (``lxml.html.fromstring``).
- Element identity and attributes: ``el.tag``, ``el.get``/``el.set``/``el.attrib``, the ``el.classes`` set operations.
- Tree navigation: ``getparent``, ``getnext``, ``getprevious``, ``iterdescendants``, ``iterancestors``, ``list(el)``.
- Queries: ``findall`` and ``xpath`` (XPath 1.0), ``cssselect`` (CSS), precompiled ``etree.XPath`` objects, node-set
  ``$variable`` bindings, ``namespaces=`` prefix maps, and custom XPath callables.
- The EXSLT ``re:``, ``set:``, ``str:``, ``math:``, and ``date:`` function namespaces.
- Locator generation (``getroottree().getpath``), link iteration and rewriting (``iterlinks``, ``make_links_absolute``,
  ``rewrite_links``), source positions (``sourceline``), tree edits (``drop_tag``, ``drop_tree``), the
  ``lxml.builder.E`` builder, and serialization (``lxml.html.tostring``).

What turbohtml adds
===================

- A WHATWG-conformant parse: malformed input lands in the same tree a browser builds, where libxml2's HTML parser does
  not follow the HTML5 tree-construction algorithm.
- One node API. XPath, CSS, and the ``find``/``find_all`` grammar are methods on every node rather than three separate
  extension entry points, and a callable or ``extensions=`` mapping that returns an :class:`~turbohtml.Element` is
  marshaled straight back into the evaluator's node-set.
- Built-in EXSLT. The ``re:``, ``set:``, ``str:``, ``math:``, and ``date:`` namespaces dispatch in the compiled-C XPath
  engine with no per-call registration; lxml has to register ``libexslt`` and re-resolve the namespace map on each
  evaluation.
- The XPath 2.0 string convenience functions -- ``ends-with``, ``string-join``, ``lower-case``, ``upper-case``,
  ``matches``, and ``replace`` -- resolve in the same dispatch. libxml2's XPath 1.0 has none of these, so an expression
  ported from ``elementpath`` or ``htmlquery`` that leans on them runs unchanged rather than raising an unknown-function
  error.
- :meth:`~turbohtml.Element.css_path`, a unique CSS-selector locator, which lxml has no equivalent for.
- Full type annotations and shipped stubs across the whole surface.

What lxml has that turbohtml does not
=====================================

The wider libxml2 toolchain is a deliberate clean-break scope cut:

- XSLT is at parity for the 1.0 core (see :ref:`the transform section <migration-lxml>` below): ``lxml.etree.XSLT``
  ports to :class:`turbohtml.transform.Transform`. Out of scope are external-document loading (``xsl:include``,
  ``xsl:import``, ``document()``) and the libxslt/EXSLT extension-element surface.
- Schema validation: ``etree.XMLSchema`` and ``etree.RelaxNG`` map to :class:`turbohtml.validate.XMLSchema` and
  :class:`~turbohtml.validate.RelaxNG` (below); DTD (``etree.DTD``) and Schematron have no equivalent.
- DTD-declared entities and the wider infoset: :func:`turbohtml.parse_xml` (below) handles well-formed XML but resolves
  only the five predefined entities and numeric references; a document that relies on ``<!ENTITY>`` definitions stays
  with lxml.
- C14N 2.0: :meth:`~turbohtml.Node.canonicalize` implements the Canonical XML 1.0/1.1 and Exclusive family that XML
  signatures sign (see below); the later, separately specified C14N 2.0 (``etree.canonicalize``) is out of scope.
- XPath is at parity, not a gap. Both are XPath 1.0 with EXSLT, and turbohtml adds the XPath 2.0 string convenience
  functions on top. The only pieces out of scope are the node-synthesizing ``str:tokenize``/``str:split``, the implicit
  current-date ``date:`` forms, and full XPath 2.0 (sequences, types, FLWOR).

Validate against a schema
=========================

``etree.XMLSchema(schema_doc).validate(tree)`` becomes :meth:`turbohtml.validate.XMLSchema.validate`, and
``etree.RelaxNG`` becomes :class:`~turbohtml.validate.RelaxNG`. Where lxml returns a bool and stashes the reasons on
``schema.error_log``, turbohtml returns a :class:`~turbohtml.validate.ValidationResult` whose ``errors`` tuple carries
each violation with the ``/root/child`` path that located it:

.. testcode::

    from turbohtml import parse_xml
    from turbohtml.validate import XMLSchema

    schema = XMLSchema(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="qty" type="xs:positiveInteger"/></xs:schema>'
    )
    result = schema.validate(parse_xml("<qty>0</qty>"))
    print(result.valid, result.errors[0].path)

.. testoutput::

    False /qty

Performance
===========

turbohtml parses two to four times faster than lxml while matching a browser on malformed input, and stays ahead across
the operational surface: fragment parsing, CSS selection, text and tree walks, the link helpers, XPath, and the
node-path generators.

.. bench-table::
    :file: bench/lxml.json

The :doc:`/development/performance` page benchmarks the full serializer, builder, editor, CSS, XPath 1.0, and EXSLT
surface against lxml directly, and sweeps the node-path generators across every page size. Compiling a hot expression
once with :class:`~turbohtml.XPath` (the parse happens at construction, so the call site only supplies the context node
and any ``$name`` variables) stays ahead of lxml per evaluation, as the precompiled ``//a[@href]`` row shows. On the
EXSLT cases, a ``re:test`` predicate runs nearly twenty times ahead of lxml even though ``re:`` dispatches to Python's
:mod:`re` where lxml uses C ``libexslt``, because it skips the per-call namespace resolution; lxml's streaming
evaluation narrows the node-set reductions on the multi-megabyte inputs.

*************
 Parsing XML
*************

``lxml.etree.fromstring`` / ``etree.XMLParser`` and :func:`turbohtml.parse_xml` both parse under XML 1.0 well-formedness
rather than the HTML tree builder: names are case-sensitive, ``<x/>`` self-closes any element, and CDATA sections,
processing instructions, and namespace prefixes are honored. The entry points swap directly, with two differences to
plan for.

First, turbohtml keeps qualified names verbatim. lxml resolves a prefix to its URI and stores the tag in Clark notation
(``{urn:h}a``, read back through ``etree.QName``); turbohtml leaves the tag as the source ``h:a`` and keeps every
``xmlns``/``xmlns:prefix`` declaration as an ordinary attribute on :attr:`~turbohtml.Element.attrs`. It still validates
namespaces -- an undeclared prefix is a well-formedness error -- but it does not build lxml's ``nsmap`` or rewrite
names.

Second, ill-formed input raises rather than recovering. A mismatched or unclosed tag, an undeclared prefix, an undefined
entity, or a duplicate attribute raises :exc:`~turbohtml.HTMLParseError`, whose ``error`` carries the
:class:`~turbohtml.ParseError` code, line, and column -- the equivalent of lxml's default ``recover=False``
``XMLSyntaxError``. turbohtml has no ``recover=True`` counterpart; a document that must survive malformed input stays
with lxml.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `lxml <https://lxml.de/>`__
      - turbohtml
    - - ``etree.fromstring(b"<r/>")``, ``etree.XMLParser().feed(...)``
      - :func:`turbohtml.parse_xml` (``parse_xml("<r/>")``)
    - - ``etree.QName(el).localname``, ``el.tag == "{urn}a"``, ``el.nsmap``
      - :attr:`el.tag <turbohtml.Element.tag>` is the source ``prefix:local``; declarations stay on
        :attr:`~turbohtml.Element.attrs`
    - - ``etree.XMLSyntaxError`` on malformed input
      - :exc:`~turbohtml.HTMLParseError` (its ``error`` is a :class:`~turbohtml.ParseError`)

libxml2 leads on raw XML throughput -- it is a decade-tuned C parser, and the ``parse XML to a tree`` row shows it ahead
on the catalog document. turbohtml's XML mode trades that for the same native, fully typed, dependency-free node API its
HTML path uses, so an XML feed and an HTML page navigate, query, and serialize through one surface.

************************
 Transforming with XSLT
************************

``lxml.etree.XSLT`` compiles a parsed stylesheet into a callable; :class:`turbohtml.transform.Transform` does the same.
Both read the stylesheet as XML, hold the compiled form, and apply it to a source tree, so the port is mechanical:
``etree.XSLT(etree.parse(sheet))`` becomes ``Transform(parse_xml(sheet))``, and calling the result on a document returns
the transformed markup as a ``str``.

.. testcode::

    from turbohtml import parse_xml
    from turbohtml.transform import Transform

    style = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="html"/>'
        '<xsl:template match="/"><ul>'
        '<xsl:apply-templates select="catalog/book"><xsl:sort select="title"/></xsl:apply-templates>'
        "</ul></xsl:template>"
        '<xsl:template match="book"><li class="{@cat}"><xsl:value-of select="title"/></li></xsl:template>'
        "</xsl:stylesheet>"
    )
    convert = Transform(style)
    source = parse_xml(
        '<catalog><book cat="sci"><title>Cosmos</title></book><book cat="fic"><title>1984</title></book></catalog>'
    )
    print(convert(source))

.. testoutput::

    <ul><li class="fic">1984</li><li class="sci">Cosmos</li></ul>

A top-level ``xsl:param`` is a keyword argument whose value is an XPath expression, exactly as in ``transform(doc,
param="'text'")``. The engine reuses turbohtml's XPath 1.0 evaluator for every match pattern and select expression and
implements the XSLT 1.0 core -- templates with modes and priorities, ``apply-templates`` with ``sort``,
``call-template``, ``for-each``, ``if``, ``choose``, ``value-of``, ``copy``/``copy-of``, ``element``/``attribute``,
``variable``/``param``, ``number``, ``key`` and ``key()``, the conflict-resolution rules, and the
``xml``/``html``/``text`` output methods.

Two limits to plan for. lxml resolves ``xsl:include``, ``xsl:import``, and ``document()`` against the filesystem;
turbohtml loads no external resources, so a multi-file stylesheet must be flattened first, and ``document()`` returns an
empty node-set. And libxslt leads on transform throughput -- it is a decade-tuned C engine, and the ``XSLT transform``
row reflects that; turbohtml trades the raw speed for a stylesheet processor that ships in the same pure,
dependency-free wheel as the parser, over one typed node API. A pipeline that lives inside libxslt's wider XSLT/EXSLT
surface stays with lxml.

****************
 How to migrate
****************

The two parse entry points swap directly: :func:`turbohtml.parse` replaces ``lxml.html.document_fromstring`` and
:func:`turbohtml.parse_fragment` replaces ``lxml.html.fromstring``. The biggest change is the tree shape. lxml stores
text as an element's ``.text`` and ``.tail`` strings; turbohtml models it as real child :class:`~turbohtml.Text` nodes,
so you iterate :attr:`~turbohtml.Node.children` instead of reading two string fields.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `lxml <https://lxml.de/>`__
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
    - - ``lxml.builder.E.ul(E.li("a"), E.li("b"))``
      - :data:`turbohtml.build.E` (``E.<tag>(attrs, *children)`` with a leading attribute mapping)
    - - ``el.drop_tag()``, ``el.drop_tree()``
      - :meth:`~turbohtml.Node.unwrap`, :meth:`~turbohtml.Node.decompose`
    - - ``el.sourceline``
      - :attr:`~turbohtml.Node.source_line` (1-based, like lxml; plus :attr:`~turbohtml.Node.source_col`)
    - - ``el.iterlinks()``
      - :meth:`~turbohtml.Node.links`
    - - ``el.make_links_absolute(base)``, ``el.rewrite_links(fn)``
      - :meth:`~turbohtml.Node.resolve_links`, :meth:`~turbohtml.Node.rewrite_links`
    - - ``etree.iterparse(...)``
      - :class:`turbohtml.IncrementalParser` (``feed`` chunks, ``close`` for the :class:`~turbohtml.Document`)
    - - ``lxml.html.tostring(el)``
      - :attr:`~turbohtml.Node.html`
    - - ``lxml.etree.tostring(el, method="xml")``, ``tostring(el, method="xhtml")``
      - :meth:`el.serialize(Html(xml=True)) <turbohtml.Node.serialize>`
    - - ``etree.tostring(el, method="c14n", exclusive=, with_comments=)``, ``ElementTree.write_c14n``
      - :meth:`el.canonicalize(Canonical(...)) <turbohtml.Node.canonicalize>`

A query-and-select flow ports directly:

.. testcode::

    doc = parse('<div><a href="/x">go</a></div>')
    print(doc.find_all("a", attrs={"href": True}))
    print(doc.select_one("div a").attrs["href"])

.. testoutput::

    [Element('a')]
    /x

Precompile a hot XPath the same way you would reach for ``lxml.etree.XPath`` over a bare ``el.xpath``. turbohtml's
compiled program is tree-independent, so a single object evaluates against many documents:

.. testcode::

    from turbohtml import XPath

    links = XPath("//a[@href=$u]")
    doc = parse('<div><a href="/x">go</a><a href="/y">stay</a></div>')
    print([link.attrs["href"] for link in links(doc, u="/x")])

.. testoutput::

    ['/x']

The builder reads like ``lxml.builder.E`` but hands back a real :class:`~turbohtml.Element`, so the query, edit, and
serialize surface stays available on what you build:

.. testcode::

    from turbohtml.build import E

    print(E.ul(E.li({"class": "item"}, "one"), E.li({"class": "item"}, "two")).serialize())

.. testoutput::

    <ul><li class="item">one</li><li class="item">two</li></ul>

Where lxml reaches for ``tostring(el, method="xml")`` (or ``"xhtml"``) to emit well-formed XML, pass
:class:`~turbohtml.Html` with ``xml=True``. Empty elements self-close, foreign SVG and MathML subtrees carry their
namespace declarations, and text and attribute values follow the XML escaping rules -- the HTML void-element and
raw-text special casing does not apply:

.. testcode::

    from turbohtml import Html

    doc = parse("<p>a &amp; b<br><svg><rect></rect></svg></p>")
    print(doc.select_one("p").serialize(Html(xml=True)))

.. testoutput::

    <p>a &amp; b<br/><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg></p>

Where lxml signs a document with ``etree.tostring(el, method="c14n")`` or ``ElementTree.write_c14n``, pass
:class:`~turbohtml.Canonical` to :meth:`~turbohtml.Node.canonicalize`. It emits the same Canonical XML infoset -- sorted
attributes, minimized namespaces, empty elements as start-end pairs, normalized character references -- as UTF-8 bytes,
and takes the same ``exclusive``, ``with_comments``, and ``inclusive_ns_prefixes`` knobs (plus a ``version`` for c14n
1.0 vs 1.1):

.. testcode::

    from turbohtml import Canonical

    doc = parse("<p z='1' a='2'>x &amp; y</p>")
    print(doc.select_one("p").canonicalize())
    print(doc.select_one("p").canonicalize(Canonical(exclusive=True, with_comments=True)))

.. testoutput::

    b'<p a="2" z="1">x &amp; y</p>'
    b'<p a="2" z="1">x &amp; y</p>'

**********************
 Gotchas and pitfalls
**********************

- No ``text``/``tail``. A node's children are its text runs and elements interleaved; read :attr:`~turbohtml.Node.text`
  for the concatenation.
- Different tree on malformed input. lxml parses with libxml2, which is not WHATWG-conformant, so broken markup lands in
  a different tree than the one turbohtml (and a browser) builds. Do not expect byte-identical trees when porting
  scrapers that leaned on libxml2's recovery quirks.
- Custom XPath functions bind per call, not globally. lxml registers callables through ``etree.FunctionNamespace``;
  turbohtml passes them through the ``extensions=`` mapping of :meth:`~turbohtml.Node.xpath`, bound once against the
  compiled expression rather than a process-wide table.
- Streaming differs. For a document that arrives in pieces, ``etree.iterparse`` is replaced by
  :class:`turbohtml.IncrementalParser`: feed ``str`` or ``bytes`` chunks with ``feed`` and call ``close`` for the
  finished :class:`~turbohtml.Document`. It never holds the whole source at once, but it does not expose lxml's
  event-driven element callbacks; you walk the completed tree.
- EXSLT is built in but not exhaustive. The node-synthesizing ``str:tokenize``/``str:split`` and the implicit
  current-date ``date:`` forms stay out of scope; every other ``re:``/``set:``/``str:``/``math:``/``date:`` form ports
  straight through with no registration.
