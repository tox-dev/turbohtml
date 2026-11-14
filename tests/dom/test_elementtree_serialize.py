from __future__ import annotations

from typing import Final, cast

import pytest

from turbohtml.etree import Element, fragment_fromstring, fromstring, tostring


@pytest.mark.parametrize(
    ("encoding", "with_tail", "expected"),
    [
        pytest.param(None, True, b"<p>caf&#233;</p>tail", id="ascii"),
        pytest.param("utf-8", True, b"<p>caf\xc3\xa9</p>tail", id="utf8"),
        pytest.param("unicode", False, "<p>café</p>", id="without-tail"),
        pytest.param(str, True, "<p>café</p>tail", id="string-type"),
    ],
)
def test_element_tree_serialization_encoding(
    encoding: str | type[str] | None, expected: str | bytes, *, with_tail: bool
) -> None:
    tree: Final = fragment_fromstring("<p>café</p>tail")
    assert tostring(tree, encoding=encoding, with_tail=with_tail) == expected


def test_element_tree_iso_2022_text_writes_shift_codes_as_references() -> None:
    # as bytes they would switch the reader's decoder state; "xmlcharrefreplace" spells what the codec cannot encode
    tree: Final = fragment_fromstring("<p>\x0e\x0f\x1b</p>")
    assert tostring(tree, encoding="iso-2022-jp", method="text") == b"&#14;&#15;&#27;"
    assert tostring(tree, encoding="shift_jis", method="text") == b"\x0e\x0f\x1b"


def test_element_tree_rejects_non_string_encoding() -> None:
    with pytest.raises(TypeError):
        tostring(Element("p"), encoding=cast("str", 42))


def test_element_tree_own_text_excludes_descendant_text() -> None:
    tree: Final = fromstring("<div><p>child</p></div>")
    assert (tree.text, tree[0].text) == (None, "child")
