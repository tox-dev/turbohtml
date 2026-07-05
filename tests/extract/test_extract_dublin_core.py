"""dublin_core: Node.dublin_core() reads the dc.* / dcterms.* meta-name namespace, lower-casing keys."""

from __future__ import annotations

import pytest

from turbohtml import parse


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta name="dc.title" content="Hi"><meta name="dc.creator" content="Ada">',
            {"dc.title": "Hi", "dc.creator": "Ada"},
            id="dc-names",
        ),
        pytest.param('<meta name="dcterms.created" content="2020">', {"dcterms.created": "2020"}, id="dcterms-name"),
        pytest.param('<meta name="DC.Title" content="Hi">', {"dc.title": "Hi"}, id="name-lower-cased"),
        pytest.param(
            '<meta name="dc.title" content="first"><meta name="dc.title" content="second">',
            {"dc.title": "second"},
            id="last-occurrence-wins",
        ),
        pytest.param('<meta name="dc.title">', {"dc.title": ""}, id="missing-content-empty-string"),
        pytest.param('<meta name="dc.title" content>', {"dc.title": ""}, id="valueless-content-empty-string"),
        pytest.param('<meta name="keywords" content="x">', {}, id="non-dc-name-ignored"),
        pytest.param('<meta name="dc" content="x">', {}, id="name-shorter-than-prefix-ignored"),
        pytest.param('<meta name="dcx.title" content="x">', {}, id="near-miss-prefix-ignored"),
        pytest.param('<meta property="dc.title" content="x">', {}, id="property-not-name-ignored"),
        pytest.param('<meta name content="x">', {}, id="valueless-name-ignored"),
        pytest.param('<meta content="x">', {}, id="no-name-ignored"),
        pytest.param("<p>not a meta</p>", {}, id="no-meta"),
    ],
)
def test_dublin_core(html: str, expected: dict[str, str]) -> None:
    assert parse(html).dublin_core() == expected
