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

Code fences use the first nonempty ``language-*`` class on the sole ``<code>`` child of ``<pre>``, then on ``<pre>``
itself. Class order and ASCII whitespace between classes do not affect recognition. A class that contains a backtick is
skipped, since a backtick fence cannot carry one in its language. Whitespace around the ``<code>`` child remains part of
the code content. ``Markdown.Code(language="python")`` supplies a fallback for blocks without a language class; an
element's class takes precedence. A fallback that contains a backtick is not written.

Inline CSS ``white-space: pre``, ``pre-wrap`` and ``break-spaces`` exports the element's text as a preformatted block,
preserving spaces, tabs and line breaks. ``pre-line`` keeps line breaks and collapses spaces and tabs. Descendants
inherit the mode and can override it with their own inline declaration. Styled prose carries no language label; existing
``<pre>`` blocks retain their code-language settings.

.. testcode::

    print(turbohtml.parse('<p style="white-space: pre-wrap">one\ntwo</p>').to_markdown())

.. testoutput::

    ```
    one
    two
    ```

Preformatted output preserves spacing at the cost of inline emphasis and link formatting inside that element. It also
turns a styled inline element into a block. Table cells, headings and link text use embedded ``<pre>`` HTML instead of
fences; character references keep those fragments on one Markdown source line. A fragment never starts a source line,
where a reader would take it for an HTML block: a hard break in front of it is written ``<br>``, and a setext heading
that begins with it is written as an ATX heading. Converter output that ends a line leaves the fragment at a line start,
so it is written as a ``<span style="white-space:pre">`` element instead. Markdown readers must allow embedded HTML to
display these fragments. This conversion considers inline declarations and their inheritance, without loading
stylesheets or reproducing browser line wrapping.

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

An ``<a>`` or ``<code>`` left unclosed is reopened by the parser in every block that follows it, and each copy carries
the attributes its one start tag spelled. The link target, title or fence language such a copy writes draws on a budget
that the start tag and the document's text fund, so a copy they cannot pay for keeps its text and drops the link or the
language. Content passed through as HTML draws on the same budget: there a copy keeps its tag and drops the attributes
the budget does not cover.

Emphasis around several blocks closes at the end of each block and reopens in the next, since a delimiter run pairs only
within one block. With more than eight runs open around a block, emphasis is written as HTML tags, and reopening them
draws on a budget that the document's text funds. Where deep nesting holds many blocks or table cells with little text,
each one keeps the outermost ``<strong>``, ``<em>`` or strikethrough run and drops the runs nested inside one of their
own kind, which reads the same.

Markdown indents nested lists and block quotes only so far: past eight levels of lists or seventeen block quotes, the
outermost list or quote that holds the deeper content is written as one raw HTML block, which a reader takes verbatim,
so the nesting survives. Each list item adds two levels and each block quote one, so mixed nesting reaches the limit
sooner.

A pipe table holds one line of inline content per cell, so a table or a list *inside* a cell has no Markdown spelling of
its own; the GFM spec is explicit that "block-level elements cannot be inserted in a table". Both keep their source HTML
without its comments, which is legal in a cell and renders as the real thing wherever a reader takes embedded HTML, and
a ``<br>`` in a cell stays a ``<br>`` for the same reason. Pass ``Markdown.Tables(cell_blocks="text")`` to flatten block
layout while keeping compact list markers:

.. testcode::

    nested = turbohtml.parse(
        "<table><tr><th>Item</th><th>Sizes</th></tr>"
        "<tr><td>Loaf</td><td><ul><li>small</li><li>large</li></ul></td></tr></table>"
    )
    print(nested.to_markdown())
    print(nested.to_markdown(Markdown(tables=Markdown.Tables(cell_blocks="text"))))

.. testoutput::
    :options: +NORMALIZE_WHITESPACE

    | Item | Sizes |
    | --- | --- |
    | Loaf | <ul><li>small</li><li>large</li></ul> |
    | Item | Sizes |
    | --- | --- |
    | Loaf | * small * large |

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

By default, prose escapes whatever a Markdown reader would act on, so the rendered page shows the source text. When the
output feeds something that reads it as plain text, such as a search index or a model prompt, those backslashes are
noise. ``Markdown.Escaping(mode="none")`` leaves prose as written; ``asterisks`` and ``underscores`` still decide ``*``
and ``_``, and a ``|`` inside a table cell keeps its backslash so the row holds together:

.. testcode::

    doc = turbohtml.parse("<p>See [22] and #3: 1. mix, 2. bake.</p>")
    print(doc.to_markdown())
    print(doc.to_markdown(Markdown(escaping=Markdown.Escaping(mode="none"))))

.. testoutput::

    See \[22\] and #3: 1. mix, 2. bake.
    See [22] and #3: 1. mix, 2. bake.

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
on its own line; any other tag flows inline. ``content`` arrives trimmed, and whitespace at the element's edges lands
outside the returned text, the way it moves outside a ``**`` marker: ``x<u> t </u>y`` with a converter returning
``f"<u>{content}</u>"`` gives ``x <u>t</u> y``. The callable runs inside the same per-tree lock the walk holds, so it
may read the element's attributes and subtree freely.

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
