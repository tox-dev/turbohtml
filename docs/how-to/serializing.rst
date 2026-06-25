#####################################
 Serialize, Markdown, and plain text
#####################################

************************
 Serialize with control
************************

:attr:`~turbohtml.Node.html` and :attr:`~turbohtml.Node.inner_html` are the default WHATWG-conformant forms (outer and
children-only). :meth:`~turbohtml.Node.serialize` adds control through a single :class:`~turbohtml.Html` configuration
object: its ``formatter`` field selects the escaping through :class:`~turbohtml.Formatter`, and its ``layout`` field
selects the whitespace. The default ``layout`` of ``None`` gives the compact form; an :class:`~turbohtml.Indent` (an int
for that many spaces, or a string used verbatim) switches to a pretty form that adds whitespace and so does not preserve
meaning. :meth:`~turbohtml.Node.encode` is the same but returns bytes, with the target encoding as its first argument:

.. testcode::

    import turbohtml
    from turbohtml import Formatter, Html, Indent

    card = turbohtml.parse("<div><p>café &amp; co</p></div>").select_one("div")
    print(card.inner_html)
    print(card.serialize(Html(formatter=Formatter.NAMED_ENTITIES)))
    print(card.serialize(Html(layout=Indent(2))))
    print(card.encode("ascii", Html(formatter=Formatter.NAMED_ENTITIES)))

.. testoutput::

    <p>café &amp; co</p>
    <div><p>caf&eacute; &amp; co</p></div>
    <div>
      <p>
        café &amp; co
      </p>
    </div>
    b'<div><p>caf&eacute; &amp; co</p></div>'

*******************
 Minify the output
*******************

Set the :class:`~turbohtml.Html` config's ``layout`` field to a :class:`~turbohtml.Minify` to shrink the markup. Every
transform is round-trip safe: the minified bytes reparse to the same tree, so minifying never changes meaning. The four
flags fold insignificant whitespace, omit the start/end tags the WHATWG rules make optional, drop redundant attribute
quotes, and strip comments; all default on. Because ``layout`` holds one mode, a :class:`~turbohtml.Minify` and an
:class:`~turbohtml.Indent` cannot be combined.

.. testcode::

    import turbohtml
    from turbohtml import Html, Minify

    doc = turbohtml.parse(
        "<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"
    )
    print(doc.serialize(Html(layout=Minify())))
    print(doc.serialize(Html(layout=Minify(omit_optional_tags=False, collapse_whitespace=False))))

.. testoutput::

    <title>Hi</title><p class=lead>one</p> <p>two
    <html><head><title>Hi</title></head><body><p class=lead>one</p>  <p>two</p></body></html>

Whitespace-significant elements (``pre``, ``textarea``, ``listing``) and raw-text elements (``script``, ``style``) keep
their content verbatim, and a tag is never dropped when omitting it would let the reparse reconstruct a formatting
element across the boundary.

When the input is a string rather than a built tree -- the ``minify-html`` and ``htmlmin`` use case --
:func:`turbohtml.clean.minify` parses and minifies in one call, taking the same :class:`~turbohtml.Minify` options:

.. testcode::

    from turbohtml.clean import minify

    print(minify("<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"))

.. testoutput::

    <title>Hi</title><p class=lead>one</p> <p>two

***********************************
 Normalize attributes and encoding
***********************************

Two more :class:`~turbohtml.Html` fields normalize the output without touching the tree, and compose with any
``formatter`` or ``layout``. ``sort_attributes`` emits each start tag's attributes in ascending name order, so two
serializations of equal trees diff cleanly:

.. testcode::

    import turbohtml
    from turbohtml import Html

    node = turbohtml.parse("<p id=main class=lead data-x=1>hi</p>").select_one("p")
    print(node.serialize(Html(sort_attributes=True)))

.. testoutput::

    <p class="lead" data-x="1" id="main">hi</p>

``meta_charset`` makes the document ``<head>`` declare the output encoding: an existing ``<meta charset>`` (or ``<meta
http-equiv="content-type">``) is normalized in place, and a head that declares none gets a ``<meta charset>`` injected
as its first child, never a duplicate. ``serialize`` declares ``utf-8`` (the encoding of the returned ``str``), while
``encode`` declares the encoding it writes:

.. testcode::

    import turbohtml
    from turbohtml import Html

    doc = turbohtml.parse("<title>Hi</title>")
    print(doc.serialize(Html(meta_charset=True)))
    print(doc.encode("iso-8859-1", Html(meta_charset=True)))

.. testoutput::

    <html><head><meta charset="utf-8"><title>Hi</title></head><body></body></html>
    b'<html><head><meta charset="iso-8859-1"><title>Hi</title></head><body></body></html>'

********************
 Export to Markdown
********************

:meth:`~turbohtml.Node.to_markdown` renders a node and its subtree as GitHub-Flavored Markdown (headings, lists, links,
emphasis, code, blockquotes, images, and pipe tables), collapsing runs of whitespace the way normal flow lays them out.
It is a one-call replacement for the ``scrape`` → ``Markdown`` step that html2text or markdownify would do, with no
second dependency and the whole walk in C:

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<h1>Recipe</h1><p>A <b>quick</b> loaf.</p>"
        "<ul><li>flour</li><li>water</li></ul>"
        "<blockquote><p>Rest 1 hour.</p></blockquote>"
    )
    print(page.to_markdown())

.. testoutput::

    # Recipe

    A **quick** loaf.

    - flour
    - water

    > Rest 1 hour.

Call it on any node to export just that subtree (``article.to_markdown()``). The output is opinionated GFM: ATX
headings, ``-`` bullets, fenced code blocks, inline links, and ``*``/``**`` emphasis.

A :class:`~turbohtml.Markdown` configuration object covers the markdownify and html2text surface, so a migration
reproduces the old output: setext headings, underscore emphasis, reference links, padded tables, alternate escaping, and
more. Its knobs are grouped into themed sub-configs (``Markdown.Headings``, ``Markdown.Inline``, ``Markdown.Links``,
...) so no single object is a wall of options. The :doc:`/migration/index` guide maps each old option to its turbohtml
field.

.. testcode::

    from turbohtml import Markdown

    doc = turbohtml.parse('<h2>Tea</h2><p><b>Steep</b> it. <a href="/x">More</a>.</p>')
    print(
        doc.to_markdown(
            Markdown(
                headings=Markdown.Headings(style="setext"),
                inline=Markdown.Inline(strong="__"),
                links=Markdown.Links(style="reference"),
            )
        )
    )

.. testoutput::

    Tea
    ---

    __Steep__ it. [More][1].

    [1]: /x

The ``Markdown.Wrapping`` sub-config shapes the result further. ``width`` word-wraps prose at a column (``0``, the
default, leaves paragraphs unwrapped), honoring list and blockquote indentation; ``list_items`` extends wrapping into
list items and ``links=False`` keeps a ``[text](url)`` construct on one line. ``Markdown.Images(mode="html")`` and
``Markdown.Tables(mode="html")`` pass the original ``<img>`` or ``<table>`` through verbatim, for readers that render
embedded HTML. ``Markdown.Document(transliterate=True)`` folds common non-ASCII typography in prose (smart quotes,
dashes, ellipsis, accented letters) to ASCII:

.. testcode::

    doc = turbohtml.parse("<p>The “quick” brown fox — jumps over the lazy dog today.</p>")
    print(doc.to_markdown(Markdown(wrapping=Markdown.Wrapping(width=30), document=Markdown.Document(transliterate=True))))

.. testoutput::

    The "quick" brown fox -- jumps
    over the lazy dog today.

To convert a Google Docs HTML export, use the ``Markdown.google_doc()`` preset (or set
``Markdown.GoogleDoc(enabled=True)``) so the inline-CSS styling it carries (font weight, font style, fixed-width fonts,
and ``margin-left`` list nesting) turns into Markdown:

.. testcode::

    export = '<p><span style="font-weight:700">Bold</span> and <span style="font-style:italic">soft</span>.</p>'
    print(turbohtml.parse(export).to_markdown(Markdown.google_doc()))

.. testoutput::

    **Bold** and *soft*.

When an option cannot express the rule you need (a custom element, or a tag that should render its own way), set the
``converters`` field: a mapping from a lowercased tag name to a ``callable(element, content) -> str``. The callable
receives the :class:`~turbohtml.Element` and the already-converted Markdown of its children, and returns the Markdown
for that element. A registered tag's built-in rendering is replaced; every other tag is untouched, and the hook costs
nothing when the mapping is omitted.

.. testcode::

    html = '<p>Watch <video src="/clip.mp4">a clip</video> and <abbr title="Markdown">MD</abbr>.</p>'
    converters = {
        "video": lambda el, content: f"[{content}]({el.attrs['src']})",
        "abbr": lambda el, content: f"{content} ({el.attrs['title']})",
    }
    print(turbohtml.parse(html).to_markdown(Markdown(converters=converters)))

.. testoutput::

    Watch [a clip](/clip.mp4) and MD (Markdown).

Return ``""`` to drop an element, or return ``content`` unchanged to unwrap it. A registered block-level tag is laid out
on its own line; any other tag flows inline. The callable runs inside the same per-tree lock the walk holds, so it may
read the element's attributes and subtree freely.

To unwrap whole tags without a callable, set the ``strip`` or ``convert`` field, the two mutually exclusive filters
``markdownify`` exposes under the same names (passing both raises ``ValueError`` when the ``Markdown`` is built).
``strip`` names tags whose markup is dropped while their text keeps flowing; ``convert`` names the only tags to keep
markup for, so every other tag is unwrapped. A name the tag table does not know is ignored, and ``<script>``,
``<style>``, and ``<head>`` still vanish whole regardless:

.. testcode::

    doc = turbohtml.parse('<p>see <a href="/x">the docs</a> and <b>note</b> this</p>')
    print(doc.to_markdown(Markdown(strip=["a"])))
    print(doc.to_markdown(Markdown(convert=["b"])))

.. testoutput::

    see the docs and **note** this
    see the docs and **note** this

**********************
 Export to plain text
**********************

:meth:`~turbohtml.Node.to_text` renders layout-aware plain text (the role `inscriptis
<https://github.com/weblyzard/inscriptis>`_ fills), keeping the visual structure rather than collapsing everything like
:attr:`~turbohtml.Node.text` does. Its most visible feature is laying tables out as aligned columns:

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<h2>Stock</h2>"
        "<table><tr><th>Item</th><th>Qty</th></tr>"
        "<tr><td>Apples</td><td>3</td></tr><tr><td>Pears</td><td>40</td></tr></table>"
    )
    print(page.to_text())

.. testoutput::

    Stock

    Item    Qty
    Apples  3
    Pears   40

Links are hidden by default; pass a :class:`~turbohtml.PlainText` config to change that — ``PlainText(links="inline")``
appends ``text (url)``, ``links="footnote"`` numbers the references, ``images=True`` shows alt text, and ``width``
word-wraps:

.. testcode::

    from turbohtml import PlainText

    doc = turbohtml.parse('<p>See <a href="/docs">the docs</a> for more.</p>')
    print(doc.to_text(PlainText(links="inline")))

.. testoutput::

    See the docs (/docs) for more.

********************************
 Label spans of the export text
********************************

To pull labeled regions out of the rendered text (the role inscriptis fills with ``annotation_rules``), call
:meth:`~turbohtml.Node.to_annotated_text` with a rule mapping. It returns the same text :meth:`~turbohtml.Node.to_text`
would, plus a list of ``(start, end, label)`` triples whose offsets index into that text, and it accepts a
:class:`~turbohtml.PlainText` config as its second argument just like :meth:`~turbohtml.Node.to_text`:

.. testcode::

    import turbohtml

    text, labels = turbohtml.parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text({
        "h1": ["heading"],
        "b": ["metric"],
    })
    print(text)
    for start, end, label in labels:
        print(label, "->", repr(text[start:end]))

.. testoutput::

    Q3

    Up 12% on the year.
    heading -> 'Q3'
    metric -> '12%'

A rule key is a tag (``"b"``), a ``tag#attr`` requiring the attribute, a ``tag#attr=value`` matching one
whitespace-separated token, or the tag-less ``#attr`` / ``#attr=value`` to match across any tag; its value is the list
of labels to attach.

**************************
 Extract the main article
**************************

:meth:`~turbohtml.Node.main_content` returns the dominant content element (the article body with the navigation,
sidebars, advertising and comment boilerplate scored out), so you can work on just the prose. It is the role
`resiliparse <https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ fills with its main-content extractor.
:meth:`~turbohtml.Node.main_text` is the shortcut that renders that element with :meth:`~turbohtml.Node.to_text`:

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<body>"
        "<nav><a href='/'>Home</a> <a href='/about'>About</a></nav>"
        "<article class='post'><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>The tail always points away from the Sun, pushed out by the solar wind and radiation.</p>"
        "</article>"
        "<aside class='sidebar'><p>Related: meteors, asteroids, the Oort cloud, and more links here.</p></aside>"
        "</body>"
    )
    print(page.main_content().tag)
    print(page.main_text())

.. testoutput::

    article
    Comets

    A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.

    The tail always points away from the Sun, pushed out by the solar wind and radiation.

The score is a content-density heuristic: long paragraphs with prose punctuation raise their container, while a class or
id like ``sidebar``, ``comment`` or ``nav`` lowers it or drops the subtree outright. A page with no real article (only
short snippets or pure navigation) yields ``None`` from ``main_content`` and ``""`` from ``main_text``, so guard the
result:

.. testcode::

    stub = turbohtml.parse("<nav><a href='/'>Home</a></nav>")
    print(stub.main_content())

.. testoutput::

    None

***********************************
 Extract the article with metadata
***********************************

:meth:`~turbohtml.Node.article` returns an :class:`~turbohtml.Article` record: the scored content element and its plain
text, plus the page metadata harvested beside it -- ``title``, ``byline``, ``date``, ``description`` and ``lang``. This
is the one call that replaces trafilatura or newspaper3k, and folds in the publication-date lookup (the htmldate use
case):

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<html lang='en'>"
        "<head><title>Comets — Astronomy Today</title>"
        "<meta property='og:description' content='A short guide to comets and their tails.'>"
        "<meta property='article:published_time' content='2024-05-06'></head>"
        "<body><article class='post'>"
        "<h1>Comets</h1>"
        "<p>By <a rel='author' href='/u/ada'>Ada Lovelace</a></p>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>The tail always points away from the Sun, pushed out by the solar wind and radiation.</p>"
        "</article></body></html>"
    )
    art = page.article()
    print(art.title)
    print(art.byline)
    print(art.date)
    print(art.description)
    print(art.lang)
    print(art.element.tag)

.. testoutput::

    Comets
    Ada Lovelace
    2024-05-06
    A short guide to comets and their tails.
    en
    article

Each field is harvested from the first source that supplies it, so a partial page still yields what it can. ``title``
prefers the first ``<h1>``, then ``og:title``, then ``<title>``; ``byline`` a ``rel="author"`` link, then a ``author``
meta, then ``article:author``; ``date`` a ``<time>`` (its ``datetime`` or text), then ``article:published_time``, then a
common date meta; ``description`` ``og:description`` then a ``description`` meta; and ``lang`` the ``<html lang>``
attribute. A field with no source is ``None``, and a page with no article body leaves ``element`` ``None`` and ``text``
empty while the metadata is still filled:

.. testcode::

    bare = turbohtml.parse("<html lang='fr'><head><title>Sommaire</title></head><body><p>x</p></body></html>")
    art = bare.article()
    print(art.element, repr(art.text), art.title, art.lang)

.. testoutput::

    None '' Sommaire fr

To turn those spans into something printable, pass the returned ``(text, labels)`` pair to one of the two output
processors. :func:`turbohtml.annotation_surface` groups each label's matched substrings into a dict, in document order,
the surface forms an NLP or information-extraction pipeline consumes:

.. testcode::

    import turbohtml

    text, labels = turbohtml.parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text({
        "h1": ["heading"],
        "b": ["metric"],
    })
    print(turbohtml.annotation_surface(text, labels))

.. testoutput::

    {'heading': ['Q3'], 'metric': ['12%']}

:func:`turbohtml.annotation_tags` weaves the spans back into the text as inline ``<label>...</label>`` markup. The
innermost span always closes first, so properly nested spans stay well-formed:

.. testcode::

    text, labels = turbohtml.parse("<p>a <b><i>both</i></b> c</p>").to_annotated_text({"b": ["bold"], "i": ["italic"]})
    print(turbohtml.annotation_tags(text, labels))

.. testoutput::

    a <italic><bold>both</bold></italic> c
