from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import HTMLParseError, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable

PREFIX_COUNT: Final = 300


def _prefix(index: int) -> str:
    return f"n{index}"  # lengths 2-4, so probes meet both shorter and same-length different names


def _declared_on_root(uses: str) -> str:
    declarations: Final = " ".join(f'xmlns:{_prefix(index)}="urn:{index}"' for index in range(PREFIX_COUNT))
    return f"<root {declarations}><leaf {uses}/></root>"


def _declared_one_per_ancestor(uses: str) -> str:
    opening: Final = "".join(f'<level xmlns:{_prefix(index)}="urn:{index}">' for index in range(PREFIX_COUNT))
    return f"{opening}<leaf {uses}/>{'</level>' * PREFIX_COUNT}"


def _every_prefix_used() -> str:
    return " ".join(f'{_prefix(index)}:k="{index}"' for index in range(PREFIX_COUNT))


@pytest.fixture(
    params=[
        pytest.param(_declared_on_root, id="declared-on-root"),
        pytest.param(_declared_one_per_ancestor, id="declared-one-per-ancestor"),
    ]
)
def build(request: pytest.FixtureRequest) -> Callable[[str], str]:
    return request.param


def test_xml_namespace_many_prefixes_resolve_to_own_uri(build: Callable[[str], str]) -> None:
    leaf: Final = parse_xml(build(_every_prefix_used())).find("leaf")
    assert leaf is not None
    assert list(leaf.attrs.items()) == [(f"{_prefix(index)}:k", str(index)) for index in range(PREFIX_COUNT)]


@pytest.mark.parametrize(
    ("uses", "code"),
    [
        pytest.param(
            f'{_every_prefix_used()} xmlns:alias="urn:{PREFIX_COUNT - 1}" alias:k="x"',
            "xml-duplicate-attribute",
            id="alias-of-innermost",
        ),
        pytest.param(
            f'{_every_prefix_used()} xmlns:alias="urn:0" alias:k="x"',
            "xml-duplicate-attribute",
            id="alias-of-outermost",
        ),
        pytest.param(
            f'{_every_prefix_used()} n{PREFIX_COUNT}:k="x"', "xml-undeclared-namespace", id="undeclared-among-many"
        ),
    ],
)
def test_xml_namespace_many_prefixes_error(build: Callable[[str], str], uses: str, code: str) -> None:
    with pytest.raises(HTMLParseError) as error:
        parse_xml(build(uses))
    assert error.value.error.code == code


def _filler(count: int) -> str:
    return " ".join(f'xmlns:f{index}="urn:f{index}"' for index in range(count))


# The parser scans fewer than 16 in-scope bindings and moves them into a hash table at 16; these documents cross
# that cutoff with a shadowed binding live, either before the switch or after it.
SHADOWED_BEFORE_SWITCH: Final = (
    '<r xmlns:p="urn:a" xmlns:q="urn:a"><x xmlns:p="urn:b"><y ' + _filler(14) + '><z p:k="1" q:k="2"/></y>'
    '<w p:k="1" q:k="2"/></x>{after}</r>'
)
SHADOWED_AFTER_SWITCH: Final = (
    '<r xmlns:p="urn:a" xmlns:q="urn:a" ' + _filler(16) + '><x xmlns:p="urn:b"><z p:k="1" q:k="2"/></x>{after}</r>'
)


@pytest.mark.parametrize(
    "template",
    [
        pytest.param(SHADOWED_BEFORE_SWITCH, id="shadowed-before-switch"),
        pytest.param(SHADOWED_AFTER_SWITCH, id="shadowed-after-switch"),
    ],
)
def test_xml_namespace_table_switch_keeps_shadow(template: str) -> None:
    parse_xml(template.format(after='<v xmlns:q="urn:c" p:k="1" q:k="2"/>'))


@pytest.mark.parametrize(
    "template",
    [
        pytest.param(SHADOWED_BEFORE_SWITCH, id="shadowed-before-switch"),
        pytest.param(SHADOWED_AFTER_SWITCH, id="shadowed-after-switch"),
    ],
)
def test_xml_namespace_table_switch_restores_outer(template: str) -> None:
    with pytest.raises(HTMLParseError) as error:
        parse_xml(template.format(after='<v p:k="1" q:k="2"/>'))
    assert error.value.error.code == "xml-duplicate-attribute"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('<r xmlns:p="urn:a"><x><y><z p:k="1"/></y></x></r>', id="attribute-three-levels-below"),
        pytest.param('<r xmlns:p="urn:a"><p:x><p:y/></p:x></r>', id="element-names-below-declaration"),
        pytest.param(
            '<r xmlns:p="urn:a" xmlns:q="urn:b"><x xmlns:p="urn:c" p:k="1" q:k="2"/></r>', id="shadow-binds-new-uri"
        ),
        pytest.param(
            '<r xmlns:p="urn:a" xmlns:q="urn:b"><x xmlns:p="urn:b"/><y p:k="1" q:k="2"/></r>',
            id="shadow-restored-after-self-closing",
        ),
        pytest.param(
            '<r xmlns:p="urn:a" xmlns:q="urn:b"><x xmlns:p="urn:b"></x><y p:k="1" q:k="2"/></r>',
            id="shadow-restored-after-end-tag",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><x xmlns:p="urn:b"><y xmlns:p="urn:c"></y><z xmlns:q="urn:c" p:k="1" q:k="2"/></x></r>',
            id="nested-shadow-unwinds-one-level",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><x><p:y xmlns:p="urn:b"><p:z/></p:y><p:w/></x></r>',
            id="element-prefix-redeclared-in-place",
        ),
        pytest.param(
            '<r xmlns:xml="http://www.w3.org/XML/1998/namespace"><x><y xml:lang="en"/></x></r>',
            id="xml-declared-on-root",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><x><y xml:lang="en" p:lang="fr"/></x></r>', id="xml-alongside-declared-prefix"
        ),
    ],
)
def test_xml_namespace_scope_parses(source: str) -> None:
    parse_xml(source)


@pytest.mark.parametrize(
    ("source", "code"),
    [
        pytest.param(
            '<r><x xmlns:p="urn:a"/><p:y/></r>', "xml-undeclared-namespace", id="element-after-self-closing-scope"
        ),
        pytest.param(
            '<r><x xmlns:p="urn:a"></x><y p:k="1"/></r>', "xml-undeclared-namespace", id="attribute-after-end-tag-scope"
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><x xmlns:q="urn:b"/><q:y/></r>',
            "xml-undeclared-namespace",
            id="sibling-scope-not-inherited",
        ),
        pytest.param(
            '<r xmlns:p="urn:a" xmlns:q="urn:a"><x><y p:k="1" q:k="2"/></x></r>',
            "xml-duplicate-attribute",
            id="ancestor-bindings-share-uri",
        ),
        pytest.param(
            '<r xmlns:p="urn:a" xmlns:q="urn:b"><x xmlns:p="urn:b" p:k="1" q:k="2"/></r>',
            "xml-duplicate-attribute",
            id="shadow-collides-with-other-prefix",
        ),
        pytest.param(
            '<r xmlns:p="urn:a" xmlns:q="urn:a"><x xmlns:p="urn:b"/><y p:k="1" q:k="2"/></r>',
            "xml-duplicate-attribute",
            id="restored-after-self-closing-collides",
        ),
        pytest.param(
            '<r xmlns:p="urn:a" xmlns:q="urn:a"><x xmlns:p="urn:b"></x><y p:k="1" q:k="2"/></r>',
            "xml-duplicate-attribute",
            id="restored-after-end-tag-collides",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><x xmlns:p="urn:b"><y xmlns:p="urn:c"></y><z xmlns:q="urn:b" p:k="1" q:k="2"/></x></r>',
            "xml-duplicate-attribute",
            id="nested-shadow-restores-middle-binding",
        ),
        pytest.param(
            '<r xmlns:p="urn:a"><xml:x/><xmlns:y/></r>', "xml-undeclared-namespace", id="xmlns-prefix-on-element"
        ),
    ],
)
def test_xml_namespace_scope_error(source: str, code: str) -> None:
    with pytest.raises(HTMLParseError) as error:
        parse_xml(source)
    assert error.value.error.code == code
