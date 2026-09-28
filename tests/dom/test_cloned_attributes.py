from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, parse_fragment
from turbohtml.clean import Policy, sanitize


@pytest.mark.parametrize("changed", [pytest.param(0, id="first"), pytest.param(1, id="second")])
def test_cloned_attribute_update_is_independent(cloned_elements: list[Element], changed: int) -> None:
    cloned_elements[changed].attrs["title"] = "new"
    assert [element.attr("title") for element in cloned_elements] == (
        ["new", "old"] if changed == 0 else ["old", "new"]
    )


@pytest.mark.parametrize("changed", [pytest.param(0, id="first"), pytest.param(1, id="second")])
def test_cloned_attribute_removal_is_independent(cloned_elements: list[Element], changed: int) -> None:
    del cloned_elements[changed].attrs["title"]
    assert [element.attrs.copy() for element in cloned_elements] == (
        [{"lang": "en"}, {"title": "old", "lang": "en"}]
        if changed == 0
        else [{"title": "old", "lang": "en"}, {"lang": "en"}]
    )


@pytest.mark.parametrize("count", [pytest.param(1, id="small"), pytest.param(32, id="bulk")])
def test_sanitizing_reconstructed_attributes_keeps_unique_names(count: int) -> None:
    attributes: Final = " ".join(f'data-{index}="{index}"' for index in range(count))
    assert (
        sanitize(
            f'<p><b onclick="bad()" {attributes}>one</p>two',
            Policy(tags=frozenset({"p", "b"}), attributes={"b": frozenset({"*"})}),
        )
        == f"<p><b {attributes}>one</b></p><b {attributes}>two</b>"
    )


@pytest.fixture(
    params=[
        pytest.param("<p><b title='old' lang='en'>one</p>two", id="reconstruction"),
        pytest.param("<b title='old' lang='en'><p>one</b>two", id="adoption"),
        pytest.param(
            "<select><selectedcontent></selectedcontent><option><b title='old' lang='en'>x</b></option></select>",
            id="selectedcontent",
        ),
    ]
)
def cloned_elements(request: pytest.FixtureRequest) -> list[Element]:
    return parse_fragment(request.param).select("b")
