"""The XML sanitizer's output cross-checked against lxml as an independent well-formedness oracle.

lxml is a bench dependency, not a test one, and ships no wheels for 3.15, the free-threaded builds, or Windows, so this
module importorskips itself where the oracle is absent and is omitted from the coverage gate (see ``[tool.coverage]``).
It still runs and validates wherever lxml installs. The claim is narrow and load-bearing: whatever ``Policy(xml=True)``
emits, a second, unrelated XML parser accepts without error -- turbohtml's own :func:`parse_xml` agreeing with itself
would not prove well-formedness.
"""

from __future__ import annotations

import pytest

from turbohtml.clean import Policy, sanitize

_XML = Policy(
    tags=frozenset({"p", "br", "a", "strong", "em", "img", "ul", "li", "svg", "rect", "circle", "math", "mi"}),
    attributes={"a": frozenset({"href"}), "img": frozenset({"src", "alt"})},
    strip_comments=False,
    xml=True,
)


@pytest.mark.parametrize(
    "html",
    [
        pytest.param("<p>a<br>b<br>c</p>", id="voids"),
        pytest.param("<ul><li>one<li>two</ul>", id="implied-end-tags"),
        pytest.param('<a href="?x=1&y=2&z">link & more</a>', id="entities"),
        pytest.param("<p>a<!-- c--d- -->b</p>", id="comment"),
        pytest.param("<svg><rect/><circle/></svg>", id="svg"),
        pytest.param("<math><mi>x</mi></math>", id="mathml"),
        pytest.param("<p>ctrl\x0c\x01chars</p>", id="control-chars"),
        pytest.param("<img src=x alt='a<b\"c'>", id="attr-specials"),
        pytest.param("<p>text</p><script>evil()</script><em>more</em>", id="dropped-script"),
    ],
)
def test_output_parses_under_lxml(html: str) -> None:
    lxml_etree = pytest.importorskip("lxml.etree")
    fragment = sanitize(html, _XML)
    lxml_etree.fromstring(f"<root>{fragment}</root>".encode())
