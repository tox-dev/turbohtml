"""microdata: html5lib-based Microdata item extraction, the competitor to turbohtml's microdata surface."""

from __future__ import annotations

import microdata

REQUIREMENTS = ("microdata>=0.8",)


def microdata_items(text: str) -> None:
    """Pull the page's Microdata items, which builds an html5lib tree and walks its itemscope elements."""
    microdata.get_items(text)


# The library reads only Microdata, so it competes on the Microdata-only op, not the three-format structured walk.
OPERATIONS = {"microdata": (microdata_items, "microdata")}
