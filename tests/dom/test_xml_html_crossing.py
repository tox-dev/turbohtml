from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pytest

from turbohtml import Element, HTMLParseError, Range, parse, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable

    from work_steps import WorkSteps


def test_xml_element_in_html_tree_folds_its_names() -> None:
    body = _into_html('<root><Item-1 Attr="1" X-1="2" d-9Q="3" low="4"/></root>')
    assert body.html == '<body><item-1 attr="1" x-1="2" d-9q="3" low="4"></item-1></body>'


def _into_html(xml: str) -> Element:
    body = parse("<body></body>").select_one("body")
    assert body is not None
    for child in list(_xml_root(xml).children):
        body.append(child)
    return body


def _xml_root(xml: str) -> Element:
    root = parse_xml(xml).root
    assert root is not None
    return root


def test_xml_subtree_in_html_tree_folds_every_element() -> None:
    body = _into_html("<root><Item><A><B/></A><C/></Item></root>")
    assert body.html == "<body><item><a><b></b></a><c></c></item></body>"


def test_xml_element_into_html_by_range_insert_folds_its_names() -> None:
    body = parse("<body></body>").select_one("body")
    assert body is not None
    Range(body).insert_node(_xml_root("<r><Item Attr='1'/></r>").children[0])
    assert body.html == '<body><item attr="1"></item></body>'


def test_xml_element_in_html_tree_attrs_resolve() -> None:
    item = _into_html('<root><Item Attr="1"/></root>').children[0]
    assert isinstance(item, Element)
    assert dict(item.attrs) == {"attr": "1"}


def test_xml_element_in_html_tree_keeps_the_first_folded_attribute() -> None:
    body = _into_html('<root><i Attr="1" attr="2"/></root>')
    assert body.html == '<body><i attr="1"></i></body>'


@pytest.mark.parametrize(
    "count", [pytest.param(31, id="scanned"), pytest.param(32, id="sorted"), pytest.param(200, id="sorted-long")]
)
def test_xml_element_in_html_tree_keeps_the_first_of_many_folded_attributes(count: int) -> None:
    # of every three names, one arrives in upper case before its lower-case spelling, one after it, and one alone
    spellings = [("N{}", "n{}"), ("n{}", "N{}"), ("n{}",)]
    names = [spelling.format(index) for index in range(count // 2) for spelling in spellings[index % 3]]
    names += [f"pad{index}" for index in range(count - len(names))]
    attributes = " ".join(f'{name}="{name}"' for name in names)
    item = _into_html(f"<root><i {attributes}/></root>").children[0]
    assert isinstance(item, Element)
    expected: dict[str, str] = {}
    for name in names:
        expected.setdefault(name.lower(), name)
    assert list(item.attrs.items()) == list(expected.items())


def test_xml_element_with_many_attributes_moves_into_html_tree_in_linear_steps(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(_append_wide, (2000, 8000))


def _append_wide(count: int) -> Callable[[], object]:
    body = parse("<body></body>").select_one("body")
    assert body is not None
    attributes = " ".join(f'data-{index}="1"' for index in range(count))
    return partial(body.append, _xml_root(f"<root><i {attributes}/></root>").children[0])


def test_xml_element_in_html_tree_takes_the_html_atom() -> None:
    body = _into_html('<root><P Class="a b">t</P></root>')
    assert [paragraph.html for paragraph in body.select("p.a")] == ['<p class="a b">t</p>']


@pytest.mark.parametrize(
    "name",
    [pytest.param("x" * 70, id="long-name"), pytest.param("café", id="non-ascii-name")],
)
def test_xml_element_in_html_tree_with_an_unknown_name(name: str) -> None:
    assert _into_html(f"<root><{name}/></root>").html == f"<body><{name}></{name}></body>"


def test_html_script_in_xml_tree_escapes_its_text() -> None:
    script = parse("<script>a<b</script>").select_one("script")
    assert script is not None
    root = _xml_root("<r/>")
    root.append(script)
    assert root.html == "<r><script>a&lt;b</script></r>"


def test_xml_set_inner_html_parses_as_xml() -> None:
    child = _xml_child()
    child.set_inner_html('<p:x y="1">t &amp; u</p:x><Z/>')
    assert child.html == '<c><p:x y="1">t &amp; u</p:x><Z></Z></c>'


def _xml_child() -> Element:
    child = _xml_root(
        '<r xmlns="urn:a" xmlns:p="urn:&quot;b&lt;" a="0" xmlnsq="1" xmlnsqq="2" attr5="3"><c/></r>'
    ).children[0]
    assert isinstance(child, Element)
    return child


def test_xml_set_inner_html_under_a_redeclared_namespace() -> None:
    child = _xml_root('<r xmlns:p="urn:a"><c xmlns:p="urn:c"/></r>').children[0]
    assert isinstance(child, Element)
    child.set_inner_html("<p:x/>")
    assert child.html == '<c xmlns:p="urn:c"><p:x></p:x></c>'


def test_xml_set_inner_html_on_a_detached_element() -> None:
    child = _xml_child().extract()
    child.set_inner_html("<Mixed/>")
    assert child.html == "<c><Mixed></Mixed></c>"


def test_xml_set_inner_html_undeclared_prefix_is_rejected() -> None:
    with pytest.raises(HTMLParseError):
        _xml_child().extract().set_inner_html("<p:x/>")


def test_xml_set_inner_html_keeps_case() -> None:
    child = _xml_child()
    child.set_inner_html("<Mixed/>")
    assert child.children[0].html == "<Mixed></Mixed>"


@pytest.mark.parametrize(
    ("markup", "message"),
    [
        pytest.param("<a>&nbsp;</a>", "xml-undefined-entity at line 1, column 3", id="html-entity"),
        pytest.param("<a></b>", "xml-mismatched-tag at line 1, column 5", id="mismatched-tag"),
        pytest.param("x\n<a></b>", "xml-mismatched-tag at line 2, column 5", id="second-line"),
    ],
)
def test_xml_set_inner_html_rejects_ill_formed_markup(markup: str, message: str) -> None:
    with pytest.raises(HTMLParseError, match=message):
        _xml_child().set_inner_html(markup)


def test_xml_set_inner_html_error_leaves_the_children() -> None:
    child = _xml_child()
    child.set_inner_html("<kept/>")
    with pytest.raises(HTMLParseError):
        child.set_inner_html("<a>")
    assert child.html == "<c><kept></kept></c>"


def test_xml_insert_adjacent_html_parses_as_xml() -> None:
    child = _xml_child()
    child.insert_adjacent_html("afterend", "<K/>")
    parent = child.parent
    assert isinstance(parent, Element)
    assert [node.html for node in parent.children] == ["<c></c>", "<K></K>"]
