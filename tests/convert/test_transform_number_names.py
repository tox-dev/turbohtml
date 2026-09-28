from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Final

import pytest

from turbohtml import parse_fragment, parse_xml
from turbohtml.transform import Transform


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        pytest.param('<a:n xmlns:a="urn:x"/><b:n xmlns:b="urn:x"/>', "1,2,", id="alias"),
        pytest.param('<a:n xmlns:a="urn:x"/><a:n xmlns:a="urn:y"/>', "1,1,", id="rebound"),
        pytest.param('<n xmlns="urn:x"/><a:n xmlns:a="urn:x"/>', "1,2,", id="default-alias"),
        pytest.param('<n xmlns="urn:x"/><n/>', "1,1,", id="default-distinct"),
        pytest.param('<n/><n xmlns=""/>', "1,2,", id="empty-default"),
        pytest.param(
            '<n title="urn:y" xmlnswrong="urn:z" xmlns="urn:x"/><a:n xmlns:a="urn:x"/>',
            "1,2,",
            id="ordinary-attributes",
        ),
        pytest.param('<a:n xmlns:a="urn:x"/><a:m xmlns:a="urn:x"/>', "1,1,", id="local-name"),
        pytest.param('<a:n xmlns:a="urn:x"/><a:nn xmlns:a="urn:x"/>', "1,1,", id="local-length"),
        pytest.param("<xml:n/><n/>", "1,1,", id="reserved-distinct"),
        pytest.param('<abc:n xmlns:abc="urn:x"/><def:n xmlns:def="urn:x"/>', "1,2,", id="three-char-prefix"),
        pytest.param('<a:n xmlns:a="urn:x"/><b:n xmlns:b="urn:xx"/>', "1,1,", id="uri-length"),
        pytest.param("<?a first?><?b second?><?a third?>", "1,1,2,", id="pi-target"),
        pytest.param("<?a first?><?a second?><?a?>", "1,2,3,", id="pi-data"),
        pytest.param("<xml:n/><xml:n/>", "1,2,", id="reserved-prefix"),
        pytest.param('<a:日 xmlns:a="urn:日"/><b:日 xmlns:b="urn:日"/>', "1,2,", id="unicode-name-uri"),
        pytest.param('<a:𐀀 xmlns:a="urn:甲"/><b:𐀀 xmlns:b="urn:乙"/>', "1,1,", id="unicode-uri-distinct"),
        pytest.param('<é:n xmlns:é="urn:x"/><β:n xmlns:β="urn:x"/>', "1,2,", id="latin-prefix"),
        pytest.param('<日:n xmlns:日="urn:x"/><𐀀:n xmlns:𐀀="urn:x"/>', "1,2,", id="wide-prefix"),
        pytest.param('<a:n xmlns:ab="urn:y" xmlns:a="urn:x"/><b:n xmlns:b="urn:x"/>', "1,2,", id="prefix-length"),
        pytest.param('<aa:n xmlns:a="urn:y" xmlns:aa="urn:x"/><b:n xmlns:b="urn:x"/>', "1,2,", id="long-prefix"),
    ],
)
@pytest.mark.parametrize("level", ["single", "multiple", "any"])
@pytest.mark.parametrize("boundary", ["", ' from="root"'], ids=["no-from", "from"])
def test_transform_number_expanded_names(children: str, expected: str, level: str, boundary: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/node()">'
        f'<xsl:number level="{level}"{boundary}/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(parse_xml(f"<root>{children}</root>")) == expected


@pytest.mark.parametrize("level", ["single", "any"])
@pytest.mark.parametrize("boundary", ["", ' from="root"'], ids=["no-from", "from"])
def test_transform_number_namespace_cache_revisits(level: str, boundary: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/*"><xsl:sort select="@rank" data-type="number"/>'
        f'<xsl:number level="{level}"{boundary}/><xsl:text>:</xsl:text>'
        f'<xsl:number level="{level}"{boundary}/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    source: Final = parse_xml(
        '<root xmlns:a="urn:x" xmlns:b="urn:x" xmlns:c="urn:y">'
        '<a:n rank="5"/><b:n rank="2"/><c:n rank="3"/><b:n rank="1"/><a:n rank="4"/></root>'
    )
    assert Transform(sheet)(source) == "3:3,2:2,1:1,4:4,1:1,"


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        pytest.param("single", "2,", id="single"),
        pytest.param("multiple", "1.2,", id="multiple"),
        pytest.param("any", "3,", id="any"),
    ],
)
def test_transform_number_inherited_namespace(level: str, expected: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/*/*[2]">'
        f'<xsl:number level="{level}"/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    source: Final = parse_xml('<root xmlns:a="urn:x"><a:n xmlns:b="urn:x"><a:n/><b:n/></a:n></root>')
    assert Transform(sheet)(source) == expected


@pytest.mark.parametrize(
    ("declaration", "element"),
    [pytest.param("xmlns:b", "b:n", id="prefix"), pytest.param("xmlns", "n", id="default")],
)
def test_transform_number_mutated_xml_namespace(declaration: str, element: str) -> None:
    source: Final = parse_xml(f'<root {declaration}="urn:x"><xml:n/><{element}/></root>')
    assert source.root is not None
    source.root.attrs[declaration] = "http://www.w3.org/XML/1998/namespace"
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="/*/*"><xsl:number/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(source) == "1,2,"


def test_transform_number_html_foreign_namespace() -> None:
    source: Final = parse_fragment("<title>html</title><svg><title>svg</title><title>svg</title></svg>")
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="//title"><xsl:number level="any"/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(source) == "1,1,2,"


@pytest.mark.parametrize("level", ["single", "any"])
@pytest.mark.parametrize("prefix", ["", "alias:"], ids=["default", "prefixed"])
def test_transform_number_deep_namespace_scopes(level: str, prefix: str) -> None:
    declaration: Final = "xmlns:alias" if prefix else "xmlns"
    source: Final = parse_xml(
        f'<{prefix}n {declaration}="urn:x">'
        + "".join(f'<{prefix}n xmlns:other{index}="urn:{index}">' for index in range(127))
        + f"</{prefix}n>" * 128
    )
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        f'<xsl:for-each select="//*"><xsl:number level="{level}"/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(source) == "".join(f"{1 if level == 'single' else index}," for index in range(1, 129))


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_transform_number_namespace_scope_restoration(*, reverse: bool) -> None:
    source: Final = parse_xml(
        '<root xmlns:a="urn:x"><a:n rank="1"/>'
        '<section xmlns:a="urn:y"><a:n rank="2"/><section xmlns:a=""><n rank="3"/></section></section>'
        '<a:n rank="4"/><section xmlns:a="urn:z"><a:n rank="5"/></section><a:n rank="6"/></root>'
    )
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="//*[@rank]">'
        f'<xsl:sort select="@rank" data-type="number" order="{"descending" if reverse else "ascending"}"/>'
        '<xsl:number level="any"/><xsl:text>,</xsl:text></xsl:for-each></xsl:template></xsl:stylesheet>'
    )
    assert Transform(sheet)(source) == ("3,1,2,1,1,1," if reverse else "1,1,1,2,1,3,")


def test_transform_number_growing_unicode_prefixes() -> None:
    source: Final = parse_xml(
        "<root>"
        + "".join(f'<{prefix}:n xmlns:{prefix}="urn:x"/>' for prefix in ("日", "日" * 32, "日" * 64))
        + "</root>"
    )
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/*"><xsl:number/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(source) == "1,2,3,"


def test_transform_number_namespace_changes_between_calls() -> None:
    source: Final = parse_xml('<root xmlns:a="urn:x" xmlns:b="urn:y"><a:n/><b:n/></root>')
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/*"><xsl:number level="any"/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    transform: Final = Transform(sheet)
    before: Final = transform(source)
    assert source.root is not None
    source.root.attrs["xmlns:b"] = "urn:x"
    assert (before, transform(source)) == ("1,1,", "1,2,")


@pytest.mark.parametrize(
    ("container", "tag", "uri"),
    [
        pytest.param("svg", "title", "http://www.w3.org/2000/svg", id="svg"),
        pytest.param("math", "mi", "http://www.w3.org/1998/Math/MathML", id="mathml"),
    ],
)
@pytest.mark.parametrize("declaration", [None, "", "urn:other"], ids=["absent", "empty", "conflicting"])
@pytest.mark.parametrize("equivalent", [False, True], ids=["native-only", "parsed-equivalent"])
def test_transform_number_adopted_foreign_namespaces(
    container: str, tag: str, uri: str, declaration: str | None, *, equivalent: bool
) -> None:
    source: Final = parse_xml(f"<root><{tag}/></root>")
    root: Final = source.root
    assert root is not None
    foreign: Final = parse_fragment(f"<{container}><{tag}>value</{tag}></{container}>").select_one(tag)
    assert foreign is not None
    if declaration is not None:
        foreign.attrs["xmlns"] = declaration
    root.append(foreign)
    if equivalent:
        parsed: Final = parse_xml(f'<{tag} xmlns="{uri}"/>').root
        assert parsed is not None
        root.append(parsed)
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/*"><xsl:number level="any"/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(source) == ("1,1,2," if equivalent else "1,1,")


def test_transform_number_shared_namespace_index() -> None:
    transform: Final = Transform(
        parse_xml(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:output method="text"/><xsl:template match="/">'
            '<xsl:for-each select="root/*"><xsl:number level="any"/><xsl:text>,</xsl:text>'
            "</xsl:for-each></xsl:template></xsl:stylesheet>"
        )
    )
    barrier: Final = Barrier(4)

    def run(uri: str) -> str:
        source: Final = parse_xml(f'<root xmlns:a="urn:x" xmlns:b="{uri}"><a:n/><b:n/></root>')
        barrier.wait()
        return transform(source)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(run, ["urn:x", "urn:y", "urn:x", "urn:y"])) == ["1,2,", "1,1,", "1,2,", "1,1,"]
