from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG

_RNG: Final = "http://relaxng.org/ns/structure/1.0"


@pytest.mark.parametrize("as_node", [False, True], ids=["text", "node"])
@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(f'<externalRef xmlns="{_RNG}"/>', id="short-external"),
        pytest.param(f'<include xmlns="{_RNG}"/>', id="short-include"),
        pytest.param(f'<element xmlns="{_RNG}" name="foo"><externalRef/></element>', id="element-external"),
        pytest.param(
            f'<grammar xmlns="{_RNG}"><start><element name="foo"><externalRef/></element></start></grammar>',
            id="grammar-external",
        ),
        pytest.param(
            f'<grammar xmlns="{_RNG}"><include/><start><element name="foo"><empty/></element></start></grammar>',
            id="grammar-include",
        ),
        pytest.param(
            f'<rng:grammar xmlns:rng="{_RNG}"><rng:include/><rng:start><rng:element name="foo">'
            "<rng:empty/></rng:element></rng:start></rng:grammar>",
            id="prefixed-include",
        ),
        pytest.param(
            f'<rng:element xmlns:rng="{_RNG}" name="foo"><rng:externalRef/></rng:element>',
            id="prefixed-external",
        ),
        pytest.param(
            f'<element xmlns="{_RNG}" xmlns:doc="urn:annotation" name="foo"><externalRef doc:href="x"/></element>',
            id="foreign-href-attribute",
        ),
        pytest.param(
            f'<grammar xmlns="{_RNG}"><start><element name="foo"><empty/></element></start>'
            '<define name="unused"><externalRef/></define></grammar>',
            id="unused-definition-external",
        ),
    ],
)
def test_relaxng_missing_href_rejects_compilation(schema: str, *, as_node: bool) -> None:
    source: Final = parse_xml(schema) if as_node else schema
    for _ in range(2):
        with pytest.raises(ValueError, match="required href attribute"):
            RelaxNG(source)


@pytest.mark.parametrize(
    "annotation",
    [
        pytest.param("<doc:include/>", id="foreign-include"),
        pytest.param("<doc:externalRef/>", id="foreign-external"),
        pytest.param("<doc:annotation><externalRef/></doc:annotation>", id="foreign-subtree"),
    ],
)
def test_relaxng_missing_href_ignores_foreign_annotations(annotation: str) -> None:
    schema: Final = (
        f'<grammar xmlns="{_RNG}" xmlns:doc="urn:annotation">\n<!-- annotation -->'
        f'{annotation}<start><element name="foo"><empty/></element></start></grammar>'
    )
    validator: Final = RelaxNG(schema)
    assert [validator.validate(parse_xml(document)).valid for document in ("<foo/>", "<wrong/>")] == [True, False]


def test_relaxng_missing_href_preserves_ordinary_patterns() -> None:
    validator: Final = RelaxNG(f'<element xmlns="{_RNG}" name="foo"><text/></element>')
    assert [validator.validate(parse_xml(document)).valid for document in ("<foo>text</foo>", "<wrong/>")] == [
        True,
        False,
    ]
