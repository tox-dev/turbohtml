from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, parse_fragment
from turbohtml._html import sanitize as _sanitize
from turbohtml.clean import OnDisallowed


def test_sanitize_rejects_wrong_arguments() -> None:
    with pytest.raises(TypeError):
        _sanitize()  # ty: ignore[missing-argument]  # tests the C argument guard


def test_find_helper_reports_a_missing_element() -> None:
    # The navigation helper fails loudly rather than returning None when a case names a tag that is not present.
    with pytest.raises(AssertionError, match="no 'span' element"):
        _find(parse_fragment("<div></div>"), "span")


def _find(root: Element, tag: str, namespace: str | None = None) -> Element:
    # Return the first element named ``tag`` (optionally in ``namespace``) in document order.
    queue: list[Element] = list(getattr(root, "children", ()))
    while queue:
        node = queue.pop(0)
        if isinstance(node, Element) and node.tag == tag and (namespace is None or node.namespace.value == namespace):
            return node
        queue[0:0] = list(getattr(node, "children", ()))
    msg = f"no {tag!r} element found"
    raise AssertionError(msg)


# Each case parses valid markup, then moves a real foreign/HTML node under a parent the parser would never give it,
# producing a namespace-confused node the reachability check must drop. move is (source, source-namespace, target):
# source-namespace disambiguates a name that exists in more than one namespace. marker is the node's start tag, absent
# once it is dropped and present while correctly nested.
_CONFUSION_CASES: Final[list[pytest.ParameterSet]] = [
    pytest.param(
        "<div></div><svg><circle></circle></svg>", ("circle", "svg", "div"), {"div", "svg", "circle"}, "<circle",
        id="svg-element-reparented-under-html",
    ),
    pytest.param(
        "<math><mrow></mrow></math><svg></svg>", ("svg", "svg", "mrow"), {"math", "mrow", "svg"}, "<svg",
        id="svg-under-non-integration-mathml",
    ),
    pytest.param(
        "<div></div><math><mrow></mrow></math>", ("mrow", "math", "div"), {"div", "math", "mrow"}, "<mrow",
        id="mathml-element-reparented-under-html",
    ),
    pytest.param(
        "<math></math><svg><g></g></svg>", ("math", "math", "g"), {"math", "svg", "g"}, "<math",
        id="math-under-non-integration-svg",
    ),
    pytest.param(
        "<svg><g></g></svg><p></p>", ("p", "html", "g"), {"svg", "g", "p"}, "<p",
        id="html-under-non-integration-svg",
    ),
    pytest.param(
        "<math><mrow></mrow></math><p></p>", ("p", "html", "mrow"), {"math", "mrow", "p"}, "<p",
        id="html-under-non-integration-mathml",
    ),
]  # fmt: skip


@pytest.mark.parametrize(("html", "move", "tags", "marker"), _CONFUSION_CASES)
def test_namespace_confusion_is_dropped(
    html: str, move: tuple[str, str, str], tags: frozenset[str], marker: str
) -> None:
    # A node reparented into an unreachable namespace is dropped, while the same node correctly nested is kept.
    assert marker in _sanitize_tree(parse_fragment(html), tags)  # correctly nested: the policy keeps it
    source, source_ns, target = move
    root = parse_fragment(html)
    _find(root, target).append(_find(root, source, source_ns))
    assert marker not in _sanitize_tree(root, tags)  # confused by the move: the reachability check drops it


def _sanitize_tree(root: Element, tags: frozenset[str]) -> str:
    # named to keep boolean positional arguments off boolean-positional-value-in-call
    allow_relative = strip_comments = True
    allow_fragments = strip_templates = isolate_named_props = allow_customized_builtins = bleach_url_policy = False
    xml = False
    allow_html = allow_svg = allow_mathml = True
    empty: frozenset[str] = frozenset()
    schemes = frozenset({"http", "https", "mailto"})
    original = root.inner_html
    sanitized = _sanitize(
        root, tags, {}, schemes, allow_relative, allow_fragments, OnDisallowed.REMOVE.value, strip_comments,
        None, None, {}, empty,
        empty, empty, {}, empty, strip_templates, None, {}, {}, isolate_named_props, None, None,
        allow_customized_builtins, allow_html, allow_svg, allow_mathml, None, bleach_url_policy, xml,
    )  # fmt: skip
    assert root.inner_html == original
    return sanitized.inner_html


def test_annotation_xml_bare_encoding_is_not_integration_point() -> None:
    root = parse_fragment("<math><annotation-xml encoding></annotation-xml></math><b>hi</b>")
    _find(root, "annotation-xml").append(_find(root, "b"))
    assert _sanitize_tree(root, frozenset({"math", "annotation-xml", "style", "b"})) == (
        "<math><annotation-xml></annotation-xml></math>"
    )
