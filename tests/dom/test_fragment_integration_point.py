from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Namespace, parse

if TYPE_CHECKING:
    from turbohtml import Element

_ENTRIES: Final[tuple[str, ...]] = ("set_inner_html", "afterbegin", "beforeend", "beforebegin", "afterend")


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize(
    ("markup", "tag"),
    [
        pytest.param("<svg><foreignObject><i></i></foreignObject></svg>", "foreignobject", id="svg-foreignobject"),
        pytest.param("<svg><desc><i></i></desc></svg>", "desc", id="svg-desc"),
        pytest.param("<svg><title><i></i></title></svg>", "title", id="svg-title"),
        pytest.param("<math><mi><i></i></mi></math>", "mi", id="mathml-mi"),
        pytest.param("<math><mo><i></i></mo></math>", "mo", id="mathml-mo"),
        pytest.param("<math><mn><i></i></mn></math>", "mn", id="mathml-mn"),
        pytest.param("<math><ms><i></i></ms></math>", "ms", id="mathml-ms"),
        pytest.param("<math><mtext><i></i></mtext></math>", "mtext", id="mathml-mtext"),
    ],
)
def test_spliced_textarea_under_an_integration_point_is_html(markup: str, tag: str, entry: str) -> None:
    doc = parse(markup)
    point = doc.find(tag)
    child = doc.find("i")
    assert point is not None
    assert child is not None
    _splice(entry, point, child, "<textarea>a</textarea>")
    textarea = point.find("textarea")
    assert textarea is not None
    assert textarea.namespace is Namespace.HTML


def _splice(entry: str, point: Element, child: Element, html: str) -> None:
    match entry:
        case "set_inner_html":
            point.set_inner_html(html)
        case "afterbegin" | "beforeend":
            point.insert_adjacent_html(entry, html)
        case _:
            child.insert_adjacent_html(entry, html)


@pytest.mark.parametrize("entry", _ENTRIES)
def test_spliced_foreign_content_under_an_integration_point_keeps_its_namespace(entry: str) -> None:
    doc = parse('<math><annotation-xml encoding="text/html"><i></i></annotation-xml></math>')
    point = doc.find("annotation-xml")
    child = doc.find("i")
    assert point is not None
    assert child is not None
    _splice(entry, point, child, "<svg><circle></circle></svg><math><mi>x</mi></math>")
    circle = point.find("circle")
    mi = point.find("mi")
    assert circle is not None
    assert mi is not None
    assert (circle.namespace, mi.namespace) == (Namespace.SVG, Namespace.MATHML)


@pytest.mark.parametrize("entry", _ENTRIES)
@pytest.mark.parametrize(
    ("markup", "tag"),
    [
        pytest.param(
            '<math><annotation-xml encoding="text/xml"><mi></mi></annotation-xml></math>',
            "annotation-xml",
            id="non-html-encoding",
        ),
        pytest.param(
            '<math><annotation-xml encoding="text/xml" encoding="text/html"><mi></mi></annotation-xml></math>',
            "annotation-xml",
            id="first-encoding-decides",
        ),
        pytest.param(
            '<math><annotation-xml encoding="text/htmx"><mi></mi></annotation-xml></math>',
            "annotation-xml",
            id="near-miss-encoding",
        ),
        pytest.param(
            "<math><annotation-xml encoding><mi></mi></annotation-xml></math>",
            "annotation-xml",
            id="valueless-encoding",
        ),
        pytest.param("<math><mrow><mi></mi></mrow></math>", "mrow", id="plain-mathml"),
    ],
)
def test_spliced_textarea_under_a_non_integration_element_stays_mathml(markup: str, tag: str, entry: str) -> None:
    doc = parse(markup)
    point = doc.find(tag)
    child = doc.find("mi")
    assert point is not None
    assert child is not None
    _splice(entry, point, child, "<textarea>a</textarea>")
    textarea = point.find("textarea")
    assert textarea is not None
    assert textarea.namespace is Namespace.MATHML


@pytest.mark.parametrize(
    ("markup", "selector", "expected"),
    [
        pytest.param(
            '<math><annotation-xml encoding="text/html"><textarea>a</textarea></annotation-xml></math>',
            "textarea",
            Namespace.HTML,
            id="parsed-textarea",
        ),
        pytest.param(
            '<math><annotation-xml encoding="text/html"><mi>a</mi></annotation-xml></math>',
            "mi",
            Namespace.HTML,
            id="parsed-mi",
        ),
        pytest.param(
            '<math><annotation-xml encoding="text/html"><svg>a</svg></annotation-xml></math>',
            "svg",
            Namespace.SVG,
            id="parsed-svg",
        ),
        pytest.param("<math><mi><mglyph>a</mglyph></mi></math>", "mglyph", Namespace.MATHML, id="parsed-mglyph"),
        pytest.param(
            "<svg><foreignObject><math>a</math></foreignObject></svg>",
            "math",
            Namespace.MATHML,
            id="parsed-math",
        ),
    ],
)
def test_parser_built_children_keep_their_namespace(markup: str, selector: str, expected: Namespace) -> None:
    node = parse(markup).find(selector)
    assert node is not None
    assert node.namespace is expected
