from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element, parse

if TYPE_CHECKING:
    from types import SimpleNamespace


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("//i[2]/@id", ["b"], id="literal"),
        pytest.param("//i[1.5]/@id", [], id="fraction"),
        pytest.param("//i[0]/@id", [], id="zero"),
        pytest.param("//i[999999999999999999999]/@id", [], id="large"),
        pytest.param("//i[last()]/@id", ["c"], id="last"),
        pytest.param("//i[2][last()]/@id", ["b"], id="literal-last"),
        pytest.param("//i[last()][2]/@id", [], id="last-literal"),
        pytest.param("//i[@id='c']/preceding-sibling::i[1]/@id", ["b"], id="reverse-first"),
        pytest.param("//i[@id='c']/preceding-sibling::i[last()]/@id", ["a"], id="reverse-last"),
        pytest.param("(//i[@id='c']/preceding-sibling::i)[last()]/@id", ["b"], id="sorted-filter"),
        pytest.param("//missing[last()]/@id", [], id="empty-last"),
        pytest.param("//missing[1]/@id", [], id="empty-literal"),
    ],
)
def test_xpath_positional_predicate(expression: str, expected: list[str]) -> None:
    root: Final = parse('<main><i id="a"></i><i id="b"></i><i id="c"></i></main>')
    assert root.xpath(expression) == expected


@pytest.mark.parametrize("predicate", ["1", "last()", "0", "1.5"])
@pytest.mark.parametrize("smart", [False, True], ids=["plain", "smart"])
def test_xpath_positional_attribute_snapshot(predicate: str, *, smart: bool) -> None:
    root: Final = Element("main", {"first": "a", "middle": "b", "last": "c"})
    target: Final = Element("section")

    def move(_context: SimpleNamespace) -> bool:
        target.append(root)
        root.attrs.clear()
        return True

    result: Final = root.xpath(f"@*[{predicate}][move()]", extensions={(None, "move"): move}, smart_strings=smart)
    assert result == {"1": ["a"], "last()": ["c"], "0": [], "1.5": []}[predicate]


@pytest.mark.parametrize(
    ("name", "expected"),
    [pytest.param("custom:last", ["a"], id="extension"), pytest.param("last", ["b"], id="builtin")],
)
def test_xpath_positional_extension_resolution(name: str, expected: list[str]) -> None:
    root: Final = parse('<main><i id="a"></i><i id="b"></i></main>')

    def last(_context: SimpleNamespace) -> int:
        return 1

    assert root.xpath(f"//i[{name}()]/@id", extensions={(None, name): last}) == expected


def test_xpath_positional_last_arity() -> None:
    with pytest.raises(ValueError, match=r"last\(\) takes 0 arguments, got 1"):
        Element("main").xpath("self::*[last(1)]")
