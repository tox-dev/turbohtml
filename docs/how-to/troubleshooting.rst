#################
 Troubleshooting
#################

The things that trip up a first turbohtml session, each as a symptom and a fix. turbohtml is not a drop-in for
BeautifulSoup or lxml, so a habit from one of those is the usual cause.

*****************************
 A node indexes its children
*****************************

turbohtml models text as real child nodes, the WHATWG DOM shape, where lxml uses ``text``/``tail`` and BeautifulSoup
uses ``.string``. So ``node[i]`` indexes the children, and a text run is one of them. For the text, call
:attr:`~turbohtml.Node.text`:

.. testcode::

    import turbohtml

    para = turbohtml.parse("<p>Hello <b>world</b></p>").find("p")
    print(len(para), type(para[0]).__name__)
    print(para.text)

.. testoutput::

    2 Text
    Hello world

**************************
 Attributes live in attrs
**************************

An element is not a mapping of its attributes. Reach an attribute through :attr:`~turbohtml.Element.attrs` (or
:meth:`~turbohtml.Element.attr` for one with a default), never ``node["href"]``:

.. testcode::

    link = turbohtml.parse('<a href="/x">y</a>').find("a")
    print(link.attrs["href"])
    print(link.attr("title"))

.. testoutput::

    /x
    None

**********************
 find can return None
**********************

:meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.select_one` return ``None`` when nothing matches, so reaching an
attribute straight off the result raises ``AttributeError`` on a page that happens to lack the element. Guard it:

.. testcode::

    doc = turbohtml.parse("<p>x</p>")
    heading = doc.select_one("h1")
    print(heading.text if heading is not None else "no heading")

.. testoutput::

    no heading

****************************
 An invalid selector raises
****************************

A malformed CSS selector raises :class:`~turbohtml.SelectorSyntaxError`, and a malformed XPath raises
:class:`ValueError`. Catch the specific type when the selector comes from user input:

.. testcode::

    from turbohtml import SelectorSyntaxError

    try:
        doc.select("p!!!")
    except SelectorSyntaxError:
        print("bad selector")

.. testoutput::

    bad selector

******************************
 Encoding detection is opt-in
******************************

:func:`turbohtml.parse` treats a ``str`` as already decoded and never sniffs it. When the bytes came off a socket or a
file with an unknown encoding, pass the ``bytes`` so the parser runs the WHATWG sniff, or call
:func:`turbohtml.detect.detect` to learn the encoding first. Decoding a stream yourself with the wrong codec before
handing turbohtml a ``str`` is the usual cause of mojibake, and passing ``match.encoding`` to :meth:`bytes.decode` is
one way to pick the wrong one: decode with ``match.codec`` instead. See :doc:`encoding`.

*************************************
 Streaming rewrite cannot look ahead
*************************************

:func:`turbohtml.rewrite.rewrite` runs in one forward pass and never buffers the document, so a selector that needs
content it has not reached -- a sibling combinator, ``:has()``, ``:nth-child()`` -- raises
:class:`~turbohtml.SelectorSyntaxError` rather than silently matching nothing. When you need one of those, parse a tree
with :func:`turbohtml.parse` and edit it instead. See :doc:`/explanation/streaming`.

*****************************************
 parse, parse_fragment, parse_xml differ
*****************************************

The three entry points build different trees. :func:`turbohtml.parse` builds a full document, inserting the implied
``<html>``, ``<head>``, and ``<body>``. :func:`turbohtml.parse_fragment` parses in a fragment context, so no wrapper
elements appear. :func:`turbohtml.parse_xml` applies XML rules, where tag case is significant and every element must
close. Reaching for ``find("body")`` on a fragment, or feeding XML to the HTML parser, gives a tree you did not expect.

*************************
 Sanitize needs a policy
*************************

:func:`turbohtml.clean.sanitize` keeps only what its :class:`~turbohtml.clean.Policy` allows on top of a non-overridable
safety baseline. An empty or too-narrow policy strips more than you meant; ``Policy.relaxed()`` is the starting point
for user content. ``transform_tags`` renames a tag rather than dropping it. See :doc:`sanitizing`.

**********************************
 Foreign content changes matching
**********************************

Inside SVG and MathML, tag and attribute names are case-sensitive and namespaced (``<clipPath>``, ``viewBox``), unlike
the ASCII-folded HTML around them. A selector written in lowercase will miss a camel-cased foreign element, and XML
parsed with :func:`turbohtml.parse_xml` keeps case throughout. Match foreign elements with their exact casing.

**********************************
 Concurrency safety is per-object
**********************************

Free-threading safety holds per tree and per :class:`~turbohtml.Tokenizer`: two threads on one tree are safe, but
feeding one tokenizer, or driving one incremental parse, from several threads at once needs your own synchronization.
Give each thread its own tokenizer, or lock around the shared one. See :doc:`/explanation/free-threading`.

*********************************
 It is not a bs4 or lxml drop-in
*********************************

There is no ``.string``, no ``text``/``tail``, and no ``soup(...)`` callable; one concept carries one name. Code written
to BeautifulSoup's or lxml's contract needs a translation, not a rename. The :doc:`/migration/index` guides map each
library's surface onto turbohtml's.

For anything the guides do not cover, the :doc:`/reference` has the exact signatures, the :doc:`/explanation/index`
carries the reasoning, and the issue tracker takes a report.
