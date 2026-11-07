"""courlan: URL cleaning/normalization and regex link extraction, the competitor to turbohtml's URL helpers."""

from __future__ import annotations

from typing import Final

import courlan

REQUIREMENTS: Final[tuple[str, ...]] = ("courlan>=1.4",)


def urls_clean(case: tuple[str, tuple[str, ...]]) -> None:
    # Run courlan's scrub-and-normalize (or bare normalize) over the shared URL batch, by case kind.
    kind, batch = case
    transform = courlan.clean_url if kind == "clean" else courlan.normalize_url
    for url in batch:
        transform(url)


def links_filter(text: str) -> None:
    # Collect the checked, deduplicated page links with courlan's regex-scanning extract_links.
    courlan.extract_links(text, url="https://example.com/base/")


OPERATIONS = {
    "urls-clean": (urls_clean, "courlan"),
    "links-filter": (links_filter, "courlan"),
}
