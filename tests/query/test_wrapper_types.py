from __future__ import annotations

from typing import Final, cast

import pytest

from turbohtml import Element, parse
from turbohtml.query import Query


@pytest.mark.parametrize("operation", ["find", "indexed", "css", "xpath", "parent"])
def test_element_result_types_after_adoption(operation: str) -> None:
    source: Final = parse('<section><a data-x="yes"><b></b></a><a data-x="yes"><b></b></a></section>')
    section: Final = source.select("section")[0]
    expected: Final = section.select("a")
    target: Final = parse("<main></main>")
    target.select("main")[0].append(section)
    if operation == "find":
        result = target.find_all(attrs={"data-x": True})
    elif operation == "indexed":
        result = target.find_all("a")
    elif operation == "css":
        result = target.select("a")
    elif operation == "xpath":
        result = cast("list[Element]", target.xpath("//a"))
    else:
        result = list(Query(target.select("b")).parent())
    assert [(type(node), node) for node in result] == [(Element, node) for node in expected]
