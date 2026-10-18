"""How the super-linear lane expands a shape: each kind, the counter, the byte budget, and the shapes it refuses."""

from __future__ import annotations

import hashlib
import io
import json
import random
import shutil
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, cast

import pytest
from fuzz.amplify import Shape
from fuzz.css_generation_tables import CSS_ROWS
from fuzz.css_structure_generators import (
    css_generate,
    css_grammar,
    css_source_generate,
    css_source_seeds,
)
from fuzz.css_structure_generators import (
    main as fuzz_css_structure_generation_main,
)
from fuzz.encoding_structure_generators import (
    EncodingSeed,
    encoding_check,
    encoding_controls,
    encoding_decoded,
    encoding_expected,
    encoding_generate,
    encoding_grammar,
    encoding_manifest,
    encoding_profile,
    main,
)
from fuzz.fuzz import main as fuzz_main
from fuzz.html_generation_tables import ATTRIBUTE_VALUE_RULES, HTML_TAGS, TAG_ATTRIBUTE_RULES
from fuzz.html_structure_generators import (
    HtmlVocabulary,
    html_document,
    html_generate,
    html_grammar_complete,
)
from fuzz.html_structure_generators import (
    main as fuzz_html_structure_generation_main,
)
from fuzz.markdown_structure_generators import (
    MarkdownProfileError,
    markdown_controls,
    markdown_generate,
    markdown_grammar,
    markdown_html_check,
    markdown_snapshot,
    markdown_source_check,
    markdown_source_seeds,
)
from fuzz.markdown_structure_generators import (
    main as fuzz_markdown_structure_generation_main,
)
from fuzz.reduce import minimize
from fuzz.round_trip_oracles import ORACLES, Floor, Oracle, OutOfScopeError
from fuzz.round_trip_oracles import main as fuzz_reduce_cli_main
from fuzz.structure_generators import (
    BudgetError,
    Generated,
    GenerationBudget,
    Grammar,
    GrammarError,
    Identifier,
    Production,
    ProductionFloorError,
    Reference,
    assert_production_floors,
    compile_grammar,
    generate,
    generation_sweep,
    html_grammar,
    write_corpus,
)
from fuzz.structure_generators import (
    html_generate as fuzz_structure_generation_html_generate,
)
from fuzz.structure_generators import (
    main as fuzz_structure_generation_main,
)
from fuzz.triage import classify
from fuzz.triage import main as fuzz_triage_main
from fuzz.xml_structure_generators import (
    main as fuzz_xml_structure_generation_main,
)
from fuzz.xml_structure_generators import (
    xml_document_check,
    xml_expected,
    xml_generate,
    xml_grammar,
    xml_raw_generate,
    xml_raw_inputs,
    xml_snapshot,
    xml_source_controls,
    xml_source_generate,
    xml_source_seeds,
    xml_structure_check,
)
from tinycss2 import parse_stylesheet, serialize

from turbohtml import Document, Element, Html, HTMLParseError, Node, Text, parse, parse_fragment, parse_xml
from turbohtml.cssom import StyleSheet

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("shape", "size", "expected"),
    [
        pytest.param(Shape("repeat", "ab", head="<", tail=">"), 8, "<ababab>", id="repeat"),
        pytest.param(Shape("repeat", "ab"), 7, "ababab", id="repeat-never-splits-a-piece"),
        pytest.param(Shape("sandwich", "[", middle="x", right="]"), 7, "[[[x]]]", id="sandwich"),
        pytest.param(Shape("sandwich", "(", right="))"), 9, "(((" + "))" * 3, id="sandwich-uneven-sides"),
        pytest.param(Shape("tree", "-", middle="a"), 10, "a-a--a---a", id="tree-interleaves-growing-prefixes"),
        pytest.param(Shape("repeat", "é"), 5, "éé", id="budget-counts-utf8-bytes"),
    ],
)
def test_shape_render(shape: Shape, size: int, expected: str) -> None:
    assert shape.render(size) == expected


@pytest.mark.parametrize(
    ("shape", "size", "expected"),
    [
        pytest.param(Shape("repeat", "<b c{n}>", counter=True), 30, "<b c000000><b c000001>", id="repeat"),
        pytest.param(
            Shape("sandwich", "<i{n}>", right="</i{n}>", counter=True),
            40,
            "<i000000><i000001></i000001></i000000>",
            id="sandwich-closes-in-reverse",
        ),
        pytest.param(Shape("tree", "-", middle="{n};", counter=True), 20, "000000;-000001;", id="tree"),
        pytest.param(Shape("repeat", "{n}"), 9, "{n}{n}{n}", id="placeholder-kept-without-counter"),
    ],
)
def test_shape_render_numbers_each_repetition(shape: Shape, size: int, expected: str) -> None:
    assert shape.render(size) == expected


@pytest.mark.parametrize(
    ("build", "message"),
    [
        pytest.param(lambda: Shape("spiral", "a"), "shape kind 'spiral' is not one of", id="unknown-kind"),
        pytest.param(lambda: Shape("repeat", ""), "non-empty left piece", id="empty-left"),
        pytest.param(lambda: Shape("repeat", "a", right="b"), "repeat shape has no right", id="repeat-right"),
        pytest.param(lambda: Shape("tree", "a", right="b"), "tree shape has no right", id="tree-right"),
        pytest.param(lambda: Shape("repeat", "a", middle="b"), "repeat shape has no middle", id="repeat-middle"),
        pytest.param(lambda: Shape("repeat", "a" * 129), "left exceed 128 bytes", id="long-left"),
        pytest.param(lambda: Shape("repeat", "a", tail="é" * 65), "tail exceed 128 bytes", id="long-tail-bytes"),
    ],
)
def test_shape_rejects(build: Callable[[], Shape], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()


def test_shape_accepts_a_piece_of_exactly_128_bytes() -> None:
    assert Shape("repeat", "a" * 128).render(256) == "a" * 256


_CASES: Final = generation_sweep(encoding_grammar(), budget=GenerationBudget(32, 128))
_NAMES: Final = [production.name for production in encoding_grammar().productions]


@pytest.mark.parametrize("case", _CASES, ids=_NAMES)
def test_encoding_structure_literals(case: Generated) -> None:
    assert encoding_check(case) is None


@pytest.mark.parametrize("case", _CASES, ids=_NAMES)
def test_encoding_structure_materialized_budget(case: Generated) -> None:
    profile: Final = encoding_profile(case)
    document: Final = (
        parse(case.data, detect_encoding=True) if profile.sniff else parse(case.data, encoding=profile.encoding)
    )
    nodes: Final = (document, *document.descendants)
    assert (len(nodes), max(len(tuple(node.ancestors)) for node in nodes)) == (case.nodes, case.depth)


def test_encoding_structure_inventory() -> None:
    assert (
        len([name for name in _NAMES if name.startswith("encoding:codec:")]),
        len([name for name in _NAMES if name.startswith("encoding:meta:")]),
        len([name for name in _NAMES if name.startswith("encoding:bom:")]),
        len(_NAMES),
    ) == (41, 228, 3, 467)


@pytest.mark.parametrize(
    ("name", "encoding", "codec", "text"),
    [
        pytest.param("encoding:codec:utf-8", "UTF-8", "whatwg-utf-8", "é", id="utf8"),
        pytest.param(
            "encoding:codec:x-user-defined", "x-user-defined", "whatwg-x-user-defined", "\uf780", id="private"
        ),
        pytest.param("encoding:meta:x-user-defined", "windows-1252", "whatwg-windows-1252", "€", id="meta-remap"),
        pytest.param("encoding:meta:utf-16le", "UTF-8", "whatwg-utf-8", "é", id="meta-utf16"),
        pytest.param("encoding:bom:utf-16le", "UTF-16LE", "whatwg-utf-16le", "é", id="bom-utf16"),
    ],
)
def test_encoding_structure_forced_specimen(name: str, encoding: str, codec: str, text: str) -> None:
    case: Final = generate(encoding_grammar(), random.Random(0), GenerationBudget(32, 128), force=name)
    profile: Final = encoding_profile(case)
    assert (
        profile.encoding,
        profile.codec,
        [record[2] for record in encoding_expected(case) if record[0] == "text"],
    ) == (encoding, codec, [text, "x"])


def test_encoding_structure_utf16_binding_bytes() -> None:
    case: Final = generate(encoding_grammar(), random.Random(0), 16, force="encoding:codec:utf-16le")
    assert (case.data, case.bindings) == (
        '<div id="x"><p>é</p><a href="#x">x</a></div>'.encode("utf-16-le"),
        (("anchor", "x"),),
    )


def test_encoding_structure_independent_parents() -> None:
    case: Final = generate(encoding_grammar(), random.Random(0), 16, force="encoding:codec:utf-8")
    assert encoding_expected(case) == (
        ("document", "", "", (), -1),
        ("element:html", "html", "", (), 0),
        ("element:html", "head", "", (), 1),
        ("element:html", "body", "", (), 1),
        ("element:html", "div", "", (("id", "x"),), 3),
        ("element:html", "p", "", (), 4),
        ("text", "", "é", (), 5),
        ("element:html", "a", "", (("href", "#x"),), 4),
        ("text", "", "x", (), 7),
    )


def test_encoding_structure_empty_replacement() -> None:
    case: Final = generate(encoding_grammar(), random.Random(0), 4, force="encoding:codec:replacement:empty")
    assert (case.data, case.nodes, case.depth, encoding_decoded(case)) == (b"", 4, 2, "")


def test_encoding_structure_first_decode_loss() -> None:
    case: Final = generate(encoding_grammar(), random.Random(0), 16, force="encoding:codec:utf-8")
    assert encoding_check(replace(case, data=case.data.replace(b"\xc3\xa9", b"x"))) == (
        "decoded bytes differ from production literals"
    )


def test_encoding_structure_failure_controls() -> None:
    assert encoding_controls() == {
        "missing decoded text": True,
        "changed parsed tree": True,
        "wrong sniffed codec": True,
    }


@pytest.mark.parametrize("seed", range(32))
def test_encoding_structure_random_budget(seed: int) -> None:
    case: Final = encoding_generate(random.Random(seed), 16, steps=32)
    assert (encoding_check(case), case.nodes <= 16, len(case.productions) <= 32) == (None, True, True)


@pytest.mark.parametrize("budget", [GenerationBudget(3, 128), GenerationBudget(32, 0)], ids=["nodes", "steps"])
def test_encoding_structure_insufficient_budget(budget: GenerationBudget) -> None:
    with pytest.raises(BudgetError, match="cannot complete"):
        generate(encoding_grammar(), random.Random(0), budget)


def test_encoding_structure_production_floors() -> None:
    assert_production_floors(_CASES, _NAMES)
    with pytest.raises(ProductionFloorError, match="production floors missed"):
        assert_production_floors((case for case in _CASES if "content:UTF-8:siblings" not in case.productions), _NAMES)


def test_encoding_structure_manifest_context() -> None:
    case: Final = generate(encoding_grammar(), random.Random(0), 16, force="encoding:codec:utf-8")
    assert encoding_manifest(case) == {
        "path": hashlib.sha256(case.data).hexdigest(),
        "encoding": "UTF-8",
        "codec": "whatwg-utf-8",
        "declared": None,
        "sniff": False,
        "bom": "",
        "decoded": '<div id="x"><p>é</p><a href="#x">x</a></div>',
        "productions": ["encoding:codec:utf-8", "content:UTF-8:empty"],
        "origins": [
            "https://encoding.spec.whatwg.org/#utf-8-decoder",
            "https://encoding.spec.whatwg.org#concept-stream-end",
        ],
        "bindings": {"anchor": "x"},
        "nodes": 9,
        "depth": 5,
    }


@pytest.mark.parametrize("count", [0, 1], ids=["forced-sweep", "random"])
def test_encoding_structure_materialized_corpus(tmp_path: Path, count: int) -> None:
    main(["--output", str(tmp_path), "--count", str(count)])
    manifest: Final = cast("list[EncodingSeed]", json.loads((tmp_path / "manifest.json").read_text()))
    cases: Final = (encoding_generate(random.Random(0)),) if count else _CASES
    assert (
        manifest,
        {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.name != "manifest.json"},
    ) == (
        [encoding_manifest(case) for case in cases],
        {hashlib.sha256(case.data).hexdigest(): case.data for case in cases},
    )


@pytest.mark.parametrize(
    "arguments",
    [["--count", "-1"], ["--budget", "0"], ["--steps", "0"]],
    ids=["negative-count", "node-budget", "step-budget"],
)
def test_encoding_structure_cli_errors(tmp_path: Path, arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--output", str(tmp_path), *arguments])
    assert error.value.code == 2


_GRAMMAR: Final = css_grammar()
_SWEEP: Final = generation_sweep(_GRAMMAR, budget=GenerationBudget(512, 128))
_PREFIX: Final = b":root{--x:red;--y:red;--z:red;--u:red;}@keyframes v{from{opacity:0}}"
_BINDINGS: Final = (("anim", "v"), ("cssvara", "x"), ("cssvarb", "y"), ("cssvarc", "z"), ("cssvard", "u"))


@pytest.fixture
def literal_grammar() -> Grammar:
    return css_grammar(())


def test_css_complete_pinned_inventory() -> None:
    assert (
        len(CSS_ROWS),
        sum(row[0] == "css.txt" for row in CSS_ROWS),
        sum(row[0] == "cssproperties.txt" for row in CSS_ROWS),
        sum(row[0] == "blink-css.dict" for row in CSS_ROWS),
        sum(bool(row[9]) for row in CSS_ROWS),
        sum(row[10] for row in CSS_ROWS),
    ) == (6349, 1567, 3294, 1488, 332, 3)


def test_css_complete_production_floor() -> None:
    assert set(assert_production_floors(_SWEEP, (production.name for production in _GRAMMAR.productions))) == {
        production.name for production in _GRAMMAR.productions
    }


def test_css_complete_unknown_production_fails_floor() -> None:
    with pytest.raises(ProductionFloorError, match="floors missed"):
        assert_production_floors(_SWEEP, ("css:disabled",))


@pytest.mark.parametrize(
    ("name", "body", "nodes"),
    [
        pytest.param("css:leaf", b"a{color:red}", 38, id="rule"),
        pytest.param("css:siblings", b"a{color:red}a{color:red}", 46, id="siblings"),
        pytest.param("css:nested:media", b"@media all{a{color:red}}", 43, id="media"),
        pytest.param("css:nested:supports", b"@supports(display:grid){a{color:red}}", 46, id="supports"),
        pytest.param("css:nested:container", b"@container(width>1px){a{color:red}}", 45, id="container"),
        pytest.param("css:nested:layer", b"@layer x{a{color:red}}", 43, id="layer"),
    ],
)
def test_css_complete_literal_shapes(literal_grammar: Grammar, name: str, body: bytes, nodes: int) -> None:
    case: Final = generate(literal_grammar, random.Random(0), GenerationBudget(128, 64), force=name)
    assert (case.data, case.nodes, case.depth, case.bindings) == (_PREFIX + body, nodes, 7, _BINDINGS)


@pytest.mark.parametrize(
    "budget",
    [
        pytest.param(GenerationBudget(37, 64), id="nodes"),
        pytest.param(GenerationBudget(128, 1), id="steps"),
    ],
)
def test_css_complete_rejects_short_root_budget(literal_grammar: Grammar, budget: GenerationBudget) -> None:
    with pytest.raises(BudgetError, match="cannot complete"):
        generate(literal_grammar, random.Random(0), budget)


def test_css_complete_reserves_sibling_steps(literal_grammar: Grammar) -> None:
    with pytest.raises(BudgetError, match="cannot complete"):
        generate(literal_grammar, random.Random(0), GenerationBudget(128, 3), force="css:siblings")


@pytest.mark.parametrize(
    ("name", "body"),
    [
        pytest.param("css:domato:css.txt:107", b"a{color:var(--y)}", id="second-variable"),
        pytest.param("css:domato:cssproperties.txt:1080", b"a{border-image-outset:0 0px}", id="orphan-border"),
        pytest.param("css:domato:cssproperties.txt:1998", b"a{font-weight:0 0}", id="orphan-weight"),
        pytest.param("css:blink:959", b'@charset "UTF-8";', id="leading-charset"),
        pytest.param("css:blink:960", b"@import url(data:,x);", id="leading-import"),
        pytest.param("css:blink:961", b'@namespace svg "http://www.w3.org/2000/svg";', id="leading-namespace"),
    ],
)
def test_css_complete_literal_table_materialization(name: str, body: bytes) -> None:
    case: Final = generate(_GRAMMAR, random.Random(0), GenerationBudget(512, 128), force=name)
    assert case.data == (body + _PREFIX if name.startswith("css:blink:") else _PREFIX + body)


def test_css_complete_custom_properties_bind_to_root() -> None:
    case: Final = generate(_GRAMMAR, random.Random(0), GenerationBudget(512, 128), force="css:domato:css.txt:107")
    rules: Final = StyleSheet(case.data.decode("utf-8")).rules
    assert (rules[0].selector_text, rules[0].style.get("--y"), rules[1].style.get("color")) == (
        ":root",
        "red",
        "var(--y)",
    )


@pytest.mark.parametrize("seed", range(32))
def test_css_complete_random_inputs_respect_both_budgets(seed: int) -> None:
    case: Final = css_generate(random.Random(seed), 64, steps=12)
    assert case.nodes <= 64
    assert len(case.productions) <= 12


def test_css_complete_source_adapter() -> None:
    assert css_source_generate(random.Random(0)) == css_generate(random.Random(0)).data.decode("utf-8")


def test_css_complete_source_seeds_preserve_sweep() -> None:
    assert css_source_seeds() == [case.data.decode("utf-8") for case in _SWEEP]


def test_css_complete_cli_materializes_bytes(tmp_path: Path) -> None:
    case: Final = css_generate(random.Random(0), 64, steps=12)
    assert (
        fuzz_css_structure_generation_main((
            "--output",
            str(tmp_path),
            "--count",
            "1",
            "--budget",
            "64",
            "--steps",
            "12",
        ))
        == 0
    )
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == {
        hashlib.sha256(case.data).hexdigest(): case.data
    }


def test_css_complete_cli_sweeps_productions(tmp_path: Path) -> None:
    assert fuzz_css_structure_generation_main(("--output", str(tmp_path))) == 0
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {case.data for case in _SWEEP}


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        pytest.param("--count", "-1", "count must be nonnegative", id="count"),
        pytest.param("--budget", "0", "budget cannot complete", id="nodes"),
        pytest.param("--steps", "0", "budget cannot complete", id="steps"),
    ],
)
def test_css_complete_cli_rejects_invalid_budgets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str, value: str, message: str
) -> None:
    with pytest.raises(SystemExit, match="2"):
        fuzz_css_structure_generation_main(("--output", str(tmp_path), "--count", "1", option, value))
    assert message in capsys.readouterr().err


def test_css_complete_disabled_alternative_fails_floor() -> None:
    name: Final = "css:domato:css.txt:107"
    with pytest.raises(ProductionFloorError, match=r"css:domato:css\.txt:107"):
        assert_production_floors((case for case in _SWEEP if name not in case.productions), (name,))


_RAW_CASES: Final = generation_sweep(markdown_grammar(), budget=GenerationBudget(64, 256))
_HTML_CASES: Final = generation_sweep(markdown_grammar(html=True), budget=GenerationBudget(64, 256))


@pytest.mark.parametrize("case", _RAW_CASES, ids=[production.name for production in markdown_grammar().productions])
def test_markdown_raw_materialized_budget(case: Generated) -> None:
    assert _materialized_shape(case, html=False) == (case.nodes, case.depth)


@pytest.mark.parametrize("case", _HTML_CASES, ids=[p.name for p in markdown_grammar(html=True).productions])
def test_markdown_html_materialized_budget(case: Generated) -> None:
    assert _materialized_shape(case, html=True) == (case.nodes, case.depth)


@pytest.mark.parametrize("source", markdown_source_seeds())
def test_markdown_raw_first_conversion(source: str) -> None:
    assert markdown_source_check(source) is None


@pytest.mark.parametrize("source", markdown_source_seeds(html=True))
def test_markdown_html_first_conversion(source: str) -> None:
    assert markdown_html_check(source) is None


def test_markdown_reader_reference_literal() -> None:
    assert markdown_snapshot("[x]: /target\n\n[x]\n") == (
        ("root", "", "", (), -1),
        ("paragraph", "p", "", (), 0),
        ("inline", "", "", (), 1),
        ("link", "a", "", (("href", "/target"),), 2),
        ("text", "", "x", (), 3),
    )


def test_markdown_reader_html_block_literal() -> None:
    assert markdown_snapshot('<img src="/image" alt="x">\n') == (
        ("root", "", "", (), -1),
        ("html_block", "", '<img src="/image" alt="x">\n', (), 0),
    )


def test_markdown_reference_binding() -> None:
    case: Final = generate(markdown_grammar(), random.Random(0), GenerationBudget(5, 4), force="markdown:reference")
    assert (case.data, case.bindings) == (b"[x]: /target\n\n[x]\n", (("reference", "x"),))


def test_markdown_html_binding() -> None:
    case: Final = generate(
        markdown_grammar(html=True), random.Random(0), GenerationBudget(6, 3), force="markdown-html:thematic"
    )
    root: Final = parse_fragment(case.data.decode())
    assert [(node.tag, dict(node.attrs)) for node in root.select("#x, a")] == [
        ("div", {"id": "x"}),
        ("a", {"href": "#x"}),
    ]


@pytest.mark.parametrize("html", [False, True], ids=["raw", "html"])
def test_markdown_first_conversion_controls(*, html: bool) -> None:
    assert markdown_controls(html=html) == {
        "lost emphasis": True,
        "changed destination": True,
        "empty conversion": True,
    }


@pytest.mark.parametrize("seed", range(32))
@pytest.mark.parametrize("html", [False, True], ids=["raw", "html"])
def test_markdown_random_budget(seed: int, *, html: bool) -> None:
    case: Final = markdown_generate(random.Random(seed), 30, steps=24, html=html)
    assert (_materialized_shape(case, html=html), case.nodes <= 30, len(case.productions) <= 24) == (
        (case.nodes, case.depth),
        True,
        True,
    )


@pytest.mark.parametrize("html", [False, True], ids=["raw", "html"])
def test_markdown_production_floor(*, html: bool) -> None:
    grammar: Final = markdown_grammar(html=html)
    cases: Final = _HTML_CASES if html else _RAW_CASES
    name: Final = grammar.productions[-1].name
    with pytest.raises(ProductionFloorError, match="production floors missed"):
        assert_production_floors(
            (case for case in cases if name not in case.productions),
            (production.name for production in grammar.productions),
        )


@pytest.mark.parametrize("html", [False, True], ids=["raw", "html"])
def test_markdown_materialized_corpus(tmp_path: Path, *, html: bool) -> None:
    cases: Final = _HTML_CASES if html else _RAW_CASES
    assert fuzz_markdown_structure_generation_main(["--output", str(tmp_path), *(["--html"] if html else [])]) == 0
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {case.data for case in cases}


@pytest.mark.parametrize("html", [False, True], ids=["raw", "html"])
def test_markdown_random_corpus(tmp_path: Path, *, html: bool) -> None:
    assert (
        fuzz_markdown_structure_generation_main([
            "--output",
            str(tmp_path),
            "--count",
            "1",
            "--seed",
            "4",
            *(["--html"] if html else []),
        ])
        == 0
    )
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {markdown_generate(random.Random(4), html=html).data}


@pytest.mark.parametrize("arguments", [["--count", "-1"], ["--budget", "0"], ["--steps", "0"]])
def test_markdown_cli_errors(tmp_path: Path, arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        fuzz_markdown_structure_generation_main(["--output", str(tmp_path), *arguments])
    assert error.value.code == 2


def test_markdown_unsupported_element() -> None:
    with pytest.raises(MarkdownProfileError, match="no declared Markdown meaning"):
        markdown_html_check("<select><option>x</option></select>")


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<!-- hidden --><script>x</script><style>x</style><p>y</p>", id="hidden-elements"),
        pytest.param("<p>a<template>t</template>b</p>", id="template-content"),
        pytest.param(
            "<p>a<title>t</title><datalist>d</datalist><noembed>e</noembed><noframes>f</noframes><rp>(</rp>b</p>",
            id="rendering-hidden-elements",
        ),
        pytest.param("<div> <p><span>a</span><span>b</span></p> </div>", id="adjacent-transparent-text"),
        pytest.param("<p>x</p>y", id="trailing-inline-flow"),
        pytest.param("x<p>y</p>", id="leading-inline-flow"),
        pytest.param("<p></p>", id="empty-paragraph"),
        pytest.param("<div>target<p></p></div>", id="trailing-empty-paragraph"),
        pytest.param('<div idef="#x">target</a></p></vid>', id="repaired-empty-paragraph"),
        pytest.param("<pre>x  y\n</pre>", id="preserved-code-whitespace"),
        pytest.param('<p class="ignored">x</p>', id="nonsemantic-attribute"),
        pytest.param('<p href="/x">y</p>', id="attribute-of-another-element"),
        pytest.param("<p>x<strong></strong>y</p>", id="empty-emphasis"),
        pytest.param("<p>x<em> </em>y</p>", id="whitespace-emphasis"),
        pytest.param("<p>x</p><p></p>", id="empty-paragraph"),
        pytest.param("<p>x</p><ul></ul>", id="empty-list"),
        pytest.param("<p>x</p><table></table>", id="empty-table"),
        pytest.param("<table><tr><td>a</td></tr></table>", id="header-cell-kind"),
        pytest.param("<p><a>x</a></p>", id="placeholder-link"),
        pytest.param('<p><img src="a"></p>', id="image-without-alt"),
        pytest.param("<pre>x</pre>", id="code-without-final-newline"),
        pytest.param("<p>x<br></p>", id="break-ending-block"),
        pytest.param("<p>x <br>y</p>", id="space-before-break"),
        pytest.param("<p>x\n<br>y</p>", id="newline-before-break"),
        pytest.param("<br><ol><li>x</li></ol>", id="break-alone-before-block"),
        pytest.param("<table><br></table>", id="foster-parented-break"),
        pytest.param("<p>x<strong>a<br></strong>y</p>", id="break-ending-emphasis"),
        pytest.param("<p>x<strong><br></strong>y</p>", id="emphasis-around-break"),
        pytest.param("<p>x<em> y </em>z</p>", id="emphasis-edge-spaces"),
        pytest.param('<p><img src="a" title=""></p>', id="empty-title"),
        pytest.param("<pre><b>x</b></pre>", id="markup-in-code-block"),
        pytest.param("<pre><p>x</p></pre>", id="block-in-code-block"),
        pytest.param("<p><code><i>x</i></code></p>", id="markup-in-code-span"),
        pytest.param("x<div></div>y", id="empty-block-splits-paragraph"),
        pytest.param("x</p>y", id="implied-paragraph-splits-paragraph"),
        pytest.param("<blockquote>x</blockquote>", id="quote-implies-paragraph"),
        pytest.param("<blockquote>a<p>b</p></blockquote>", id="quote-text-before-paragraph"),
        pytest.param("<ul><li>x<p>y</p></li></ul>", id="item-text-before-paragraph"),
        pytest.param("<ul><li><p>x</p></li></ul>", id="item-single-paragraph"),
        pytest.param("<pre>a<p></p>b</pre>", id="empty-block-breaks-code-line"),
        pytest.param("<pre><strong><code>\n</code></strong></pre>", id="code-span-markup-in-code-block"),
        pytest.param("<pre><h1>\nx</h1></pre>", id="heading-in-code-block"),
        pytest.param("<p><code><br>x</code></p>", id="code-span-leading-break"),
        pytest.param("<code><p></p></code>", id="code-span-empty-block"),
        pytest.param("<code><hr></code>", id="code-span-rule"),
        pytest.param("<p><code><img></code></p>", id="code-span-image"),
        pytest.param("<table><b>x</b></table>", id="foster-parented-emphasis"),
        pytest.param("<table><em>a</em><tr><td>b</td></tr></table>", id="foster-parented-before-rows"),
        pytest.param("<h3><p>x</p></h3>", id="heading-paragraph"),
        pytest.param("<h2>a<p>b</p>c</h2>", id="heading-text-around-block"),
        pytest.param("<h2><div>a</div><div>b</div></h2>", id="heading-sibling-blocks"),
        pytest.param("<em><p>a</p>b</em>", id="emphasis-around-paragraph"),
        pytest.param("<strong><p></p></strong>", id="emphasis-around-empty-paragraph"),
        pytest.param("<em><hr></em>", id="emphasis-around-rule"),
        pytest.param("<b><ul><li>a</li></ul></b>", id="emphasis-around-list"),
        pytest.param("<i><blockquote><p>a</p></blockquote></i>", id="emphasis-around-quote"),
        pytest.param("<em><h1>a</h1></em>", id="emphasis-around-heading"),
        pytest.param("<h3><em>a<p>b</p></em></h3>", id="heading-emphasis-around-paragraph"),
        pytest.param('<a href="/t">a<p>b</p>c</a>', id="link-around-paragraph"),
        pytest.param('<a href="/t"><hr></a>', id="link-around-rule"),
        pytest.param('<p>x<a href="/t"> y </a>z</p>', id="link-edge-spaces"),
        pytest.param('<p><a href="/t"><br></a></p>', id="link-holding-break"),
        pytest.param('<h3><a href="/t">t<br>a</a></h3>', id="heading-link-break"),
        pytest.param("<table><tr><td>a<p>b</p></td></tr></table>", id="cell-paragraph"),
        pytest.param("<table><tr><td><p></p></td></tr></table>", id="cell-empty-paragraph"),
        pytest.param("<h1><pre><code>x</code></pre></h1>", id="heading-code-block"),
        pytest.param("<strong><p></p></strong> i", id="text-after-emphasis-around-block"),
        pytest.param("<em>d <p>e</p></em>", id="space-before-block-in-emphasis"),
        pytest.param('<i><a href="/t"><br></a></i>', id="emphasis-around-link-holding-break"),
        pytest.param('<p>x<a href="/t"><br></a></p>', id="break-in-empty-link-ends-block"),
        pytest.param('<a href="a\nb">ok</a>', id="link-url-line-break"),
        pytest.param("<span>a</span>  <span>b</span>", id="spaces-between-inline-elements"),
        pytest.param('<img src="a\nb" alt="ok">', id="plain-image-url-line-break"),
        pytest.param('<img src="a\nb" alt="ok" odd"="">', id="image-url-line-break"),
    ],
)
def test_markdown_supported_html_meaning(markup: str) -> None:
    assert markdown_html_check(markup) is None


@pytest.mark.parametrize(
    ("markup", "container"),
    [
        pytest.param("<ul>x<li>y</li></ul>", "ul", id="text-in-list"),
        pytest.param("<ol><p>x</p></ol>", "ol", id="paragraph-in-list"),
        pytest.param("<li>x</li>", "li", id="item-outside-list"),
        pytest.param('<a href="/x"><table><a href="/y">t</a></table></a>', "a", id="link-in-link"),
    ],
)
def test_markdown_content_model_violation(markup: str, container: str) -> None:
    with pytest.raises(MarkdownProfileError, match=f"content model violated at {container}$"):
        markdown_html_check(markup)


@pytest.mark.parametrize("budget", [GenerationBudget(0, 20), GenerationBudget(20, 0)], ids=["nodes", "steps"])
def test_markdown_insufficient_budget(budget: GenerationBudget) -> None:
    with pytest.raises(BudgetError, match="cannot complete"):
        generate(markdown_grammar(), random.Random(0), budget)


@pytest.mark.parametrize("name", ["markdown-source-grammar", "markdown-html-grammar"])
def test_markdown_registry_production_consumers(name: str) -> None:
    oracle: Final = ORACLES[name]
    assert [oracle.check(source) for source in oracle.seeds()] == [None] * oracle.floor.count


@pytest.mark.parametrize("name", ["markdown-source-grammar", "markdown-html-grammar"])
def test_markdown_registry_generated_consumer(name: str) -> None:
    oracle: Final = ORACLES[name]
    assert oracle.check(oracle.generate(random.Random(4))) is None


@pytest.mark.parametrize("name", ["markdown-source-grammar", "markdown-html-grammar"])
def test_markdown_registry_rejects_undeclared_semantics(name: str) -> None:
    with pytest.raises(OutOfScopeError):
        ORACLES[name].check("<select><option>x</option></select>")


def _materialized_shape(case: Generated, *, html: bool) -> tuple[int, int]:
    if html:
        root: Final = parse_fragment(case.data.decode())
        nodes: Final = (root, *root.descendants)
        return (
            len(nodes),
            max(sum(isinstance(ancestor, Element) for ancestor in node.ancestors) for node in nodes),
        )
    records: Final = markdown_snapshot(case.data.decode())
    depths: Final[list[int]] = []
    for record in records:
        depths.append(0 if record[-1] < 0 else depths[record[-1]] + 1)
    return len(records), max(depths)


@pytest.mark.parametrize("space", ["\u00a0", "\u2007", "\u202f"], ids=["nbsp", "figure-space", "narrow-nbsp"])
def test_markdown_unicode_spacing_preserved(space: str) -> None:
    assert markdown_html_check(f"<p>x{space}y</p>") is None


@pytest.mark.parametrize("space", ["\u00a0", "\u2007", "\u202f"], ids=["nbsp", "figure-space", "narrow-nbsp"])
def test_markdown_unicode_spacing_loss_detected(space: str) -> None:
    assert markdown_html_check(f"<p>x{space}y</p>", lambda _node: "x y") == "Markdown conversion changes meaning"


def test_markdown_reader_drops_control_whitespace_at_paragraph_edge() -> None:
    with pytest.raises(MarkdownProfileError, match="Markdown reader discards control whitespace"):
        markdown_html_check("<p>\x0bx</p>")


_FUZZ_XML_STRUCTURE_GENERATION_CASES: Final = generation_sweep(xml_grammar(), budget=GenerationBudget(30, 120))


@pytest.mark.parametrize(
    "case", _FUZZ_XML_STRUCTURE_GENERATION_CASES, ids=[production.name for production in xml_grammar().productions]
)
def test_xml_structure_literals(case: Generated) -> None:
    assert xml_structure_check(case) is None


@pytest.mark.parametrize(
    "case", _FUZZ_XML_STRUCTURE_GENERATION_CASES, ids=[production.name for production in xml_grammar().productions]
)
def test_xml_structure_materialized_budget(case: Generated) -> None:
    root: Final = parse_xml(case.data.decode())
    nodes: Final = (root, *root.descendants)
    assert (len(nodes), max(len(tuple(node.ancestors)) for node in nodes)) == (case.nodes, case.depth)


def test_xml_structure_independent_parent_literals() -> None:
    case: Final = generate(xml_grammar(), random.Random(0), GenerationBudget(4, 6), force="xml:leaf:0")
    assert xml_expected(case) == (
        ("document", "", "", (), -1),
        ("element:html", "Root", "", (("id", "x"),), 0),
        ("element:html", "Leaf", "", (), 1),
        ("element:html", "Ref", "", (("target", "#x"),), 1),
    )


def test_xml_structure_changed_input() -> None:
    case: Final = replace(
        _FUZZ_XML_STRUCTURE_GENERATION_CASES[0],
        data=_FUZZ_XML_STRUCTURE_GENERATION_CASES[0].data.replace(b"Root", b"Wrong"),
    )
    assert xml_structure_check(case) == "parsed XML differs from production literals"


def test_xml_structure_changed_serialization() -> None:
    assert (
        xml_structure_check(
            _FUZZ_XML_STRUCTURE_GENERATION_CASES[0],
            serialize=lambda node: node.serialize(Html(xml=True)).replace('id="x"', 'id="wrong"'),
        )
        == "serialized XML differs from production literals"
    )


@pytest.mark.parametrize("seed", range(64))
def test_xml_structure_random_bounds(seed: int) -> None:
    case: Final = xml_generate(random.Random(seed), 12, steps=60)
    assert (xml_structure_check(case), case.nodes <= 12, len(case.productions) <= 60) == (None, True, True)


@pytest.mark.parametrize("case", xml_raw_inputs(), ids=[case.productions[0] for case in xml_raw_inputs()])
def test_xml_structure_raw_rejection(case: Generated) -> None:
    with pytest.raises((UnicodeError, HTMLParseError, ValueError)):
        parse_xml(case.data.decode("utf-8"))


@pytest.mark.parametrize("raw", [False, True], ids=["valid", "raw"])
def test_xml_structure_materialized_corpus(tmp_path: Path, *, raw: bool) -> None:
    cases: Final = xml_raw_inputs() if raw else _FUZZ_XML_STRUCTURE_GENERATION_CASES
    assert fuzz_xml_structure_generation_main(["--output", str(tmp_path), *(["--raw"] if raw else [])]) == 0
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {case.data for case in cases}


def test_generation_zero_node_forced_path() -> None:
    grammar: Final = compile_grammar(
        (
            Production("root", "root", (b"<x", Reference("attribute", 0), b"/>"), 1, "test"),
            Production("empty", "attribute", (b"",), 0, "test"),
            Production("attribute", "attribute", (b' a="x"',), 0, "test"),
        ),
        "root",
    )
    assert generate(grammar, random.Random(0), GenerationBudget(1, 2), force="attribute").data == b'<x a="x"/>'


def test_generation_zero_node_cycle_terminates() -> None:
    grammar: Final = compile_grammar(
        (
            Production("cycle", "root", (b"x", Reference("root", 0)), 0, "test"),
            Production("leaf", "root", (b"z",), 0, "test"),
        ),
        "root",
    )
    case: Final = generate(grammar, random.Random(0), GenerationBudget(0, 2), force="cycle")
    assert (case.data, case.nodes, case.productions) == (b"xz", 0, ("cycle", "leaf"))


def test_generation_step_reservation_for_siblings() -> None:
    grammar: Final = compile_grammar(
        (
            Production("root", "root", (Reference("child", 0), Reference("child", 0)), 1, "test"),
            Production("cycle", "child", (b"x", Reference("child", 0)), 0, "test"),
            Production("leaf", "child", (b"z",), 0, "test"),
        ),
        "root",
    )
    case: Final = generate(grammar, random.Random(0), GenerationBudget(1, 4), force="cycle")
    assert (case.data, case.nodes, len(case.productions)) == (b"xzz", 1, 4)


@pytest.mark.parametrize("budget", [GenerationBudget(3, 120), GenerationBudget(30, 5)], ids=["nodes", "steps"])
def test_xml_structure_insufficient_budget(budget: GenerationBudget) -> None:
    with pytest.raises(BudgetError, match="cannot complete"):
        generate(xml_grammar(), random.Random(0), budget)


def test_xml_structure_snapshot_literal() -> None:
    assert xml_snapshot(parse_xml("<Root/>")) == (
        ("document", "", "", (), -1),
        ("element:html", "Root", "", (), 0),
    )


def test_xml_document_seed_consumers() -> None:
    assert [xml_document_check(source) for source in xml_source_seeds()] == [None] * len(
        _FUZZ_XML_STRUCTURE_GENERATION_CASES
    )


def test_xml_document_generated_consumer() -> None:
    assert xml_document_check(xml_source_generate(random.Random(4))) is None


def test_xml_document_failure_controls() -> None:
    assert xml_source_controls() == {"dropped attribute": True, "changed parent": True}


def test_xml_document_custom_serializer() -> None:
    assert xml_document_check("<Root/>", lambda node: node.serialize(Html(xml=True))) is None


def test_xml_structure_custom_serializer() -> None:
    assert (
        xml_structure_check(
            _FUZZ_XML_STRUCTURE_GENERATION_CASES[0], serialize=lambda node: node.serialize(Html(xml=True))
        )
        is None
    )


@pytest.mark.parametrize("arguments", [["--count", "-1"], ["--budget", "0"]], ids=["count", "budget"])
def test_xml_structure_cli_errors(tmp_path: Path, arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        fuzz_xml_structure_generation_main(["--output", str(tmp_path), *arguments])
    assert error.value.code == 2


def test_xml_structure_random_corpus(tmp_path: Path) -> None:
    assert fuzz_xml_structure_generation_main(["--output", str(tmp_path), "--count", "1", "--seed", "4"]) == 0
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {xml_generate(random.Random(4)).data}


def test_xml_structure_disabled_production() -> None:
    name: Final = xml_grammar().productions[-1].name
    with pytest.raises(ProductionFloorError, match="production floors missed"):
        assert_production_floors(
            (case for case in _FUZZ_XML_STRUCTURE_GENERATION_CASES if name not in case.productions),
            (production.name for production in xml_grammar().productions),
        )


def test_xml_raw_generate_literal() -> None:
    assert xml_raw_generate(random.Random(0), 4) == Generated(bytes.fromhex("4c09c2"), 0, 0, ("xml:raw:bytes",), ())


def test_xml_raw_generate_empty_budget() -> None:
    assert xml_raw_generate(random.Random(0), 0).data == b""


def test_xml_raw_generate_negative_budget() -> None:
    with pytest.raises(BudgetError, match="byte budget must be nonnegative"):
        xml_raw_generate(random.Random(0), -1)


def test_xml_raw_random_corpus(tmp_path: Path) -> None:
    assert (
        fuzz_xml_structure_generation_main(["--output", str(tmp_path), "--raw", "--count", "1", "--byte-budget", "4"])
        == 0
    )
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {bytes.fromhex("4c09c2")}


def test_xml_structure_identifier_lookup() -> None:
    root: Final = parse_xml(_FUZZ_XML_STRUCTURE_GENERATION_CASES[0].data.decode())
    assert [(node.tag, dict(node.attrs)) for node in root.select("#x")] == [("Root", {"id": "x"})]


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_reduce_html_removes_subtrees_and_attributes() -> None:
    source = '<section><p id="keep" title="noise">x</p><aside>discard</aside></section>'

    def reproduces(text: str) -> bool:
        root = parse_fragment(text)
        return any(node.attrs.get("id") == "keep" and node.text == "x" for node in root.iter_elements())

    assert minimize(source, reproduces, "html") == '<section><p id="keep">x</p></section>'


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("p{color:red;margin:1px}", "p{color:red;}", id="style-rule"),
        pytest.param("@media screen{p{color:red;margin:1px}}", "@media screen{p{color:red;}}", id="media"),
        pytest.param("@font-face{color:red;src:url(x)}", "@font-face{color:red;}", id="at-rule-declarations"),
        pytest.param("color:red;margin:1px", "color:red;", id="inline"),
        pytest.param('p{color:red;content:"a;b"}', "p{color:red;}", id="string-semicolon"),
        pytest.param("@import 'x';p{color:red;margin:1px}", '@import "x";p{color:red;}', id="keep-other-rule"),
        pytest.param("p{broken;}", "p{broken;}", id="invalid-declaration"),
        pytest.param("", "", id="empty"),
    ],
)
def test_reduce_css_removes_declarations(source: str, expected: str) -> None:
    assert minimize(source, lambda candidate: "color:red" in candidate, "css") == expected


@pytest.mark.parametrize("syntax", ["html", "css", "js"])
def test_reduce_zero_budget_does_not_compare(syntax: Literal["html", "css", "js"]) -> None:
    assert minimize("<p>x</p>", lambda _text: pytest.fail("zero budget compared"), syntax, budget=0) == "<p>x</p>"


def test_reduce_budget_stops_predicate_calls() -> None:
    compared: list[str] = []
    assert minimize(
        "p{color:red;margin:1px;padding:2px}", lambda candidate: bool(compared.append(candidate)), "css", 1
    ) == ("p{color:red;margin:1px;padding:2px}")
    assert len(compared) == 1


def test_reduce_keeps_original_finding_class() -> None:
    source = "p{color:red;margin:1px}"

    def finding(text: str) -> str | None:
        return "original" if "color:red" in text and "margin:1px" in text else "different"

    assert minimize(source, lambda candidate: finding(candidate) == "original", "css") == source


def test_reduce_js_protocol_checks_candidates(mocker: MockerFixture) -> None:
    mocker.patch("fuzz.reduce.shutil.which", return_value="node")
    mocker.patch("fuzz.reduce.Path.is_dir", return_value=True)
    replies = io.StringIO(
        json.dumps({"kind": "candidate", "text": "keep();"})
        + "\n"
        + json.dumps({"kind": "done", "text": "keep();"})
        + "\n"
    )
    answers = io.StringIO()
    mocker.patch("fuzz.reduce.subprocess.Popen", autospec=True).return_value.__enter__.return_value = mocker.MagicMock(
        stdin=answers, stdout=replies
    )
    assert minimize("discard();keep();", lambda source: source == "keep();", "js") == "keep();"
    assert answers.getvalue() == '{"text": "discard();keep();", "budget": 600}\ntrue\n'


def test_reduce_js_reports_premature_exit(mocker: MockerFixture) -> None:
    mocker.patch("fuzz.reduce.shutil.which", return_value="node")
    mocker.patch("fuzz.reduce.Path.is_dir", return_value=True)
    mocker.patch("fuzz.reduce.subprocess.Popen", autospec=True).return_value.__enter__.return_value = mocker.MagicMock(
        stdin=io.StringIO(), stdout=io.StringIO(), returncode=3
    )
    with pytest.raises(RuntimeError, match="exited without a result: 3"):
        minimize("keep();", lambda _source: True, "js")


@pytest.mark.parametrize("missing", [pytest.param("node", id="node"), pytest.param("package", id="package")])
def test_reduce_js_reports_missing_backend(mocker: MockerFixture, missing: str) -> None:
    mocker.patch("fuzz.reduce.shutil.which", return_value=None if missing == "node" else "node")
    mocker.patch("fuzz.reduce.Path.is_dir", return_value=False)
    with pytest.raises(FileNotFoundError, match="required for JS reduction"):
        minimize("keep();", lambda _source: True, "js")


@pytest.mark.oracle
@pytest.mark.skipif(
    shutil.which("node") is None
    or not (Path(__file__).parents[1] / "tools/bench/node/node_modules/uglify-js").is_dir(),
    reason="node/npm backend absent",
)
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("discard();keep();", id="statements"),
        pytest.param("function unused(){return 42}keep();", id="function"),
        pytest.param("if (noise) {discard()} keep();", id="conditional"),
        pytest.param("for(let i=0;i<3;i++)discard(i);keep();", id="loop"),
        pytest.param("const unused={name:'text'};keep();", id="object"),
        pytest.param("const unused=[1,2,3];keep();", id="array"),
        pytest.param("try{discard()}catch(e){discard(e)}finally{discard()}keep();", id="try"),
        pytest.param("class Unused{method(){return 4}}keep();", id="class"),
    ],
)
def test_reduce_js_uses_pinned_ast_fork(source: str) -> None:
    assert minimize(source, lambda candidate: "keep()" in candidate, "js", 100) == "keep();"


@pytest.mark.oracle
@pytest.mark.skipif(
    shutil.which("node") is None
    or not (Path(__file__).parents[1] / "tools/bench/node/node_modules/uglify-js").is_dir(),
    reason="node/npm backend absent",
)
def test_reduce_js_keeps_invalid_source() -> None:
    assert minimize("function {", lambda _candidate: True, "js") == "function {"


def test_reduce_acceptance_consumes_last_budget_call() -> None:
    assert minimize("p{color:red;margin:1px}", lambda _candidate: True, "css", 1) == "p{margin:1px;}"


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("source", "syntax", "expected"),
    [
        pytest.param("<div><aside>noise</aside><p>x</p></div>", "html", "<div><p>x</p></div>", id="html-subtree"),
        pytest.param("p{color:red;margin:1px;}", "css", "p{margin:1px;}", id="css-declaration"),
    ],
)
def test_reduce_cli_saves_structural_finding(
    mocker: MockerFixture, tmp_path: Path, source: str, syntax: Literal["html", "css"], expected: str
) -> None:
    def check(text: str) -> str | None:
        if syntax == "html":
            return "retained" if parse_fragment(text).serialize(inner=True) == text and "x" in text else None
        return (
            "retained"
            if text.startswith("p{") and serialize(parse_stylesheet(text)) == text and "margin:1px;" in text
            else None
        )

    mocker.patch.dict(
        ORACLES,
        {
            "probe": Oracle(
                check, lambda _rng: source, lambda: [source], lambda: {"control": True}, Floor(1, 1), syntax=syntax
            )
        },
        clear=True,
    )
    code = fuzz_reduce_cli_main(["--minutes", "0", "--crash-dir", str(tmp_path)])
    artifacts = [path.read_text() for path in tmp_path.glob("crash-*") if path.suffix != ".json"]
    assert (code, artifacts) == (1, [expected])


def test_reduce_cli_preserves_json_fields(mocker: MockerFixture, tmp_path: Path) -> None:
    source = json.dumps({"html": "<div><p>x</p><aside>noise</aside></div>", "css": "p{color:red;margin:1px;}"})

    def check(text: str) -> str | None:
        payload = json.loads(text)
        html, css = payload["html"], payload["css"]
        return (
            "retained"
            if (
                parse_fragment(html).serialize(inner=True) == html
                and "x" in html
                and css.startswith("p{")
                and serialize(parse_stylesheet(css)) == css
                and "margin:1px;" in css
            )
            else None
        )

    mocker.patch.dict(
        ORACLES,
        {
            "probe": Oracle(
                check,
                lambda _rng: source,
                lambda: [source],
                lambda: {"control": True},
                Floor(1, 1),
                fields=("html", "css"),
                syntax="css",
            )
        },
        clear=True,
    )
    code = fuzz_reduce_cli_main(["--minutes", "0", "--crash-dir", str(tmp_path)])
    artifacts = [json.loads(path.read_text()) for path in tmp_path.glob("crash-*") if path.suffix != ".json"]
    assert (code, artifacts) == (1, [{"html": "<div><p>x</p></div>", "css": "p{margin:1px;}"}])


def test_reduce_cli_caps_comparison_calls(mocker: MockerFixture, tmp_path: Path) -> None:
    source = "z" * 2000
    calls = 0

    def check(text: str) -> str | None:
        nonlocal calls
        calls += 1
        return "retained" if text == source else None

    mocker.patch.dict(
        ORACLES,
        {"probe": Oracle(check, lambda _rng: source, lambda: [source], lambda: {"control": True}, Floor(1, 1))},
        clear=True,
    )
    code = fuzz_reduce_cli_main(["--minutes", "0", "--crash-dir", str(tmp_path)])
    artifacts = [path.read_text() for path in tmp_path.glob("crash-*") if path.suffix != ".json"]
    assert (code, artifacts, calls) == (1, [source], 601)


if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("report", "kind"),
    [
        pytest.param("ERROR: libFuzzer: timeout after 10 seconds", "timeout", id="timeout"),
        pytest.param("ERROR: libFuzzer: out-of-memory (malloc(64))", "oom", id="oom"),
        pytest.param("AddressSanitizer: rss limit exhausted", "oom", id="rss-limit"),
        pytest.param("MemoryError", "oom", id="python-memory"),
        pytest.param("TimeoutError", "timeout", id="python-timeout"),
        pytest.param("ERROR: AddressSanitizer: heap-buffer-overflow", "crash", id="crash"),
        pytest.param("Killed", "crash", id="unknown-kill"),
    ],
)
def test_triage_classifies_report(report: str, kind: str) -> None:
    assert classify(report).kind == kind


def test_triage_hashes_five_frames_without_addresses_or_build_paths() -> None:
    first = "\n".join(
        f"    #{index} 0x{index + 10:x} in call{index} /first/src/input.c:{index + 1}" for index in range(6)
    )
    second = "\n".join(
        f"    #{index} 0x{index + 100:x} in call{index} /second/src/input.c:{index + 1}" for index in range(5)
    )
    finding = classify(first)
    assert (finding.frames, finding.digest) == (
        tuple(f"call{index} input.c:{index + 1}" for index in range(5)),
        classify(second + "\n    #5 0x567 in changed elsewhere.c:1").digest,
    )


def test_triage_separates_different_top_frames() -> None:
    assert classify("#0 0x12 in original source.c:1").digest != classify("#0 0x23 in changed source.c:1").digest


def test_triage_ignores_allocation_stack() -> None:
    assert classify("#0 0x12 in fault source.c:1\nallocated by thread:\n#0 0x34 in allocator malloc.c:9").frames == (
        "fault source.c:1",
    )


def test_triage_python_innermost_frames_first() -> None:
    report = "Traceback (most recent call last):\n" + "\n".join(
        f'  File "/checkout/case.py", line {index + 1}, in call{index}' for index in range(7)
    )
    assert classify(report).frames == tuple(f"call{index} case.py:{index + 1}" for index in range(6, 1, -1))


def test_triage_resource_classes_keep_separate_hashes() -> None:
    frames = "\n#0 0x12 in same source.c:1"
    assert len({classify(kind + frames).digest for kind in ("TimeoutError", "MemoryError", "crash")}) == 3


def test_triage_frameless_reports_keep_distinct_messages() -> None:
    assert classify("unexpected result A").digest != classify("unexpected result B").digest


def test_triage_unsymbolized_module_offsets() -> None:
    assert classify("#0 0x12 (/tmp/subject+0x1234)").frames == ("(subject+0x1234)",)


def test_triage_cli_groups_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = [tmp_path / name for name in ("first.log", "second.log", "timeout.log")]
    paths[0].write_text("#0 0x12 in fault /first/input.c:3", encoding="utf-8")
    paths[1].write_text("#0 0x34 in fault /second/input.c:3", encoding="utf-8")
    paths[2].write_text("TimeoutError", encoding="utf-8")
    output = tmp_path / "triage.json"
    assert fuzz_triage_main([*map(str, paths), "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == {
        classify(paths[0].read_text()).digest: {
            "kind": "crash",
            "frames": ["fault input.c:3"],
            "reports": list(map(str, paths[:2])),
        },
        classify("TimeoutError").digest: {"kind": "timeout", "frames": [], "reports": [str(paths[2])]},
    }
    assert capsys.readouterr().out == "triage: 3 reports, 2 groups\n"


@pytest.mark.parametrize(
    "missing", [pytest.param("input", id="missing-input"), pytest.param("output", id="output-parent")]
)
def test_triage_cli_reports_file_errors(tmp_path: Path, missing: str, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source.log"
    if missing == "output":
        source.write_text("error", encoding="utf-8")
    output = tmp_path / "absent" / "triage.json"
    with pytest.raises(SystemExit, match="2"):
        fuzz_triage_main([str(source), "--output", str(output)])
    assert capsys.readouterr().err.startswith("triage: ")


def test_triage_runner_does_not_build_or_replay(tmp_path: Path) -> None:
    source = tmp_path / "report.log"
    source.write_text("MemoryError", encoding="utf-8")
    output = tmp_path / "groups.json"
    assert fuzz_main(["--mode", "triage", "--build", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == {
        classify("MemoryError").digest: {"kind": "oom", "frames": [], "reports": [str(source)]}
    }


def test_triage_runner_rejects_unknown_smoke_arguments() -> None:
    with pytest.raises(SystemExit, match="2"):
        fuzz_main(["--mode", "smoke", "--unknown"])


def test_triage_runner_keeps_release_dispatch(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch("subprocess.run", autospec=True, return_value=mocker.MagicMock(returncode=0))
    assert fuzz_main(["--mode", "release-diff", "--minutes", "0", "--crash-dir", str(tmp_path)]) == 0


_FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR: Final = html_grammar_complete()
_FUZZ_HTML_STRUCTURE_GENERATION_SWEEP: Final = generation_sweep(_FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR)
_FUZZ_HTML_STRUCTURE_GENERATION_CASES: Final = (
    *_FUZZ_HTML_STRUCTURE_GENERATION_SWEEP,
    *(html_generate(random.Random(seed)) for seed in range(64)),
)
_CASE_IDS: Final = [f"production-{index}" for index in range(len(_FUZZ_HTML_STRUCTURE_GENERATION_CASES))]


@pytest.fixture
def root(request: pytest.FixtureRequest) -> Node:
    case: Final = cast("Generated", request.param)
    return parse(case.data.decode("utf-8")) if html_document(case) else parse_fragment(case.data.decode("utf-8"))


@pytest.mark.parametrize(
    ("root", "case"), [(case, case) for case in _FUZZ_HTML_STRUCTURE_GENERATION_CASES], indirect=["root"], ids=_CASE_IDS
)
def test_html_complete_node_accounting(root: Node, case: Generated) -> None:
    assert 1 + sum(1 for _ in root.descendants) == case.nodes


@pytest.mark.parametrize(
    ("root", "case"), [(case, case) for case in _FUZZ_HTML_STRUCTURE_GENERATION_CASES], indirect=["root"], ids=_CASE_IDS
)
def test_html_complete_depth_accounting(root: Node, case: Generated) -> None:
    assert (
        max(
            sum(isinstance(root, Document) or not isinstance(ancestor, Document) for ancestor in node.ancestors)
            for node in (root, *root.descendants)
        )
        == case.depth
    )


@pytest.mark.parametrize("root", _FUZZ_HTML_STRUCTURE_GENERATION_CASES, indirect=True, ids=_CASE_IDS)
def test_html_complete_identifier_target(root: Node) -> None:
    assert [element.tag for element in root.select('[id="x"]')] in [["div"], ["html"], ["frameset"]]


@pytest.mark.parametrize(
    ("line", "attribute", "expected"),
    [
        pytest.param(82, "aria-activedescendant", "x", id="active-descendant"),
        pytest.param(93, "aria-controls", "x", id="controls"),
        pytest.param(95, "aria-describedby", "x", id="description"),
        pytest.param(108, "aria-flowto", "x", id="flow"),
        pytest.param(114, "aria-help", "x", id="help"),
        pytest.param(122, "aria-labeledby", "x", id="legacy-label"),
        pytest.param(124, "aria-labelledby", "x", id="label"),
        pytest.param(141, "aria-owns", "x", id="owns"),
        pytest.param(274, "contextmenu", "x", id="context-menu"),
        pytest.param(372, "for", "x", id="label-for"),
        pytest.param(374, "form", "x", id="form"),
        pytest.param(385, "formtarget", "x", id="form-target"),
        pytest.param(407, "headers", ["x"], id="headers"),
        pytest.param(477, "itemref", "x", id="item-reference"),
        pytest.param(729, "list", "x", id="datalist"),
        pytest.param(771, "menu", "x", id="menu"),
        pytest.param(1011, "select", "#x", id="selector"),
        pytest.param(1069, "target", "x", id="target"),
        pytest.param(1326, "usemap", "#x", id="image-map"),
    ],
)
def test_html_complete_table_identifier_reference(line: int, attribute: str, expected: str | list[str]) -> None:
    case: Final = generate(_FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR, random.Random(0), force=f"html:domato:value:{line}")
    root: Final = parse_fragment(case.data.decode("utf-8"))
    assert (root.select(f"[{attribute}]")[0].attrs[attribute], root.select('[id="x"]')[0].tag) == (expected, "div")


@pytest.mark.parametrize(
    ("line", "attribute", "expected"),
    [
        pytest.param(15, "accept-charset", ["x"], id="charset-delegation"),
        pytest.param(16, "accept-charset", ["UTF-7"], id="charset-alternative"),
        pytest.param(28, "autocomplete", "on", id="onoff-helper"),
        pytest.param(29, "autocomplete", "off", id="onoff-alternative"),
        pytest.param(243, "class", ["a"], id="class-pool"),
        pytest.param(1063, "style", "color: red; color: red; color: red; color: red; color: red", id="css-import"),
    ],
)
def test_html_complete_value_alternatives(line: int, attribute: str, expected: str | list[str]) -> None:
    case: Final = generate(_FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR, random.Random(0), force=f"html:domato:value:{line}")
    assert parse_fragment(case.data.decode("utf-8")).select(f"[{attribute}]")[0].attrs[attribute] == expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        pytest.param("frame", ["html", "head", "frameset", "frame"], id="frame-document"),
        pytest.param("frameset", ["html", "head", "frameset", "frame"], id="frameset-document"),
        pytest.param("html", ["html", "head", "title", "body", "a"], id="html-document"),
        pytest.param("head", ["html", "head", "title", "body", "a"], id="head-document"),
        pytest.param("body", ["html", "head", "title", "body", "a"], id="body-document"),
        pytest.param("plaintext", ["div", "a", "plaintext"], id="plaintext-last"),
    ],
)
def test_html_complete_special_tag_materialization(tag: str, expected: list[str]) -> None:
    name: Final = f"html:domato:tag:{tag}"
    case: Final = generate(
        _FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR,
        random.Random(0),
        force=name if tag == "plaintext" else "html:document:" + name,
    )
    root: Final = parse(case.data.decode("utf-8")) if html_document(case) else parse_fragment(case.data.decode("utf-8"))
    assert [node.tag for node in root.descendants if isinstance(node, Element)] == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param(
            "html:hostile:formatting",
            [
                ("div", "html", 0),
                ("b", "html", 1),
                ("i", "html", 2),
                ("x", "text", 3),
                ("i", "html", 1),
                ("y", "text", 5),
                ("a", "html", 1),
                ("x", "text", 7),
            ],
            id="adoption",
        ),
        pytest.param(
            "html:hostile:near-miss",
            [
                ("div", "html", 0),
                ("span", "html", 1),
                ("x", "text", 2),
                ("b", "html", 2),
                ("y", "text", 4),
                ("a", "html", 1),
                ("x", "text", 6),
            ],
            id="near-miss",
        ),
        *(
            pytest.param(
                f"html:foreign:svg:{tag}",
                [
                    ("div", "html", 0),
                    ("svg", "svg", 1),
                    (tag, "svg", 2),
                    ("p", "html", 3),
                    ("x", "text", 4),
                    ("circle", "svg", 2),
                    ("a", "html", 1),
                    ("x", "text", 7),
                ],
                id=tag,
            )
            for tag in ("foreignObject", "desc", "title")
        ),
    ],
)
def test_html_complete_literal_foreign_and_hostile_shape(name: str, expected: list[tuple[str, str, int]]) -> None:
    case: Final = generate(_FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR, random.Random(0), force=name)
    root: Final = parse_fragment(case.data.decode("utf-8"))
    nodes: Final = [root, *root.descendants]
    assert [
        (node.data, "text", nodes.index(cast("Node", node.parent)))
        if isinstance(node, Text)
        else (
            cast("Element", node).tag,
            cast("Element", node).namespace.name.lower(),
            nodes.index(cast("Node", node.parent)),
        )
        for node in nodes[1:]
    ] == expected


def test_html_complete_all_table_productions_fire() -> None:
    counts: Final = assert_production_floors(
        _FUZZ_HTML_STRUCTURE_GENERATION_SWEEP,
        (production.name for production in _FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR.productions),
    )
    assert (
        sum(name.startswith(("html:domato:tag:", "html:document:html:domato:tag:")) for name in counts),
        sum(name.startswith("html:domato:value:") for name in counts),
        sum("html:domato:tag-attribute:" in name for name in counts),
    ) == (len(HTML_TAGS), len(ATTRIBUTE_VALUE_RULES), len(TAG_ATTRIBUTE_RULES))


def test_html_complete_disabled_production_fails_floor() -> None:
    cases: Final = tuple(
        case for case in _FUZZ_HTML_STRUCTURE_GENERATION_SWEEP if "html:hostile:formatting" not in case.productions
    )
    with pytest.raises(ProductionFloorError, match="html:hostile:formatting"):
        assert_production_floors(
            cases, (production.name for production in _FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR.productions)
        )


def test_html_complete_exports_input_bytes(tmp_path: Path) -> None:
    case: Final = generate(_FUZZ_HTML_STRUCTURE_GENERATION_GRAMMAR, random.Random(0), force="html:hostile:near-miss")
    write_corpus((case, case), tmp_path)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == {
        hashlib.sha256(case.data).hexdigest(): b'<div id="x"><span>x</spna><b>y</b></span><a href="#x">x</a></div>'
    }


@pytest.mark.parametrize(
    ("vocabulary", "message"),
    [
        pytest.param(
            HtmlVocabulary(("span",), (), ((1, "missing", "x"),), ()), "no emitting attribute", id="unused-value"
        ),
        pytest.param(
            HtmlVocabulary(("span",), ((1, "attribute_x", 'x="<missing>"'),), (), ()),
            "unproductive value",
            id="unknown-value",
        ),
        pytest.param(
            HtmlVocabulary(("span",), (), (), ((1, "unknown_attribute", "x"),)),
            "no tag materialization",
            id="unknown-tag",
        ),
        pytest.param(
            HtmlVocabulary(("span",), ((1, "attribute_x", 'x="<import from=missing symbol=x>"'),), (), ()),
            "unsupported grammar import",
            id="unknown-import",
        ),
    ],
)
def test_html_complete_rejects_incomplete_tables(vocabulary: HtmlVocabulary, message: str) -> None:
    with pytest.raises(GrammarError, match=message):
        html_grammar_complete(vocabulary)


def test_html_complete_productive_value_cycle_terminates() -> None:
    grammar: Final = html_grammar_complete(
        HtmlVocabulary(
            ("span",),
            ((1, "attribute_x", 'x="<value>"'),),
            ((1, "value", "<helper>"), (2, "helper", "<value>"), (3, "helper", "done")),
            (),
        )
    )
    case: Final = generate(grammar, random.Random(0), force="html:domato:value:2")
    assert parse_fragment(case.data.decode("utf-8")).select("[x]")[0].attrs["x"] == "done"


def test_html_complete_escapes_attribute_literal() -> None:
    grammar: Final = html_grammar_complete(
        HtmlVocabulary(
            ("span",),
            ((1, "attribute_x", 'x="<value>"'),),
            ((1, "value", "<char min=34>&<lt><gt>"),),
            (),
        )
    )
    case: Final = generate(grammar, random.Random(0), force="html:domato:value:1")
    assert (case.data, parse_fragment(case.data.decode("utf-8")).select("[x]")[0].attrs["x"]) == (
        b'<div id="x"><span x="&quot;&amp;&lt;&gt;">x</span><a href="#x">x</a></div>',
        '"&<>',
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<int>", b"0", id="integer-default"),
        pytest.param("<int min=7 max=9>", b"7", id="integer-minimum"),
        pytest.param("<float min=-1.25 max=2>", b"-1.25", id="float-minimum"),
        pytest.param("<char>", b"x", id="character-default"),
        pytest.param("<string><htmlsafestring><hex>", b"xx0", id="bounded-string"),
        pytest.param("<space><cr><lf>", b" \r\n", id="whitespace"),
    ],
)
def test_html_complete_bounded_primitive_values(source: str, expected: bytes) -> None:
    grammar: Final = html_grammar_complete(
        HtmlVocabulary(
            ("span",),
            ((1, "attribute_x", 'x="<value>"'),),
            ((1, "value", source),),
            (),
        )
    )
    case: Final = generate(grammar, random.Random(0), force="html:domato:value:1")
    assert case.data == b'<div id="x"><span x="' + expected + b'">x</span><a href="#x">x</a></div>'


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        pytest.param(["--count", "0"], "count must be positive", id="zero-count"),
        pytest.param(["--budget", "0"], "budget cannot complete", id="zero-budget"),
    ],
)
def test_html_complete_cli_rejects_invalid_budget(
    arguments: list[str], message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit, match="2"):
        fuzz_html_structure_generation_main(["--output", str(tmp_path), *arguments])
    assert message in capsys.readouterr().err


def test_html_complete_cli_materializes_bytes(tmp_path: Path) -> None:
    fuzz_html_structure_generation_main(["--output", str(tmp_path), "--count", "1", "--budget", "5"])
    assert [path.read_bytes() for path in tmp_path.iterdir()] == [b'<frameset id="x"><frame src="#x"></frameset>']


def test_html_complete_cli_sweep_has_hostile_inputs(tmp_path: Path) -> None:
    fuzz_html_structure_generation_main(["--output", str(tmp_path), "--count", "1", "--sweep"])
    assert b'<div id="x"><b><i>x</b>y</i><a href="#x">x</a></div>' in {path.read_bytes() for path in tmp_path.iterdir()}


@pytest.fixture
def grammar() -> Grammar:
    return compile_grammar(
        (
            Production("root", "root", (b"<div>", Reference("item", 2), Reference("item", 2), b"</div>"), 2, "test", 1),
            Production("leaf", "item", (b"<b>x</b>",), 2, "test", 1),
            Production("nested", "item", (b"<i>", Reference("item"), b"</i>"), 1, "test"),
        ),
        "root",
    )


def test_generation_reserves_siblings(grammar: Grammar) -> None:
    assert generate(grammar, random.Random(0), 6) == Generated(
        b"<div><b>x</b><b>x</b></div>", 6, 3, ("root", "leaf", "leaf"), ()
    )


def test_generation_leaf_uses_minimum(grammar: Grammar) -> None:
    assert generate(grammar, random.Random(1), 100, leaf=True) == Generated(
        b"<div><b>x</b><b>x</b></div>", 6, 3, ("root", "leaf", "leaf"), ()
    )


def test_generation_forces_nested(grammar: Grammar) -> None:
    assert generate(grammar, random.Random(2), 7, force="nested") == Generated(
        b"<div><i><b>x</b></i><b>x</b></div>", 7, 4, ("root", "nested", "leaf", "leaf"), ()
    )


def test_generation_forced_path_uses_minimum() -> None:
    compiled: Final = compile_grammar(
        (
            Production("heavy", "root", (b"<div><span></span><i>", Reference("item", 3), b"</i></div>"), 4, "test", 2),
            Production("small", "root", (b"<div>", Reference("item", 2), b"</div>"), 2, "test", 1),
            Production("leaf", "item", (b"<b>x</b>",), 2, "test", 1),
            Production("nested", "item", (b"<i>", Reference("item"), b"</i>"), 1, "test"),
        ),
        "root",
    )
    assert generate(compiled, random.Random(0), 5, force="nested") == Generated(
        b"<div><i><b>x</b></i></div>", 5, 4, ("small", "nested", "leaf"), ()
    )


@pytest.mark.parametrize(
    ("budget", "forced"), [pytest.param(5, None, id="root"), pytest.param(6, "nested", id="forced-path")]
)
def test_generation_rejects_insufficient_budget(grammar: Grammar, budget: int, forced: str | None) -> None:
    with pytest.raises(BudgetError, match="cannot complete"):
        generate(grammar, random.Random(0), budget, force=forced)


def test_generation_rejects_unknown_production(grammar: Grammar) -> None:
    with pytest.raises(GrammarError, match="unknown production"):
        generate(grammar, random.Random(0), force="missing")


@pytest.mark.parametrize(
    ("productions", "root", "message"),
    [
        pytest.param(
            (Production("same", "root", (b"x",), 1, "test"), Production("same", "root", (b"y",), 1, "test")),
            "root",
            "unique",
            id="duplicate-name",
        ),
        pytest.param((Production("negative", "root", (b"x",), -1, "test"),), "root", "nonnegative", id="negative-cost"),
        pytest.param(
            (Production("negative-height", "root", (b"x",), 1, "test", -1),),
            "root",
            "nonnegative",
            id="negative-height",
        ),
        pytest.param(
            (Production("negative-depth", "root", (Reference("root", -1),), 1, "test"),),
            "root",
            "nonnegative",
            id="negative-reference-depth",
        ),
        pytest.param((Production("root", "root", (b"x",), 1, "test"),), "missing", "unknown root", id="unknown-root"),
        pytest.param(
            (Production("root", "root", (Reference("missing"),), 1, "test"),),
            "root",
            "unknown symbols",
            id="unknown-symbol",
        ),
        pytest.param(
            (Production("root", "root", (b"x",), 1, "test"), Production("unused", "unused", (b"y",), 1, "test")),
            "root",
            "unreachable",
            id="unreachable",
        ),
        pytest.param(
            (Production("root", "root", (Reference("root"),), 1, "test"),),
            "root",
            "nonproductive",
            id="nonproductive-cycle",
        ),
    ],
)
def test_generation_rejects_invalid_grammar(productions: tuple[Production, ...], root: str, message: str) -> None:
    with pytest.raises(GrammarError, match=message):
        compile_grammar(productions, root)


def test_generation_sweep_forces_productions(grammar: Grammar) -> None:
    cases: Final = generation_sweep(grammar, 2)
    assert (len(cases), dict(assert_production_floors(cases, ("root", "leaf", "nested"), 2))) == (
        6,
        {"root": 6, "leaf": 12, "nested": 2},
    )


def test_generation_floor_detects_disabled_production(grammar: Grammar) -> None:
    with pytest.raises(ProductionFloorError, match="nested"):
        assert_production_floors((generate(grammar, random.Random(0), leaf=True),), ("nested",))


def test_generation_floor_rejects_zero(grammar: Grammar) -> None:
    with pytest.raises(ProductionFloorError, match="positive"):
        assert_production_floors((generate(grammar, random.Random(0)),), ("root",), 0)


def test_generation_sweep_rejects_zero(grammar: Grammar) -> None:
    with pytest.raises(ProductionFloorError, match="positive"):
        generation_sweep(grammar, 0)


def test_generation_identifiers_are_unique() -> None:
    compiled: Final = compile_grammar(
        (
            Production(
                "ids",
                "root",
                (
                    b'<div id="',
                    Identifier("first", definition=True),
                    b'"></div><div id="',
                    Identifier("second", definition=True),
                    b'"></div><div id="',
                    Identifier("third", definition=True),
                    b'"></div>',
                ),
                4,
                "test",
                1,
            ),
        ),
        "root",
    )
    case: Final = generate(compiled, random.Random(0))
    assert case == Generated(
        b'<div id="x"></div><div id="y"></div><div id="z"></div>',
        4,
        1,
        ("ids",),
        (("first", "x"), ("second", "y"), ("third", "z")),
    )


def test_generation_reference_before_definition() -> None:
    compiled: Final = compile_grammar(
        (
            Production(
                "before",
                "root",
                (
                    b'<a href="#',
                    Identifier("target", definition=False),
                    b'">x</a><div id="',
                    Identifier("target", definition=True),
                    b'"></div>',
                ),
                4,
                "test",
                2,
            ),
        ),
        "root",
    )
    assert generate(compiled, random.Random(0)) == Generated(
        b'<a href="#x">x</a><div id="x"></div>', 4, 2, ("before",), (("target", "x"),)
    )


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        pytest.param(
            tuple(Identifier(name, definition=True) for name in ("first", "second", "third", "fourth")),
            "exhausted",
            id="pool-exhausted",
        ),
        pytest.param(
            (Identifier("first", definition=True), Identifier("first", definition=True)),
            "duplicate identifier",
            id="duplicate-definition",
        ),
        pytest.param((Identifier("missing", definition=False),), "undefined identifiers", id="undefined-reference"),
    ],
)
def test_generation_rejects_invalid_identifiers(parts: tuple[Identifier, ...], message: str) -> None:
    compiled: Final = compile_grammar((Production("ids", "root", parts, 1, "test"),), "root")
    with pytest.raises(GrammarError, match=message):
        generate(compiled, random.Random(0))


@pytest.mark.parametrize(
    ("budget", "force", "expected"),
    [
        pytest.param(3, None, Generated(b"<div>x</div>", 3, 2, ("root", "leaf"), ()), id="minimum"),
        pytest.param(
            6,
            "cycle",
            Generated(b"<div><i><div>x</div></i></div>", 6, 5, ("root", "cycle", "root", "leaf"), ()),
            id="forced-cycle",
        ),
    ],
)
def test_generation_productive_cycle_terminates(budget: int, force: str | None, expected: Generated) -> None:
    compiled: Final = compile_grammar(
        (
            Production("root", "root", (b"<div>", Reference("item", 2), b"</div>"), 2, "test", 1),
            Production("cycle", "item", (b"<i>", Reference("root"), b"</i>"), 1, "test"),
            Production("leaf", "item", (b"x",), 1, "test"),
        ),
        "root",
    )
    assert generate(compiled, random.Random(0), budget, force=force) == expected


@pytest.fixture
def depth_grammars() -> tuple[Grammar, ...]:
    return tuple(
        compile_grammar(
            (
                Production(
                    "root",
                    "root",
                    (b"<div>" * level, Reference("item", level + 1), b"</div>" * level),
                    level + 1,
                    "test",
                    level,
                ),
                Production("leaf", "item", (b"<b>x</b>",), 2, "test", 1),
                Production("nested", "item", (b"<i>", Reference("item"), b"</i>"), 1, "test"),
            ),
            "root",
        )
        for level in (1, 8)
    )


def test_generation_depth_decay(depth_grammars: tuple[Grammar, ...]) -> None:
    counts: Final = [
        sum(generate(compiled, random.Random(seed), 100).productions.count("nested") for seed in range(128))
        for compiled in depth_grammars
    ]
    assert counts[0] > counts[1]


def test_generation_corpus_deduplicates(grammar: Grammar, tmp_path: Path) -> None:
    case: Final = generate(grammar, random.Random(0), leaf=True)
    assert write_corpus((case, case), tmp_path / "corpus") == 1


def test_generation_corpus_contains_materialized_bytes(grammar: Grammar, tmp_path: Path) -> None:
    case: Final = generate(grammar, random.Random(0), leaf=True)
    write_corpus((case,), tmp_path)
    assert (tmp_path / hashlib.sha256(case.data).hexdigest()).read_bytes() == b"<div><b>x</b><b>x</b></div>"


def test_generation_raw_bytes_survive_export(tmp_path: Path) -> None:
    compiled: Final = compile_grammar((Production("raw", "root", (b"\xff",), 1, "test"),), "root")
    case: Final = generate(compiled, random.Random(0))
    write_corpus((case,), tmp_path)
    assert (tmp_path / hashlib.sha256(b"\xff").hexdigest()).read_bytes() == b"\xff"


_FUZZ_STRUCTURE_GENERATION_HTML_CASES: Final = generation_sweep(html_grammar())
_HTML_INPUTS: Final = (
    *_FUZZ_STRUCTURE_GENERATION_HTML_CASES,
    *(fuzz_structure_generation_html_generate(random.Random(seed)) for seed in range(128)),
)


@pytest.mark.parametrize("case", [pytest.param(case, id=f"case-{index}") for index, case in enumerate(_HTML_INPUTS)])
def test_generation_html_node_accounting(case: Generated) -> None:
    root: Final = parse_fragment(case.data.decode("utf-8"))
    assert sum(1 for _ in root.descendants) + 1 == case.nodes


@pytest.mark.parametrize("case", [pytest.param(case, id=f"case-{index}") for index, case in enumerate(_HTML_INPUTS)])
def test_generation_html_depth_accounting(case: Generated) -> None:
    root: Final = parse_fragment(case.data.decode("utf-8"))
    assert (
        max(sum(isinstance(ancestor, Element) for ancestor in node.ancestors) for node in root.descendants)
        == case.depth
    )


@pytest.mark.parametrize("case", [pytest.param(case, id=f"case-{index}") for index, case in enumerate(_HTML_INPUTS)])
def test_generation_html_references_bind(case: Generated) -> None:
    root: Final = parse_fragment(case.data.decode("utf-8"))
    references: Final = [
        cast("str", node.attrs.get("href") or node.attrs.get("src")) for node in root.select('[href^="#"],[src^="#"]')
    ]
    assert (bool(references), set(references)) == (True, {"#x"})


@pytest.mark.parametrize("case", [pytest.param(case, id=f"case-{index}") for index, case in enumerate(_HTML_INPUTS)])
def test_generation_html_reference_target(case: Generated) -> None:
    root: Final = parse_fragment(case.data.decode("utf-8"))
    assert [
        (node.tag, tuple(sorted(node.attrs.items())), sum(isinstance(ancestor, Element) for ancestor in node.ancestors))
        for node in root.select("#x")
    ] == [("div", (("id", "x"),), 1)]


@pytest.mark.parametrize("case", [pytest.param(case, id=f"case-{index}") for index, case in enumerate(_HTML_INPUTS)])
def test_generation_html_small_identifier_pool(case: Generated) -> None:
    root: Final = parse_fragment(case.data.decode("utf-8"))
    assert (
        {cast("str", node.attrs["id"]) for node in root.select("[id]")} <= {"x", "y", "z"},
        {token for node in root.select("[class]") for token in cast("list[str]", node.attrs["class"])}
        <= {"a", "b", "c"},
    ) == (True, True)


def test_generation_html_minimum() -> None:
    assert fuzz_structure_generation_html_generate(random.Random(0), 5) == Generated(
        b'<div id="x">x<a href="#x">x</a></div>',
        5,
        3,
        ("html:root", "html:text:0"),
        (("target", "x"),),
    )


def test_generation_html_budget() -> None:
    assert max(case.nodes for case in _HTML_INPUTS) <= 30


def test_generation_html_all_declared_productions_fire() -> None:
    grammar: Final = html_grammar()
    assert set(
        assert_production_floors(
            _FUZZ_STRUCTURE_GENERATION_HTML_CASES, (production.name for production in grammar.productions)
        )
    ) == {production.name for production in grammar.productions}


@pytest.mark.parametrize(
    ("name", "origin"),
    [
        pytest.param(
            "html:tag:span",
            "https://github.com/googleprojectzero/domato/blob/fadff396cc45d521cc594d3e2396e27e887b1963/html_tags.py",
            id="pinned-tag",
        ),
        pytest.param(
            "html:attribute:title:0",
            "https://github.com/tox-dev/turbohtml/blob/6a8722d7614a69df4b6ca6c8563f62d00f0837c1/tools/fuzz/round_trip_oracles.py",
            id="pinned-local-value",
        ),
        pytest.param("html:tag:svg", "https://html.spec.whatwg.org/multipage/parsing.html", id="foreign-boundary"),
    ],
)
def test_generation_html_provenance(name: str, origin: str) -> None:
    assert next(production.origin for production in html_grammar().productions if production.name == name) == origin


def test_generation_general_html_adapter() -> None:
    assert ORACLES["html-fixpoint"].generate(random.Random(0)) == '<frameset id="x"><frame src="#x"></frameset>'


def test_generation_cli_exports(tmp_path: Path) -> None:
    assert (
        fuzz_structure_generation_main(["--output", str(tmp_path), "--count", "2", "--budget", "5"]),
        {path.read_bytes() for path in tmp_path.iterdir()},
    ) == (
        0,
        {b'<div id="x">x<a href="#x">x</a></div>', b'<div id="x"><img src="a b"><a href="#x">x</a></div>'},
    )


def test_generation_cli_sweep(tmp_path: Path) -> None:
    fuzz_structure_generation_main(["--output", str(tmp_path), "--count", "1", "--sweep"])
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {
        case.data for case in _FUZZ_STRUCTURE_GENERATION_HTML_CASES
    }


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        pytest.param(["--count", "0"], "count must be positive", id="bad-count"),
        pytest.param(["--budget", "4"], "cannot complete", id="insufficient-budget"),
    ],
)
def test_generation_cli_rejects_invalid_bounds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str], message: str
) -> None:
    with pytest.raises(SystemExit) as error:
        fuzz_structure_generation_main(["--output", str(tmp_path), *arguments])
    assert (error.value.code, message in capsys.readouterr().err) == (2, True)


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param((), id="empty"),
        pytest.param(("x", "x"), id="duplicate"),
        pytest.param(("x-y",), id="punctuation"),
        pytest.param(("1x",), id="leading-digit"),
        pytest.param(("é",), id="non-ascii"),
        pytest.param(tuple(f"name{index}" for index in range(65)), id="too-large"),
    ],
)
def test_generation_rejects_invalid_identifier_pool(pool: tuple[str, ...]) -> None:
    with pytest.raises(GrammarError, match="1 to 64 distinct ASCII identifiers"):
        compile_grammar((Production("root", "root", (b"x",), 1, "test"),), "root", identifiers=pool)


def test_generation_uses_distinct_supplied_identifiers() -> None:
    grammar: Final = compile_grammar(
        (
            Production(
                "root",
                "root",
                (
                    *(part for name in ("a", "b", "c", "d", "e") for part in (Identifier(name, definition=True), b" ")),
                    *(
                        part
                        for name in ("e", "d", "c", "b", "a")
                        for part in (Identifier(name, definition=False), b" ")
                    ),
                ),
                1,
                "test",
            ),
        ),
        "root",
        identifiers=("first", "second", "third", "fourth", "fifth"),
    )
    assert generate(grammar, random.Random(0), 1) == Generated(
        b"first second third fourth fifth fifth fourth third second first ",
        1,
        0,
        ("root",),
        (("a", "first"), ("b", "second"), ("c", "third"), ("d", "fourth"), ("e", "fifth")),
    )


def test_generation_rejects_supplied_pool_exhaustion() -> None:
    grammar: Final = compile_grammar(
        (
            Production(
                "root",
                "root",
                (Identifier("a", definition=True), Identifier("b", definition=True)),
                1,
                "test",
            ),
        ),
        "root",
        identifiers=("only",),
    )
    with pytest.raises(GrammarError, match="identifier pool exhausted"):
        generate(grammar, random.Random(0), 1)


@pytest.mark.parametrize(
    ("seed", "generated"),
    [pytest.param(0, False, id="legacy-sheet"), pytest.param(1, True, id="structured-sheet")],
)
def test_generation_css_general_adapter(seed: int, *, generated: bool) -> None:
    source: Final = ORACLES["css-fixpoint"].generate(random.Random(seed))
    assert (":root{--x:red;" in source, source.endswith("}")) == (generated, True)
