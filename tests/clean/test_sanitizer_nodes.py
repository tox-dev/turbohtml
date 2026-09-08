"""sanitize_node and friends: sanitizing an already parsed tree and getting a tree back."""

from __future__ import annotations

import pytest

from turbohtml import Document, Element, parse, parse_fragment, parse_xml
from turbohtml.clean import (
    OnDisallowed,
    Policy,
    Removed,
    Sanitizer,
    sanitize,
    sanitize_node,
    sanitize_report,
    sanitize_report_node,
)

_POST = "<p onclick='x'>Hi <b>there</b> <script>evil()</script></p>"


def test_node_form_returns_a_sanitized_copy_of_the_same_kind() -> None:
    root = parse_fragment(_POST)
    clean = sanitize_node(root, Policy.relaxed())
    assert isinstance(clean, Element)
    assert clean.inner_html == sanitize(_POST, Policy.relaxed())


def test_the_source_is_left_untouched() -> None:
    root = parse_fragment(_POST)
    before = root.inner_html
    sanitize_node(root, Policy.relaxed())
    assert root.inner_html == before


def test_the_node_itself_is_the_kept_context() -> None:
    # the policy never judges the node passed in, only its descendants, like the fragment root of the string form
    script = parse_fragment("<script>evil()</script>").select_one("script")
    assert script is not None
    clean = sanitize_node(script, Policy.relaxed())
    assert isinstance(clean, Element)
    assert clean.tag == "script"


def test_a_document_has_its_html_element_judged() -> None:
    document = parse("<p onclick='x'>hi</p>")
    clean = sanitize_node(document, Policy(tags=frozenset({"html", "head", "body", "p"})))
    assert isinstance(clean, Document)
    assert clean.serialize() == "<html><head></head><body><p>hi</p></body></html>"


def test_a_document_under_a_fragment_policy_loses_its_shell() -> None:
    clean = sanitize_node(parse("<p>hi</p>"), Policy(tags=frozenset({"p"}), on_disallowed_tag=OnDisallowed.STRIP))
    assert clean.serialize() == "<p>hi</p>"


def test_the_copy_inherits_the_xml_flag() -> None:
    root = parse_xml("<r><b/><script>x</script></r>").root
    assert root is not None
    clean = sanitize_node(root, Policy(tags=frozenset({"b"}), on_disallowed_tag=OnDisallowed.STRIP))
    assert clean.inner_xml == "<b/>x"


def test_the_string_forms_accept_a_node() -> None:
    root = parse_fragment(_POST)
    assert sanitize(root, Policy.relaxed()) == sanitize(_POST, Policy.relaxed())
    assert sanitize_report(root, Policy.relaxed()) == sanitize_report(_POST, Policy.relaxed())


def test_the_report_form_pairs_the_copy_with_the_drops() -> None:
    clean, removed = sanitize_report_node(parse_fragment(_POST), Policy.relaxed())
    assert clean.inner_html == "<p>Hi <b>there</b> &lt;script&gt;evil()&lt;/script&gt;</p>"
    assert removed == [Removed(tag="p", attribute="onclick"), Removed(tag="script", attribute=None)]


def test_a_reusable_sanitizer_offers_both_node_forms() -> None:
    sanitizer = Sanitizer(Policy.relaxed())
    root = parse_fragment(_POST)
    assert sanitizer.sanitize_node(root).inner_html == sanitizer.sanitize(_POST)
    assert sanitizer.sanitize_report_node(root)[1] == sanitizer.sanitize_report(_POST)[1]


@pytest.mark.parametrize("entry", [sanitize_node, sanitize_report_node], ids=["sanitize_node", "sanitize_report_node"])
def test_the_node_forms_refuse_a_str(entry: object) -> None:
    with pytest.raises(TypeError, match="pass a str to sanitize instead"):
        entry(_POST)  # ty: ignore[call-non-callable]  # the argument check is the point


@pytest.mark.parametrize("entry", [sanitize, sanitize_node], ids=["sanitize", "sanitize_node"])
def test_a_foreign_object_is_rejected(entry: object) -> None:
    with pytest.raises(TypeError):
        entry(42)  # ty: ignore[call-non-callable]  # the argument check is the point
