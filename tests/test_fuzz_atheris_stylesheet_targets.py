from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fuzz.atheris_stylesheet_targets import stylesheet_observation, stylesheet_targets
from fuzz.atheris_targets import owner_inventory

if TYPE_CHECKING:
    from fuzz.atheris_registry import Target


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(b"", "", id="empty"),
        pytest.param(b"p { color: #ff0000; margin: 0px; }", "p{color:red;margin:0}", id="sheet"),
        pytest.param('p { content: "水😀"; }'.encode(), 'p{content:"水😀"}', id="unicode"),
        pytest.param(b'p { content: "</style>"; color: red; }', 'p{content:"</style>";color:red}', id="html-boundary"),
    ],
)
def test_stylesheet_consumer_exact_source(source: bytes, expected: str) -> None:
    assert stylesheet_observation(source) == expected


@pytest.mark.parametrize("target", stylesheet_targets(), ids=lambda target: target.name)
def test_stylesheet_callback_rejects_invalid_utf8(target: Target) -> None:
    with pytest.raises(UnicodeDecodeError):
        target.callback(b"\xff")


def test_stylesheet_owns_public_minifier() -> None:
    assert owner_inventory()["turbohtml.clean.minify_css"] == "css-stylesheet"


@pytest.mark.parametrize("kind", ["fixpoint", "selector"])
def test_stylesheet_consumer_rejects_wrong_output(kind: str) -> None:
    def wrong(source: str) -> str:
        return source + "x" if kind == "fixpoint" else "q{color:red}"

    with pytest.raises(AssertionError):
        stylesheet_observation(b"p { color: red; }", wrong)
