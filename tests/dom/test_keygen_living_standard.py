from __future__ import annotations

import pytest

from turbohtml import Element, parse, parse_fragment


@pytest.mark.parametrize(
    ("markup", "parent_tag", "expected"),
    [
        pytest.param("<select><keygen>", "select", ["keygen"], id="select"),
        pytest.param("<keygen><span>", "body", ["keygen", "span"], id="body"),
    ],
)
def test_keygen_document_children(markup: str, parent_tag: str, expected: list[str]) -> None:
    parent = parse(markup).find(parent_tag)
    assert parent is not None
    assert [child.tag for child in parent.children if isinstance(child, Element)] == expected


def test_keygen_and_option_are_siblings_in_select_fragment() -> None:
    fragment = parse_fragment("<keygen><option>", "select")
    assert [child.tag for child in fragment.children if isinstance(child, Element)] == ["keygen", "option"]
