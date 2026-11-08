"""BeautifulSoup over the lxml tree builder: the same API, the backend its own docs recommend for speed."""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment
from bs4.element import AttributeValueList, CData, NavigableString, Script, Stylesheet, TemplateString

from bench._beautifulsoup import (
    body,
    collapse_whitespace,
    encode_inner,
    encode_inner_indent,
    find,
    fresh,
    parsed,
    serialize_inner_indent,
    socialcard,
)
from bench.timing import Mutating

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("beautifulsoup4>=4.15", "lxml>=5.2", "soupsieve>=2.5")

# Only the operations whose cost depends on the tree builder appear here. Building a tree through ``new_tag`` and
# sniffing bytes through UnicodeDammit run the same code on either backend, so measuring them twice would add a
# duplicate column to those tables without telling a reader anything.
_BACKEND: Final[str] = "lxml"
_FIND_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(r"test")
_STRIP: Final[tuple[str, ...]] = ("code", "a", "q")
_LINKS_BASE: Final[str] = "https://example.com/base/"


def parse(text: str) -> None:
    # Parse a whole document with BeautifulSoup over the lxml backend.
    BeautifulSoup(text, _BACKEND)


def select(text: str) -> None:
    # Run the CSS selector with BeautifulSoup's soupsieve select.
    parsed(_BACKEND, text).select("div a[href]")


def select_has(text: str) -> None:
    # Run the :has() relational selector with BeautifulSoup's soupsieve.
    parsed(_BACKEND, text).select("div:has(a)")


def find_text(text: str) -> None:
    # Collect every matching string with BeautifulSoup's find_all(string=...).
    parsed(_BACKEND, text).find_all(string=_FIND_TEXT_PATTERN)


def _find_text_exact(case: tuple[str, str]) -> None:
    _ = [
        node
        for node in parsed(_BACKEND, case[0]).find_all("div")
        if node.get_text(types=(NavigableString, CData, Script, Stylesheet, TemplateString)) == case[1]
    ]


def _find_attr_presence(case: tuple[str, bool]) -> None:
    parsed(_BACKEND, case[0]).find_all("p", attrs={"data-x": case[1]})


def _select_default(case: tuple[str, str]) -> None:
    if case[0] == ":default" and "<button>" in case[1]:
        unsupported: Final = "SoupSieve requires explicit type=submit for default buttons"
        raise NotImplementedError(unsupported)
    _select_relative(case)


def _select_relative(case: tuple[str, str]) -> None:
    parsed(_BACKEND, case[1]).select(case[0])


def text_content(text: str) -> None:
    # Collect the document's visible text with BeautifulSoup's get_text().
    parsed(_BACKEND, text).get_text()


def _serialize_named(text: str) -> str:
    return parsed(_BACKEND, text).decode(formatter="html")


def serialize(text: str) -> None:
    # Serialize a parsed document back to HTML with BeautifulSoup's decode.
    parsed(_BACKEND, text).decode()


def class_edit(text: str) -> None:
    # Add then drop a class token on every link through BeautifulSoup's multi-valued class list (a net no-op).
    for anchor in parsed(_BACKEND, text).find_all("a"):
        tokens = anchor.get_attribute_list("class")
        anchor["class"] = AttributeValueList([*tokens, "seen"])
        anchor["class"] = tokens


def extract_attr(text: str) -> None:
    # Read every anchor's href by selecting once and reading the attribute off each node.
    for anchor in parsed(_BACKEND, text).select("a"):
        anchor.get("href")


def extract_text(text: str) -> None:
    # Read every anchor's visible text by selecting once and reading get_text off each node.
    for anchor in parsed(_BACKEND, text).select("a"):
        anchor.get_text()


def strip_remove(text: str) -> None:
    # Drop every code/a/q subtree with BeautifulSoup's decompose on a fresh parse, then serialize.
    soup = fresh(_BACKEND, text)
    for tag in list(soup.find_all(_STRIP)):
        tag.decompose()
    _ = soup.decode()


def strip_tags(text: str) -> None:
    # Unwrap every code/a/q element keeping its content with BeautifulSoup's unwrap on a fresh parse, then emit.
    soup = fresh(_BACKEND, text)
    for tag in list(soup.find_all(_STRIP)):
        tag.unwrap()
    _ = soup.decode()


def rewrite(text: str) -> None:
    # Full parse, mutate, serialize -- the DOM round-trip the streamer skips: rel=nofollow, lazy img, drop comments.
    soup = BeautifulSoup(text, _BACKEND)
    for anchor in soup.select("a[href]"):
        anchor["rel"] = "nofollow"
    for image in soup.find_all("img"):
        image["loading"] = "lazy"
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    _ = str(soup)


def edit(soup: BeautifulSoup) -> None:
    # Tag every link with rel=nofollow on a freshly parsed tree through BeautifulSoup's item assignment.
    for anchor in soup.find_all("a"):
        anchor["rel"] = "nofollow"


def set_html(soup: BeautifulSoup) -> None:
    # Clear a freshly parsed body and append a reparsed fragment, BeautifulSoup's inner-HTML shape.
    markup: str = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
    body = soup.find_all("body")[0]
    body.clear()
    for node in list(BeautifulSoup(markup, _BACKEND).children):
        body.append(node)


def set_text(soup: BeautifulSoup) -> None:
    # Replace a freshly parsed body's children with one verbatim text node through BeautifulSoup's .string.
    soup.find_all("body")[0].string = "Replacement text, escaped & verbatim."


def navigate(text: str) -> None:
    # Walk every descendant with BeautifulSoup's descendants iterator.
    for _node in parsed(_BACKEND, text).descendants:
        pass


def match(text: str) -> None:
    # Test every anchor against a selector with BeautifulSoup's soupsieve-backed css.match.
    for anchor in parsed(_BACKEND, text).find_all("a"):
        anchor.css.match("div a[href]")


def links_extract(text: str) -> None:
    # Collect every anchor's href with BeautifulSoup, the shape a scraper reads links in.
    _ = [anchor.get("href") for anchor in parsed(_BACKEND, text).find_all("a")]


def links_rewrite(text: str) -> None:
    # Rewrite every link with an identity map, idempotent so the cached tree stays reusable.
    for anchor in parsed(_BACKEND, text).find_all("a"):
        if (href := anchor.get("href")) is not None:
            anchor["href"] = href


def links_filter(text: str) -> None:
    # Collect the cleaned, absolutized, deduplicated page links, the work turbohtml's extract_links does.
    seen: dict[str, None] = {}
    for anchor in BeautifulSoup(text, _BACKEND).find_all("a"):
        if href := anchor.get("href"):
            seen[urljoin(_LINKS_BASE, str(href))] = None
    _ = list(seen)


def extract_url(case: tuple[str, str]) -> None:
    # Read a freshly parsed document's own URL hint with BeautifulSoup: the base href or the meta refresh.
    kind, text = case
    soup = fresh(_BACKEND, text)
    if kind == "base":
        if (base := soup.find("base")) is not None:
            base.get("href")
    elif (refresh := soup.find("meta", attrs={"http-equiv": "refresh"})) is not None:
        refresh.get("content")


def links_absolutize(soup: BeautifulSoup) -> None:
    # Resolve every relative link on a freshly parsed tree against a base with BeautifulSoup's item assignment.
    for anchor in soup.find_all("a"):
        if isinstance(href := anchor.get("href"), str):
            anchor["href"] = urljoin(_LINKS_BASE, href)


def _serialize_inner(text: str) -> str:
    return body(_BACKEND, text).decode_contents()


def _transform_tree(soup: BeautifulSoup) -> None:
    _strip_comments(soup)
    collapse_whitespace(soup)


def _strip_comments(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "collapse-whitespace": (
        Mutating(setup=functools.partial(fresh, _BACKEND), run=collapse_whitespace),
        "BeautifulSoup (lxml)",
    ),
    "transform-tree": (Mutating(setup=functools.partial(fresh, _BACKEND), run=_transform_tree), "BeautifulSoup (lxml)"),
    "serialize-inner": (_serialize_inner, "BeautifulSoup (lxml)"),
    "encode-inner": (functools.partial(encode_inner, _BACKEND), "BeautifulSoup (lxml)"),
    "serialize-inner-indent": (functools.partial(serialize_inner_indent, _BACKEND), "BeautifulSoup (lxml)"),
    "encode-inner-indent": (functools.partial(encode_inner_indent, _BACKEND), "BeautifulSoup (lxml)"),
    "strip-comments": (Mutating(setup=functools.partial(fresh, _BACKEND), run=_strip_comments), "BeautifulSoup (lxml)"),
    "parse": (parse, "BeautifulSoup (lxml)"),
    "parse-formatting": (parse, "BeautifulSoup (lxml)"),
    "parse-foster": (parse, "BeautifulSoup (lxml)"),
    "parse-crlf": (parse, "BeautifulSoup (lxml)"),
    "parse-nul": (parse, "BeautifulSoup (lxml)"),
    "parse-afe": (parse, "BeautifulSoup (lxml)"),
    "parse-scope": (parse, "BeautifulSoup (lxml)"),
    "find": (functools.partial(find, _BACKEND), "BeautifulSoup (lxml)"),
    "select": (select, "BeautifulSoup (lxml)"),
    "select-nth": (_select_relative, "BeautifulSoup (lxml)"),
    "select-relative": (_select_relative, "BeautifulSoup (lxml)"),
    "select-default": (_select_default, "BeautifulSoup (lxml)"),
    "select-has": (select_has, "BeautifulSoup (lxml)"),
    "find-text": (find_text, "BeautifulSoup (lxml)"),
    "find-text-exact": (_find_text_exact, "BeautifulSoup (lxml)"),
    "find-attr-presence": (_find_attr_presence, "BeautifulSoup (lxml)"),
    "text-content": (text_content, "BeautifulSoup (lxml)"),
    "serialize": (serialize, "BeautifulSoup (lxml)"),
    "class-edit": (class_edit, "BeautifulSoup (lxml)"),
    "extract-attr": (extract_attr, "BeautifulSoup (lxml)"),
    "extract-text": (extract_text, "BeautifulSoup (lxml)"),
    "strip-remove": (strip_remove, "BeautifulSoup (lxml)"),
    "strip-tags": (strip_tags, "BeautifulSoup (lxml)"),
    "rewrite": (rewrite, "BeautifulSoup (lxml)"),
    "edit": (Mutating(setup=functools.partial(fresh, _BACKEND), run=edit), "BeautifulSoup (lxml)"),
    "set-html": (Mutating(setup=functools.partial(fresh, _BACKEND), run=set_html), "BeautifulSoup (lxml)"),
    "set-text": (Mutating(setup=functools.partial(fresh, _BACKEND), run=set_text), "BeautifulSoup (lxml)"),
    "navigate": (navigate, "BeautifulSoup (lxml)"),
    "match": (match, "BeautifulSoup (lxml)"),
    "links-extract": (links_extract, "BeautifulSoup (lxml)"),
    "links-rewrite": (links_rewrite, "BeautifulSoup (lxml)"),
    "links-filter": (links_filter, "BeautifulSoup (lxml)"),
    "socialcard": (functools.partial(socialcard, _BACKEND), "BeautifulSoup (lxml)"),
    "extract-url": (extract_url, "BeautifulSoup (lxml)"),
    "links-absolutize": (
        Mutating(setup=functools.partial(fresh, _BACKEND), run=links_absolutize),
        "BeautifulSoup (lxml)",
    ),
    "serialize-named": (_serialize_named, "BeautifulSoup (lxml)"),
}
