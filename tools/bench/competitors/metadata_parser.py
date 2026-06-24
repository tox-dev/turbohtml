"""
metadata_parser: social-card (OpenGraph/Twitter) meta-tag extraction.

This is the competitor that forces the per-venv design: it pins an older beautifulsoup4 than the rest of the suite, so
it can only be benchmarked in a venv of its own.
"""

from __future__ import annotations

from metadata_parser import MetadataParser  # ty: ignore[unresolved-import]  # undeclared: pins an older beautifulsoup4

REQUIREMENTS = ("metadata-parser>=1", "legacy-cgi; python_version>='3.13'")


def socialcard(text: str) -> None:
    """Read the social-card tags with metadata_parser, which parses then maps the meta block."""
    MetadataParser(html=text)


OPERATIONS = {"socialcard": (socialcard, "metadata_parser")}
