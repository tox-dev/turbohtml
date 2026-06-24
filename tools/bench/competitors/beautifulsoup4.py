"""BeautifulSoup: build a tree with new_tag and string assignment."""

from __future__ import annotations

from bs4 import BeautifulSoup

REQUIREMENTS = ("beautifulsoup4>=4.15",)


def build(count: int) -> None:
    """Build a ``<ul>`` of rows with BeautifulSoup's ``new_tag`` and ``.string``, then serialize."""
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    _ = ul.decode()


OPERATIONS = {"build": (build, "BeautifulSoup")}
