"""opengraph: og: meta-tag extraction over BeautifulSoup, the competitor to turbohtml's OpenGraph surface."""

from __future__ import annotations

from opengraph_py3 import OpenGraph

REQUIREMENTS = ("opengraph-py3>=0.71", "html5lib>=1.1")


def socialcard(text: str) -> None:
    """Read the og: tags with opengraph, which builds a BeautifulSoup tree and scans the head's og-prefixed meta."""
    OpenGraph(html=text)


OPERATIONS = {"socialcard": (socialcard, "opengraph")}
