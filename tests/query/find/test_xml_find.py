from __future__ import annotations

import pytest

from turbohtml import Document, parse_xml


@pytest.mark.parametrize("tag", ["div", "DIV", "custom", "Custom"])
def test_xml_find_all_matches_exact_tag_name(tag: str, xml_case_document: Document) -> None:
    assert [element.tag for element in xml_case_document.find_all(tag)] == [tag]


@pytest.mark.parametrize("tag", ["div", "DIV", "custom", "Custom"])
def test_xml_find_matches_exact_tag_name(tag: str, xml_case_document: Document) -> None:
    assert (match := xml_case_document.find(tag)) is not None
    assert match.tag == tag


def test_xml_find_all_matches_known_tag_below_subtree() -> None:
    root = parse_xml("<Root><section><div id='inside'/></section><div id='outside'/></Root>").root
    assert root is not None
    assert (section := root.find("section")) is not None
    assert [element.attrs["id"] for element in section.find_all("div")] == ["inside"]


@pytest.fixture
def xml_case_document() -> Document:
    return parse_xml("<Root><div/><DIV/><custom/><Custom/></Root>")
