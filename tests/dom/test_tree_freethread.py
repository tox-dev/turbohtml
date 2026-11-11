from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Final, cast

import pytest

import turbohtml
from turbohtml.clean import sanitize_node
from turbohtml.conformance import check
from turbohtml.mutations import MutationObserver
from turbohtml.query import Query
from turbohtml.transform import Transform

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def test_concurrent_transform_import_resolution_and_mutation_is_memory_safe(tmp_path: Path) -> None:
    namespace = 'version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'
    for name in ("base", "other"):
        (tmp_path / f"{name}.xsl").write_text(
            f'<xsl:stylesheet {namespace}><xsl:output method="text"/>'
            f'<xsl:template match="/">{name}</xsl:template></xsl:stylesheet>',
            encoding="utf-8",
        )
    stylesheet = turbohtml.parse_xml(f'<xsl:stylesheet {namespace}><xsl:import href="base.xsl"/></xsl:stylesheet>')
    imported = stylesheet.find("xsl:import")
    assert imported is not None
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            Transform(stylesheet, base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)

    def mutator() -> None:
        start.wait()
        for index in range(200):
            imported.attrs["href"] = "base.xsl" if index % 2 == 0 else "other.xsl"

    _run(reader, mutator)
    result = Transform(stylesheet, base_url=str(tmp_path / "main.xsl"), import_root=tmp_path)(
        turbohtml.parse_xml("<r/>")
    )
    assert result in {"base", "other"}


def _run(*targets: object) -> None:
    threads = [threading.Thread(target=target) for target in targets]  # ty: ignore[invalid-argument-type]  # targets are typed object by the race harness; Thread needs a callable
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_concurrent_find_all_and_extract_is_memory_safe() -> None:
    doc = _doc(400)
    body = doc.find("body")
    assert body is not None
    children = list(body.children)
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(300):
            doc.find_all("p")  # walks the tree while the mutator rewires it

    def mutator() -> None:
        start.wait()
        for child in children:
            child.extract()  # detaches each <div> (and its <p>) from the live tree

    _run(reader, mutator)
    assert doc.find_all("p") == []  # every <div> was extracted, so no <p> remains


def _doc(divs: int) -> turbohtml.Document:
    body = "".join(f"<div><p>x{index}</p><span>s{index}</span></div>" for index in range(divs))
    return turbohtml.parse(f"<html><body>{body}</body></html>")


def test_concurrent_find_by_text_and_extract_is_memory_safe() -> None:
    doc = _doc(400)
    body = doc.find("body")
    assert body is not None
    children = list(body.children)
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(300):
            # the callable predicate runs Python mid-walk, suspending the per-tree lock;
            # the C side snapshots the candidates and their text under the lock first
            doc.find_all(text=lambda value: value is not None and value.startswith("x"))

    def mutator() -> None:
        start.wait()
        for child in children:
            child.extract()

    _run(reader, mutator)


def test_concurrent_reads_and_mixed_mutations_are_memory_safe() -> None:
    doc = _doc(300)
    body = doc.find("body")
    assert body is not None
    children = list(body.children)
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.select("div p")
            body.serialize()
            assert isinstance(body.text, str)

    def extractor() -> None:
        start.wait()
        for child in children:
            child.extract()

    def appender() -> None:
        start.wait()
        for _ in range(200):
            body.append(turbohtml.Element("hr"))

    _run(reader, extractor, appender)
    assert body.serialize().startswith("<body>")  # still well-formed after the concurrent churn


def test_concurrent_multi_root_query_is_one_tree_snapshot() -> None:
    document = turbohtml.parse('<main><section id="a"><p id="x"></p></section><section id="b"><p id="y"></p></section>')
    first, second = document.select("section")
    moved = document.select_one("#y")
    assert moved is not None
    first.extract()
    second.extract()
    query = Query([second, first])
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(500):
            assert sorted(cast("str", element.attrs["id"]) for element in query.find("p")) == ["x", "y"]

    def mutator() -> None:
        start.wait()
        for _ in range(250):
            first.append(moved)
            second.append(moved)

    _run(reader, mutator)


def test_concurrent_lazy_node_iterators_and_extract_is_memory_safe() -> None:
    body = _doc(400).find("body")
    assert body is not None
    children = list(body.children)
    start = threading.Barrier(3)

    def descendant_reader() -> None:
        start.wait()
        for _ in range(300):
            list(body.descendants)
            list(body.following)

    def sequence_reader() -> None:
        start.wait()
        for _ in range(300):
            len(body)
            list(body)

    def mutator() -> None:
        start.wait()
        for child in children:
            child.extract()

    _run(descendant_reader, sequence_reader, mutator)
    assert list(body.children) == []


def test_concurrent_attrs_views_and_mutation_are_memory_safe() -> None:
    body = _doc(300).find("body")
    assert body is not None
    divs = body.find_all("div")
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            for div in divs:
                len(div.attrs)
                list(div.attrs)
                div.attrs.keys()
                div.attrs.values()
                div.attrs.items()
                repr(div.attrs)

    def mutator() -> None:
        start.wait()
        for index in range(200):
            for div in divs:
                div.attrs["data-x"] = str(index)
                del div.attrs["data-x"]

    _run(reader, mutator)
    assert all("data-x" not in div.attrs for div in divs)


def test_concurrent_reads_and_bulk_wrap_is_memory_safe() -> None:
    doc = _doc(300)
    body = doc.find("body")
    assert body is not None
    divs = body.find_all("div")
    firsts = [next(iter(div.children)) for div in divs]  # each <div> opens with a <p>
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.find_all("p")  # walks the tree while the wrappers relink runs of children
            body.serialize()

    def child_wrapper() -> None:
        start.wait()
        for div in divs:
            div.wrap_children(turbohtml.Element("box"))  # boxes each div's children under the per-tree lock

    def sibling_wrapper() -> None:
        start.wait()
        for first in firsts:
            first.wrap_siblings(turbohtml.Element("run"))  # wraps the run after each div's first child

    _run(reader, child_wrapper, sibling_wrapper)
    assert body.serialize().startswith("<body>")  # still well-formed after the concurrent wrapping


def test_concurrent_annotation_export_is_memory_safe() -> None:
    doc = _doc(300)
    body = doc.find("body")
    assert body is not None
    children = list(body.children)
    rules = {"p": ["para"], "span": ["note"]}
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            text, spans = doc.to_annotated_text(rules)  # walks the tree under the per-tree lock
            turbohtml.annotation_surface(text, spans)  # pure transform over the snapshotted result
            turbohtml.annotation_tags(text, spans)

    def mutator() -> None:
        start.wait()
        for child in children:
            child.extract()

    _run(reader, mutator)
    text, spans = doc.to_annotated_text(rules)
    assert isinstance(turbohtml.annotation_surface(text, spans), dict)  # still usable after the churn


def test_concurrent_prune_and_reads_are_memory_safe() -> None:
    doc = _doc(400)
    body = doc.find("body")
    assert body is not None
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.find_all("span")  # walks the tree while prune snapshots and rewires it
            body.serialize()

    def selector() -> None:
        start.wait()
        for _ in range(200):
            doc.select("div p")

    def pruner() -> None:
        start.wait()
        body.prune("p")  # keeps every <p> (and its <div>), drops the <span> siblings

    _run(reader, selector, pruner)
    assert doc.find_all("span") == []  # prune removed every <span>, leaving the <p> subtrees
    assert len(doc.find_all("p")) == 400


def test_concurrent_remove_and_reads_are_memory_safe() -> None:
    doc = _doc(400)
    body = doc.find("body")
    assert body is not None
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.find_all("p")  # walks the tree while remove snapshots and detaches subtrees
            body.serialize()

    def selector() -> None:
        start.wait()
        for _ in range(200):
            doc.select("div span")

    def remover() -> None:
        start.wait()
        body.remove("span")  # drops every <span> subtree, keeps the <p> siblings

    _run(reader, selector, remover)
    assert doc.find_all("span") == []  # remove dropped every <span>
    assert len(doc.find_all("p")) == 400


def test_concurrent_strip_tags_and_reads_are_memory_safe() -> None:
    doc = _doc(400)
    body = doc.find("body")
    assert body is not None
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.find_all("p")  # walks the tree while strip_tags snapshots and relinks it
            body.serialize()

    def selector() -> None:
        start.wait()
        for _ in range(200):
            doc.select("div p")

    def stripper() -> None:
        start.wait()
        body.strip_tags("div")  # unwraps every <div>, lifting its <p>/<span> into the body

    _run(reader, selector, stripper)
    assert doc.find_all("div") == []  # every <div> was unwrapped
    assert len(doc.find_all("p")) == 400  # its children survived


def test_concurrent_main_content_extraction_is_memory_safe() -> None:
    paragraphs = "".join(
        f"<p>Paragraph {index}, a clause, with prose, holds enough words to score as real content here.</p>"
        for index in range(40)
    )
    doc = turbohtml.parse(
        f"<html><body><nav><a href='/'>Home</a></nav><article class=post>{paragraphs}</article></body></html>"
    )
    body = doc.find("body")
    assert body is not None
    children = list(body.children)
    start = threading.Barrier(3)

    def scorer() -> None:
        start.wait()
        for _ in range(200):
            doc.main_content()  # scores the whole tree in C while it is rewired

    def renderer() -> None:
        start.wait()
        for _ in range(200):
            assert isinstance(doc.main_text(), str)  # scores then renders the winner under the lock

    def extractor() -> None:
        start.wait()
        for child in children:
            child.extract()

    _run(scorer, renderer, extractor)
    assert isinstance(doc.main_text(), str)  # the tree is still walkable after the concurrent churn


def test_concurrent_classlist_edits_are_memory_safe() -> None:
    doc = _doc(300)
    body = doc.find("body")
    assert body is not None
    divs = body.find_all("div")
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.select("div.on")  # matches against the class value while it is rewritten
            for div in divs:
                div.has_class("on")

    def toggler() -> None:
        start.wait()
        for _ in range(200):
            for div in divs:
                div.toggle_class("on")  # rewrites the class attribute under the per-tree lock

    def adder() -> None:
        start.wait()
        for div in divs:
            div.add_class("seen").remove_class("seen")

    _run(reader, toggler, adder)
    for div in divs:
        assert isinstance(div.has_class("on"), bool)  # still readable after the concurrent churn


def test_concurrent_reads_and_fragment_splicing_is_memory_safe() -> None:
    doc = _doc(300)
    body = doc.find("body")
    assert body is not None
    divs = body.find_all("div")
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.find_all("p")  # walks the tree while the splices relink each div's children
            body.serialize()

    def inner_setter() -> None:
        start.wait()
        for div in divs:
            div.set_inner_html("<p>fresh</p><span>x</span>")  # clears then splices under the lock

    def adjacent_inserter() -> None:
        start.wait()
        for div in divs:
            div.insert_adjacent_html("beforeend", "<em>more</em>")  # appends a parsed fragment

    _run(reader, inner_setter, adjacent_inserter)
    assert body.serialize().startswith("<body>")  # still well-formed after the concurrent splicing


def test_concurrent_link_enumeration_and_resolve_is_memory_safe() -> None:
    anchors = "".join(f'<a href="p{index}/"><img src="i{index}.png"></a>' for index in range(300))
    doc = turbohtml.parse(f"<html><body>{anchors}</body></html>")
    body = doc.find("body")
    assert body is not None
    children = list(body.children)
    start = threading.Barrier(3)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.links()  # walks every link-bearing attribute while the tree is rewired

    def resolver() -> None:
        start.wait()
        for _ in range(200):
            doc.resolve_links("https://example.com/base/")  # rewrites values under the per-tree lock

    def extractor() -> None:
        start.wait()
        for child in children:
            child.extract()

    _run(reader, resolver, extractor)
    assert isinstance(doc.links(), list)  # the tree is still walkable after the concurrent churn


def test_concurrent_structured_data_and_extract_is_memory_safe() -> None:
    items = "".join(
        f'<div itemscope itemtype="https://schema.org/Thing"><meta itemprop="name" content="n{index}">'
        f'<script type="application/ld+json">{{"@type": "Thing", "id": {index}}}</script></div>'
        for index in range(300)
    )
    doc = turbohtml.parse(f"<html><head></head><body>{items}</body></html>")
    body = doc.find("body")
    assert body is not None
    children = list(body.children)
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            doc.structured_data()  # gathers json-ld/microdata/opengraph while the tree is rewired

    def extractor() -> None:
        start.wait()
        for child in children:
            child.extract()

    _run(reader, extractor)
    # the tree is still walkable after the concurrent churn
    assert isinstance(doc.structured_data(), turbohtml.StructuredData)


def test_concurrent_structured_data_reads_one_tree_snapshot() -> None:
    def markup(version: str) -> str:
        return (
            f'<script type="application/ld+json">{{"version": "{version}"}}</script>'
            f'<meta property="og:title" content="{version}"><meta name="dc.title" content="{version}">'
            f'<div itemscope><meta itemprop="version" content="{version}"></div>'
            f'<div typeof="Thing"><meta property="version" content="{version}"></div>'
        )

    doc = turbohtml.parse(f"<body>{markup('a')}</body>")
    body = doc.find("body")
    assert body is not None
    start = threading.Barrier(2)
    snapshots: list[frozenset[str]] = []

    def reader() -> None:
        start.wait()
        for _ in range(200):
            data = doc.structured_data()
            json_value = data.json_ld[0]
            assert isinstance(json_value, dict)
            json_version = json_value["version"]
            microdata_version = data.microdata[0].properties["version"][0]
            rdfa_version = data.rdfa[0].properties["version"][0]
            assert isinstance(json_version, str)
            assert isinstance(microdata_version, str)
            assert isinstance(rdfa_version, str)
            snapshots.append(
                frozenset({
                    json_version,
                    microdata_version,
                    data.opengraph["og:title"],
                    rdfa_version,
                    data.dublin_core["dc.title"],
                })
            )

    def mutator() -> None:
        start.wait()
        for index in range(200):
            body.set_inner_html(markup("a" if index % 2 == 0 else "b"))

    _run(reader, mutator)
    assert len(snapshots) == 200
    assert all(len(snapshot) == 1 for snapshot in snapshots)


def test_concurrent_table_reads_and_mutation_are_memory_safe() -> None:
    rows = "".join(
        f"<tr><td rowspan=2>r{index}</td><td>{index}</td></tr><tr><td>{index}b</td></tr>" for index in range(150)
    )
    doc = turbohtml.parse(f"<html><body><table>{rows}</table></body></html>")
    table = doc.find("table")
    assert table is not None
    cells = list(table.children)
    start = threading.Barrier(3)

    def rows_reader() -> None:
        start.wait()
        for _ in range(200):
            table.rows()  # builds the spanned grid while the mutator rewires the rows

    def tables_reader() -> None:
        start.wait()
        for _ in range(200):
            doc.tables()  # locates and snapshots every table under the per-tree lock

    def mutator() -> None:
        start.wait()
        for cell in cells:
            cell.extract()  # detaches each row group from the live table

    _run(rows_reader, tables_reader, mutator)
    assert isinstance(table.rows(), list)  # the tree is still walkable after the concurrent churn


@pytest.mark.parametrize(
    "use",
    [
        pytest.param(lambda selection, node: selection.set_start(node, 0), id="set-start"),
        pytest.param(lambda selection, node: selection.set_end(node, 0), id="set-end"),
        pytest.param(lambda selection, node: selection.set_start_before(node), id="set-start-before"),
        pytest.param(lambda selection, node: selection.set_start_after(node), id="set-start-after"),
        pytest.param(lambda selection, node: selection.set_end_before(node), id="set-end-before"),
        pytest.param(lambda selection, node: selection.set_end_after(node), id="set-end-after"),
        pytest.param(lambda selection, node: selection.select_node(node), id="select-node"),
        pytest.param(lambda selection, node: selection.select_node_contents(node), id="select-node-contents"),
        pytest.param(lambda selection, node: selection.compare_point(node, 0), id="compare-point"),
        pytest.param(lambda selection, node: selection.is_point_in_range(node, 0), id="is-point-in-range"),
        pytest.param(lambda selection, node: selection.intersects_node(node), id="intersects-node"),
        pytest.param(lambda selection, node: selection.insert_node(node), id="insert-node"),
        pytest.param(lambda selection, node: selection.surround_contents(node), id="surround-contents"),
    ],
)
def test_concurrent_range_use_of_a_moving_node_is_memory_safe(
    use: Callable[[turbohtml.Range, turbohtml.Element], object],
) -> None:
    node: Final = turbohtml.Element("b")
    source: Final = turbohtml.Element("section", children=[node])
    destinations: Final = [turbohtml.Element("main"), turbohtml.Element("aside")]
    start: Final = threading.Barrier(2)

    def user() -> None:
        start.wait()
        for _ in range(1_000):
            # each call reads the argument's tree handle and node, which a move rebinds together; the range lives in
            # another tree, so the point methods raise ValueError after the read
            with contextlib.suppress(ValueError):
                use(turbohtml.Range(turbohtml.Element("p", children=[turbohtml.Element("i")])), node)

    def mover() -> None:
        start.wait()
        for index in range(1_000):
            destinations[index % 2].append(source)

    _run(user, mover)
    assert destinations[1].children == (source,)


def test_concurrent_cross_tree_adoption_copies_one_source_state() -> None:
    document = turbohtml.parse(f"<section>{'<i></i>' * 50_000}<i id=target data-state=s></i></section>")
    source = document.find("section")
    assert source is not None
    target = document.select_one("#target")
    assert target is not None
    destination = turbohtml.Element("main")
    long_state = "L" * 4_096
    start = threading.Barrier(2)
    ready = threading.Event()
    done = threading.Event()

    def mutator() -> None:
        start.wait()
        target.attrs["data-state"] = "s"
        target.attrs["data-state"] = long_state
        ready.set()
        while not done.is_set():
            target.attrs["data-state"] = "s"
            target.attrs["data-state"] = long_state

    def adopter() -> None:
        start.wait()
        ready.wait()
        try:
            destination.append(source)
        finally:
            done.set()

    _run(mutator, adopter)
    adopted = destination.select_one("#target")
    assert adopted is not None
    assert adopted.attrs["data-state"] in {"s", long_state}


def test_concurrent_adoption_preserves_descendant_aliases() -> None:
    source = turbohtml.Element("section", children=[turbohtml.Element("b")])
    descendant = source.select_one("b")
    assert descendant is not None
    destinations = [turbohtml.Element("main"), turbohtml.Element("aside")]
    start = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(10_000):
            assert descendant.parent is not None
            descendant.attrs["seen"] = "yes"

    def mover() -> None:
        start.wait()
        for index in range(10_000):
            destinations[index % 2].append(source)

    _run(reader, mover)
    assert (destinations[1].select_one("b"), descendant.attr("seen")) == (descendant, "yes")


@pytest.mark.parametrize(
    ("read", "expected"),
    [
        pytest.param(lambda node: node.serialize(), '<section live="yes"><b>text</b></section>', id="serialize"),
        pytest.param(lambda node: len(node.select("b")), 1, id="select"),
        pytest.param(lambda node: len(Query(node).filter("section")), 1, id="query-filter"),
        pytest.param(lambda node: node.xpath("count(.//b)"), 1.0, id="xpath"),
        pytest.param(turbohtml.XPath("count(.//b)"), 1.0, id="compiled-xpath"),
        pytest.param(lambda node: len(Query([node, node])), 1, id="query-deduplication"),
        pytest.param(lambda node: len({node.children[0], node.children[0]}), 1, id="alias-equality"),
        pytest.param(lambda node: len(Query(node).children()), 1, id="query-children"),
        pytest.param(lambda node: len(Query(node).children().parent()), 1, id="query-parent"),
        pytest.param(lambda node: sanitize_node(node).text, "text", id="sanitize"),
        pytest.param(lambda node: check(node).valid, True, id="validate"),
        pytest.param(lambda node: len(list(node.descendants)), 2, id="descendants"),
        pytest.param(lambda node: node.attrs.copy(), {"live": "yes"}, id="attributes"),
        pytest.param(lambda node: len(node.find_all(text=lambda text: text == "text")), 1, id="callback"),
    ],
)
def test_concurrent_adoption_and_reads(
    read: Callable[[turbohtml.Element], object], expected: int | str | dict[str, str]
) -> None:
    source: Final = turbohtml.Element(
        "section", {"live": "yes"}, children=[turbohtml.Element("b", children=[turbohtml.Text("text")])]
    )
    destinations: Final = [turbohtml.Element("main"), turbohtml.Element("aside")]
    start: Final = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(1_000):
            assert read(source) == expected

    def mover() -> None:
        start.wait()
        for index in range(1_000):
            destinations[index % 2].append(source)

    _run(reader, mover)
    assert destinations[1].text == "text"


def test_concurrent_adoption_keeps_hash_stable() -> None:
    source: Final = turbohtml.Element(
        "section", {"live": "yes"}, children=[turbohtml.Element("b", children=[turbohtml.Text("text")])]
    )
    expected_hash: Final = hash(source)
    destinations: Final = [turbohtml.Element("main"), turbohtml.Element("aside")]
    start: Final = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(1_000):
            assert hash(source) == expected_hash

    def mover() -> None:
        start.wait()
        for index in range(1_000):
            destinations[index % 2].append(source)

    _run(reader, mover)
    assert destinations[1].text == "text"


def test_concurrent_range_construction_while_the_container_moves_is_memory_safe() -> None:
    node: Final = turbohtml.Element("b")
    source: Final = turbohtml.Element("section", children=[node])
    destinations: Final = [turbohtml.Element("main"), turbohtml.Element("aside")]
    start: Final = threading.Barrier(2)

    def builder() -> None:
        start.wait()
        for _ in range(2_000):
            # each reads the container's tree handle and node, which a move rebinds together
            turbohtml.Range(node, 0)
            turbohtml.StaticRange(node, 0, node, 0)

    def mover() -> None:
        start.wait()
        for index in range(2_000):
            destinations[index % 2].append(source)

    _run(builder, mover)
    assert destinations[1].children == (source,)


def test_concurrent_wraps_and_inserts_of_foreign_nodes_keep_the_tree_intact() -> None:
    markup = f"<div>{'<p><b>x</b></p>' * 200}</div>"
    root = turbohtml.parse(markup).find("div")
    assert root is not None
    paragraphs = root.find_all("p")
    start = threading.Barrier(4)

    def editor(offset: int) -> None:
        start.wait()
        for index in range(offset, offset + 2000):
            paragraph = paragraphs[index * 7 % len(paragraphs)]
            paragraph.wrap(turbohtml.Element("span")).unwrap()  # each foreign wrapper is copied into the tree
            paragraph.wrap_siblings(turbohtml.Element("span"), until=paragraph).unwrap()
            paragraph.wrap_children(turbohtml.Element("span")).unwrap()
            marker = turbohtml.Element("i")
            paragraph.insert_before(marker)
            marker.decompose()

    _run(*(lambda offset=offset: editor(offset) for offset in range(4)))
    assert root.serialize() == markup


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda node, item: node.insert_before(item, turbohtml.Text("t")), id="insert-before"),
        pytest.param(lambda node, item: node.insert_after(item, turbohtml.Text("t")), id="insert-after"),
        pytest.param(lambda node, item: node.replace_with(node, item), id="replace-with-self"),
        pytest.param(lambda node, item: node.insert(0, item), id="insert"),
        pytest.param(lambda node, item: node.extend([item, turbohtml.Text("t")]), id="extend"),
        pytest.param(lambda node, item: node.wrap(item), id="wrap"),
        pytest.param(lambda node, item: node.wrap_children(item), id="wrap-children"),
        pytest.param(lambda node, item: node.wrap_siblings(item), id="wrap-siblings"),
    ],
)
def test_concurrent_edit_with_foreign_arguments_while_the_node_moves(
    edit: Callable[[turbohtml.Element, turbohtml.Element], object],
) -> None:
    node: Final = turbohtml.Element("b")
    source: Final = turbohtml.Element("section", children=[node])
    destinations: Final = [turbohtml.Element("main"), turbohtml.Element("aside")]
    items: Final = [turbohtml.Element("i") for _ in range(300)]
    holder: Final = turbohtml.Element("div", children=items)
    pools: Final = [turbohtml.Element("ul"), turbohtml.Element("ol")]
    start: Final = threading.Barrier(3)

    def editor() -> None:
        start.wait()
        for item in items:
            # importing item waits for its contended tree, which suspends the lock on node's tree; the mover can then
            # take node into another tree before the edit resumes
            edit(node, item)

    def mover() -> None:
        start.wait()
        for index in range(len(items)):
            destinations[index % 2].append(source)

    def shuffler() -> None:
        start.wait()
        for index in range(len(items)):
            pools[index % 2].append(holder)  # rebinds every item still in holder to the other tree

    _run(editor, mover, shuffler)
    assert (destinations[1].children, holder.children) == ((source,), ())


@pytest.mark.parametrize("move_parent", [pytest.param(False, id="first"), pytest.param(True, id="parent")])
def test_concurrent_sibling_memo_and_adoption(*, move_parent: bool) -> None:
    children: Final = [turbohtml.Element("b") for _ in range(128)]
    parent: Final = turbohtml.Element("main", children=children)
    marker: Final = turbohtml.Element("i")
    destinations: Final = [turbohtml.Element("div"), turbohtml.Element("aside", children=[marker])]
    query: Final = Query(children[::2])
    stable: Final = set(children[1:])
    allowed: Final = {*children, marker}
    start: Final = threading.Barrier(2)

    def reader() -> None:
        start.wait()
        for _ in range(200):
            siblings = list(query.siblings())
            assert stable <= set(siblings) <= allowed
            assert len(siblings) == len(set(siblings))

    def mover() -> None:
        start.wait()
        for index in range(200):
            if move_parent:
                destinations[index % 2].append(parent)
            else:
                (parent if index % 2 else destinations[1]).append(children[0])

    _run(reader, mover)


def test_concurrent_extends_of_one_shared_child_keep_every_tree_intact() -> None:
    child: Final = turbohtml.Element("b", children=[turbohtml.Element("i")])
    parents: Final = [turbohtml.Element("p") for _ in range(4)]
    start: Final = threading.Barrier(len(parents))

    def mover(parent: turbohtml.Element) -> None:
        start.wait()
        for _ in range(2_000):
            parent.extend([child])  # another thread can move child while this import waits for its source tree

    _run(*(lambda parent=parent: mover(parent) for parent in parents))
    assert sorted(parent.serialize() for parent in parents) == ["<p></p>"] * 3 + ["<p><b><i></i></b></p>"]


def test_concurrent_traversal_and_observer_setup_while_the_root_moves_is_memory_safe() -> None:
    root: Final = turbohtml.Element("section", children=[turbohtml.Element("b")])
    destinations: Final = [turbohtml.Element("main"), turbohtml.Element("aside")]
    start: Final = threading.Barrier(2)

    def builder() -> None:
        start.wait()
        for _ in range(2_000):
            # each reads root's tree handle and node, which a move rebinds together
            turbohtml.NodeIterator(root).next_node()
            turbohtml.TreeWalker(root).first_child()
            MutationObserver().observe(root, child_list=True)

    def mover() -> None:
        start.wait()
        for index in range(2_000):
            destinations[index % 2].append(root)

    _run(builder, mover)
    assert destinations[1].children == (root,)
