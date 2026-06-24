"""BeautifulSoup: the constructor build plus the construct/serialize breakdown."""

from __future__ import annotations

import functools

from bs4 import BeautifulSoup

REQUIREMENTS = ("beautifulsoup4>=4.15",)


def parse(text: str) -> None:
    """Parse a whole document with BeautifulSoup over its stdlib html.parser backend."""
    BeautifulSoup(text, "html.parser")


def build(count: int) -> None:
    """Build a ``<ul>`` of rows with BeautifulSoup's ``new_tag`` and ``.string``, then serialize (the workload)."""
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    _ = ul.decode()


def construct(count: int) -> None:
    """Construct ``count`` elements with attributes and text, in isolation from serialization."""
    soup = BeautifulSoup("", "html.parser")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"


@functools.cache
def _tree(count: int) -> object:
    """Return a built ``<ul>`` of ``count`` rows, cached so ``serialize`` times only the emit step."""
    soup = BeautifulSoup("", "html.parser")
    ul = soup.new_tag("ul")
    for index in range(count):
        li = soup.new_tag("li", attrs={"class": "item", "data-i": str(index)})
        li.string = f"item {index}"
        ul.append(li)
    return ul


def serialize(count: int) -> None:
    """Serialize a pre-built ``count``-row tree with ``.decode()``."""
    _ = _tree(count).decode()  # ty: ignore[unresolved-attribute]  # bs4 Tag has no stubs


OPERATIONS = {
    "parse": (parse, "BeautifulSoup"),
    "build": (build, "BeautifulSoup"),
    "construct": (construct, "BeautifulSoup"),
    "serialize": (serialize, "BeautifulSoup"),
}
