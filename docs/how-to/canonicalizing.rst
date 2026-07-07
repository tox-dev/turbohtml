##############################
 Canonicalize a tree for c14n
##############################

When two systems must agree on a document's exact bytes -- signing it, hashing it, comparing it -- serialize it to
Canonical XML with :meth:`~turbohtml.Node.canonicalize`. It returns the UTF-8 bytes an XML signature signs: attributes
reordered (namespace declarations first, then by namespace URI and local name), redundant namespace declarations
dropped, empty elements written as start-end pairs, and character references normalized.

******************************
 Produce a stable byte string
******************************

Call :meth:`~turbohtml.Node.canonicalize` with no arguments for Canonical XML 1.0 without comments. Two trees with the
same content canonicalize to the same bytes regardless of source attribute order or how empty elements were written:

.. testcode::

    import turbohtml

    one = turbohtml.parse("<p z='1' a='2'>x &amp; y</p>").find("p")
    two = turbohtml.parse('<p a="2" z="1">x &amp; y</p>').find("p")
    print(one.canonicalize())
    print(one.canonicalize() == two.canonicalize())

.. testoutput::

    b'<p a="2" z="1">x &amp; y</p>'
    True

******************************
 Pick a variant with a config
******************************

Pass a :class:`~turbohtml.Canonical` config to choose the algorithm. ``exclusive=True`` selects Exclusive XML
Canonicalization, which renders only the namespaces a subtree visibly uses rather than every in-scope ancestor
declaration; ``with_comments=True`` keeps comment nodes; ``version`` selects c14n 1.0 or 1.1. The difference shows when
you canonicalize a subtree whose ancestors declare a namespace it does not use:

.. testcode::

    from turbohtml import Canonical

    svg = turbohtml.parse("<svg xlink:href='x'><g><rect></rect></g></svg>")
    group = svg.select_one("g")
    print(group.canonicalize())
    print(group.canonicalize(Canonical(exclusive=True)))

.. testoutput::

    b'<g xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><rect></rect></g>'
    b'<g xmlns="http://www.w3.org/2000/svg"><rect></rect></g>'

Under exclusive canonicalization, force an otherwise-dropped ancestor prefix back onto the apex with
``inclusive_ns_prefixes``:

.. testcode::

    print(group.canonicalize(Canonical(exclusive=True, inclusive_ns_prefixes=("xlink",))))

.. testoutput::

    b'<g xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><rect></rect></g>'

***************************
 Canonicalize a whole page
***************************

Canonicalizing the :class:`~turbohtml.Document` emits its root element (the document type declaration is dropped) plus
any comment or processing instruction outside it, each set off with a newline:

.. testcode::

    doc = turbohtml.parse("<!doctype html><!--sig--><p>body</p>")
    print(doc.canonicalize(Canonical(with_comments=True)))

.. testoutput::

    b'<!--sig-->\n<html><head></head><body><p>body</p></body></html>'

The bytes are UTF-8, so a non-ASCII character stays a literal multi-byte sequence rather than a numeric reference --
only ``&``, ``<``, ``>`` and the whitespace controls take character references. Hash the result directly; there is no
encoding step left to disagree on.
