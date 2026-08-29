########################
 Find and rewrite links
########################

Work with the URLs in a page: enumerate and absolutize every link, canonicalize a single URL, and turn bare URLs and
emails into anchors.

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

    from turbohtml.clean import LinkCandidate, Linkify, linkify


    def annotate(link: LinkCandidate) -> LinkCandidate:
        link.attrs["data-seen"] = "author" if link.existing else "auto"
        return link


    html = '<a href="https://docs.example">docs</a>, ping app.internal, skip ftp://x.example'
    config = Linkify(callbacks=[annotate], process_existing=True, extra_tlds=["internal"], schemes=["https"])
    print(linkify(html, config))

.. testoutput::

    <a href="https://docs.example" data-seen="author">docs</a>, ping <a href="http://app.internal" data-seen="auto">app.internal</a>, skip ftp://x.example

The native walk collects target references in document order. It copies each target's text and attributes under the tree
lock, releases the lock, then invokes Python. One :class:`~turbohtml.clean.Linker` can serve concurrent calls; candidate
mutations remain local to each call.

**************************
 Find links in plain text
**************************

When the text is not HTML and you only need *where* the links are (to highlight them, count them, or build your own
markup), use :class:`turbohtml.clean.LinkDetector`. ``find`` returns a :class:`~turbohtml.clean.LinkSpan` per match,
with offsets, the matched text, and the normalized ``url``; ``has_link`` answers the yes/no question more cheaply:

.. testcode::

    from turbohtml.clean import LinkDetector

    detector = LinkDetector()
    for span in detector.find("ping bob@example.com about example.com"):
        print(span.start, span.end, span.url)

.. testoutput::

    5 20 mailto:bob@example.com
    27 38 http://example.com

Register custom ``tlds`` to detect bare domains on an internal suffix, and ``schemes`` such as ``tel`` so their opaque
URLs are found too (a ``scheme://`` URL autolinks when its scheme is ``http``/``https``/``ftp`` or one you register, so
a typo scheme or a ``javascript://`` payload is left alone):

.. testcode::

    detector = LinkDetector(tlds=["corp"], schemes=["tel"])
    print([span.url for span in detector.find("wiki.corp or tel:+1-800-555-0100")])

.. testoutput::

    ['http://wiki.corp', 'tel:+1-800-555-0100']

********************
 Link phone numbers
********************

Phone numbers link when you pass a :class:`turbohtml.clean.PhoneNumbers` setting. ``regions`` is the ordered fallback
for numbers written without ``+`` (with an empty tuple, ``+`` numbers alone link), and the href is the number in E.164
form, with ``;ext=`` for an extension. Detection uses the numbering plans, so ``650-253-0000`` links for a US text while
``3/10/2011``, ``192.168.0.1`` and ``Order 12345`` stay plain:

.. testcode::

    from turbohtml.clean import Linkify, PhoneNumbers, linkify

    phones = PhoneNumbers(regions=("US", "GB"))
    print(linkify("Call 650-253-0000 or +44 20 7946 0958 x12, order 12345", Linkify(phones=phones)))

.. testoutput::

    Call <a href="tel:+16502530000">650-253-0000</a> or <a href="tel:+442079460958;ext=12">+44 20 7946 0958 x12</a>, order 12345

A callback reads ``link.phone``, a :class:`~turbohtml.clean.PhoneNumber` with the country code, national number, region
and :class:`~turbohtml.clean.PhoneType`, so it can route mobiles to ``sms:`` or drop premium-rate numbers; URLs and
emails carry ``phone=None``. :class:`~turbohtml.clean.LinkDetector` takes the same setting and puts the number on each
:class:`~turbohtml.clean.LinkSpan`:

.. testcode::

    from turbohtml.clean import LinkDetector

    detector = LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=False))
    for span in detector.find("try 555-123-4567 or 650-253-0000"):
        print(span.text, span.phone.international_number, span.phone.region, span.phone.type.value)

.. testoutput::

    555-123-4567 +15551234567 None unknown
    650-253-0000 +16502530000 US unknown

``require_valid=False`` links any number of a plausible length: the type is ``UNKNOWN`` and the region may be ``None``.
Use it for text with mistyped numbers; the default requires a number the plan assigns. See
:doc:`/explanation/phone-detection` for the rules.

``require_national_prefix=False`` links a number written without the national prefix its format writes (``20 7946 0958``
for a British text). :meth:`PhoneNumber.parse <turbohtml.clean.PhoneNumber.parse>` reads a string you already hold under
the same rule: it accepts words before the number, a ``tel:`` scheme, brackets and an extension, and raises
``ValueError`` when the string holds anything other than one number:

.. testcode::

    from turbohtml.clean import PhoneNumber

    number = PhoneNumber.parse("Tel: (650) 253-0000 ext. 7", regions=("US",))
    print(number.international_number, number.extension, number.type.value)
    try:
        PhoneNumber.parse("650-253-0000 or 650-253-0001", regions=("US",))
    except ValueError as error:
        print(error)

.. testoutput::

    +16502530000 7 fixed_line_or_mobile
    '650-253-0000 or 650-253-0001' is not a phone number

:meth:`PhoneNumber.format <turbohtml.clean.PhoneNumber.format>` writes the number in four layouts, chosen with
:class:`~turbohtml.clean.PhoneFormat`; the grouping and the extension marker come from the numbering plan of the
number's calling code, so a callback can put the international form in the link text or the national form in a
``title``:

.. testcode::

    from turbohtml.clean import PhoneFormat

    number = LinkDetector(phones=PhoneNumbers(regions=("GB",))).find("ring 020 7946 0958 x12")[0].phone
    for style in PhoneFormat:
        print(style.value, number.format(style))

.. testoutput::

    e164 +442079460958
    international +44 20 7946 0958 x12
    national 020 7946 0958 x12
    rfc3966 tel:+44-20-7946-0958;ext=12
