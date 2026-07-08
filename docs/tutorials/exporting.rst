###################
 Exporting to text
###################

Once you have the node you want, :meth:`~turbohtml.Node.to_markdown` turns it into `GitHub-Flavored Markdown
<https://github.github.com/gfm/>`_ in one call, so a scraping script ends with Markdown instead of a tag soup:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<article><h2>Tea</h2><p>Steep <em>green</em> tea for <b>3</b> minutes.</p></article>")
    print(doc.find("article").to_markdown())

.. testoutput::

    ## Tea

    Steep *green* tea for **3** minutes.

**********************
 Pull out the article
**********************

A real page wraps that prose in navigation, sidebars and footers. When you only want the article and do not know its
selector, :meth:`~turbohtml.Node.main_content` finds the dominant content element for you by scoring the tree, and
:meth:`~turbohtml.Node.main_text` hands you its text directly:

.. testcode::

    page = turbohtml.parse(
        "<nav><a href='/'>Home</a></nav>"
        "<main class='post'><h2>Tea</h2>"
        "<p>Steeping green tea for three minutes draws out its flavor without turning it bitter.</p></main>"
        "<footer>(c) 2026</footer>"
    )
    print(page.main_content().tag)
    print(page.main_text())

.. testoutput::

    main
    Tea

    Steeping green tea for three minutes draws out its flavor without turning it bitter.

********************
 Sanitize a snippet
********************

Export runs the other way too: when the HTML comes from someone else, clean it with :func:`turbohtml.clean.sanitize`
before you embed it. The default policy keeps a safe subset of tags, drops event handlers and dangerous URL schemes, and
escapes the elements it removes rather than discarding their text:

.. testcode::

    from turbohtml.clean import sanitize

    print(sanitize('<a href="javascript:alert(1)">x</a> <b onclick="y()">bold</b><script>bad()</script>'))

.. testoutput::

    <a>x</a> <b>bold</b>&lt;script&gt;bad()&lt;/script&gt;

*******************
 Shrink the output
*******************

When you serialize a page to ship it, pass a :class:`~turbohtml.Minify` layout to drop the whitespace, optional tags and
quotes the parser can put back. Hand its ``minify_js`` a :class:`~turbohtml.clean.JSMinify` and inline ``<script>``
JavaScript is minified in the same pass, with local names renamed and constants folded:

.. testcode::

    from turbohtml import Html, Minify
    from turbohtml.clean import JSMinify

    doc = turbohtml.parse('<p>Hi</p>  <script>function greet(who) { return "hi " + who; }</script>')
    print(doc.serialize(Html(layout=Minify(minify_js=JSMinify()))))

.. testoutput::

    <p>Hi</p> <script>function greet(a){return"hi "+a}</script>

**********************
 Emit well-formed XML
**********************

To hand the tree to an XML toolchain instead of a browser, set ``xml=True`` on the :class:`~turbohtml.Html` config.
Every empty element self-closes, foreign SVG and MathML subtrees carry their namespace declarations, and text and
attribute values follow the XML escaping rules, so the output parses with any XML reader:

.. testcode::

    from turbohtml import Html

    doc = turbohtml.parse("<p>a &amp; b<br><svg><circle r=5></circle></svg></p>")
    print(doc.find("p").serialize(Html(xml=True)))

.. testoutput::

    <p>a &amp; b<br/><svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg></p>

******************************
 Canonicalize for a signature
******************************

When a document has to hash to the same bytes on both sides of a signature, serialize it to Canonical XML with
:meth:`~turbohtml.Node.canonicalize` and a :class:`~turbohtml.Canonical` config. Attributes are reordered, redundant
namespace declarations are dropped, empty elements become start-end pairs, and character references are normalized, so
two trees with the same content produce identical bytes:

.. testcode::

    from turbohtml import Canonical

    doc = turbohtml.parse("<p z='1' a='2'>hi &amp; bye<br></p>")
    print(doc.find("p").canonicalize())
    print(doc.find("p").canonicalize(Canonical(exclusive=True)))

.. testoutput::

    b'<p a="2" z="1">hi &amp; bye<br></br></p>'
    b'<p a="2" z="1">hi &amp; bye<br></br></p>'

Those renderers all normalize the markup. When instead you want to edit one thing and leave the rest of the source
untouched, parse with ``source_locations=True`` and call :meth:`~turbohtml.Node.to_source`: it re-emits the verbatim
bytes of everything you did not change and reserializes only what you did.

.. testcode::

    doc = turbohtml.parse("<p class='lead'>Steep the <b>tea</b>.</p>", source_locations=True)
    doc.find("b").attrs["data-term"] = "1"
    print(doc.find("p").to_source())

.. testoutput::

    <p class='lead'>Steep the <b data-term="1">tea</b>.</p>

The single-quoted ``class`` and the unchanged text stay exactly as written; only the edited ``<b>`` tag rebuilds.

That is the whole tree API. Head to the :doc:`/how-to/index` guides for task-focused recipes, the
:doc:`/migration/index` guide if you are coming from another HTML library, or the :doc:`/reference` for the exact
signatures.
