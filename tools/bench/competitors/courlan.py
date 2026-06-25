"""courlan: the URL cleaning/normalization/extraction layer turbohtml.extract's URL helpers replace."""

from __future__ import annotations

from typing import cast

import courlan

REQUIREMENTS = ("courlan>=1.4",)


def urls(case: tuple[str, object]) -> None:
    """Clean, normalize, or extract URLs with courlan's three regex-and-urllib calls."""
    kind, payload = case
    if kind == "clean":
        courlan.clean_url(cast("str", payload))
    elif kind == "normalize":
        courlan.normalize_url(cast("str", payload))
    else:
        html, base_url = cast("tuple[str, str]", payload)
        courlan.extract_links(html, base_url)


OPERATIONS = {"urls": (urls, "courlan")}
