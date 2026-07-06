"""extruct: JSON-LD / Microdata / OpenGraph extraction over an lxml tree."""

from __future__ import annotations

import extruct

REQUIREMENTS = ("extruct>=0.18",)


def structured(text: str) -> None:
    """Extract the three formats with extruct, which builds an lxml tree and runs one extractor per syntax."""
    extruct.extract(text, syntaxes=["json-ld", "microdata", "opengraph"])


def socialcard(text: str) -> None:
    """Extract the OpenGraph card with extruct, its opengraph-only syntax pass over an lxml tree."""
    extruct.extract(text, syntaxes=["opengraph"])


OPERATIONS = {
    "structured": (structured, "extruct"),
    "socialcard": (socialcard, "extruct"),
}
