from __future__ import annotations

from typing import Final

import pytest

from turbohtml import XPath, parse_xml


@pytest.mark.parametrize("function", [pytest.param("re:test", id="exslt"), pytest.param("matches", id="xpath2")])
def test_regex_dynamic_patterns(function: str) -> None:
    document: Final = parse_xml(
        "<root>"
        + "".join(f'<n pattern="^{index}$">{index}</n><n pattern="^{index}$">miss</n>' for index in range(160))
        + "</root>"
    )
    assert document.xpath(f"count(//n[{function}(., @pattern)])") == 160


@pytest.mark.parametrize("flags", [pytest.param("", id="case-sensitive"), pytest.param("i", id="ignore-case")])
def test_regex_evaluations_keep_flags_separate(flags: str) -> None:
    expression: Final = XPath("count(//n[re:test(., $pattern, $flags)])")
    document: Final = parse_xml("<root><n>CAFÉ</n><n>café</n><n>tea</n></root>")
    assert [expression(document, pattern=pattern, flags=flags) for pattern in ("café", "tea", "café")] == (
        [2, 1, 2] if flags else [1, 1, 1]
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("re:test('10', 0)", True, id="numeric-pattern"),
        pytest.param("re:test('x', 'x', 0)", True, id="numeric-flags"),
        pytest.param("re:test('x', 'X', '')", False, id="empty-flags"),
    ],
)
def test_regex_coerces_arguments(expression: str, expected: bool) -> None:
    assert parse_xml("<root/>").xpath(expression) is expected
