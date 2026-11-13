from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeAlias

import pytest

import turbohtml
from turbohtml import Node, XPath

if TYPE_CHECKING:
    from collections.abc import Callable

_STUBS: Final[Path] = Path(__file__).parents[3] / "src" / "turbohtml" / "_stubs"
_NAMES: Final[MappingProxyType[str, type]] = MappingProxyType({"Node": Node, "str": str, "float": float, "bool": bool})
_XPathValue: TypeAlias = "list[Node | str] | float | bool | str | Node | Iterator[Node | str | float | bool] | None"
_DOCUMENT: Final[turbohtml.Document] = turbohtml.parse("<p>a</p><!--c--><p>b</p><?pi x?>")
_EXPRESSIONS: Final[list[pytest.ParameterSet]] = [
    pytest.param("count(//p)", id="number"),
    pytest.param("boolean(//p)", id="boolean"),
    pytest.param("string(//p)", id="string"),
    pytest.param("//p", id="elements"),
    pytest.param("//p/text()", id="text-values"),
    pytest.param("//comment()", id="comments"),
    pytest.param("//processing-instruction()", id="processing-instructions"),
    pytest.param("/", id="document"),
    pytest.param("//p/@missing", id="empty-node-set"),
]


def _declared_return(stub: str, owner: str, method: str) -> ast.expr:
    module = ast.parse((_STUBS / stub).read_text(encoding="utf-8"))
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == owner)
    func = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method)
    assert func.returns is not None
    return func.returns


def _conforms(value: _XPathValue, annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.BinOp):
        return _conforms(value, annotation.left) or _conforms(value, annotation.right)
    if isinstance(annotation, ast.Constant):
        return value is None
    if isinstance(annotation, ast.Subscript):
        assert isinstance(annotation.value, ast.Name)
        container = {"list": list, "Iterator": Iterator}[annotation.value.id]
        return isinstance(value, container) and all(_conforms(item, annotation.slice) for item in value)
    assert isinstance(annotation, ast.Name)
    return isinstance(value, _NAMES[annotation.id])


@pytest.mark.parametrize(
    ("stub", "owner", "method", "evaluate"),
    [
        pytest.param("dom.pyi", "Node", "xpath", _DOCUMENT.xpath, id="xpath"),
        pytest.param("dom.pyi", "Node", "xpath_one", _DOCUMENT.xpath_one, id="xpath_one"),
        pytest.param("dom.pyi", "Node", "xpath_iter", _DOCUMENT.xpath_iter, id="xpath_iter"),
        pytest.param("query.pyi", "XPath", "__call__", lambda expr: XPath(expr)(_DOCUMENT), id="XPath-call"),
    ],
)
@pytest.mark.parametrize("expression", _EXPRESSIONS)
def test_xpath_result_matches_stub(
    stub: str, owner: str, method: str, evaluate: Callable[[str], _XPathValue], expression: str
) -> None:
    assert _conforms(evaluate(expression), _declared_return(stub, owner, method))
