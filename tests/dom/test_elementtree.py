from __future__ import annotations

import gc
import weakref
from copy import deepcopy
from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import DocumentFragment, Element, Range, ShadowRoot, etree, parse
from turbohtml.etree import (
    ElementView,
    SubElement,
    XPath,
    document_context,
    document_fromstring,
    from_lxml,
    fromstring,
    strip_elements,
    strip_tags,
    to_lxml,
    to_lxml_html,
    tostring,
)

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from types import ModuleType

    from lxml.html import HtmlElement


def test_element_view_identity(tree: ElementView) -> None:
    assert ElementView(tree.node) is tree


def test_element_view_text_and_tail(tree: ElementView) -> None:
    assert (tree.text, tree[0].text, tree[0].tail, tree[1].tail) == ("before", "bold", "tail", "end")


def test_element_view_attributes_are_flat_and_live(tree: ElementView) -> None:
    attributes: Final[MutableMapping[str, str]] = tree.attrib
    attributes["class"] = "new words"
    assert (attributes["class"], tree.node.attr("class"), tree.values()) == ("new words", "new words", ["new words"])


def test_element_view_attribute_pop_is_flat(tree: ElementView) -> None:
    assert (tree.attrib.pop("class"), tree.keys()) == ("one two", [])


def test_element_view_relations_reuse_identity(tree: ElementView) -> None:
    first: Final[ElementView] = tree[0]
    last: Final[ElementView] = tree[1]
    parent: Final[ElementView | None] = tree.getparent()
    assert parent is not None
    assert (first.getparent(), first.getnext(), last.getprevious(), parent.tag) == (tree, last, first, "body")


def test_element_view_remove_keeps_tail_and_identity(tree: ElementView) -> None:
    child: Final[ElementView] = tree[0]
    descendant: Final[ElementView] = child[0]
    tree.remove(child)
    assert (child.tail, child.getparent(), child[0], tree.text_content()) == ("tail", None, descendant, "beforelastend")


def test_element_view_replace_keeps_both_tails(tree: ElementView) -> None:
    child: Final[ElementView] = tree[0]
    replacement: Final[ElementView] = ElementView(Element("strong"))
    replacement.text = "new"
    replacement.tail = "new tail"
    tree.replace(child, replacement)
    assert (child.tail, replacement.tail, tree.text_content()) == ("tail", "new tail", "beforenewnew taillastend")


def test_element_view_drop_tree_keeps_tail_in_parent(tree: ElementView) -> None:
    child: Final[ElementView] = tree[0]
    child.drop_tree()
    assert (child.tail, child.getparent(), tree.text_content()) == (None, None, "beforetaillastend")


def test_element_view_detached_tail_moves_with_element(tree: ElementView) -> None:
    child: Final[ElementView] = ElementView(Element("span"))
    child.text = "text"
    child.tail = "tail"
    tree.append(child)
    assert (tree[-1], child.tail, tree.text_content()) == (child, "tail", "beforeboldinnertaillastendtexttail")


def test_element_view_copy_keeps_tail(tree: ElementView) -> None:
    copied: Final[ElementView] = deepcopy(tree[0])
    copied.text = "copy"
    assert (copied.text_content(), copied.tail, tree[0].text_content()) == ("copyinner", "tail", "boldinner")


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        pytest.param((), ["div", "b", "i", "p"], id="all"),
        pytest.param(("*",), ["div", "b", "i", "p"], id="wildcard"),
        pytest.param(("b", "p"), ["b", "p"], id="several"),
        pytest.param((["b", "i"],), ["b", "i"], id="iterable"),
    ],
)
def test_element_view_iter_tags(tree: ElementView, tags: tuple[str | list[str], ...], expected: list[str]) -> None:
    assert [node.tag for node in tree.iter(*tags)] == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        pytest.param((), ["html", "body", "p", "div", "p", "svg"], id="all"),
        pytest.param(("p",), ["p", "p"], id="tag"),
        pytest.param(("div", "svg"), ["div", "svg"], id="several"),
    ],
)
def test_element_tree_iter_starts_at_root(tags: tuple[str, ...], expected: list[str]) -> None:
    child: Final[ElementView | None] = document_fromstring("<p>a</p><div><p>b</p><svg></svg></div>").find(".//svg")
    assert child is not None
    assert [node.tag for node in child.getroottree().iter(*tags)] == expected


def test_element_view_nsmap_is_empty_for_html_and_foreign_elements() -> None:
    tree: Final[ElementView] = fromstring("<div><svg><circle/></svg><math><mi>x</mi></math></div>")
    maps: Final[list[dict[str | None, str]]] = [node.nsmap for node in tree.iter()]
    assert (maps, maps[0] is maps[1]) == ([{}] * 5, False)


def test_element_view_iteration_survives_removed_current(tree: ElementView) -> None:
    tags: Final[list[str]] = []
    for child in iter(tree):
        tags.append(child.tag)
        tree.remove(child)
    assert (tags, tree.text_content()) == (["b", "p"], "before")


def test_element_view_weak_cache_releases_view(tree: ElementView) -> None:
    child = tree[0]
    reference: Final[weakref.ReferenceType[ElementView]] = weakref.ref(child)
    del child
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param(".//i", ["i"], id="relative"),
        pytest.param("//p", ["p"], id="absolute"),
        pytest.param("//b | //p", ["b", "p"], id="union"),
        pytest.param("(//b)[1]", ["b"], id="parenthesized"),
        pytest.param(".//*[@class='//quoted']", [], id="quoted-slashes"),
    ],
)
def test_element_view_copied_root_xpath(tree: ElementView, path: str, expected: list[str]) -> None:
    copied: Final[ElementView] = deepcopy(tree)
    assert [node.tag for node in copied.findall(path)] == expected


def test_element_view_xpath_returns_scalars_and_attributes(tree: ElementView) -> None:
    assert (tree.xpath("count(.//*)"), tree.xpath("@class"), tree.xpath("string(b)")) == (3.0, ["one two"], "boldinner")


def test_element_view_compiled_xpath_preserves_identity(tree: ElementView) -> None:
    expression: Final[XPath] = XPath(".//*[local-name()=$tag]")
    assert expression(tree, tag="b") == [tree[0]]


def test_element_view_removed_node_retains_document_context(tree: ElementView) -> None:
    last: Final[ElementView] = tree[1]

    @document_context
    def detach() -> tuple[str, ElementView | None]:
        child: Final[ElementView] = tree[0]
        tree.remove(child)
        return child.getroottree().getroot().tag, child.find("//p")

    assert detach() == ("html", last)


@pytest.mark.parametrize(
    ("content", "target", "expected"),
    [
        pytest.param("<span>x</span>", "span", "span", id="child"),
        pytest.param("<p><em>x</em></p>", "em", "p", id="descendant"),
    ],
)
def test_element_view_root_in_shadow_tree_is_outermost_element(content: str, target: str, expected: str) -> None:
    markup: Final[str] = f'<div><template shadowrootmode="open">{content}</template></div>'
    host: Final[Element | None] = parse(markup, source_locations=True).find("div")
    assert host is not None
    shadow: Final[ShadowRoot | None] = host.shadow_root
    assert shadow is not None
    node: Final[Element | None] = shadow.find(target)
    assert node is not None
    root: Final[ElementView] = ElementView(node).getroottree().getroot()
    assert (root.tag, root.getparent(), root.node.parent) == (expected, None, shadow)
    root.attrib["data-marked"] = "1"
    assert root.node.attr("data-marked") == "1"


def test_element_view_root_in_fragment_is_outermost_element() -> None:
    element: Final[Element | None] = parse("<div><p><b>bold</b></p>tail</div>", source_locations=True).find("div")
    assert element is not None
    boundary: Final[Range] = Range(element, 0)
    boundary.set_end(element, 1)
    fragment: Final[DocumentFragment] = boundary.extract_contents()
    bold: Final[Element | None] = fragment.find("b")
    assert bold is not None
    root: Final[ElementView] = ElementView(bold).getroottree().getroot()
    assert (root.tag, root.getparent()) == ("p", None)
    root.attrib["class"] = "moved"
    assert fragment.html == '<p class="moved"><b>bold</b></p>'


def test_element_view_removed_from_shadow_tree_is_its_own_root() -> None:
    markup: Final[str] = '<div><template shadowrootmode="open"><p><b>bold</b></p><i>last</i></template></div>'
    host: Final[Element | None] = parse(markup, source_locations=True).find("div")
    assert host is not None
    shadow: Final[ShadowRoot | None] = host.shadow_root
    assert shadow is not None
    paragraph: Final[Element | None] = shadow.find("p")
    assert paragraph is not None
    parent: Final[ElementView] = ElementView(paragraph)
    child: Final[ElementView] = parent[0]

    @document_context
    def detach() -> tuple[ElementView, ElementView | None]:
        parent.remove(child)
        return child.getroottree().getroot(), child.find("//i")

    root, last = detach()
    assert last is not None
    assert (root, last.tag) == (child, "i")


@pytest.mark.parametrize(
    ("tags", "with_tail", "expected"),
    [
        pytest.param((), True, ["before", "bold", "inner", "tail", "last", "end"], id="all"),
        pytest.param((), False, ["before", "bold", "inner", "last"], id="without-tails"),
        pytest.param(("b",), True, ["bold", "tail"], id="tag"),
        pytest.param(("b", "p"), True, ["bold", "tail", "last", "end"], id="several-tags"),
    ],
)
def test_element_view_itertext(
    tree: ElementView, tags: tuple[str, ...], expected: list[str], *, with_tail: bool
) -> None:
    assert list(tree.itertext(*tags, with_tail=with_tail)) == expected


def test_element_tree_factory_retains_valid_attributes() -> None:
    element: Final[ElementView] = etree.Element("a", {"bad name": "drop", "href": "page"}, title="label")
    assert (element.tag, dict(element.attrib)) == ("a", {"href": "page", "title": "label"})


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param('ahref="https:', "ahref__https:", id="broken-attribute"),
        pytest.param('_a="', "_a__", id="underscore-prefix"),
        pytest.param('A_.-:="', "a_.-:__", id="name-punctuation"),
        pytest.param('1a="', "_a__", id="digit-prefix"),
        pytest.param("é bad", "__bad", id="non-ascii-prefix"),
        pytest.param("aé=", "aé_", id="unicode-letter"),
        pytest.param("a\u0661=", "a\u0661_", id="unicode-decimal"),
        pytest.param("a²=", "a²_", id="unicode-digit"),
        pytest.param("a½=", "a½_", id="unicode-number"),
        pytest.param("a💡=", "a__", id="unicode-symbol"),
    ],
)
def test_element_tree_factory_normalizes_invalid_names(name: str, expected: str) -> None:
    assert etree.Element(name).tag == expected


def test_element_tree_subelement_attaches_to_parent(tree: ElementView) -> None:
    child: Final[ElementView] = SubElement(tree, "span", {"class": "one two"})
    assert (child.getparent(), tree[-1], child.get("class")) == (tree, child, "one two")


def test_element_tree_xml_serialization_escapes_tail(tree: ElementView) -> None:
    child: Final[ElementView] = tree[0]
    child.tail = "<&"
    assert tostring(child, encoding="unicode") == "<b>bold<i>inner</i></b>&lt;&amp;"


def test_element_tree_text_serialization_keeps_tail(tree: ElementView) -> None:
    assert tostring(tree[0], method="text") == b"boldinnertail"


def test_element_tree_strip_tags_keeps_descendant_text(tree: ElementView) -> None:
    strip_tags(tree, "b", "i")
    assert (tree.text, [child.tag for child in tree], tree.text_content()) == (
        "beforeboldinnertail",
        ["p"],
        "beforeboldinnertaillastend",
    )


def test_element_tree_strip_elements_without_tail(tree: ElementView) -> None:
    strip_elements(tree, "b", "i", with_tail=False)
    assert tree.text_content() == "beforetaillastend"


@pytest.mark.oracle
def test_element_tree_lxml_copy_drops_comments_but_keeps_text() -> None:
    html: Final[ModuleType] = pytest.importorskip("lxml.html")

    source = html.fromstring("<div>one<!--comment-->two<b>bold</b>tail<!--comment-->end</div>")
    copied: Final[ElementView] = from_lxml(source)
    assert tostring(copied, encoding="unicode") == "<div>onetwo<b>bold</b>tailend</div>"


@pytest.mark.oracle
@pytest.mark.parametrize(
    "path",
    [
        pytest.param(".//div[not(.//h2[not(//h1)])]", id="descendant-in-predicate"),
        pytest.param(".//h2[/html]", id="root-in-predicate"),
        pytest.param("/html/body/h1", id="absolute"),
        pytest.param(".//h2[count(//h1) = 1]", id="count"),
    ],
)
def test_element_tree_lxml_copy_resolves_absolute_paths_like_lxml(path: str) -> None:
    html: Final[ModuleType] = pytest.importorskip("lxml.html")
    source = html.fromstring("<html><body><div><h2>Preview</h2></div><h1>Title</h1></body></html>")
    copied: Final[ElementView] = from_lxml(source)
    assert ([element.tag for element in cast("list[ElementView]", copied.xpath(path))], copied.getparent()) == (
        [element.tag for element in source.xpath(path)],
        None,
    )


@pytest.mark.oracle
def test_element_tree_copy_to_lxml_keeps_comment_tails() -> None:
    lxml: Final[ModuleType] = pytest.importorskip("lxml.etree")

    node: Final[Element | None] = parse("<div>one<!--comment-->two<b>bold</b>tail<!--comment-->end</div>").find("div")
    assert node is not None
    copied = to_lxml(ElementView(node))
    assert lxml.tostring(copied, encoding="unicode") == "<div>onetwo<b>bold</b>tailend</div>"


@pytest.mark.oracle
def test_element_tree_copy_to_html_supports_lxml_links(tree: ElementView) -> None:
    pytest.importorskip("lxml.html")
    SubElement(tree, "a", href="page")
    copied: Final[HtmlElement] = to_lxml_html(tree)
    copied.make_links_absolute("https://example.com/")
    assert copied.xpath(".//a/@href") == ["https://example.com/page"]


@pytest.mark.oracle
def test_element_tree_lxml_copy_normalizes_namespace_less_names() -> None:
    lxml: Final[ModuleType] = pytest.importorskip("lxml.etree")
    tree: Final[ElementView] = etree.Element("foo:bar", {"foo:attr": "drop", "id": "keep"})
    assert lxml.tostring(to_lxml(tree), encoding="unicode") == '<foo_bar id="keep"/>'


@pytest.mark.oracle
def test_element_tree_lxml_copy_preserves_existing_lxml_identity() -> None:
    tree = pytest.importorskip("lxml.etree").Element("p")
    assert to_lxml(tree) is tree


@pytest.mark.oracle
def test_element_tree_lxml_copy_rejects_unrepresentable_root_name() -> None:
    pytest.importorskip("lxml.etree")
    tree: Final[ElementView] = etree.Element("a²")
    with pytest.raises(ValueError, match="Invalid tag name"):
        to_lxml(tree)


@pytest.mark.oracle
def test_element_tree_lxml_copy_rejects_unrepresentable_child_name() -> None:
    pytest.importorskip("lxml.etree")
    tree: Final[ElementView] = etree.Element("p")
    SubElement(tree, "a²")
    with pytest.raises(ValueError, match="Invalid tag name"):
        to_lxml(tree)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a\x01b\x0bc", "abc", id="control"),
        pytest.param("\t\n\r kept", "\t\n\r kept", id="whitespace-control"),
        pytest.param("a\ud800b\udfffc", "abc", id="surrogate"),
        pytest.param("a\ufffeb\uffffc\uffefd", "abc\uffefd", id="noncharacter"),
        pytest.param("caf\xe9", "caf\xe9", id="clean"),
    ],
)
def test_element_tree_lxml_copy_strips_xml_illegal_characters(value: str, expected: str) -> None:
    lxml: Final[ModuleType] = pytest.importorskip("lxml.etree")
    tree: Final[ElementView] = etree.Element("p", title=value)
    tree.text = value
    child: Final[ElementView] = SubElement(tree, "b", alt=value)
    child.tail = value
    copied = to_lxml(tree)
    assert (copied.get("title"), copied.text, copied[0].get("alt"), copied[0].tail) == (expected,) * 4
    assert lxml.tostring(copied, encoding="unicode").count("<b") == 1


@pytest.mark.oracle
def test_element_tree_lxml_copy_keeps_attributes_with_control_characters() -> None:
    html: Final[ModuleType] = pytest.importorskip("lxml.html")
    source: Final[str] = "<div><p title='a\x01b'>x\x01y<b>z</b>\x0bt</p><a href='/z\x01'><img alt='p\x0bq'></a></div>"
    copied = to_lxml(from_lxml(html.fromstring(source)))
    assert [(node.tag, dict(node.attrib), node.text, node.tail) for node in copied.iter()] == [
        ("div", {}, None, None),
        ("p", {"title": "ab"}, "xy", None),
        ("b", {}, "z", "t"),
        ("a", {"href": "/z"}, None, None),
        ("img", {"alt": "pq"}, None, None),
    ]


@pytest.fixture
def tree() -> ElementView:
    element: Final[Element | None] = parse(
        '<div class="one two">before<b>bold<i>inner</i></b>tail<p>last</p>end</div>'
    ).find("div")
    assert element is not None
    return ElementView(element)
