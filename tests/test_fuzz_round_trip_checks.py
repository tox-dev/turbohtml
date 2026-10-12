"""Each round-trip oracle passes turbohtml's output and flags a deliberately broken printer, reader or entry point."""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import runpy
import shutil
import sys
from collections import Counter
from dataclasses import replace
from functools import partial
from pathlib import Path
from subprocess import run  # ruff: ignore[suspicious-subprocess-import] - startup resolution needs a fresh interpreter.
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.atheris_content_targets import (
    content_targets,
    detector_check,
    minifier_check,
    minifier_observation,
    sanitizer_check,
    sanitizer_observation,
    stdlib_check,
    stdlib_observation,
    url_check,
)
from fuzz.atheris_corpora import CorpusProfile, write_corpora
from fuzz.atheris_corpora import main as corpus_main
from fuzz.atheris_corpora import main as fuzz_atheris_corpora_main
from fuzz.atheris_dom_targets import DomObservation, dom_observation, dom_targets
from fuzz.atheris_driver import fuzz
from fuzz.atheris_generation_targets import encoding_observation, generation_targets
from fuzz.atheris_header import BYTEWISE, HEADER_SIZE, SEED_HEADER, Header, feed_chunks, split_header
from fuzz.atheris_invariants import assert_invariant
from fuzz.atheris_javascript_targets import javascript_observation, javascript_targets
from fuzz.atheris_parser_targets import (
    document_observation,
    fragment_observation,
    incremental_observation,
    parser_targets,
    token_observation,
)
from fuzz.atheris_reference_targets import (
    cli_observation,
    computed_observation,
    conformance_observation,
    cssom_observation,
    reference_targets,
    render_observation,
    schema_observation,
    selector_observation,
    transform_observation,
)
from fuzz.atheris_registry import Target, validate_owners
from fuzz.atheris_runtime import (
    build_runtime,
    failure_hook,
    rejecting_callback,
    rejection_hook,
)
from fuzz.atheris_runtime import (
    main as fuzz_atheris_runtime_main,
)
from fuzz.atheris_stylesheet_targets import stylesheet_observation, stylesheet_targets
from fuzz.atheris_targets import MODULES, main, owner_inventory, public_modules, public_targets
from fuzz.css_custom_oracles import (
    UnsupportedCssCustomCaseError,
    css_custom_check,
    css_custom_controls,
    css_custom_generate,
    css_custom_seeds,
)
from fuzz.encoding_structure_generators import encoding_grammar
from fuzz.html_structure_generators import html_grammar_complete
from fuzz.idna_nfc_oracles import (
    UnsupportedIdnaNfcCaseError,
    idna_nfc_check,
    idna_nfc_controls,
    idna_nfc_generate,
    idna_nfc_seeds,
)
from fuzz.markdown_structure_generators import MarkdownProfileError, markdown_grammar
from fuzz.parser_byte_oracles import (
    UnsupportedParserCaseError,
    parser_bytes_check,
    parser_bytes_controls,
    parser_bytes_generate,
    parser_bytes_seeds,
)
from fuzz.round_trip_oracles import (
    ORACLES,
    Floor,
    OutOfScopeError,
    clean_url_check,
    css_semantics_check,
    encoding_decode_check,
    encoding_stream_check,
    fixpoint_check,
    html_check,
    idna_host_check,
    markdown_check,
    normalize_url_check,
    resolve_links_check,
    selector_entry_check,
    span_check,
    style_check,
    url_reparse_check,
    xml_check,
    xpath_entry_check,
)
from fuzz.round_trip_oracles import main as fuzz_css_custom_invariants_main
from fuzz.round_trip_oracles import (
    main as fuzz_encoding_invariants_main,
)
from fuzz.round_trip_oracles import main as fuzz_idna_invariants_main
from fuzz.round_trip_oracles import main as fuzz_idna_nfc_invariants_main
from fuzz.round_trip_oracles import main as fuzz_parser_byte_invariants_main
from fuzz.round_trip_oracles import (
    main as fuzz_resolve_links_invariants_main,
)
from fuzz.round_trip_oracles import (
    main as fuzz_url_invariants_main,
)
from fuzz.round_trip_oracles import main as fuzz_url_reparse_main
from fuzz.round_trip_oracles import main as fuzz_xml_island_invariants_main
from fuzz.round_trip_oracles import main as fuzz_xml_literal_invariants_main
from fuzz.structure_generators import GenerationBudget, Grammar, Identifier, Production, compile_grammar, generate
from fuzz.xml_grammar_oracles import (
    UnsupportedXmlLiteralCaseError,
    xml_literal_check,
    xml_literal_controls,
    xml_literal_generate,
    xml_literal_seeds,
)
from fuzz.xml_island_oracles import (
    UnsupportedXmlIslandCaseError,
    xml_island_check,
    xml_island_controls,
    xml_island_generate,
    xml_island_seeds,
)

from turbohtml import (
    CData,
    Comment,
    Element,
    Html,
    HTMLParseError,
    IncrementalParser,
    Node,
    SourceLocation,
    SourceSpan,
    Text,
    parse,
    parse_fragment,
    parse_xml,
)
from turbohtml.__main__ import main as fuzz_atheris_registry_main
from turbohtml.clean import Removed, minify_css, minify_css_inline
from turbohtml.convert import ExpressionError, css_to_xpath
from turbohtml.cssom import StyleDeclaration
from turbohtml.detect import EncodingDetector, EncodingMatch, detect
from turbohtml.extract import clean_url, normalize_url
from turbohtml.migration.stdlib import HTMLParser

if TYPE_CHECKING:
    from collections.abc import Callable


def _xml(node: Node) -> str:
    return node.serialize(Html(xml=True))


def _settles_on_numbers(text: str) -> str:
    # the first pass tags the text, the second shortens its number and changes nothing else
    return text.replace("1.50", "1.5") if text.startswith("#") else f"#{text} 1.50"


def _reject(_text: str) -> str:
    msg = "rejected"
    raise ValueError(msg)


def _reject_own_output(text: str) -> str:
    return _reject(text) if text.startswith("#") else f"#{text}"


@pytest.mark.parametrize(
    "name",
    [pytest.param(name, id=name) for name in sorted(ORACLES) if name != "js-names"],
)
def test_round_trip_negative_controls_fire(name: str) -> None:
    controls = ORACLES[name].controls()
    assert (len(controls) > 0, all(controls.values())) == (True, True)


@pytest.mark.parametrize(
    ("numeric", "expected"),
    [
        pytest.param(True, None, id="numeric-settles-on-third-pass"),
        pytest.param(False, "not a fixpoint: digit vs end", id="strict"),
    ],
)
def test_fixpoint_numeric_exception(*, numeric: bool, expected: str | None) -> None:
    assert fixpoint_check("w", _settles_on_numbers, numeric=numeric) == expected


def test_fixpoint_holds_for_minify_css() -> None:
    assert fixpoint_check("a{color:#ff0000;margin:0px 0px}", minify_css, numeric=True) is None


def test_fixpoint_skips_an_input_the_printer_rejects() -> None:
    with pytest.raises(OutOfScopeError):
        fixpoint_check("w", _reject, numeric=True)


def test_fixpoint_flags_a_printer_rejecting_its_own_output() -> None:
    assert fixpoint_check("w", _reject_own_output, numeric=True) == "rejects its own output"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<p>a &amp; b <b title='x\"y'>c</b></p>", id="escapes"),
        pytest.param("<p><table><d><form>", id="quirks-re-nesting-keeps-content"),
    ],
)
def test_html_check_passes_the_serializer(markup: str) -> None:
    assert html_check(markup) is None


def test_html_check_flags_a_serializer_that_drops_content() -> None:
    assert html_check("<p>a<b>c</b></p>", lambda node: node.serialize().replace("<b>", "", 1)) == (
        "document reparse changed content"
    )


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<plaintext>x", id="plaintext"),
        pytest.param("<pre>&#13;x</pre>", id="carriage-return"),
        pytest.param("<a><table><a>x", id="nested-anchor"),
        pytest.param("<script><!--<script></script>x", id="script-double-escape"),
    ],
)
def test_html_check_skips_trees_the_standard_does_not_round_trip(markup: str) -> None:
    with pytest.raises(OutOfScopeError):
        html_check(markup)


def test_xml_check_passes_the_serializer() -> None:
    assert xml_check("<p a=1>x<svg><circle r=1></circle></svg>") is None


@pytest.mark.parametrize(
    ("markup", "serialize", "expected"),
    [
        pytest.param(
            "<p>a&amp;b</p>",
            lambda node: _xml(node).replace("&amp;", "&"),
            "parse_xml rejects the XML serialization",
            id="not-well-formed",
        ),
        pytest.param(
            "<p a=1>x</p>",
            lambda node: _xml(node).replace(' a="1"', ""),
            "xml re-read tree differs: < vs <",
            id="dropped-attribute",
        ),
    ],
)
def test_xml_check_flags_a_broken_serializer(markup: str, serialize: Callable[[Node], str], expected: str) -> None:
    assert xml_check(markup, serialize) == expected


def test_xml_check_waives_the_tree_for_content_xml_cannot_hold() -> None:
    assert xml_check("<!--a--b--><p a=1>x", lambda node: _xml(node).replace(' a="1"', "")) is None


def test_style_check_passes_the_declaration_printer() -> None:
    assert style_check("color : RED;; margin:0 auto !important; x: 'a;b'") is None


def test_style_check_flags_a_reader_that_changes_a_value() -> None:
    assert style_check("a:b", lambda text: StyleDeclaration.parse(text.replace(": ", ":x", 1))) == (
        "declarations differ after re-read"
    )


def test_markdown_check_passes_to_markdown() -> None:
    assert markdown_check("<h2>t</h2><p>*a* and <em>b</em></p><ul><li>c</li></ul>") is None


def test_markdown_check_flags_unescaped_text() -> None:
    assert markdown_check("<p>*a*</p>", lambda node: node.text) == "not a fixpoint: '<' vs letter"


def test_markdown_check_skips_a_block_inside_an_inline() -> None:
    with pytest.raises(OutOfScopeError):
        markdown_check("<del><pre>x</pre></del>")


def test_css_semantics_check_passes_minify_css() -> None:
    markup = "<style>p{color:#ff0000;margin:0px 1px}p{margin-top:0}div>p{z-index:01}</style><div><p>x</p></div>"
    assert css_semantics_check(markup) is None


@pytest.mark.parametrize(
    ("markup", "minify", "expected"),
    [
        pytest.param(
            "<style>p{color:red}</style><p>x",
            lambda css: css.replace("red", "#00f"),
            "computed color: value changed",
            id="value-changed",
        ),
        pytest.param(
            "<style>p{z-index:1.}</style><p>x",
            lambda css: css.replace("1.", "1"),
            "computed integer: invalid value became valid",
            id="invalid-became-valid",
        ),
        pytest.param(
            "<style>p{margin-top:1px}</style><p>x",
            lambda css: css.replace("1px", "1xx"),
            "computed length: valid value became invalid",
            id="valid-became-invalid",
        ),
        pytest.param(
            "<style>p{x-unknown:1}</style><p>x",
            lambda css: "" if "color:red" in css else css,
            "selector rule dropped",
            id="selector-dropped",
        ),
        pytest.param(
            "<style>p{x-unknown:1}</style><p>x",
            lambda css: css.replace("p{color", "p::{color"),
            "minified selector does not parse",
            id="selector-unparsable",
        ),
        pytest.param(
            "<style>div>b{x-unknown:1}</style><div><p><b>x</b></p></div>",
            lambda css: css.replace(">", " "),
            "selector match set differs",
            id="selector-rewritten",
        ),
    ],
)
def test_css_semantics_check_flags_a_broken_minifier(markup: str, minify: Callable[[str], str], expected: str) -> None:
    assert css_semantics_check(markup, minify) == expected


def test_css_semantics_check_skips_a_document_without_style() -> None:
    with pytest.raises(OutOfScopeError):
        css_semantics_check("<p>x")


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("//p", id="node-set"),
        pytest.param("count(//p) div 0", id="number"),
        pytest.param("0 div 0", id="nan"),
        pytest.param("string(//p)", id="string"),
        pytest.param("//p[", id="error"),
    ],
)
def test_xpath_entry_check_passes_the_entry_points(expression: str) -> None:
    assert xpath_entry_check(json.dumps({"html": "<p>a</p><p>b</p>", "xpath": expression})) is None


@pytest.mark.parametrize(
    ("expression", "check", "expected"),
    [
        pytest.param(
            "//p",
            partial(xpath_entry_check, iterate=lambda node, expression: list(node.xpath_iter(expression))[1:]),
            "list(xpath_iter()) != xpath()",
            id="iterator-drops-an-item",
        ),
        pytest.param(
            "//p",
            partial(xpath_entry_check, first=lambda node, expression: list(node.xpath_iter(expression))[-1]),
            "xpath_one() != first item",
            id="one-returns-the-last",
        ),
        pytest.param(
            "//p[",
            partial(xpath_entry_check, first=lambda _node, _expression: None),
            "entry points disagree on raising",
            id="one-swallows-the-error",
        ),
    ],
)
def test_xpath_entry_check_flags_a_broken_entry_point(
    expression: str, check: Callable[[str], str | None], expected: str
) -> None:
    assert check(json.dumps({"html": "<p>a</p><p>b</p>", "xpath": expression})) == expected


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("div > .a, p:first-child", id="translatable"),
        pytest.param(":dir(ltr)", id="untranslatable"),
    ],
)
def test_selector_entry_check_passes_the_entry_points(selector: str) -> None:
    case = {"html": "<!DOCTYPE html><div><p class=a dir=ltr>x</p><p>y</p></div>", "css": selector}
    assert selector_entry_check(json.dumps(case)) is None


def test_selector_entry_check_flags_a_broken_translation() -> None:
    case = json.dumps({"html": "<p>x</p>", "css": "p"})
    assert selector_entry_check(case, lambda selector: css_to_xpath(selector, prefix="child::")) == (
        "xpath(css_to_xpath()) != select()"
    )


def test_selector_entry_check_skips_an_invalid_selector() -> None:
    with pytest.raises(OutOfScopeError):
        selector_entry_check(json.dumps({"html": "<p>x</p>", "css": "p["}))


def test_span_check_passes_the_parser() -> None:
    assert span_check("<!DOCTYPE html><div class=a\r\nid=b>x</div>\r\n<p>y<img src=x><b>z</b>") is None


def _late_start(location: SourceLocation) -> SourceLocation:
    start = location.start_tag
    return location._replace(
        start_tag=start._replace(start_offset=start.start_offset + 1, start_col=start.start_col + 1)
    )


def _attribute_outside(location: SourceLocation) -> SourceLocation:
    return location._replace(attrs=dict.fromkeys(location.attrs, location.end_tag or location.start_tag))


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        pytest.param(_late_start, "start tag span does not slice <name ...>", id="start-one-late"),
        pytest.param(
            lambda location: location._replace(end_tag=location.start_tag),
            "end tag span does not slice </name> after the start tag",
            id="end-tag-at-the-start-tag",
        ),
        pytest.param(_attribute_outside, "attribute span outside its start tag or overlapping", id="attribute-outside"),
    ],
)
def test_span_check_flags_broken_spans(edit: Callable[[SourceLocation], SourceLocation], expected: str) -> None:
    def locate(element: Element) -> SourceLocation | None:
        return None if (location := element.source_location) is None else edit(location)

    assert span_check("<p class=a>x</p>", locate) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(Header(7, 5, 3).encode() + b"<p>", (Header(7, 5, 3), b"<p>"), id="fields-then-payload"),
        pytest.param(
            Header(0, 300, 40).encode() + b"<p>", (Header(0, 300 % 115, 40 % 17), b"<p>"), id="modulo-by-size"
        ),
        pytest.param(Header(0, 0, 0).encode(), (Header(0, 0, 1), b""), id="zero-chunk-reads-as-one"),
        pytest.param(b"\x01\x02", (Header(0x0102, 0, 1), b""), id="short-input-reads-fewer-bytes"),
    ],
)
def test_atheris_header_split(data: bytes, expected: tuple[Header, bytes]) -> None:
    assert split_header(data) == expected


def test_atheris_header_seed_layout() -> None:
    assert (12, bytes(8) + (256).to_bytes(4, "big")) == (HEADER_SIZE, SEED_HEADER)


@pytest.mark.parametrize(
    ("data", "max_chunk", "expected"),
    [
        pytest.param(b"", 4, [b"", b"", b""], id="empty"),
        pytest.param(b"abcde", 2, [b"", b"ab", b"", b"cd", b"", b"e", b""], id="pieces"),
        pytest.param(
            b"a" * 150, 1, [b"", *(piece for _ in range(50) for piece in (b"a", b"")), b"a" * 100, b""], id="cap"
        ),
    ],
)
def test_atheris_header_feed_chunks_bytes(data: bytes, max_chunk: int, expected: list[bytes]) -> None:
    fed: list[bytes] = []
    feed_chunks(fed.append, data, max_chunk)
    assert fed == expected


def test_atheris_header_feed_chunks_text() -> None:
    fed: list[str] = []
    feed_chunks(fed.append, "水😀x", 1)
    assert fed == ["", "水", "", "😀", "", "x", ""]


def test_atheris_invariant_raises_oracle_failure() -> None:
    with pytest.raises(AssertionError, match=r"^not a fixpoint$"):
        assert_invariant(lambda _text: "not a fixpoint", "x")


def test_atheris_invariant_skips_out_of_scope_case() -> None:
    def out_of_scope(text: str) -> str | None:
        raise OutOfScopeError(text)

    assert_invariant(out_of_scope, "x")


def test_atheris_invariant_passes_held_oracle() -> None:
    seen: list[str] = []
    assert_invariant(seen.append, "x")
    assert seen == ["x"]


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("name", "data"),
    [
        pytest.param("markdown-source", b"**kept**\n", id="markdown"),
        pytest.param("markdown-html", b"<p><strong>kept</strong></p>", id="html"),
        pytest.param("encoding-bytes", b"<p>kept</p><!--comment-->tail", id="encoding"),
    ],
)
def test_generation_public_consumers_accept_domain_bytes(name: str, data: bytes) -> None:
    target: Final = next(target for target in public_targets() if target.name == name)
    target.callback(data)
    assert (target.name, target.exports) == (name, ())


@pytest.mark.parametrize("name", ["markdown-source", "markdown-html"])
def test_generation_public_consumers_reject_non_utf8(name: str) -> None:
    target: Final = next(target for target in public_targets() if target.name == name)
    with pytest.raises(UnicodeDecodeError):
        target.callback(b"\xff")


@pytest.mark.parametrize(
    ("name", "source"),
    [
        pytest.param("markdown-source", b"<video>unsupported</video>\n", id="markdown"),
        pytest.param("markdown-html", b"<video>unsupported</video>", id="html"),
    ],
)
def test_generation_public_consumers_reject_undeclared_profile(name: str, source: bytes) -> None:
    target: Final = next(target for target in public_targets() if target.name == name)
    with pytest.raises(MarkdownProfileError, match="no declared Markdown meaning"):
        target.callback(source)


@pytest.mark.parametrize(
    ("data", "encoding", "sniff", "expected"),
    [
        pytest.param(b"<p>\xe9</p>", "windows-1252", False, "é", id="fixed"),
        pytest.param(b"\xef\xbb\xbf<p>\xc3\xa9</p>", "windows-1252", False, "é", id="utf8-bom-overrides-fixed"),
        pytest.param(
            b"\xff\xfe" + "<p>é</p>".encode("utf-16-le"), "UTF-16BE", False, "é", id="utf16-bom-overrides-fixed"
        ),
        pytest.param(b"\xff\xfe" + "<p>é</p>".encode("utf-16-le"), "UTF-8", True, "é", id="utf16-bom"),
        pytest.param(b"<meta charset=windows-1252><p>\xe9</p>", "UTF-8", True, "é", id="prescan"),
        pytest.param(b" " * 1025 + b"<meta charset=windows-1251><p>\xc0</p>", "UTF-8", True, "\u0410", id="late-meta"),
    ],
)
def test_encoding_mutation_context(data: bytes, encoding: str, expected: str, *, sniff: bool) -> None:
    assert encoding_observation(data, encoding, sniff=sniff) == expected


def test_generation_unknown_execution_codec() -> None:
    with pytest.raises(LookupError):
        generation_targets("missing")


@pytest.mark.parametrize(
    ("profile", "grammar", "production", "context"),
    [
        pytest.param("markdown-source", markdown_grammar(), "markdown:reference", (None, False), id="markdown"),
        pytest.param("markdown-html", markdown_grammar(html=True), "markdown-html:thematic", (None, False), id="html"),
        pytest.param(
            "encoding", encoding_grammar(), "encoding:codec:windows-1252", ("windows-1252", False), id="fixed"
        ),
        pytest.param("encoding", encoding_grammar(), "encoding:bom:utf-16le", ("UTF-16LE", True), id="bom"),
    ],
)
def test_generation_corpus_preserves_context(
    tmp_path: Path, profile: CorpusProfile, grammar: Grammar, production: str, context: tuple[str | None, bool]
) -> None:
    case: Final = generate(grammar, random.SystemRandom(), GenerationBudget(64, 256), force=production)
    manifest: Final = write_corpora((case, case), grammar, tmp_path, profile)
    entry: Final = manifest.entries[0]
    assert (
        (tmp_path / entry.file).read_bytes(),
        entry.encoding,
        entry.sniff,
        entry.sha256,
        len(tuple(tmp_path.glob("**/" + entry.sha256))),
        manifest.entries[0] == manifest.entries[1],
        manifest.rejected,
    ) == (SEED_HEADER + case.data, *context, hashlib.sha256(SEED_HEADER + case.data).hexdigest(), 1, True, ())


def test_generation_corpus_rejects_changed_encoding_literal(tmp_path: Path) -> None:
    grammar: Final = encoding_grammar()
    case: Final = generate(grammar, random.SystemRandom(), GenerationBudget(64, 256), force="encoding:codec:utf-8")
    with pytest.raises(AssertionError, match="production literals"):
        write_corpora((replace(case, data=b"<p>wrong</p>"),), grammar, tmp_path, "encoding")


@pytest.mark.parametrize("profile", ["markdown-source", "markdown-html", "encoding"])
def test_generation_cli_sweeps_domain_productions(tmp_path: Path, profile: str) -> None:
    assert (
        corpus_main(("--output", str(tmp_path), "--profile", profile, "--count", "0", "--sweep", "--budget", "64")) == 0
    )
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert (len(manifest["entries"]), manifest["rejected"]) == (
        len(
            (
                encoding_grammar() if profile == "encoding" else markdown_grammar(html=profile == "markdown-html")
            ).productions
        ),
        [],
    )


def test_generation_cli_execution_context(tmp_path: Path, mocker: MockerFixture) -> None:
    run: Final = mocker.patch("fuzz.atheris_targets.fuzz", autospec=True)
    corpus: Final = tmp_path / "encoding"
    assert (
        main((
            "--target",
            "encoding-bytes",
            "--corpus",
            str(corpus),
            "--encoding",
            "windows-1252",
            "--sniff",
            "-atheris_runs=8",
        ))
        == 0
    )
    assert (json.loads(corpus.with_suffix(".json").read_text()), run.call_args.args[2]) == (
        {"target": "encoding-bytes", "exports": [], "corpus": str(corpus), "encoding": "windows-1252", "sniff": True},
        "encoding-bytes",
    )


def test_generation_preserves_qualified_owners() -> None:
    assert (len(owner_inventory()), len(public_targets())) == (223, 34)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        pytest.param("markdown-source", b"**kept**", id="source"),
        pytest.param("markdown-html", b"<p><strong>kept</strong></p>", id="html"),
    ],
)
def test_generation_first_conversion_loss(name: str, data: bytes) -> None:
    target: Final = next(
        target
        for target in generation_targets(render=lambda node: node.to_markdown().replace("kept", "changed"))
        if target.name == name
    )
    with pytest.raises(AssertionError, match="changes meaning"):
        target.callback(data)


def test_generation_changed_decoder_text() -> None:
    with pytest.raises(AssertionError, match="Encoded parser text differs"):
        encoding_observation(b"<p>kept</p>", decode=lambda _data, _codec: "<p>wrong</p>")


@pytest.mark.parametrize(
    ("profile", "target"),
    [pytest.param("xml", "xml-schema", id="xml"), pytest.param("css-stylesheet", "css-stylesheet", id="css")],
)
def test_generation_cli_existing_domain_consumers(tmp_path: Path, profile: str, target: str) -> None:
    assert corpus_main(("--output", str(tmp_path), "--profile", profile, "--count", "1", "--budget", "512")) == 0
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert ({entry["target"] for entry in manifest["entries"]}, manifest["rejected"]) == ({target}, [])


if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from turbohtml.clean import JSMinify

_JS_READER_REQUIRED: Final = pytest.mark.skipif(
    shutil.which("node") is None
    or not (Path(__file__).parents[1] / "tools/bench/node/node_modules/eslint-scope").is_dir(),
    reason="Node and the pinned JavaScript reader packages are required",
)


@_JS_READER_REQUIRED
@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(b"", "", id="empty"),
        pytest.param(b'consume ( "kept" , external );', 'consume("kept",external)', id="free-names"),
        pytest.param('consume("水😀");'.encode(), 'consume("水😀")', id="unicode"),
        pytest.param(b"let value = 1 + 2; consume(value);", "let value=1+2;consume(value)", id="unfolded"),
    ],
)
def test_javascript_exact_source(source: bytes, expected: str) -> None:
    assert javascript_observation(source) == expected


@_JS_READER_REQUIRED
@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "error"),
    [
        pytest.param(b"\xff", UnicodeDecodeError, id="invalid-utf8"),
        pytest.param(b"(", OutOfScopeError, id="invalid-javascript"),
    ],
)
def test_javascript_callback_rejects_input(source: bytes, error: type[Exception]) -> None:
    with pytest.raises(error):
        javascript_targets()[0].callback(source)


@_JS_READER_REQUIRED
@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "error", "message"),
    [
        pytest.param(b"(", OutOfScopeError, None, id="invalid-input-first"),
        pytest.param(b'consume("kept",external);', AssertionError, "not a fixpoint", id="valid-input-nonfixpoint"),
    ],
)
def test_javascript_rejects_independent_invalid_input_first(
    source: bytes, error: type[Exception], message: str | None
) -> None:
    def unstable(source: str, _options: JSMinify) -> str:
        return source + ";"

    with pytest.raises(error, match=message):
        javascript_observation(source, unstable)


@_JS_READER_REQUIRED
@pytest.mark.oracle
@pytest.mark.parametrize(
    ("output", "message"),
    [
        pytest.param('consume("lost",external);', "static string", id="wrong-string"),
        pytest.param('invented("kept",external);', "free identifier", id="wrong-name"),
        pytest.param("(", "not JavaScript", id="invalid-output"),
    ],
)
def test_javascript_rejects_wrong_stable_output(output: str, message: str) -> None:
    def wrong(_source: str, _options: JSMinify) -> str:
        return output

    with pytest.raises(AssertionError, match=message):
        javascript_observation(b'consume("kept",external);', wrong)


@pytest.mark.oracle
def test_javascript_owns_public_minifier() -> None:
    assert owner_inventory()["turbohtml.clean.minify_js"] == "javascript"


@_JS_READER_REQUIRED
@pytest.mark.oracle
def test_javascript_cli_initializes_reader_and_routes_corpus(tmp_path: Path, mocker: MockerFixture) -> None:
    run: Final = mocker.patch("fuzz.atheris_targets.fuzz", autospec=True)
    corpus: Final = tmp_path / "javascript"
    assert main(("--target", "javascript", "--corpus", str(corpus), "-atheris_runs=8")) == 0
    assert (json.loads(corpus.with_suffix(".json").read_text()), run.call_args.args[2]) == (
        {"target": "javascript", "exports": ["turbohtml.clean.minify_js"], "corpus": str(corpus)},
        "javascript",
    )


@_JS_READER_REQUIRED
@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "error"),
    [
        pytest.param(b'consume("kept",external);', None, id="valid"),
        pytest.param(b"(", "OutOfScopeError", id="syntax"),
        pytest.param(b"\xff", "UnicodeDecodeError", id="utf8"),
    ],
)
def test_javascript_corpus_admission(tmp_path: Path, source: bytes, error: str | None) -> None:
    grammar: Final = compile_grammar((Production("script", "root", (source,), 1, "fixture:1"),), "root")
    manifest: Final = write_corpora((generate(grammar, random.SystemRandom()),), grammar, tmp_path, "javascript")
    if error is not None:
        assert (manifest.entries, tuple(entry.exception for entry in manifest.rejected)) == ((), (error,))
    else:
        assert (
            tuple(entry.target for entry in manifest.entries),
            tuple((tmp_path / entry.file).read_bytes() for entry in manifest.entries),
            tuple(entry.productions for entry in manifest.entries),
            manifest.rejected,
        ) == (("javascript",), (SEED_HEADER + source,), ((("script", "fixture:1"),),), ())


@_JS_READER_REQUIRED
@pytest.mark.oracle
def test_javascript_cli_dependency_failure_precedes_corpus_creation(tmp_path: Path) -> None:
    corpus: Final = tmp_path / "javascript"
    result: Final = run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and owned CLI.
        [sys.executable, "-m", "fuzz.atheris_targets", "--target", "javascript", "--corpus", str(corpus)],
        env={
            **os.environ,
            "PATH": str(tmp_path),
            "PYTHONPATH": str(Path(__file__).parents[1] / "tools"),
            "GCOV_PREFIX": str(tmp_path / "gcda"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, "required by the js-names oracle" in result.stderr, corpus.exists()) == (1, True, False)


@pytest.mark.parametrize(
    ("profile", "production", "source", "targets"),
    [
        pytest.param(
            "html",
            "html:document:fixture",
            b"<html><body><p>x</p></body></html>",
            ("html-document", "html-incremental", "html-tokenizer"),
            id="document",
        ),
        pytest.param("html", "html:fragment:fixture", b"<p>x</p>", ("html-fragment", "html-tokenizer"), id="fragment"),
        pytest.param("xml", "xml:fixture", b"<root>text</root>", ("xml-schema",), id="xml"),
        pytest.param("css-stylesheet", "css:sheet", b"p { color: red; }", ("css-stylesheet",), id="stylesheet"),
        pytest.param("css-declaration", "css:declaration", b"color: red;", ("css-object-model",), id="declaration"),
        pytest.param("css-selector", "css:selector", b"p.x", ("css-translate",), id="selector"),
    ],
)
def test_corpus_routes_exact_bytes(
    tmp_path: Path, profile: CorpusProfile, production: str, source: bytes, targets: tuple[str, ...]
) -> None:
    grammar: Final = compile_grammar((Production(production, "root", (source,), 1, "fixture:1"),), "root")
    case: Final = generate(grammar, random.SystemRandom())
    manifest: Final = write_corpora((case,), grammar, tmp_path, profile)
    assert (
        tuple(entry.target for entry in manifest.entries),
        tuple((tmp_path / entry.file).read_bytes() for entry in manifest.entries),
        tuple(entry.productions for entry in manifest.entries),
        manifest.rejected,
    ) == (targets, (SEED_HEADER + source,) * len(targets), (((production, "fixture:1"),),) * len(targets), ())
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["entries"][0]["sha256"]
        == hashlib.sha256(SEED_HEADER + source).hexdigest()
    )


def test_corpus_deduplicates_bytes_preserves_traces(tmp_path: Path) -> None:
    grammar: Final = compile_grammar(
        (
            Production("first", "root", (b"<p>x</p>",), 1, "fixture:1"),
            Production("second", "root", (b"<p>x</p>",), 1, "fixture:2"),
        ),
        "root",
    )
    cases: Final = tuple(generate(grammar, random.SystemRandom(), force=name) for name in ("first", "second"))
    manifest: Final = write_corpora(cases, grammar, tmp_path, "html")
    assert (
        len(tuple((tmp_path / "html-fragment").iterdir())),
        tuple(entry.productions for entry in manifest.entries if entry.target == "html-fragment"),
    ) == (1, ((("first", "fixture:1"),), (("second", "fixture:2"),)))


def test_corpus_rejects_invalid_input_before_writing(tmp_path: Path) -> None:
    grammar: Final = compile_grammar((Production("invalid", "root", (b"\xff",), 1, "fixture:1"),), "root")
    manifest: Final = write_corpora((generate(grammar, random.SystemRandom()),), grammar, tmp_path, "css-stylesheet")
    assert (
        manifest.entries,
        tuple(entry.exception for entry in manifest.rejected),
        (tmp_path / "css-stylesheet").exists(),
    ) == ((), ("UnicodeDecodeError",), False)


def test_corpus_cli_creates_target_files(tmp_path: Path) -> None:
    assert fuzz_atheris_corpora_main(("--output", str(tmp_path), "--count", "1", "--seed", "1")) == 0
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["entries"]
    assert all((tmp_path / entry["file"]).is_file() for entry in manifest["entries"])


def test_corpus_cli_empty_count(tmp_path: Path) -> None:
    assert fuzz_atheris_corpora_main(("--output", str(tmp_path), "--count", "0")) == 0
    assert json.loads((tmp_path / "manifest.json").read_text()) == {"entries": [], "rejected": []}


def test_corpus_cli_sweep_preserves_productions(tmp_path: Path) -> None:
    assert fuzz_atheris_corpora_main(("--output", str(tmp_path), "--count", "0", "--sweep")) == 0
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert {production.name for production in html_grammar_complete().productions} <= {
        production[0] for entry in manifest["entries"] for production in entry["productions"]
    }


def test_corpus_preserves_generated_bindings(tmp_path: Path) -> None:
    grammar: Final = compile_grammar(
        (
            Production(
                "bound", "root", (b'<p id="', Identifier("element", definition=True), b'">text</p>'), 1, "fixture:1"
            ),
        ),
        "root",
    )
    case: Final = generate(grammar, random.SystemRandom())
    manifest: Final = write_corpora((case,), grammar, tmp_path, "html")
    assert tuple(entry.bindings for entry in manifest.entries) == (case.bindings, case.bindings)
    assert json.loads((tmp_path / "manifest.json").read_text())["entries"][0]["bindings"] == [["element", "x"]]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(b"", "", id="empty"),
        pytest.param(b"p { color: #ff0000; margin: 0px; }", "p{color:red;margin:0}", id="sheet"),
        pytest.param('p { content: "水😀"; }'.encode(), 'p{content:"水😀"}', id="unicode"),
        pytest.param(b'p { content: "</style>"; color: red; }', 'p{content:"</style>";color:red}', id="html-boundary"),
    ],
)
def test_stylesheet_consumer_exact_source(source: bytes, expected: str) -> None:
    assert stylesheet_observation(source) == expected


@pytest.mark.parametrize("target", stylesheet_targets(), ids=lambda target: target.name)
def test_stylesheet_callback_rejects_invalid_utf8(target: Target) -> None:
    with pytest.raises(UnicodeDecodeError):
        target.callback(b"\xff")


def test_stylesheet_owns_public_minifier() -> None:
    assert owner_inventory()["turbohtml.clean.minify_css"] == "css-stylesheet"


@pytest.mark.parametrize("kind", ["fixpoint", "selector"])
def test_stylesheet_consumer_rejects_wrong_output(kind: str) -> None:
    def wrong(source: str) -> str:
        return source + "x" if kind == "fixpoint" else "q{color:red}"

    with pytest.raises(AssertionError):
        stylesheet_observation(b"p { color: red; }", wrong)


if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture
def engine() -> ModuleType:
    return pytest.importorskip("fuzz.rng_inline", exc_type=ImportError)


@pytest.mark.oracle
@pytest.mark.parametrize("seed", range(8))
def test_rng_inline_generated_labels(engine: ModuleType, seed: int) -> None:
    case: Final = engine.generate(seed)
    assert [(row.engine, row.phase, row.expected, row.actual) for row in engine.compare(case)] == [
        ("turbohtml", "compilation", case.compiles, case.compiles),
        ("libxml2", "compilation", case.compiles, case.compiles),
        *[
            (name, "validation", expected, expected)
            for _, expected in case.documents
            for name in ("turbohtml", "libxml2")
        ],
    ]


@pytest.mark.oracle
@pytest.mark.parametrize("schema", ["<", '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>'])
def test_rng_inline_compile_verdicts(engine: ModuleType, schema: str) -> None:
    expected: Final = False
    case: Final = engine.Case(0, schema, expected, ())
    assert [(row.engine, row.phase, row.expected, row.actual) for row in engine.compare(case)] == [
        ("turbohtml", "compilation", expected, expected),
        ("libxml2", "compilation", expected, expected),
    ]


@pytest.mark.oracle
def test_rng_inline_unavailable_documents_remain_findings(engine: ModuleType) -> None:
    case: Final = engine.Case(0, "<", compiles=False, documents=(("<v/>", True),))
    assert [(row.engine, row.phase, row.expected, row.actual) for row in engine.compare(case)] == [
        ("turbohtml", "compilation", False, False),
        ("libxml2", "compilation", False, False),
        ("turbohtml", "validation", True, None),
        ("libxml2", "validation", True, None),
    ]


@pytest.mark.oracle
def test_rng_inline_negative_control_changes_public_verdict(engine: ModuleType) -> None:
    case: Final = engine.generate(0)
    assert [
        (row.engine, row.phase, row.expected, row.actual) for row in engine.compare(case, negative_control=True)
    ] == [
        ("turbohtml", "compilation", True, True),
        ("libxml2", "compilation", True, True),
        ("turbohtml", "validation", True, False),
        ("libxml2", "validation", True, True),
        ("turbohtml", "validation", True, False),
        ("libxml2", "validation", True, True),
        ("turbohtml", "validation", False, True),
        ("libxml2", "validation", False, False),
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("arguments", "exit_status", "findings"),
    [
        pytest.param(["--cases", "8"], 0, 0, id="agreement"),
        pytest.param(["--cases", "8", "--negative-control"], 1, 18, id="wrong-verdict"),
    ],
)
def test_rng_inline_cli(
    engine: ModuleType,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    exit_status: int,
    findings: int,
) -> None:
    assert engine.main(arguments) == exit_status
    rows: Final = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1] == {"summary": {"cases": 8, "rows": 52, "findings": findings}}
    assert {(row["engine"], row["phase"]) for row in rows[:-1]} == {
        ("turbohtml", "compilation"),
        ("libxml2", "compilation"),
        ("turbohtml", "validation"),
        ("libxml2", "validation"),
    }


@pytest.mark.oracle
@pytest.mark.parametrize("count", ["0", "257", "-1"])
def test_rng_inline_cli_rejects_unbounded_cases(engine: ModuleType, count: str) -> None:
    with pytest.raises(SystemExit, match="2"):
        engine.main(["--cases", count])


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("pattern", "documents"),
    [
        pytest.param("<empty/>", (("<v/>", True), ("<v>wrong</v>", False)), id="group"),
        pytest.param(
            '<choice><value type="string">ok</value>{annotation}</choice>',
            (("<v>ok</v>", True), ("<v/>", False)),
            id="choice",
        ),
        pytest.param(
            "<interleave><empty/>{annotation}</interleave>",
            (("<v/>", True), ("<v>wrong</v>", False)),
            id="interleave",
        ),
        pytest.param(
            '<attribute name="key">{annotation}</attribute><empty/>',
            (('<v key="anything"/>', True), ("<v/>", False)),
            id="attribute-default-text",
        ),
    ],
)
def test_rng_inline_foreign_annotations_preserve_pattern_verdicts(
    engine: ModuleType,
    pattern: str,
    documents: tuple[tuple[str, bool], ...],
) -> None:
    annotation: Final = '<doc:annotation><ref name="missing"/></doc:annotation>'
    body: Final = pattern.replace("{annotation}", annotation)
    schema: Final = (
        '<grammar xmlns="http://relaxng.org/ns/structure/1.0" xmlns:doc="urn:documentation">'
        f'<start><element name="v">{body}{annotation}</element></start></grammar>'
    )
    case: Final = engine.Case(0, schema, compiles=True, documents=documents)
    assert [(row.engine, row.phase, row.expected, row.actual) for row in engine.compare(case)] == [
        ("turbohtml", "compilation", True, True),
        ("libxml2", "compilation", True, True),
        *[(name, "validation", expected, expected) for _, expected in documents for name in ("turbohtml", "libxml2")],
    ]


@pytest.mark.oracle
def test_rng_inline_foreign_annotation_preserves_explicit_name_class(engine: ModuleType) -> None:
    schema: Final = (
        '<element xmlns="http://relaxng.org/ns/structure/1.0" xmlns:doc="urn:documentation">'
        '<doc:annotation><ref name="missing"/></doc:annotation><name>v</name><empty/></element>'
    )
    case: Final = engine.Case(0, schema, compiles=True, documents=(("<v/>", True), ("<wrong/>", False)))
    assert [row.actual == row.expected for row in engine.compare(case)] == [True] * 6


if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture
def engines() -> tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType]:
    return (
        pytest.importorskip("fuzz.schema_mutation", exc_type=ImportError),
        pytest.importorskip("fuzz.xslt_inline", exc_type=ImportError),
        pytest.importorskip("fuzz.xsd_inline", exc_type=ImportError),
        pytest.importorskip("fuzz.rng_inline", exc_type=ImportError),
        pytest.importorskip("lxml.etree", exc_type=ImportError),
    )


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("index", "count"),
    [pytest.param(1, 10, id="xslt"), pytest.param(2, 6, id="xsd"), pytest.param(3, 6, id="rng")],
)
@pytest.mark.parametrize("broken", [False, True], ids=["repaired", "broken"])
def test_schema_mutation_registered_cli(
    engines: tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType],
    capsys: pytest.CaptureFixture[str],
    index: int,
    count: int,
    *,
    broken: bool,
) -> None:
    arguments = ["--cases", str(count), "--mutate"]
    if broken:
        arguments.append("--broken-references")
    assert engines[index].main(arguments) == 0
    assert '"findings": 0' in capsys.readouterr().out


@pytest.mark.oracle
def test_schema_mutation_xpath_literals_and_declarations(
    engines: tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType],
) -> None:
    root: Final = (
        '<x:stylesheet xmlns:x="http://www.w3.org/1999/XSL/Transform" version="1.0">'
        '<x:output method="text"/><x:variable name="recipient" select="&apos;kept&apos;"/>'
        '<x:key name="recipient-key" match="item" use="@id"/>'
        '<x:template name="recipient-template">template</x:template>'
        '<x:template match="/"/></x:stylesheet>'
    )
    donor: Final = root.replace("recipient", "donor").replace(
        '<x:template match="/"/>',
        """<x:template match="/"><x:value-of select="concat('$literal', $donor, key('donor-key', 'one'))"/>
        <x:call-template name="donor-template"/></x:template>""",
    )
    mutated: Final = engines[0].mutate(root, (donor,), 0)
    assert str(engines[4].XSLT(engines[4].fromstring(mutated.encode()))(engines[4].fromstring(b"<root/>"))) == (
        "$literalkepttemplate"
    )


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "message"),
    [
        pytest.param("<root/>", "only inline", id="unsupported"),
        pytest.param(
            '<x:stylesheet xmlns:x="http://www.w3.org/1999/XSL/Transform"/>',
            "root template",
            id="missing-template",
        ),
        pytest.param(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>',
            "global element",
            id="missing-element",
        ),
        pytest.param(
            '<rng:grammar xmlns:rng="http://relaxng.org/ns/structure/1.0"/>',
            "start pattern",
            id="missing-start",
        ),
    ],
)
def test_schema_mutation_rejects_missing_seed_contract(
    engines: tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType],
    source: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        engines[0].mutate(source, (source,), 0)


@pytest.mark.oracle
@pytest.mark.parametrize("index", [2, 3], ids=["xsd", "rng"])
def test_schema_mutation_cli_requires_mutation_for_broken_references(
    engines: tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType],
    index: int,
) -> None:
    with pytest.raises(SystemExit, match="2"):
        engines[index].main(["--broken-references"])


@pytest.mark.oracle
def test_schema_mutation_rng_missing_recipient_definition_is_not_invented(
    engines: tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType],
) -> None:
    source: Final = (
        '<rng:grammar xmlns:rng="http://relaxng.org/ns/structure/1.0">'
        '<rng:start><rng:element name="v"><rng:empty/></rng:element></rng:start></rng:grammar>'
    )
    mutated: Final = engines[0].mutate(source, (engines[3].generate(0).schema,), 0)
    case: Final = engines[3].Case(0, mutated, compiles=False, documents=())
    assert [(row.engine, row.expected, row.actual) for row in engines[3].compare(case)] == [
        ("turbohtml", False, False),
        ("libxml2", False, False),
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("index", "source"),
    [
        pytest.param(
            1,
            '<x:stylesheet version="1.0"><x:output method="text"/>'
            '<x:template match="/">word</x:template></x:stylesheet>',
            id="x",
        ),
        pytest.param(2, '<xs:schema><xs:element name="v" type="xs:string"/></xs:schema>', id="xs"),
        pytest.param(
            3,
            '<rng:grammar><rng:start><rng:element name="v"><rng:empty/></rng:element></rng:start></rng:grammar>',
            id="rng",
        ),
    ],
)
def test_schema_mutation_binds_dictionary_prefixes(
    engines: tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType],
    index: int,
    source: str,
) -> None:
    mutated: Final = engines[0].mutate(source, (source,), 0)
    reference: Final = engines[4].fromstring(mutated.encode())
    if index == 1:
        assert str(engines[4].XSLT(reference)(engines[4].fromstring(b"<root/>"))) == "word"
    else:
        validator: Final = engines[4].XMLSchema(reference) if index == 2 else engines[4].RelaxNG(reference)
        assert validator.validate(engines[4].fromstring(b"<v/>"))


@pytest.mark.oracle
@pytest.mark.parametrize("source", ["", "<root/><root/>"], ids=["empty", "multiple"])
def test_schema_mutation_requires_one_dictionary_root(
    engines: tuple[ModuleType, ModuleType, ModuleType, ModuleType, ModuleType],
    source: str,
) -> None:
    with pytest.raises(ValueError, match="one root element"):
        engines[0].mutate(source, (source,), 0)


if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture
def fuzz_xslt_inline_engines() -> tuple[ModuleType, ModuleType]:
    return (
        pytest.importorskip("fuzz.xslt_inline", exc_type=ImportError),
        pytest.importorskip("fuzz.schema_mutation", exc_type=ImportError),
    )


@pytest.mark.oracle
@pytest.mark.parametrize("seed", range(12))
def test_xslt_inline_generated_phase_labels(fuzz_xslt_inline_engines: tuple[ModuleType, ModuleType], seed: int) -> None:
    engine: Final = fuzz_xslt_inline_engines[0]
    case: Final = engine.generate(seed)
    rows: Final = list(engine.compare(case))
    assert [(row.engine, row.phase, row.actual == row.expected) for row in rows] == [
        (name, phase, True)
        for name in ("turbohtml", "libxslt")
        for phase in (("compilation", "application", "result") if case.applies else ("compilation", "application"))
    ]


@pytest.mark.oracle
@pytest.mark.parametrize("seed", [0, 7])
def test_xslt_inline_wrong_result_is_a_finding(
    fuzz_xslt_inline_engines: tuple[ModuleType, ModuleType], seed: int
) -> None:
    engine: Final = fuzz_xslt_inline_engines[0]
    assert [
        (row.engine, row.phase)
        for row in engine.compare(engine.generate(seed), negative_control=True)
        if row.actual != row.expected
    ] == [
        ("turbohtml", "result"),
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("body", "output"),
    [
        pytest.param(
            '<out xmlns:p="urn:example" p:key="one"> alpha <child/> beta </out>',
            '<out xmlns:q="urn:example" q:key="one"> alpha <child/> beta </out>',
            id="expanded-names",
        ),
        pytest.param(
            '<out><x:comment>note</x:comment><x:processing-instruction name="go">now</x:processing-instruction></out>',
            "<out><!--note--><?go now?></out>",
            id="comment-and-instruction",
        ),
    ],
)
def test_xslt_inline_xml_content_is_preserved(
    fuzz_xslt_inline_engines: tuple[ModuleType, ModuleType], body: str, output: str
) -> None:
    engine: Final = fuzz_xslt_inline_engines[0]
    stylesheet: Final = (
        '<x:stylesheet xmlns:x="http://www.w3.org/1999/XSL/Transform" version="1.0">'
        '<x:output method="xml"/><x:template match="/">' + body + "</x:template></x:stylesheet>"
    )
    case: Final = engine.Case(0, stylesheet, "<root/>", "xml", compiles=True, applies=True, output=output)
    assert [(row.engine, row.phase, row.actual == row.expected) for row in engine.compare(case)] == [
        (name, phase, True) for name in ("turbohtml", "libxslt") for phase in ("compilation", "application", "result")
    ]


@pytest.mark.oracle
def test_xslt_inline_malformed_stylesheet_retains_unavailable_application(
    fuzz_xslt_inline_engines: tuple[ModuleType, ModuleType],
) -> None:
    engine: Final = fuzz_xslt_inline_engines[0]
    case: Final = engine.Case(0, "<", "<root/>", "text", compiles=False, applies=False, output=None)
    assert [(row.engine, row.phase, row.expected, row.actual) for row in engine.compare(case)] == [
        ("turbohtml", "compilation", False, False),
        ("turbohtml", "application", None, None),
        ("libxslt", "compilation", False, False),
        ("libxslt", "application", None, None),
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        pytest.param([], (0, 0, 20), id="labelled-families"),
        pytest.param(["--negative-control"], (1, 10, 20), id="wrong-result"),
        pytest.param(["--mutate"], (0, 0, 24), id="repaired-subtrees"),
        pytest.param(["--mutate", "--broken-references"], (0, 0, 0), id="broken-references"),
    ],
)
def test_xslt_inline_cli_outcome_counts(
    fuzz_xslt_inline_engines: tuple[ModuleType, ModuleType],
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected: tuple[int, int, int],
) -> None:
    exit_status, findings, results = expected
    assert fuzz_xslt_inline_engines[0].main(arguments) == exit_status
    rows: Final = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1] == {
        "summary": {
            "cases": 12,
            "findings": findings,
            "phases": {"compilation": 24, "application": 24, **({"result": results} if results else {})},
        },
    }


@pytest.mark.oracle
@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["--cases", "0"], id="empty"),
        pytest.param(["--cases", "257"], id="too-many"),
        pytest.param(["--broken-references"], id="missing-mutation"),
    ],
)
def test_xslt_inline_cli_rejects_invalid_domain(
    fuzz_xslt_inline_engines: tuple[ModuleType, ModuleType],
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit, match="2"):
        fuzz_xslt_inline_engines[0].main(arguments)


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

_TARGETS: Final = content_targets()


@pytest.mark.parametrize("target", _TARGETS, ids=lambda target: target.name)
@pytest.mark.parametrize("data", [b"", b"hello", bytes(range(256))], ids=["empty", "ascii", "byte-range"])
def test_content_target_behaviors(target: Target, data: bytes) -> None:
    target.callback(data)


def test_content_targets_have_unique_owners() -> None:
    exports: Final = [export for target in _TARGETS for export in target.exports]
    assert len(exports) == len(set(exports)) == 90


def test_content_sanitizer_removes_attributes_and_comments() -> None:
    assert sanitizer_observation(b"x") == (
        "<b>value78</b><b>value78</b>",
        [Removed("b", "title"), Removed("b", "onclick")],
    )


def test_content_minifiers_transform_source() -> None:
    assert minifier_observation(b"x") == (
        "<html><head></head><body><p>value78</p></body></html>",
        ".value78{color:red}",
        "color:red",
        'const value="value78"',
    )


def test_content_stdlib_decodes_chunked_references() -> None:
    assert stdlib_observation(b"x") == "value78&"


def test_content_sanitizer_rejects_wrong_renderer_result() -> None:
    def render(data: bytes) -> tuple[str, list[Removed]]:
        return data.decode(), []

    with pytest.raises(AssertionError, match="Content API mismatch"):
        sanitizer_check(b"wrong", render)


@pytest.mark.parametrize("kind", ["css-semantics", "css-fixpoint", "js-fixpoint"])
def test_content_minifier_rejects_wrong_output(kind: str) -> None:
    def wrong(source: str) -> str:
        return "q{color:blue}" if kind == "css-semantics" else source + "x"

    with pytest.raises(AssertionError, match="Content API mismatch"):
        minifier_check(b"x", js=wrong) if kind == "js-fixpoint" else minifier_check(b"x", css=wrong)


def test_content_url_rejects_stable_wrong_host() -> None:
    with pytest.raises(AssertionError, match="Content API mismatch"):
        url_check(b"x", lambda _source: "https://wrong.example/")


@pytest.mark.parametrize(
    "max_chunk", [pytest.param(1, id="one"), pytest.param(3, id="three"), pytest.param(64, id="whole")]
)
@pytest.mark.parametrize(
    ("check", "data"),
    [
        pytest.param(detector_check, "\ufeff<p>水😀</p>".encode(), id="encoding-detector"),
        pytest.param(stdlib_check, "<b>水😀&amp;</b>".encode(), id="stdlib-parser"),
    ],
)
def test_content_chunks_match_one_call(check: Callable[[bytes, Header], None], data: bytes, max_chunk: int) -> None:
    check(data, Header(0, 0, max_chunk))


@pytest.mark.parametrize(
    ("check", "consumer", "data"),
    [
        pytest.param(detector_check, EncodingDetector, "\ufeff<p>水😀</p>".encode(), id="encoding-detector"),
        pytest.param(stdlib_check, HTMLParser, "<b>水😀&amp;</b>".encode(), id="stdlib-parser"),
    ],
)
def test_content_chunks_reject_dropped_chunk(
    mocker: MockerFixture,
    check: Callable[[bytes, Header], None],
    consumer: type[EncodingDetector | HTMLParser],
    data: bytes,
) -> None:
    feed = consumer.feed
    mocker.patch.object(
        consumer, "feed", autospec=True, side_effect=lambda stream, chunk: len(chunk) == 1 or feed(stream, chunk)
    )
    with pytest.raises(AssertionError, match="Content API mismatch"):
        check(data, Header(0, 0, 1))


def test_content_url_rejects_unstable_reparse() -> None:
    with pytest.raises(AssertionError, match="reparse changes normalized URL"):
        url_check(b"x", lambda source: source + "/")


if TYPE_CHECKING:
    from pytest_mock import MockerFixture

_DOMAINS: Final = (
    "dom-construction",
    "dom-elementtree",
    "dom-traversal",
    "dom-query",
    "dom-mutation",
    "dom-range",
    "dom-shadow",
    "dom-locations",
    "dom-rewrite",
    "dom-sax",
    "dom-treebuild",
)


@pytest.mark.parametrize("domain", _DOMAINS)
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"text", id="plain"),
        pytest.param("é水😀".encode(), id="unicode"),
        pytest.param(b"<&>\r\n\x00", id="escaped"),
    ],
)
def test_dom_independent_contract(domain: str, data: bytes) -> None:
    observation: Final = dom_observation(data, domain)
    assert observation.actual == observation.expected


@pytest.mark.parametrize("domain", _DOMAINS)
def test_dom_registered_callback(domain: str) -> None:
    target: Final = next(target for target in dom_targets() if target.name == domain)
    assert target.exceptions == (UnicodeDecodeError,)
    target.callback(b"text")
    with pytest.raises(UnicodeDecodeError):
        target.callback(b"\xff")


def test_dom_ownership() -> None:
    targets: Final = dom_targets()
    exports: Final = [export for target in targets for export in target.exports]
    assert (tuple(target.name for target in targets), len(exports), len(set(exports))) == (_DOMAINS, 78, 78)


def test_dom_verification_rejects_changed_result() -> None:
    with pytest.raises(AssertionError, match=r"actual.*expected"):
        DomObservation("actual", "expected").verify()


def test_dom_verification_accepts_observed_result() -> None:
    assert dom_observation(b"text", "dom-range").verify() is None


def test_dom_range_literal() -> None:
    assert dom_observation(b"<&>", "dom-range").actual == repr(("<p>&lt;&amp;&gt;</p>", 0, 1, 0, 1, False, "<&>"))


def test_dom_input_bound() -> None:
    assert dom_observation(b"x" * 65, "dom-range") == dom_observation(b"x" * 64, "dom-range")


def test_dom_unknown_domain() -> None:
    with pytest.raises(KeyError, match="unknown"):
        dom_observation(b"text", "unknown")


def test_dom_elementtree_without_lxml(mocker: MockerFixture) -> None:
    mocker.patch.dict(sys.modules, {"lxml.etree": None})
    assert dom_observation(b"text", "dom-elementtree").actual == repr((
        True,
        True,
        True,
        "prefixtexttail",
        "prefixtext",
        "prefixtext",
        "trailing",
        None,
        None,
        None,
    ))


_UNPLACED: Final = ((None, None, None),) * 3


def test_atheris_parser_document_output() -> None:
    assert document_observation("<p>水😀</p>".encode()) == "<html><head></head><body><p>水😀</p></body></html>"


@pytest.mark.parametrize(
    ("header", "positions"),
    [
        pytest.param(BYTEWISE, (*_UNPLACED, (1, 0, None)), id="bytes-one-unit"),
        pytest.param(Header(1, 0, 2), (*_UNPLACED, (1, 0, None)), id="text-two-units"),
        pytest.param(Header(2, 0, 3), (*_UNPLACED, (None, None, None)), id="no-positions"),
        pytest.param(
            Header(4, 0, 5),
            (*_UNPLACED, (1, 0, SourceLocation(SourceSpan(1, 0, 0, 1, 3, 3), SourceSpan(1, 5, 5, 1, 9, 9), {}))),
            id="source-locations",
        ),
    ],
)
def test_atheris_parser_incremental_output(header: Header, positions: tuple[object, ...]) -> None:
    assert incremental_observation("<p>水😀</p>".encode(), header) == (
        "<html><head></head><body><p>水😀</p></body></html>",
        positions,
    )


def test_atheris_parser_fragment_output() -> None:
    assert fragment_observation(b"<p>x&amp;y</p>") == "<div><p>x&amp;y</p></div>"


def test_atheris_parser_token_fields() -> None:
    assert token_observation(b'<p id="a">x</p>') == (
        ("START_TAG", "p", None, (("id", "a"),), False, 1, 0),
        ("TEXT", None, "x", None, False, 1, 10),
        ("END_TAG", "p", None, (), False, 1, 11),
    )


@pytest.mark.parametrize(
    ("data", "opts", "expected"),
    [
        pytest.param(
            b"x&amp;",
            1,
            (
                ("TEXT", None, "x", None, False, 1, 0),
                ("CHARACTER_REFERENCE", None, "&", None, False, 1, 1),
            ),
            id="references-apart",
        ),
        pytest.param(b'<p id="a">', 4, (("START_TAG", "p", None, (), False, 1, 0),), id="no-attributes"),
    ],
)
def test_atheris_parser_token_options(data: bytes, opts: int, expected: tuple[object, ...]) -> None:
    assert token_observation(data, Header(opts, 0, 2)) == expected


@pytest.mark.parametrize(
    "oracle",
    [
        pytest.param("html", id="html-fixpoint"),
        pytest.param("spans", id="source-spans"),
        pytest.param("entries", id="xpath-entries"),
    ],
)
def test_atheris_parser_document_reports_oracle_failure(oracle: str) -> None:
    with pytest.raises(AssertionError, match=f"^{oracle} broke$"):
        document_observation(b"<p>x</p>", **{oracle: lambda _text: f"{oracle} broke"})


@pytest.mark.parametrize("target", parser_targets(), ids=lambda target: target.name)
def test_atheris_parser_callback_rejects_invalid_utf8(target: Target) -> None:
    with pytest.raises(UnicodeDecodeError):
        target.callback(b"\xff")


def test_atheris_parser_exports_have_unique_consumers() -> None:
    assert tuple((target.name, target.exports) for target in parser_targets()) == (
        ("html-document", ("turbohtml.parse", "turbohtml.Document", "turbohtml.Node")),
        (
            "html-fragment",
            (
                "turbohtml.parse_fragment",
                "turbohtml.Element",
                "turbohtml.Html",
                "turbohtml.Formatter",
                "turbohtml.Indent",
                "turbohtml.Minify",
            ),
        ),
        ("html-incremental", ("turbohtml.IncrementalParser",)),
        ("html-tokenizer", ("turbohtml.tokenize", "turbohtml.Tokenizer", "turbohtml.Token", "turbohtml.TokenType")),
    )


if TYPE_CHECKING:
    from collections.abc import Callable


def test_atheris_reference_rendered_text() -> None:
    assert render_observation(b"<p>one <b>two</b></p>") == (
        "one **two**",
        "one two",
        "one two",
        b"<html><head></head><body><p>one <b>two</b></p></body></html>",
    )


def test_atheris_reference_annotation_text() -> None:
    markdown, plain, tagged, canonical = render_observation(b'<p><a href="/x">one</a></p>')
    assert (markdown, plain, tagged, canonical) == (
        "[one](/x)",
        "one",
        "<link>one</link>",
        b'<html><head></head><body><p><a href="/x">one</a></p></body></html>',
    )


def test_atheris_reference_conformance_records() -> None:
    valid, codes, diagnostic = conformance_observation(
        b'<html lang="en"><head><title>x</title></head><body><img></body></html>'
    )
    assert (valid, "img-missing-alt" in codes, diagnostic) == (False, True, None)


def test_atheris_reference_css_records() -> None:
    assert cssom_observation(b"color:red;color:blue!important;margin:0") == (
        ("color", "blue", True),
        ("margin", "0", False),
    )


def test_atheris_reference_empty_css_records() -> None:
    assert cssom_observation(b"") == ()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(b"color: (", (("color", "(", False),), id="open-block"),
        pytest.param(b'content: "x', (("content", '"x', False),), id="open-string"),
        pytest.param(b'content: "a\n/* x', (("content", '"a\n/* x', False),), id="open-comment-after-string"),
        pytest.param(b"a: b}", (("a", "b}", False),), id="top-level-closer"),
    ],
)
def test_atheris_reference_css_records_left_open_at_end(
    data: bytes, expected: tuple[tuple[str, str, bool], ...]
) -> None:
    assert cssom_observation(data) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(b"--x: {a}", (("--x", "{a}", False),), id="custom-property-block"),
        pytest.param(rb"--x: a\}", (("--x", r"a\}", False),), id="escaped-closer"),
    ],
)
def test_atheris_reference_css_block_records(data: bytes, expected: tuple[tuple[str, str, bool], ...]) -> None:
    assert cssom_observation(data) == expected


def test_atheris_reference_selector_specificity() -> None:
    expression, specificity = selector_observation(b"div#a > p.x")
    assert (bool(expression), specificity) == (True, ((1, 1, 2),))


def test_atheris_reference_untranslatable_selector() -> None:
    with pytest.raises(ExpressionError):
        selector_observation(b":dir(ltr)")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(b"<root>one</root>", (True, True), id="valid"),
        pytest.param(b"<wrong/>", (False, False), id="invalid"),
    ],
)
def test_atheris_reference_schema_verdict(source: bytes, expected: tuple[bool, bool]) -> None:
    assert schema_observation(source) == expected


@pytest.mark.parametrize(
    "source",
    [pytest.param(b"one", id="plain"), pytest.param(b"\x00", id="null"), pytest.param(b"""a'b"c""", id="both-quotes")],
)
def test_atheris_reference_transform_parameter(source: bytes) -> None:
    assert transform_observation(source) == source.decode()


def test_atheris_reference_cli_file_output() -> None:
    assert cli_observation(b"<p>one<br>two</p>") == "one\ntwo"


@pytest.mark.parametrize("target", reference_targets(), ids=lambda target: target.name)
def test_atheris_reference_callback_rejects_invalid_utf8(target: Target) -> None:
    with pytest.raises(UnicodeDecodeError):
        target.callback(b"\xff")


def test_atheris_reference_strict_parse_record() -> None:
    assert conformance_observation(b"<div")[2] == ("eof-in-tag", 1, 4)


@pytest.mark.parametrize(
    ("source", "expected"),
    [pytest.param(b"color:blue!important", "blue", id="declared"), pytest.param(b"", "black", id="fallback")],
)
def test_atheris_reference_computed_cascade(source: bytes, expected: str) -> None:
    assert computed_observation(source) == expected


def test_atheris_reference_xml_rejection_is_documented() -> None:
    target: Final = next(target for target in reference_targets() if target.name == "xml-schema")
    assert target.exceptions == (UnicodeDecodeError, HTMLParseError)
    with pytest.raises(HTMLParseError):
        target.callback(b"<root>")


@pytest.mark.parametrize(
    ("observe", "oracle", "data"),
    [
        pytest.param(schema_observation, "xml", b"<root>text</root>", id="xml-fixpoint"),
        pytest.param(cssom_observation, "style", b"color: red", id="style-fixpoint"),
        pytest.param(selector_observation, "entries", b"p.x", id="selector-entries"),
    ],
)
def test_reference_reports_oracle_failure(observe: Callable[..., object], oracle: str, data: bytes) -> None:
    with pytest.raises(AssertionError, match=r"^oracle broke$"):
        observe(data, **{oracle: lambda _text: "oracle broke"})


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_atheris_targets_complete_qualified_inventory() -> None:
    owners: Final = owner_inventory()
    assert (
        len(owners),
        len(MODULES),
        {name: owners[name] for name in ("turbohtml.parse", "turbohtml.parse_xml", "turbohtml.__main__.main")},
    ) == (
        223,
        21,
        {
            "turbohtml.parse": "html-document",
            "turbohtml.parse_xml": "xml-schema",
            "turbohtml.__main__.main": "html-cli",
        },
    )


def test_atheris_targets_discover_public_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package: Final = tmp_path / "discovered"
    (package / "nested").mkdir(parents=True)
    (package / "_private").mkdir()
    for name in ("__init__", "__main__", "public", "nested/__init__", "nested/leaf", "_private/__init__", "_hidden"):
        (package / f"{name}.py").write_text("__all__ = []\n", encoding="utf-8")
    (package / "undeclared.py").write_text("", encoding="utf-8")
    (package / "_private" / "inner.py").write_text("__all__ = []\n", encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    assert public_modules("discovered") == (
        "discovered",
        "discovered.__main__",
        "discovered.nested",
        "discovered.nested.leaf",
        "discovered.public",
    )


def test_atheris_targets_missing_group_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing="):
        validate_owners(tuple(target for target in public_targets() if target.name != "javascript"), MODULES)


def test_atheris_targets_duplicate_group_is_rejected() -> None:
    targets: Final = public_targets()
    with pytest.raises(ValueError, match="Duplicate export owners"):
        validate_owners((*targets, targets[0]), MODULES)


def test_atheris_targets_cli_manifest_and_flags(tmp_path: Path, mocker: MockerFixture) -> None:
    run: Final = mocker.patch("fuzz.atheris_targets.fuzz", autospec=True)
    corpus: Final = tmp_path / "html-tokenizer"
    assert main(("--target", "html-tokenizer", "--corpus", str(corpus), "-atheris_runs=64", "-max_len=128")) == 0
    assert (json.loads(corpus.with_suffix(".json").read_text()), run.call_args.args[2:]) == (
        {
            "target": "html-tokenizer",
            "exports": ["turbohtml.Token", "turbohtml.TokenType", "turbohtml.Tokenizer", "turbohtml.tokenize"],
            "corpus": str(corpus),
        },
        ("html-tokenizer", (run.call_args.args[3][0], str(corpus), "-atheris_runs=64", "-max_len=128")),
    )


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


def test_atheris_driver_connects_exception_boundary(mocker: MockerFixture) -> None:
    accepted: list[bytes] = []
    rejected: list[int] = []

    def consume(data: bytes) -> None:
        if data == b"invalid":
            message = "documented error"
            raise UnicodeError(message)
        accepted.append(data)

    target = Target("cli", consume, ("turbohtml.__main__.main",), (UnicodeError,))
    runtime = mocker.MagicMock(spec=["Setup", "Fuzz", "instrument_func", "instrument_all"])
    runtime.instrument_func.side_effect = lambda callback: callback
    mocker.patch("fuzz.atheris_driver.import_module", autospec=True, return_value=runtime)
    mocker.patch("fuzz.atheris_driver.rejection_hook", autospec=True, return_value=partial(rejected.append, 1))
    mocker.patch("fuzz.atheris_driver.failure_hook", autospec=True, return_value=lambda _position: (0, False))

    def native_loop() -> None:
        callback: Callable[[bytes], None] = runtime.Setup.call_args.args[1]
        callback(SEED_HEADER + b"invalid")
        callback(SEED_HEADER + b"valid")

    runtime.Fuzz.side_effect = native_loop
    fuzz([target], ["turbohtml.__main__"], "cli", ["driver", "-runs=2"])
    runtime.instrument_all.assert_called_once_with()
    assert (accepted, rejected, runtime.Setup.call_args.args[0], runtime.Setup.call_args.kwargs) == (
        [b"valid"],
        [1],
        ["driver", "-runs=2"],
        {"custom_mutator": None},
    )


def test_atheris_driver_rejects_gap_before_runtime(mocker: MockerFixture) -> None:
    load = mocker.patch("fuzz.atheris_driver.import_module", autospec=True)
    with pytest.raises(ValueError, match="Export ownership mismatch"):
        fuzz([], ["turbohtml.__main__"], "cli", ["driver"])
    assert load.call_count == 0


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_atheris_registry_executes_qualified_owner(mocker: MockerFixture, capsys: pytest.CaptureFixture[str]) -> None:
    def minify(data: bytes) -> None:
        mocker.patch.object(sys, "stdin", io.StringIO(data.decode()))
        assert fuzz_atheris_registry_main(["minify-css"]) == 0

    target = Target("cli-css", minify, ("turbohtml.__main__.main",))
    owners = validate_owners([target], ["turbohtml.__main__"])
    owners["turbohtml.__main__.main"].callback(b"a { color: red }")
    assert capsys.readouterr().out == "a{color:red}"


@pytest.mark.parametrize(
    ("exports", "expected"),
    [
        pytest.param((), "missing=['turbohtml.__main__.main'], unknown=[]", id="missing"),
        pytest.param(
            ("turbohtml.__main__.extra",),
            "missing=['turbohtml.__main__.main'], unknown=['turbohtml.__main__.extra']",
            id="unknown",
        ),
    ],
)
def test_atheris_registry_rejects_ownership_gap(exports: tuple[str, ...], expected: str) -> None:
    target = Target("unassigned", print, exports)
    with pytest.raises(ValueError, match="Export ownership mismatch") as raised:
        validate_owners([target], ["turbohtml.__main__"])
    assert str(raised.value) == f"Export ownership mismatch: {expected}"


def test_atheris_registry_rejects_duplicate_qualified_owner() -> None:
    target = Target("duplicate", print, ("turbohtml.__main__.main",))
    with pytest.raises(ValueError, match=r"Duplicate export owners: turbohtml\.__main__\.main"):
        validate_owners([target, target], ["turbohtml.__main__"])


def test_atheris_registry_rejects_duplicate_target_name() -> None:
    targets = [Target("same", print, ("first.export",)), Target("same", print, ("second.export",))]
    with pytest.raises(ValueError, match="Duplicate target names"):
        validate_owners(targets, [])


def test_atheris_registry_runs_whole_payload_without_chunked_consumer() -> None:
    seen: list[bytes] = []
    Target("whole", seen.append, ()).run(b"payload", Header(1, 2, 3))
    assert seen == [b"payload"]


def test_atheris_registry_runs_chunked_consumer_with_header() -> None:
    seen: list[tuple[bytes, Header]] = []
    Target("chunked", print, (), (), lambda payload, header: seen.append((payload, header))).run(
        b"payload", Header(1, 2, 3)
    )
    assert seen == [(b"payload", Header(1, 2, 3))]


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize("instrumented", [pytest.param(False, id="release"), pytest.param(True, id="coverage")])
@pytest.mark.parametrize("via_cli", [pytest.param(False, id="build"), pytest.param(True, id="cli")])
def test_atheris_runtime_build_keeps_archive(
    mocker: MockerFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, via_cli: bool, instrumented: bool
) -> None:
    archive = tmp_path / "wheel-runtime.a"
    archive.write_bytes(b"original archive")
    for name in ("CC", "CXX", "OBJCOPY"):
        monkeypatch.delenv(name, raising=False)
    output = tmp_path / "built"
    run = mocker.patch("subprocess.run", autospec=True, return_value=None)
    if via_cli:
        assert (
            fuzz_atheris_runtime_main([
                "--archive",
                str(archive),
                "--output",
                str(output),
                *(["--coverage"] if instrumented else []),
            ])
            == 0
        )
    else:
        assert build_runtime(archive, output, coverage=instrumented) == output / "libatheris_fuzzer.so"
    commands = [call.args[0] for call in run.call_args_list]
    assert (archive.read_bytes(), (output / "runtime.a").read_bytes(), len(commands), commands[0], commands[2]) == (
        b"original archive",
        b"original archive",
        3,
        [
            "objcopy",
            "--redefine-sym",
            "LLVMFuzzerRunDriver=turbohtml_real_fuzzer_driver",
            str(output / "runtime.a"),
        ],
        [
            "g++",
            "-shared",
            "-o",
            str(output / "libatheris_fuzzer.so"),
            str(output / "bridge.o"),
            "-Wl,--whole-archive",
            str(output / "runtime.a"),
            "-Wl,--no-whole-archive",
            "-ldl",
            "-pthread",
            *(["--coverage"] if instrumented else []),
        ],
    )


@pytest.mark.parametrize(
    ("members", "deleted"),
    [
        pytest.param("asan_rtl.cpp.o\nasan_preinit.cpp.o\n", ["asan_preinit.cpp.o"], id="preinit"),
        pytest.param("ubsan_handlers.cpp.o\n", None, id="no-preinit"),
    ],
)
def test_atheris_runtime_merges_sanitizer_without_preinit(
    mocker: MockerFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: str, deleted: list[str] | None
) -> None:
    archive = tmp_path / "wheel-runtime.a"
    archive.write_bytes(b"original archive")
    sanitizer = tmp_path / "libclang_rt.asan.a"
    sanitizer.write_bytes(b"sanitizer archive")
    monkeypatch.delenv("AR", raising=False)
    output = tmp_path / "built"
    run = mocker.patch("subprocess.run", autospec=True, return_value=mocker.MagicMock(stdout=members))
    assert (
        fuzz_atheris_runtime_main(["--archive", str(archive), "--output", str(output), "--sanitizer", str(sanitizer)])
        == 0
    )
    commands = [call.args[0] for call in run.call_args_list]
    merged = str(output / "sanitizer.a")
    assert (sanitizer.read_bytes(), (output / "sanitizer.a").read_bytes(), commands[2:-1], commands[-1][6:9]) == (
        b"sanitizer archive",
        b"sanitizer archive",
        [["ar", "t", merged], *([] if deleted is None else [["ar", "d", merged, *deleted]])],
        [str(output / "runtime.a"), merged, "-Wl,--no-whole-archive"],
    )


def test_atheris_runtime_build_propagates_compiler_failure(mocker: MockerFixture, tmp_path: Path) -> None:
    archive = tmp_path / "wheel-runtime.a"
    archive.write_bytes(b"original archive")
    failure = OSError("compiler unavailable")
    mocker.patch("subprocess.run", autospec=True, side_effect=failure)
    with pytest.raises(OSError, match="compiler unavailable") as raised:
        build_runtime(archive, tmp_path / "built")
    assert raised.value is failure


def test_atheris_callback_rejects_documented_error() -> None:
    accepted: list[bytes] = []
    rejected: list[int] = []

    def consume(data: bytes, _header: Header) -> None:
        if data == b"invalid":
            message = "documented error"
            raise UnicodeError(message)
        accepted.append(data)

    callback = rejecting_callback(consume, (UnicodeError,), partial(rejected.append, 1), lambda _position: (0, False))
    callback(SEED_HEADER + b"invalid")
    callback(SEED_HEADER + b"valid")
    assert (accepted, rejected) == ([b"valid"], [1])


def test_atheris_callback_preserves_unexpected_error() -> None:
    rejected: list[int] = []

    def consume(data: bytes, _header: Header) -> None:
        raise ValueError(data.decode())

    callback = rejecting_callback(consume, (UnicodeError,), partial(rejected.append, 1), lambda _position: (0, False))
    with pytest.raises(ValueError, match="unexpected"):
        callback(SEED_HEADER + b"unexpected")
    assert rejected == []


def test_atheris_callback_opens_and_closes_failure_window(mocker: MockerFixture) -> None:
    inject = mocker.MagicMock(return_value=(3, False))
    target = mocker.MagicMock()
    rejecting_callback(target, (), mocker.MagicMock(), inject)(Header(2, 7, 1).encode() + b"x")
    assert (inject.call_args_list, target.call_args_list) == (
        [mocker.call(7), mocker.call(0)],
        [mocker.call(b"x", Header(2, 7, 1))],
    )


def test_atheris_callback_keeps_injected_memory_error(mocker: MockerFixture) -> None:
    def consume(_data: bytes, _header: Header) -> None:
        raise MemoryError

    reject = mocker.MagicMock()
    rejecting_callback(consume, (UnicodeError,), reject, lambda _position: (1, True))(SEED_HEADER)
    assert reject.call_count == 0


@pytest.mark.parametrize(
    ("raised", "failed", "error", "match"),
    [
        pytest.param(MemoryError, False, MemoryError, "^$", id="uninjected-memory-error"),
        pytest.param(None, True, AssertionError, "^a result for an injected", id="injected-result"),
        pytest.param(UnicodeError, True, AssertionError, "^UnicodeError for an injected", id="injected-documented"),
    ],
)
def test_atheris_callback_reports_failure_mismatch(
    mocker: MockerFixture, raised: type[Exception] | None, error: type[Exception], match: str, *, failed: bool
) -> None:
    def consume(_data: bytes, _header: Header) -> None:
        if raised is not None:
            raise raised

    reject = mocker.MagicMock()
    callback = rejecting_callback(consume, (UnicodeError,), reject, lambda _position: (1, failed))
    with pytest.raises(error, match=match):
        callback(SEED_HEADER)
    assert reject.call_count == 0


def test_atheris_rejection_hook_propagates_loader_error(mocker: MockerFixture) -> None:
    mocker.patch("ctypes.CDLL", autospec=True, side_effect=OSError("loader failed"))
    with pytest.raises(OSError, match="loader failed"):
        rejection_hook()


def test_atheris_failure_hook_requires_fuzzing_build() -> None:
    with pytest.raises(KeyError, match="_fuzz_inject_failure"):
        failure_hook()


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("HTTPS://Example.test/a?b=2&a=1#f", "https://example.test/a?a=1&b=2#f", id="canonical-components"),
        pytest.param("https:a", "https://a/", id="special-without-authority"),
        pytest.param("file:///tmp/x", "file:///tmp/x", id="empty-authority"),
        pytest.param("file:/tmp/x", "file:///tmp/x", id="file-without-authority"),
        pytest.param("//host/a?q=1#f", "//host/a?q=1#f", id="scheme-relative"),
        pytest.param("a/b?x=1#f", "a/b?x=1#f", id="relative"),
        pytest.param("mailto:a@b.test", "mailto:a@b.test", id="opaque"),
        pytest.param("http://example.test/?#", "http://example.test/", id="empty-delimiters"),
        pytest.param("https://example.test/#?", "https://example.test/#?", id="query-mark-in-fragment"),
    ],
)
def test_url_reparse_public_components(source: str, expected: str) -> None:
    assert (normalize_url(source), url_reparse_check(source)) == (expected, None)


@pytest.mark.parametrize(
    "normalized",
    [
        pytest.param("https://example.test/?", id="bare-query"),
        pytest.param("https://example.test/#", id="bare-fragment"),
        pytest.param("file:///x?#", id="all-delimiters"),
    ],
)
def test_url_reparse_keeps_optional_delimiters(normalized: str) -> None:
    assert url_reparse_check("raw", lambda _text: normalized) is None


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("https://[broken/", id="target-rejection"),
        pytest.param("https://[bad]/", id="reference-bracket-rejection"),
    ],
)
def test_url_reparse_rejects_unsupported_domain(source: str) -> None:
    with pytest.raises(OutOfScopeError):
        url_reparse_check(source)


@pytest.mark.parametrize(
    "output",
    [
        pytest.param("HTTPS://example.test/", id="stable-upper-scheme"),
        pytest.param(" https://example.test/", id="leading-space"),
        pytest.param("https://exam\tple.test/", id="embedded-tab"),
    ],
)
def test_url_reparse_detects_noncanonical_split(output: str) -> None:
    assert (result := url_reparse_check("raw", lambda _text: output)) is not None
    assert result.startswith("split/recompose changes normalized URL")


def test_url_reparse_detects_changed_second_result() -> None:
    assert (
        result := url_reparse_check(
            "raw", lambda text: "https://example.test/" if text == "raw" else "https://changed.test/"
        )
    ) is not None
    assert result.startswith("reparse changes normalized URL")


def test_url_reparse_detects_rejection_of_recomposed_output() -> None:
    def reject(text: str) -> str:
        if text == "raw":
            return "https://example.test/"
        msg = "rejected"
        raise ValueError(msg)

    assert url_reparse_check("raw", reject) == "rejects recomposed output"


def test_url_reparse_controls_discriminate() -> None:
    assert ORACLES["url-split-reparse"].controls() == {
        "stable uppercased scheme": True,
        "changes after recomposition": True,
        "rejects recomposed output": True,
    }


def test_url_reparse_cli_is_registered(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seeds = tmp_path / "tools/fuzz-data/wpt/url/resources/urltestdata.json"
    seeds.parent.mkdir(parents=True)
    seeds.write_text('[{"input": "HTTP://Example.COM:80/"}]', encoding="utf-8")
    corpus = tmp_path / "tools/fuzz/corpus/url"
    corpus.mkdir(parents=True)
    (corpus / "url.txt").write_text("https://seed.example/", encoding="utf-8")
    mocker.patch("fuzz.round_trip_oracles._ROOT", tmp_path)
    assert (
        fuzz_url_reparse_main(["--oracle", "url-split-reparse", "--minutes", "0", "--crash-dir", str(tmp_path)]),
        capsys.readouterr().out.splitlines()[-1],
    ) == (0, f"0 finding(s) written to {tmp_path}")


if TYPE_CHECKING:
    from types import ModuleType


@pytest.fixture
def fuzz_xsd_inline_engine() -> ModuleType:
    return pytest.importorskip("fuzz.xsd_inline", exc_type=ImportError)


@pytest.mark.oracle
@pytest.mark.parametrize("seed", range(9))
def test_xsd_inline_generated_labels(fuzz_xsd_inline_engine: ModuleType, seed: int) -> None:
    case: Final = fuzz_xsd_inline_engine.generate(seed)
    assert [(row.engine, row.phase, row.expected, row.actual) for row in fuzz_xsd_inline_engine.compare(case)] == [
        ("turbohtml", "compilation", case.compiles, case.compiles),
        ("libxml2", "compilation", case.compiles, case.compiles),
        *[
            (name, "validation", expected, expected)
            for _, expected in case.documents
            for name in ("turbohtml", "libxml2")
        ],
    ]


@pytest.mark.oracle
@pytest.mark.parametrize("schema", ["<", '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>'])
def test_xsd_inline_compile_verdicts(fuzz_xsd_inline_engine: ModuleType, schema: str) -> None:
    expected: Final = schema != "<"
    case: Final = fuzz_xsd_inline_engine.Case(0, schema, expected, ())
    assert [(row.engine, row.phase, row.expected, row.actual) for row in fuzz_xsd_inline_engine.compare(case)] == [
        ("turbohtml", "compilation", expected, expected),
        ("libxml2", "compilation", expected, expected),
    ]


@pytest.mark.oracle
def test_xsd_inline_unavailable_documents_remain_findings(fuzz_xsd_inline_engine: ModuleType) -> None:
    case: Final = fuzz_xsd_inline_engine.Case(0, "<", compiles=False, documents=(("<v/>", True),))
    assert [(row.engine, row.phase, row.expected, row.actual) for row in fuzz_xsd_inline_engine.compare(case)] == [
        ("turbohtml", "compilation", False, False),
        ("libxml2", "compilation", False, False),
        ("turbohtml", "validation", True, None),
        ("libxml2", "validation", True, None),
    ]


@pytest.mark.oracle
def test_xsd_inline_negative_control_changes_public_verdict(fuzz_xsd_inline_engine: ModuleType) -> None:
    case: Final = fuzz_xsd_inline_engine.generate(0)
    assert [
        (row.engine, row.phase, row.expected, row.actual)
        for row in fuzz_xsd_inline_engine.compare(case, negative_control=True)
    ] == [
        ("turbohtml", "compilation", True, True),
        ("libxml2", "compilation", True, True),
        ("turbohtml", "validation", True, False),
        ("libxml2", "validation", True, True),
        ("turbohtml", "validation", True, False),
        ("libxml2", "validation", True, True),
        ("turbohtml", "validation", False, True),
        ("libxml2", "validation", False, False),
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("arguments", "exit_status", "findings"),
    [
        pytest.param(["--cases", "8"], 0, 0, id="agreement"),
        pytest.param(["--cases", "8", "--negative-control"], 1, 18, id="wrong-verdict"),
    ],
)
def test_xsd_inline_cli(
    fuzz_xsd_inline_engine: ModuleType,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    exit_status: int,
    findings: int,
) -> None:
    assert fuzz_xsd_inline_engine.main(arguments) == exit_status
    rows: Final = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[-1] == {"summary": {"cases": 8, "rows": 52, "findings": findings}}
    assert {(row["engine"], row["phase"]) for row in rows[:-1]} == {
        ("turbohtml", "compilation"),
        ("libxml2", "compilation"),
        ("turbohtml", "validation"),
        ("libxml2", "validation"),
    }


@pytest.mark.oracle
@pytest.mark.parametrize("count", ["0", "257", "-1"])
def test_xsd_inline_cli_rejects_unbounded_cases(fuzz_xsd_inline_engine: ModuleType, count: str) -> None:
    with pytest.raises(SystemExit, match="2"):
        fuzz_xsd_inline_engine.main(["--cases", count])


if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("declared", ["latin1", "iso-8859-1", "us-ascii"])
def test_encoding_aliases_preserve_public_text(declared: str) -> None:
    text: Final = "café €"
    markup: Final = f"<meta charset={declared}><p>{text}</p>"
    data: Final = markup.encode("cp1252")
    case: Final = f"{declared}-meta-alias\n{text}"
    match: Final = detect(data)
    assert match.codec is not None
    assert (
        data,
        match.encoding,
        match.codec,
        data.decode(match.codec),
        parse(data).text,
        encoding_stream_check(case),
        encoding_decode_check(case),
    ) == (
        f"<meta charset={declared}><p>".encode("ascii") + b"caf\xe9 \x80</p>",
        "windows-1252",
        "whatwg-windows-1252",
        markup,
        text,
        None,
        None,
    )


@pytest.mark.parametrize("declared", ["latin1", "iso-8859-1", "us-ascii"])
@pytest.mark.parametrize(
    ("check", "expected"),
    [
        pytest.param(
            lambda case: encoding_stream_check(case, one_shot=lambda _data: EncodingMatch(None, 0.0, None)),
            "one-shot detection differs from expected match",
            id="wrong-one-shot",
        ),
        pytest.param(
            lambda case: encoding_stream_check(case, stream=lambda _data, _width: EncodingMatch(None, 0.0, None)),
            "chunked detection differs from expected match",
            id="wrong-stream",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, detect_bytes=lambda _data: EncodingMatch(None, 0.0, None)),
            "detection differs from expected match",
            id="wrong-detection",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, decode=lambda _data, _codec: ""),
            "detected codec differs from expected text",
            id="missing-decoded-text",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, parse_text=lambda _data: ""),
            "byte parse differs from expected text",
            id="missing-parsed-text",
        ),
    ],
)
def test_encoding_aliases_discriminate_wrong_targets(
    declared: str, check: Callable[[str], str | None], expected: str
) -> None:
    assert check(f"{declared}-meta-alias\ncafé") == expected


@pytest.mark.parametrize("declared", ["latin1", "iso-8859-1", "us-ascii"])
def test_encoding_aliases_reject_cpython_latin1(declared: str) -> None:
    assert (
        encoding_decode_check(f"{declared}-meta-alias\ncafé €", decode=lambda data, _codec: data.decode("latin1"))
        == "detected codec differs from expected text"
    )


if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("declared", ["utf-16", "utf-16le", "utf-16be"])
def test_encoding_meta_remap_preserves_public_text(declared: str) -> None:
    text: Final = "café €中文𐐀"
    markup: Final = f"<meta charset={declared}><p>{text}</p>"
    data: Final = markup.encode("utf-8")
    case: Final = f"{declared}-meta-remap\n{text}"
    match: Final = detect(data)
    assert match.codec is not None
    assert (
        match.encoding,
        match.codec,
        data.decode(match.codec),
        parse(data).text,
        encoding_stream_check(case),
        encoding_decode_check(case),
    ) == ("UTF-8", "whatwg-utf-8", markup, text, None, None)


@pytest.mark.parametrize("declared", ["utf-16", "utf-16le", "utf-16be"])
@pytest.mark.parametrize(
    ("check", "expected"),
    [
        pytest.param(
            lambda case: encoding_stream_check(case, one_shot=lambda _data: EncodingMatch(None, 0.0, None)),
            "one-shot detection differs from expected match",
            id="wrong-one-shot",
        ),
        pytest.param(
            lambda case: encoding_stream_check(case, stream=lambda _data, _width: EncodingMatch(None, 0.0, None)),
            "chunked detection differs from expected match",
            id="wrong-stream",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, detect_bytes=lambda _data: EncodingMatch(None, 0.0, None)),
            "detection differs from expected match",
            id="wrong-detection",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, decode=lambda _data, _codec: ""),
            "detected codec differs from expected text",
            id="missing-decoded-text",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, parse_text=lambda _data: ""),
            "byte parse differs from expected text",
            id="missing-parsed-text",
        ),
    ],
)
def test_encoding_meta_remap_discriminates_wrong_targets(
    declared: str, check: Callable[[str], str | None], expected: str
) -> None:
    assert check(f"{declared}-meta-remap\ncafé") == expected


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "expected"),
    [
        pytest.param("reorder:", "q\u0301\u0323", "xn--q-xbb5h", id="reorder"),
        pytest.param("blocked:", "a\u0305\u0301", "xn--a-xbbl", id="blocked"),
        pytest.param("successive-compose:", "a\u030a\u0301", "xn--3ka", id="successive-compose"),
        pytest.param("noncomposing-tail:", "s\u0323\u0301", "xn--lsa331l", id="noncomposing-tail"),
        pytest.param("lower-class-intervening:", "a\u0316\u0301", "xn--1ca44i", id="lower-class-intervening"),
        pytest.param("hangul-lv:", "\u1100\u1161", "xn--o39a", id="hangul-lv"),
        pytest.param("hangul-lvt:", "\u1100\u1161\u11a8", "xn--p39a", id="hangul-lvt"),
        pytest.param("hangul-lv-plus-t:", "\uac00\u11a8", "xn--p39a", id="hangul-lv-plus-t"),
    ],
)
def test_idna_nfc_pins_public_target(case: str, source: str, expected: str) -> None:
    assert (normalize_url(f"https://{source}.example/"), idna_nfc_check(case)) == (
        f"https://{expected}.example/",
        None,
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="empty"),
        pytest.param("reorder", id="missing-separator"),
        pytest.param("unknown:", id="unknown-family"),
        pytest.param("reorder:A", id="uppercase"),
        pytest.param("reorder:abcdefghi", id="overlong"),
        pytest.param("reorder:a:b", id="extra-separator"),
    ],
)
def test_idna_nfc_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedIdnaNfcCaseError):
        idna_nfc_check(case)


@pytest.mark.parametrize(
    ("normalize", "expected"),
    [
        pytest.param(lambda text: text, "canonical host differs from independent target", id="unchanged"),
        pytest.param(
            lambda _text: "https://wrong.example/",
            "canonical host differs from independent target",
            id="stable-wrong-target",
        ),
        pytest.param(
            lambda _text: _fuzz_idna_nfc_invariants_reject(), "rejects known-valid canonical host", id="input-rejection"
        ),
        pytest.param(
            lambda text: _fuzz_idna_nfc_invariants_reject() if text.isascii() else normalize_url(text),
            "rejects canonical host output",
            id="output-rejection",
        ),
        pytest.param(
            lambda text: text + "x" if text.isascii() else normalize_url(text),
            "canonical host changes on repeat",
            id="changed-repeat",
        ),
    ],
)
def test_idna_nfc_discriminates_failures(normalize: Callable[[str], str], expected: str) -> None:
    assert idna_nfc_check("reorder:", normalize) == expected


def _fuzz_idna_nfc_invariants_reject() -> str:
    msg = "rejected"
    raise ValueError(msg)


def test_idna_nfc_registry_controls_and_seed_floor() -> None:
    seeds: Final = idna_nfc_seeds()
    assert (
        all(idna_nfc_controls().values()),
        len(seeds),
        all(ORACLES["idna-nfc"].check(case) is None for case in seeds),
        ORACLES["idna-nfc"].floor,
    ) == (True, 160, True, Floor(100, 1))


def test_idna_nfc_generated_cases_compare() -> None:
    rng: Final = random.Random(1010)  # ruff: ignore[suspicious-non-cryptographic-random-usage]  # reproduce oracle cases
    assert [idna_nfc_check(idna_nfc_generate(rng)) for _ in range(100)] == [None] * 100


def test_idna_nfc_registry_counts_unsupported_case() -> None:
    with pytest.raises(OutOfScopeError):
        ORACLES["idna-nfc"].check("unknown:")


def test_idna_nfc_cli_detects_stable_wrong_mapping(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["idna-nfc"],
        check=partial(idna_nfc_check, normalize=lambda _text: "https://wrong.example/"),
        seeds=lambda: ["reorder:"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"idna-nfc": oracle}, clear=True)
    assert (
        fuzz_idna_nfc_invariants_main(["--minutes", "0", "--crash-dir", str(tmp_path)]),
        len(list(tmp_path.glob("*.json"))),
    ) == (1, 1)


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("variant", "raw", "label", "expected", "encoding"),
    [
        pytest.param(
            "utf8",
            b"<p>caf\xc3\xa9 \xe6\x97\xa5\xe6\x9c\xac\xf0\x9f\x98\x80</p>",
            "utf-8",
            "café 日本😀",
            "UTF-8",
            id="utf8",
        ),
        pytest.param("shift-jis", b"<p>\x93\xfa\x96{\x8c\xea</p>", "shift_jis", "日本語", "Shift_JIS", id="shift-jis"),
        pytest.param(
            "iso-2022-jp", b"<p>\x1b$BF|K\\8l\x1b(B</p>", "iso-2022-jp", "日本語", "ISO-2022-JP", id="iso-2022-jp"
        ),
        pytest.param("windows1252", b"<p>caf\xe9 \x80</p>", "windows-1252", "café €", "windows-1252", id="windows1252"),
        pytest.param("utf8-bom", b"\xef\xbb\xbf<p>\xc3\xa9</p>", "windows-1252", "é", "UTF-8", id="utf8-bom"),
        pytest.param(
            "utf16le-bom",
            b"\xff\xfe<\x00p\x00>\x00\xe9\x00<\x00/\x00p\x00>\x00",
            "windows-1252",
            "é",
            "UTF-16LE",
            id="utf16le-bom",
        ),
        pytest.param(
            "utf16be-bom",
            b"\xfe\xff\x00<\x00p\x00>\x00\xe9\x00<\x00/\x00p\x00>",
            "utf-8",
            "é",
            "UTF-16BE",
            id="utf16be-bom",
        ),
    ],
)
def test_parser_byte_oracle_pins_text_and_canonical_label(
    variant: str, raw: bytes, label: str, expected: str, encoding: str
) -> None:
    document: Final = parse(raw, encoding=label)
    outcomes: Final = [(document.text, document.encoding)]
    for width in (1, 2, 3, 4, 5, len(raw)):
        parser = IncrementalParser(encoding=label)
        parser.feed(b"")
        for start in range(0, len(raw), width):
            parser.feed(raw[start : start + width])
            parser.feed(b"")
        result = parser.close()
        outcomes.append((result.text, result.encoding))
    assert (outcomes, parser_bytes_check(f"{variant}:g")) == ([(expected, encoding)] * 7, None)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("utf8", id="missing-separator"),
        pytest.param("unknown:g", id="unknown-fixture"),
        pytest.param("utf8:", id="empty-prefix"),
        pytest.param("utf8:é", id="non-ascii-prefix"),
        pytest.param("utf8:g<p>", id="markup-prefix"),
        pytest.param("utf8:g" + "1" * 16, id="overlong-prefix"),
    ],
)
def test_parser_byte_oracle_rejects_unsupported_grammar(source: str) -> None:
    with pytest.raises(UnsupportedParserCaseError):
        parser_bytes_check(source)
    with pytest.raises(OutOfScopeError):
        ORACLES["parser-bytes"].check(source)


@pytest.mark.parametrize(
    ("one_shot", "stream", "expected"),
    [
        pytest.param(
            lambda _data, _label: ("", "UTF-8"),
            None,
            "one-shot parser differs from fixed text or label",
            id="wrong-first-text",
        ),
        pytest.param(
            lambda _data, _label: ("g:café 日本😀", None),
            None,
            "one-shot parser differs from fixed text or label",
            id="wrong-first-label",
        ),
        pytest.param(
            None,
            lambda _data, _label, _width: ("", "UTF-8"),
            "streaming parser differs from fixed text or label",
            id="wrong-stream-text",
        ),
        pytest.param(
            None,
            lambda _data, _label, _width: ("g:café 日本😀", None),
            "streaming parser differs from fixed text or label",
            id="wrong-stream-label",
        ),
        pytest.param(
            lambda _data, _label: ("", None),
            lambda _data, _label, _width: ("", None),
            "one-shot parser differs from fixed text or label",
            id="shared-wrong-agreement",
        ),
    ],
)
def test_parser_byte_oracle_discriminates_wrong_results(
    one_shot: Callable[[bytes, str], tuple[str, str | None]] | None,
    stream: Callable[[bytes, str, int], tuple[str, str | None]] | None,
    expected: str,
) -> None:
    assert parser_bytes_check("utf8:g", one_shot, stream) == expected


def test_parser_byte_registry_generated_cases_and_controls() -> None:
    rng: Final = random.SystemRandom()
    generated: Final = [parser_bytes_generate(rng) for _ in range(100)]
    seeds: Final = parser_bytes_seeds()
    assert (
        len(seeds),
        all(parser_bytes_check(case) is None for case in [*seeds, *generated]),
        parser_bytes_controls(),
    ) == (
        210,
        True,
        {
            "wrong one-shot text": True,
            "wrong one-shot label": True,
            "wrong streaming text": True,
            "wrong streaming label": True,
            "shared wrong result": True,
        },
    )


def test_parser_byte_cli_compares_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = fuzz_parser_byte_invariants_main([
        "--oracle",
        "parser-bytes",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, list(tmp_path.glob("crash-*")), "210" in capsys.readouterr().out) == (0, [], True)


def test_parser_byte_cli_reports_valid_fixture_rejection(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["parser-bytes"],
        check=partial(parser_bytes_check, one_shot=_reject_fixture),
        seeds=lambda: ["utf8:g"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"parser-bytes": oracle}, clear=True)
    status: Final = fuzz_parser_byte_invariants_main([
        "--oracle",
        "parser-bytes",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (1, 1, "raised ValueError", 1)


def _reject_fixture(_data: bytes, _label: str) -> tuple[str, str | None]:
    message: Final = "rejected valid bytes"
    raise ValueError(message)


def test_parser_byte_cli_rejects_vacuous_run(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        ORACLES,
        {"parser-bytes": replace(ORACLES["parser-bytes"], seeds=lambda: ["unknown:g"], floor=Floor(1, 1))},
        clear=True,
    )
    assert (
        fuzz_parser_byte_invariants_main(["--oracle", "parser-bytes", "--minutes", "0", "--crash-dir", str(tmp_path)])
        == 2
    )


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "expected"),
    [
        pytest.param("umlaut\n", "münchen.example", "xn--mnchen-3ya.example", id="umlaut"),
        pytest.param("sharp-s\n", "faß.example", "xn--fa-hia.example", id="nontransitional"),
        pytest.param("acute-decomposed\n", "a\u0301.example", "xn--1ca.example", id="acute-decomposed"),
        pytest.param("acute-precomposed\n", "á.example", "xn--1ca.example", id="acute-precomposed"),
        pytest.param("umlaut-decomposed\n", "u\u0308.example", "xn--tda.example", id="umlaut-decomposed"),
        pytest.param("japanese\n", "日本語.example", "xn--wgv71a119e.example", id="japanese"),
        pytest.param("greek\n", "δοκιμή.example", "xn--jxalpdlp.example", id="greek"),
        pytest.param("ignored\n", "ex\u00adample.example", "example.example", id="ignored-character"),
        pytest.param("fullwidth\n", "\uff26\uff2f\uff2f.example", "foo.example", id="fullwidth"),
        pytest.param("mapped-dot\n", "日本語。\uff2a\uff30.example", "xn--wgv71a119e.jp.example", id="mapped-dot"),
        pytest.param("umlaut\na", "amünchen.example", "xn--amnchen-o2a.example", id="affix"),
    ],
)
def test_idna_invariant_pins_public_output(case: str, source: str, expected: str) -> None:
    url: Final = f"https://{expected}/"
    assert (normalize_url(f"https://{source}/"), normalize_url(url), idna_host_check(case)) == (url, url, None)


@pytest.mark.parametrize("case", ["", "umlaut", "unknown\n", "umlaut\nA", "umlaut\nabcdefghi", "umlaut\na\nb"])
def test_idna_invariant_counts_unsupported_cases(case: str) -> None:
    with pytest.raises(OutOfScopeError):
        idna_host_check(case)


@pytest.mark.parametrize(
    ("normalize", "expected"),
    [
        pytest.param(lambda text: text, "IDNA output is not ASCII", id="unchanged-input"),
        pytest.param(
            lambda _text: "https://wrong.example/", "IDNA host differs from expected mapping", id="wrong-mapping"
        ),
        pytest.param(
            lambda _text: "https://xn--0.example/", "IDNA output has undecodable Punycode", id="invalid-punycode"
        ),
        pytest.param(lambda _text: "https://xn--u-ccb.example/", "IDNA decoded label is not NFC", id="non-nfc"),
        pytest.param(
            lambda _text: "https://xn--mnchen-3yA.example/", "IDNA Punycode is not canonical", id="noncanonical"
        ),
        pytest.param(
            lambda _text: _fuzz_idna_invariants_reject(), "rejects known-valid Unicode host", id="input-rejection"
        ),
        pytest.param(
            lambda text: _fuzz_idna_invariants_reject() if text.isascii() else normalize_url(text),
            "rejects canonical IDNA output",
            id="output-rejection",
        ),
        pytest.param(
            lambda text: text + "a" if text.isascii() else normalize_url(text),
            "IDNA output is not a fixpoint",
            id="unstable-output",
        ),
    ],
)
def test_idna_invariant_discriminates_failures(normalize: Callable[[str], str], expected: str) -> None:
    assert idna_host_check("umlaut\n", normalize) == expected


def _fuzz_idna_invariants_reject() -> str:
    msg = "rejected"
    raise ValueError(msg)


def test_idna_registry_has_discriminating_controls_and_seed_floor() -> None:
    oracle: Final = ORACLES["idna-host"]
    seeds: Final = oracle.seeds()
    assert (
        all(oracle.controls().values()),
        len(seeds),
        all(oracle.check(case) is None for case in seeds),
        oracle.floor,
    ) == (
        True,
        160,
        True,
        Floor(100, 0.95),
    )


def test_idna_cli_writes_mapping_failure(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["idna-host"],
        check=partial(idna_host_check, normalize=lambda text: text),
        seeds=lambda: ["umlaut\n"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"idna-host": oracle}, clear=True)
    status: Final = fuzz_idna_invariants_main(["--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    assert len(findings) == 1
    minimized: Final = findings[0].with_suffix("").read_text(encoding="utf-8")
    assert (
        status,
        json.loads(findings[0].read_text()),
        findings[0].stem,
        idna_host_check(minimized, lambda text: text),
    ) == (
        1,
        {"oracle": "idna-host", "detail": "IDNA output is not ASCII", "hits": 1, "rng_seed": 0},
        f"crash-{hashlib.sha256(minimized.encode()).hexdigest()}",
        "IDNA output is not ASCII",
    )


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


_BASE: Final = "http://example.test/b/c/d;p?q"


@pytest.mark.parametrize(
    ("case", "reference", "expected"),
    [
        pytest.param("child", "g", "http://example.test/b/c/g", id="child"),
        pytest.param("current", "./g", "http://example.test/b/c/g", id="current"),
        pytest.param("parent", "../g", "http://example.test/b/g", id="parent"),
        pytest.param("grandparent", "../../g", "http://example.test/g", id="grandparent"),
        pytest.param("root", "/g", "http://example.test/g", id="root"),
        pytest.param("query", "?g", "http://example.test/b/c/d;p?g", id="query"),
        pytest.param("fragment", "#g", "http://example.test/b/c/d;p?q#g", id="fragment"),
        pytest.param("directory", "g/", "http://example.test/b/c/g/", id="directory"),
    ],
)
def test_resolve_links_oracle_pins_first_and_repeated_targets(case: str, reference: str, expected: str) -> None:
    document = parse_fragment('<a href="">link</a>')
    document.select("a")[0].attrs["href"] = reference
    document.resolve_links(_BASE)
    first: Final = document.links()[0].url
    document.resolve_links(_BASE)
    assert (first, document.links()[0].url, resolve_links_check(f"{case}:g")) == (expected, expected, None)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("parent", id="missing-separator"),
        pytest.param("unknown:g", id="unknown-case"),
        pytest.param("parent:", id="empty-leaf"),
        pytest.param("parent:g/path", id="path-in-leaf"),
        pytest.param("parent:é", id="unicode-leaf"),
        pytest.param("parent:g" + "1" * 16, id="overlong-leaf"),
    ],
)
def test_resolve_links_oracle_counts_unsupported_grammar(source: str) -> None:
    with pytest.raises(OutOfScopeError):
        resolve_links_check(source)


def _wrong_target(node: Node, _base: str) -> None:
    node.select("a")[0].attrs["href"] = "http://wrong.example/"


def _changed_repeat(node: Node, base: str) -> None:
    if node.links()[0].url.startswith("http:"):
        node.select("a")[0].attrs["href"] = node.links()[0].url + "a"
    else:
        node.resolve_links(base)


@pytest.mark.parametrize(
    ("resolve", "expected"),
    [
        pytest.param(lambda _node, _base: None, "resolution differs from RFC target", id="no-op"),
        pytest.param(_wrong_target, "resolution differs from RFC target", id="stable-wrong-target"),
        pytest.param(_changed_repeat, "resolved link changes on repeat", id="changed-repeat"),
    ],
)
def test_resolve_links_oracle_discriminates_failures(resolve: Callable[[Node, str], None], expected: str) -> None:
    assert resolve_links_check("parent:g", resolve) == expected


def test_resolve_links_registry_checks_seed_batch_and_controls() -> None:
    oracle: Final = ORACLES["resolve-links"]
    seeds: Final = oracle.seeds()
    assert (
        len(seeds),
        len(set(seeds)) >= 100,
        all(oracle.check(seed) is None for seed in seeds),
        oracle.controls(),
    ) == (
        500,
        True,
        True,
        {"unchanged relative link": True, "stable wrong target": True, "changed repeat": True},
    )


def test_resolve_links_cli_compares_seeds(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result: Final = fuzz_resolve_links_invariants_main([
        "--oracle",
        "resolve-links",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (result, list(tmp_path.glob("crash-*")), "resolve-links" in capsys.readouterr().out) == (0, [], True)


def test_resolve_links_cli_writes_wrong_target_finding(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["resolve-links"],
        check=partial(resolve_links_check, resolve=_wrong_target),
        seeds=lambda: ["parent:g"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"resolve-links": oracle}, clear=True)
    result: Final = fuzz_resolve_links_invariants_main([
        "--oracle",
        "resolve-links",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (result, len(findings), payload["oracle"], payload["detail"], payload["hits"]) == (
        1,
        1,
        "resolve-links",
        "resolution differs from RFC target",
        1,
    )


def test_resolve_links_cli_rejects_vacuous_run(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        ORACLES,
        {"resolve-links": replace(ORACLES["resolve-links"], seeds=lambda: ["parent"], floor=Floor(1, 1))},
        clear=True,
    )
    assert (
        fuzz_resolve_links_invariants_main([
            "--oracle",
            "resolve-links",
            "--minutes",
            "0",
            "--crash-dir",
            str(tmp_path),
        ])
        == 2
    )


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "expected"),
    [
        pytest.param(
            "p:Leaf:double:1:plain",
            '<Root xmlns:p="urn:p"><p:Leaf p:Key="Value" Key="Other">MiXeD</p:Leaf></Root>',
            [("Root", {"xmlns:p": "urn:p"}, "MiXeD"), ("p:Leaf", {"p:Key": "Value", "Key": "Other"}, "MiXeD")],
            id="qualified-case",
        ),
        pytest.param(
            "p:Leaf:double:3:plain",
            '<Root xmlns:p="urn:p">' + '<p:Leaf p:Key="Value" Key="Other">MiXeD</p:Leaf>' * 3 + "</Root>",
            [("Root", {"xmlns:p": "urn:p"}, "MiXeDMiXeDMiXeD")]
            + [("p:Leaf", {"p:Key": "Value", "Key": "Other"}, "MiXeD")] * 3,
            id="three-identical-children",
        ),
        pytest.param(
            "P:leaf:single:1:escaped",
            "<Root xmlns:P='urn:P'><P:leaf P:Key='&quot;&apos;&amp;&lt;' Key='Other'>&amp;&lt;&gt;</P:leaf></Root>",
            [("Root", {"xmlns:P": "urn:P"}, "&<>"), ("P:leaf", {"P:Key": "\"'&<", "Key": "Other"}, "&<>")],
            id="escaped-delimiters",
        ),
        pytest.param(
            "p:Leaf:double:1:literal",
            '<Root xmlns:p="urn:p"><p:Leaf p:Key="a\tb\r\nc\rd" Key="Other">a\tb\r\nc\rd</p:Leaf></Root>',
            [
                ("Root", {"xmlns:p": "urn:p"}, "a\tb\nc\nd"),
                ("p:Leaf", {"p:Key": "a b c d", "Key": "Other"}, "a\tb\nc\nd"),
            ],
            id="literal-whitespace",
        ),
        pytest.param(
            "p:Leaf:double:1:numeric",
            '<Root xmlns:p="urn:p"><p:Leaf p:Key="a&#9;b&#10;c&#13;d" Key="Other">a&#9;b&#10;c&#13;d</p:Leaf></Root>',
            [
                ("Root", {"xmlns:p": "urn:p"}, "a\tb\nc\rd"),
                ("p:Leaf", {"p:Key": "a\tb\nc\rd", "Key": "Other"}, "a\tb\nc\rd"),
            ],
            id="referenced-whitespace",
        ),
    ],
)
def test_xml_literal_trees(case: str, source: str, expected: list[tuple[str, dict[str, str], str]]) -> None:
    assert (
        [(node.tag, dict(node.attrs), node.text) for node in parse_xml(source).iter_elements()],
        xml_literal_check(case),
        xml_literal_check(case, lambda _node: source),
        xml_literal_check(case, lambda _node: "<Root/>"),
    ) == (expected, None, None, "serialized XML tree differs from literals")


def test_xml_literal_rejects_nested_second_child() -> None:
    source: Final = (
        '<Root xmlns:p="urn:p"><p:Leaf p:Key="Value" Key="Other">MiXeD'
        '<p:Leaf p:Key="Value" Key="Other">MiXeD</p:Leaf></p:Leaf></Root>'
    )
    assert xml_literal_check("p:Leaf:double:2:plain", lambda _node: source) == (
        "serialized XML tree differs from literals"
    )


def test_xml_literal_checks_first_parse() -> None:
    assert xml_literal_check("p:Leaf:double:1:plain", read=lambda _source: parse_xml("<Root/>")) == (
        "parsed XML tree differs from literals"
    )


def test_xml_literal_rejects_unexpected_comment() -> None:
    assert xml_literal_check("p:Leaf:double:1:plain", lambda _node: "<Root><!--extra--></Root>") == (
        "serialized XML tree differs from literals"
    )


def test_xml_literal_checks_repeat_formatting() -> None:
    outputs: Final = iter([
        '<Root xmlns:p="urn:p"><p:Leaf p:Key="Value" Key="Other">MiXeD</p:Leaf></Root>',
        "<Root xmlns:p='urn:p'><p:Leaf p:Key='Value' Key='Other'>MiXeD</p:Leaf></Root>",
    ])
    assert (
        xml_literal_check("p:Leaf:double:1:plain", lambda _node: next(outputs)) == "XML serialization changes on repeat"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="missing-program"),
        pytest.param("q:Leaf:double:1:plain", id="prefix-pool"),
        pytest.param("p:Other:double:1:plain", id="name-pool"),
        pytest.param("p:Leaf:bare:1:plain", id="quote-pool"),
        pytest.param("p:Leaf:double:0:plain", id="zero-children"),
        pytest.param("p:Leaf:double:4:plain", id="node-budget"),
        pytest.param("p:Leaf:double:1:raw", id="terminal-pool"),
    ],
)
def test_xml_literal_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedXmlLiteralCaseError):
        xml_literal_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["xml-literal-grammar"].check(case)


def test_xml_literal_production_sweep_and_bounds() -> None:
    rng: Final = random.Random(1018)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - Reproducible grammar samples.
    cases: Final = [*xml_literal_seeds(), *(xml_literal_generate(rng) for _ in range(100))]
    assert (
        len(xml_literal_seeds()),
        {case.split(":")[index] for case in cases for index in (0, 1, 2, 4)},
        max(int(case.split(":")[3]) for case in cases),
        all(xml_literal_check(case) is None for case in cases),
        xml_literal_controls(),
    ) == (
        96,
        {"p", "P", "Leaf", "leaf", "single", "double", "plain", "escaped", "literal", "numeric"},
        3,
        True,
        {
            "nested second child": True,
            "changed declaration": True,
            "changed qualified name": True,
            "referenced whitespace folded": True,
        },
    )


def test_xml_literal_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = fuzz_xml_literal_invariants_main([
        "--oracle",
        "xml-literal-grammar",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, list(tmp_path.glob("crash-*")), "96" in capsys.readouterr().out) == (0, [], True)


def test_xml_literal_cli_emits_changed_declaration(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["xml-literal-grammar"],
        check=partial(
            xml_literal_check,
            serialize=lambda _node: '<Root xmlns:p="urn:wrong"><p:Leaf p:Key="Value" Key="Other">MiXeD</p:Leaf></Root>',
        ),
        seeds=lambda: ["p:Leaf:double:1:plain"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"xml-literal-grammar": oracle}, clear=True)
    status: Final = fuzz_xml_literal_invariants_main([
        "--oracle",
        "xml-literal-grammar",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "serialized XML tree differs from literals",
        1,
    )


def test_xml_literal_cli_rejection_is_out_of_scope(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle: Final = replace(ORACLES["xml-literal-grammar"], seeds=lambda: ["invalid"], floor=Floor(1, 1))
    mocker.patch.dict(ORACLES, {"xml-literal-grammar": oracle}, clear=True)
    status: Final = fuzz_xml_literal_invariants_main([
        "--oracle",
        "xml-literal-grammar",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, list(tmp_path.glob("*.json")), "VACUOUS RUN" in capsys.readouterr().err) == (2, [], True)


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "expected"),
    [
        pytest.param(
            "inside:plain:plain:1",
            "<Root><!--note--><![CDATA[Value]]>&lt;&amp;</Root>",
            [
                ("Element", "Root", "Document"),
                ("Comment", "note", "Root"),
                ("CData", "Value", "Root"),
                ("Text", "<&", "Root"),
            ],
            id="lexical-kinds",
        ),
        pytest.param(
            "outside:plain:plain:2",
            "<!--lead &amp;--><Root>" + "<!--note--><![CDATA[Value]]>&lt;&amp;" * 2 + "</Root><!--tail-->",
            [("Comment", "lead &amp;", "Document"), ("Element", "Root", "Document")]
            + [("Comment", "note", "Root"), ("CData", "Value", "Root"), ("Text", "<&", "Root")] * 2
            + [("Comment", "tail", "Document")],
            id="outside-root-attachment",
        ),
        pytest.param(
            "inside:markup:markup:1",
            "<Root><!--<x> &amp;--><![CDATA[<x>&amp;]]>&lt;&amp;</Root>",
            [
                ("Element", "Root", "Document"),
                ("Comment", "<x> &amp;", "Root"),
                ("CData", "<x>&amp;", "Root"),
                ("Text", "<&", "Root"),
            ],
            id="opaque-reference-looking-data",
        ),
        pytest.param(
            "inside:lines:lines:1",
            "<Root><!--a\r\nb\rc--><![CDATA[a\r\nb\rc]]>&lt;&amp;</Root>",
            [
                ("Element", "Root", "Document"),
                ("Comment", "a\nb\nc", "Root"),
                ("CData", "a\nb\nc", "Root"),
                ("Text", "<&", "Root"),
            ],
            id="literal-line-endings",
        ),
        pytest.param(
            "inside:plain:markup:3",
            "<Root>" + "<!--note--><![CDATA[<x>&amp;]]>&lt;&amp;" * 3 + "</Root>",
            [("Element", "Root", "Document")]
            + [("Comment", "note", "Root"), ("CData", "<x>&amp;", "Root"), ("Text", "<&", "Root")] * 3,
            id="three-lexical-groups",
        ),
    ],
)
def test_xml_island_literal_nodes(case: str, source: str, expected: list[tuple[str, str, str]]) -> None:
    assert (
        [
            (
                type(node).__name__,
                node.tag if isinstance(node, Element) else node.data,
                parent.tag if isinstance(parent := node.parent, Element) else "Document",
            )
            for node in parse_xml(source).descendants
            if isinstance(node, (Element, Comment, CData, Text))
        ],
        xml_island_check(case),
        xml_island_check(case, lambda _node: source.replace("\r\n", "\n").replace("\r", "\n")),
        xml_island_check(case, lambda _node: "<Root/>"),
    ) == (expected, None, None, "serialized XML islands differ from literals")


def test_xml_island_checks_first_parse() -> None:
    assert xml_island_check("inside:plain:plain:1", read=lambda _source: parse_xml("<Root/>")) == (
        "parsed XML islands differ from literals"
    )


def test_xml_island_rejects_unexpected_instruction() -> None:
    assert xml_island_check("inside:plain:plain:1", lambda _node: "<Root><?extra data?></Root>") == (
        "serialized XML islands differ from literals"
    )


def test_xml_island_rejects_changed_attachment() -> None:
    source: Final = "<!--lead &amp;--><Root><!--note--><![CDATA[Value]]>&lt;&amp;<!--tail--></Root>"
    assert xml_island_check("outside:plain:plain:1", lambda _node: source) == (
        "serialized XML islands differ from literals"
    )


def test_xml_island_checks_repeat_formatting() -> None:
    outputs: Final = iter([
        "<Root><!--note--><![CDATA[Value]]>&lt;&amp;</Root>",
        "<Root ><!--note--><![CDATA[Value]]>&lt;&amp;</Root>",
    ])
    assert xml_island_check("inside:plain:plain:1", lambda _node: next(outputs)) == (
        "XML island serialization changes on repeat"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="missing-program"),
        pytest.param("around:plain:plain:1", id="attachment-pool"),
        pytest.param("inside:hyphens:plain:1", id="comment-pool"),
        pytest.param("inside:plain:delimiter:1", id="cdata-pool"),
        pytest.param("inside:plain:plain:0", id="zero-groups"),
        pytest.param("inside:plain:plain:4", id="node-budget"),
    ],
)
def test_xml_island_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedXmlIslandCaseError):
        xml_island_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["xml-island-grammar"].check(case)


def test_xml_island_production_sweep_and_bounds() -> None:
    rng: Final = random.Random(1018)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - Reproducible grammar samples.
    cases: Final = [*xml_island_seeds(), *(xml_island_generate(rng) for _ in range(100))]
    assert (
        len(xml_island_seeds()),
        {case.split(":")[index] for case in cases for index in (0, 1, 2)},
        max(int(case.rsplit(":", 1)[1]) for case in cases),
        all(xml_island_check(case) is None for case in cases),
        xml_island_controls(),
    ) == (
        54,
        {"inside", "outside", "plain", "markup", "lines"},
        3,
        True,
        {"CDATA converted to Text": True, "comment moved into Root": True, "CDATA reference decoded": True},
    )


def test_xml_island_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = fuzz_xml_island_invariants_main([
        "--oracle",
        "xml-island-grammar",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, list(tmp_path.glob("crash-*")), "54" in capsys.readouterr().out) == (0, [], True)


def test_xml_island_cli_emits_kind_loss(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["xml-island-grammar"],
        check=partial(xml_island_check, serialize=lambda _node: "<Root><!--note-->Value&lt;&amp;</Root>"),
        seeds=lambda: ["inside:plain:plain:1"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"xml-island-grammar": oracle}, clear=True)
    status: Final = fuzz_xml_island_invariants_main([
        "--oracle",
        "xml-island-grammar",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "serialized XML islands differ from literals",
        1,
    )


def test_xml_island_cli_rejection_is_out_of_scope(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle: Final = replace(ORACLES["xml-island-grammar"], seeds=lambda: ["invalid"], floor=Floor(1, 1))
    mocker.patch.dict(ORACLES, {"xml-island-grammar": oracle}, clear=True)
    status: Final = fuzz_xml_island_invariants_main([
        "--oracle",
        "xml-island-grammar",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, list(tmp_path.glob("*.json")), "VACUOUS RUN" in capsys.readouterr().err) == (2, [], True)


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "text", "expected"),
    [
        pytest.param(
            "sheet:ident:2:0", "a { --Foo : MiXeD ; --foo : UPPER ; }", "a{--Foo:MiXeD;--foo:UPPER}", id="name-case"
        ),
        pytest.param("inline:ident:1:0", " --Foo : MiXeD ; ", "--Foo:MiXeD", id="opaque-identifier"),
        pytest.param(
            "sheet:string:2:0",
            "a { --Foo : 'A B' ; --foo : 'x;y' ; }",
            "a{--Foo:'A B';--foo:'x;y'}",
            id="quoted-delimiter",
        ),
        pytest.param("inline:string:1:0", " --Foo : 'A B' ; ", "--Foo:'A B'", id="inline-string"),
    ],
)
def test_css_custom_literals_require_conservation_and_shortening(case: str, text: str, expected: str) -> None:
    printer: Final = minify_css if case.startswith("sheet:") else minify_css_inline
    assert (
        printer(text),
        printer(expected),
        css_custom_check(case),
        css_custom_check(case, lambda _text: expected),
        css_custom_check(case, lambda text: text),
    ) == (
        expected,
        expected,
        None,
        None,
        "custom-property tokens differ from literals",
    )


@pytest.mark.parametrize("quote", [pytest.param("'", id="single-quote"), pytest.param('"', id="double-quote")])
def test_css_custom_accepts_either_string_quote(quote: str) -> None:
    expected: Final = f"--Foo:{quote}A B{quote}"
    assert css_custom_check("inline:string:1:0", lambda _text: expected) is None


def test_css_custom_repeated_output_is_checked() -> None:
    expected: Final = "--Foo:MiXeD"
    assert css_custom_check("inline:ident:1:0", lambda text: expected if text != expected else expected + " ") == (
        "custom-property output changes on repeat"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="missing-program"),
        pytest.param("sheet:ident:0:0", id="zero-declarations"),
        pytest.param("sheet:ident:5:0", id="node-budget"),
        pytest.param("sheet:string:1:8", id="payload-pool"),
        pytest.param("other:ident:1:0", id="root-production"),
        pytest.param("inline:color:1:0", id="leaf-production"),
    ],
)
def test_css_custom_rejects_unsupported_productions(case: str) -> None:
    with pytest.raises(UnsupportedCssCustomCaseError):
        css_custom_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["css-custom-tokens"].check(case)


def test_css_custom_production_sweep_and_bounds() -> None:
    seeds: Final = css_custom_seeds()
    rng: Final = random.Random(1018)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - Reproduce grammar samples; no security tokens.
    generated: Final = [css_custom_generate(rng) for _ in range(100)]
    productions: Final = Counter(tuple(case.split(":")[:2]) for case in seeds)
    assert (
        len(seeds),
        productions,
        all(css_custom_check(case) is None for case in [*seeds, *generated]),
        max(int(case.split(":")[2]) for case in [*seeds, *generated]),
        css_custom_controls(),
    ) == (
        128,
        {("sheet", "ident"): 32, ("sheet", "string"): 32, ("inline", "ident"): 32, ("inline", "string"): 32},
        True,
        4,
        {"identity minifier": True, "folded name": True, "folded payload": True, "lost string content": True},
    )


def test_css_custom_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = fuzz_css_custom_invariants_main([
        "--oracle",
        "css-custom-tokens",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, list(tmp_path.glob("crash-*")), "128" in capsys.readouterr().out) == (0, [], True)


def test_css_custom_cli_emits_token_loss(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["css-custom-tokens"],
        check=partial(css_custom_check, minify=lambda _text: ""),
        seeds=lambda: ["inline:ident:1:0"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"css-custom-tokens": oracle}, clear=True)
    status: Final = fuzz_css_custom_invariants_main([
        "--oracle",
        "css-custom-tokens",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "custom-property tokens differ from literals",
        1,
    )


def test_css_custom_cli_rejection_is_out_of_scope(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle: Final = replace(ORACLES["css-custom-tokens"], seeds=lambda: ["invalid"], floor=Floor(1, 1))
    mocker.patch.dict(ORACLES, {"css-custom-tokens": oracle}, clear=True)
    status: Final = fuzz_css_custom_invariants_main([
        "--oracle",
        "css-custom-tokens",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, list(tmp_path.glob("*.json")), "VACUOUS RUN" in capsys.readouterr().err) == (2, [], True)


if TYPE_CHECKING:
    from types import ModuleType

    from pytest_mock import MockerFixture

_XML: Final = '<root id="n0"><item id="n1" score="2">alpha</item><item id="n2" score="5">beta</item></root>'


@pytest.fixture
def anchored() -> ModuleType:
    return pytest.importorskip("fuzz.xpath_anchored", exc_type=ImportError)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("predicate", "target", "expected"),
    [
        pytest.param("@score < 3", "n1", ("n1",), id="retained-true"),
        pytest.param("@score < 3", "n2", ("n2",), id="negated-false"),
        pytest.param("position()=1", "n2", ("n2",), id="selection-position-context"),
        pytest.param("count(child::*)", "n1", ("n1", "n2"), id="numeric-boolean-conversion"),
        pytest.param("@missing", "n1", ("n1", "n2"), id="empty-node-set"),
    ],
)
def test_xpath_anchored_retains_target(
    anchored: ModuleType, predicate: str, target: str, expected: tuple[str, ...]
) -> None:
    verdict: Final = next(anchored.compare(anchored.Case(_XML, target, predicate)))
    assert (verdict.ours, verdict.reference) == (("nodes", expected), ("nodes", expected))


@pytest.mark.oracle
def test_xpath_anchored_compares_values_and_types(anchored: ModuleType) -> None:
    rows: Final = list(anchored.compare(anchored.Case(_XML, "n1", "@score < 3")))
    assert [(row.ours, row.reference) for row in rows] == [
        (("nodes", ("n1",)), ("nodes", ("n1",))),
        (("nodes", ("n1",)), ("nodes", ("n1",))),
        (("nodes", ("alpha",)), ("nodes", ("alpha",))),
        (("number", 1.0), ("number", 1.0)),
        (("boolean", True), ("boolean", True)),
        (("string", "alpha"), ("string", "alpha")),
    ]


@pytest.mark.oracle
def test_xpath_anchored_reports_evaluation_rejection(anchored: ModuleType) -> None:
    assert [(row.ours, row.reference) for row in anchored.compare(anchored.Case(_XML, "n1", "unknown()"))] == [
        (("error", "evaluation"), ("error", "evaluation"))
    ] * 6


@pytest.mark.oracle
def test_xpath_anchored_control_changes_actual_boolean_answer(anchored: ModuleType) -> None:
    rows: Final = list(anchored.compare(anchored.Case(_XML, "n1", "@score < 3"), negative_control=True))
    assert [(row.ours, row.reference) for row in rows if row.ours != row.reference] == [
        (("boolean", False), ("boolean", True))
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    "seed",
    [
        pytest.param(0, id="first"),
        pytest.param(1, id="second"),
        pytest.param(7, id="root-target"),
        pytest.param(42, id="later-seed"),
    ],
)
def test_xpath_anchored_generation_is_deterministic_and_bounded(anchored: ModuleType, seed: int) -> None:
    case: Final = anchored.generate(seed)
    etree: Final = pytest.importorskip("lxml.etree")
    elements: Final = list(etree.fromstring(case.xml.encode()).iter())
    identifiers: Final = [element.get("id") for element in elements]
    assert (
        case == anchored.generate(seed),
        case != anchored.generate(seed + 1),
        case.seed,
        8 <= len(elements) <= 16,
        len(set(identifiers)),
        case.target in identifiers,
    ) == (
        True,
        True,
        seed,
        True,
        len(elements),
        True,
    )


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("control", "expected_status", "findings"),
    [pytest.param(False, 0, 0, id="agreement"), pytest.param(True, 1, 2, id="actual-engine-control")],
)
def test_xpath_anchored_cli_summary(
    anchored: ModuleType, capsys: pytest.CaptureFixture[str], *, control: bool, expected_status: int, findings: int
) -> None:
    arguments: Final = ["--seed", "0", "--cases", "2"] + (["--negative-control"] if control else [])
    status: Final = anchored.main(arguments)
    assert (status, json.loads(capsys.readouterr().out.splitlines()[-1])) == (
        expected_status,
        {"summary": {"cases": 2, "rows": 12, "findings": findings}},
    )


@pytest.mark.oracle
@pytest.mark.parametrize("count", [pytest.param("0", id="empty"), pytest.param("257", id="over-limit")])
def test_xpath_anchored_cli_rejects_unbounded_batches(anchored: ModuleType, count: str) -> None:
    with pytest.raises(SystemExit) as error:
        anchored.main(["--cases", count])
    assert error.value.code == 2


@pytest.mark.oracle
@pytest.mark.usefixtures("anchored")
def test_xpath_anchored_script_entry(mocker: MockerFixture, capsys: pytest.CaptureFixture[str]) -> None:
    mocker.patch("sys.argv", ["xpath_anchored.py", "--cases", "1"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(Path(__file__).parents[1] / "tools" / "fuzz" / "xpath_anchored.py"), run_name="__main__")
    assert (error.value.code, json.loads(capsys.readouterr().out.splitlines()[-1])) == (
        0,
        {"summary": {"cases": 1, "rows": 6, "findings": 0}},
    )


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "data", "label", "decoded"),
    [
        pytest.param("utf-8-bom\ncafé", b"\xef\xbb\xbf<p>caf\xc3\xa9</p>", "UTF-8-SIG", "<p>café</p>", id="utf8-bom"),
        pytest.param(
            "utf-16le-bom\ncafé",
            b"\xff\xfe" + "<p>café</p>".encode("utf-16-le"),
            "UTF-16LE",
            "\ufeff<p>café</p>",
            id="utf16le-bom",
        ),
        pytest.param(
            "utf-16be-bom\ncafé",
            b"\xfe\xff" + "<p>café</p>".encode("utf-16-be"),
            "UTF-16BE",
            "\ufeff<p>café</p>",
            id="utf16be-bom",
        ),
        pytest.param(
            "utf-8-bom-conflicting-meta\ncafé",
            b"\xef\xbb\xbf<meta charset=windows-1252><p>caf\xc3\xa9</p>",
            "UTF-8-SIG",
            "<meta charset=windows-1252><p>café</p>",
            id="utf8-bom-conflicting-meta",
        ),
        pytest.param(
            "utf-16le-bom-conflicting-meta\ncafé",
            b"\xff\xfe" + "<meta charset=utf-8><p>café</p>".encode("utf-16-le"),
            "UTF-16LE",
            "\ufeff<meta charset=utf-8><p>café</p>",
            id="utf16le-bom-conflicting-meta",
        ),
        pytest.param(
            "utf-16be-bom-conflicting-meta\ncafé",
            b"\xfe\xff" + "<meta charset=utf-8><p>café</p>".encode("utf-16-be"),
            "UTF-16BE",
            "\ufeff<meta charset=utf-8><p>café</p>",
            id="utf16be-bom-conflicting-meta",
        ),
        pytest.param(
            "utf-8-pragma-first\ncafé",
            b'<meta http-equiv=Content-Type content="text/html; charset=utf-8"><p>caf\xc3\xa9</p>',
            "UTF-8",
            '<meta http-equiv=Content-Type content="text/html; charset=utf-8"><p>café</p>',
            id="utf-8-pragma-first",
        ),
        pytest.param(
            "utf-8-content-first\ncafé",
            b'<meta content="text/html; charset=utf-8" http-equiv=Content-Type><p>caf\xc3\xa9</p>',
            "UTF-8",
            '<meta content="text/html; charset=utf-8" http-equiv=Content-Type><p>café</p>',
            id="utf-8-content-first",
        ),
        pytest.param(
            "windows-1252-pragma-first\ncafé",
            b'<meta http-equiv=Content-Type content="text/html; charset=windows-1252"><p>caf\xe9</p>',
            "windows-1252",
            '<meta http-equiv=Content-Type content="text/html; charset=windows-1252"><p>café</p>',
            id="windows-1252-pragma-first",
        ),
        pytest.param(
            "windows-1252-content-first\ncafé",
            b'<meta content="text/html; charset=windows-1252" http-equiv=Content-Type><p>caf\xe9</p>',
            "windows-1252",
            '<meta content="text/html; charset=windows-1252" http-equiv=Content-Type><p>café</p>',
            id="windows-1252-content-first",
        ),
        pytest.param(
            "utf-8-meta\ncafé",
            b"<meta charset=utf-8><p>caf\xc3\xa9</p>",
            "UTF-8",
            "<meta charset=utf-8><p>café</p>",
            id="utf8-meta",
        ),
        pytest.param(
            "windows-1252-meta\ncafé",
            b"<meta charset=windows-1252><p>caf\xe9</p>",
            "windows-1252",
            "<meta charset=windows-1252><p>café</p>",
            id="windows1252-meta",
        ),
    ],
)
def test_encoding_invariants_pin_public_outputs(case: str, data: bytes, label: str, decoded: str) -> None:
    match: Final = detect(data)
    assert match.codec is not None
    assert (
        match.encoding,
        data.decode(match.codec),
        parse(data).text,
        encoding_stream_check(case),
        encoding_decode_check(case),
    ) == (label, decoded, "café", None, None)


@pytest.mark.parametrize(
    "check",
    [
        pytest.param(encoding_stream_check, id="stream"),
        pytest.param(encoding_decode_check, id="decode"),
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("utf-8-bom", id="missing-separator"),
        pytest.param("unknown\ntext", id="unknown-fixture"),
        pytest.param("empty\ntext", id="nonempty-sentinel"),
        pytest.param("utf-8-meta\n" + "a" * 257, id="bounded-size"),
        pytest.param("utf-8-meta\n\x00", id="html-nul-preprocessing"),
        pytest.param("utf-8-meta\n<p>", id="markup"),
        pytest.param("utf-8-meta\n&copy;", id="entity"),
        pytest.param("windows-1252-meta\n𐐀", id="unrepresentable"),
    ],
)
def test_encoding_invariants_reject_unsupported_cases(check: Callable[[str], str | None], case: str) -> None:
    with pytest.raises(OutOfScopeError):
        check(case)


def test_encoding_stream_empty_sentinel() -> None:
    detector: Final = EncodingDetector()
    detector.feed(b"")
    assert (detect(b""), detector.close(), encoding_stream_check("empty\n")) == (
        EncodingMatch(None, 0.0, None),
        EncodingMatch(None, 0.0, None),
        None,
    )


def test_encoding_decode_empty_sentinel_is_out_of_scope() -> None:
    with pytest.raises(OutOfScopeError):
        encoding_decode_check("empty\n")


@pytest.mark.parametrize(
    ("check", "expected"),
    [
        pytest.param(
            lambda case: encoding_stream_check(case, one_shot=lambda _data: EncodingMatch(None, 0.0, None)),
            "one-shot detection differs from expected match",
            id="wrong-one-shot",
        ),
        pytest.param(
            lambda case: encoding_stream_check(case, stream=lambda _data, _width: EncodingMatch(None, 0.0, None)),
            "chunked detection differs from expected match",
            id="wrong-stream",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, detect_bytes=lambda _data: EncodingMatch(None, 0.0, None)),
            "detection differs from expected match",
            id="wrong-detection",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, decode=lambda _data, _codec: ""),
            "detected codec differs from expected text",
            id="wrong-codec",
        ),
        pytest.param(
            lambda case: encoding_decode_check(case, parse_text=lambda _data: None),
            "byte parse differs from expected text",
            id="missing-parse-text",
        ),
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("utf-8-bom-conflicting-meta\ncafé", id="utf8-conflict"),
        pytest.param("utf-16le-bom-conflicting-meta\ncafé", id="utf16le-conflict"),
        pytest.param("utf-16be-bom-conflicting-meta\ncafé", id="utf16be-conflict"),
        pytest.param("utf-8-meta\ncafé", id="utf-8-meta"),
        pytest.param("utf-8-pragma-first\ncafé", id="utf-8-pragma-first"),
        pytest.param("utf-8-content-first\ncafé", id="utf-8-content-first"),
        pytest.param("windows-1252-pragma-first\ncafé", id="windows-1252-pragma-first"),
        pytest.param("windows-1252-content-first\ncafé", id="windows-1252-content-first"),
    ],
)
def test_encoding_invariants_discriminate_failures(
    case: str, check: Callable[[str], str | None], expected: str
) -> None:
    assert check(case) == expected


@pytest.mark.parametrize(
    "name", [pytest.param("encoding-stream", id="stream"), pytest.param("encoding-decode", id="decode")]
)
def test_encoding_registry_controls_seeds_and_floor(name: str) -> None:
    oracle: Final = ORACLES[name]
    assert (
        oracle.check("utf-8-meta\ncafé"),
        all(oracle.controls().values()),
        oracle.floor.count,
        len(oracle.seeds()),
    ) == (None, True, 100, 169)


def test_encoding_cli_replays_seed_floor(tmp_path: Path) -> None:
    assert (
        fuzz_encoding_invariants_main([
            "--oracle",
            "encoding-stream",
            "--oracle",
            "encoding-decode",
            "--minutes",
            "0",
            "--crash-dir",
            str(tmp_path),
        ])
        == 0
    )


def test_encoding_cli_emits_wrong_detection(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["encoding-stream"],
        check=partial(encoding_stream_check, one_shot=lambda _data: EncodingMatch(None, 0.0, None)),
        seeds=lambda: ["utf-8-meta\ncafé"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"encoding-stream": oracle}, clear=True)
    status: Final = fuzz_encoding_invariants_main([
        "--oracle",
        "encoding-stream",
        "--minutes",
        "0",
        "--crash-dir",
        str(tmp_path),
    ])
    assert (status, [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("crash-*.json")]) == (
        1,
        [
            {
                "oracle": "encoding-stream",
                "detail": "one-shot detection differs from expected match",
                "hits": 1,
                "rng_seed": 0,
            }
        ],
    )


if TYPE_CHECKING:
    from types import ModuleType

_RNG: Final = "http://relaxng.org/ns/structure/1.0"


@pytest.fixture
def fuzz_rng_labels_engines() -> tuple[ModuleType, ModuleType]:
    load: Final = pytest.importorskip
    return load("fuzz.rng_labels", exc_type=ImportError), load("lxml.etree", exc_type=ImportError)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            f'<correct><element xmlns="{_RNG}" name="root"><text/></element></correct>'
            "<valid><root>yes</root></valid><invalid><other/></invalid>",
            [
                ("turbohtml", "compilation", True, True),
                ("libxml2", "compilation", True, True),
                ("turbohtml", "validation", True, True),
                ("libxml2", "validation", True, True),
                ("turbohtml", "validation", False, False),
                ("libxml2", "validation", False, False),
            ],
            id="both-document-labels",
        ),
        pytest.param(
            f'<incorrect><grammar xmlns="{_RNG}"/></incorrect>',
            [("turbohtml", "compilation", False, False), ("libxml2", "compilation", False, False)],
            id="incorrect-schema",
        ),
        pytest.param(
            f'<incorrect><element xmlns="{_RNG}" name="foo"><externalRef/></element></incorrect>',
            [("turbohtml", "compilation", False, False), ("libxml2", "compilation", False, False)],
            id="missing-external-href",
        ),
        pytest.param(
            f'<incorrect><grammar xmlns="{_RNG}"><include/><start><element name="foo">'
            "<empty/></element></start></grammar></incorrect>",
            [("turbohtml", "compilation", False, False), ("libxml2", "compilation", False, False)],
            id="missing-include-href",
        ),
        pytest.param(
            f'<correct><grammar xmlns="{_RNG}"/></correct><valid><root/></valid>',
            [
                ("turbohtml", "compilation", True, False),
                ("libxml2", "compilation", True, False),
                ("turbohtml", "validation", True, None),
                ("libxml2", "validation", True, None),
            ],
            id="unavailable-document-verdicts",
        ),
    ],
)
def test_rng_labels_verdicts(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType],
    case: str,
    expected: list[tuple[str, str, bool, bool | None]],
) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    assert [
        (row.engine, row.phase, row.expected, row.actual)
        for row in rng_labels.compare(etree.fromstring(f"<testSuite><testCase>{case}</testCase></testSuite>"))
    ] == expected


@pytest.mark.oracle
@pytest.mark.parametrize("name", ["dir", "resource", "testCase", "testSuite"])
def test_rng_labels_instance_names_are_not_resource_metadata(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType], name: str
) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    suite: Final = etree.fromstring(
        f'<testSuite><testCase><correct><element xmlns="{_RNG}" name="{name}"><empty/></element></correct>'
        f"<valid><{name}/></valid></testCase></testSuite>"
    )
    assert [(row.phase, row.actual) for row in rng_labels.compare(suite)] == [
        ("compilation", True),
        ("compilation", True),
        ("validation", True),
        ("validation", True),
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            f'<dir name="sub"><resource name="x"><element xmlns="{_RNG}" name="foo"><empty/></element></resource>'
            f'</dir><correct><externalRef xmlns="{_RNG}" xml:base="sub/y" href="x"/></correct>'
            "<valid><foo/></valid><invalid><bar/></invalid>",
            [True, True, True, True, False, False],
            id="base-uri-hierarchy",
        ),
        pytest.param(
            f'<resource name="x"><grammar xmlns="{_RNG}"><start><element name="foo"><empty/></element></start>'
            f'</grammar></resource><correct><grammar xmlns="{_RNG}"><include href="x"/></grammar></correct>'
            "<valid><foo/></valid>",
            [True, True, True, True],
            id="include",
        ),
        pytest.param(
            f'<resource name="x">not a schema</resource><incorrect><externalRef xmlns="{_RNG}" href="x"/></incorrect>',
            [False, False],
            id="text-resource",
        ),
        pytest.param(f'<incorrect><externalRef xmlns="{_RNG}" href=""/></incorrect>', [False, False], id="empty-href"),
    ],
)
def test_rng_labels_resource_cases(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType], case: str, expected: list[bool]
) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    suite: Final = etree.fromstring(f"<testSuite><testCase>{case}</testCase></testSuite>")
    assert [row.actual for row in rng_labels.compare(suite)] == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("..", id="parent"),
        pytest.param("sub/x", id="nested"),
    ],
)
def test_rng_labels_rejects_resource_name_outside_case(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType], name: str
) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    suite: Final = etree.fromstring(
        f'<testSuite><testCase><resource name="{name}"/><correct><empty xmlns="{_RNG}"/></correct>'
        "</testCase></testSuite>"
    )
    with pytest.raises(ValueError, match="one path component"):
        list(rng_labels.compare(suite))


@pytest.mark.oracle
def test_rng_labels_preserves_inherited_qname_prefix(fuzz_rng_labels_engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    suite: Final = etree.fromstring(
        f'<testSuite xmlns:q="urn:example"><testCase><correct><element xmlns="{_RNG}" name="q:root">'
        "<empty/></element></correct><valid><q:root/></valid><invalid><root/></invalid></testCase></testSuite>"
    )
    assert [row.actual for row in rng_labels.compare(suite)] == [True, True, True, True, False, False]


@pytest.mark.oracle
def test_rng_labels_internal_entities(fuzz_rng_labels_engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    suite: Final = etree.fromstring(
        (
            '<!DOCTYPE testSuite [<!ENTITY word "expected">]><testSuite><testCase><correct>'
            f'<element xmlns="{_RNG}" name="root"><value>&word;</value></element></correct>'
            "<valid><root>&word;</root></valid><invalid><root>different</root></invalid></testCase></testSuite>"
        ).encode(),
        etree.XMLParser(resolve_entities="internal", no_network=True, load_dtd=False),
    )
    assert [row.actual for row in rng_labels.compare(suite)] == [True, True, True, True, False, False]


@pytest.mark.oracle
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("<correct/><incorrect/>", id="two-labels"),
        pytest.param("<valid><root/></valid>", id="no-label"),
        pytest.param("<correct><root/><other/></correct>", id="multiple-roots"),
        pytest.param('<correct dtd="value"><root/></correct>', id="dtd"),
        pytest.param("<correct>outside<root/></correct>", id="leading-text"),
        pytest.param("<correct><root/>outside</correct>", id="trailing-text"),
    ],
)
def test_rng_labels_rejects_unsupported_extraction(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType], case: str
) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    with pytest.raises(ValueError, match=r"testCase|single-root|outside"):
        list(rng_labels.compare(etree.fromstring(f"<testSuite><testCase>{case}</testCase></testSuite>")))


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            f'<incorrect><grammar xmlns="{_RNG}"/></incorrect>',
            (0, {"compilation": 2}),
            id="agreement",
        ),
        pytest.param(
            f'<incorrect><element xmlns="{_RNG}" name="root"><empty/></element></incorrect>',
            (1, {"findings": 2}),
            id="both-engines-share-wrong-label",
        ),
        pytest.param(
            f'<correct><grammar xmlns="{_RNG}"/></correct><valid><root/></valid>',
            (1, {"findings": 2, "unavailable": 2}),
            id="compilation-rejection",
        ),
        pytest.param("", (2, {}), id="empty-corpus"),
    ],
)
def test_rng_labels_cli(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected: tuple[int, dict[str, int]],
) -> None:
    corpus: Final = tmp_path / "suite.xml"
    corpus.write_text(f"<testSuite><testCase>{case}</testCase></testSuite>" if case else "<testSuite/>")
    assert (
        fuzz_rng_labels_engines[0].main(["--corpus", str(corpus)]),
        json.loads(capsys.readouterr().out.splitlines()[-1]),
    ) == (expected[0], {"summary": expected[1]})


@pytest.mark.oracle
def test_rng_labels_cli_preparation_error_keeps_source_private(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus: Final = tmp_path / "suite.xml"
    corpus.write_text("<testSuite>private-marker")
    assert (fuzz_rng_labels_engines[0].main(["--corpus", str(corpus)]), capsys.readouterr()) == (
        2,
        ("", "Corpus preparation failed\n"),
    )


@pytest.mark.oracle
def test_rng_labels_preserves_prefix_used_only_in_instance_text(
    fuzz_rng_labels_engines: tuple[ModuleType, ModuleType],
) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    suite: Final = etree.fromstring(
        f'<testSuite xmlns:e2="urn:second"><testCase><correct><element xmlns="{_RNG}" name="root">'
        '<value type="QName" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
        "e2:xyzzy</value></element></correct><valid><root>e2:xyzzy</root></valid>"
        "<invalid><root>xyzzy</root></invalid></testCase></testSuite>"
    )
    assert [(row.phase, row.actual) for row in rng_labels.compare(suite) if row.engine == "libxml2"] == [
        ("compilation", True),
        ("validation", True),
        ("validation", False),
    ]


@pytest.mark.oracle
def test_rng_labels_hash_tracks_resources(fuzz_rng_labels_engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    rows: Final = [
        next(
            iter(
                rng_labels.compare(
                    etree.fromstring(
                        f'<testSuite><testCase><resource name="{name}"/><incorrect><grammar xmlns="{_RNG}"/>'
                        "</incorrect></testCase></testSuite>"
                    )
                )
            )
        )
        for name in ("first", "first", "second")
    ]
    assert (rows[0] == rows[1], rows[0].sha256 != rows[2].sha256) == (True, True)


@pytest.mark.oracle
def test_rng_labels_nested_suites(fuzz_rng_labels_engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = fuzz_rng_labels_engines
    suite: Final = etree.fromstring(
        f"<testSuite><documentation>ignored</documentation><testSuite><testCase><incorrect>"
        f'<grammar xmlns="{_RNG}"/></incorrect></testCase></testSuite></testSuite>'
    )
    assert [(row.case_id, row.phase, row.actual) for row in rng_labels.compare(suite)] == [
        ("001", "compilation", False),
        ("001", "compilation", False),
    ]


if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "HTTP://Example.COM:80/a/../b?utm_source=x&z=2&a=1", "http://example.com/b?a=1&z=2", id="canonical"
        ),
        pytest.param("https://bücher.example/café", "https://xn--bcher-kva.example/caf%C3%A9", id="unicode"),
    ],
)
def test_normalize_url_invariant_pins_output(source: str, expected: str) -> None:
    assert (normalize_url(source), normalize_url(expected), normalize_url_check(source)) == (expected, expected, None)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("HTTP://Example.COM:80/?utm_source=x", "http://example.com/", id="canonical"),
        pytest.param(" http://test.org/?a=1&amp;b=2 ", "http://test.org/?a=1&b=2", id="markup-damage"),
    ],
)
def test_clean_url_invariant_pins_output(source: str, expected: str) -> None:
    assert (clean_url(source), clean_url(expected), clean_url_check(source)) == (expected, expected, None)


@pytest.mark.parametrize(
    ("check", "source"),
    [
        pytest.param(normalize_url_check, "http://[", id="malformed-normalization"),
        pytest.param(clean_url_check, "javascript:alert(1)", id="unsupported-cleaning"),
        pytest.param(clean_url_check, "http://[", id="malformed-cleaning"),
    ],
)
def test_url_invariants_count_initial_rejection_as_out_of_scope(
    check: Callable[[str], str | None], source: str
) -> None:
    with pytest.raises(OutOfScopeError):
        check(source)


@pytest.mark.parametrize(
    ("transform", "expected"),
    [
        pytest.param(lambda text: text + "a", "not a fixpoint: end vs letter", id="changed-output"),
        pytest.param(lambda text: "ok" if text == "raw" else None, "rejects its own output", id="none-after-success"),
        pytest.param(
            lambda text: _fuzz_url_invariants_reject() if text == "ok" else "ok",
            "rejects its own output",
            id="error-after-success",
        ),
        pytest.param(lambda _text: _fuzz_url_invariants_reject(), None, id="error-on-input"),
    ],
)
def test_clean_url_invariant_discriminates_failures(
    transform: Callable[[str], str | None], expected: str | None
) -> None:
    if expected is None:
        with pytest.raises(OutOfScopeError):
            clean_url_check("raw", transform)
    else:
        assert clean_url_check("raw", transform) == expected


def _fuzz_url_invariants_reject() -> str:
    msg = "rejected"
    raise ValueError(msg)


@pytest.mark.parametrize("name", ["normalize-url-fixpoint", "clean-url-fixpoint"])
def test_url_registry_controls_and_floor(name: str) -> None:
    oracle = ORACLES[name]
    assert (oracle.check("HTTP://Example.COM:80/"), all(oracle.controls().values()), oracle.floor.count) == (
        None,
        True,
        100,
    )


def test_url_registry_seeds_use_wpt_inputs(mocker: MockerFixture, tmp_path: Path) -> None:
    wpt = tmp_path / "tools/fuzz-data/wpt/url/resources/urltestdata.json"
    wpt.parent.mkdir(parents=True)
    wpt.write_text(
        json.dumps(["description", {"input": "HTTP://Example.COM:80/", "href": "ignored"}]), encoding="utf-8"
    )
    corpus = tmp_path / "tools/fuzz/corpus/url"
    corpus.mkdir(parents=True)
    (corpus / "url.txt").write_text("https://seed.example/", encoding="utf-8")
    mocker.patch("fuzz.round_trip_oracles._ROOT", tmp_path)
    seeds = ORACLES["normalize-url-fixpoint"].seeds()
    assert (seeds[:2], len(seeds) > 100, "ignored" in seeds) == (
        ["HTTP://Example.COM:80/", "https://seed.example/"],
        True,
        False,
    )


def test_url_cli_emits_successful_cleaning_rejection(mocker: MockerFixture, tmp_path: Path) -> None:
    source = "HTTP://Example.COM:80/?utm_source=x"
    oracle = replace(
        ORACLES["clean-url-fixpoint"],
        check=partial(clean_url_check, clean=_drop_cleaned_url),
        seeds=lambda: [source],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"clean-url-fixpoint": oracle}, clear=True)
    status = fuzz_url_invariants_main(["--minutes", "0", "--crash-dir", str(tmp_path)])
    findings = list(tmp_path.glob("*.json"))
    sidecar = json.loads(findings[0].read_text(encoding="utf-8"))
    minimized = findings[0].with_suffix("").read_text(encoding="utf-8")
    assert (
        status,
        len(findings),
        sidecar,
        findings[0].stem,
        clean_url_check(minimized, _drop_cleaned_url),
    ) == (
        1,
        1,
        {"oracle": "clean-url-fixpoint", "detail": "rejects its own output", "hits": 1, "rng_seed": 0},
        f"crash-{hashlib.sha256(minimized.encode()).hexdigest()}",
        "rejects its own output",
    )


def _drop_cleaned_url(text: str) -> str | None:
    return None if text == "http://example.com/" else clean_url(text)
