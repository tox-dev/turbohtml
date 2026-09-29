from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import Element, Text, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


@pytest.mark.parametrize("tags", [None, "*", ["*"], {"*": True}], ids=["none", "star", "list", "mapping"])
def test_iter_elements_skips_non_elements(tags: str | list[str] | dict[str, bool] | None) -> None:
    root: Final = Element("main", children=[Text("before"), Element("p"), Text("after")])
    assert [node.tag for node in root.iter_elements(tags)] == ["p"]


def test_iter_elements_filters_eagerly() -> None:
    root: Final = Element("main", children=[Element("p"), Element("li"), Element("p")])
    tags: Final = iter(["p", "li"])
    result: Final = root.iter_elements(tags, include_self=True)
    assert [node.tag for node in result] == ["p", "li", "p"]


def test_iter_elements_matches_known_and_custom_tags() -> None:
    root: Final = Element("main", children=[Element("p"), Element("widget"), Element("li")])
    assert [node.tag for node in root.iter_elements(("p", "widget"))] == ["p", "widget"]


def test_iter_elements_snapshots_tag_list() -> None:
    root: Final = Element("main", children=[Element("p"), Element("li")])
    tags: Final = ["p"]
    result: Final = root.iter_elements(tags)
    tags.append("li")
    assert [node.tag for node in result] == ["p"]


def test_iter_elements_empty_walk() -> None:
    assert list(Element("main").iter_elements()) == []


def test_iter_elements_includes_root() -> None:
    root: Final = Element("main", children=[Element("p")])
    assert [node.tag for node in root.iter_elements("main", include_self=True)] == ["main"]


def test_iter_elements_keeps_prefetched_sibling() -> None:
    first: Final = Element("li")
    second: Final = Element("li")
    root: Final = Element("ul", children=[first, second])
    iterator: Final = root.iter_elements("li")
    assert next(iterator) == first
    holder: Final = Element("section")
    holder.append(second)
    assert next(iterator) == second


def test_iter_elements_keeps_renamed_pending_node() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    iterator: Final = Element("main", children=[first, second]).iter_elements("p")
    assert next(iterator) == first
    second.tag = "aside"
    assert list(iterator) == [second]


def test_iter_elements_skips_insertion_before_pending() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    first.insert_after(Element("p"))
    assert list(iterator) == [second]


def test_iter_elements_stops_at_detached_boundary() -> None:
    child: Final = Element("p")
    first: Final = Element("section", children=[child])
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    Element("holder").append(first)
    assert next(iterator) == child
    assert list(iterator) == []


def test_iter_elements_stops_after_extracted_pending_node() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    second.extract()
    assert list(iterator) == [second]


def test_iter_elements_stops_after_pending_leaves_root() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("section", children=[first, second])
    holder: Final = Element("aside")
    Element("main", children=[root, holder])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    holder.append(second)
    assert list(iterator) == [second]


def test_iter_elements_excludes_new_siblings_of_moved_pending() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    tail: Final = Element("p")
    root: Final = Element("section", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    Element("aside", children=[second, tail])
    assert list(iterator) == [second]


def test_iter_elements_crosses_nested_following_sibling() -> None:
    nested: Final = Element("p")
    following: Final = Element("p")
    root: Final = Element("main", children=[Element("section", children=[nested]), following])
    assert list(root.iter_elements("p")) == [nested, following]


def test_iter_elements_walks_detached_pending_subtree() -> None:
    first: Final = Element("p")
    child: Final = Element("p")
    second: Final = Element("section", children=[child])
    root: Final = Element("main", children=[first, second, Element("p")])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    Element("holder").append(second)
    assert list(iterator) == [second, child]


def test_iter_elements_follows_moved_root() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    Element("holder").append(root)
    assert next(iterator) == second


def test_iter_elements_sees_insertions_after_pending() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    third: Final = Element("p")
    second.insert_after(third)
    assert list(iterator) == [second, third]


def test_iter_elements_preserves_xml_case() -> None:
    root: Final = parse_xml("<Root><item/><Item/></Root>").root
    assert root is not None
    assert [node.tag for node in root.iter_elements("Item")] == ["Item"]


def test_iter_elements_matches_xml_known_tag() -> None:
    root: Final = parse_xml("<Root><p/><P/></Root>").root
    assert root is not None
    assert [node.tag for node in root.iter_elements("p")] == ["p"]


def test_iter_elements_matches_unicode_custom_tag() -> None:
    root: Final = parse_xml("<Root><é/><e/></Root>").root
    assert root is not None
    assert [node.tag for node in root.iter_elements("é")] == ["é"]


def test_iter_elements_surrogate_tag_has_no_match() -> None:
    assert list(Element("main", children=[Element("p")]).iter_elements("\ud800")) == []


def test_iter_elements_concurrent_next_yields_each_once() -> None:
    nodes: Final = [Element("p") for _ in range(200)]
    iterator: Final = Element("main", children=nodes).iter_elements("p")

    def consume() -> list[Element]:
        return list(iterator)

    with ThreadPoolExecutor(max_workers=4) as executor:
        found: Final = [node for future in (executor.submit(consume) for _ in range(4)) for node in future.result()]
    assert set(found) == set(nodes)
    assert len(found) == len(nodes)


def test_iter_elements_rejects_non_string_tags() -> None:
    root: Final = Element("main")
    with pytest.raises(TypeError, match="only str"):
        root.iter_elements(cast("Iterable[str]", [1]))


def test_iter_elements_rejects_non_iterable_tags() -> None:
    with pytest.raises(TypeError):
        Element("main").iter_elements(cast("Iterable[str]", 1))


def test_iter_elements_rejects_extra_arguments() -> None:
    with pytest.raises(TypeError):
        cast("Callable[..., object]", Element("main").iter_elements)(None, None, None)
