"""opengraph_py3: OpenGraph (``og:``) card extraction over a BeautifulSoup tree."""

from __future__ import annotations

from opengraph_py3 import OpenGraph

REQUIREMENTS = ("opengraph-py3>=0.71",)


def socialcard(text: str) -> None:
    """Read the OpenGraph card tags with opengraph_py3, which parses with BeautifulSoup then maps the ``og:`` block."""
    # opengraph_py3 reaches the tags through ``doc.html.head``, which the stdlib parser only builds when the markup has
    # an ``<html>`` root, so the head-only fixtures are wrapped to match the full pages the library is written for.
    OpenGraph(html=f"<html>{text}</html>")


OPERATIONS = {"socialcard": (socialcard, "opengraph_py3")}
