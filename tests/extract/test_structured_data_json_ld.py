from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import parse

if TYPE_CHECKING:
    from turbohtml.extract._structured_data import JSONValue


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<script type="application/ld+json">{"@type": "Person", "name": "Ada"}</script>',
            [{"@type": "Person", "name": "Ada"}],
            id="single-object",
        ),
        pytest.param(
            '<script type="application/ld+json">[{"a": 1}, {"b": 2}]</script>',
            [[{"a": 1}, {"b": 2}]],
            id="array-value",
        ),
        pytest.param(
            '<script type="application/ld+json">{"n": 1}</script><script type="application/ld+json">{"n": 2}</script>',
            [{"n": 1}, {"n": 2}],
            id="multiple-blocks-in-document-order",
        ),
        pytest.param(
            '<script type="application/ld+json">{not valid json}</script>'
            '<script type="application/ld+json">{"ok": true}</script>',
            [{"ok": True}],
            id="invalid-block-skipped",
        ),
        pytest.param('<script type="APPLICATION/LD+JSON">{"ok": 1}</script>', [{"ok": 1}], id="type-uppercase"),
        pytest.param('<script type="  application/ld+json  ">{"ok": 1}</script>', [{"ok": 1}], id="type-whitespace"),
        pytest.param('<script type="Application/LD+Json">{"ok": 1}</script>', [{"ok": 1}], id="type-mixed-case"),
        pytest.param('<script>{"ok": 1}</script>', [], id="no-type-attribute"),
        pytest.param('<script type>{"ok": 1}</script>', [], id="valueless-type-attribute"),
        pytest.param('<script type="text/javascript">{"ok": 1}</script>', [], id="other-type"),
        pytest.param('<script type="application/ld+jsox">{"ok": 1}</script>', [], id="same-length-mismatch"),
        pytest.param('<script type="   ">{"ok": 1}</script>', [], id="all-whitespace-type"),
        pytest.param("<p>no script at all</p>", [], id="no-script"),
        pytest.param(
            '<script type="application/ld+json">null</script>'
            '<script type="application/ld+json">{"@type": "Thing"}</script>',
            [{"@type": "Thing"}],
            id="null-payload-dropped",
        ),
        pytest.param(
            '<script type="application/ld+json">42</script>'
            '<script type="application/ld+json">"text"</script>'
            '<script type="application/ld+json">true</script>'
            '<script type="application/ld+json">[{"@id": "x"}]</script>',
            [[{"@id": "x"}]],
            id="scalar-payloads-dropped-list-kept",
        ),
    ],
)
def test_json_ld(html: str, expected: list[JSONValue]) -> None:
    assert parse(html).json_ld() == expected
