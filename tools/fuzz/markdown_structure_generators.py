"""Reader wrappers must consume budgets even when rendered HTML has fewer nodes."""

from __future__ import annotations

import argparse
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import html5lib
from fuzz.structure_generators import (
    BudgetError,
    Generated,
    GenerationBudget,
    Grammar,
    Identifier,
    Production,
    ProductionFloorError,
    Reference,
    compile_grammar,
    generate,
    generation_sweep,
    write_corpus,
)
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from typing_extensions import override

from turbohtml import Node, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from xml.etree.ElementTree import Element as XmlElement

MarkdownRecord = tuple[str, str, str, tuple[tuple[str, str], ...], int]
_Meaning = tuple[str, tuple[tuple[str, str], ...], tuple["_Meaning", ...]]


def main(argv: Sequence[str] | None = None) -> int:
    """Write source bytes so corpus consumers need no mode-string interpretation."""
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=64)
    parser.add_argument("--steps", type=int, default=256)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--html", action="store_true")
    args: Final = parser.parse_args(argv)
    if args.count < 0:
        parser.error("count must be nonnegative")
    try:
        cases: Final = (
            tuple(
                markdown_generate(random.Random(args.seed + index), args.budget, steps=args.steps, html=args.html)
                for index in range(args.count)
            )
            if args.count
            else generation_sweep(markdown_grammar(html=args.html), budget=GenerationBudget(args.budget, args.steps))
        )
    except (BudgetError, ProductionFloorError) as error:
        parser.error(str(error))
    write_corpus(cases, args.output)
    return 0


def markdown_generate(rng: random.Random, budget: int = 64, *, steps: int = 256, html: bool = False) -> Generated:
    """Reader wrappers and HTML elements require different node costs."""
    return generate(markdown_grammar(html=html), rng, GenerationBudget(budget, steps))


def markdown_grammar(*, html: bool = False) -> Grammar:
    """Provenance and finite floors let corpus consumers audit coverage."""
    return _HTML_GRAMMAR if html else _RAW_GRAMMAR


def markdown_snapshot(source: str) -> tuple[MarkdownRecord, ...]:
    """Inline source spelling duplicates the semantic leaf text."""
    nodes: Final = tuple(SyntaxTreeNode(_READER.parse(source)).walk())
    return tuple(
        (
            node.type,
            "" if node.is_root else node.tag,
            "" if node.is_root or node.type == "inline" else node.content,
            () if node.is_root else tuple(sorted((name, str(value)) for name, value in node.attrs.items())),
            -1 if node.parent is None else nodes.index(node.parent),
        )
        for node in nodes
    )


def markdown_source_check(source: str, render: Callable[[Node], str] = Node.to_markdown) -> str | None:
    """Check the first conversion because repeated passes can stabilize a loss."""
    return markdown_html_check(_READER.render(source), render)


def markdown_html_check(markup: str, render: Callable[[Node], str] = Node.to_markdown) -> str | None:
    """Compare source meaning because a stable conversion can lose HTML semantics."""
    expected: Final = _html_meaning(markup)
    converted: Final = _READER.render(render(parse_fragment(markup)))
    return None if _html_meaning(converted) == expected else "Markdown conversion changes meaning"


def _html_meaning(markup: str) -> tuple[_Meaning, ...]:
    return _flow(
        _children(cast("XmlElement", html5lib.parseFragment(markup, namespaceHTMLElements=False)), preserve=False)
    )


def _flow(records: tuple[_Meaning, ...]) -> tuple[_Meaning, ...]:
    output: Final[list[_Meaning]] = []
    inline: Final[list[_Meaning]] = []
    for record in records:
        if record[0] in _FLOW_INLINE:
            inline.append(record)
        else:
            if inline:
                output.append(("p", (), _block_end(tuple(inline))))
                inline.clear()
            output.append(record)
    if inline:
        output.append(("p", (), _block_end(tuple(inline))))
    return tuple(output)


def _block_end(records: tuple[_Meaning, ...]) -> tuple[_Meaning, ...]:
    # a hard break that ends a block draws no line in a browser and does nothing in CommonMark (6.7)
    end = len(records)
    while end and records[end - 1][0] == "br":
        end -= 1
    if end == len(records) or not end or records[end - 1][0] != "#text":
        return records[:end]
    text: Final = records[end - 1][1][0][1].rstrip(" \t\n\f\r")
    return (*records[: end - 1], *((("#text", (("value", text),), ()),) if text else ()))


def _children(element: XmlElement, *, preserve: bool) -> tuple[_Meaning, ...]:
    records: Final[list[_Meaning]] = list(_text(element.text, preserve=preserve, keep=element.tag in _INLINE_HOSTS))
    for child in element:
        records.extend(_element(child, element.tag, preserve=preserve))
        records.extend(_text(child.tail, preserve=preserve, keep=element.tag in _INLINE_HOSTS))
    merged: Final[list[_Meaning]] = []
    for record in records:
        if record[0] == "#text" and merged and merged[-1][0] == "#text":
            merged[-1] = ("#text", (("value", merged[-1][1][0][1] + record[1][0][1]),), ())
        else:
            merged.append(record)
    spaced: Final = _spacing(tuple(merged), preserve=preserve, inline=element.tag in _INLINE_TEXT)
    return spaced if preserve or element.tag in _INLINE_TEXT else _block_end(spaced)


def _spacing(records: tuple[_Meaning, ...], *, preserve: bool, inline: bool) -> tuple[_Meaning, ...]:
    output: Final[list[_Meaning]] = []
    for index, record in enumerate(records):
        if record[0] != "#text" or preserve:
            output.append(record)
            continue
        text: str = re.sub(r"[ \t\n\f\r]+", " ", record[1][0][1])
        previous: Final = records[index - 1][0] if index else None
        if (previous is None and not inline) or (
            previous is not None and (previous not in _FLOW_INLINE or previous == "br")
        ):
            text = text.lstrip(" \t\n\f\r")
        following: Final = records[index + 1][0] if index + 1 < len(records) else None
        if (following is None and not inline) or (following is not None and following not in _FLOW_INLINE):
            text = text.rstrip(" \t\n\f\r")
        if text:
            output.append(("#text", (("value", text),), ()))
    return tuple(output)


def _element(element: XmlElement, parent: str, *, preserve: bool) -> tuple[_Meaning, ...]:
    if not isinstance(element.tag, str) or element.tag in {"script", "style", "head", "meta", "title"}:
        return ()
    _check_profile(element, parent)
    children: Final = _children(element, preserve=preserve or element.tag == "pre")
    if element.tag == "p" and not children:
        return ()
    if (
        element.tag in {"div", "span", "thead", "tbody"}
        or (element.tag, parent) in {("p", "li"), ("code", "pre")}
        # an <a> without href is a placeholder (WHATWG 4.5.1) and Markdown has no link without a destination
        or (element.tag == "a" and "href" not in element.attrib)
    ):
        return children
    tag: Final = {"b": "strong", "i": "em", "del": "s", "th": "td"}.get(element.tag, element.tag)
    if tag in _CONTENT_REQUIRED and not children:
        return ()
    if tag in _EMPHASIS:
        return _emphasis(tag, children)
    if tag in {"pre", "code"}:
        return ((tag, (), _code_text(children, block=tag == "pre")),)
    return ((tag, _attributes(tag, element.attrib), children),)


def _check_profile(element: XmlElement, parent: str) -> None:
    if element.tag not in _ALLOWED:
        msg = f"HTML element has no declared Markdown meaning: {element.tag}"
        raise MarkdownProfileError(msg)
    if (element.tag == "li" and parent not in {"ul", "ol"}) or (
        (allowed := _CONTENT_MODEL.get(element.tag)) is not None
        and (
            any(isinstance(child.tag, str) and child.tag not in allowed for child in element)
            or any(text.strip(" \t\n\f\r") for text in (element.text or "", *(child.tail or "" for child in element)))
        )
    ):
        msg = f"HTML content model violated at {element.tag}"
        raise MarkdownProfileError(msg)


def _emphasis(tag: str, children: tuple[_Meaning, ...]) -> tuple[_Meaning, ...]:
    if all(record[0] == "#text" and not record[1][0][1].strip(" \t\n\f\r") for record in children):
        return children  # a delimiter run cannot wrap whitespace alone (CommonMark 6.2)
    # emphasis does not style a hard break, so a break at its edge renders the same outside it
    start: Final = next((index for index, record in enumerate(children) if record[0] != "br"), len(children))
    end: Final = next((index for index in range(len(children), start, -1) if children[index - 1][0] != "br"), start)
    inner: list[_Meaning] = list(children[start:end])
    before: Final[list[_Meaning]] = list(children[:start])
    after: Final[list[_Meaning]] = list(children[end:])
    # a delimiter run touching whitespace cannot open or close (CommonMark 6.2), so edge spaces sit outside it
    if inner and inner[0][0] == "#text" and (text := inner[0][1][0][1]) != (stripped := text.lstrip(" \t\n\f\r")):
        before.append(("#text", (("value", text[: len(text) - len(stripped)]),), ()))
        inner[0] = ("#text", (("value", stripped),), ())
    if inner and inner[-1][0] == "#text" and (text := inner[-1][1][0][1]) != (stripped := text.rstrip(" \t\n\f\r")):
        after.insert(0, ("#text", (("value", text[len(stripped) :]),), ()))
        inner[-1] = ("#text", (("value", stripped),), ())
    return (*before, *(((tag, (), tuple(inner)),) if inner else ()), *after)


def _attributes(tag: str, attributes: dict[str, str]) -> tuple[tuple[str, str], ...]:
    # an empty title gives no advisory information (WHATWG 3.2.6.1), the same as none
    semantic: Final = {
        name: value
        for name, value in attributes.items()
        if name in _SEMANTIC_ATTRIBUTES.get(tag, ()) and (value or name != "title")
    }
    # image syntax always writes both attributes (CommonMark 6.4)
    return tuple(sorted((({"src": "", "alt": ""} if tag == "img" else {}) | semantic).items()))


def _code_text(children: tuple[_Meaning, ...], *, block: bool) -> tuple[_Meaning, ...]:
    # code holds literal text only (CommonMark 4.5, 6.1): a block keeps its line breaks, a span turns them to spaces,
    # and fenced content always ends in a newline that a browser does not draw
    text: Final = _plain_text(children).removesuffix("\n") if block else _plain_text(children).replace("\n", " ")
    return (("#text", (("value", text),), ()),) if text else ()


def _plain_text(records: tuple[_Meaning, ...]) -> str:
    text = ""
    for record in records:
        if record[0] == "#text":
            text += record[1][0][1]
        elif record[0] == "br":
            text += "\n"
        elif record[0] in _FLOW_INLINE:
            text += _plain_text(record[2])
        elif inner := _plain_text(record[2]):
            text += ("" if not text or text.endswith("\n") else "\n") + inner.removesuffix("\n") + "\n"
    return text


def _text(text: str | None, *, preserve: bool, keep: bool) -> tuple[_Meaning, ...]:
    if not text:
        return ()
    if not preserve:
        text = re.sub(r"[ \t\n\f\r]+", " ", text)
        if not keep and not text.strip(" \t\n\f\r"):
            return ()
    return (("#text", (("value", text),), ()),)


def markdown_source_seeds(*, html: bool = False) -> list[str]:
    """Force named productions to make corpus coverage reproducible."""
    return [
        case.data.decode("utf-8")
        for case in generation_sweep(markdown_grammar(html=html), budget=GenerationBudget(64, 256))
    ]


def markdown_controls(*, html: bool = False) -> dict[str, bool]:
    """Stable losses distinguish first-conversion and repeated-pass checks."""
    check: Final = markdown_html_check if html else markdown_source_check
    sources: Final = (
        ("<p><strong>x</strong></p>", '<p><a href="/target">x</a></p>', "<p>x</p>")
        if html
        else ("**x**", "[x](/target)", "x")
    )
    return {
        "lost emphasis": check(sources[0], lambda node: node.text) is not None,
        "changed destination": check(sources[1], lambda node: node.to_markdown().replace("/target", "/changed"))
        is not None,
        "empty conversion": check(sources[2], lambda _node: "") is not None,
    }


class MarkdownProfileError(ValueError):
    """Keep unsupported HTML semantics outside the Markdown conversion profile."""


class _FaithfulMarkdown(MarkdownIt):
    """Keep link spelling and policy independent of the reader's URL normalization."""

    @override
    def normalizeLink(self, url: str) -> str:
        return url

    @override
    def validateLink(self, url: str) -> bool:
        return True


def _raw_grammar() -> Grammar:
    productions: Final = [
        Production("markdown:root", "root", (Reference("definition", 0), Reference("blocks", 1)), 1, _COMMONMARK),
        Production(
            "markdown:definition",
            "definition",
            (b"[", Identifier("reference", definition=True), b"]: /target\n\n"),
            0,
            _COMMONMARK,
        ),
        Production("markdown:single", "blocks", (Reference("block", 0),), 0, _COMMONMARK),
        Production(
            "markdown:siblings",
            "blocks",
            (Reference("block", 0), b"\n\n---\n\n", Reference("blocks", 0)),
            1,
            _COMMONMARK,
        ),
        Production(
            "markdown:reference",
            "block",
            (b"[", Identifier("reference", definition=False), b"]\n"),
            4,
            _COMMONMARK,
            3,
        ),
    ]
    productions.extend(
        Production(
            f"markdown:{name}",
            "block",
            (source.encode("utf-8"),),
            nodes,
            _GFM if name in {"table", "strike"} else _COMMONMARK,
            height,
        )
        for name, source, nodes, height in _RAW
    )
    return compile_grammar(productions, "root")


def _html_grammar() -> Grammar:
    productions: Final = [
        Production(
            "markdown-html:root",
            "root",
            (
                b'<div id="',
                Identifier("target", definition=True),
                b'">',
                Reference("blocks", 2),
                b'<p><a href="#',
                Identifier("target", definition=False),
                b'">target</a></p></div>',
            ),
            5,
            _COMMONMARK,
            4,
        ),
        Production("markdown-html:single", "blocks", (Reference("block", 0),), 0, _COMMONMARK),
        Production(
            "markdown-html:siblings",
            "blocks",
            (Reference("block", 0), b"<hr>", Reference("blocks", 0)),
            1,
            _COMMONMARK,
        ),
    ]
    productions.extend(
        Production(
            f"markdown-html:{name}",
            "block",
            (source.encode("utf-8"),),
            nodes,
            _GFM if name in {"table", "s", "del"} else _COMMONMARK,
            height,
        )
        for name, source, nodes, height in _HTML
    )
    return compile_grammar(productions, "root")


_COMMONMARK: Final = "https://spec.commonmark.org/0.31.2/"
_GFM: Final = "https://github.github.com/gfm/"
MARKDOWN_READER_SOURCE: Final = (
    "https://github.com/executablebooks/markdown-it-py/tree/36c5f547144df2d01970a5792d68c71a3380b227"
)
MARKDOWN_RULES: Final = (
    "table",
    "code",
    "fence",
    "blockquote",
    "hr",
    "list",
    "reference",
    "html_block",
    "heading",
    "lheading",
    "paragraph",
    "text",
    "linkify-disabled",
    "newline",
    "escape",
    "backticks",
    "strikethrough",
    "emphasis",
    "link",
    "image",
    "autolink",
    "html_inline",
    "entity",
)
_RAW: Final = (
    ("heading1", "# x\n", 3, 2),
    ("heading2", "## x\n", 3, 2),
    ("heading3", "### x\n", 3, 2),
    ("heading4", "#### x\n", 3, 2),
    ("heading5", "##### x\n", 3, 2),
    ("heading6", "###### x\n", 3, 2),
    ("setext1", "x\n===\n", 3, 2),
    ("setext2", "x\n---\n", 3, 2),
    ("thematic", "---\n", 1, 0),
    ("indented", "    x\n", 1, 0),
    ("fence-backtick", "```\nx\n```\n", 1, 0),
    ("fence-tilde", "~~~\nx\n~~~\n", 1, 0),
    ("html-comment", "<!-- x -->\n", 1, 0),
    ("html-pi", "<?x?>\n", 1, 0),
    ("html-declaration", "<!DOCTYPE html>\n", 1, 0),
    ("html-cdata", "<![CDATA[x]]>\n", 1, 0),
    ("html-script", "<script>x</script>\n", 1, 0),
    ("html-block", "<p>x</p>\n", 1, 0),
    ("html-span", "<span>x</span>\n", 5, 2),
    ("html-tag", '<img src="/image" alt="x">\n', 1, 0),
    ("quote", "> x\n", 4, 3),
    ("bullet", "- x\n", 5, 4),
    ("ordered", "1. x\n", 5, 4),
    ("loose", "- x\n\n- y\n", 9, 4),
    ("nested", "- x\n  - y\n", 10, 6),
    ("table", "| a |\n|---|\n| b |\n", 11, 5),
    ("paragraph", "x\n", 3, 2),
    ("softbreak", "x\ny\n", 5, 2),
    ("hardbreak", "x  \ny\n", 5, 2),
    ("em-star", "*x*\n", 4, 3),
    ("em-underscore", "_x_\n", 4, 3),
    ("strong-star", "**x**\n", 6, 3),
    ("strong-underscore", "__x__\n", 6, 3),
    ("code", "`x`\n", 3, 2),
    ("strike", "~~x~~\n", 4, 3),
    ("link", "[x](/target)\n", 4, 3),
    ("image", "![x](/image)\n", 4, 3),
    ("autolink", "<https://e.test/>\n", 4, 3),
    ("email", "<a@e.test>\n", 4, 3),
    ("escape", "\\*x\\*\n", 3, 2),
    ("entity", "&amp;\n", 3, 2),
    ("html-inline", "a <em>x</em> b\n", 7, 2),
    ("linkify-disabled", "https://e.test/\n", 3, 2),
)
_HTML: Final = (
    ("paragraph", "<p>x</p>", 2, 1),
    ("heading1", "<h1>x</h1>", 2, 1),
    ("heading2", "<h2>x</h2>", 2, 1),
    ("heading3", "<h3>x</h3>", 2, 1),
    ("heading4", "<h4>x</h4>", 2, 1),
    ("heading5", "<h5>x</h5>", 2, 1),
    ("heading6", "<h6>x</h6>", 2, 1),
    ("thematic", "<hr>", 1, 0),
    ("code-block", "<pre><code>x\n</code></pre>", 3, 2),
    ("quote", "<blockquote><p>x</p></blockquote>", 3, 2),
    ("bullet", "<ul><li>x</li></ul>", 3, 2),
    ("ordered", "<ol><li>x</li></ol>", 3, 2),
    ("loose", "<ul><li><p>x</p></li><li><p>y</p></li></ul>", 7, 3),
    ("nested", "<ul><li>x<ul><li>y</li></ul></li></ul>", 6, 4),
    ("table", "<table><thead><tr><th>a</th></tr></thead><tbody><tr><td>b</td></tr></tbody></table>", 9, 4),
    ("em", "<p><em>x</em></p>", 3, 2),
    ("strong", "<p><strong>x</strong></p>", 3, 2),
    ("b", "<p><b>x</b></p>", 3, 2),
    ("i", "<p><i>x</i></p>", 3, 2),
    ("s", "<p><s>x</s></p>", 3, 2),
    ("del", "<p><del>x</del></p>", 3, 2),
    ("code", "<p><code>x</code></p>", 3, 2),
    ("link", '<p><a href="/target" title="t">x</a></p>', 3, 2),
    ("image", '<p><img src="/image" alt="x" title="t"></p>', 2, 1),
    ("break", "<p>x<br>y</p>", 4, 1),
    ("special", "<p>*x* [y] &amp; é漢</p>", 2, 1),
)
_FLOW_INLINE: Final = frozenset({"#text", "a", "em", "strong", "s", "img", "br", "code"})
_INLINE_HOSTS: Final = frozenset({
    "p",
    "li",
    "td",
    "th",
    "a",
    "em",
    "strong",
    "b",
    "i",
    "s",
    "del",
    "span",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
})
_INLINE_TEXT: Final = frozenset({"a", "em", "strong", "b", "i", "s", "del", "span"})
_ALLOWED: Final = _INLINE_HOSTS | {
    "div",
    "hr",
    "pre",
    "code",
    "blockquote",
    "ul",
    "ol",
    "table",
    "thead",
    "tbody",
    "tr",
    "img",
    "br",
}
_SEMANTIC_ATTRIBUTES: Final[dict[str, frozenset[str]]] = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title"}),
    "ol": frozenset({"start"}),
    "td": frozenset({"align"}),
}
# CommonMark and GFM write none of these without content: emphasis and code spans need a character (6.1, 6.2), a
# paragraph a non-blank line (4.8), a list an item (5.3) and a table a header cell (GFM 4.10)
_CONTENT_REQUIRED: Final = frozenset({"p", "strong", "em", "s", "code", "ul", "ol", "table", "tr"})
_EMPHASIS: Final = frozenset({"strong", "em", "s"})
# the WHATWG content models of the containers whose Markdown syntax can hold nothing else (4.4.5-4.4.8, 4.9)
_CONTENT_MODEL: Final[dict[str, frozenset[str]]] = {
    "ul": frozenset({"li", "script"}),
    "ol": frozenset({"li", "script"}),
    "table": frozenset({"thead", "tbody", "tr", "script"}),
    "thead": frozenset({"tr", "script"}),
    "tbody": frozenset({"tr", "script"}),
    "tr": frozenset({"td", "th", "script"}),
}
_READER: Final = _FaithfulMarkdown("gfm-like", {"linkify": False})
_RAW_GRAMMAR: Final = _raw_grammar()
_HTML_GRAMMAR: Final = _html_grammar()

__all__ = [
    "MARKDOWN_READER_SOURCE",
    "MARKDOWN_RULES",
    "MarkdownProfileError",
    "MarkdownRecord",
    "main",
    "markdown_controls",
    "markdown_generate",
    "markdown_grammar",
    "markdown_html_check",
    "markdown_snapshot",
    "markdown_source_check",
    "markdown_source_seeds",
]

if __name__ == "__main__":
    raise SystemExit(main())
