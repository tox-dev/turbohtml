from __future__ import annotations

import pytest

from turbohtml import parse_fragment
from turbohtml._html import _links, _rewrite_links


def test_links_rejects_a_non_node() -> None:
    with pytest.raises(TypeError, match="turbohtml element"):
        _links("not a node")  # ty: ignore[invalid-argument-type]


def test_links_rejects_wrong_argument_count() -> None:
    with pytest.raises(TypeError):
        _links()  # ty: ignore[missing-argument]


def test_rewrite_links_rejects_a_non_node() -> None:
    with pytest.raises(TypeError, match="turbohtml element"):
        _rewrite_links("not a node", str)  # ty: ignore[invalid-argument-type]


def test_rewrite_links_rejects_wrong_argument_count() -> None:
    with pytest.raises(TypeError):
        _rewrite_links(parse_fragment("<a href=a>x</a>"))  # ty: ignore[missing-argument]
