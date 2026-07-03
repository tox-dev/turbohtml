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

******************************
 Clean and canonicalize a URL
******************************

To recognize two spellings of the same page -- for deduplication, cache keys, or a crawl frontier -- canonicalize them
with :func:`turbohtml.extract.normalize_url`. It applies the WHATWG URL standard's normalization (case, default ports,
``..`` segments, percent-encoding) and drops known tracking parameters, sorting the rest:

.. testcode::

    from turbohtml.extract import normalize_url

    print(normalize_url("HTTPS://Example.ORG:443/a/../page?utm_source=rss&b=2&a=1"))

.. testoutput::

    https://example.org/page?a=1&b=2

For URLs scraped out of markup, :func:`turbohtml.extract.clean_url` first scrubs HTML damage (stray whitespace,
``&amp;``, a truncating quote) and answers ``None`` for anything that is not a fetchable web URL, so a scraping pipeline
can filter and normalize in one call. :class:`turbohtml.extract.UrlCleaning` carries the knobs: a strict query-parameter
allowlist, trailing-slash folding, fragment stripping, and a URL-based language filter.
:func:`turbohtml.extract.extract_links` runs the whole pipeline over a page -- parse, collect anchors, resolve against
the base, clean, deduplicate:

.. testcode::

    from turbohtml.extract import extract_links

    page = '<a href="/a?utm_source=x">a</a> <a href="https://other.example/b">b</a>'
    print(sorted(extract_links(page, "https://site.example/")))

.. testoutput::

    ['https://other.example/b', 'https://site.example/a']

*********************************
 Turn URLs and emails into links
*********************************

To linkify user-entered text the way `bleach.linkify <https://github.com/mozilla/bleach>`_ did, use
:func:`turbohtml.clean.linkify`. It parses the HTML, so it links only in text the reader sees, never inside an existing
``<a>``, a ``<script>``, or a tag you list in the ``Linkify.skip_tags`` field. Email autolinking is behind the
``Linkify.parse_email`` field because not every page wants it. The default ``nofollow`` callback marks web links, and
leaves a ``mailto:`` link alone:

.. testcode::

    from turbohtml.clean import Linkify, linkify

    print(linkify("email bob@example.com or visit https://example.com", Linkify(parse_email=True)))

.. testoutput::

    email <a href="mailto:bob@example.com">bob@example.com</a> or visit <a href="https://example.com" rel="nofollow">https://example.com</a>

By default the callbacks only see freshly detected links; set the ``Linkify.process_existing`` field to ``True`` to also
run them over ``<a>`` tags already in the input. A callback reads ``link.existing`` to tell an author's anchor from a
detected one, and returning ``None`` for an existing anchor unwraps it to its text. Use the ``Linkify.extra_tlds`` field
to link bare domains on a private suffix the IANA table does not know, and ``Linkify.schemes`` to autolink only an
allowlist of explicit URL schemes:

.. testcode::

    from turbohtml.clean import Link, Linkify, linkify


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
markup), use :class:`turbohtml.clean.Detector`. ``find`` returns a :class:`~turbohtml.clean.LinkSpan` per match, with
offsets, the matched text, and the normalized ``url``; ``has_link`` answers the yes/no question more cheaply:

.. testcode::

    from turbohtml.clean import Detector

    detector = Detector()
    for span in detector.find("ping bob@example.com about example.com"):
        print(span.start, span.end, span.url)

.. testoutput::

    5 20 mailto:bob@example.com
    27 38 http://example.com

Register custom ``tlds`` to detect bare domains on an internal suffix, and ``schemes`` such as ``tel`` so their opaque
URLs are found too (a ``scheme://`` URL autolinks when its scheme is ``http``/``https``/``ftp`` or one you register, so
a typo scheme or a ``javascript://`` payload is left alone):

.. testcode::

    detector = Detector(tlds=["corp"], schemes=["tel"])
    print([span.url for span in detector.find("wiki.corp or tel:+1-800-555-0100")])

.. testoutput::

    ['http://wiki.corp', 'tel:+1-800-555-0100']
