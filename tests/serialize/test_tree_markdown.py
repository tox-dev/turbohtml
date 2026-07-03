"""GitHub-Flavored-Markdown export via Node.to_markdown().

Two layers of validation: hand-written golden cases pin the exact output for
every mapping and edge case, and a round-trip differential renders the output
back to HTML with the markdown-it-py reference engine and asserts no visible
text token was lost — the same property the competitor suites (markdownify,
html2text) check by hand. The adversarial inputs are sampled from the vendored
html5lib-tests corpus, so to_markdown() is exercised on malformed markup too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

from turbohtml import parse


def md(html: str) -> str:
    return parse(html).to_markdown()


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<h1>One</h1>", "# One", id="h1"),
        pytest.param("<h2>Two</h2>", "## Two", id="h2"),
        pytest.param("<h3>Three</h3>", "### Three", id="h3"),
        pytest.param("<h4>Four</h4>", "#### Four", id="h4"),
        pytest.param("<h5>Five</h5>", "##### Five", id="h5"),
        pytest.param("<h6>Six</h6>", "###### Six", id="h6"),
        pytest.param("<h1>A</h1><h2>B</h2>", "# A\n\n## B", id="two-headings-blank-line"),
    ],
)
def test_headings(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>Hello world</p>", "Hello world", id="paragraph"),
        pytest.param("<p>one</p><p>two</p>", "one\n\ntwo", id="two-paragraphs"),
        pytest.param("<div>a</div><div>b</div>", "a\n\nb", id="divs"),
        pytest.param("<p>a\n  b\t c</p>", "a b c", id="collapse-whitespace"),
        pytest.param("<p>  leading trailing  </p>", "leading trailing", id="trim-edges"),
        pytest.param("<section><p>x</p></section>", "x", id="transparent-container"),
        pytest.param("<p>x<svg><style>.c{fill:red}</style></svg>y</p>", "xy", id="svg-style-suppressed"),
        pytest.param("<p>x<svg><script>a=1</script></svg>y</p>", "xy", id="svg-script-suppressed"),
    ],
)
def test_blocks_and_whitespace(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p><strong>s</strong></p>", "**s**", id="strong"),
        pytest.param("<p><b>b</b></p>", "**b**", id="b"),
        pytest.param("<p><em>e</em></p>", "*e*", id="em"),
        pytest.param("<p><i>i</i></p>", "*i*", id="i"),
        pytest.param("<p><del>d</del></p>", "~~d~~", id="del"),
        pytest.param("<p><s>s</s></p>", "~~s~~", id="s"),
        pytest.param("<p><strike>k</strike></p>", "~~k~~", id="strike"),
        pytest.param("<p>a <b>bold</b> b</p>", "a **bold** b", id="inline-in-text"),
        pytest.param("<p>a<b> bold </b>b</p>", "a **bold** b", id="chomp-inner-space"),
        pytest.param("<p>a<b></b>b</p>", "ab", id="empty-emphasis-dropped"),
        pytest.param("<p><em>x <strong>y</strong></em></p>", "*x **y***", id="nested-emphasis"),
        pytest.param("<p><span>plain</span></p>", "plain", id="span-transparent"),
        pytest.param("<p><mark>m</mark> <kbd>k</kbd></p>", "m k", id="passthrough-inline"),
    ],
)
def test_inline_emphasis(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<p><a href="http://x.com">link</a></p>', "[link](http://x.com)", id="link"),
        pytest.param('<p><a href="/p" title="T">l</a></p>', '[l](/p "T")', id="link-title"),
        pytest.param("<p><a>no href</a></p>", "no href", id="link-no-href"),
        pytest.param('<p><a href="a b">l</a></p>', "[l](<a b>)", id="link-space-url"),
        pytest.param(
            '<a href="http://x.com" title="The &quot;Goog&quot;">G</a>',
            '[G](http://x.com "The \\"Goog\\"")',
            id="link-title-escapes-quote",
        ),
        pytest.param('<a href="http://x"><div>hi</div></a>', "[hi](http://x)", id="block-in-anchor-flattens"),
        pytest.param(
            '<a href="http://x"><div><img src="http://y/i.png"></div></a>',
            "[![](http://y/i.png)](http://x)",
            id="block-image-in-anchor-flattens",
        ),
        pytest.param('<p><img src="i.png" alt="cat"></p>', "![cat](i.png)", id="image"),
        pytest.param("<p><img></p>", "![]()", id="image-empty"),
        pytest.param('<p><img src="a b.png" alt="x"></p>', "![x](<a b.png>)", id="image-space-url"),
        pytest.param('<img src="/i.jpg" alt="a]b">', "![a\\]b](/i.jpg)", id="image-alt-escapes-bracket"),
        pytest.param(
            '<img src="/i.jpg" alt="a[b\\c">', "![a\\[b\\\\c](/i.jpg)", id="image-alt-escapes-open-and-backslash"
        ),
        pytest.param('<a href="/u" title="a\\b">L</a>', '[L](/u "a\\\\b")', id="link-title-escapes-backslash"),
        pytest.param(
            '<img src="/i.jpg" alt="Alt text" title="Optional title">',
            '![Alt text](/i.jpg "Optional title")',
            id="image-title",
        ),
    ],
)
def test_links_and_images(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>say <code>x = 1</code></p>", "say `x = 1`", id="code-span"),
        pytest.param("<p><code>a`b</code></p>", "``a`b``", id="code-span-backtick"),
        pytest.param("<p><code>`edge`</code></p>", "`` `edge` ``", id="code-span-backtick-edge"),
        pytest.param("<pre><code>line1\nline2</code></pre>", "```\nline1\nline2\n```", id="pre-code"),
        pytest.param(
            '<pre><code class="language-python">x=1</code></pre>',
            "```python\nx=1\n```",
            id="pre-code-language",
        ),
        pytest.param("<pre>raw\ntext</pre>", "```\nraw\ntext\n```", id="pre-no-code"),
        pytest.param("<pre><code>a```b</code></pre>", "````\na```b\n````", id="pre-grows-fence"),
        pytest.param(
            "First <code>blah blah<br />blah blah</code> second",
            "First `blah blah blah blah` second",
            id="br-in-code-keeps-boundary",
        ),
        pytest.param(
            "<p>x <code>a<span>b</span><svg>s</svg><template>t</template><!--c-->d</code> y</p>",
            "x `abstd` y",
            id="code-span-flattens-nested-content",
        ),
    ],
)
def test_code(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<ul><li>a</li><li>b</li></ul>", "- a\n- b", id="ul"),
        pytest.param("<ol><li>a</li><li>b</li></ol>", "1. a\n2. b", id="ol"),
        pytest.param('<ol start="3"><li>a</li><li>b</li></ol>', "3. a\n4. b", id="ol-start"),
        pytest.param('<ol start="x"><li>a</li></ol>', "1. a", id="ol-start-invalid"),
        pytest.param("<menu><li>a</li></menu>", "- a", id="menu"),
        pytest.param(
            "<ul><li>a<ul><li>b</li></ul></li></ul>",
            "- a\n  - b",
            id="nested-ul",
        ),
        pytest.param(
            "<ol><li>a<ol><li>x</li><li>y</li></ol></li><li>b</li></ol>",
            "1. a\n   1. x\n   2. y\n2. b",
            id="nested-ol-aligned-indent",
        ),
        pytest.param(
            "<ul><li><ul><li>only</li></ul></li></ul>",
            "- \n  - only",
            id="nested-without-leading-text",
        ),
        pytest.param(
            "<ul><li>a</li><ul><li>b</li><li>c</li></ul><li>d</li></ul>",
            "- a\n  - b\n  - c\n- d",
            id="list-nested-directly-in-list",
        ),
        pytest.param(
            "<ol><li>a</li><ol><li>b</li></ol><menu><li>c</li></menu></ol>",
            "1. a\n   1. b\n   - c",
            id="ordered-and-menu-nested-directly-in-list",
        ),
        pytest.param("<ul><li><em>a</em> b <strong>c</strong></li></ul>", "- *a* b **c**", id="item-inline-run"),
        pytest.param(
            "<ul><li><p>first para</p><p>second para</p></li></ul>",
            "- first para\n\n  second para",
            id="loose-item-two-paragraphs",
        ),
        pytest.param(
            "<ol><li><p>first para</p><p>second para</p></li></ol>",
            "1. first para\n\n   second para",
            id="loose-ordered-item-two-paragraphs",
        ),
        pytest.param("<ul><li><p>solo</p></li></ul>", "- solo", id="single-paragraph-item-rides-marker"),
        pytest.param(
            "<ul><li><p>a</p><p>b</p></li><li>c</li></ul>",
            "- a\n\n  b\n\n- c",
            id="one-loose-item-makes-the-list-loose",
        ),
    ],
)
def test_lists(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<hr>", "---", id="hr"),
        pytest.param("<p>a</p><hr><p>b</p>", "a\n\n---\n\nb", id="hr-between"),
        pytest.param("<blockquote><p>q</p></blockquote>", "> q", id="blockquote"),
        pytest.param("<blockquote><p>a</p><p>b</p></blockquote>", "> a\n>\n> b", id="blockquote-two"),
        pytest.param(
            "<blockquote><blockquote><p>deep</p></blockquote></blockquote>",
            "> > deep",
            id="blockquote-nested",
        ),
        pytest.param("<p>a<br>b</p>", "a  \nb", id="br"),
    ],
)
def test_breaks_quotes_rules(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
            "| A | B |\n| --- | --- |\n| 1 | 2 |",
            id="table-basic",
        ),
        pytest.param(
            "<table><thead><tr><th>H</th></tr></thead><tbody><tr><td>v</td></tr></tbody></table>",
            "| H |\n| --- |\n| v |",
            id="table-thead-tbody",
        ),
        pytest.param(
            "<table><tr><td>a|b</td></tr><tr><td>c</td></tr></table>",
            "| a\\|b |\n| --- |\n| c |",
            id="table-pipe-escape",
        ),
        pytest.param(
            "<table><tr><td>x<br>y</td></tr><tr><td>z</td></tr></table>",
            "| x y |\n| --- |\n| z |",
            id="table-cell-newline-flattened",
        ),
    ],
)
def test_tables(html: str, expected: str) -> None:
    # the trailing " |" of each row carries one space; compare line-rstripped
    assert "\n".join(line.rstrip() for line in md(html).splitlines()) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>a*b_c[d]</p>", "a\\*b\\_c\\[d\\]", id="escape-inline-specials"),
        pytest.param("<p>back\\slash</p>", "back\\\\slash", id="escape-backslash"),
        pytest.param("<p># not a heading</p>", "\\# not a heading", id="escape-line-start-hash"),
        pytest.param("<p>- not a bullet</p>", "\\- not a bullet", id="escape-line-start-dash"),
        pytest.param("<p>&gt; not a quote</p>", "\\> not a quote", id="escape-line-start-gt"),
        pytest.param("<p>+ not a bullet</p>", "\\+ not a bullet", id="escape-line-start-plus"),
        pytest.param("<p>mid - dash stays</p>", "mid - dash stays", id="no-escape-mid-line-dash"),
    ],
)
def test_escaping(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("", "", id="empty-document"),
        pytest.param("<head><title>t</title></head><body>x</body>", "x", id="head-skipped"),
        pytest.param("<p>a</p><script>var x=1</script><p>b</p>", "a\n\nb", id="script-skipped"),
        pytest.param("<style>.x{}</style><p>b</p>", "b", id="style-skipped"),
        pytest.param("<!-- comment --><p>x</p>", "x", id="comment-skipped"),
        pytest.param("<p>a&amp;b</p>", "a&b", id="entity-decoded"),
    ],
)
def test_document_structure(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p><b>a<!--c-->b</b></p>", "**ab**", id="comment-inside-inline"),
        pytest.param("<p>a<wbr>b</p>", "ab", id="wbr-produces-nothing"),
        pytest.param("<p><b>two words here</b></p>", "**two words here**", id="multi-word-emphasis"),
        pytest.param("<p>a</p> <p>b</p>", "a\n\nb", id="whitespace-between-blocks"),
        pytest.param("<ul><li>x<!--c--></li></ul>", "- x", id="li-trailing-comment"),
        pytest.param("<ul><li></li></ul>", "-", id="li-empty"),
        pytest.param("<ul><li><!--c-->after</li></ul>", "- after", id="li-leading-comment"),
        pytest.param("<ul><li><script>s</script>after</li></ul>", "- after", id="li-leading-script"),
        pytest.param("<ul><li> <ul><li>x</li></ul></li></ul>", "- \n  - x", id="li-leading-ws-then-list"),
        pytest.param("<pre><code>line\n</code></pre>", "```\nline\n```", id="pre-trailing-newline"),
        pytest.param(
            '<pre><code class="highlight-xx">y</code></pre>',
            "```\ny\n```",
            id="pre-class-not-language",
        ),
        pytest.param(
            '<pre><code class="language-py more">z</code></pre>',
            "```py\nz\n```",
            id="pre-language-extra-class",
        ),
    ],
)
def test_edge_cases(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td></tr></table>",
            "| A | B |\n| --- | --- |\n| 1 |  |",
            id="ragged-row-padded",
        ),
        pytest.param(
            "<table><tr><!--c--><td>a</td></tr><tr><td>b</td></tr></table>",
            "| a |\n| --- |\n| b |",
            id="row-with-comment",
        ),
        pytest.param(
            "<table><!--c--><tr><td>a</td></tr><tr><td>b</td></tr></table>",
            "| a |\n| --- |\n| b |",
            id="table-with-comment",
        ),
    ],
)
def test_table_edge_cases(html: str, expected: str) -> None:
    assert "\n".join(line.rstrip() for line in md(html).splitlines()) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>a\tb\fc</p>", "a b c", id="tab-and-formfeed-collapse"),
        pytest.param("<p>x`y</p>", "x\\`y", id="escape-backtick-in-text"),
        pytest.param("<p>5</p>", "5", id="digits-to-end-not-a-list"),
        pytest.param("<p>5a more</p>", "5a more", id="digit-then-letter-not-a-list"),
        pytest.param("<p>3) item</p>", "3\\) item", id="escape-line-start-paren-number"),
        pytest.param('<ol start="10"><li>a</li></ol>', "10. a", id="ol-two-digit-number"),
        pytest.param('<ol start="2x"><li>a</li></ol>', "2. a", id="ol-start-digits-then-letter"),
        pytest.param('<ol start="-5"><li>a</li></ol>', "1. a", id="ol-start-negative-ignored"),
        pytest.param("<pre><svg></svg>code</pre>", "```\ncode\n```", id="pre-foreign-first-child"),
        pytest.param("<p><a href>x</a></p>", "x", id="link-valueless-href"),
        pytest.param("<p><code></code></p>", "``", id="code-span-empty"),
        pytest.param("<p><code>a`</code></p>", "`` a` ``", id="code-span-ends-with-backtick"),
        pytest.param("<p>before<template>t</template>after</p>", "beforetafter", id="template-inline-content"),
        pytest.param("<pre></pre>", "```\n\n```", id="pre-empty"),
        pytest.param("<pre><code></code></pre>", "```\n\n```", id="pre-empty-code"),
        pytest.param('<pre><code class="c">y</code></pre>', "```\ny\n```", id="pre-short-class"),
        pytest.param("<pre><code>a</code><code>b</code></pre>", "```\nab\n```", id="pre-code-with-sibling"),
        pytest.param("<pre><b>x</b></pre>", "```\nx\n```", id="pre-first-child-not-code"),
        pytest.param("<table></table>", "", id="table-empty"),
        pytest.param("<table><tr></tr></table>", "", id="table-row-no-cells"),
    ],
)
def test_more_edge_cases(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    "html",
    [
        pytest.param("<ul><li><svg></svg>x</li></ul>", id="foreign-leads-list-item"),
        pytest.param("<ul><svg></svg><li>a</li></ul>", id="foreign-in-list"),
        pytest.param("<table><tr><svg></svg><td>a</td></tr><tr><td>b</td></tr></table>", id="foreign-in-row"),
        pytest.param(
            "<table><tr><template>x</template><td>a</td></tr><tr><td>b</td></tr></table>",
            id="template-non-cell-in-row",
        ),
        pytest.param(
            "<table><caption>c</caption><thead><tr><th>h</th></tr></thead>"
            "<tbody><tr><td>a</td></tr></tbody><tfoot><tr><td>f</td></tr></tfoot></table>",
            id="full-table-sections",
        ),
        pytest.param("<ul><li>text<blockquote><p>q</p></blockquote></li></ul>", id="blockquote-in-tight-item"),
        pytest.param("<ul><li><h3>Sub</h3>more</li></ul>", id="heading-in-list-item"),
        pytest.param("<ul><li><p>a</p><p>b</p></li></ul>", id="two-paragraphs-in-item"),
    ],
)
def test_does_not_crash(html: str) -> None:
    # structural variants that exercise foreign-namespace and nesting branches
    assert isinstance(md(html), str)


def test_to_markdown_on_foreign_element() -> None:
    svg = parse("<p><svg><desc>caption</desc></svg></p>").find("svg")
    assert svg is not None
    assert svg.to_markdown() == "caption"


def test_to_markdown_on_template_content() -> None:
    template = parse("<template><p>inside</p></template>").find("template")
    assert template is not None
    assert template.to_markdown() == "inside"


def test_to_markdown_on_subtree() -> None:
    body = parse("<div><h1>T</h1><p>body</p></div>")
    heading = body.find("h1")
    assert heading is not None
    assert heading.to_markdown() == "# T"
    para = body.find("p")
    assert para is not None
    assert para.to_markdown() == "body"


def test_to_markdown_on_text_node() -> None:
    paragraph = parse("<p>just text</p>").find("p")
    assert paragraph is not None
    assert paragraph.children[0].to_markdown() == "just text"


def test_kitchen_sink() -> None:
    html = (
        "<h1>Title</h1>"
        "<p>Intro with <b>bold</b>, <i>italics</i>, <code>code</code> and "
        '<a href="http://e.com">a link</a>.</p>'
        "<h2>List</h2><ul><li>first</li><li>second<ul><li>nested</li></ul></li></ul>"
        "<blockquote><p>A quote.</p></blockquote>"
        '<pre><code class="language-c">int main(void);</code></pre>'
        "<table><tr><th>K</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>"
    )
    expected = (
        "# Title\n\n"
        "Intro with **bold**, *italics*, `code` and [a link](http://e.com).\n\n"
        "## List\n\n"
        "- first\n- second\n  - nested\n\n"
        "> A quote.\n\n"
        "```c\nint main(void);\n```\n\n"
        "| K | V |\n| --- | --- |\n| a | 1 |"
    )
    assert "\n".join(line.rstrip() for line in md(html).splitlines()) == expected


# --------------------------------------------------------- round-trip differential

_WORD = re.compile(r"[0-9a-z]+")


def _tokens(html: str) -> list[str]:
    """Visible-text word tokens, with block boundaries kept apart so packed and
    re-rendered markup tokenize the same way."""
    return _WORD.findall(" ".join(parse(html).stripped_strings).lower())


def _render(markdown: str) -> str:

    return MarkdownIt("gfm-like", {"linkify": False}).render(markdown)


@pytest.mark.parametrize(
    "html",
    [
        # cases mirroring the markdownify / html2text / CommonMark test suites
        pytest.param("<h1>Hello World</h1><p>Some <b>bold</b> text here.</p>", id="heading-para"),
        pytest.param('<p>A <a href="http://x">link</a> and <em>emphasis</em>.</p>', id="link-em"),
        pytest.param("<ul><li>apple</li><li>banana</li><li>cherry</li></ul>", id="bullet-list"),
        pytest.param('<ol start="2"><li>two</li><li>three</li></ol>', id="ordered-list"),
        pytest.param("<blockquote><p>quote one</p><p>quote two</p></blockquote>", id="blockquote"),
        pytest.param("<pre><code>code here\nsecond line</code></pre>", id="code-block"),
        pytest.param(
            "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>",
            id="table",
        ),
        pytest.param("<p>nested <em>em <strong>both</strong> end</em> tail</p>", id="nested-inline"),
        pytest.param("<p>a &amp; b &lt; c and <code>x | y</code></p>", id="entities-and-code"),
        pytest.param("<h3>Heading # with hash</h3><p>1. not a list</p>", id="escaping-needed"),
        pytest.param("<div><p>para</p><ul><li>one<ul><li>deep</li></ul></li></ul></div>", id="nested-structure"),
    ],
)
def test_roundtrip_preserves_text(html: str) -> None:
    rendered = _render(md(html))
    assert _tokens(rendered) == _tokens(html)


def _corpus_inputs() -> list[str]:
    """HTML fragments pulled from the vendored html5lib-tests tree-construction
    .dat files: small, deliberately malformed markup that stresses the walker."""
    root = Path(__file__).parents[1] / "html5lib-tests" / "tree-construction"
    inputs: list[str] = []
    for dat in sorted(root.glob("*.dat")):
        for block in dat.read_text(encoding="utf-8").split("#data\n")[1:]:
            data = block.split("\n#errors", 1)[0]
            if "�" not in data and len(data) < 200:
                inputs.append(data)
    return inputs[:400]


@pytest.mark.parametrize("html", _corpus_inputs())
def test_corpus_never_crashes_and_renders(html: str) -> None:
    result = md(html)
    assert isinstance(result, str)
    # whatever markup came in, the output is markdown a GFM engine can render
    assert isinstance(_render(result), str)
