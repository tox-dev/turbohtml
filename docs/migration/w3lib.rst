#####################
 From w3lib (Scrapy)
#####################

`w3lib <https://github.com/scrapy/w3lib>`_ collects the web utilities Scrapy reuses. Only its ``w3lib.html`` text/entity
subset overlaps with turbohtml; the URL canonicalization, response-encoding, and HTTP helpers in ``w3lib.url`` and
elsewhere stay outside turbohtml's scope and have no equivalent here.

``replace_entities`` resolves character references the same way :func:`turbohtml.unescape` does, so it is a drop-in;
``w3lib.html.replace_entities("caf&eacute; &amp; co")`` returns the same string this prints:

.. testcode::

    from turbohtml import unescape
    print(unescape("caf&eacute; &amp; co"))

.. testoutput::

    café & co

The tag and comment strippers map onto parsing to a real tree and reading its text. ``remove_tags`` becomes
:func:`turbohtml.parse` followed by :attr:`~turbohtml.Node.text`, and ``remove_comments`` needs nothing extra because
comments never appear in ``text``:

.. testcode::

    from turbohtml import parse
    print(parse("<p>Tom &amp; Jerry <b>says</b> hi</p><!--note-->").text)

.. testoutput::

    Tom & Jerry says hi

The behavior differs in one way worth knowing before migrating: ``remove_tags`` strips angle brackets with a regular
expression and leaves entities encoded (``Tom &amp; Jerry``), while ``text`` runs the WHATWG tree builder and returns
decoded characters (``Tom & Jerry``). turbohtml parses malformed and nested markup the way a browser does rather than
matching ``<...>`` spans, so the two diverge on inputs a regex misreads. ``remove_tags_with_content``, which drops a tag
together with its subtree, has no single call: select the subtrees to keep with :meth:`~turbohtml.Node.find_all` and
join their ``text``, the allowlist-style filtering the :doc:`/how-to/index` guide covers, or reach for
``turbohtml.sanitizer`` when the goal is producing safe HTML rather than plain text.

The two helpers that read a document's own URL hints, ``get_base_url`` and ``get_meta_refresh``, map to the
:meth:`~turbohtml.Document.base_url` and :meth:`~turbohtml.Document.meta_refresh` methods on the parsed document. Each
takes the fallback base URL w3lib calls ``baseurl`` and resolves the hint against it:

.. testcode::

    from turbohtml import parse

    doc = parse('<base href="/sub/"><meta http-equiv=refresh content="5; url=next.html">')
    print(doc.base_url("http://site.com/"))
    print(doc.meta_refresh("http://site.com/"))

.. testoutput::

    http://site.com/sub/
    (5.0, 'http://site.com/next.html')
