"""microdata: HTML Microdata item extraction over an html5lib DOM tree."""

from __future__ import annotations

import microdata

REQUIREMENTS = ("microdata>=0.8",)


def structured(text: str) -> None:
    """Extract every Microdata item with microdata, which parses with html5lib then walks the itemscope tree."""
    microdata.get_items(text)


OPERATIONS = {"structured": (structured, "microdata")}
