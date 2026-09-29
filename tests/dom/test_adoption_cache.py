from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="one"), pytest.param(12, id="inline"), pytest.param(32, id="expanded")]
)
@pytest.mark.parametrize("keep", [pytest.param(False, id="released"), pytest.param(True, id="retained")])
def test_adoption_preserves_descendants_after_alias_release(count: int, *, keep: bool) -> None:
    source: Final = Element("section")
    source.set_inner_html("<span>text</span>" * count)
    aliases: Final = source.select("span")
    retained: Final = aliases[-1:] if keep else []
    aliases.clear()
    target: Final = Element("main")
    target.append(source)
    if retained:
        retained[0].attrs["live"] = "yes"
    assert target.inner_html == "<section>" + "<span>text</span>" * (count - 1) + (
        '<span live="yes">text</span></section>' if keep else "<span>text</span></section>"
    )
