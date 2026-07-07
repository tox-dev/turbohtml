"""The per-node namespace-reachability check: a namespace-confused node is dropped, valid foreign content is untouched.

turbohtml parses once and its tree builder produces spec-correct namespacing, so no string a caller can pass carries a
namespace-confused node. The check is defense in depth against a *mutated* tree: an element whose namespace is
unreachable from its parent's -- an HTML element reparented under SVG/MathML, or a foreign element spliced into HTML
content where its children would reparse as HTML -- is the mutation-XSS class DOMPurify's ``_checkValidNamespace``
guards. These tests build such a node with the public mutation API (moving a real foreign node under a parent the
parser would never give it) and drive the C sanitizer over it, then confirm the same element correctly nested survives,
so the move -- not the policy -- is what triggers the drop.
"""

from __future__ import annotations

import pytest

from turbohtml import Element, parse_fragment
from turbohtml._html import _sanitize
from turbohtml.clean import OnDisallowed, Policy, sanitize

# The set of tags the reachability check reasons over, allowed together so only the namespace relationship can drop one.
_FOREIGN_TAGS = frozenset({"svg", "math", "foreignObject", "annotation-xml", "mtext", "circle", "p", "b", "i"})
_FOREIGN_POLICY = Policy(tags=_FOREIGN_TAGS, attributes={"annotation-xml": frozenset({"encoding"})})


def _find(root: Element, tag: str, namespace: str | None = None) -> Element:
    """Return the first element named ``tag`` (optionally in ``namespace``) in document order."""
    queue: list[Element] = list(getattr(root, "children", ()))
    while queue:
        node = queue.pop(0)
        if isinstance(node, Element) and node.tag == tag and (namespace is None or node.namespace.value == namespace):
            return node
        queue[0:0] = list(getattr(node, "children", ()))
    msg = f"no {tag!r} element found"
    raise AssertionError(msg)


def _sanitize_tree(root: Element, tags: frozenset[str]) -> str:
    """Run the C sanitizer in place over a pre-built tree, removing every disallowed node's subtree."""
    # named to keep the boolean positional arguments off the FBT003 lint, not to document them
    allow_relative = strip_comments = True
    strip_templates = False
    empty: frozenset[str] = frozenset()
    schemes = frozenset({"http", "https", "mailto"})
    _sanitize(
        root, tags, {}, schemes, allow_relative, OnDisallowed.REMOVE.value, strip_comments, None, None, {}, empty,
        empty, empty, {}, empty, strip_templates, None,
    )  # fmt: skip
    return root.inner_html


def test_find_helper_reports_a_missing_element() -> None:
    """The navigation helper fails loudly rather than returning None when a case names a tag that is not present."""
    with pytest.raises(AssertionError, match="no 'span' element"):
        _find(parse_fragment("<div></div>"), "span")


@pytest.mark.parametrize(
    ("html", "policy"),
    [
        pytest.param("<svg>x</svg>", _FOREIGN_POLICY, id="svg-enters-from-html"),
        pytest.param("<math>x</math>", _FOREIGN_POLICY, id="math-enters-from-html"),
        pytest.param("<svg><circle></circle></svg>", _FOREIGN_POLICY, id="svg-child-of-svg"),
        pytest.param("<svg><foreignObject><p>hi</p></foreignObject></svg>", _FOREIGN_POLICY, id="html-under-svg-point"),
        pytest.param("<math><mtext><b>hi</b></mtext></math>", _FOREIGN_POLICY, id="html-under-mathml-text-point"),
        pytest.param(
            '<math><annotation-xml encoding="text/html"><b>hi</b></annotation-xml></math>',
            _FOREIGN_POLICY,
            id="html-under-annotation-xml",
        ),
        pytest.param(
            '<math><annotation-xml encoding="application/xhtml+xml"><i>hi</i></annotation-xml></math>',
            _FOREIGN_POLICY,
            id="html-under-annotation-xml-xhtml",
        ),
        pytest.param("<svg><foreignObject><math>y</math></foreignObject></svg>", _FOREIGN_POLICY, id="math-under-svg"),
        pytest.param("<math><mtext><svg>z</svg></mtext></math>", _FOREIGN_POLICY, id="svg-under-mathml-text-point"),
        pytest.param(
            "<math><annotation-xml><svg>z</svg></annotation-xml></math>", _FOREIGN_POLICY, id="svg-under-annotation-xml"
        ),
    ],
)
def test_namespace_reachable_foreign_content_untouched(html: str, policy: Policy) -> None:
    """Every namespace transition the parser produces is reachable, so the check leaves valid foreign content as-is."""
    assert sanitize(html, policy) == parse_fragment(html).inner_html


# Each case parses valid markup, then moves a real foreign/HTML node under a parent the parser would never give it,
# producing a namespace-confused node the reachability check must drop. move is (source, source-namespace, target):
# source-namespace disambiguates a name that exists in more than one namespace. marker is the node's start tag, absent
# once it is dropped and present while correctly nested.
_CONFUSION_CASES = [
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
    """A node reparented into an unreachable namespace is dropped, while the same node correctly nested is kept."""
    assert marker in _sanitize_tree(parse_fragment(html), tags)  # correctly nested: the policy keeps it
    source, source_ns, target = move
    root = parse_fragment(html)
    _find(root, target).append(_find(root, source, source_ns))
    assert marker not in _sanitize_tree(root, tags)  # confused by the move: the reachability check drops it
