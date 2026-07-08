"""The ``Policy.xml`` flag: a sanitized fragment serialized as well-formed XML/XHTML.

The walk is the same allowlist walk as the HTML path; only the final serialization differs, so these tests fix the XML
syntax (self-closing empty elements, XML escaping, foreign namespace declarations) and prove every output reparses
through :func:`turbohtml.parse_xml` -- the well-formedness guarantee the flag exists to give.
"""

from __future__ import annotations

import pytest

from turbohtml import parse_xml
from turbohtml.clean import Policy, Sanitizer, sanitize, sanitize_report

_XML = Policy(
    tags=frozenset({"p", "br", "a", "strong", "em", "img", "svg", "rect", "math", "mi"}),
    attributes={"a": frozenset({"href"}), "img": frozenset({"src"})},
    xml=True,
)


def _well_formed(fragment: str) -> None:
    """Assert a sanitized fragment reparses as XML once wrapped in a single root, so it is well-formed."""
    parse_xml(f"<root>{fragment}</root>")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>a<br>b</p>", "<p>a<br/>b</p>", id="void-self-closes"),
        pytest.param("<img src=a>", '<img src="a"/>', id="void-with-attr"),
        pytest.param("<strong>x & y < z</strong>", "<strong>x &amp; y &lt; z</strong>", id="text-escaped"),
        pytest.param('<a href="?a=1&b=2">l</a>', '<a href="?a=1&amp;b=2">l</a>', id="attr-escaped"),
        pytest.param("<svg><rect></svg>", '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', id="svg-namespace"),
        pytest.param(
            "<math><mi>x</mi></math>",
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>',
            id="mathml-namespace",
        ),
    ],
)
def test_xml_output_is_well_formed(html: str, expected: str) -> None:
    out = sanitize(html, _XML)
    assert out == expected
    _well_formed(out)


def test_control_characters_are_dropped_from_xml() -> None:
    out = sanitize("<p>bad\x0cchar\x01here</p>", _XML)
    assert out == "<p>badcharhere</p>"
    _well_formed(out)


def test_kept_comment_is_neutralized_for_xml() -> None:
    policy = Policy(tags=frozenset({"p"}), strip_comments=False, xml=True)
    out = sanitize("<p>a</p><!-- c--d- -->", policy)
    assert out == "<p>a</p><!-- c- -d- -->"
    _well_formed(out)


def test_default_policy_still_emits_html() -> None:
    assert sanitize("<p>a<br>b") == "&lt;p&gt;a&lt;br&gt;b"
    assert sanitize("<a href='http://x'>l</a>") == '<a href="http://x">l</a>'


def test_disallowed_tag_escaped_stays_well_formed() -> None:
    out = sanitize("<p>ok</p><script>evil()</script>", Policy(tags=frozenset({"p"}), xml=True))
    assert "<script" not in out
    _well_formed(out)


def test_report_reports_drops_and_emits_xml() -> None:
    out, removed = sanitize_report("<p>keep<br><span>drop</span></p>", _XML)
    assert out == "<p>keep<br/>&lt;span&gt;drop&lt;/span&gt;</p>"
    assert [item.tag for item in removed] == ["span"]
    _well_formed(out)


def test_sanitizer_instance_reuses_xml_policy() -> None:
    cleaner = Sanitizer(_XML)
    assert cleaner.sanitize("<br>") == "<br/>"
    assert cleaner.sanitize("<img src=x>") == '<img src="x"/>'
