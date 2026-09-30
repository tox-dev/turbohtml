"""GitHub-Flavored-Markdown export via Node.to_markdown().

Two layers of validation: hand-written golden cases pin the exact output for
every mapping and edge case, and a round-trip differential renders the output
back to HTML with the markdown-it-py reference engine and asserts no visible
text token was lost — the same property the competitor suites (markdownify,
html2text) check by hand. The adversarial inputs are sampled from the committed
WPT tree-construction corpus, so to_markdown() is exercised on malformed markup too.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import unquote

import pytest
from markdown_it import MarkdownIt

from turbohtml import parse, parse_fragment

if TYPE_CHECKING:
    from wpt_tree_corpus import WptHtmlTreeCorpus
from types import MappingProxyType
from typing import Final

from turbohtml import Element, Markdown, Text

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pytest_mock import MockerFixture

    Converter = Callable[[Element, str], str]
if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from turbohtml import Document
from turbohtml import PlainText

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node


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
        # &#13; injects a real U+000D past the CR->LF preprocessor fold; CR is HTML
        # whitespace, so it collapses and trims like the rest of the set.
        pytest.param("<p>a&#13;&#13;b</p>", "a b", id="collapse-carriage-return"),
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
        pytest.param("<p><mark>m</mark></p>", "m", id="passthrough-inline"),
    ],
)
def test_inline_emphasis(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize("tag", ["code", "kbd", "samp"])
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("plain", "`plain`", id="plain"),
        pytest.param("`edge`", "`` `edge` ``", id="backtick-edges"),
        pytest.param("a``b", "```a``b```", id="backtick-run"),
        pytest.param("a<br>b", "`a b`", id="line-break"),
        pytest.param("", "", id="empty"),
        pytest.param("<b>x</b>", "`x`", id="nested-markup"),
    ],
)
def test_code_elements_preserve_text(tag: str, content: str, expected: str) -> None:
    assert md(f"<{tag}>{content}</{tag}>") == expected


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
        pytest.param("x<a href='h'> t </a>y", "x [t](h) y", id="link-edge-spaces-move-outside"),
        pytest.param("x<a href='h'><b> t</b></a>y", "x [**t**](h)y", id="link-space-inside-emphasis-moves-outside"),
        pytest.param("<a href='h'> t</a>", "[t](h)", id="link-leading-space-at-line-start-dropped"),
        pytest.param("x<a href='h'></a>y", "x[](h)y", id="link-empty-text-keeps-brackets"),
        pytest.param("x<b><a href='h'> t</a></b>", "x **[t](h)**", id="link-inside-emphasis-opens-both"),
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
        pytest.param("<code><ul><li>one</li><li>two</li></ul></code>", "`one two`", id="code-list-boundaries"),
        pytest.param("<code><p>one</p><p>two</p></code>", "`one two`", id="code-paragraph-boundaries"),
        pytest.param("<code>before<p>inside</p>after</code>", "`before inside after`", id="code-block-edges"),
        pytest.param("<code>before <p>after</p></code>", "`before after`", id="code-existing-space-before-block"),
        pytest.param("<code>before<p> after</p></code>", "`before after`", id="code-existing-space-in-block"),
        pytest.param("<code>before\t<p>after</p></code>", "`before\tafter`", id="code-existing-tab-before-block"),
        pytest.param("<code>before<p>\tafter</p></code>", "`before\tafter`", id="code-existing-tab-in-block"),
        pytest.param("<code>before<br><p>after</p></code>", "`before after`", id="code-break-before-block"),
        pytest.param(
            "<pre><code><ul><li>one</li><li>two</li></ul></code></pre>",
            "```\none\ntwo\n```",
            id="pre-code-list-boundaries",
        ),
        pytest.param("<pre><code><p>one</p><p>two</p></code></pre>", "```\none\ntwo\n```", id="pre-code-paragraphs"),
        pytest.param(
            "<pre><code>before\n<p>after</p></code></pre>", "```\nbefore\nafter\n```", id="pre-existing-newline"
        ),
        pytest.param(
            "<pre><code>before<p>\nafter</p></code></pre>", "```\nbefore\nafter\n```", id="pre-newline-in-block"
        ),
        pytest.param(
            "<pre><code>before <p>after</p></code></pre>", "```\nbefore after\n```", id="pre-space-before-block"
        ),
        pytest.param("<pre><code>a<br>b</code></pre>", "```\nab\n```", id="pre-code-break"),
        pytest.param("<pre><code>a<span>b</span>c</code></pre>", "```\nabc\n```", id="pre-code-inline-adjacent"),
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


def test_code_empty_text_keeps_block_boundary() -> None:
    code: Final = Element("code", children=[Element("p", children=[Text("one")]), Text(""), Text("two")])
    assert Element("main", children=[code]).to_markdown() == "`one two`"


_LOOSE_NAV_HTML: Final[str] = (
    '<ol start="13"><li>Before<p>More</p></li><li><nav><h4>Pagination</h4>'
    '<ul><li><a href="/next">Next</a></li></ul></nav></li><li>After</li></ol>'
)


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
        pytest.param("<ul><li><h2>x</h2><p>y</p></li></ul>", "- ## x\n  y", id="heading-on-item-marker"),
        pytest.param(
            _LOOSE_NAV_HTML,
            "13. Before\n\n    More\n\n14. #### Pagination\n\n    - [Next](/next)\n\n15. After",
            id="loose-item-heading-through-nav",
        ),
        pytest.param(
            '<ol start="13"><li>Before<p>More</p></li><li><nav></nav><section><h4>Pagination</h4></section>'
            '<ul><li><a href="/next">Next</a></li></ul></li><li>After</li></ol>',
            "13. Before\n\n    More\n\n14. #### Pagination\n\n    - [Next](/next)\n\n15. After",
            id="empty-wrapper-before-heading",
        ),
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
        pytest.param("<ul><div>x</div><li>y</li></ul>", "x\n\n- y", id="content-before-first-item"),
        pytest.param("<ul><li>a</li>tail<li>b</li></ul>", "- a\n  tail\n- b", id="text-between-items"),
        pytest.param("<ul><li>a</li><p>para</p><li>b</li></ul>", "- a\n  para\n- b", id="block-between-items"),
        pytest.param(
            "<ul><li><p>a</p><p>b</p></li><div>x</div><li>c</li></ul>",
            "- a\n\n  b\n\n  x\n\n- c",
            id="block-between-loose-items",
        ),
        pytest.param(
            "<blockquote><ul><li>a</li>tail</ul></blockquote>", "> - a\n>   tail", id="text-after-quoted-item"
        ),
        pytest.param("<ul><div><li>z</li></div></ul>", "- z", id="item-inside-wrapper"),
        pytest.param(
            "<ol><div><li>a</li><li>b</li></div><li>c</li></ol>",
            "1. a\n2. b\n3. c",
            id="wrapped-items-keep-numbering",
        ),
        pytest.param(
            "<ul><div><li><p>a</p><p>b</p></li></div><li>c</li></ul>",
            "- a\n\n  b\n\n- c",
            id="loose-item-inside-wrapper",
        ),
        pytest.param(
            "<ul><div><span>s</span><svg></svg></div><li>a</li></ul>",
            "s\n\n- a",
            id="wrapper-without-items-is-content",
        ),
        pytest.param("<ul><li>a</li><svg><text>t</text></svg></ul>", "- a\n  t", id="svg-after-item"),
        pytest.param("<ul><li>a</li></ul><ul><li>b</li></ul>", "- a\n* b", id="adjacent-bullet-lists-stay-apart"),
        pytest.param("<ol><li>a</li></ol><ol><li>b</li></ol>", "1. a\n1) b", id="adjacent-ordered-lists-stay-apart"),
        pytest.param(
            "<ol><li>a</li></ol> <ol><li>b</li></ol><ol><li>c</li></ol>",
            "1. a\n1) b\n1. c",
            id="three-adjacent-lists-alternate",
        ),
        pytest.param(
            "<ul><li>a</li></ul><ul></ul><ul><li>b</li></ul>",
            "- a\n* b",
            id="empty-list-between-adjacent-lists",
        ),
        pytest.param("<ul></ul><ul><li>a</li></ul>", "- a", id="list-after-empty-list"),
        pytest.param("<ul><li>a</li></ul><ol><li>b</li></ol>", "- a\n1. b", id="bullet-then-ordered-keeps-markers"),
        pytest.param(
            "<ul><li>a</li></ul><p>x</p><ul><li>b</li></ul>",
            "- a\n\nx\n\n- b",
            id="lists-split-by-a-paragraph-keep-markers",
        ),
        pytest.param(
            "<ul><li>a<ul><li>b</li></ul><ul><li>c</li></ul></li></ul>",
            "- a\n  - b\n  * c",
            id="adjacent-sublists-stay-apart",
        ),
        pytest.param(
            "<ul><li>a<ul><li>b</li></ul></li></ul><ul><li>c</li></ul>",
            "- a\n  - b\n* c",
            id="list-after-a-list-ending-in-a-sublist",
        ),
        pytest.param(
            "<blockquote><ul><li>a</li></ul></blockquote><ul><li>b</li></ul>",
            "> - a\n- b",
            id="list-after-a-quoted-list-keeps-its-marker",
        ),
    ],
)
def test_lists(html: str, expected: str) -> None:
    assert md(html) == expected


def test_loose_list_nested_heading_and_link_stay_structural() -> None:
    rendered: Final = parse(MarkdownIt("gfm-like", {"linkify": False}).render(md(_LOOSE_NAV_HTML)))
    assert (
        len(rendered.select("ol > li")),
        [(node.tag, node.text, node.attr("href")) for node in rendered.select("h4, a")],
    ) == (
        3,
        [("h4", "Pagination", None), ("a", "Next", "/next")],
    )


@pytest.mark.parametrize(
    ("html", "selector", "expected"),
    [
        pytest.param("<li>orphan</li>", "li", "- orphan", id="standalone"),
        pytest.param("<ul><li>a</li><li>b</li></ul>", "li:last-child", "- b", id="unordered-second"),
        pytest.param("<ol><li>a</li><li>b</li></ol>", "li:last-child", "2. b", id="ordered-second"),
        pytest.param('<ol start="3"><li>a</li><li>b</li></ol>', "li:last-child", "4. b", id="ordered-start"),
        pytest.param('<ol start=" +3"><li>a</li><li>b</li></ol>', "li:last-child", "4. b", id="signed-start"),
        pytest.param('<ol start="   +3"><li>a</li><li>b</li></ol>', "li:last-child", "4. b", id="spaced-start"),
        pytest.param('<ol start="   "><li>a</li></ol>', "li", "1. a", id="whitespace-start"),
        pytest.param('<ol start="+"><li>a</li></ol>', "li", "1. a", id="sign-only-start"),
        pytest.param('<ol start="-3"><li>a</li></ol>', "li", "1. a", id="negative-start-fallback"),
        pytest.param("<ol reversed><li>a</li><li>b</li></ol>", "li:last-child", "2. b", id="reversed-ascending"),
        pytest.param('<ol><li value="8">a</li><li>b</li></ol>', "li:last-child", "9. b", id="ordered-value"),
        pytest.param('<ol><li value="8">a</li></ol>', "li", "8. a", id="selected-value"),
        pytest.param('<ol><li value="+8">a</li></ol>', "li", "8. a", id="signed-value"),
        pytest.param('<ol><li value="8x">a</li></ol>', "li", "8. a", id="value-with-suffix"),
        pytest.param('<ol><li value="8/">a</li></ol>', "li", "8. a", id="value-with-punctuation"),
        pytest.param('<ol><li value="-8">a</li></ol>', "li", "1. a", id="negative-value-fallback"),
        pytest.param(
            '<ol start="3"><div><li>a</li><li value="8">b</li></div><li>c</li></ol>',
            "ol > li",
            "9. c",
            id="wrapped-preceding-items",
        ),
        pytest.param(
            '<ol start="3"><li>a</li><div><li>b</li></div></ol>',
            "div > li",
            "4. b",
            id="selected-wrapper-item",
        ),
        pytest.param('<ol><li value="x">a</li></ol>', "li", "1. a", id="invalid-value"),
        pytest.param('<ol><li value="999999999999999999999999999999">a</li></ol>', "li", "1. a", id="overflow-value"),
        pytest.param(
            '<ol><li value="9223372036854775807">a</li></ol>',
            "li",
            "9223372036854775807. a",
            id="maximum-value",
        ),
        pytest.param(
            '<ol><li value="9223372036854775807">a</li><li>b</li></ol>',
            "li:last-child",
            "9223372036854775807. b",
            id="maximum-preceding-value",
        ),
        pytest.param('<ol><li value="">a</li></ol>', "li", "1. a", id="empty-value"),
        pytest.param(
            '<ol><li><h2><a href="https://example.com">Result</a></h2><p>Summary</p></li></ol>',
            "li",
            "1. ## [Result](https://example.com)\n   Summary",
            id="bing-heading",
        ),
        pytest.param("<ul><li>a<ul><li>b</li></ul></li></ul>", "ul ul li", "- b", id="nested"),
        pytest.param("<menu><li>a</li></menu>", "li", "- a", id="menu"),
        pytest.param("<ol><li>a</li>\n<li>b</li></ol>", "li:last-child", "2. b", id="text-between-items"),
        pytest.param("<ol><div>x</div><li>b</li></ol>", "li", "1. b", id="non-item-wrapper"),
        pytest.param("<ol><svg></svg><li>b</li></ol>", "li", "1. b", id="foreign-sibling"),
        pytest.param(
            "<ul><li><blockquote><p>x</p></blockquote></li></ul>",
            "li",
            "- \n  > x",
            id="leading-blockquote",
        ),
        pytest.param(
            "<svg><foreignObject><ol><li>b</li></ol></foreignObject></svg>",
            "li",
            "1. b",
            id="list-below-foreign-ancestor",
        ),
    ],
)
def test_selected_list_item(html: str, selector: str, expected: str) -> None:
    item = parse_fragment(html).select_one(selector)
    assert item is not None
    assert item.to_markdown() == expected


def test_detached_list_item() -> None:
    assert Element("li", children=[Text("x")]).to_markdown() == "- x"


def test_list_item_below_foreign_parent() -> None:
    parent = parse_fragment("<svg></svg>").select_one("svg")
    assert parent is not None
    item = Element("li", children=[Text("x")])
    parent.append(item)
    assert item.to_markdown() == "- x"


def test_ordered_list_item_value() -> None:
    assert md('<ol start="3"><li>a</li><li value="8">b</li><li>c</li></ol>') == "3. a\n8. b\n9. c"


def test_selected_nested_item_uses_list_depth() -> None:
    item = parse_fragment("<ul><li>a<ul><li>b</li></ul></li></ul>").select_one("ul ul li")
    assert item is not None
    assert item.to_markdown(Markdown(lists=Markdown.Lists(bullets="-*"))) == "* b"


def test_selected_item_keeps_nested_list_depth() -> None:
    item = parse_fragment("<ul><li>a<ul><li>b</li></ul></li></ul>").select_one("ul > li")
    assert item is not None
    assert item.to_markdown(Markdown(lists=Markdown.Lists(bullets="-*"))) == "- a\n  * b"


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
            "| x<br>y |\n| --- |\n| z |",
            id="table-cell-break-kept-as-html",
        ),
        pytest.param(
            "<table><tr><td>a<table><tr><td>b</td></tr></table></td><td>c</td></tr></table>",
            "| a<table><tr><td>b</td></tr></table> | c |\n| --- | --- |",
            id="table-nested-becomes-cell-html",
        ),
        pytest.param(
            "<table><tr><td><ul><li>x</li><li>y</li></ul></td></tr><tr><td>z</td></tr></table>",
            "| <ul><li>x</li><li>y</li></ul> |\n| --- |\n| z |",
            id="table-list-in-cell-becomes-html",
        ),
        pytest.param(
            "<table><tr><td><table><tr><td>a|b</td></tr></table></td></tr></table>",
            "| <table><tr><td>a\\|b</td></tr></table> |\n| --- |",
            id="table-pipe-inside-nested-escaped-once",
        ),
        pytest.param(
            "<table><tr><td><code>a|b</code></td></tr><tr><td>c</td></tr></table>",
            "| `a\\|b` |\n| --- |\n| c |",
            id="table-pipe-in-code-span-escaped",
        ),
        pytest.param(
            "<table><caption>Sales <b>2023</b></caption><tr><th>a</th></tr><tr><td>1</td></tr></table>",
            "Sales **2023**\n\n| a |\n| --- |\n| 1 |",
            id="table-caption-above-grid",
        ),
        pytest.param(
            "<table><tr><th>a</th></tr><caption>late</caption></table>",
            "late\n\n| a |\n| --- |",
            id="table-caption-after-rows-still-above",
        ),
        pytest.param(
            "<p>x</p><table><caption><p>one</p><p>two</p></caption><tr><td>1</td></tr></table>",
            "x\n\none\n\ntwo\n\n| 1 |\n| --- |",
            id="table-caption-with-blocks",
        ),
        pytest.param("<table><caption>only</caption></table>", "only", id="table-caption-without-rows"),
        pytest.param("<table><tr><td><p>A</p></td></tr></table>", "| A |\n| --- |", id="table-block-cell"),
        pytest.param(
            "<table><tr><td> <p>A</p> <p>B</p> </td></tr></table>",
            "| A B |\n| --- |",
            id="table-blocks-in-cell-join-with-one-space",
        ),
        pytest.param(
            "<table><tr><td>a</td></tr><tr><td>b</td><td>c</td></tr></table>",
            "| a |  |\n| --- | --- |\n| b | c |",
            id="table-short-row-padded-with-empty-cells",
        ),
    ],
)
def test_tables(html: str, expected: str) -> None:
    assert md(html) == expected


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


_COMMONMARK_TEXT_CASES: Final = [
    pytest.param("<ul><li>1. x</li></ul>", "- 1\\. x", id="li-ordered-marker"),
    pytest.param("<ul><li>2) x</li></ul>", "- 2\\) x", id="li-paren-marker"),
    pytest.param("<ol><li>1) x</li></ol>", "1. 1\\) x", id="ol-paren-marker"),
    pytest.param("<ul><li>- inner</li></ul>", "- \\- inner", id="li-dash"),
    pytest.param("<ul><li>+ x</li></ul>", "- \\+ x", id="li-plus"),
    pytest.param("<ul><li># h</li></ul>", "- \\# h", id="li-hash"),
    pytest.param("<ol><li>&gt; q</li></ol>", "1. \\> q", id="li-gt"),
    pytest.param("<ul><li><p>- b</p></li></ul>", "- \\- b", id="li-paragraph-dash"),
    pytest.param("<ul><li>a<ul><li>- b</li></ul></li></ul>", "- a\n  - \\- b", id="nested-li-dash"),
    pytest.param("<blockquote><p>1. x</p></blockquote>", "> 1\\. x", id="blockquote-ordered-marker"),
    pytest.param("<p>~~x~~</p>", "\\~\\~x\\~\\~", id="double-tilde"),
    pytest.param("<p>~x~</p>", "\\~x\\~", id="single-tilde"),
    pytest.param("<p>~~~</p>", "\\~\\~\\~", id="tilde-fence"),
    pytest.param("<p>&lt;div&gt;</p>", "\\<div>", id="lt-tag"),
    pytest.param("<p>&lt;/p&gt;</p>", "\\</p>", id="lt-closing-tag"),
    pytest.param("<p>&lt;!-- c --&gt;</p>", "\\<!-- c -->", id="lt-comment"),
    pytest.param("<p>&lt;http://x.com&gt;</p>", "\\<http://x.com>", id="lt-autolink"),
    pytest.param("<p>a&lt;b</p>", "a\\<b", id="lt-mid-word"),
    pytest.param("<p>a &lt; b</p>", "a < b", id="lt-before-space"),
    pytest.param("<p>a&lt;<b>x</b></p>", "a\\<**x**", id="lt-at-run-end"),
    pytest.param("<p>&amp;amp;</p>", "\\&amp;", id="amp-named-reference"),
    pytest.param("<p>a&amp;#65;b</p>", "a\\&#65;b", id="amp-decimal-reference"),
    pytest.param("<p>&amp;#x41;</p>", "\\&#x41;", id="amp-hex-reference"),
    pytest.param("<p>&amp;#X41;</p>", "\\&#X41;", id="amp-upper-hex-reference"),
    pytest.param("<p>a &amp; b</p>", "a & b", id="amp-before-space"),
    pytest.param("<p>&amp;;</p>", "&;", id="amp-empty-name"),
    pytest.param("<p>&amp;#</p>", "&#", id="amp-hash-at-end"),
    pytest.param("<p>AT&amp;T</p>", "AT&T", id="amp-name-at-end"),
    pytest.param("<p>&amp;x y;</p>", "&x y;", id="amp-name-without-semicolon"),
    pytest.param("<h1>a # b #</h1>", "# a # b \\#", id="heading-trailing-hash"),
    pytest.param("<h2>x ##</h2>", "## x \\##", id="heading-trailing-hash-run"),
    pytest.param("<h1>#</h1>", "# \\#", id="heading-only-hash"),
    pytest.param("<h1>a#</h1>", "# a#", id="heading-hash-after-word"),
    pytest.param("<h1></h1>", "#", id="heading-empty"),
    pytest.param("<p>a<br>===</p>", "a  \n\\===", id="setext-equals-after-break"),
    pytest.param("<p>a<br>---</p>", "a  \n\\---", id="setext-dash-after-break"),
]


@pytest.mark.parametrize(("html", "expected"), _COMMONMARK_TEXT_CASES)
def test_escaping_commonmark(html: str, expected: str) -> None:
    assert md(html) == expected


def _visible(html: str) -> str:
    return "".join(parse(html).text.split())


@pytest.mark.parametrize(("html", "expected"), _COMMONMARK_TEXT_CASES)
def test_escaping_commonmark_renders_source_text(html: str, expected: str) -> None:
    assert _visible(_render(expected)) == _visible(html)


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>a ← b</p>", "a <- b", id="arrow-before-space"),
        pytest.param("<p>a ←b</p>", "a \\<-b", id="arrow-before-word"),
        pytest.param("<p>a ←</p>", "a \\<-", id="arrow-at-run-end"),
        pytest.param("<p>a • b</p>", "a \\* b", id="bullet-folds-to-escaped-asterisk"),
    ],
)
def test_escaping_transliterated(html: str, expected: str) -> None:
    assert parse(html).to_markdown(Markdown(document=Markdown.Document(transliterate=True))) == expected


def test_escaping_arrow_kept_without_transliteration() -> None:
    assert md("<p>a ←b</p>") == "a ←b"


_NO_ESCAPING: Final[Markdown] = Markdown(escaping=Markdown.Escaping(mode="none"))


@pytest.mark.parametrize(
    ("html", "options", "expected"),
    [
        pytest.param("<p>see [22] a~b `c` \\ x</p>", _NO_ESCAPING, "see [22] a~b `c` \\ x", id="punctuation"),
        pytest.param("<p>&amp;copy; &lt;b&gt;</p>", _NO_ESCAPING, "&copy; <b>", id="reference-and-tag-shapes"),
        pytest.param("<p>- a</p><p>1. b</p><p># c</p>", _NO_ESCAPING, "- a\n\n1. b\n\n# c", id="line-start-markers"),
        pytest.param("<h2>Issue #</h2>", _NO_ESCAPING, "## Issue #", id="heading-closing-hashes"),
        pytest.param("<p>a*b_c</p>", _NO_ESCAPING, "a\\*b\\_c", id="asterisks-and-underscores-still-escaped"),
        pytest.param(
            "<p>a*b_c</p>",
            Markdown(escaping=Markdown.Escaping(mode="none", asterisks=False, underscores=False)),
            "a*b_c",
            id="asterisks-and-underscores-off",
        ),
        pytest.param(
            "<table><tr><td>a|b [x]</td></tr></table>", _NO_ESCAPING, "| a\\|b [x] |\n| --- |", id="cell-pipe"
        ),
        pytest.param(
            "<p>x ←b</p>",
            Markdown(escaping=Markdown.Escaping(mode="none"), document=Markdown.Document(transliterate=True)),
            "x <-b",
            id="transliterated-arrow",
        ),
        pytest.param('<img alt="a]b" src="s">', _NO_ESCAPING, "![a\\]b](s)", id="alt-text-keeps-its-syntax"),
    ],
)
def test_escaping_none(html: str, options: Markdown, expected: str) -> None:
    assert parse(html).to_markdown(options) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<a href="http://x.com/a)b">t</a>', "[t](http://x.com/a\\)b)", id="unbalanced-close"),
        pytest.param('<a href="x(y">t</a>', "[t](x\\(y)", id="unclosed-open"),
        pytest.param('<a href="w/F_(b)">t</a>', "[t](w/F_(b))", id="balanced-kept"),
        pytest.param('<a href="x\\">t</a>', "[t](x\\\\)", id="backslash"),
        pytest.param('<a href="x&amp;amp;y">t</a>', "[t](x\\&amp;y)", id="reference-shaped-amp"),
        pytest.param('<a href="?a=1&amp;b=2">t</a>', "[t](?a=1&b=2)", id="query-amp"),
        pytest.param('<a href="&lt;x&gt;">t</a>', "[t](<\\<x\\>>)", id="leading-angle"),
        pytest.param('<a href="a&lt;b">t</a>', "[t](a<b)", id="inner-angle-bare"),
        pytest.param('<a href="a b)">t</a>', "[t](<a b)>)", id="space-takes-parens"),
        pytest.param('<img src="a)b" alt="x">', "![x](a\\)b)", id="image-unbalanced"),
        pytest.param(
            '<a href="http://x.com/a b">http://x.com/a b</a>',
            "[http://x.com/a b](<http://x.com/a b>)",
            id="no-autolink-space",
        ),
        pytest.param('<a href="http://x/&lt;">http://x/&lt;</a>', "[http://x/\\<](http://x/<)", id="no-autolink-lt"),
        pytest.param('<a href="http://x/&gt;">http://x/&gt;</a>', "[http://x/>](http://x/>)", id="no-autolink-gt"),
        pytest.param(
            '<a href="http://x/\x7f">http://x/\x7f</a>', "[http://x/\x7f](http://x/\x7f)", id="no-autolink-del"
        ),
        pytest.param('<a href="http://x/y">http://x/y</a>', "<http://x/y>", id="autolink"),
    ],
)
def test_link_destination(html: str, expected: str) -> None:
    assert md(html) == expected


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<a href="http://x.com/a)b">t</a>', id="unbalanced-close"),
        pytest.param('<a href="x(y">t</a>', id="unclosed-open"),
        pytest.param('<a href="x\\">t</a>', id="backslash"),
        pytest.param('<a href="x&amp;amp;y">t</a>', id="reference-shaped-amp"),
        pytest.param('<a href="&lt;x&gt;">t</a>', id="leading-angle"),
        pytest.param('<a href="a b)">t</a>', id="space-takes-parens"),
        pytest.param('<a href="http://x/&gt;">http://x/&gt;</a>', id="no-autolink-gt"),
    ],
)
def test_link_destination_renders_source_href(html: str) -> None:
    rendered: Final = parse(_render(md(html))).select("a")
    assert [unquote(link.attr("href") or "") for link in rendered] == [
        link.attr("href") for link in parse(html).select("a")
    ]


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<a href="a b">t</a>', "[t](<http://s/a b>)", id="space"),
        pytest.param('<a href="&lt;u">u</a>', "[u](http://s/<u)", id="leading-angle-after-base"),
    ],
)
def test_link_destination_base_url(html: str, expected: str) -> None:
    assert parse(html).to_markdown(Markdown(links=Markdown.Links(base_url="http://s/"))) == expected


def test_reference_definition_escapes_destination() -> None:
    config: Final = Markdown(links=Markdown.Links(style="reference"))
    assert parse('<a href="a)b">t</a>').to_markdown(config) == "[t][1]\n\n[1]: a\\)b"


def test_atx_closed_heading_keeps_trailing_hash() -> None:
    config: Final = Markdown(headings=Markdown.Headings(style="atx_closed"))
    assert parse("<h1>a #</h1>").to_markdown(config) == "# a # #"


def test_setext_heading_escapes_line_start() -> None:
    config: Final = Markdown(headings=Markdown.Headings(style="setext"))
    assert parse("<h1>- a</h1>").to_markdown(config) == "\\- a\n===="


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
    assert md(html) == expected


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
        pytest.param("<p><code></code></p>", "", id="code-span-empty"),
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
    assert md(html) == expected


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


def _nested_tables(depth: int) -> str:
    """`depth` tables, each the only cell of the one above it, around a pipe in text."""
    html = "a|b"
    for _ in range(depth):
        html = f"<table><tr><td>{html}</td></tr></table>"
    return html


@pytest.mark.parametrize("depth", [1, 2, 3, 5], ids=lambda depth: f"depth-{depth}")
def test_nested_table_pipe_escape_never_compounds(depth: int) -> None:
    # `\\|` reads as an escaped backslash followed by a live cell break, and escaping
    # an already-escaped cell once per nesting level is how it used to arrive
    assert "\\\\|" not in md(_nested_tables(depth))


@pytest.mark.parametrize("depth", [2, 3, 5], ids=lambda depth: f"depth-{depth}")
def test_nested_table_survives_the_round_trip(depth: int) -> None:
    # the word-token round-trip below passes on a cell flattened to junk, so pin the
    # structure: every nested table comes back a table
    assert _render(md(_nested_tables(depth))).count("<table") == depth


def test_corpus_never_crashes_and_renders(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    inputs = [
        case["data"] for case in wpt_html_tree_corpus["cases"] if "�" not in case["data"] and len(case["data"]) < 200
    ][:400]
    for html in inputs:
        result = md(html)
        assert isinstance(result, str)
        assert isinstance(_render(result), str)


@pytest.mark.parametrize("count", [1, 10, 1000], ids=["one", "ten", "thousand"])
def test_markdown_repeated_escapes(count: int) -> None:
    assert parse(f"<p>{'*' * count}</p>").to_markdown() == r"\*" * count


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>aa <em>***</em> zz</p>", "aa\n*\\*\\*\\**\nzz", id="deferred-emphasis"),
        pytest.param("<p>aa *** zz</p>", "aa\n\\*\\*\\*\nzz", id="escaped-word"),
        pytest.param("<p>  *** zz</p>", "\\*\\*\\*\nzz", id="leading-whitespace"),
        pytest.param("<p>aa café zz</p>", "aa\ncafe\nzz", id="transliteration"),
    ],
)
def test_markdown_escaped_word_wrapping(html: str, expected: str) -> None:
    config: Final = Markdown(wrapping=Markdown.Wrapping(width=5), document=Markdown.Document(transliterate=True))
    assert parse(html).to_markdown(config) == expected


@pytest.mark.parametrize("width", [80, 8192], ids=["ordinary", "wide"])
def test_markdown_many_words_wrap_at_requested_width(width: int) -> None:
    words: Final = ["aa"] * 10_000
    per_line: Final = (width + 1) // 3
    expected: Final = "\n".join(" ".join(words[start : start + per_line]) for start in range(0, len(words), per_line))
    assert (
        parse("<p>" + " ".join(words) + "</p>").to_markdown(Markdown(wrapping=Markdown.Wrapping(width=width)))
        == expected
    )


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>aa bb cc</p><p>dd ee ff</p>", "aa bb\ncc\n\ndd ee\nff", id="paragraphs"),
        pytest.param(
            "<p>aa bb cc</p><table><tr><td>dd ee</td></tr></table><p>ff gg hh</p>",
            "aa bb\ncc\n\n| dd ee |\n| --- |\n\nff gg\nhh",
            id="table-buffer",
        ),
    ],
)
def test_markdown_wrapping_after_blocks(html: str, expected: str) -> None:
    assert parse(html).to_markdown(Markdown(wrapping=Markdown.Wrapping(width=5))) == expected


def test_markdown_wrapping_after_converter_content() -> None:
    config: Final = Markdown(wrapping=Markdown.Wrapping(width=5), converters={"span": _keep_content})
    assert parse("<p>aa bb <span>cc dd ee</span>! ff gg</p>").to_markdown(config) == "aa bb\ncc dd\nee!\nff gg"


def _keep_content(_element: Element, content: str) -> str:
    return content


def wrap(marker: str) -> Converter:
    """A converter that surrounds the rendered child Markdown with a marker."""
    return lambda _element, content: f"{marker}{content}{marker}"


@pytest.mark.parametrize(
    ("html", "tag", "converter", "expected"),
    [
        pytest.param(
            "<p>see <a href='https://x.test'>the site</a> now</p>",
            "a",
            lambda _el, text: f"[{text}]",
            "see [the site] now",
            id="inline-wrap",
        ),
        pytest.param("<p>a<b>x</b>b</p>", "b", wrap("=="), "a==x==b", id="inline-marker"),
        pytest.param("<p>a<span>x</span>b</p>", "span", lambda _e, _t: "", "ab", id="inline-drop"),
        pytest.param("<p>a<u>keep</u>b</p>", "u", lambda _e, text: text, "akeepb", id="inline-unwrap"),
        pytest.param("<p>only <i>italic</i></p>", "i", wrap("/"), "only /italic/", id="inline-trailing"),
        pytest.param("x<u> t </u>y", "u", wrap("_"), "x _t_ y", id="inline-edge-spaces-move-outside"),
        pytest.param("<u> t</u>", "u", wrap("_"), "_t_", id="inline-leading-space-at-line-start-dropped"),
        pytest.param("x<u><img src=i> t</u>y", "u", wrap("_"), "x_![](i) t_y", id="inline-image-edge-is-no-space"),
        pytest.param("x<u><!--c--> t</u>y", "u", wrap("_"), "x _t_y", id="inline-edge-looks-past-a-comment"),
        pytest.param("x<u><script>s</script> t</u>y", "u", wrap("_"), "x _t_y", id="inline-edge-looks-past-script"),
        pytest.param("x<u><span> <b>t</b></span></u>y", "u", wrap("_"), "x _**t**_y", id="inline-edge-found-nested"),
        pytest.param(
            "x<u><b>t </b><span></span></u>y", "u", wrap("_"), "x_**t**_ y", id="inline-edge-looks-past-empty"
        ),
        pytest.param("x<u>t<br></u>y", "u", wrap("_"), "x_t_ y", id="inline-edge-break-reads-as-space"),
        pytest.param("x<u> </u>y", "u", lambda _e, text: f"[{text}]", "x []y", id="inline-blank-owes-one-space"),
        pytest.param("x<u><b><span></span></b> t</u>y", "u", wrap("_"), "x _t_y", id="inline-edge-climbs-out-of-empty"),
        pytest.param(
            "x<u>t <b><span></span></b></u>y", "u", wrap("_"), "x_t_ y", id="inline-end-edge-climbs-out-of-empty"
        ),
        pytest.param("x<u><span></span></u>y", "u", wrap("_"), "x__y", id="inline-edge-of-only-empty-elements"),
        pytest.param("x<u></u>y", "u", wrap("_"), "x__y", id="inline-edge-of-empty-element"),
        pytest.param("x<u><svg></svg> t</u>y", "u", wrap("_"), "x _t_y", id="inline-edge-looks-past-empty-foreign"),
        pytest.param(
            "x<u><input type=checkbox> t</u>y",
            "u",
            lambda _e, text: f"<{text}>",
            "x<t>y",
            id="inline-edge-form-control-is-no-space",
        ),
    ],
)
def test_inline_converter(html: str, tag: str, converter: Converter, expected: str) -> None:
    assert parse(html).to_markdown(Markdown(converters={tag: converter})) == expected


def test_inner_trailing_break_is_trimmed() -> None:
    # the <br> leaves a trailing "  \n" in the rendered child Markdown that the hook strips
    out = parse("<p>go <a href='https://x.test'>x<br></a> on</p>").to_markdown(Markdown(converters={"a": wrap("|")}))
    assert out == "go |x| on"


def test_inner_all_whitespace_trims_to_empty() -> None:
    # a child that renders to only a break trims away entirely, so the hook sees ""
    out = parse("<p>a<i><br></i>b</p>").to_markdown(Markdown(converters={"i": lambda _e, content: f"[{content}]"}))
    assert out == "a []b"


def test_custom_element_with_attribute() -> None:
    html = "<p>play <video src='m.mp4'>fallback</video> here</p>"
    out = parse(html).to_markdown(Markdown(converters={"video": lambda el, _t: f"[{el.attrs['src']}]"}))
    assert out == "play [m.mp4] here"


def test_converter_on_foreign_element() -> None:
    # a non-HTML (SVG) element matches by tag name and flows inline, never as a block
    out = parse("<p>see <svg><title>chart</title></svg> now</p>").to_markdown(Markdown(converters={"title": wrap("@")}))
    assert out == "see @chart@ now"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<section><p>a</p><div>x</div><p>b</p></section>",
            "a\n\n<<x>>\n\nb",
            id="block-between-paragraphs",
        ),
        pytest.param("<ul><li>one<div>x</div></li></ul>", "- one\n\n  <<x>>", id="block-in-list-item"),
        pytest.param(
            "<blockquote><div>x</div></blockquote>",
            "> <<x>>",
            id="block-in-blockquote",
        ),
    ],
)
def test_block_converter(html: str, expected: str) -> None:
    assert parse(html).to_markdown(Markdown(converters={"div": lambda _e, text: f"<<{text}>>"})) == expected


def test_block_converter_multiline_keeps_prefix() -> None:
    out = parse("<ul><li>one<div>x</div></li></ul>").to_markdown(
        Markdown(converters={"div": lambda _e, _t: "line1\nline2"}),
    )
    assert out == "- one\n\n  line1\n  line2"


def test_empty_converter_result_emits_nothing() -> None:
    out = parse("<section><div>x</div></section>").to_markdown(Markdown(converters={"div": lambda _e, _t: ""}))
    assert not out


def test_converter_content_ignores_a_list_before_the_element() -> None:
    # the converted content renders into its own buffer, so a list closed outside it is no neighbor of one inside
    out = parse("<ul><li>a</li></ul><div>abc<ul><li>b</li></ul></div>").to_markdown(
        Markdown(converters={"div": lambda _e, text: text})
    )
    assert out == "- a\n\nabc\n\n- b"


def test_converter_on_root_element() -> None:
    section = parse("<section>hi <b>there</b></section>").find("section")
    assert section is not None
    out = section.to_markdown(Markdown(converters={"section": lambda _e, text: f"S[{text}]"}))
    assert out == "S[hi **there**]"


def test_converter_edge_skips_empty_text_nodes() -> None:
    document = parse("<p>x<u> t </u>y</p>")
    underline = document.find("u")
    assert underline is not None
    underline.insert(0, Text(""))
    underline.append(Text(""))
    assert document.to_markdown(Markdown(converters={"u": wrap("_")})) == "x _t_ y"


def test_converter_on_inline_root_owes_no_leading_space() -> None:
    span = parse("<p><span> hi </span></p>").find("span")
    assert span is not None
    assert span.to_markdown(Markdown(converters={"span": wrap("_")})) == "_hi_"


def test_converter_output_in_a_table_cell_escapes_its_pipes() -> None:
    html = "<table><tr><td><span>x</span></td></tr></table>"
    out = parse(html).to_markdown(Markdown(converters={"span": lambda _e, text: f"{text}|y"}))
    assert out == "| x\\|y |\n| --- |"


def test_reference_link_inside_converter_registers() -> None:
    html = "<div><a href='https://e.test'>e</a></div>"
    out = parse(html).to_markdown(Markdown(links=Markdown.Links(style="reference"), converters={"div": wrap("|")}))
    assert out == "|[e][1]|\n\n[1]: https://e.test"


def test_converter_receives_element_and_content(mocker: MockerFixture) -> None:
    converter = mocker.MagicMock(return_value="X")
    out = parse("<p><b>hi <i>there</i></b></p>").to_markdown(Markdown(converters={"b": converter}))
    assert out == "X"
    converter.assert_called_once()
    element, content = converter.call_args.args
    assert isinstance(element, Element)
    assert element.tag == "b"
    assert content == "hi *there*"


def test_unregistered_tag_renders_normally() -> None:
    out = parse("<p><b>x</b><i>y</i></p>").to_markdown(Markdown(converters={"b": wrap("@")}))
    assert out == "@x@*y*"


def test_content_node_passes_through_with_converters() -> None:
    out = parse("<p>a<template>b</template>c</p>").to_markdown(Markdown(converters={"unused": wrap("@")}))
    assert out == "abc"


@pytest.mark.parametrize(
    "converters",
    [
        pytest.param({}, id="empty-dict"),
        pytest.param(None, id="none"),
        pytest.param(MappingProxyType({}), id="empty-mapping"),
    ],
)
def test_no_op_converters_match_default(converters: Mapping[str, Converter] | None) -> None:
    html = "<p><b>x</b></p>"
    assert parse(html).to_markdown(Markdown(converters=converters)) == parse(html).to_markdown()


def test_non_dict_mapping_is_accepted() -> None:
    out = parse("<p><b>x</b></p>").to_markdown(Markdown(converters=MappingProxyType({"b": wrap("__")})))
    assert out == "__x__"


def test_non_str_return_raises_type_error() -> None:
    def convert(_element: Element, _content: str) -> str:
        return 123  # ty: ignore[invalid-return-type]  # a non-str on purpose, to exercise the runtime check

    with pytest.raises(TypeError, match=r"converter for <b> must return a str, not int"):
        parse("<p><b>x</b></p>").to_markdown(Markdown(converters={"b": convert}))


def test_converter_exception_propagates() -> None:
    def boom(_element: Element, _content: str) -> str:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        parse("<p><b>x</b></p>").to_markdown(Markdown(converters={"b": boom}))


def test_non_callable_value_raises() -> None:
    with pytest.raises(TypeError):
        # a non-callable value on purpose, to exercise the runtime call failure
        parse("<p><b>x</b></p>").to_markdown(
            Markdown(converters={"b": "not callable"}),  # ty: ignore[invalid-argument-type]
        )


def test_non_mapping_argument_raises() -> None:
    with pytest.raises((TypeError, AttributeError)):
        # a non-mapping on purpose, to exercise the binding's argument coercion
        parse("<p><b>x</b></p>").to_markdown(Markdown(converters=42))  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("html", "strip", "expected"),
    [
        pytest.param(
            '<p>visit <a href="https://e.test">the site</a> today</p>',
            ["a"],
            "visit the site today",
            id="link-loses-markup",
        ),
        pytest.param("<p>a <b>bold</b> and <i>soft</i></p>", ["b"], "a bold and *soft*", id="one-of-two"),
        pytest.param("<p>a <b>bold</b> and <i>soft</i></p>", ["b", "i"], "a bold and soft", id="both"),
        pytest.param("<p>a <b>bold</b> and <i>soft</i></p>", ["i"], "a **bold** and soft", id="other-kept"),
    ],
)
def test_strip_inline(html: str, strip: list[str], expected: str) -> None:
    assert parse(html).to_markdown(Markdown(strip=strip)) == expected


@pytest.mark.parametrize(
    ("html", "convert", "expected"),
    [
        pytest.param(
            '<p>a <b>bold</b> and <a href="https://e.test">link</a></p>',
            ["a"],
            "a bold and [link](https://e.test)",
            id="only-link-kept",
        ),
        pytest.param(
            '<p>a <b>bold</b> and <a href="https://e.test">link</a></p>',
            ["b"],
            "a **bold** and link",
            id="only-bold-kept",
        ),
        pytest.param("<p>a <b>bold</b> and <i>soft</i></p>", ["b", "i"], "a **bold** and *soft*", id="both-kept"),
    ],
)
def test_convert_inline(html: str, convert: list[str], expected: str) -> None:
    assert parse(html).to_markdown(Markdown(convert=convert)) == expected


def test_convert_empty_drops_all_markup() -> None:
    # an empty allowlist keeps markup for nothing, so only the text survives
    out = parse('<p><b>x</b> <a href="https://e.test">y</a></p>').to_markdown(Markdown(convert=[]))
    assert out == "x y"


def test_strip_block_keeps_children() -> None:
    out = parse("<blockquote><p>quoted</p></blockquote>").to_markdown(Markdown(strip=["blockquote"]))
    assert out == "quoted"


def test_strip_heading_unwraps_to_prose() -> None:
    out = parse("<h2>Heading</h2><p>Body text.</p>").to_markdown(Markdown(strip=["h2"]))
    assert out == "Heading\n\nBody text."


def test_convert_block_drops_outer_block() -> None:
    out = parse("<blockquote><p>kept</p></blockquote>").to_markdown(Markdown(convert=["p"]))
    assert out == "kept"


def test_strip_leaves_foreign_element_untouched() -> None:
    # an SVG element has no HTML atom, so it is never named by a filter and renders normally
    out = parse("<p>a <svg><title>chart</title></svg> b</p>").to_markdown(Markdown(strip=["b"]))
    assert out == "a chart b"


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda doc: doc.to_markdown(Markdown(strip=["script"])), id="strip"),
        pytest.param(lambda doc: doc.to_markdown(Markdown(convert=["b"])), id="convert"),
    ],
)
def test_skipped_tag_inside_kept_inline_vanishes_whole(render: Callable[[Document], str]) -> None:
    # a <script> nested in a kept inline parent is reached by the inline walk, yet still
    # drops content-and-all rather than unwrapping the way the filter unwraps other tags
    assert render(parse("<p>a <b><script>var x = 1</script>keep</b> b</p>")) == "a **keep** b"


def test_uppercase_tag_name_is_lowercased() -> None:
    # a tag name is matched case-insensitively, exercising the ASCII lowercasing
    out = parse("<p><b>x</b></p>").to_markdown(Markdown(strip=["B"]))
    assert out == "x"


def test_unknown_tag_name_is_ignored() -> None:
    html = "<p><b>x</b></p>"
    assert parse(html).to_markdown(Markdown(strip=["nosuchtag"])) == parse(html).to_markdown()


def test_overlong_tag_name_is_ignored() -> None:
    html = "<p><b>x</b></p>"
    assert parse(html).to_markdown(Markdown(strip=["z" * 65])) == parse(html).to_markdown()


def test_surrogate_tag_name_is_ignored() -> None:
    html = "<p><b>x</b></p>"
    assert parse(html).to_markdown(Markdown(strip=["\ud800"])) == parse(html).to_markdown()


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda doc: doc.to_markdown(Markdown(strip=None)), id="strip-none"),
        pytest.param(lambda doc: doc.to_markdown(Markdown(convert=None)), id="convert-none"),
        pytest.param(lambda doc: doc.to_markdown(Markdown(strip=[])), id="strip-empty"),
    ],
)
def test_no_op_filters_match_default(render: Callable[[Document], str]) -> None:
    html = "<p><b>x</b> <i>y</i></p>"
    assert render(parse(html)) == parse(html).to_markdown()


def test_non_str_iterable_accepted() -> None:
    out = parse("<p><b>x</b></p>").to_markdown(Markdown(strip=(tag for tag in ["b"])))
    assert out == "x"


def test_strip_and_convert_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="strip and convert are mutually exclusive"):
        Markdown(strip=["b"], convert=["b"])


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda doc: doc.to_markdown(Markdown(strip="b")), id="strip"),
        pytest.param(lambda doc: doc.to_markdown(Markdown(convert="a")), id="convert"),
    ],
)
def test_single_str_rejected(render: Callable[[Document], str]) -> None:
    with pytest.raises(TypeError, match="iterable of tag names, not a single str"):
        render(parse("<p><b>x</b></p>"))


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda doc: doc.to_markdown(Markdown(strip=42)), id="strip"),  # ty: ignore[invalid-argument-type]  # pass a non-iterable to test the binding rejects it
        pytest.param(lambda doc: doc.to_markdown(Markdown(convert=42)), id="convert"),  # ty: ignore[invalid-argument-type]  # pass a non-iterable to test the binding rejects it
    ],
)
def test_non_iterable_rejected(render: Callable[[Document], str]) -> None:
    with pytest.raises(TypeError):
        render(parse("<p><b>x</b></p>"))


def test_non_str_tag_rejected() -> None:
    with pytest.raises(TypeError, match="tags must be str, not int"):
        # a non-str element on purpose, to exercise the per-item type check
        parse("<p><b>x</b></p>").to_markdown(Markdown(strip=["b", 5]))  # ty: ignore[invalid-argument-type]


def test_iterator_error_propagates() -> None:
    def tags() -> Iterator[str]:
        yield "b"
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        parse("<p><b>x</b></p>").to_markdown(Markdown(strip=tags()))


def _google_markdown(html: str, config: Markdown) -> str:
    return parse(html).to_markdown(config)


@pytest.mark.parametrize(
    ("html", "config", "expected"),
    [
        pytest.param(
            '<p><span style="font-weight:700">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**a**",
            id="font-weight-700-is-bold",
        ),
        pytest.param(
            '<p><span style="font-weight:bold">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**a**",
            id="font-weight-bold-keyword",
        ),
        pytest.param(
            '<p><span style="font-weight:800">a</span><span style="font-weight:900">b</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**a****b**",
            id="font-weight-800-and-900",
        ),
        pytest.param(
            '<p><span style="font-weight:400">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "a",
            id="font-weight-400-is-not-bold",
        ),
        pytest.param(
            '<p><span style="font-weight:bolder">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "a",
            id="value-longer-than-keyword-not-bold",
        ),
        pytest.param(
            '<p><span style="font-weight:70">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "a",
            id="value-shorter-than-keyword-not-bold",
        ),
        pytest.param(
            '<p><span style="font-style:italic">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "*a*",
            id="font-style-italic",
        ),
        pytest.param(
            '<p><span style="font-style:normal">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "a",
            id="font-style-normal-is-plain",
        ),
        pytest.param(
            '<p><span style="font-weight:bold;font-style:italic">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "***a***",
            id="bold-and-italic-combine",
        ),
        pytest.param(
            '<p><span style="font-family:Courier New">code()</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "`code()`",
            id="courier-new-is-fixed-width-code",
        ),
        pytest.param(
            '<p><span style="font-family:Consolas">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "`x`",
            id="consolas-is-fixed-width-code",
        ),
        pytest.param(
            '<p><span style="font-family:Arial">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "x",
            id="proportional-font-is-plain",
        ),
        pytest.param(
            '<p><span style="font-weight:bold;font-family:Courier New">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**`x`**",
            id="bold-fixed-width-nests",
        ),
        pytest.param(
            '<p><span style="font-weight:bold"><span style="color:red">x</span></span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**x**",
            id="nested-span-inherits-bold-no-double",
        ),
        pytest.param(
            '<p><span style="font-weight:bold"><span style="font-weight:bold">x</span></span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**x**",
            id="restated-bold-not-doubled",
        ),
        pytest.param(
            '<p>a<span style="font-weight:bold"> x </span>b</p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "a **x** b",
            id="inner-space-moves-outside-markers",
        ),
        pytest.param(
            '<p>keep<span style="font-weight:bold"></span> on</p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "keep on",
            id="empty-styled-span-emits-nothing",
        ),
        pytest.param(
            '<p>a <span style="text-decoration:line-through">gone</span> b</p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True), inline=Markdown.Inline(hide_strikethrough=True)),
            "a b",
            id="line-through-hidden-when-asked",
        ),
        pytest.param(
            '<p>a <span style="text-decoration:line-through">b</span> c</p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "a b c",
            id="line-through-ignored-by-default",
        ),
        pytest.param(
            '<p><span style="text-decoration:underline">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True), inline=Markdown.Inline(hide_strikethrough=True)),
            "a",
            id="underline-is-not-strikethrough",
        ),
        pytest.param(
            "<p>a <span>b</span> c</p>",
            Markdown(google=Markdown.GoogleDoc(enabled=True), inline=Markdown.Inline(hide_strikethrough=True)),
            "a b c",
            id="hide-strikethrough-with-styleless-element",
        ),
        pytest.param(
            '<p><span style="font-weight:bold">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True), inline=Markdown.Inline(hide_strikethrough=True)),
            "**x**",
            id="hide-strikethrough-without-text-decoration",
        ),
        pytest.param(
            '<p><span style="font-style:italic"><span style="font-style:italic">x</span></span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "*x*",
            id="nested-span-inherits-italic-no-double",
        ),
        pytest.param(
            '<p>a<span style="font-style:italic"></span>b</p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "ab",
            id="empty-italic-span-emits-nothing",
        ),
        pytest.param(
            '<p><span style="font-weight:bold;display">y</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**y**",
            id="trailing-declaration-without-colon-skipped",
        ),
        pytest.param(
            '<p><span style="font-weight:bold; :">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**x**",
            id="blank-name-and-value-declaration",
        ),
        pytest.param(
            '<p><span style="font-weight:bold">a</span></p>',
            Markdown(),
            "a",
            id="styles-ignored-without-google-doc",
        ),
        pytest.param(
            "<p><span>plain</span></p>",
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "plain",
            id="span-without-style-is-plain",
        ),
        pytest.param(
            '<p><span style="color:red">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "x",
            id="unrelated-style-property",
        ),
        pytest.param(
            '<p><span style="display;font-weight:bold">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**x**",
            id="declaration-without-colon-skipped",
        ),
        pytest.param(
            '<p><span style="FONT-WEIGHT: BOLD">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**x**",
            id="property-and-value-case-insensitive",
        ),
        pytest.param(
            '<p><span style="  font-weight : bold ">x</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "**x**",
            id="whitespace-around-property-and-value-trimmed",
        ),
        pytest.param(
            '<p><span style="font-weight:bold">a</span></p>',
            Markdown(google=Markdown.GoogleDoc(enabled=True), inline=Markdown.Inline(strong="__", emphasis="_")),
            "__a__",
            id="bold-uses-configured-marker",
        ),
        pytest.param(
            '<ul><li style="margin-left:36px">a</li><li style="margin-left:72px">b</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "  - a\n    - b",
            id="margin-left-nests-list-items",
        ),
        pytest.param(
            '<ul><li style="margin-left:72px">a</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True, list_indent=72)),
            "  - a",
            id="custom-list-indent-divisor",
        ),
        pytest.param(
            '<ol><li style="margin-left:36px">a</li></ol>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "  1. a",
            id="margin-left-nests-ordered-items",
        ),
        pytest.param(
            "<ul><li>a</li></ul>",
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "- a",
            id="list-item-without-margin-is-flat",
        ),
        pytest.param(
            '<ul><li style="margin-left:48px">x</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "  - x",
            id="margin-not-a-multiple-floors-down",
        ),
        pytest.param(
            '<ul><li style="margin-left:36">a</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "  - a",
            id="margin-left-without-px-unit",
        ),
        pytest.param(
            '<ul><li style="margin-left:auto">a</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "- a",
            id="non-numeric-margin-is-no-nesting",
        ),
        pytest.param(
            '<ul><li style="margin-left:-36px">a</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "- a",
            id="negative-margin-is-no-nesting",
        ),
        pytest.param(
            '<ul><li style="color:red">a</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "- a",
            id="list-item-style-without-margin",
        ),
        pytest.param(
            '<ol style="list-style-type:disc"><li>a</li></ol>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "- a",
            id="list-style-disc-renders-unordered",
        ),
        pytest.param(
            '<ul style="list-style-type:decimal"><li>a</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "1. a",
            id="list-style-decimal-renders-ordered",
        ),
        pytest.param(
            '<ul style="color:red"><li>a</li></ul>',
            Markdown(google=Markdown.GoogleDoc(enabled=True)),
            "- a",
            id="list-without-style-type-keeps-tag",
        ),
    ],
)
def test_google_doc(html: str, config: Markdown, expected: str) -> None:
    assert _google_markdown(html, config) == expected


def test_google_list_indent_must_be_positive() -> None:
    with pytest.raises(ValueError, match="google_list_indent"):
        parse("<p>x</p>").to_markdown(Markdown(google=Markdown.GoogleDoc(list_indent=0)))


def test_google_doc_preset_enables_styling_and_drops_struck_text() -> None:
    preset = Markdown.google_doc()
    assert preset.google.enabled
    assert preset.inline.hide_strikethrough
    html = '<p><span style="font-weight:700">keep</span> <span style="text-decoration:line-through">gone</span></p>'
    assert parse(html).to_markdown(preset) == "**keep**"


def _mode_markdown(html: str, config: Markdown) -> str:
    return parse(html).to_markdown(config)


@pytest.mark.parametrize(
    ("html", "config", "expected"),
    [
        pytest.param(
            "<p>one two three four five six seven eight</p>",
            Markdown(wrapping=Markdown.Wrapping(width=15)),
            "one two three\nfour five six\nseven eight",
            id="wrap-prose-greedy",
        ),
        pytest.param(
            "<p>antidisestablishmentarianism rocks</p>",
            Markdown(wrapping=Markdown.Wrapping(width=10)),
            "antidisestablishmentarianism\nrocks",
            id="wrap-long-word-not-split",
        ),
        pytest.param(
            "<blockquote>alpha beta gamma delta</blockquote>",
            Markdown(wrapping=Markdown.Wrapping(width=12)),
            "> alpha beta\n> gamma\n> delta",
            id="wrap-keeps-blockquote-prefix",
        ),
        pytest.param(
            "<p>one two three</p>",
            Markdown(wrapping=Markdown.Wrapping(width=0)),
            "one two three",
            id="wrap-zero-disables",
        ),
    ],
)
def test_wrap_width(html: str, config: Markdown, expected: str) -> None:
    assert _mode_markdown(html, config) == expected


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param(
            Markdown(wrapping=Markdown.Wrapping(width=12)),
            "- alpha beta gamma delta epsilon",
            id="list-items-unwrapped-by-default",
        ),
        pytest.param(
            Markdown(wrapping=Markdown.Wrapping(width=12, list_items=True)),
            "- alpha beta\n  gamma\n  delta\n  epsilon",
            id="list-items-wrapped",
        ),
    ],
)
def test_wrap_list_items(config: Markdown, expected: str) -> None:
    html = "<ul><li>alpha beta gamma delta epsilon</li></ul>"
    assert _mode_markdown(html, config) == expected


@pytest.mark.parametrize(
    ("html", "config", "expected"),
    [
        pytest.param(
            '<p>see <a href="u">the long link text here</a> ok</p>',
            Markdown(wrapping=Markdown.Wrapping(width=10, links=False)),
            "see [the long link text here](u)\nok",
            id="links-unbroken",
        ),
        pytest.param(
            '<p><a href="u">alpha beta gamma</a></p>',
            Markdown(wrapping=Markdown.Wrapping(width=10)),
            "[alpha\nbeta gamma](u)",
            id="links-wrap-when-allowed",
        ),
    ],
)
def test_wrap_links(html: str, config: Markdown, expected: str) -> None:
    assert _mode_markdown(html, config) == expected


@pytest.mark.parametrize(
    ("html", "config", "expected"),
    [
        pytest.param(
            '<p><img src="a.png" alt="x" width="4"></p>',
            Markdown(images=Markdown.Images(mode="html")),
            '<img src="a.png" alt="x" width="4">',
            id="image-html-keeps-attributes",
        ),
        pytest.param(
            "<table><tr><td>a</td></tr></table>",
            Markdown(tables=Markdown.Tables(mode="html")),
            "<table><tbody><tr><td>a</td></tr></tbody></table>",
            id="table-html-verbatim",
        ),
    ],
)
def test_html_passthrough(html: str, config: Markdown, expected: str) -> None:
    assert _mode_markdown(html, config) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>café naïve</p>", "cafe naive", id="accented-latin1"),
        pytest.param("<p>œuvre Œ ß</p>", "oeuvre OE ss", id="latin-extended-a"),
        pytest.param("<p>“q” \u2018r\u2019 — … ©</p>", "\"q\" 'r' -- ... (C)", id="punctuation-and-symbols"),
        pytest.param("<p>a → b ← c \u00d7 d</p>", "a -> b <- c x d", id="arrows-and-times"),
        pytest.param("<p>中文 é</p>", "中文 e", id="unmapped-non-ascii-passthrough"),
    ],
)
def test_transliterate(html: str, expected: str) -> None:
    assert _mode_markdown(html, Markdown(document=Markdown.Document(transliterate=True))) == expected


def test_transliterate_leaves_code_verbatim() -> None:
    assert (
        _mode_markdown("<p><code>café</code></p>", Markdown(document=Markdown.Document(transliterate=True))) == "`café`"
    )


def test_wrap_and_transliterate_compose() -> None:
    html = "<p>The “quick” brown fox — jumps over the lazy dog today.</p>"
    expected = 'The "quick" brown fox -- jumps\nover the lazy dog today.'
    config = Markdown(wrapping=Markdown.Wrapping(width=30), document=Markdown.Document(transliterate=True))
    assert _mode_markdown(html, config) == expected


def test_wrap_width_rejects_negative() -> None:
    with pytest.raises(ValueError, match="wrap_width"):
        parse("<p>x</p>").to_markdown(Markdown(wrapping=Markdown.Wrapping(width=-1)))


@pytest.mark.parametrize(
    ("config", "option"),
    [
        # a bogus enum value on purpose, to exercise the renderer's runtime validation
        pytest.param(
            Markdown(images=Markdown.Images(mode="bogus")),  # ty: ignore[invalid-argument-type]
            "image_mode",
            id="image_mode",
        ),
        pytest.param(
            Markdown(tables=Markdown.Tables(mode="bogus")),  # ty: ignore[invalid-argument-type]
            "table_mode",
            id="table_mode",
        ),
    ],
)
def test_new_enum_values_still_validate(config: Markdown, option: str) -> None:
    with pytest.raises(ValueError, match=option):
        parse("<p>x</p>").to_markdown(config)


def _configured_markdown(html: str, options: Markdown) -> str:
    return parse(html).to_markdown(options)


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            "<h2>H</h2>", Markdown(headings=Markdown.Headings(style="atx_closed")), "## H ##", id="heading-atx-closed"
        ),
        pytest.param(
            "<h1>H</h1>", Markdown(headings=Markdown.Headings(style="setext")), "H\n=", id="heading-setext-h1"
        ),
        pytest.param(
            "<h2>Hi</h2>", Markdown(headings=Markdown.Headings(style="setext")), "Hi\n--", id="heading-setext-h2"
        ),
        pytest.param(
            "<h3>H</h3>",
            Markdown(headings=Markdown.Headings(style="setext")),
            "### H",
            id="heading-setext-h3-falls-back",
        ),
        pytest.param(
            "<ul><li><h3>H</h3></li></ul>",
            Markdown(headings=Markdown.Headings(style="setext")),
            "- ### H",
            id="list-heading-setext-h3-falls-back",
        ),
        pytest.param(
            "<ul><li><h1>H</h1></li></ul>",
            Markdown(headings=Markdown.Headings(style="setext")),
            "- \n  H\n  =",
            id="list-heading-setext-h1",
        ),
    ],
)
def test_heading_style(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param("<ul><li>a</li></ul>", Markdown(lists=Markdown.Lists(bullets="*")), "* a", id="bullets-single"),
        pytest.param(
            "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>",
            Markdown(lists=Markdown.Lists(bullets="*+")),
            "* a\n  + b\n    * c",
            id="bullets-cycled-by-depth",
        ),
        pytest.param(
            "<ul><li>a</li></ul><ul><li>b</li></ul>",
            Markdown(lists=Markdown.Lists(bullets="*")),
            "* a\n- b",
            id="bullets-adjacent-list-switches-from-configured",
        ),
    ],
)
def test_bullets(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param("<p><b>x</b></p>", Markdown(inline=Markdown.Inline(strong="__")), "__x__", id="strong-underscore"),
        pytest.param(
            "<p><em>x</em></p>", Markdown(inline=Markdown.Inline(emphasis="_")), "_x_", id="emphasis-underscore"
        ),
        pytest.param(
            "<p>a<b>x</b><i>y</i><s>z</s>b</p>",
            Markdown(inline=Markdown.Inline(ignore_emphasis=True)),
            "axyzb",
            id="ignore-emphasis",
        ),
        pytest.param(
            "<p>a<s>z</s>b</p>", Markdown(inline=Markdown.Inline(strikethrough="hide")), "ab", id="strikethrough-hide"
        ),
        pytest.param("<p>H<sub>2</sub>O</p>", Markdown(inline=Markdown.Inline(sub="~")), "H~2~O", id="sub-symbol"),
        pytest.param("<p>x<sup>2</sup></p>", Markdown(inline=Markdown.Inline(sup="^")), "x^2^", id="sup-symbol"),
        pytest.param(
            "<p><q>hi</q></p>",
            Markdown(inline=Markdown.Inline(quote_open="«", quote_close="»")),
            "«hi»",
            id="quote-custom",
        ),
        pytest.param("<p><q>hi</q></p>", Markdown(), '"hi"', id="quote-default"),
    ],
)
def test_inline_markers(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            "<pre><code>x=1</code></pre>",
            Markdown(code=Markdown.Code(block_style="indented")),
            "    x=1",
            id="code-indented",
        ),
        pytest.param(
            "<pre><code>x=1\ny=2</code></pre>",
            Markdown(code=Markdown.Code(block_style="indented")),
            "    x=1\n    y=2",
            id="code-indented-multiline",
        ),
        pytest.param(
            "<pre><code>x=1</code></pre>",
            Markdown(code=Markdown.Code(mark=True)),
            "[code]\nx=1\n[/code]",
            id="code-mark",
        ),
        pytest.param(
            "<pre><code>x</code></pre>",
            Markdown(code=Markdown.Code(language="py")),
            "```py\nx\n```",
            id="code-default-language",
        ),
        pytest.param(
            '<pre><code class="language-c">x</code></pre>',
            Markdown(code=Markdown.Code(language="py")),
            "```c\nx\n```",
            id="code-language-class-wins",
        ),
    ],
)
def test_code_blocks(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            '<p><a href="http://x">L</a></p>',
            Markdown(links=Markdown.Links(style="reference")),
            "[L][1]\n\n[1]: http://x",
            id="link-reference",
        ),
        pytest.param(
            '<p><a href="/a" title="T">A</a> <a href="/b">B</a></p>',
            Markdown(links=Markdown.Links(style="reference")),
            '[A][1] [B][2]\n\n[1]: /a "T"\n[2]: /b',
            id="link-reference-multiple",
        ),
        pytest.param(
            '<p><a href="http://x.com">http://x.com</a></p>', Markdown(), "<http://x.com>", id="autolink-match"
        ),
        pytest.param(
            '<p><a href="http://x.com">text</a></p>',
            Markdown(),
            "[text](http://x.com)",
            id="link-autolink-no-match",
        ),
        pytest.param(
            '<p><a href="http://x.com">http://x.com</a></p>',
            Markdown(links=Markdown.Links(autolink=False)),
            "[http://x.com](http://x.com)",
            id="link-autolink-disabled",
        ),
        pytest.param(
            '<p><a href="/p">L</a></p>',
            Markdown(links=Markdown.Links(title=True)),
            '[L](/p "/p")',
            id="link-title-from-href",
        ),
        pytest.param('<p><a href="/p">L</a></p>', Markdown(links=Markdown.Links(ignore=True)), "L", id="link-ignore"),
        pytest.param(
            '<p><a href="#sec">L</a></p>',
            Markdown(links=Markdown.Links(skip_internal=True)),
            "L",
            id="link-skip-internal",
        ),
        pytest.param(
            '<p><a href="page">L</a></p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "[L](http://s/page)",
            id="link-base",
        ),
        pytest.param(
            '<p><a href="http://x/p">L</a></p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "[L](http://x/p)",
            id="link-base-url-absolute-untouched",
        ),
    ],
)
def test_links(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            '<p><img src="i" alt="cat"></p>', Markdown(images=Markdown.Images(mode="alt")), "cat", id="image-alt"
        ),
        pytest.param(
            '<p>a<img src="i">b</p>', Markdown(images=Markdown.Images(mode="ignore")), "ab", id="image-ignore"
        ),
        pytest.param(
            '<p><img src="i">b</p>',
            Markdown(images=Markdown.Images(default_alt="img")),
            "![img](i)b",
            id="image-default-alt",
        ),
        pytest.param(
            '<p><img src="p.png">x</p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "![](http://s/p.png)x",
            id="image-base-url",
        ),
    ],
)
def test_images(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            "<table><tr><th>Name</th><th>X</th></tr><tr><td>a</td><td>bb</td></tr></table>",
            Markdown(tables=Markdown.Tables(pad=True)),
            "| Name | X   |\n| ---- | --- |\n| a    | bb  |",
            id="table-pad",
        ),
        pytest.param(
            "<table><tr><td>a</td></tr><tr><td>b</td></tr></table>",
            Markdown(tables=Markdown.Tables(pad=True, header="none")),
            "|     |\n| --- |\n| a   |\n| b   |",
            id="table-pad-no-header",
        ),
        pytest.param(
            "<table><tr><td>a</td><td>b</td></tr></table>",
            Markdown(tables=Markdown.Tables(mode="strip")),
            "a b",
            id="table-strip",
        ),
        pytest.param(
            "<table><caption>cap</caption><tr><td>a</td></tr></table>",
            Markdown(tables=Markdown.Tables(mode="strip")),
            "cap\n\na",
            id="table-strip-caption",
        ),
        pytest.param(
            "<table><caption>cap</caption><tr><td>a</td></tr></table>",
            Markdown(tables=Markdown.Tables(mode="html")),
            "<table><caption>cap</caption><tbody><tr><td>a</td></tr></tbody></table>",
            id="table-html-keeps-caption-once",
        ),
        pytest.param(
            "<table><tr><td>a</td></tr><tr><td>b</td></tr></table>",
            Markdown(tables=Markdown.Tables(header="detect")),
            "|  |\n| --- |\n| a |\n| b |",
            id="table-header-detect-no-header",
        ),
        pytest.param(
            "<table><tr><th>H</th></tr><tr><td>a</td></tr></table>",
            Markdown(tables=Markdown.Tables(header="detect")),
            "| H |\n| --- |\n| a |",
            id="table-header-detect-with-th",
        ),
        pytest.param(
            "<table><thead><tr><td>H</td></tr></thead><tbody><tr><td>a</td></tr></tbody></table>",
            Markdown(tables=Markdown.Tables(header="detect")),
            "| H |\n| --- |\n| a |",
            id="table-header-detect-thead",
        ),
        pytest.param(
            "<table><tr><!--c--><td>a</td><td>bb</td></tr></table>",
            Markdown(tables=Markdown.Tables(pad=True)),
            "| a   | bb  |\n| --- | --- |",
            id="table-pad-comment-in-row",
        ),
        pytest.param(
            "<table><tr><!--c--><td>a</td><td>b</td></tr></table>",
            Markdown(tables=Markdown.Tables(mode="strip")),
            "a b",
            id="table-strip-comment-in-row",
        ),
        pytest.param(
            "<table><tr><th>H</th></tr><tr><td>a</td></tr></table>",
            Markdown(tables=Markdown.Tables(header="none")),
            "|  |\n| --- |\n| H |\n| a |",
            id="table-header-none",
        ),
        pytest.param(
            "<table><tr><td>a<table><tr><td>b</td></tr></table></td><td>c</td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| a b | c |\n| --- | --- |",
            id="cell-blocks-text-flattens-nested-table",
        ),
        pytest.param(
            "<table><tr><td><ul><li>x</li><li>y</li></ul></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| * x * y |\n| --- |",
            id="cell-blocks-text-keeps-list-markers",
        ),
        pytest.param(
            '<table><tr><th>Traded as</th><td><ul><li><a href="/nasdaq">Nasdaq</a></li>'
            "<li>DJIA</li></ul></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| Traded as | * [Nasdaq](/nasdaq) * DJIA |\n| --- | --- |",
            id="cell-blocks-text-keeps-links-and-markers",
        ),
        pytest.param(
            '<table><tr><td><ol start="3"><li>a</li><li value="7">b</li><li>c</li></ol></td></tr></table>',
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| 3. a 7. b 8. c |\n| --- |",
            id="cell-blocks-text-keeps-ordered-numbers",
        ),
        pytest.param(
            '<table><tr><td><ol start="9223372036854775807"><li>a</li><li>b</li></ol></td></tr></table>',
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| 9223372036854775807. a 9223372036854775807. b |\n| --- |",
            id="cell-blocks-text-ordered-number-limit",
        ),
        pytest.param(
            "<table><tr><td><ul><li>a</li><li>b</li><li>c<ul><li>d</li></ul></li></ul></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| * a * b * c * d |\n| --- |",
            id="cell-blocks-text-keeps-nested-items",
        ),
        pytest.param(
            '<table><tr><td><ul><li>a<ol start="4"><li>b</li><li value="9">c</li></ol></li><li>d</li>'
            "</ul></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| * a 4. b 9. c * d |\n| --- |",
            id="cell-blocks-text-nested-ordered-list",
        ),
        pytest.param(
            "<table><tr><td>a<br>b</td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| a b |\n| --- |",
            id="cell-blocks-text-break-is-a-space",
        ),
        pytest.param(
            "<table><tr><td><table><tr><td><b>x</b>|y</td></tr></table></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| **x**\\|y |\n| --- |",
            id="cell-blocks-text-keeps-inline-markup",
        ),
        pytest.param(
            "<table><tr><td><table><thead><tr><th>H</th></tr></thead>"
            "<tbody><tr><td><ul><li>u</li></ul><ol><li>o</li></ol><menu><li>m</li></menu>"
            "<table><tr><td>t</td></tr></table></td></tr></tbody>"
            "<tfoot><tr><td>f</td></tr></tfoot></table></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| H * u 1. o * m t f |\n| --- |",
            id="cell-blocks-text-spaces-every-boundary",
        ),
        pytest.param(
            "<table><tr><td>a<table><tr><td>b</td></tr></table></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="html", pad=True)),
            "| a<table><tr><td>b</td></tr></table> |\n| ----------------------------------- |",
            id="cell-blocks-html-widens-the-padded-column",
        ),
        pytest.param(
            '<table><tr><td><table><tbody class="x"><tr><td>b</td></tr></tbody></table></td></tr></table>',
            Markdown(),
            '| <table><tbody class="x"><tr><td>b</td></tr></table> |\n| --- |',
            id="cell-html-keeps-a-tbody-that-carries-attributes",
        ),
        pytest.param(
            "<table><tr><td>a|b</td></tr></table>",
            Markdown(escaping=Markdown.Escaping(mode="all")),
            "| a\\|b |\n| --- |",
            id="cell-pipe-escaped-once-under-escape-all",
        ),
        pytest.param(
            '<table><tr><td><a href="u|v" title="t|t">a|b</a></td></tr></table>',
            Markdown(),
            '| [a\\|b](u\\|v "t\\|t") |\n| --- |',
            id="cell-pipe-escaped-in-link-text-url-and-title",
        ),
        pytest.param(
            '<table><tr><td><a href="u |v">x</a></td></tr></table>',
            Markdown(),
            "| [x](<u \\|v>) |\n| --- |",
            id="cell-pipe-escaped-in-bracketed-url",
        ),
        pytest.param(
            '<table><tr><td><img src="a|b.png" alt="x|y"></td></tr></table>',
            Markdown(),
            "| ![x\\|y](a\\|b.png) |\n| --- |",
            id="cell-pipe-escaped-in-image",
        ),
        pytest.param(
            '<table><tr><td><img src="s.png" alt="x|y"></td></tr></table>',
            Markdown(images=Markdown.Images(mode="alt")),
            "| x\\|y |\n| --- |",
            id="cell-pipe-escaped-in-bare-alt-text",
        ),
        pytest.param(
            '<table><tr><td><img src="a|b.png" alt="x"></td></tr></table>',
            Markdown(images=Markdown.Images(mode="html")),
            '| <img src="a\\|b.png" alt="x"> |\n| --- |',
            id="cell-pipe-escaped-in-embedded-html",
        ),
        pytest.param(
            "<table><tr><td><ol><li>x</li></ol></td></tr></table>",
            Markdown(),
            "| <ol><li>x</li></ol> |\n| --- |",
            id="cell-ordered-list-becomes-html",
        ),
        pytest.param(
            "<table><tr><td><menu><li>x</li></menu></td></tr></table>",
            Markdown(),
            "| <menu><li>x</li></menu> |\n| --- |",
            id="cell-menu-becomes-html",
        ),
        pytest.param(
            "<table><tr><td><table><tr><td><svg><text>s</text></svg>t</td></tr></table></td></tr></table>",
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| st |\n| --- |",
            id="cell-blocks-text-flattens-foreign-content",
        ),
        pytest.param(
            "<table><tr><td><pre>a|b</pre></td></tr></table>",
            Markdown(),
            "| ``` a\\|b ``` |\n| --- |",
            id="cell-pipe-escaped-in-preformatted-text",
        ),
    ],
)
def test_markdown_table_options(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


def test_markdown_table_text_orphan_item() -> None:
    nested = Element(
        "table", children=[Element("tr", children=[Element("td", children=[Element("li", children=[Text("x")])])])]
    )
    table = Element("table", children=[Element("tr", children=[Element("td", children=[nested])])])
    assert table.to_markdown(Markdown(tables=Markdown.Tables(cell_blocks="text"))) == "| * x |\n| --- |"


@pytest.mark.parametrize(
    ("wrapped_list", "expected", "cell_html"),
    [
        pytest.param(
            "before <strong><ul><li>x</li><li>y</li></ul></strong> after",
            "| before **\\* x \\* y** after |\n| --- |",
            "before <strong>* x * y</strong> after",
            id="strong",
        ),
        pytest.param(
            "<em><ul><li>x</li></ul></em>",
            "| *\\* x* |\n| --- |",
            "<em>* x</em>",
            id="emphasis",
        ),
        pytest.param(
            '<a href="/x"><ul><li>one</li><li>two</li></ul></a>',
            "| [\\* one \\* two](/x) |\n| --- |",
            '<a href="/x">* one * two</a>',
            id="link",
        ),
        pytest.param(
            "<del><ul><li>one</li><li>two</li></ul></del>",
            "| ~~\\* one \\* two~~ |\n| --- |",
            "<s>* one * two</s>",
            id="strikethrough",
        ),
    ],
)
def test_markdown_table_text_list_inline_frames(wrapped_list: str, expected: str, cell_html: str) -> None:
    output = parse(f"<table><tr><td>{wrapped_list}</td></tr></table>").to_markdown(
        Markdown(tables=Markdown.Tables(cell_blocks="text"))
    )
    assert (output, _render(output).split("<th>", 1)[1].split("</th>", 1)[0]) == (expected, cell_html)


@pytest.mark.parametrize(
    ("html", "config", "expected"),
    [
        pytest.param(
            '<a href="/x"><ul><li>one</li><li>two</li></ul></a>',
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "[one two](/x)",
            id="outside-cell",
        ),
        pytest.param(
            '<table><tr><td><a href="/x"><ul><li>one</li><li>two</li></ul></a></td></tr></table>',
            Markdown(),
            "| [one two](/x) |\n| --- |",
            id="html-cell-blocks",
        ),
        pytest.param(
            '<table><tr><td><a href="/x"><p>one</p></a></td></tr></table>',
            Markdown(tables=Markdown.Tables(cell_blocks="text")),
            "| [one](/x) |\n| --- |",
            id="non-list-block",
        ),
    ],
)
def test_markdown_table_link_block_fallback(html: str, config: Markdown, expected: str) -> None:
    assert parse(html).to_markdown(config) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            "<p>a&lt;b&gt;c</p>", Markdown(escaping=Markdown.Escaping(mode="all")), "a\\<b\\>c", id="escape-all"
        ),
        pytest.param(
            "<p>a*b_c</p>", Markdown(escaping=Markdown.Escaping(asterisks=False)), "a*b\\_c", id="escape-no-asterisks"
        ),
        pytest.param(
            "<p>a*b_c</p>",
            Markdown(escaping=Markdown.Escaping(underscores=False)),
            "a\\*b_c",
            id="escape-no-underscores",
        ),
        pytest.param(
            "<p>a<br>b</p>",
            Markdown(document=Markdown.Document(line_break="backslash")),
            "a\\\nb",
            id="break-backslash",
        ),
        pytest.param(
            "<p>a</p><p>b</p>",
            Markdown(document=Markdown.Document(block_spacing="single")),
            "a\nb",
            id="spacing-single",
        ),
        pytest.param("  <p>x</p>  ", Markdown(document=Markdown.Document(trim="none")), "x", id="doc-strip-none"),
        pytest.param("<p>x</p>", Markdown(document=Markdown.Document(trim="lstrip")), "x", id="doc-strip-lstrip"),
        pytest.param("<p>x</p>", Markdown(document=Markdown.Document(trim="rstrip")), "x", id="doc-strip-rstrip"),
    ],
)
def test_text_options(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            "<p>&lt; &gt; # + - = ~ | ! &amp;</p>",
            Markdown(escaping=Markdown.Escaping(mode="all")),
            "\\< \\> \\# \\+ \\- \\= \\~ \\| \\! \\&",
            id="escape-all-every-char",
        ),
        pytest.param('<p><a href="">e</a></p>', Markdown(), "e", id="link-empty-href-dropped"),
        pytest.param(
            '<p><a href="page">L</a></p>',
            Markdown(links=Markdown.Links(skip_internal=True)),
            "[L](page)",
            id="link-skip-non-internal",
        ),
        pytest.param(
            '<p><a href="/abs?q=1">L</a></p>',
            Markdown(links=Markdown.Links(base_url="http://s")),
            "[L](http://s/abs?q=1)",
            id="link-base-url-rooted-path",
        ),
        pytest.param(
            '<p><a href="mailto:a@b">L</a></p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "[L](mailto:a@b)",
            id="link-base-url-mailto-absolute",
        ),
        pytest.param(
            '<p><img src="/p.png">x</p>',
            Markdown(links=Markdown.Links(base_url="http://s")),
            "![](http://s/p.png)x",
            id="image-root",
        ),
        pytest.param(
            '<p><img src="http://x/p">x</p>',
            Markdown(links=Markdown.Links(base_url="http://s")),
            "![](http://x/p)x",
            id="image-base-absolute-untouched",
        ),
        pytest.param(
            "<table><tr><!--c--><td>a</td></tr><tr><td>b</td></tr></table>",
            Markdown(tables=Markdown.Tables(header="detect")),
            "|  |\n| --- |\n| a |\n| b |",
            id="table-detect-comment-row",
        ),
        pytest.param(
            "<table><tr><th>H</th><td>x</td></tr><tr><td>a</td><td>b</td></tr></table>",
            Markdown(tables=Markdown.Tables(mode="strip")),
            "H x\n\na b",
            id="table-strip-with-th",
        ),
    ],
)
def test_option_edge_cases(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


# A bad value is not caught when the config is built (the typed fields are not enforced
# at runtime) but when the renderer reads the unpacked keyword; the match is the renderer
# keyword name, which the grouped field maps back to (e.g. Document.trim -> document_strip).
@pytest.mark.parametrize(
    ("operation", "exc", "match"),
    [
        pytest.param(
            lambda root: root.to_markdown(Markdown(headings=Markdown.Headings(style="nope"))),  # ty: ignore[invalid-argument-type]  # pass an invalid enum to test the renderer rejects it
            ValueError,
            "heading_style",
            id="invalid-enum",
        ),
        pytest.param(
            lambda root: root.to_markdown(Markdown(lists=Markdown.Lists(bullets=""))),
            ValueError,
            "bullets",
            id="empty-bullets",
        ),
        pytest.param(
            lambda root: root.to_markdown(Markdown(headings=Markdown.Headings(style=5))),  # ty: ignore[invalid-argument-type]  # pass a non-string to test the renderer's type check
            TypeError,
            "string",
            id="non-string-enum",
        ),
        pytest.param(
            lambda root: root.to_markdown(Markdown(inline=Markdown.Inline(strong=5))),  # ty: ignore[invalid-argument-type]  # pass a non-string to test the renderer's type check
            TypeError,
            "str",
            id="wrong-type-marker",
        ),
        pytest.param(
            lambda _root: Markdown(unknown_option=1),  # ty: ignore[unknown-argument]  # pass an unknown field to test it is rejected at construction
            TypeError,
            "keyword",
            id="unknown-keyword",
        ),
    ],
)
def test_invalid_options(operation: Callable[[Node], object], exc: type[Exception], match: str) -> None:
    root = parse("<p>x</p>")
    with pytest.raises(exc, match=match):
        operation(root)


# every case passes an invalid "bogus" value to test the renderer validates each enum at
# render time; the typed sub-config fields would otherwise block it, hence the ty: ignore
@pytest.mark.parametrize(
    ("config", "match"),
    [
        pytest.param(Markdown(inline=Markdown.Inline(strikethrough="bogus")), "strikethrough", id="strikethrough"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(code=Markdown.Code(block_style="bogus")), "code_block_style", id="code_block_style"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(links=Markdown.Links(style="bogus")), "link_style", id="link_style"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(images=Markdown.Images(mode="bogus")), "image_mode", id="image_mode"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(tables=Markdown.Tables(mode="bogus")), "table_mode", id="table_mode"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(tables=Markdown.Tables(header="bogus")), "table_header", id="table_header"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(tables=Markdown.Tables(cell_blocks="bogus")), "cell_blocks", id="cell_blocks"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(escaping=Markdown.Escaping(mode="bogus")), "escape_mode", id="escape_mode"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(document=Markdown.Document(line_break="bogus")), "line_break", id="line_break"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(document=Markdown.Document(block_spacing="bogus")), "block_spacing", id="block_spacing"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
        pytest.param(Markdown(document=Markdown.Document(trim="bogus")), "document_strip", id="document_strip"),  # ty: ignore[invalid-argument-type]  # invalid value tests the runtime enum check
    ],
)
def test_each_enum_is_validated(config: Markdown, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse("<p>x</p>").to_markdown(config)


def test_explicit_none_is_the_default() -> None:
    page = parse("<h1>Hi</h1><p>x</p>")
    assert page.to_markdown(None) == page.to_markdown()


def test_options_must_be_a_markdown() -> None:
    with pytest.raises(TypeError, match="options must be a Markdown"):
        parse("<p>x</p>").to_markdown(object())  # ty: ignore[invalid-argument-type]  # pass a non-Markdown to test the type error


def test_rejects_another_renderers_config() -> None:
    with pytest.raises(TypeError, match="options must be a Markdown, not PlainText"):
        parse("<p>x</p>").to_markdown(PlainText())  # ty: ignore[invalid-argument-type]  # the wrong config class is rejected


def test_rejects_extra_positional() -> None:
    with pytest.raises(TypeError):
        parse("<p>x</p>").to_markdown(Markdown(), Markdown())  # ty: ignore[too-many-positional-arguments]  # a second arg is rejected


@pytest.mark.parametrize(
    ("html", "opts", "expected"),
    [
        pytest.param(
            '<p><a href="a#b">L</a></p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "[L](http://s/a#b)",
            id="abs-hash-relative",
        ),
        pytest.param(
            '<p><a href="q?x">L</a></p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "[L](http://s/q?x)",
            id="abs-query-relative",
        ),
        pytest.param(
            '<p><a href="#x">L</a></p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "[L](#x)",
            id="base-url-skips-fragment",
        ),
        pytest.param(
            '<p><img src="#x">y</p>',
            Markdown(links=Markdown.Links(base_url="http://s/")),
            "![](#x)y",
            id="image-base-url-skips-fragment",
        ),
        pytest.param(
            '<p><a href="http://x.com">http://y.com</a></p>',
            Markdown(),
            "[http://y.com](http://x.com)",
            id="autolink-same-length-no-match",
        ),
    ],
)
def test_href_resolution(html: str, opts: Markdown, expected: str) -> None:
    assert _configured_markdown(html, opts) == expected


def test_reference_links_grow_past_initial_capacity() -> None:
    html = "<p>" + "".join(f'<a href="/{i}">L{i}</a>' for i in range(10)) + "</p>"
    out = _configured_markdown(html, Markdown(links=Markdown.Links(style="reference")))
    assert "[10]: /9" in out


@pytest.mark.parametrize(
    ("opts", "expected"),
    [
        pytest.param(
            Markdown(tables=Markdown.Tables(pad=True)),
            "| H   | x   |\n| --- | --- |\n| a   | b   |",
            id="pad-th-and-comment",
        ),
        pytest.param(Markdown(tables=Markdown.Tables(mode="strip")), "H x\n\na b", id="strip-th-and-comment"),
    ],
)
def test_table_cells_with_th_and_comment(opts: Markdown, expected: str) -> None:
    html = "<table><tr><!--c--><template></template><th>H</th><td>x</td></tr><tr><td>a</td><td>b</td></tr></table>"
    assert _configured_markdown(html, opts) == expected
