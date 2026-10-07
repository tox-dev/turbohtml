"""Corpus-label checks must expose shared errors and preserve XML context."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

_RNG: Final = "http://relaxng.org/ns/structure/1.0"


@pytest.fixture
def engines() -> tuple[ModuleType, ModuleType]:
    load: Final = pytest.importorskip
    return load("fuzz.rng_labels", exc_type=ImportError), load("lxml.etree", exc_type=ImportError)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            f'<correct><element xmlns="{_RNG}" name="root"><text/></element></correct>'
            "<valid><root>yes</root></valid><invalid><other/></invalid>",
            [
                ("turbohtml", "compilation", True, True),
                ("libxml2", "compilation", True, True),
                ("turbohtml", "validation", True, True),
                ("libxml2", "validation", True, True),
                ("turbohtml", "validation", False, False),
                ("libxml2", "validation", False, False),
            ],
            id="both-document-labels",
        ),
        pytest.param(
            f'<incorrect><grammar xmlns="{_RNG}"/></incorrect>',
            [("turbohtml", "compilation", False, False), ("libxml2", "compilation", False, False)],
            id="incorrect-schema",
        ),
        pytest.param(
            f'<incorrect><element xmlns="{_RNG}" name="foo"><externalRef/></element></incorrect>',
            [("turbohtml", "compilation", False, False), ("libxml2", "compilation", False, False)],
            id="missing-external-href",
        ),
        pytest.param(
            f'<incorrect><grammar xmlns="{_RNG}"><include/><start><element name="foo">'
            "<empty/></element></start></grammar></incorrect>",
            [("turbohtml", "compilation", False, False), ("libxml2", "compilation", False, False)],
            id="missing-include-href",
        ),
        pytest.param(
            f'<correct><grammar xmlns="{_RNG}"/></correct><valid><root/></valid>',
            [
                ("turbohtml", "compilation", True, False),
                ("libxml2", "compilation", True, False),
                ("turbohtml", "validation", True, None),
                ("libxml2", "validation", True, None),
            ],
            id="unavailable-document-verdicts",
        ),
    ],
)
def test_rng_labels_verdicts(
    engines: tuple[ModuleType, ModuleType],
    case: str,
    expected: list[tuple[str, str, bool, bool | None]],
) -> None:
    rng_labels, etree = engines
    assert [
        (row.engine, row.phase, row.expected, row.actual)
        for row in rng_labels.compare(etree.fromstring(f"<testSuite><testCase>{case}</testCase></testSuite>"))
    ] == expected


@pytest.mark.oracle
@pytest.mark.parametrize("name", ["dir", "resource", "testCase", "testSuite"])
def test_rng_labels_instance_names_are_not_resource_metadata(engines: tuple[ModuleType, ModuleType], name: str) -> None:
    rng_labels, etree = engines
    suite: Final = etree.fromstring(
        f'<testSuite><testCase><correct><element xmlns="{_RNG}" name="{name}"><empty/></element></correct>'
        f"<valid><{name}/></valid></testCase></testSuite>"
    )
    assert [(row.phase, row.actual) for row in rng_labels.compare(suite)] == [
        ("compilation", True),
        ("compilation", True),
        ("validation", True),
        ("validation", True),
    ]


@pytest.mark.oracle
@pytest.mark.parametrize(
    "context",
    [
        pytest.param('<resource name="other.rng"><root/></resource>', id="resource"),
        pytest.param('<dir name="other"/>', id="directory"),
        pytest.param(f'<correct><externalRef xmlns="{_RNG}" href="other.rng"/></correct>', id="external-reference"),
        pytest.param(f'<correct><include xmlns="{_RNG}" href="other.rng"/></correct>', id="include"),
        pytest.param(f'<incorrect><externalRef xmlns="{_RNG}" href=""/></incorrect>', id="empty-external-href"),
        pytest.param(f'<incorrect><include xmlns="{_RNG}" href=""/></incorrect>', id="empty-include-href"),
        pytest.param(f'<correct xml:base="other/"><empty xmlns="{_RNG}"/></correct>', id="base-uri"),
    ],
)
def test_rng_labels_inventory_has_content_hash(engines: tuple[ModuleType, ModuleType], context: str) -> None:
    rng_labels, etree = engines
    rows: Final = list(rng_labels.compare(etree.fromstring(f"<testSuite><testCase>{context}</testCase></testSuite>")))
    assert [(row.case_id, len(row.sha256), row.phase, row.expected, row.actual) for row in rows] == [
        ("001", 64, "unsupported-inventory", None, None)
    ]


@pytest.mark.oracle
def test_rng_labels_preserves_inherited_qname_prefix(engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = engines
    suite: Final = etree.fromstring(
        f'<testSuite xmlns:q="urn:example"><testCase><correct><element xmlns="{_RNG}" name="q:root">'
        "<empty/></element></correct><valid><q:root/></valid><invalid><root/></invalid></testCase></testSuite>"
    )
    assert [row.actual for row in rng_labels.compare(suite)] == [True, True, True, True, False, False]


@pytest.mark.oracle
def test_rng_labels_internal_entities(engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = engines
    suite: Final = etree.fromstring(
        (
            '<!DOCTYPE testSuite [<!ENTITY word "expected">]><testSuite><testCase><correct>'
            f'<element xmlns="{_RNG}" name="root"><value>&word;</value></element></correct>'
            "<valid><root>&word;</root></valid><invalid><root>different</root></invalid></testCase></testSuite>"
        ).encode(),
        etree.XMLParser(resolve_entities="internal", no_network=True, load_dtd=False),
    )
    assert [row.actual for row in rng_labels.compare(suite)] == [True, True, True, True, False, False]


@pytest.mark.oracle
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("<correct/><incorrect/>", id="two-labels"),
        pytest.param("<valid><root/></valid>", id="no-label"),
        pytest.param("<correct><root/><other/></correct>", id="multiple-roots"),
        pytest.param('<correct dtd="value"><root/></correct>', id="dtd"),
        pytest.param("<correct>outside<root/></correct>", id="leading-text"),
        pytest.param("<correct><root/>outside</correct>", id="trailing-text"),
    ],
)
def test_rng_labels_rejects_unsupported_extraction(engines: tuple[ModuleType, ModuleType], case: str) -> None:
    rng_labels, etree = engines
    with pytest.raises(ValueError, match=r"testCase|single-root|outside"):
        list(rng_labels.compare(etree.fromstring(f"<testSuite><testCase>{case}</testCase></testSuite>")))


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            f'<incorrect><grammar xmlns="{_RNG}"/></incorrect>',
            (0, {"compilation": 2}),
            id="agreement",
        ),
        pytest.param(
            f'<incorrect><element xmlns="{_RNG}" name="root"><empty/></element></incorrect>',
            (1, {"findings": 2}),
            id="both-engines-share-wrong-label",
        ),
        pytest.param(
            f'<correct><grammar xmlns="{_RNG}"/></correct><valid><root/></valid>',
            (1, {"findings": 2, "unavailable": 2}),
            id="compilation-rejection",
        ),
        pytest.param(
            "<resource name='root'/><correct><junk/></correct>", (2, {"unsupported-inventory": 1}), id="no-work"
        ),
        pytest.param("", (2, {}), id="empty-corpus"),
    ],
)
def test_rng_labels_cli(
    engines: tuple[ModuleType, ModuleType],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected: tuple[int, dict[str, int]],
) -> None:
    corpus: Final = tmp_path / "suite.xml"
    corpus.write_text(f"<testSuite><testCase>{case}</testCase></testSuite>" if case else "<testSuite/>")
    assert (
        engines[0].main(["--corpus", str(corpus)]),
        json.loads(capsys.readouterr().out.splitlines()[-1]),
    ) == (expected[0], {"summary": expected[1]})


@pytest.mark.oracle
def test_rng_labels_cli_preparation_error_keeps_source_private(
    engines: tuple[ModuleType, ModuleType], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus: Final = tmp_path / "suite.xml"
    corpus.write_text("<testSuite>private-marker")
    assert (engines[0].main(["--corpus", str(corpus)]), capsys.readouterr()) == (2, ("", "Corpus preparation failed\n"))


@pytest.mark.oracle
def test_rng_labels_preserves_prefix_used_only_in_instance_text(engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = engines
    suite: Final = etree.fromstring(
        f'<testSuite xmlns:e2="urn:second"><testCase><correct><element xmlns="{_RNG}" name="root">'
        '<value type="QName" datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
        "e2:xyzzy</value></element></correct><valid><root>e2:xyzzy</root></valid>"
        "<invalid><root>xyzzy</root></invalid></testCase></testSuite>"
    )
    assert [(row.phase, row.actual) for row in rng_labels.compare(suite) if row.engine == "libxml2"] == [
        ("compilation", True),
        ("validation", True),
        ("validation", False),
    ]


@pytest.mark.oracle
def test_rng_labels_inventory_hash_tracks_content(engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = engines
    first: Final = etree.fromstring("<testSuite><testCase><resource name='first'/></testCase></testSuite>")
    second: Final = etree.fromstring("<testSuite><testCase><resource name='second'/></testCase></testSuite>")
    first_row: Final = next(iter(rng_labels.compare(first)))
    repeated_row: Final = next(iter(rng_labels.compare(first)))
    second_row: Final = next(iter(rng_labels.compare(second)))
    assert (
        first_row == repeated_row,
        first_row.case_id == second_row.case_id,
        first_row.sha256 != second_row.sha256,
    ) == (True, True, True)


@pytest.mark.oracle
def test_rng_labels_nested_suites(engines: tuple[ModuleType, ModuleType]) -> None:
    rng_labels, etree = engines
    suite: Final = etree.fromstring(
        f"<testSuite><documentation>ignored</documentation><testSuite><testCase><incorrect>"
        f'<grammar xmlns="{_RNG}"/></incorrect></testCase></testSuite></testSuite>'
    )
    assert [(row.case_id, row.phase, row.actual) for row in rng_labels.compare(suite)] == [
        ("001", "compilation", False),
        ("001", "compilation", False),
    ]
