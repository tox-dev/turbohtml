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
