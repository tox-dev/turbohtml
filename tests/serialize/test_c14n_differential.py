"""Canonical XML cross-checked byte-for-byte against lxml's c14n.

lxml is a bench dependency, not a test one, and ships no wheels for 3.15, the free-threaded builds, or Windows, so this
module importorskips itself where the oracle is absent and is omitted from the coverage gate (see ``[tool.coverage]``).
It still runs and validates wherever lxml installs.

turbohtml canonicalizes its own HTML infoset; the oracle tree is built by reparsing turbohtml's XML serialization, so
namespace-declaration placement matches and the comparison is a genuine byte-exact c14n check over the same infoset.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from turbohtml import Canonical, Html, parse

_NS = {"s": "http://www.w3.org/2000/svg", "m": "http://www.w3.org/1998/Math/MathML"}


@pytest.mark.parametrize(
    ("html", "exclusive", "with_comments"),
    [
        pytest.param("<p z=1 a=2>x&amp;y</p>", False, False, id="attr-order"),
        pytest.param("<div><br><p class='a&b<c'>x&amp;<b>y</b></p></div>", False, False, id="mixed"),
        pytest.param("<p title='a\tb\nc\rd'>t&lt;u&gt;v</p>", False, False, id="char-refs"),
        pytest.param("<a> keep  the   spaces <b>y</b> here </a>", False, False, id="whitespace"),
        pytest.param("<svg xlink:href=x><a xlink:title=t><rect/></a></svg>", False, False, id="foreign-xlink"),
        pytest.param("<math><mi mathvariant=bold>x</mi></math>", False, False, id="mathml"),
        pytest.param("<a><!--note--><b>y</b><!--tail--></a>", True, False, id="comments-dropped"),
        pytest.param("<a><!--note--><b>y</b></a>", False, True, id="comments-kept"),
        pytest.param("<p>caf\xe9 → \xa9</p>", False, False, id="non-ascii"),
        pytest.param("<svg xlink:href=x><g><rect/></g></svg>", True, False, id="exclusive-doc"),
    ],
)
def test_whole_document_matches_lxml(html: str, exclusive: bool, with_comments: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    etree = pytest.importorskip("lxml.etree")
    tree = parse(html)
    ours = tree.canonicalize(Canonical(exclusive=exclusive, with_comments=with_comments))
    reparsed = etree.parse(BytesIO(tree.serialize(Html(xml=True)).encode()))
    sink = BytesIO()
    reparsed.write_c14n(sink, exclusive=exclusive, with_comments=with_comments)
    assert ours == sink.getvalue()


@pytest.mark.parametrize(
    ("selector", "xpath", "exclusive", "prefixes"),
    [
        pytest.param("g", ".//s:g", False, None, id="subtree-inclusive"),
        pytest.param("g", ".//s:g", True, None, id="subtree-exclusive-drops-unused"),
        pytest.param("g", ".//s:g", True, ["xlink"], id="subtree-exclusive-promotes-prefix"),
        pytest.param("svg", ".//s:svg", True, None, id="subtree-exclusive-renders-on-user"),
    ],
)
def test_subtree_matches_lxml(selector: str, xpath: str, exclusive: bool, prefixes: list[str] | None) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    etree = pytest.importorskip("lxml.etree")
    tree = parse("<svg xlink:href=x><g><rect/></g></svg>")
    node = tree.select_one(selector)
    assert node is not None
    ours = node.canonicalize(Canonical(exclusive=exclusive, inclusive_ns_prefixes=tuple(prefixes or ())))
    root = etree.fromstring(tree.serialize(Html(xml=True)).encode())
    target = root.find(xpath, namespaces=_NS)
    theirs = etree.tostring(target, method="c14n", exclusive=exclusive, inclusive_ns_prefixes=prefixes)
    assert ours == theirs
