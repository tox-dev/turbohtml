from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, parse, parse_xml


@pytest.mark.parametrize("xml", [pytest.param(False, id="html"), pytest.param(True, id="xml")])
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("section:has(> a)", ["parent"], id="child"),
        pytest.param("section:has(> b)", [], id="child-absent"),
        pytest.param("section:has(+ p)", ["parent"], id="adjacent"),
        pytest.param("section:has(+ a)", [], id="adjacent-mismatch"),
        pytest.param("section:has(~ a)", ["parent"], id="following"),
        pytest.param("section:has(~ b)", [], id="following-absent"),
        pytest.param("section:has(> a.hit)", ["parent"], id="compound"),
        pytest.param("section:has(> a.miss)", [], id="compound-mismatch"),
        pytest.param("section:has(> x-widget)", ["parent"], id="custom"),
        pytest.param("section:has(+ x-widget)", [], id="custom-mismatch"),
        pytest.param("p:has(+ a)", ["next"], id="skip-comment"),
        pytest.param("section:has(> svg)", ["parent"], id="foreign"),
    ],
)
def test_relative_type_selectors(selector: str, expected: list[str], *, xml: bool) -> None:
    markup: Final = (
        '<main><section id="parent"><a class="hit"></a><x-widget></x-widget><svg></svg></section>'
        'text<p id="next"></p><!--gap--><a></a></main>'
    )
    document: Final = parse_xml(markup) if xml else parse(markup)
    assert [element.attrs["id"] for element in document.select(selector)] == expected


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("section:has(> A)", ["upper"], id="upper"),
        pytest.param("section:has(> a)", ["lower"], id="lower"),
        pytest.param("section:has(+ A)", ["lower"], id="adjacent"),
        pytest.param("section:has(~ a)", [], id="following-case-mismatch"),
    ],
)
def test_relative_xml_type_case(selector: str, expected: list[str]) -> None:
    document: Final = parse_xml('<main><section id="upper"><A/></section><section id="lower"><a/></section><A/></main>')
    assert [element.attrs["id"] for element in document.select(selector)] == expected


@pytest.mark.parametrize("tag", [pytest.param("a", id="lower"), pytest.param("A", id="upper")])
def test_relative_type_after_xml_adoption(tag: str) -> None:
    parent: Final = Element("section")
    parent.append(parse_xml(f"<{tag}/>").select("*")[0])
    assert parent.matches("section:has(> a)")


def test_relative_type_after_rename() -> None:
    parent: Final = Element("section")
    child: Final = Element("x-widget")
    parent.append(child)
    child.tag = "A"
    assert parent.matches("section:has(> a)")
