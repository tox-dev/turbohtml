from __future__ import annotations

import pytest

from turbohtml import parse


@pytest.mark.parametrize(
    ("markup", "body_html"),
    [
        pytest.param(
            "<nobr><table><marquee></table><nobr>",
            "<body><nobr><marquee></marquee><table></table></nobr><nobr></nobr></body>",
            id="table-and-open-marquee",
        ),
        pytest.param(
            "<nobr><table></table><nobr>",
            "<body><nobr><table></table></nobr><nobr></nobr></body>",
            id="table",
        ),
        pytest.param(
            "<nobr><marquee></marquee><nobr>",
            "<body><nobr><marquee></marquee></nobr><nobr></nobr></body>",
            id="marquee",
        ),
        pytest.param("<nobr>x<nobr>y", "<body><nobr>x</nobr><nobr>y</nobr></body>", id="repeated-nobr"),
    ],
)
def test_nobr_adoption_builds_siblings(markup: str, body_html: str) -> None:
    body = parse(markup).find("body")
    assert body is not None
    assert body.html == body_html
