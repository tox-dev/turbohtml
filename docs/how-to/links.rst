########################
 Find and rewrite links
########################

***********************************************
 Enumerate and absolutize every link in a page
***********************************************

Iterating ``<a href>`` by hand misses the URLs in ``srcset``, a ``<meta refresh>`` redirect, and CSS
``url()``/``@import``. :meth:`~turbohtml.Node.links` finds them all, and :meth:`~turbohtml.Node.resolve_links` rewrites
them absolute against a base URL in place:

.. testcode::

    doc = turbohtml.parse('<p style="background:url(hero.png)"><a href="a/b.html">x</a></p>')
    doc.resolve_links("https://example.com/dir/")
    for link in doc.links():
        print(link.element.tag, link.attribute, link.url)

.. testoutput::

    p style https://example.com/dir/hero.png
    a href https://example.com/dir/a/b.html

For a one-off transform (rewriting a CDN host, signing URLs), pass a function to :meth:`~turbohtml.Node.rewrite_links`;
returning ``None`` leaves a link untouched.

*********************************
 Turn URLs and emails into links
*********************************

To linkify user-entered text the way `bleach.linkify <https://github.com/mozilla/bleach>`_ did, use
:func:`turbohtml.linkify.linkify`. It parses the HTML, so it links only in text the reader sees, never inside an
existing ``<a>``, a ``<script>``, or a tag you list in the ``Linkify.skip_tags`` field. Email autolinking is behind the
``Linkify.parse_email`` field because not every page wants it. The default ``nofollow`` callback marks web links, and
leaves a ``mailto:`` link alone:

.. testcode::

    from turbohtml.linkify import Linkify, linkify

    print(linkify("email bob@example.com or visit https://example.com", Linkify(parse_email=True)))

.. testoutput::

    email <a href="mailto:bob@example.com">bob@example.com</a> or visit <a href="https://example.com" rel="nofollow">https://example.com</a>

By default the callbacks only see freshly detected links; set the ``Linkify.process_existing`` field to ``True`` to also
run them over ``<a>`` tags already in the input. A callback reads ``link.existing`` to tell an author's anchor from a
detected one, and returning ``None`` for an existing anchor unwraps it to its text. Use the ``Linkify.extra_tlds`` field
to link bare domains on a private suffix the IANA table does not know, and ``Linkify.schemes`` to autolink only an
allowlist of explicit URL schemes:

.. testcode::

    from turbohtml.linkify import Link, Linkify, linkify


    def annotate(link: Link) -> Link:
        link.attrs["data-seen"] = "author" if link.existing else "auto"
        return link


    html = '<a href="https://docs.example">docs</a>, ping app.internal, skip ftp://x.example'
    config = Linkify(callbacks=[annotate], process_existing=True, extra_tlds=["internal"], schemes=["https"])
    print(linkify(html, config))

.. testoutput::

    <a href="https://docs.example" data-seen="author">docs</a>, ping <a href="http://app.internal" data-seen="auto">app.internal</a>, skip ftp://x.example

**************************
 Find links in plain text
**************************

When the text is not HTML and you only need *where* the links are (to highlight them, count them, or build your own
markup), use :class:`turbohtml.linkify.Detector`. ``find`` returns a :class:`~turbohtml.linkify.LinkSpan` per match,
with offsets, the matched text, and the normalized ``url``; ``has_link`` answers the yes/no question more cheaply:

.. testcode::

    from turbohtml.linkify import Detector

    detector = Detector()
    for span in detector.find("ping bob@example.com about example.com"):
        print(span.start, span.end, span.url)

.. testoutput::

    5 20 mailto:bob@example.com
    27 38 http://example.com

Register custom ``tlds`` to detect bare domains on an internal suffix, and scheme-less ``schemes`` such as ``tel`` so
their opaque URLs are found too (every ``scheme://`` URL is detected without registration):

.. testcode::

    detector = Detector(tlds=["corp"], schemes=["tel"])
    print([span.url for span in detector.find("wiki.corp or tel:+1-800-555-0100")])

.. testoutput::

    ['http://wiki.corp', 'tel:+1-800-555-0100']
