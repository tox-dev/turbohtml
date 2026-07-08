"""Concurrent access to one shared tree must stay memory-safe under free-threading.

The ``_html`` extension declares ``Py_MOD_GIL_NOT_USED``, so it owns the thread
safety of its mutable tree. A reader and a mutator sharing a tree may produce a
stale result, but must never segfault (issue #84). Under the GIL build the
threads serialize; under a free-threaded build they run truly in parallel, so
these exercise the per-tree critical sections the operations take.

The free-threaded tox envs and the ThreadSanitizer job re-run this module under
``pytest --parallel-threads=auto --iterations=N`` (pytest-run-parallel), which
runs each test in one thread per core at once, multiplying the contention.
"""

from __future__ import annotations

import threading

import turbohtml


def _doc(divs: int) -> turbohtml.Document:
    body = "".join(f"<div><p>x{index}</p><span>s{index}</span></div>" for index in range(divs))
    return turbohtml.parse(f"<html><body>{body}</body></html>")


def _run(*targets: object) -> None:
    threads = [threading.Thread(target=target) for target in targets]  # ty: ignore[invalid-argument-type]
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


def test_concurrent_lazy_node_iterators_and_extract_is_memory_safe() -> None:
    doc = _doc(400)
    body = doc.find("body")
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
    doc = _doc(300)
    body = doc.find("body")
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
