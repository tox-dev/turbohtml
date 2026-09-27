"""Renaming an element in place through the ``tag`` setter."""

from __future__ import annotations

import pytest

from turbohtml import Document, Element, Namespace, parse, parse_xml


def _found(root: Document | Element, selector: str) -> Element:
    found = root.select_one(selector)
    assert isinstance(found, Element)
    return found


def test_rename_keeps_attributes_and_children() -> None:
    document = parse('<div id="a" class="c"><b>x</b></div>')
    _found(document, "div").tag = "section"
    assert _found(document, "body").inner_html == '<section id="a" class="c"><b>x</b></section>'


def test_rename_is_found_under_the_new_name() -> None:
    document = parse("<div><b>x</b></div>")
    assert len(document.find_all("div")) == 1  # builds the whole-tree tag index the rename must drop
    _found(document, "div").tag = "section"
    assert [len(document.find_all("div")), len(document.find_all("section"))] == [0, 1]


@pytest.mark.parametrize(
    ("markup", "selector", "name", "expected"),
    [
        pytest.param("<p>x</p>", "p", "SECTION", "section", id="html-lowercased"),
        pytest.param("<p>x</p>", "p", "My-Widget", "my-widget", id="custom-lowercased"),
        pytest.param("<svg><rect/></svg>", "rect", "foreignObject", "foreignObject", id="svg-keeps-case"),
    ],
)
def test_rename_spelling(markup: str, selector: str, name: str, expected: str) -> None:
    element = _found(parse(markup), selector)
    element.tag = name
    assert element.tag == expected


def test_rename_keeps_the_namespace() -> None:
    element = _found(parse("<svg><rect/></svg>"), "rect")
    element.tag = "circle"
    assert element.namespace == Namespace.SVG


def test_rename_in_xml_keeps_case() -> None:
    element = parse_xml("<a><B/></a>").find("B")
    assert element is not None
    element.tag = "cC"
    assert element.tag == "cC"


def test_rename_to_raw_text_serializes_text_literally() -> None:
    element = _found(parse("<p>a &lt; b</p>"), "p")
    element.tag = "script"
    assert element.html == "<script>a < b</script>"


def test_rename_to_void_drops_the_end_tag() -> None:
    element = _found(parse("<p></p>"), "p")
    element.tag = "br"
    assert element.html == "<br>"


@pytest.mark.parametrize(
    ("markup", "selector", "name"),
    [
        pytest.param("<p></p>", "p", "template", id="to-template"),
        pytest.param("<template><i></i></template>", "template", "div", id="from-template"),
    ],
)
def test_rename_to_or_from_template_is_rejected(markup: str, selector: str, name: str) -> None:
    element = _found(parse(markup), selector)
    with pytest.raises(ValueError, match="to or from template"):
        element.tag = name


@pytest.mark.parametrize(
    ("name", "error", "message"),
    [
        pytest.param("", ValueError, "must not be empty", id="empty"),
        pytest.param("a b", ValueError, "invalid character", id="space"),
        pytest.param(3, TypeError, "tag must be a str, not int", id="not-a-str"),
    ],
)
def test_rename_rejects_a_bad_name(name: object, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        Element("div").tag = name  # ty: ignore[invalid-assignment]  # the invalid value tests the runtime check


@pytest.mark.parametrize(
    ("markup", "name", "expected"),
    [
        pytest.param(
            "<body><div  id=a>x</div></body>", "section", '<body><section id="a">x</section></body>', id="closed"
        ),
        pytest.param("<body><p  id=a>x</body>", "section", '<body><section id="a">x</body>', id="implicitly-closed"),
        pytest.param("<body><br  id=a></body>", "hr", '<body><hr id="a"></body>', id="void"),
    ],
)
def test_rename_rewrites_the_source_tags(markup: str, name: str, expected: str) -> None:
    document = parse(markup, source_locations=True)
    body = _found(document, "body")
    renamed = body.children[0]
    assert isinstance(renamed, Element)
    renamed.tag = name
    assert body.to_source() == expected


def test_tag_cannot_be_deleted() -> None:
    element = Element("div")
    with pytest.raises(TypeError, match="cannot delete the tag"):
        del element.tag  # ty: ignore[invalid-assignment]  # deleting the tag tests the runtime check
