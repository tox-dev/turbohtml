###########################
 Export a tree to Markdown
###########################

Turn a node into `GitHub-Flavored Markdown <https://github.github.com/gfm/>`_ with :meth:`~turbohtml.Node.to_markdown`,
so a scraping script ends with Markdown instead of tag soup.

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
