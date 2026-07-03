"""The to_markdown() configuration surface: the markdownify + html2text knobs.

One golden case per option value, plus the binding's validation errors, so every
option code path in the C walker is exercised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Markdown, PlainText, parse

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Node


def md(html: str, options: Markdown) -> str:
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
    ],
)
def test_heading_style(html: str, opts: Markdown, expected: str) -> None:
    assert md(html, opts) == expected


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
    ],
)
def test_bullets(html: str, opts: Markdown, expected: str) -> None:
    assert md(html, opts) == expected


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
    assert md(html, opts) == expected


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
    assert md(html, opts) == expected


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
    assert md(html, opts) == expected


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
    assert md(html, opts) == expected


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
    ],
)
def test_tables(html: str, opts: Markdown, expected: str) -> None:
    assert "\n".join(line.rstrip() for line in md(html, opts).splitlines()) == expected


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
    assert md(html, opts) == expected


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
    assert "\n".join(line.rstrip() for line in md(html, opts).splitlines()) == expected


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
    assert md(html, opts) == expected


def test_reference_links_grow_past_initial_capacity() -> None:
    html = "<p>" + "".join(f'<a href="/{i}">L{i}</a>' for i in range(10)) + "</p>"
    out = md(html, Markdown(links=Markdown.Links(style="reference")))
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
    assert "\n".join(line.rstrip() for line in md(html, opts).splitlines()) == expected
