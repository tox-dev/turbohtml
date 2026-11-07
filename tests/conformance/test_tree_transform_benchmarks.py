from __future__ import annotations

import shutil
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

import pytest

from turbohtml import Html, parse, parse_fragment
from turbohtml.clean import collapse_whitespace_node, strip_comments_node, transform_node

if TYPE_CHECKING:
    from collections.abc import Callable

    from bench.timing import Mutating
    from lxml.html import HtmlElement
    from pytest_mock import MockerFixture
    from selectolax.lexbor import LexborHTMLParser

_MUTATORS: Final[tuple[str, ...]] = ("lxml", "beautifulsoup4", "beautifulsoup4_lxml", "selectolax")
_OUTPUTS: Final[tuple[str, ...]] = (*_MUTATORS, "html5lib", "pyquery", "parsel")
_MUTATIONS: Final[tuple[str, ...]] = ("collapse-whitespace", "strip-comments", "transform-tree")
_FRAGMENTS: Final[dict[str, str]] = {
    "comment-tail": "<p>a  <!--note--> b <b>c  d</b> e</p>",
    "unicode-escaping": "<p> café\t \u03b1\n😀 &amp; &lt; &gt; \u00a0 x </p>",
    "preserved": "<pre>a  b\n c</pre><textarea>d  e</textarea><p>x  y</p>",
    "raw-text": '<script>if (a < b) x = "  ";</script><style> a  b {}</style>',
    "template": "<template><p>a  <!--note--> b</p></template>",
    "foreign": "<svg><text>a  b</text></svg><p>x  y</p>",
    "root-text": "lead &amp; <br> tail <!--one--><!--two-->",
    "empty-adjacent-text": "<p>a <!--one--> <!--two--> b</p>",
}
_CASES: Final = tuple(pytest.param(fragment, id=name) for name, fragment in _FRAGMENTS.items())
_RENDERERS: Final[dict[str, Callable[[Any], str]]] = {
    "lxml": lambda tree: import_module("lxml.html").tostring(cast("HtmlElement", tree), encoding="unicode"),
    "beautifulsoup4": str,
    "beautifulsoup4_lxml": str,
    "selectolax": lambda tree: cast("str", cast("LexborHTMLParser", tree).html),
}
_NODE_BENCH: Final = Path(__file__).parents[2] / "tools/bench/node/node_modules"


def _as_text(output: str | bytes) -> str:
    return output.decode() if isinstance(output, bytes) else output


@pytest.mark.parametrize(
    ("library", "operation", "fragment"),
    [
        pytest.param(library, operation, fragment, id=f"{library}-{operation}-{name}")
        for library in _MUTATORS
        for operation in _MUTATIONS
        for name, fragment in _FRAGMENTS.items()
        if not (library == "selectolax" and "<template>" in fragment)
    ],
)
def test_mutation(library: str, operation: str, fragment: str) -> None:
    module: Final = pytest.importorskip(f"bench.competitors.{library}", exc_type=ImportError)
    mutation: Final = cast("Mutating", module.OPERATIONS[operation][0])
    source: Final = f"<html><head></head><body>{fragment}</body></html>"
    tree: Final = mutation.setup(source)
    mutation.run(tree)
    output: Final = _RENDERERS[library](tree)
    expected: Final = parse(source)
    transform_node(
        expected,
        *{
            "collapse-whitespace": (collapse_whitespace_node,),
            "strip-comments": (strip_comments_node,),
            "transform-tree": (strip_comments_node, collapse_whitespace_node),
        }[operation],
    )
    assert parse(output).serialize(Html(sort_attributes=True)) == expected.serialize(Html(sort_attributes=True))


@pytest.mark.parametrize("operation", _MUTATIONS)
def test_selectolax_refuses_template_contents(operation: str) -> None:
    module: Final = pytest.importorskip("bench.competitors.selectolax", exc_type=ImportError)
    mutation: Final = cast("Mutating", module.OPERATIONS[operation][0])
    with pytest.raises(ValueError, match="template contents"):
        mutation.setup(f"<html><head></head><body>{_FRAGMENTS['template']}</body></html>")


@pytest.mark.parametrize("library", _OUTPUTS)
@pytest.mark.parametrize("operation", ["serialize-inner", "encode-inner"])
@pytest.mark.parametrize("fragment", _CASES)
def test_inner_output(library: str, operation: str, fragment: str) -> None:
    module: Final = pytest.importorskip(f"bench.competitors.{library}", exc_type=ImportError)
    run: Final = cast("Callable[[str], str | bytes]", module.OPERATIONS[operation][0])
    output: Final = run(f"<html><head></head><body>{fragment}</body></html>")
    assert parse_fragment(_as_text(output)).serialize(Html(sort_attributes=True)) == parse_fragment(fragment).serialize(
        Html(sort_attributes=True)
    )


@pytest.mark.parametrize("library", ["lxml", "beautifulsoup4", "beautifulsoup4_lxml"])
@pytest.mark.parametrize("operation", ["serialize-inner-indent", "encode-inner-indent"])
def test_pretty_inner_output(library: str, operation: str) -> None:
    module: Final = pytest.importorskip(f"bench.competitors.{library}", exc_type=ImportError)
    run: Final = cast("Callable[[str], str | bytes]", module.OPERATIONS[operation][0])
    output: Final = run("<html><head></head><body><div><p>one</p><p>two</p></div></body></html>")
    tree: Final = parse_fragment(_as_text(output))
    assert ([element.text.strip() for element in tree.find_all("p")], bool(tree.find_all("body"))) == (
        ["one", "two"],
        False,
    )


@pytest.mark.parametrize("operation", ["serialize-inner-minify", "encode-inner-minify"])
@pytest.mark.parametrize("fragment", _CASES[1:4])
def test_html5lib_minified_output(operation: str, fragment: str) -> None:
    module: Final = pytest.importorskip("bench.competitors.html5lib", exc_type=ImportError)
    run: Final = cast("Callable[[str], str | bytes]", module.OPERATIONS[operation][0])
    output: Final = run(f"<html><head></head><body>{fragment}</body></html>")
    expected: Final = parse_fragment(fragment)
    transform_node(expected, strip_comments_node, collapse_whitespace_node)
    assert parse_fragment(_as_text(output)).serialize() == expected.serialize()


@pytest.mark.parametrize("fragment", _CASES[:5])
def test_html5lib_whitespace_roundtrip(fragment: str) -> None:
    module: Final = pytest.importorskip("bench.competitors.html5lib", exc_type=ImportError)
    run: Final = cast("Callable[[str], str]", module.OPERATIONS["whitespace-roundtrip"][0])
    source: Final = f"<html><head></head><body>{fragment}</body></html>"
    expected: Final = parse(source)
    collapse_whitespace_node(expected)
    assert parse(run(source)).serialize() == expected.serialize()


@pytest.mark.parametrize(
    "library",
    [
        pytest.param(
            name,
            marks=pytest.mark.skipif(
                shutil.which("node") is None or not (_NODE_BENCH / name).exists(),
                reason="Node and the benchmark npm dependencies are required",
            ),
        )
        for name in ("parse5", "jsdom")
    ],
)
@pytest.mark.parametrize("operation", ["parse-inner", "parse-inner-encode"])
def test_javascript_inner_output(library: str, operation: str) -> None:
    run: Final = cast(
        "Callable[[str], str | bytes]", import_module(f"bench.competitors.{library}").OPERATIONS[operation][0]
    )
    fragment: Final = "a &amp; <b>café 😀</b><!--note--><template><p>one</p></template>"
    output: Final = run(f"<html><head></head><body>{fragment}</body></html>")
    assert parse_fragment(_as_text(output)).serialize() == parse_fragment(fragment).serialize()


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param("<p>a <!--comment--> b</p>", id="comment-boundary"),
        pytest.param("<template><p>a <!--comment--> b</p></template>", id="template-comment"),
    ],
)
def test_html5lib_minification_token_boundaries(fragment: str) -> None:
    module: Final = pytest.importorskip("bench.competitors.html5lib", exc_type=ImportError)
    run: Final = cast("Callable[[str], str]", module.OPERATIONS["serialize-inner-minify"][0])
    output: Final = parse_fragment(run(f"<html><head></head><body>{fragment}</body></html>"))
    expected: Final = parse_fragment(fragment)
    transform_node(expected, strip_comments_node, collapse_whitespace_node)
    assert (output.find_all("p")[0].text, expected.find_all("p")[0].text) == ("a  b", "a b")


def test_sanitize_node_builds_a_new_tree() -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml_html_clean", exc_type=ImportError)
    mutation: Final = cast("Mutating", module.OPERATIONS["sanitize-node"][0])
    tree: Final = cast(
        "HtmlElement", mutation.setup('<p onclick="bad()">https://example.com<script>bad()</script></p>')
    )
    output: Final = cast("HtmlElement", mutation.run(tree))
    assert (output is tree, len(tree.xpath(".//script")), len(output.xpath(".//script"))) == (False, 1, 0)


def test_linkify_node_edits_the_tree_in_place() -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml_html_clean", exc_type=ImportError)
    mutation: Final = cast("Mutating", module.OPERATIONS["linkify-node"][0])
    tree: Final = cast(
        "HtmlElement", mutation.setup('<p onclick="bad()">https://example.com<script>bad()</script></p>')
    )
    output: Final = cast("HtmlElement", mutation.run(tree))
    assert (output is tree, output.xpath(".//a/@href")) == (True, ["https://example.com"])


@pytest.mark.parametrize(
    ("operation", "serializer", "source", "expected"),
    [
        pytest.param(
            "build-e",
            "lxml.etree",
            0,
            b"<ul></ul>",
            id="builder-html",
        ),
        pytest.param(
            "emit",
            "lxml.html",
            2,
            b'<ul><li class="item" data-i="0">item 0</li><li class="item" data-i="1">item 1</li></ul>',
            id="cached-html",
        ),
        pytest.param(
            "canonicalize",
            "lxml.etree",
            '<p z="2" a="1">a<br></p>',
            b'<html><body><p a="1" z="2">a<br></br></p></body></html>',
            id="c14n",
        ),
        pytest.param(
            "strip-remove",
            "lxml.html",
            "<p>a<code>x</code>b</p>",
            b"<html><body><p>ab</p></body></html>",
            id="preserve-tail",
        ),
        pytest.param(
            "strip-tags",
            "lxml.html",
            "<p>a<code>x</code>b</p>",
            b"<html><body><p>axb</p></body></html>",
            id="unwrap-text",
        ),
        pytest.param(
            "rewrite",
            "lxml.html",
            '<p><a href="/x">one</a><!--note--><img src="x"></p>',
            b'<html><body><p><a href="/x" rel="nofollow">one</a><img src="x" loading="lazy"></p></body></html>',
            id="rewrite-attributes",
        ),
    ],
)
def test_lxml_serialized_output(
    operation: str, serializer: str, source: str | int, expected: bytes, mocker: MockerFixture
) -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    output: Final = mocker.spy(import_module(serializer), "tostring")
    cast("Callable[[str | int], None]", module.OPERATIONS[operation][0])(source)
    assert output.spy_return == expected


def test_lxml_set_html_output() -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    mutation: Final = cast("Mutating", module.OPERATIONS["set-html"][0])
    tree: Final = cast("HtmlElement", mutation.setup("<p>original</p>"))
    mutation.run(tree)
    assert import_module("lxml.html").tostring(tree, encoding="unicode") == (
        '<html><body><p>Updated <a href="/x">link</a> and <b>bold</b>.</p>'
        "<ul><li>one</li><li>two</li></ul></body></html>"
    )


@pytest.mark.parametrize(
    ("operation", "source"),
    [
        pytest.param("parse-xml", "<root>", id="xml"),
        pytest.param(
            "is-valid",
            ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="root"/></xs:schema>', "<root>"),
            id="schema-document",
        ),
    ],
)
def test_lxml_rejects_malformed_xml(operation: str, source: str | tuple[str, str]) -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final = import_module("lxml.etree")
    with pytest.raises(etree.XMLSyntaxError, match="Premature end"):
        cast("Callable[[str | tuple[str, str]], None]", module.OPERATIONS[operation][0])(source)


def test_lxml_rejects_invalid_schema() -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    etree: Final = import_module("lxml.etree")
    schema: Final = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="root" type="xs:missing"/></xs:schema>'
    )
    with pytest.raises(etree.XMLSchemaParseError, match="does not resolve"):
        cast("Callable[[tuple[str, str]], None]", module.OPERATIONS["is-valid"][0])((schema, "<root/>"))


def test_lxml_parser_target_counts_real_events(mocker: MockerFixture) -> None:
    module: Final = pytest.importorskip("bench.competitors.lxml", exc_type=ImportError)
    parser: Final = mocker.spy(import_module("lxml.etree"), "HTMLParser")
    cast("Callable[[str], None]", module.OPERATIONS["htmlparser"][0])('<p a="1">text</p>')
    assert parser.call_args.kwargs["target"].close() == 10
