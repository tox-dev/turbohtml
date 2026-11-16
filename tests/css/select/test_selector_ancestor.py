from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Document, Element, MutationObserver, Text, parse, parse_xml
from turbohtml._html import query_closest  # ruff:ignore[import-private-name]  # the native entry takes a text start
from turbohtml.cssom import computed_style
from turbohtml.query import Query, compile  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name
from turbohtml.rewrite import rewrite

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from traced_bytes import PeakBytes
    from work_steps import WorkSteps

    from turbohtml.rewrite import Element as RewriteElement


def _expected(span: range) -> list[str | None]:
    return [f"e{index}" for index in span]


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("section div", _expected(range(80)), id="typed-ancestor"),
        pytest.param("p div", [], id="typed-ancestor-missing"),
        pytest.param(".top div", _expected(range(80)), id="untyped-ancestor"),
        pytest.param("[data-q] div", [], id="untyped-ancestor-missing"),
        pytest.param("p section div", [], id="typed-ancestor-fails-completely"),
        pytest.param("p .top div", [], id="untyped-ancestor-fails-completely"),
        pytest.param("section > div div", _expected(range(1, 80)), id="typed-ancestor-past-local-failures"),
        pytest.param(".top > [id] div", _expected(range(1, 80)), id="untyped-ancestor-past-local-failures"),
        pytest.param("body > div div", [], id="local-failures-only"),
        pytest.param("p + section div", _expected(range(80)), id="adjacent-left"),
        pytest.param("div + section div", [], id="adjacent-left-missing"),
        pytest.param("p ~ section div", _expected(range(80)), id="sibling-left"),
        pytest.param(":is(p section) div", [], id="nested-ancestor-missing"),
        pytest.param("div:not(p div)", _expected(range(80)), id="nested-subject"),
        pytest.param(":scope div", _expected(range(80)), id="scope-keeps-off-the-memo"),
        pytest.param("div:has(section div)", [], id="has-ancestor-outside-anchor"),
        pytest.param("body:has(section div)", [None], id="has-ancestor-inside-anchor"),
        pytest.param(":nth-child(1 of section div) div", _expected(range(1, 80)), id="nth-of-ancestor"),
        pytest.param(":nth-last-child(1 of section div)", _expected(range(80)), id="nth-last-of"),
        pytest.param(":nth-child(2 of div)", [], id="nth-of-missing"),
        pytest.param("section :nth-child(1)", _expected(range(80)), id="nth-without-of"),
    ],
)
def test_select_deep_chain_at_every_level(selector: str, expected: list[str | None]) -> None:
    document: Final = _deep(_chain())
    assert (_ids(document.select(selector)), _ids([document.select_one(selector)])) == (
        expected,
        expected[:1] or [None],
    )


def _deep(html: str, parser: Callable[[str], Document] = parse) -> Document:
    # linking in a subtree too large to measure drops what the parser knows of how deep the page nests, so its queries
    # run as on a deep tree
    document: Final = parser(html)
    holder: Final = Element("x")
    for _ in range(40):
        branch = Element("y")
        branch.append(Element("z"))
        holder.append(branch)
    root: Final = document.root
    assert root is not None
    root.append(holder)
    holder.extract()
    return document


def _chain(outer: str = "<section class=top>", inner: str = "") -> str:
    # every level past 32 walks into the ancestor memo, and 80 levels grow it; `inner` is set on the div e40
    divs = "".join(f"<div id=e{index}{inner if index == 40 else ''}>" for index in range(80))
    return f"<p></p>{outer}{divs}"


def _ids(nodes: Iterable[Element | None]) -> list[str | None]:
    return [None if node is None else node.attr("id") for node in nodes]


@pytest.mark.parametrize(
    ("selector", "outer", "inner", "expected"),
    [
        pytest.param("div:lang(fr)", "<section lang=fr>", "", _expected(range(80)), id="lang-declared"),
        pytest.param("div:lang(en)", "<section lang=fr>", "", [], id="lang-other"),
        pytest.param("div:lang(fr)", "<section>", "", [], id="lang-undeclared"),
        pytest.param("div:lang(fr)", "<section lang=fr>", " lang=en", _expected(range(40)), id="lang-redeclared"),
        pytest.param("div:lang(en)", "<section lang=fr>", " lang=en", _expected(range(40, 80)), id="lang-nearest"),
        pytest.param(":lang(fr) div", "<section lang=fr>", "", _expected(range(80)), id="lang-ancestor"),
        pytest.param(
            ":lang(en) div", "<section lang=fr>", " lang=en", _expected(range(41, 80)), id="lang-ancestor-nearest"
        ),
        pytest.param("div:dir(rtl)", "<section dir=rtl>", "", _expected(range(80)), id="dir-set"),
        pytest.param("div:dir(ltr)", "<section dir=rtl>", "", [], id="dir-other"),
        pytest.param("div:dir(ltr)", "<section>", "", _expected(range(80)), id="dir-default"),
        pytest.param("div:dir(rtl)", "<section>", "", [], id="dir-default-other"),
        pytest.param("div:dir(rtl)", "<section dir=rtl>", " dir=ltr", _expected(range(40)), id="dir-reset"),
        pytest.param("div:dir(rtl)", "<section dir=rtl>", " dir=up", _expected(range(80)), id="dir-invalid-ignored"),
        pytest.param("div:dir(rtl)", "<section dir=rtl>", " dir=auto", _expected(range(40)), id="dir-auto"),
        pytest.param("div:dir(rtl)", "<section dir=rtl>", " dir", _expected(range(80)), id="dir-empty-ignored"),
        pytest.param("div:dir(rtl)", "<section dir=rtl><bdi>", "", [], id="dir-bdi"),
        pytest.param(":dir(rtl) div", "<section dir=rtl>", " dir=ltr", _expected(range(80)), id="dir-ancestor"),
    ],
)
def test_select_inherited_pseudo_deep_chain(selector: str, outer: str, inner: str, expected: list[str | None]) -> None:
    assert _ids(_deep(_chain(outer, inner)).select(selector)) == expected


def test_select_dir_through_deep_foreign_chain() -> None:
    document: Final = _deep(f"<section dir=rtl><svg>{'<g>' * 40}")
    assert (len(document.select("g:dir(rtl)")), document.select("g:dir(ltr)")) == (40, [])


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<input type=tel id=t>", ["t"], id="tel-default"),
        pytest.param("<input type=tel dir=rtl id=t>", [], id="tel-rtl"),
        pytest.param("<input type=tel dir=up id=t>", ["t"], id="tel-invalid"),
        pytest.param("<input type=text id=t>", [], id="text-inherits"),
    ],
)
def test_select_tel_input_direction_below_deep_chain(html: str, expected: list[str | None]) -> None:
    # a telephone input keeps its own direction, ltr unless its dir sets one, whatever its ancestors set
    document: Final = _deep(f"<section dir=rtl>{'<div>' * 40}<form>{html}</form>")
    assert _ids(document.select("input:dir(ltr)")) == expected


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("section div", "e79", id="self"),
        pytest.param("section > div", "e0", id="top"),
        pytest.param("p div", None, id="ancestor-missing"),
        pytest.param("div:lang(en)", "e79", id="lang"),
        pytest.param("div:lang(fr)", "e39", id="lang-redeclared"),
        pytest.param("div:dir(rtl)", None, id="dir-other"),
        pytest.param("div:dir(ltr)", "e79", id="dir-default"),
        pytest.param("div:has(> div > div)", "e77", id="has"),
    ],
)
def test_closest_deep_chain(selector: str, expected: str | None) -> None:
    leaf: Final = _deep(_chain("<section lang=fr>", " lang=en")).select_one("#e79")
    assert leaf is not None
    assert _ids([leaf.closest(selector)]) == [expected]


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("section div", True, id="ancestor"),
        pytest.param("p div", False, id="ancestor-missing"),
        pytest.param(":lang(fr)", True, id="lang"),
        pytest.param(":dir(rtl)", False, id="dir-other"),
    ],
)
def test_matches_deep_chain(selector: str, *, expected: bool) -> None:
    leaf: Final = _deep(_chain("<section lang=fr>")).select_one("#e79")
    assert leaf is not None
    assert leaf.matches(selector) is expected


def test_matches_detached_deep_chain() -> None:
    # a chain built without a document ends at an element that has no parent
    leaf: Final = Element("div")
    top = leaf
    for _ in range(40):
        parent = Element("div")
        parent.append(top)
        top = parent
    assert (leaf.matches(":lang(en)"), leaf.matches(":dir(ltr)"), leaf.matches("section div")) == (False, True, False)
    assert leaf.closest(":lang(en)") is None
    assert (top.matches(":dir(ltr) > div"), top.matches(":dir(ltr) + div")) == (False, False)


def test_matcher_filter_deep_chain_out_of_order() -> None:
    elements: Final = _deep(_chain()).select("div")
    candidates: Final = [elements[79], elements[40], elements[78], elements[0]]
    assert (_ids(compile("section > div div").filter(candidates)), _ids(compile("p div").filter(candidates))) == (
        ["e79", "e40", "e78"],
        [],
    )


def test_query_find_from_many_roots_deep_chain() -> None:
    document: Final = _deep(_chain())
    roots: Final = Query(document)("#e10, #e60")
    assert _ids(roots.find(":scope div div")) == _expected(range(12, 80))


@pytest.mark.parametrize(
    ("selector", "count"),
    [
        pytest.param("item b", 0, id="ancestor-missing"),
        pytest.param("doc b", 600, id="ancestor-at-root"),
        pytest.param("b b", 599, id="typed-ancestor"),
        pytest.param(":lang(en)", 603, id="lang"),
        pytest.param(":dir(rtl)", 0, id="dir"),
    ],
)
def test_select_deep_xml_chain(selector: str, count: int) -> None:
    assert len(_nested(600).select(selector)) == count


# parse_xml() has no nesting cap, unlike the 512 levels parse() keeps, so the document sets the depth of every climb.
def _nested(depth: int) -> Document:
    return parse_xml(f'<doc xml:lang="en">{"<b>" * depth}<i/><u/>{"</b>" * depth}</doc>')


@pytest.mark.parametrize(
    ("selector", "count"),
    [
        pytest.param("b:has(b i)", 599, id="descendant"),
        pytest.param("b:has(item i)", 0, id="descendant-missing"),
        pytest.param("b:has(b > i)", 599, id="child-subject"),
        pytest.param("b:has(> b i)", 599, id="child-lead"),
        pytest.param("b:has(> b > b)", 598, id="child-chain"),
        pytest.param("b:has(> item)", 0, id="child-missing"),
        pytest.param("b:has(b b b)", 597, id="three-compounds"),
        pytest.param("b:has(i + u)", 600, id="adjacent"),
        pytest.param("b:has(u + i)", 0, id="adjacent-missing"),
        pytest.param("b:has(b i ~ u)", 599, id="general-sibling"),
        pytest.param("b:has(b u ~ i)", 0, id="general-sibling-missing"),
        pytest.param("b:has(+ b i)", 0, id="adjacent-lead"),
        pytest.param("b:has(~ b i)", 0, id="sibling-lead"),
        pytest.param("doc:has(b:lang(en) i)", 1, id="lang-inside"),
        pytest.param("b:has(:scope b i)", 599, id="scope-pinned"),
        pytest.param("b:has(:scope > b i)", 599, id="scope-pinned-child"),
        pytest.param("b:has(:scope > i + u)", 1, id="scope-pinned-adjacent"),
        pytest.param("b:has(:scope[id] b i)", 0, id="scope-pinned-mismatch"),
        pytest.param("b:has(b:not(:scope) i)", 599, id="scope-later"),
        pytest.param("b:has(b:not(:scope) item)", 0, id="scope-later-missing"),
        pytest.param("b:has(b:not(:scope + q) i)", 599, id="scope-reached-later"),
        pytest.param("b:has(b i, item i)", 599, id="alternatives"),
        pytest.param("b:has(> b > i)", 1, id="child-chain-subject"),
        pytest.param("b:has(> b > u)", 1, id="child-chain-sibling-subject"),
        pytest.param("b:has(> b + i)", 0, id="child-chain-adjacent-missing"),
        pytest.param("b:has(> b ~ i)", 0, id="child-chain-general-missing"),
        pytest.param("b:has(> b > b > i)", 1, id="child-chain-three"),
        pytest.param("b:has(> b:not(:scope) > i)", 1, id="child-chain-scope-later"),
        pytest.param("b:has(> b > i:not(:scope))", 1, id="child-chain-scope-subject"),
        pytest.param("b:has(> b > i, > b > u)", 1, id="child-chain-alternatives"),
        pytest.param("b:has(> b:scope > i)", 1, id="child-chain-scope-after-type"),
        pytest.param("b:has(> i:scope > u)", 0, id="child-chain-scope-after-type-missing"),
    ],
)
def test_select_has_compound_chain_deep_xml(selector: str, count: int) -> None:
    assert len(_nested(600).select(selector)) == count


@pytest.mark.parametrize(
    ("selector", "count"),
    [
        pytest.param("x:has(~ y z)", 64, id="general-sibling-lead"),
        pytest.param("x:has(~ y q)", 0, id="general-sibling-lead-missing"),
        pytest.param("x:has(+ y z)", 1, id="adjacent-lead"),
        pytest.param("x:has(y ~ y z)", 0, id="general-sibling-inside"),
        pytest.param("x:has(~ y:not(:scope))", 64, id="scope-general-sibling-lead"),
        pytest.param("x:has(~ z:not(:scope))", 0, id="scope-general-sibling-lead-missing"),
        pytest.param("x:has(~ y z:not(:scope))", 64, id="scope-general-sibling-lead-subtree"),
        pytest.param("x:has(+ y:not(:scope))", 1, id="scope-adjacent-lead"),
        pytest.param("x:has(~ x + y)", 63, id="sibling-chain"),
        pytest.param("y ~ x", 0, id="earlier-sibling-missing"),
        pytest.param("x ~ x", 63, id="earlier-sibling"),
    ],
)
def test_select_has_sibling_chain_deep_xml(selector: str, count: int) -> None:
    # 64 x siblings then a y holding a z, all below a chain past the parser's depth
    document: Final = parse_xml(f"<doc>{'<w>' * 600}{'<x/>' * 64}<y><z/></y>{'</w>' * 600}</doc>")
    assert len(document.select(selector)) == count


def test_closest_deep_xml_chain() -> None:
    deepest: Final = _deepest(600)
    assert (deepest.closest("item i"), deepest.closest("b:has(> i)")) == (None, deepest.parent)


def _deepest(depth: int) -> Element:
    found = _nested(depth).select_one("i")
    assert found is not None
    return found


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("item b", id="ancestor-missing"),
        pytest.param("doc b", id="ancestor-at-root"),
        pytest.param(":lang(en)", id="lang"),
        pytest.param(":dir(rtl)", id="dir"),
        pytest.param("b:has(item i)", id="has-descendant-missing"),
        pytest.param("b:has(b i)", id="has-descendant"),
        pytest.param("b:has(b u ~ i)", id="has-general-sibling-missing"),
        pytest.param("b:has(:scope b item)", id="has-scope-pinned"),
        pytest.param(":scope item b", id="scope-ancestor"),
        pytest.param(":nth-child(1 of item b)", id="nth-of-ancestor"),
    ],
)
def test_select_deep_xml_chain_work_linear(selector: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_nested(depth).select, selector), (600, 2400))


def _reinserted(depth: int) -> Document | Element:
    document, top = _measured_without_chain(depth)
    root: Final = document.root
    assert root is not None
    root.append(top)
    return document


def _detached(depth: int) -> Document | Element:
    return _measured_without_chain(depth)[1]


def _detached_below(depth: int) -> Document | Element:
    below: Final = _measured_without_chain(depth)[1].select_one("b")
    assert below is not None
    return below


def _measured_without_chain(depth: int) -> tuple[Document, Element]:
    # the chain is out of the document while a query reads how deep the document nests
    document: Final = _nested(depth)
    top: Final = _top(document).extract()
    root: Final = document.root
    assert root is not None
    root.append(Element("x"))
    assert document.select("b") == []
    return document, top


def _below_edited_document(depth: int) -> Document | Element:
    document: Final = _nested(depth)
    top: Final = _top(document)
    root: Final = document.root
    assert root is not None
    root.append(Element("x"))
    return top


def _below_measured_document(depth: int) -> Document | Element:
    return _top(_nested(depth))


def _top(document: Document) -> Element:
    found = document.select_one("b")
    assert found is not None
    return found


@pytest.mark.parametrize(
    "origin",
    [
        pytest.param(_reinserted, id="reinserted"),
        pytest.param(_detached, id="detached"),
        pytest.param(_detached_below, id="detached-below"),
        pytest.param(_below_edited_document, id="below-edited-document"),
        pytest.param(_below_measured_document, id="below-measured-document"),
    ],
)
def test_select_deep_xml_chain_origin_work_linear(
    origin: Callable[[int], Document | Element], work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(lambda depth: partial(origin(depth).select, "item b"), (600, 2400))


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("item b", id="ancestor"),
        pytest.param("b:has(item i)", id="has"),
        pytest.param(":lang(fr)", id="lang"),
    ],
)
def test_closest_deep_xml_chain_work_linear(selector: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_deepest(depth).closest, selector), (600, 2400))


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("b:has(item i)", id="has"),
        pytest.param("b:not(:scope):has(item i)", id="has-scoped"),
        pytest.param("item b", id="ancestor"),
        pytest.param("item b, :scope > u", id="ancestor-scoped"),
    ],
)
def test_query_closest_deep_xml_chain_work_linear(selector: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(Query(_nested(depth))("i, u").closest, selector), (600, 2400))


def test_query_closest_scoped_from_text_deep_xml_chain() -> None:
    document: Final = _nested(600)
    deepest: Final = document.select_one("i")
    assert deepest is not None
    start: Final = Text("t")
    deepest.append(start)
    # a start that is not an element and an element start each climb under a scope of their own kind
    assert query_closest([start, deepest], "doc b:not(:scope) > i, :scope > b > i") == [deepest]


def test_matches_deep_xml_chain_work_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_deepest(depth).matches, ":lang(fr) i"), (600, 2400))


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("b:has(item i)", id="has"),
        pytest.param("item b", id="ancestor"),
        pytest.param("item b, :scope > u", id="ancestor-scoped"),
    ],
)
def test_matcher_filter_deep_xml_chain_work_linear(selector: str, work_steps: WorkSteps) -> None:
    matcher: Final = compile(selector)
    work_steps.assert_linear(lambda depth: partial(matcher.filter, _nested(depth).select("b")), (600, 2400))


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("item b", id="ancestor"),
        pytest.param(":lang(fr)", id="lang"),
    ],
)
def test_remove_deep_xml_chain_work_linear(selector: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_nested(depth).remove, selector), (600, 2400))


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(":lang(fr) div", id="lang-ancestor"),
        pytest.param(":dir(rtl) > div div", id="dir-ancestor"),
        pytest.param(":is(p div) div", id="nested-ancestor"),
        pytest.param(":not(:has(p b)) div", id="has-ancestor"),
    ],
)
def test_select_climb_from_climbs_parsed_page_work_linear(selector: str, work_steps: WorkSteps) -> None:
    # a selector that walks ancestors from each ancestor it passes squares how deep the page nests, even below the
    # 512 levels parse() keeps
    work_steps.assert_linear(lambda depth: partial(parse("<div>" * depth).select, selector), (100, 400))


@pytest.mark.parametrize(
    "levels",
    [
        pytest.param(1, id="leaves"),
        pytest.param(2, id="small-subtrees"),
        pytest.param(None, id="whole-chain"),
    ],
)
def test_select_after_inserting_past_parser_depth_work_linear(levels: int | None, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_grown(depth, levels or depth).select, "[data-q] div"), (600, 2400))


def _grown(depth: int, levels: int) -> Document:
    # parse() nests no deeper than 512 levels; links of `levels` divs inserted one below the other take the chain past
    # that
    document: Final = parse("<div>" * 600)
    node = document.select("div")[-1]
    for _ in range(depth // levels):
        top = bottom = Element("div")
        for _ in range(levels - 1):
            child = Element("div")
            bottom.append(child)
            bottom = child
        node.append(top)
        node = bottom
    return document


def test_select_after_inserting_text_and_leaves() -> None:
    document: Final = parse(f"<section lang=fr>{'<div>' * 40}")
    deepest: Final = document.select("div")[-1]
    deepest.append(Text("t"))
    deepest.append(Element("i"))
    assert (len(document.select(":lang(fr) i")), len(document.select("section i"))) == (1, 1)


@pytest.mark.parametrize(
    "html",
    [
        pytest.param("<a><p>x</a>y", id="adoption-agency"),
        pytest.param(
            f"<select><button>{'<span>' * 20}<selectedcontent></selectedcontent>{'</span>' * 20}</button>"
            f"<option selected>{'<span>' * 505}x</option></select>",
            id="selectedcontent-past-parser-depth",
        ),
    ],
)
def test_select_after_parser_reparents(html: str) -> None:
    # the tree builder can move a node below where its start tag put it, so the finished page is measured
    document: Final = parse(f"<section lang=fr>{html}")
    assert document.select(":lang(fr) p, :lang(fr) span:empty") == document.select("section p, section span:empty")


@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("item div", id="ancestor"),
        pytest.param("div:lang(fr)", id="lang"),
        pytest.param("div:has(item span)", id="has"),
    ],
)
def test_computed_style_deep_chain_work_linear(rule: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(computed_style, _deep_styled(depth, rule)), (600, 2400))


def _deep_styled(depth: int, rule: str) -> Element:
    document: Final = parse(f"<style>{rule}{{color:red}}</style>")
    node = document.select_one("body")
    assert node is not None
    for _ in range(depth):
        child = Element("div")
        node.append(child)
        node = child
    return node


def test_computed_style_climb_from_climbs_parsed_page_work_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda depth: partial(
            computed_style, parse(f"<style>:lang(fr) div{{color:red}}</style>{'<div>' * depth}").select("div")[-1]
        ),
        (100, 400),
    )


@pytest.mark.parametrize(
    ("rule", "color"),
    [
        pytest.param("section div", "red", id="ancestor"),
        pytest.param(":lang(fr) div", "red", id="lang-ancestor"),
        pytest.param(":lang(en) div", "black", id="lang-ancestor-other"),
        pytest.param("p div", "black", id="ancestor-missing"),
    ],
)
def test_computed_style_deep_chain(rule: str, color: str) -> None:
    leaf: Final = _deep(f"<style>div{{color:black}}{rule}{{color:red}}</style>{_chain('<section lang=fr>')}")
    found: Final = leaf.select_one("#e79")
    assert found is not None
    assert computed_style(found)["color"] == color


@pytest.mark.parametrize(
    ("selectors", "color"),
    [
        pytest.param("p, .c, #t", "red", id="rising"),
        pytest.param("#t, .c, p", "red", id="falling"),
        pytest.param("p, body p", "blue", id="rising-types"),
        pytest.param(".c, p, .c", "blue", id="equal"),
    ],
)
def test_computed_style_deep_tree_picks_most_specific_alternative(selectors: str, color: str) -> None:
    # the most specific alternative that matches gives the rule its weight against `p.c`
    html: Final = f"<style>p.c{{color:blue}}{selectors}{{color:red}}</style><p id=t class=c style title=x>"
    styles: Final[list[str]] = []
    for document in (parse(html), _deep(html)):
        found = document.select_one("p")
        assert found is not None
        styles.append(computed_style(found)["color"])
    assert styles == [color, color]


@pytest.mark.parametrize(
    ("parser", "markup", "expected"),
    [
        pytest.param(
            parse,
            "<section lang=fr><svg xml:lang=de><g id=a></g></svg><svg xml:lang='' lang=it><g id=b></g></svg>"
            "<svg><g id=c></g></svg><math lang=es><mi id=d></mi></math><div lang=''><p id=e></p></div>"
            "<div lang=en><p id=f></p></div></section>",
            [["a"], ["b"], ["c", "d", "e"], [], ["f"]],
            id="html",
        ),
        pytest.param(
            parse_xml,
            "<r xml:lang='fr'><a xml:lang=''><b id='g'/></a><c lang='en'><d id='h'/></c>"
            "<e xml:lang='de'><f id='i'/></e></r>",
            [["i"], [], ["h"], [], []],
            id="xml",
        ),
    ],
)
def test_select_lang_declarations_deep_tree(
    parser: Callable[[str], Document], markup: str, expected: list[list[str]]
) -> None:
    # xml:lang counts on a foreign or XML element and lang on an HTML or SVG element of an HTML page; an empty lang, or
    # an empty xml:lang on a page the HTML parser built, declares nothing
    found: Final = [
        [_ids(document.select(f"[id]:lang({lang})")) for lang in ("de", "it", "fr", "es", "en")]
        for document in (parser(markup), _deep(markup, parser))
    ]
    assert found == [expected, expected]


def test_matches_lang_and_dir_on_extracted_element() -> None:
    # an element taken out of a parsed page has no ancestor to inherit from
    document: Final = parse("<main lang=fr dir=rtl><p>")
    paragraph: Final = document.select_one("p")
    assert paragraph is not None
    paragraph.extract()
    assert (paragraph.matches(":lang(fr)"), paragraph.matches(":dir(rtl)"), paragraph.matches(":dir(ltr)")) == (
        False,
        False,
        True,
    )


@pytest.mark.parametrize(
    ("selector", "marked"),
    [
        pytest.param("section div", 700, id="ancestor"),
        pytest.param("p div", 0, id="ancestor-missing"),
        pytest.param(":lang(fr) div", 700, id="lang-ancestor"),
        pytest.param("div:lang(en)", 0, id="lang-other"),
    ],
)
def test_rewrite_past_parser_depth(selector: str, marked: int) -> None:
    out: Final = rewrite(f"<section lang=fr>{'<div>' * 700}", elements=[(selector, _mark)])
    assert out.count(' k=""') == marked


def _mark(element: RewriteElement) -> None:
    element.set_attribute("k", "")


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("p div", id="ancestor"),
        pytest.param("div:lang(fr)", id="lang"),
    ],
)
def test_rewrite_past_parser_depth_work_linear(selector: str, work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda depth: partial(rewrite, "<div>" * depth, elements=[(selector, _mark)]), (1000, 4000)
    )


def test_rewrite_climb_from_climbs_work_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda depth: partial(rewrite, "<div>" * depth, elements=[(":lang(fr) div", _mark)]), (100, 400)
    )


# 70 b siblings, past twice the 32 a sibling walk takes before the sibling memo, below a chain past the ancestor memo's
_RUN: Final[str] = "".join(
    f"<b id=s{index}{' class=c' if index % 3 == 0 else ''}>{'<i></i>' if index % 5 == 0 else ''}</b>"
    f"{'<u></u>' if index % 4 == 0 else ''} "
    for index in range(70)
)
_SIBLINGS: Final[str] = (
    f"<section lang=fr class=top>{'<div>' * 40}<p>{_RUN}</p><span dir=rtl><b lang=en><i></i></b></span>"
)


@pytest.mark.parametrize(
    ("selector", "count"),
    [
        pytest.param("b ~ b", 69, id="general-sibling"),
        pytest.param(".c ~ b", 69, id="general-sibling-untyped"),
        pytest.param("b.c ~ u ~ b", 69, id="general-sibling-chain"),
        pytest.param("i ~ b", 0, id="general-sibling-missing"),
        pytest.param("section #s0 ~ b", 69, id="general-sibling-far"),
        pytest.param("section #s99 ~ b", 0, id="general-sibling-far-missing"),
        pytest.param("section #s99 ~ b, section #s0 ~ b", 69, id="general-sibling-far-alternatives"),
        pytest.param("section u + .c ~ b", 60, id="general-sibling-past-local-failures"),
        pytest.param(":nth-child(2 of section b) ~ u", 17, id="general-sibling-after-nth-of"),
        pytest.param(":lang(fr) u ~ b", 69, id="general-sibling-after-ancestor"),
        pytest.param("u + b", 18, id="adjacent"),
        pytest.param(".top b + u", 18, id="adjacent-subject"),
        pytest.param(":lang(fr) + b", 69, id="adjacent-after-inherited"),
        pytest.param("i + b", 0, id="adjacent-missing"),
        pytest.param("section b:nth-child(odd of section b)", 36, id="nth-of"),
        pytest.param(":nth-last-child(2 of section b)", 1, id="nth-last-of"),
        pytest.param(":nth-child(3 of .c:lang(fr))", 1, id="nth-of-inherited"),
        pytest.param("b:has(~ b i)", 65, id="has-general-sibling-lead"),
        pytest.param("b:has(+ u)", 18, id="has-adjacent-lead"),
        pytest.param("b:has(~ u:lang(fr):not(:scope))", 69, id="has-scope-general-sibling-lead"),
        pytest.param("b:has(+ u:lang(fr):not(:scope))", 18, id="has-scope-adjacent-lead"),
        pytest.param("b:has(~ i:lang(fr):not(:scope))", 0, id="has-scope-general-sibling-lead-missing"),
        pytest.param("b:has(~ b i:not(:scope))", 65, id="has-scope-general-sibling-lead-subtree"),
        pytest.param("b:has(~ u:lang(fr):not(:scope + q))", 69, id="has-reached-scope-general-sibling-lead"),
        pytest.param("b:has(+ u:lang(fr):not(:scope + q))", 18, id="has-reached-scope-adjacent-lead"),
        pytest.param("b:has(~ b i:not(:scope + q))", 65, id="has-reached-scope-general-sibling-lead-subtree"),
        pytest.param(":lang(fr):has(b i:not(:scope + q))", 43, id="has-reached-scope-descendant"),
        pytest.param("b:has(~ u ~ b:lang(fr):not(:scope))", 69, id="has-scope-general-sibling-chain"),
        pytest.param("section:not(:scope) i", 15, id="scope-ancestor"),
        pytest.param(":dir(rtl) i", 1, id="dir-ancestor"),
        pytest.param(":lang(en) i, :lang(fr) b:has(i) i", 15, id="lang-alternatives"),
    ],
)
def test_deep_tree_matches_parsed_page(selector: str, count: int) -> None:
    # the same page, once as parsed and once as a tree that may nest deep, which the matcher walks with its memo
    parsed: Final = parse(_SIBLINGS)
    deep: Final = _deep(_SIBLINGS)
    found: Final = [_paths(document.select(selector)) for document in (parsed, deep)]
    filtered: Final = [_paths(compile(selector).filter(document.select("*"))) for document in (parsed, deep)]
    closest: Final = [_paths(query_closest(document.select("i"), selector)) for document in (parsed, deep)]
    assert (len(found[0]), found[1], filtered[1], closest[1]) == (count, found[0], filtered[0], closest[0])


def _paths(nodes: Iterable[Element]) -> list[str]:
    return [node.xpath_path() for node in nodes]


def test_computed_style_nth_of_out_of_order_deep_tree() -> None:
    html: Final = f"<style>:nth-child(odd of section b){{color:red}}</style>{_SIBLINGS}"
    colors: Final = [
        [computed_style(element)["color"] for element in reversed(document.select("p > b"))]
        for document in (parse(html), _deep(html))
    ]
    assert (colors[1], colors[1].count("red")) == (colors[0], 35)


def test_computed_style_deep_has_past_memo_limit() -> None:
    # 128 :has() arguments of two compounds keep two answers per element, past the bytes of answers the cascade keeps
    arguments: Final = 128
    sheet: Final = f":has({','.join(['p q'] * arguments)}){{color:blue}}:has(p){{color:red}}"
    document: Final = _deep(f"<style>{sheet}</style>{'<div>' * 28}{'<p>' * ((1 << 24) // (2 * arguments) + 8_192)}")
    root: Final = document.select_one("html")
    assert root is not None
    assert computed_style(root)["color"] == "red"


def test_computed_style_deep_has_past_memo_limit_raises_past_the_allowance() -> None:
    # past the memo every :has() walks from its anchor, so p:has(~ q) on each p of the run passes every later sibling
    arguments: Final = 128
    sheet: Final = f":has({','.join(['p q'] * arguments)}){{color:blue}}p:has(~ q){{color:red}}"
    document: Final = _deep(f"<style>{sheet}</style>{'<div>' * 28}{'<p>' * ((1 << 24) // (2 * arguments) + 8_192)}")
    root: Final = document.select_one("html")
    assert root is not None
    computed_style(root)
    with pytest.raises(ValueError, match="exceed the allowance"):
        list(map(computed_style, document.select("p")))


def test_rewrite_retires_many_deep_elements() -> None:
    # each closed element waits until the most the stand-ins may name, which then starts over
    out: Final = rewrite(f"<i lang=fr>{'<b></b>' * 70_000}</i>", elements=[(":lang(fr) b", _mark)])
    assert out.count(' k=""') == 70_000


def test_select_after_observed_insert_into_deep_tree_work_linear(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda depth: partial(_observed(depth).select, "item b"), (600, 2400))


def _observed(depth: int) -> Document:
    # an observer keeps insertions on the path that tracks how deep the tree nests
    document: Final = _nested(depth)
    MutationObserver().observe(document, child_list=True, subtree=True)
    _top(document).append(Element("x"))
    return document


@pytest.mark.parametrize(
    ("above", "count"),
    [
        pytest.param(999, 1, id="reaches"),
        pytest.param(998, 0, id="misses"),
    ],
)
def test_select_deep_has_past_memo_bound(above: int, count: int) -> None:
    # an i with `above` d's over it, then a run of 5000 d's holding no i: d^999 i matches only if 999 d's sit over the i
    document: Final = parse_xml(f"<doc>{'<d>' * above}<i/>{'<d>' * 5000}{'</d>' * 5000}{'</d>' * above}</doc>")
    assert len(document.select(f"doc:has({'d ' * 999}i)")) == count


def test_select_deep_has_memo_stays_within_budget(peak_bytes: PeakBytes) -> None:
    # the answers of a deep query take two bits per compound and node, so the traced peak stays far below the hash table
    peak: Final = peak_bytes(
        "import tracemalloc\n"
        "from turbohtml import parse_xml\n"
        "above = 998\n"
        "chain = '<d>' * above + '<i/>' + '<d>' * 5000\n"
        "document = parse_xml('<doc>' + chain + '</d>' * 5000 + '</d>' * above + '</doc>')\n"
        "tracemalloc.start()\n"
        "found = document.select('doc:has(' + 'd ' * 999 + 'i)')\n"
        "assert not found\n"
        "print(tracemalloc.get_traced_memory()[1])\n",
        (),
    )
    assert peak < 16 * 1024 * 1024, peak


def test_select_deep_has_memo_work_linear(work_steps: WorkSteps) -> None:
    # a 16-compound :has() over a chain: each compound answers once per node, so the steps grow with the depth alone
    work_steps.assert_linear(lambda depth: partial(_nested(depth).select, f"b:has({'b ' * 15}i)"), (600, 2400))


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("b:has(> b > i)", id="child-chain"),
        pytest.param("b:has(> b + i)", id="child-adjacent"),
        pytest.param("b:has(> b ~ u)", id="child-general-sibling"),
        pytest.param("b:has(> b:not(:scope) > i)", id="child-chain-scope-later"),
        pytest.param("b:has(> b > i, > b > u)", id="child-chain-alternatives"),
    ],
)
def test_select_child_has_chain_work_linear(selector: str, work_steps: WorkSteps) -> None:
    # a child-led argument with no descendant combinator reaches only the levels its compounds name, so every anchor
    # walks its neighbourhood instead of its whole subtree
    work_steps.assert_linear(lambda depth: partial(_nested(depth).select, selector), (600, 2400))


def test_closest_child_has_chain_work_linear(work_steps: WorkSteps) -> None:
    # no ancestor matches, so closest() asks every ancestor of the deepest element
    work_steps.assert_linear(lambda depth: partial(_deepest(depth).closest, "b:has(> item > i)"), (600, 2400))


def test_has_descendant_on_shallow_parsed_page() -> None:
    # a parsed page nests under the memo depth, so its descendant :has() walks the subtree without the memo
    document: Final = parse("<div><p><a></a></p></div><div></div>")
    assert len(document.select("div:has(a)")) == 1


def test_child_has_chain_matches_and_closest_on_deep_xml() -> None:
    deepest: Final = _deepest(600)
    parent: Final = deepest.parent
    assert parent is not None
    grandparent: Final = parent.parent
    assert grandparent is not None
    assert deepest.closest("b:has(> b > i)") == grandparent
    assert grandparent.matches("b:has(> b > i)")
    assert not parent.matches("b:has(> b > i)")


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("section:has(div b)", id="descendant-pair"),
        pytest.param("div:has(p b i)", id="descendant-triple"),
        pytest.param("section:has(p > b i)", id="child-pair"),
        pytest.param("div:has(u ~ b i)", id="general-sibling-inside"),
        pytest.param("section:has(b + u)", id="adjacent-inside"),
        pytest.param("section:has(p b x)", id="descendant-missing"),
    ],
)
def test_deep_has_matches_parsed_page_per_compound(selector: str) -> None:
    # the same page through the ordinary matcher and through the deep stand-in, whose :has() memo keys each compound
    assert _paths(_deep(_SIBLINGS).select(selector)) == _paths(parse(_SIBLINGS).select(selector))
