from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG

if TYPE_CHECKING:
    from pathlib import Path

_NS: Final[str] = 'xmlns="http://relaxng.org/ns/structure/1.0"'
_ELEMENT: Final[str] = f'<element {_NS} name="foo"><empty/></element>'
_LONG: Final = "p" * 260
_START_FOO: Final[str] = f'<grammar {_NS}><start><element name="foo"><empty/></element></start></grammar>'


def _compile(directory: Path, files: dict[str, str], **options: str | Path) -> RelaxNG:
    for name, text in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text)
    return RelaxNG(files["main.rng"], base_url=str(directory / "main.rng"), **options)


def _verdicts(schema: RelaxNG, *documents: str) -> list[bool]:
    return [schema.validate(parse_xml(document)).valid for document in documents]


@pytest.mark.parametrize(
    ("files", "documents", "expected"),
    [
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="sub/x"/>', "sub/x": _ELEMENT},
            ("<foo/>", "<bar/>"),
            [True, False],
            id="external-root",
        ),
        pytest.param(
            {
                "main.rng": f'<element {_NS} name="r"><externalRef xml:base="sub/y" href="x"/></element>',
                "sub/x": _ELEMENT,
            },
            ("<r><foo/></r>", "<r><bar/></r>"),
            [True, False],
            id="xml-base-file",
        ),
        pytest.param(
            {
                "main.rng": f'<group {_NS} xml:base="sub1/"><group xml:base="sub2"><group xml:base="sub3/y">'
                '<externalRef href="x"/></group></group></group>',
                "x": f'<element {_NS} name="bar"><empty/></element>',
                "sub1/x": f'<element {_NS} name="bar"><empty/></element>',
                "sub1/sub3/x": _ELEMENT,
            },
            ("<foo/>", "<bar/>"),
            [True, False],
            id="xml-base-composed",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="x" ns="urn:example"/>', "x": _ELEMENT},
            ('<foo xmlns="urn:example"/>', "<foo/>"),
            [True, False],
            id="external-ns-transferred",
        ),
        pytest.param(
            {"main.rng": f'<group {_NS} ns="urn:example"><externalRef href="x"/></group>', "x": _ELEMENT},
            ('<foo xmlns="urn:example"/>', "<foo/>"),
            [True, False],
            id="external-ns-inherited",
        ),
        pytest.param(
            {
                "main.rng": f'<externalRef {_NS} href="x" ns="urn:outer"/>',
                "x": f'<element {_NS} name="foo" ns="urn:inner"><empty/></element>',
            },
            ('<foo xmlns="urn:inner"/>', '<foo xmlns="urn:outer"/>'),
            [True, False],
            id="external-ns-kept",
        ),
        pytest.param(
            {
                "main.rng": f'<externalRef {_NS} href="sub/x"/>',
                "sub/x": f'<externalRef {_NS} href="sub/x"/>',
                "sub/sub/x": _ELEMENT,
            },
            ("<foo/>", "<bar/>"),
            [True, False],
            id="same-href-other-directory",
        ),
        pytest.param(
            {"main.rng": f'<grammar {_NS}><include href="x"/></grammar>', "x": _START_FOO},
            ("<foo/>", "<bar/>"),
            [True, False],
            id="include",
        ),
        pytest.param(
            {
                "main.rng": f'<choice {_NS}><externalRef href="x"/><externalRef href="y"/></choice>',
                "x": _ELEMENT,
                "y": f'<element {_NS} name="bar"><empty/></element>',
            },
            ("<foo/>", "<bar/>", "<baz/>"),
            [True, True, False],
            id="two-references",
        ),
        pytest.param(
            {"main.rng": f'<grammar {_NS}><include href="x" ns="urn:example"/></grammar>', "x": _START_FOO},
            ('<foo xmlns="urn:example"/>', "<foo/>"),
            [True, False],
            id="include-ns",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}><include href="x"/></grammar>',
                "x": f'<grammar {_NS}><include href="y"/></grammar>',
                "y": _START_FOO,
            },
            ("<foo/>", "<bar/>"),
            [True, False],
            id="include-nested",
        ),
        pytest.param(
            {
                "main.rng": '<rng:grammar xmlns:rng="http://relaxng.org/ns/structure/1.0"><rng:include href="x">'
                '<rng:start><rng:element name="bar"><rng:empty/></rng:element></rng:start>'
                "</rng:include></rng:grammar>",
                "x": f'<grammar {_NS}><div><start><element name="foo"><empty/></element></start></div></grammar>',
            },
            ("<bar/>", "<foo/>"),
            [True, False],
            id="include-overrides-start",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}><start><ref name="foo"/></start><include href="x"><div>'
                '<define name="foo" combine="choice"><element name="foo1"><empty/></element></define></div>'
                '</include><define name="foo"><element name="foo2"><empty/></element></define></grammar>',
                "x": f'<grammar {_NS}><define><empty/></define><define name="foo" combine="choice">'
                '<element name="foo3"><empty/></element></define></grammar>',
            },
            ("<foo1/>", "<foo2/>", "<foo3/>"),
            [True, True, False],
            id="include-overrides-define",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}><start combine="choice"><element name="bar"><empty/></element></start>'
                '<include href="x"/></grammar>',
                "x": f'<grammar {_NS}><start combine="choice"><element name="foo"><empty/></element></start></grammar>',
            },
            ("<foo/>", "<bar/>", "<baz/>"),
            [True, True, False],
            id="include-combines-start",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS} datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
                '<include href="x"/></grammar>',
                "x": f'<grammar {_NS}><start><element name="n"><data type="integer"/></element></start></grammar>',
            },
            ("<n>text</n>", "<m>text</m>"),
            [True, False],
            id="datatype-library-not-inherited",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS} datatypeLibrary=""><include href="x"/></grammar>',
                "x": f'<grammar {_NS} datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes"><start>'
                '<element name="n"><data type="integer"/></element></start></grammar>',
            },
            ("<n>12</n>", "<n>text</n>"),
            [True, False],
            id="datatype-library-own",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}>\n<include href="x">\n<define><empty/></define>\n</include>\n</grammar>',
                "x": _START_FOO,
            },
            ("<foo/>", "<bar/>"),
            [True, False],
            id="include-nameless-override",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS} xmlns:doc="urn:doc"><include href="x"/></grammar>',
                "x": f'<grammar {_NS} xmlns:doc="urn:doc"><doc:note><include href="missing"/></doc:note>'
                '<start><element name="foo"><empty/></element></start></grammar>',
            },
            ("<foo/>", "<bar/>"),
            [True, False],
            id="annotation-reference-ignored",
        ),
    ],
)
def test_relaxng_resources_accept(
    tmp_path: Path, files: dict[str, str], documents: tuple[str, ...], expected: list[bool]
) -> None:
    assert _verdicts(_compile(tmp_path, files, include_root=tmp_path), *documents) == expected


@pytest.mark.parametrize(
    ("files", "error", "match"),
    [
        pytest.param(
            {"main.rng": f'<group {_NS} xml:base="http://["><externalRef href="x"/></group>'},
            ValueError,
            "Invalid IPv6 URL",
            id="malformed-xml-base",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="http://["/>'},
            ValueError,
            "Invalid IPv6 URL",
            id="malformed-href",
        ),
        pytest.param(
            {"main.rng": f'<grammar {_NS}><include/><start><externalRef href="x"/></start></grammar>', "x": _ELEMENT},
            ValueError,
            "missing the required href attribute",
            id="include-without-href-beside-reference",
        ),
        pytest.param(
            {"main.rng": f'<choice {_NS}><externalRef/><externalRef href="x"/></choice>', "x": _ELEMENT},
            ValueError,
            "missing the required href attribute",
            id="external-without-href-beside-reference",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="x#foo"/>', "x": _ELEMENT},
            ValueError,
            "must not contain a fragment identifier",
            id="fragment",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="x"/>', "x": f'<externalRef {_NS} href="x"/>'},
            ValueError,
            r"circular RELAX NG reference: .*main\.rng -> .*x -> .*x$",
            id="self-loop",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}><include href="x"/></grammar>',
                "x": f'<grammar {_NS}><include href="y"/></grammar>',
                "y": f'<grammar {_NS}><include href="x"/></grammar>',
            },
            ValueError,
            "circular RELAX NG reference",
            id="include-loop",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="x"/>', "x": f"<start {_NS}>{_ELEMENT}</start>"},
            ValueError,
            "externalRef target is not a pattern",
            id="external-not-pattern",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="x"/>', "x": "<foo/>"},
            ValueError,
            "externalRef target is not a pattern",
            id="external-foreign-root",
        ),
        pytest.param(
            {"main.rng": f'<grammar {_NS}><include href="x"/></grammar>', "x": _ELEMENT},
            ValueError,
            "include target is not a grammar",
            id="include-not-grammar",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}><include href="x"><start><ref name="foo"/></start></include></grammar>',
                "x": f'<grammar {_NS}><define name="foo">{_ELEMENT}</define></grammar>',
            },
            ValueError,
            "overrides start, which the included grammar does not define",
            id="override-missing-start",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}><start><ref name="foo"/></start><include href="x"><div>'
                f'<define name="foo">{_ELEMENT}</define></div></include></grammar>',
                "x": f'<grammar {_NS}><start>{_ELEMENT}</start><define name="bar">{_ELEMENT}</define></grammar>',
            },
            ValueError,
            "overrides define 'foo', which the included grammar does not define",
            id="override-missing-define",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="../outside.rng"/>'},
            ValueError,
            "RELAX NG path escapes include_root",
            id="escapes-root",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="http://example.com/x.rng"/>'},
            ValueError,
            "RELAX NG href must be a local path or file URL",
            id="remote-href",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="sub"/>', "sub/x": _ELEMENT},
            ValueError,
            "RELAX NG target is not a regular file",
            id="directory-target",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="missing"/>'},
            FileNotFoundError,
            "missing",
            id="missing-file",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="x"/>', "x": "<element"},
            ValueError,
            "malformed schema",
            id="malformed-target",
        ),
        pytest.param(
            {"main.rng": f'<externalRef {_NS} href="x"/>', "x": f"<group {_NS}>{'<group>' * 400}{'</group>' * 401}"},
            RecursionError,
            "schema compilation does not support trees nested 400 levels",
            id="deep-target",
        ),
        pytest.param(
            {
                "main.rng": f'<group {_NS}>{"<group>" * 200}<externalRef href="x"/>{"</group>" * 201}',
                "x": f"<group {_NS}>{'<group>' * 200}<empty/>{'</group>' * 201}",
            },
            RecursionError,
            "schema compilation does not support trees nested 400 levels",
            id="deep-graft",
        ),
    ],
)
def test_relaxng_resources_reject(tmp_path: Path, files: dict[str, str], error: type[Exception], match: str) -> None:
    root: Final = tmp_path / "root"
    with pytest.raises(error, match=match):
        _compile(root, files, include_root=root)


def test_relaxng_resources_need_base_url() -> None:
    with pytest.raises(ValueError, match="need a base_url to resolve href against"):
        RelaxNG(f'<externalRef {_NS} href="x"/>')


def test_relaxng_resources_symlink_escape(tmp_path: Path) -> None:
    root: Final = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.rng").write_text(_ELEMENT)
    (root / "link.rng").symlink_to(tmp_path / "outside.rng")
    with pytest.raises(ValueError, match="RELAX NG path escapes include_root"):
        RelaxNG(f'<externalRef {_NS} href="link.rng"/>', base_url=str(root / "main.rng"), include_root=root)


def test_relaxng_resources_without_root_follow_symlinks(tmp_path: Path) -> None:
    (tmp_path / "x").write_text(_ELEMENT)
    (tmp_path / "link.rng").symlink_to(tmp_path / "x")
    schema: Final = RelaxNG(f'<externalRef {_NS} href="link.rng"/>', base_url=str(tmp_path / "main.rng"))
    assert _verdicts(schema, "<foo/>", "<bar/>") == [True, False]


def test_relaxng_resources_file_urls(tmp_path: Path) -> None:
    (tmp_path / "x y").write_text(_ELEMENT)
    schema: Final = RelaxNG(
        parse_xml(f'<externalRef {_NS} href="{(tmp_path / "x y").as_uri()}"/>'),
        base_url=(tmp_path / "main.rng").as_uri(),
    )
    assert _verdicts(schema, "<foo/>", "<bar/>") == [True, False]


def test_relaxng_resources_reject_remote_base_url() -> None:
    with pytest.raises(ValueError, match="RELAX NG base_url must be a local path or file URL"):
        RelaxNG(f'<externalRef {_NS} href="x"/>', base_url="https://example.com/main.rng")


def test_relaxng_resources_base_url_unused_without_references() -> None:
    schema: Final = RelaxNG(f'<element {_NS} name="foo"><empty/></element>', base_url="unused.rng")
    assert _verdicts(schema, "<foo/>", "<bar/>") == [True, False]


@pytest.mark.parametrize(
    "files",
    [
        pytest.param(
            {
                "main.rng": f'<{_LONG}:grammar xmlns:{_LONG}="http://relaxng.org/ns/structure/1.0">'
                f'<{_LONG}:include href="x"/></{_LONG}:grammar>',
                "x": _START_FOO,
            },
            id="include",
        ),
        pytest.param(
            {
                "main.rng": f'<grammar {_NS}><include href="x"/></grammar>',
                "x": f'<{_LONG}:grammar xmlns:{_LONG}="http://relaxng.org/ns/structure/1.0"><{_LONG}:start>'
                f"<{_LONG}:empty/></{_LONG}:start></{_LONG}:grammar>",
            },
            id="included-grammar",
        ),
    ],
)
def test_relaxng_resources_reject_long_prefix(tmp_path: Path, files: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="RELAX NG namespace prefix is too long"):
        _compile(tmp_path, files)


def test_relaxng_start_combine_checks_every_start() -> None:
    with pytest.raises(ValueError, match="has no matching define"):
        RelaxNG(
            f'<grammar {_NS}><start combine="choice"><element name="foo"><empty/></element></start>'
            '<div><start combine="choice"><ref name="missing"/></start></div></grammar>'
        )


def test_relaxng_resources_reject_missing_include_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        RelaxNG(
            f'<externalRef {_NS} href="x"/>', base_url=str(tmp_path / "main.rng"), include_root=tmp_path / "missing"
        )
