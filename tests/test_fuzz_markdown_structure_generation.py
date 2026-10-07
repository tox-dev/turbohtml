from __future__ import annotations

import random
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.markdown_structure_generators import (
    MarkdownProfileError,
    main,
    markdown_controls,
    markdown_generate,
    markdown_grammar,
    markdown_html_check,
    markdown_snapshot,
    markdown_source_check,
    markdown_source_seeds,
)
from fuzz.round_trip_oracles import ORACLES, OutOfScopeError
from fuzz.structure_generators import (
    BudgetError,
    Generated,
    GenerationBudget,
    ProductionFloorError,
    assert_production_floors,
    generate,
    generation_sweep,
)

from turbohtml import Element, parse_fragment

if TYPE_CHECKING:
    from pathlib import Path

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
    assert main(["--output", str(tmp_path), *(["--html"] if html else [])]) == 0
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {case.data for case in cases}


@pytest.mark.parametrize("html", [False, True], ids=["raw", "html"])
def test_markdown_random_corpus(tmp_path: Path, *, html: bool) -> None:
    assert main(["--output", str(tmp_path), "--count", "1", "--seed", "4", *(["--html"] if html else [])]) == 0
    assert {path.read_bytes() for path in tmp_path.iterdir()} == {markdown_generate(random.Random(4), html=html).data}


@pytest.mark.parametrize("arguments", [["--count", "-1"], ["--budget", "0"], ["--steps", "0"]])
def test_markdown_cli_errors(tmp_path: Path, arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--output", str(tmp_path), *arguments])
    assert error.value.code == 2


def test_markdown_unsupported_element() -> None:
    with pytest.raises(MarkdownProfileError, match="no declared Markdown meaning"):
        markdown_html_check("<select><option>x</option></select>")


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<!-- hidden --><script>x</script><style>x</style><p>y</p>", id="hidden-elements"),
        pytest.param("<div> <p><span>a</span><span>b</span></p> </div>", id="adjacent-transparent-text"),
        pytest.param("<p>x</p>y", id="trailing-inline-flow"),
        pytest.param("x<p>y</p>", id="leading-inline-flow"),
        pytest.param("<p></p>", id="empty-paragraph"),
        pytest.param("<div>target<p></p></div>", id="trailing-empty-paragraph"),
        pytest.param('<div idef="#x">target</a></p></vid>', id="repaired-empty-paragraph"),
        pytest.param("<pre>x  y\n</pre>", id="preserved-code-whitespace"),
        pytest.param('<p class="ignored">x</p>', id="nonsemantic-attribute"),
    ],
)
def test_markdown_supported_html_meaning(markup: str) -> None:
    assert markdown_html_check(markup) is None


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
