from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Final

import pytest

from turbohtml import Element, parse_fragment, parse_xml
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


@pytest.mark.parametrize("level", ["single", "multiple", "any"])
@pytest.mark.parametrize(
    ("children", "expected"),
    [
        pytest.param('<a:n xmlns:a="urn:x"/><n/>', "1", id="prefixed"),
        pytest.param("<xml:n/><n/>", "1", id="reserved"),
        pytest.param("<long/><n/>", "1", id="different-length"),
        pytest.param('<n title="x" longattribute="y"/><n/>', "2", id="ordinary-attributes"),
    ],
)
def test_transform_number_unprefixed_selection(level: str, children: str, expected: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/n[last()]">'
        f'<xsl:number level="{level}" from="root"/>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(parse_xml(f"<root>{children}</root>")) == expected


@pytest.mark.parametrize(
    ("name", "foreign", "expected"),
    [
        pytest.param("a:title", False, "2", id="unbound"),
        pytest.param("abc:title", False, "2", id="three-char-prefix"),
        pytest.param("a:other", False, "1", id="distinct-local"),
        pytest.param("xml:title", False, "1", id="reserved"),
        pytest.param("a:title", True, "2", id="foreign-unbound"),
        pytest.param("xml:title", True, "2", id="foreign-reserved"),
    ],
)
def test_transform_number_renamed_prefix(name: str, expected: str, *, foreign: bool) -> None:
    source: Final = parse_xml("<root/>")
    root: Final = source.root
    assert root is not None
    for index in range(2):
        node = parse_fragment("<svg><title/></svg>").select_one("title") if foreign else parse_xml("<title/>").root
        assert node is not None
        root.append(node)
        if index == 0:
            node.tag = name
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/*[last()]"><xsl:number from="root"/>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(source) == expected


@pytest.mark.parametrize(
    ("attributes", "source", "expected"),
    [
        pytest.param(
            'count="n" from="root" level="any" format="01"',
            "<root><n/><n/></root>",
            "00,01,02,",
            id="patterns",
        ),
        pytest.param('value="1234" grouping-separator="-" grouping-size="3"', "<root/>", "1-234,", id="grouping"),
    ],
)
def test_transform_number_keeps_stylesheet_attributes(attributes: str, source: str, expected: str) -> None:
    stylesheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        f'<xsl:for-each select="//*"><xsl:number {attributes}/>,</xsl:for-each>'
        "</xsl:template></xsl:stylesheet>"
    )
    transform: Final = Transform(stylesheet)
    instruction: Final = stylesheet.find("xsl:number")
    assert instruction is not None
    instruction.attrs.clear()
    assert transform(parse_xml(source)) == expected


@pytest.mark.parametrize("container", ["svg", "math"])
@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        pytest.param({"COUNT": "n", "FROM": "root", "LEVEL": "any", "FORMAT": "01"}, "0102", id="patterns"),
        pytest.param({"VALUE": "1234", "GROUPING-SEPARATOR": "-", "GROUPING-SIZE": "3"}, "1-2341-234", id="value"),
    ],
)
def test_transform_number_adopted_instruction(container: str, attributes: dict[str, str], expected: str) -> None:
    stylesheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/n"/></xsl:template></xsl:stylesheet>'
    )
    target: Final = stylesheet.find("xsl:for-each")
    instruction: Final = parse_fragment(f"<{container}><title/></{container}>").select_one("title")
    assert target is not None
    assert instruction is not None
    target.append(instruction)
    instruction.tag = "xsl:number"
    instruction.attrs.update(attributes)
    assert Transform(stylesheet)(parse_xml("<root><n/><n/></root>")) == expected


@pytest.mark.parametrize(
    ("declarations", "body", "expected"),
    [
        pytest.param(
            "",
            '<xsl:template match="/"><xsl:for-each select="//*">'
            '<xsl:number level="any" count="n"/><xsl:text>|</xsl:text></xsl:for-each></xsl:template>',
            "0|1|1|2|",
            id="number-unprefixed",
        ),
        pytest.param(
            'xmlns:a="urn:x"',
            '<xsl:template match="/"><xsl:for-each select="//*">'
            '<xsl:number level="any" count="a:n"/><xsl:text>|</xsl:text></xsl:for-each></xsl:template>',
            "0|0|1|1|",
            id="number-prefixed",
        ),
        pytest.param(
            "",
            '<xsl:template match="/"><xsl:apply-templates select="//*"/></xsl:template>'
            '<xsl:template match="n">n</xsl:template><xsl:template match="*">x</xsl:template>',
            "xnxn",
            id="template-unprefixed",
        ),
        pytest.param(
            'xmlns:a="urn:x"',
            '<xsl:template match="/"><xsl:apply-templates select="//*"/></xsl:template>'
            '<xsl:template match="a:n">a</xsl:template><xsl:template match="*">x</xsl:template>',
            "xxax",
            id="template-prefixed",
        ),
        pytest.param(
            "",
            '<xsl:key name="k" match="n" use="@id"/>'
            "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k', '2'))\"/></xsl:template>",
            "0",
            id="key-unprefixed",
        ),
        pytest.param(
            'xmlns:a="urn:x"',
            '<xsl:key name="k" match="a:n" use="@id"/>'
            "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k', '2'))\"/></xsl:template>",
            "1",
            id="key-prefixed",
        ),
        pytest.param(
            'xmlns:a="urn:x"',
            '<xsl:template match="/"><xsl:apply-templates select="//*"/></xsl:template>'
            '<xsl:template match="n[@a:id]">a</xsl:template><xsl:template match="*">x</xsl:template>',
            "xaxx",
            id="attribute-prefixed",
        ),
    ],
)
def test_transform_pattern_expanded_names(declarations: str, body: str, expected: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        f'{declarations}><xsl:output method="text"/>{body}</xsl:stylesheet>'
    )
    source: Final = parse_xml('<root><n id="1" xmlns:a="urn:x" a:id="x"/><n id="2" xmlns="urn:x"/><n id="3"/></root>')
    assert Transform(sheet)(source) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            '<xsl:template match="/"><xsl:apply-templates select="//*"/></xsl:template>'
            '<xsl:template xmlns:p="urn:y" match="p:n">Y</xsl:template>'
            '<xsl:template match="p:n">X</xsl:template><xsl:template match="*">.</xsl:template>',
            ".XYXY",
            id="template",
        ),
        pytest.param(
            '<xsl:template match="/"><xsl:for-each select="//*">'
            '<xsl:number count="p:n" level="any" xmlns:p="urn:y"/>|'
            "</xsl:for-each></xsl:template>",
            "0|0|1|1|2|",
            id="number",
        ),
        pytest.param(
            '<xsl:key xmlns:p="urn:y" name="k" match="p:n" use="name()"/>'
            "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k', 'b:n'))\"/></xsl:template>",
            "1",
            id="key",
        ),
    ],
)
def test_transform_pattern_namespace_rebinding(body: str, expected: str) -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        f'xmlns:p="urn:x"><xsl:output method="text"/>{body}</xsl:stylesheet>'
    )
    source: Final = parse_xml(
        '<root xmlns:a="urn:x" xmlns:b="urn:y"><a:n/><b:n/><c:n xmlns:c="urn:x"/><n xmlns="urn:y"/></root>'
    )
    assert Transform(sheet)(source) == expected


def test_transform_number_pattern_namespace_scopes() -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="root/*">'
        '<xsl:number level="any" count="p:n" xmlns:p="urn:x"/>:'
        '<xsl:number level="any" count="p:n" xmlns:p="urn:y"/>, '
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    source: Final = parse_xml('<root xmlns:a="urn:x" xmlns:b="urn:y"><a:n/><b:n/></root>')
    assert Transform(sheet)(source) == "1:0, 1:1, "


def test_transform_pattern_attribute_namespaces() -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:p="urn:x">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:apply-templates select="root/n/@*"/></xsl:template>'
        '<xsl:template match="@id">I</xsl:template>'
        '<xsl:template match="@p:id">P</xsl:template>'
        '<xsl:template match="@xml:lang">L</xsl:template>'
        '<xsl:template match="@*">.</xsl:template></xsl:stylesheet>'
    )
    source: Final = parse_xml(
        '<root xmlns:p="urn:x" xmlns:q="urn:y"><n id="0" p:id="1" q:id="2" xml:lang="en"/></root>'
    )
    assert Transform(sheet)(source) == "IP.L"


@pytest.mark.parametrize(
    ("container", "tag", "uri"),
    [
        pytest.param("svg", "title", "http://www.w3.org/2000/svg", id="svg"),
        pytest.param("math", "mi", "http://www.w3.org/1998/Math/MathML", id="mathml"),
    ],
)
def test_transform_pattern_adopted_foreign_namespace(container: str, tag: str, uri: str) -> None:
    source: Final = parse_xml("<root/>")
    root: Final = source.root
    foreign: Final = parse_fragment(f"<{container}><{tag}/></{container}>").select_one(tag)
    assert root is not None
    assert foreign is not None
    root.append(foreign)
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        f'xmlns:p="{uri}"><xsl:output method="text"/>'
        '<xsl:template match="/"><xsl:apply-templates select="//*"/></xsl:template>'
        f'<xsl:template match="p:{tag}">match</xsl:template>'
        '<xsl:template match="*">other</xsl:template></xsl:stylesheet>'
    )
    assert Transform(sheet)(source) == "othermatch"


@pytest.mark.parametrize(
    ("source_text", "pattern", "binding", "expected"),
    [
        pytest.param(
            '<root xmlns:abc="urn:x"><n abc:id="1"/></root>', "n[@p:id]", None, "othermatch", id="three-letter"
        ),
        pytest.param("<root><xml:n/><n/></root>", "xml:n", None, "othermatchother", id="implicit-xml"),
        pytest.param("<root><n/></root>", "n[@p:id]", "unbound", "otherother", id="unbound-source"),
        pytest.param(
            '<root xmlns:abc="urn:x"><n abc:é="1"/></root>', "n[@p:é]", None, "othermatch", id="unicode-attribute"
        ),
        pytest.param(
            '<root xmlns:abc="urn:x"><n abc:é="1"/></root>', "n[@p:ê]", None, "otherother", id="unicode-mismatch"
        ),
        pytest.param(
            '<root xmlns:abc="urn:x"><n abc:a="1"/></root>', "n[@p:ab]", None, "otherother", id="short-attribute"
        ),
        pytest.param('<root xmlns:abc="urn:x"><n abc:a="1"/></root>', "n[@p:é]", None, "otherother", id="short-utf8"),
        pytest.param('<root xmlns:abc="urn:x"><n abc:id="1"/></root>', "n[@id]", "empty", "othermatch", id="empty-uri"),
    ],
)
def test_transform_pattern_source_prefix_cases(
    source_text: str, pattern: str, binding: str | None, expected: str
) -> None:
    source: Final = parse_xml(source_text)
    root: Final = source.root
    assert root is not None
    if binding == "unbound":
        child: Final = root.children[0]
        assert isinstance(child, Element)
        child.attrs["abc:id"] = "1"
    elif binding == "empty":
        root.attrs["xmlns:abc"] = ""
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:p="urn:x">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:apply-templates select="//*"/></xsl:template>'
        f'<xsl:template match="{pattern}">match</xsl:template>'
        '<xsl:template match="*">other</xsl:template></xsl:stylesheet>'
    )
    assert Transform(sheet)(source) == expected


def test_transform_pattern_html_key() -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:key name="k" match="n" use="@id"/>'
        "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k', '2'))\"/>"
        "</xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(parse_fragment('<n id="1"/><n id="2"/>')) == "1"


def test_transform_pattern_html_number() -> None:
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        '<xsl:for-each select="//n"><xsl:number level="any" count="n"/><xsl:text>,</xsl:text>'
        "</xsl:for-each></xsl:template></xsl:stylesheet>"
    )
    assert Transform(sheet)(parse_fragment("<div><n/><n/></div>")) == "1,2,"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            '<xsl:template match="/"><xsl:apply-templates select="//*"/></xsl:template>'
            '<xsl:template match="title">match</xsl:template>'
            '<xsl:template match="*">other</xsl:template>',
            "othermatchother",
            id="template",
        ),
        pytest.param(
            '<xsl:template match="/"><xsl:for-each select="//*">'
            '<xsl:number level="any" count="title"/>|</xsl:for-each></xsl:template>',
            "0|1|1|",
            id="number",
        ),
        pytest.param(
            '<xsl:key name="k" match="title" use="@id"/>'
            "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k', '2'))\"/></xsl:template>",
            "0",
            id="key",
        ),
        pytest.param(
            '<xsl:key name="k" match="title" use="@id"/>'
            "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k', '1'))\"/></xsl:template>",
            "1",
            id="key-native",
        ),
    ],
)
def test_transform_pattern_adopted_foreign_unprefixed(body: str, expected: str) -> None:
    source: Final = parse_xml('<root><title id="1"/></root>')
    root: Final = source.root
    foreign: Final = parse_fragment('<svg><title id="2"/></svg>').select_one("title")
    assert root is not None
    assert foreign is not None
    root.append(foreign)
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        f'<xsl:output method="text"/>{body}</xsl:stylesheet>'
    )
    assert Transform(sheet)(source) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [pytest.param("p", "2", id="same-case"), pytest.param("P", "0", id="case-sensitive")],
)
def test_transform_pattern_key_adopted_html_atom(name: str, expected: str) -> None:
    source: Final = parse_xml('<root><p key="same"/></root>')
    root: Final = source.root
    adopted: Final = parse_fragment('<p key="same"/>').select_one("p")
    assert root is not None
    assert adopted is not None
    root.append(adopted)
    sheet: Final = parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        f'<xsl:output method="text"/><xsl:key name="k" match="{name}" use="@key"/>'
        "<xsl:template match=\"/\"><xsl:value-of select=\"count(key('k', 'same'))\"/></xsl:template>"
        "</xsl:stylesheet>"
    )
    assert Transform(sheet)(source) == expected
