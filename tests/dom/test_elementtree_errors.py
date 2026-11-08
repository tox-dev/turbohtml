from __future__ import annotations

import gc
import sys
import weakref
from inspect import unwrap
from operator import attrgetter
from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import Text, etree

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from pytest_mock import MockerFixture

    from turbohtml import Element
    from turbohtml.etree import ElementView


@pytest.mark.parametrize(
    ("function", "arguments"),
    [
        pytest.param("document_fromstring", (42,), id="document-type"),
        pytest.param("fromstring", (42,), id="snippet-type"),
        pytest.param("fragment_fromstring", (42,), id="fragment-type"),
        pytest.param("tostring", (42,), id="serialize-type"),
        pytest.param("SubElement", (), id="child-missing-parent"),
        pytest.param("SubElement", (42,), id="child-parent-type"),
        pytest.param("strip_tags", (), id="unwrap-missing-tree"),
        pytest.param("strip_tags", (42,), id="unwrap-tree-type"),
        pytest.param("strip_elements", (), id="remove-missing-tree"),
        pytest.param("strip_elements", (42,), id="remove-tree-type"),
        pytest.param("XPath", (42,), id="xpath-expression-type"),
        pytest.param("to_lxml_html", (42,), id="html-copy-type"),
    ],
)
def test_element_tree_rejects_invalid_function_arguments(function: str, arguments: tuple[int, ...]) -> None:
    with pytest.raises(TypeError):
        getattr(etree, function)(*arguments)


@pytest.mark.parametrize("method", [pytest.param("iterchildren", id="children"), pytest.param("itertext", id="text")])
def test_element_tree_rejects_invalid_axis_filter(tree: ElementView, method: str) -> None:
    with pytest.raises(TypeError):
        list(getattr(tree, method)(42))


@pytest.mark.parametrize(
    ("method", "arguments", "keywords"),
    [
        pytest.param("get", (), {}, id="get-missing-key"),
        pytest.param("set", (), {}, id="set-missing-key"),
        pytest.param("replace", (), {}, id="replace-missing-children"),
        pytest.param("insert", (), {}, id="insert-missing-index"),
        pytest.param("xpath", (), {}, id="xpath-missing-expression"),
        pytest.param("clear", (), {"unknown": True}, id="clear-unknown-option"),
        pytest.param("iterchildren", (), {"unknown": True}, id="children-unknown-option"),
        pytest.param("itersiblings", (), {"unknown": True}, id="siblings-unknown-option"),
        pytest.param("itertext", (), {"unknown": True}, id="text-unknown-option"),
        pytest.param("makeelement", (), {}, id="makeelement-missing-tag"),
    ],
)
def test_element_tree_rejects_invalid_method_arguments(
    tree: ElementView, method: str, arguments: tuple[int, ...], keywords: dict[str, bool]
) -> None:
    with pytest.raises(TypeError):
        getattr(tree, method)(*arguments, **keywords)


@pytest.mark.parametrize(
    "method",
    [pytest.param("find", id="find"), pytest.param("findall", id="findall"), pytest.param("iterfind", id="iterfind")],
)
def test_element_tree_rejects_non_string_expression(tree: ElementView, method: str) -> None:
    with pytest.raises(TypeError, match="expression must be a str"):
        getattr(tree, method)(42)


@pytest.mark.parametrize("property_name", [pytest.param("text", id="text"), pytest.param("tail", id="tail")])
def test_element_tree_rejects_non_string_text(tree: ElementView, property_name: str) -> None:
    with pytest.raises(TypeError, match="str or None"):
        setattr(tree, property_name, 42)


@pytest.mark.parametrize("property_name", [pytest.param("text", id="text"), pytest.param("tail", id="tail")])
def test_element_tree_rejects_text_deletion(tree: ElementView, property_name: str) -> None:
    with pytest.raises(TypeError, match="str or None"):
        delattr(tree, property_name)


def test_element_view_requires_an_element() -> None:
    with pytest.raises(TypeError, match="Element"):
        etree.ElementView(cast("Element", Text("text")))


def test_element_tree_rejects_non_string_attribute(tree: ElementView) -> None:
    with pytest.raises(TypeError):
        tree.set("class", cast("str", 42))


def test_element_tree_rejects_non_iterable_extension(tree: ElementView) -> None:
    with pytest.raises(TypeError):
        tree.extend(cast("list[ElementView]", 42))


def test_element_tree_rejects_ancestor_append(tree: ElementView) -> None:
    with pytest.raises(ValueError, match="own subtree"):
        tree[0].append(tree)


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda tree: tree.extend([tree]), id="extend"),
        pytest.param(lambda tree: tree.replace(tree[0], tree), id="replace"),
        pytest.param(lambda tree: tree.insert(0, tree), id="insert"),
        pytest.param(lambda tree: tree[0].addprevious(tree), id="previous"),
        pytest.param(lambda tree: tree[0].addnext(tree), id="next"),
    ],
)
def test_element_tree_rejects_ancestor_insertion(tree: ElementView, operation: Callable[[ElementView], None]) -> None:
    with pytest.raises(ValueError, match="own subtree"):
        operation(tree)


@pytest.mark.parametrize("method", [pytest.param("addprevious", id="previous"), pytest.param("addnext", id="next")])
def test_element_tree_siblings_require_parent(method: str) -> None:
    with pytest.raises(ValueError, match="parent"):
        getattr(etree.Element("div"), method)(etree.Element("p"))


def test_element_tree_rejects_invalid_compiled_xpath() -> None:
    with pytest.raises(ValueError, match="expected a node test"):
        etree.XPath(".//[")


def test_element_tree_rejects_invalid_find_xpath() -> None:
    with pytest.raises(ValueError, match="expected a node test"):
        etree.Element("p").find(".//[")


def test_element_tree_rejects_non_string_factory_attribute() -> None:
    with pytest.raises(TypeError):
        etree.Element("p", {"class": cast("str", 42)})


def test_element_tree_rejects_invalid_attribute_mapping() -> None:
    with pytest.raises(AttributeError, match="keys"):
        etree.Element("p", cast("dict[str, str]", 42))


def test_element_tree_rejects_empty_tag() -> None:
    with pytest.raises(ValueError, match="empty"):
        etree.Element("")


def test_element_tree_lxml_copy_requires_lxml_etree(mocker: MockerFixture) -> None:
    mocker.patch.dict(sys.modules, {"lxml.etree": None})
    with pytest.raises(ModuleNotFoundError, match="halted"):
        etree.to_lxml(etree.Element("p"))


def test_element_tree_lxml_copy_requires_lxml_html(mocker: MockerFixture) -> None:
    mocker.patch.dict(sys.modules, {"lxml.html": None})
    with pytest.raises(ModuleNotFoundError, match="halted"):
        etree.to_lxml_html(etree.Element("p"))


@pytest.mark.oracle
def test_element_tree_lxml_comment_cannot_be_root() -> None:
    lxml: Final = pytest.importorskip("lxml.etree")
    with pytest.raises(TypeError):
        etree.from_lxml(lxml.Comment("comment"))


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda tree, value: tree.iterchildren(reversed=value), id="direction"),
        pytest.param(etree.strip_tags, id="tag-filter"),
        pytest.param(lambda _tree, value: etree.fragment_fromstring("<p>text</p>", create_parent=value), id="parent"),
        pytest.param(lambda tree, value: etree.strip_elements(tree, "p", with_tail=value), id="strip-tail"),
    ],
)
def test_element_tree_propagates_truth_value_error(
    tree: ElementView, operation: Callable[[ElementView, bool], None]
) -> None:
    expired = etree.Element("p")
    value: Final = weakref.proxy(expired)
    del expired
    gc.collect()
    with pytest.raises(ReferenceError, match="referenced object no longer exists"):
        operation(tree, cast("bool", value))


@pytest.mark.parametrize(
    "function", [pytest.param(etree.strip_tags, id="unwrap"), pytest.param(etree.strip_elements, id="remove")]
)
def test_element_tree_rejects_invalid_strip_filter(
    tree: ElementView, function: Callable[[ElementView, *tuple[str | Iterable[str] | None, ...]], None]
) -> None:
    with pytest.raises(TypeError):
        function(tree, 42)  # ty: ignore[invalid-argument-type]  # passes a non-str tag on purpose to check the rejection


def test_element_tree_requires_callable_context() -> None:
    with pytest.raises(TypeError, match="callable"):
        etree.document_context(cast("Callable[[], None]", 42))


def test_element_tree_rejects_invalid_fragment_parent_name() -> None:
    with pytest.raises(ValueError, match="tag"):
        etree.fragment_fromstring("<p>text</p>", create_parent="bad name")


def test_element_tree_context_preserves_function_metadata() -> None:
    def extract() -> str:
        return "text"

    wrapped: Final = etree.document_context(extract)
    assert (attrgetter("__name__", "__class__")(wrapped), unwrap(wrapped), wrapped()) == (
        ("extract", type(wrapped)),
        extract,
        "text",
    )


def test_element_tree_propagates_xpath_evaluation_error(tree: ElementView) -> None:
    with pytest.raises(ValueError, match="unknown function"):
        tree.xpath("bogus-fn(1)")


def test_element_tree_context_reports_missing_attribute() -> None:
    with pytest.raises(AttributeError, match="missing"):
        attrgetter("missing")(etree.document_context(str))


def test_element_tree_compiled_xpath_requires_element() -> None:
    expression: Final = etree.XPath(".//p")
    with pytest.raises(TypeError, match="ElementView"):
        expression(cast("ElementView", 42))


@pytest.fixture
def tree() -> ElementView:
    return etree.fromstring("<div><p>text</p></div>")
