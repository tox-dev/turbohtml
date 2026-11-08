from __future__ import annotations

from typing import Final

import pytest
from markdown_it import MarkdownIt

from turbohtml import Element, Markdown, Text, parse


@pytest.mark.parametrize(
    "style",
    [
        pytest.param("white-space:pre", id="pre"),
        pytest.param("white-space:pre-wrap", id="pre-wrap"),
        pytest.param("white-space:break-spaces", id="break-spaces"),
        pytest.param("WHITE-SPACE: PRE-WRAP", id="case-insensitive"),
        pytest.param("color:red; white-space:pre-wrap; color:blue", id="other-properties"),
        pytest.param("white-space:normal;white-space:pre-wrap", id="last-declaration"),
        pytest.param("white-space:pre-wrap!important;white-space:normal", id="important-wins"),
        pytest.param("white-space:normal!important;white-space:pre-wrap ! IMPORTANT", id="last-important"),
        pytest.param("white-space:pre-wrap;white-space:unknown", id="ignore-invalid-value"),
        pytest.param("white-space:pre-wrap;white-space:normal!unknown", id="ignore-invalid-priority"),
        pytest.param("white-space:/**/pre-wrap/**/", id="comments"),
        pytest.param("white-space:pre-wrap/*", id="unclosed-comment"),
        pytest.param("broken;white-space:pre-wrap", id="invalid-declaration"),
        pytest.param("content:';white-space:normal;';white-space:pre-wrap", id="quoted-semicolon"),
        pytest.param('content:";white-space:normal;";white-space:pre-wrap', id="double-quoted-semicolon"),
        pytest.param("--custom:(white-space:normal;);white-space:pre-wrap", id="parentheses"),
        pytest.param("--custom:[white-space:normal;];white-space:pre-wrap", id="brackets"),
        pytest.param("--custom:{white-space:normal;};white-space:pre-wrap", id="braces"),
        pytest.param(r"content:'\';white-space:normal';white-space:pre-wrap", id="escaped-quote"),
        pytest.param(f"--long:{'x' * 100};white-space:pre-wrap", id="long-other-property"),
    ],
)
def test_inline_css_preserves_whitespace(style: str) -> None:
    assert parse(f'<p style="{style.replace(chr(34), "&quot;")}">  one\t two\n\nthree  </p>').to_markdown() == (
        "```\n  one\t two\n\nthree  \n```"
    )


@pytest.mark.parametrize(
    "style",
    [
        pytest.param("white-space:normal", id="normal"),
        pytest.param("white-space:nowrap", id="nowrap"),
        pytest.param("white-space:initial", id="initial"),
        pytest.param("white-space:revert", id="revert"),
        pytest.param("white-space:revert-layer", id="revert-layer"),
        pytest.param("white-space:inherit", id="inherit-normal"),
        pytest.param("white-space:unset", id="unset-normal"),
        pytest.param("white-space:invalid", id="invalid"),
        pytest.param("white-space:'pre-wrap'", id="quoted-keyword"),
        pytest.param("white-space:pre-wrap;white-space:normal", id="last-normal"),
        pytest.param("white-space:normal!important;white-space:pre-wrap", id="important-normal"),
        pytest.param("content:';white-space:pre-wrap'", id="ignore-quoted-property"),
        pytest.param("white-space:pre/**/-wrap", id="comment-separates-tokens"),
        pytest.param(f"white-space:pre-wrap {'x' * 100}", id="long-invalid-value"),
        pytest.param("/*white-space:pre-wrap", id="only-comment"),
    ],
)
def test_inline_css_normalizes_whitespace(style: str) -> None:
    assert parse(f'<p style="{style}">  one\t two\n\nthree  </p>').to_markdown() == "one two three"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("one<b>  two\nthree</b>", "one  two\nthree", id="inherit"),
        pytest.param(
            '<span style="white-space:inherit">one  two\nthree</span>', "one  two\nthree", id="explicit-inherit"
        ),
        pytest.param('<span style="white-space:unset">one  two\nthree</span>', "one  two\nthree", id="unset"),
        pytest.param('<span style="white-space:normal">one  two\nthree</span>', "one two three", id="normal-child"),
        pytest.param(
            'one<span style="white-space:normal">  two\nthree </span>four', "one two three four", id="normal-edges"
        ),
        pytest.param("<p>one</p><p>two</p>", "one\ntwo", id="paragraphs"),
        pytest.param("one<br>two", "one\ntwo", id="break"),
        pytest.param("one<!--comment--><script>skip</script>two", "onetwo", id="skip-hidden-text"),
        pytest.param("<span></span>one", "one", id="empty-child"),
        pytest.param(
            '<span style="white-space:normal"><pre>one  two\nthree</pre></span>', "one  two\nthree", id="pre-default"
        ),
    ],
)
def test_preserved_whitespace_inheritance(source: str, expected: str) -> None:
    assert parse(f'<div style="white-space:pre-wrap">{source}</div>').to_markdown() == f"```\n{expected}\n```"


def test_pre_line_collapses_spaces_and_preserves_lines() -> None:
    assert parse('<p style="white-space:pre-line">  one\t two \n  three\n\nfour </p>').to_markdown() == (
        "```\none two\nthree\n\nfour\n```"
    )


def test_css_whitespace_round_trip() -> None:
    markdown: Final = parse('<p style="white-space:pre-wrap">one\ntwo</p>').to_markdown()
    assert MarkdownIt().render(markdown) == "<pre><code>one\ntwo\n</code></pre>\n"


@pytest.mark.parametrize(
    ("source", "options", "expected"),
    [
        pytest.param(
            '<pre style="white-space:pre"><code class="language-python">one\ntwo</code></pre>',
            Markdown(),
            "```python\none\ntwo\n```",
            id="code-class",
        ),
        pytest.param(
            '<pre style="white-space:pre">one\ntwo</pre>',
            Markdown(code=Markdown.Code(language="python")),
            "```python\none\ntwo\n```",
            id="code-fallback",
        ),
        pytest.param(
            '<p style="white-space:pre">one\ntwo</p>',
            Markdown(code=Markdown.Code(language="python")),
            "```\none\ntwo\n```",
            id="prose-has-no-language",
        ),
    ],
)
def test_css_whitespace_keeps_code_language_rules(source: str, options: Markdown, expected: str) -> None:
    assert parse(source).to_markdown(options) == expected


def test_css_whitespace_inline_boundaries() -> None:
    assert parse('<p>before <span style="white-space:pre">one\ntwo</span> after</p>').to_markdown() == (
        "before\n\n```\none\ntwo\n```\n\nafter"
    )


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        pytest.param(
            'style="white-space:pre"',
            "| <pre>one&#32;&#32;two&#10;&#60;&#38;&#62;&#124;</pre> |\n| --- |",
            id="preserved",
        ),
        pytest.param('class="cell"', "| one two \\<&>\\| |\n| --- |", id="unrelated-attribute"),
    ],
)
def test_css_whitespace_table_cell(attributes: str, expected: str) -> None:
    assert parse(f"<table><tr><td {attributes}>one  two\n&lt;&amp;&gt;|</td></tr></table>").to_markdown() == expected


def test_css_whitespace_deep_tree() -> None:
    source: Final = f'<div style="white-space:pre">{"<span>" * 1000}one\ntwo{"</span>" * 1000}</div>'
    assert parse(source).to_markdown() == "```\none\ntwo\n```"


def test_css_whitespace_stripped_table() -> None:
    assert (
        parse('<table><tr><td style="white-space:pre">one  two\nthree</td></tr></table>').to_markdown(
            Markdown(tables=Markdown.Tables(mode="strip"))
        )
        == "```\none  two\nthree\n```"
    )


def test_css_whitespace_empty_text_node() -> None:
    element: Final = Element("div", {"style": "white-space:pre"})
    element.append(Text(""))
    element.append(Text("one\ntwo"))
    assert element.to_markdown() == "```\none\ntwo\n```"


def test_css_whitespace_does_not_format_selected_script() -> None:
    element: Final = parse('<script style="white-space:pre">hidden</script>').find("script")
    assert element is not None
    assert element.to_markdown() == "hidden"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<h1><span style="white-space:pre">one\ntwo</span></h1>', "# <pre>one&#10;two</pre>", id="heading"
        ),
        pytest.param(
            '<a href="https://example.com"><span style="white-space:pre">one\ntwo</span></a>',
            "[<pre>one&#10;two</pre>](https://example.com)",
            id="link",
        ),
    ],
)
def test_css_whitespace_inline_only_context(source: str, expected: str) -> None:
    assert parse(source).to_markdown() == expected


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('<div style="white-space:pre-wrap"><span>one\ntwo</span></div>', id="ancestor-style"),
        pytest.param('<div style="white-space:pre-wrap"><p><span>one\ntwo</span></p></div>', id="inherited-style"),
        pytest.param("<pre><span>one\ntwo</span></pre>", id="pre-parent"),
    ],
)
def test_css_whitespace_selected_subtree(source: str) -> None:
    element: Final = parse(source).find("span")
    assert element is not None
    assert element.to_markdown() == "```\none\ntwo\n```"
