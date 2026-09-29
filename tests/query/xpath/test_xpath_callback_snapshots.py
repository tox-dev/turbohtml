from __future__ import annotations

from typing import TYPE_CHECKING, Final
from weakref import finalize

import pytest

from turbohtml import Element, Text, XPath, XPathString, parse

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import SimpleNamespace


@pytest.mark.parametrize("compiled", [False, True], ids=["method", "compiled"])
@pytest.mark.parametrize("smart", [False, True], ids=["plain", "smart"])
@pytest.mark.parametrize("change", ["replace", "remove", "readd", "unrelated"])
def test_xpath_callback_attribute_union(*, compiled: bool, smart: bool, change: str) -> None:
    source: Final = Element("section", {"id": "old", "other": "value"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        if change in {"remove", "readd"}:
            del source.attrs["id"]
        if change in {"replace", "readd"}:
            source.attrs["id"] = "new"
        if change == "unrelated":
            source.attrs["other"] = "changed"
        return []

    extensions: Final[dict[tuple[str | None, str], Callable[..., list[Element]]]] = {(None, "mutate"): mutate}
    expression: Final = "@id | mutate() | @id"
    result: Final = (
        XPath(expression, extensions=extensions, smart_strings=smart)(source)
        if compiled
        else source.xpath(expression, extensions={(None, "mutate"): mutate}, smart_strings=smart)
    )
    assert result == ["old"]
    if smart:
        assert isinstance(result, list)
        assert isinstance(result[0], XPathString)
        assert (result[0].getparent(), result[0].attrname) == (source, "id")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("name((@id | mutate())[1])", "id"),
        ("str:concat(@id | mutate())", "old"),
        ("set:distinct(@id | mutate())", ["old"]),
        ("set:intersection(@id | mutate(), @id)", ["old"]),
        ("set:difference(@id | mutate(), @id)", []),
        ("@id = string(mutate())", False),
    ],
)
def test_xpath_callback_attribute_consumers(expression: str, *, expected: str | bool | list[str]) -> None:
    source: Final = Element("section", {"id": "old"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        source.attrs["id"] = "new"
        return []

    assert source.xpath(expression, extensions={(None, "mutate"): mutate}) == expected


def test_xpath_callback_translate_uses_prior_attribute_value() -> None:
    source: Final = Element("section", {"id": "old"})

    def mutate(_context: SimpleNamespace) -> str:
        assert source.xpath("string(@id)") == "old"
        source.attrs["id"] = "new"
        return "o"

    assert source.xpath("translate(@id, mutate(), 'O')", extensions={(None, "mutate"): mutate}) == "Old"


@pytest.mark.parametrize("smart", [False, True], ids=["plain", "smart"])
def test_xpath_callback_adopted_pending_owner(*, smart: bool) -> None:
    source: Final = Element("section", {"id": "old"}, children=[Element("b")])
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    result: Final = source.xpath(". | @id | adopt()", extensions={(None, "adopt"): adopt}, smart_strings=smart)
    assert isinstance(result, list)
    assert result[0] == source
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert target.select("section[live=yes]") == [source]
    assert result[1] == "old"
    if smart:
        assert isinstance(result[1], XPathString)
        assert result[1].getparent() == source


@pytest.mark.parametrize("size", [3, 24])
@pytest.mark.parametrize("operation", ["set:intersection", "set:difference"])
def test_xpath_callback_attribute_membership(size: int, operation: str) -> None:
    source: Final = Element("section", {f"attr{index}": str(index) for index in range(size)})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        for index in range(size):
            source.attrs[f"attr{index}"] = "changed"
        return []

    result: Final = source.xpath(f"{operation}(@* | mutate(), @*)", extensions={(None, "mutate"): mutate})
    assert result == ([str(index) for index in range(size)] if operation == "set:intersection" else [])


def test_xpath_callback_preserves_adopted_context() -> None:
    source: Final = Element("section")
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    def inspect(context: SimpleNamespace) -> bool:
        context.context_node.attrs["live"] = "yes"
        return context.context_node == source and target.select("section[live=yes]") == [source]

    assert (
        source.xpath("count(. | adopt()) + inspect()", extensions={(None, "adopt"): adopt, (None, "inspect"): inspect})
        == 2
    )


def test_xpath_callback_attribute_ordinal_collision() -> None:
    source: Final = Element("section", {"old": "first"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        del source.attrs["old"]
        source.attrs["new"] = "second"
        return []

    assert source.xpath("@* | mutate() | @*", extensions={(None, "mutate"): mutate}) == ["first", "second"]


def test_xpath_callback_shrinks_parsed_attributes() -> None:
    source: Final = parse('<section a="one" b="two" c="three" d="four"></section>').select("section")[0]

    def mutate(_context: SimpleNamespace) -> list[Element]:
        for name in ("a", "b", "c", "d"):
            del source.attrs[name]
        source.attrs["new"] = "changed"
        return []

    assert source.xpath("@* | mutate()", extensions={(None, "mutate"): mutate}) == ["one", "two", "three", "four"]


def test_xpath_callback_result_finalizer_adopts_node() -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    assert isinstance(child, Element)
    target: Final = Element("main")

    def produce(_context: SimpleNamespace) -> Iterator[Element]:
        result = (node for node in (child,))
        finalize(result, target.append, child)
        return result

    result: Final = source.xpath("produce()", extensions={(None, "produce"): produce})
    assert isinstance(result, list)
    assert result[0] == child
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert child.attrs["live"] == "yes"


def test_xpath_callback_preserves_text_parent() -> None:
    source: Final = Element("section", children=[Text("body")])
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    result: Final = source.xpath(
        "text() | adopt() | empty()", extensions={(None, "adopt"): adopt, (None, "empty"): empty}, smart_strings=True
    )
    assert isinstance(result, list)
    assert isinstance(result[0], XPathString)
    result[0].getparent().attrs["live"] = "yes"
    assert (str(result[0]), target.select("section[live=yes]")) == ("body", [source])


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("set:leading(@*, .)", []),
        ("@*[position() > 1 and keep()]", ["two", "three"]),
        ("@*/../@*", ["one", "two", "three"]),
    ],
)
def test_xpath_callback_attribute_paths(expression: str, expected: list[str]) -> None:
    source: Final = Element("section", {"first": "one", "second": "two", "third": "three"})

    def keep(_context: SimpleNamespace) -> bool:
        return True

    assert source.xpath(expression, extensions={(None, "keep"): keep}) == expected


def test_xpath_callback_adopts_variable_node() -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    assert isinstance(child, Element)
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(child)
        return []

    result: Final = source.xpath("adopt() | $node", extensions={(None, "adopt"): adopt}, node=child)
    assert isinstance(result, list)
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert target.select("b[live=yes]") == [child]


def test_xpath_callback_sparse_attribute_union() -> None:
    source: Final = Element("section", {f"data-item-{index}": str(index) for index in range(48)})
    for index in range(48):
        if index % 16:
            del source.attrs[f"data-item-{index}"]

    def mutate(_context: SimpleNamespace) -> list[Element]:
        for name in source.attrs:
            source.attrs[name] = "changed"
        return []

    assert source.xpath("@* | mutate() | @*", extensions={(None, "mutate"): mutate}) == ["0", "16", "32"]


@pytest.mark.parametrize("expression", ["@second | empty() | @first", "@first | empty() | @second"])
def test_xpath_callback_attribute_union_order(expression: str) -> None:
    source: Final = Element("section", {"first": "one", "second": "two"})

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    assert source.xpath(expression, extensions={(None, "empty"): empty}) == ["one", "two"]


def test_xpath_callback_reuses_attribute_name() -> None:
    source: Final = Element("section", {"new": "placeholder", "old": "first"})
    del source.attrs["new"]

    def mutate(_context: SimpleNamespace) -> list[Element]:
        del source.attrs["old"]
        source.attrs["new"] = "second"
        return []

    assert source.xpath("@* | mutate() | @*", extensions={(None, "mutate"): mutate}) == ["second", "first"]


def test_xpath_callback_mixed_attribute_namespace() -> None:
    source: Final = Element("section", {"id": "value"})

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    assert source.xpath("@id | namespace::* | empty()", extensions={(None, "empty"): empty}) == [
        "value",
        "http://www.w3.org/XML/1998/namespace",
    ]


@pytest.mark.parametrize(
    ("expression", "expected", "moved"),
    [
        pytest.param("@id[self::node()[move()]]", ["old"], True, id="attribute-self"),
        pytest.param("@id[parent::section[move()]]", ["old"], True, id="attribute-parent"),
        pytest.param("@id[child::node()[move()]]", [], False, id="empty-first-step"),
    ],
)
def test_xpath_callback_initial_attribute_context(expression: str, expected: list[str], *, moved: bool) -> None:
    source: Final = Element("section", {"id": "old"})
    target: Final = Element("main")

    def move(_context: SimpleNamespace) -> bool:
        target.append(source)
        del source.attrs["id"]
        return True

    result: Final = source.xpath(expression, extensions={(None, "move"): move})
    assert (result, tuple(target.children)) == (expected, (source,) if moved else ())
