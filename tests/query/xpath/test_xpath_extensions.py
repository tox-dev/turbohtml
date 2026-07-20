"""User extension functions via ``extensions=``, like lxml.

A custom function is registered under a ``(namespace, name)`` key and called as
``name(...)`` (the un-namespaced ``(None, name)`` form, which needs no namespace
mapping). It receives a context whose ``context_node`` is the current element,
then the evaluated arguments: a node-set arrives as a list, a string/number/bool
as the matching Python scalar. The return may be a str, number, or bool, or (issue
#265) an element or an iterable of elements that becomes a node-set feeding later
path steps and predicates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

import turbohtml
from turbohtml import Element

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from types import SimpleNamespace

HTML = "<html><body><a href='/x'>one</a><a href='/y'>two</a></body></html>"


def count_nodes(_context: SimpleNamespace, nodes: list[object]) -> float:
    return float(len(nodes))


def shout(_context: SimpleNamespace, text: str) -> str:
    return text.upper()


def echo(_context: SimpleNamespace, value: float | bool) -> float | bool:  # ruff:ignore[boolean-type-hint-positional-argument]  # positional by convention
    return value


def context_tag(context: SimpleNamespace) -> str:
    return context.context_node.tag


EXTENSIONS: dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]] = {
    (None, "count_nodes"): count_nodes,
    (None, "shout"): shout,
    (None, "echo"): echo,
    (None, "context_tag"): context_tag,
}


@pytest.fixture
def doc() -> turbohtml.Node:
    return turbohtml.parse(HTML)


def test_nodeset_argument_arrives_as_a_list(doc: turbohtml.Node) -> None:
    assert doc.xpath("count_nodes(//a)", extensions=EXTENSIONS) == pytest.approx(2.0)


def test_string_argument_and_string_return(doc: turbohtml.Node) -> None:
    assert doc.xpath("shout(string(//a[1]/@href))", extensions=EXTENSIONS) == "/X"


def test_number_argument_round_trips(doc: turbohtml.Node) -> None:
    assert doc.xpath("echo(40 + 2)", extensions=EXTENSIONS) == pytest.approx(42.0)


def test_boolean_argument_round_trips(doc: turbohtml.Node) -> None:
    assert doc.xpath("echo(true())", extensions=EXTENSIONS) is True


def test_context_node_is_the_current_element(doc: turbohtml.Node) -> None:
    nodes = [n for n in doc.xpath("//a[context_tag()='a']", extensions=EXTENSIONS) if isinstance(n, Element)]
    assert [n.text for n in nodes] == ["one", "two"]


def test_extension_in_a_predicate(doc: turbohtml.Node) -> None:
    nodes = [n for n in doc.xpath("//a[count_nodes(.) = 1]", extensions=EXTENSIONS) if isinstance(n, Element)]
    assert [n.text for n in nodes] == ["one", "two"]


def test_extension_alongside_a_variable(doc: turbohtml.Node) -> None:
    assert doc.xpath("shout($s)", s="hi", extensions=EXTENSIONS) == "HI"


def test_extension_through_xpath_one(doc: turbohtml.Node) -> None:
    assert doc.xpath_one("count_nodes(//a)", extensions=EXTENSIONS) == pytest.approx(2.0)


def test_unknown_function_with_extensions_raises(doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'nope'"):
        doc.xpath("nope()", extensions=EXTENSIONS)


def test_unknown_function_without_extensions_raises(doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'count_nodes'"):
        doc.xpath("count_nodes(//a)")


def test_empty_extensions_dict_registers_nothing(doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'count_nodes'"):
        doc.xpath("count_nodes(//a)", extensions={})


def test_none_extensions_is_the_same_as_omitting_it(doc: turbohtml.Node) -> None:
    assert isinstance(doc.xpath("//a", extensions=None), list)


def test_extension_that_raises_propagates(doc: turbohtml.Node) -> None:
    with pytest.raises(ZeroDivisionError):
        doc.xpath("boom()", extensions={(None, "boom"): lambda _context: 1 / 0})


def test_extension_returning_a_non_scalar_is_a_type_error(doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="extension result must be"):
        doc.xpath("bad()", extensions={(None, "bad"): lambda _context: [1, 2]})  # ty: ignore[invalid-argument-type]  # non-scalar return on purpose


def test_extensions_must_be_a_dict(doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="extensions must be a dict"):
        doc.xpath("//a", extensions="not a dict")  # ty: ignore[invalid-argument-type]  # wrong type on purpose


def test_extension_receiving_an_element_from_a_nodeset(doc: turbohtml.Node) -> None:
    # the marshaled node-set holds Element objects the extension can navigate.
    def first_text(_context: SimpleNamespace, nodes: list[Element]) -> str:
        return nodes[0].text

    assert doc.xpath("first_text(//a)", extensions={(None, "first_text"): first_text}) == "one"


def first_node(_context: SimpleNamespace, nodes: list[Element]) -> Element:
    return nodes[0]


def first_two(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return nodes[:2]


def each(_context: SimpleNamespace, nodes: list[Element]) -> Iterator[Element]:
    yield from nodes


def all_nodes(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return nodes


def empty(_context: SimpleNamespace, _nodes: list[Element]) -> list[Element]:
    return []


NODESET_EXTENSIONS: dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]] = {
    (None, "first_node"): first_node,
    (None, "first_two"): first_two,
    (None, "each"): each,
    (None, "all_nodes"): all_nodes,
    (None, "empty"): empty,
}


@pytest.fixture
def big_doc() -> turbohtml.Node:
    return turbohtml.parse("<ul>" + "".join(f"<li>{index}</li>" for index in range(12)) + "</ul>")


def _texts(result: object) -> list[str]:
    assert isinstance(result, list)
    return [node.text if isinstance(node, Element) else str(node) for node in result]


@pytest.mark.parametrize(
    ("fixture", "expression", "expected"),
    [
        pytest.param("doc", "first_node(//a)", ["one"], id="single-element-is-a-node-set"),
        pytest.param("doc", "first_two(//a)", ["one", "two"], id="list-of-elements-is-a-node-set"),
        pytest.param("doc", "each(//a)", ["one", "two"], id="generator-of-elements-is-a-node-set"),
        pytest.param("doc", "empty(//a)", [], id="empty-iterable-is-an-empty-node-set"),
        pytest.param("doc", "first_node(//a)/text()", ["one"], id="node-set-feeds-a-later-path-step"),
        pytest.param("doc", "first_two(//a)[2]", ["two"], id="node-set-feeds-a-predicate"),
        pytest.param(
            "big_doc", "all_nodes(//li)", [str(index) for index in range(12)], id="many-elements-grow-the-node-set"
        ),
    ],
)
def test_extension_result_becomes_a_node_set(
    request: pytest.FixtureRequest, *, fixture: str, expression: str, expected: list[str]
) -> None:
    page = cast("turbohtml.Node", request.getfixturevalue(fixture))
    assert _texts(page.xpath(expression, extensions=NODESET_EXTENSIONS)) == expected


def test_extension_returning_an_integer_stays_a_number(doc: turbohtml.Node) -> None:
    assert doc.xpath("five()", extensions={(None, "five"): lambda _context: 5}) == pytest.approx(5.0)


def return_none(_context: SimpleNamespace) -> Element:
    return cast("Element", None)


def mixed(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [nodes[0], cast("Element", 123)]


def raise_partway(_context: SimpleNamespace, nodes: list[Element]) -> Iterator[Element]:
    yield nodes[0]
    msg = "boom"
    raise RuntimeError(msg)


_OTHER_DOCUMENT = turbohtml.parse("<p>elsewhere</p>")
_STRANGER = next(node for node in _OTHER_DOCUMENT.xpath("//p") if isinstance(node, Element))


def steal(_context: SimpleNamespace) -> Element:
    return _STRANGER


@pytest.mark.parametrize(
    ("function", "expression", "exception", "match"),
    [
        pytest.param(
            return_none, "return_none()", TypeError, "extension result must be", id="none-result-is-a-type-error"
        ),
        pytest.param(
            mixed, "mixed(//a)", TypeError, "extension result must be", id="non-element-in-iterable-is-a-type-error"
        ),
        pytest.param(
            steal, "steal()", ValueError, "different document", id="foreign-document-element-is-a-value-error"
        ),
        pytest.param(
            raise_partway, "raise_partway(//a)", RuntimeError, "boom", id="iterable-that-raises-partway-propagates"
        ),
    ],
)
def test_extension_result_marshaling_is_rejected(
    doc: turbohtml.Node,
    *,
    function: Callable[..., str | float | bool | Element | Iterable[Element]],
    expression: str,
    exception: type[Exception],
    match: str,
) -> None:
    name = expression[: expression.index("(")]
    with pytest.raises(exception, match=match):
        doc.xpath(expression, extensions={(None, name): function})
