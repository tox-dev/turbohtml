"""BeautifulSoup over the lxml tree builder: the same API, the backend its own docs recommend for speed."""

from __future__ import annotations

import functools
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment
from bs4.element import AttributeValueList, CData, NavigableString, Script, Stylesheet, Tag, TemplateString

from bench.timing import Mutating
from bench.tree_text import PRESERVE_TAGS, SPACE_RUN

REQUIREMENTS = ("beautifulsoup4>=4.15", "lxml>=5.2", "soupsieve>=2.5")

# Only the operations whose cost depends on the tree builder appear here. Building a tree through ``new_tag`` and
# sniffing bytes through UnicodeDammit run the same code on either backend, so measuring them twice would add a
# duplicate column to those tables without telling a reader anything.
_BACKEND = "lxml"
_FIND_TEXT_PATTERN = re.compile(r"test")
_SET_HTML = "<p>Updated <a href='/x'>link</a> and <b>bold</b>.</p><ul><li>one</li><li>two</li></ul>"
_SET_TEXT = "Replacement text, escaped & verbatim."
_STRIP = ("code", "a", "q")
_LINKS_BASE = "https://example.com/base/"


def parse(text: str) -> None:
    """Parse a whole document with BeautifulSoup over the lxml backend."""
    BeautifulSoup(text, _BACKEND)


def _fresh(text: str) -> BeautifulSoup:
    """Parse a fresh document for the mutating operations, which each iteration must run on an unmodified tree."""
    return BeautifulSoup(text, _BACKEND)


@functools.cache
def _parsed(text: str) -> BeautifulSoup:
    """Return a document parsed once, cached so the read-path operations time only the query."""
    return BeautifulSoup(text, _BACKEND)


def find(text: str) -> None:
    """Collect every anchor with BeautifulSoup's find_all."""
    _parsed(text).find_all("a")


def select(text: str) -> None:
    """Run the CSS selector with BeautifulSoup's soupsieve select."""
    _parsed(text).select("div a[href]")


def select_has(text: str) -> None:
    """Run the :has() relational selector with BeautifulSoup's soupsieve."""
    _parsed(text).select("div:has(a)")


def find_text(text: str) -> None:
    """Collect every matching string with BeautifulSoup's find_all(string=...)."""
    _parsed(text).find_all(string=_FIND_TEXT_PATTERN)


def _find_text_exact(case: tuple[str, str]) -> None:
    _ = [
        node
        for node in _parsed(case[0]).find_all("div")
        if node.get_text(types=(NavigableString, CData, Script, Stylesheet, TemplateString)) == case[1]
    ]


def _find_attr_presence(case: tuple[str, bool]) -> None:
    _parsed(case[0]).find_all("p", attrs={"data-x": case[1]})


def _select_relative(case: tuple[str, str]) -> None:
    _parsed(case[1]).select(case[0])


def text_content(text: str) -> None:
    """Collect the document's visible text with BeautifulSoup's get_text()."""
    _parsed(text).get_text()


def _serialize_named(text: str) -> str:
    return _parsed(text).decode(formatter="html")


def serialize(text: str) -> None:
    """Serialize a parsed document back to HTML with BeautifulSoup's decode."""
    _parsed(text).decode()


def class_edit(text: str) -> None:
    """Add then drop a class token on every link through BeautifulSoup's multi-valued class list (a net no-op)."""
    for anchor in _parsed(text).find_all("a"):
        tokens = anchor.get_attribute_list("class")
        anchor["class"] = AttributeValueList([*tokens, "seen"])
        anchor["class"] = tokens


def extract_attr(text: str) -> None:
    """Read every anchor's href by selecting once and reading the attribute off each node."""
    for anchor in _parsed(text).select("a"):
        anchor.get("href")


def extract_text(text: str) -> None:
    """Read every anchor's visible text by selecting once and reading get_text off each node."""
    for anchor in _parsed(text).select("a"):
        anchor.get_text()


def strip_remove(text: str) -> None:
    """Drop every code/a/q subtree with BeautifulSoup's decompose on a fresh parse, then serialize."""
    soup = _fresh(text)
    for tag in list(soup.find_all(_STRIP)):
        tag.decompose()
    _ = soup.decode()


def strip_tags(text: str) -> None:
    """Unwrap every code/a/q element keeping its content with BeautifulSoup's unwrap on a fresh parse, then emit."""
    soup = _fresh(text)
    for tag in list(soup.find_all(_STRIP)):
        tag.unwrap()
    _ = soup.decode()


def rewrite(text: str) -> None:
    """Full parse, mutate, serialize -- the DOM round-trip the streamer skips: rel=nofollow, lazy img, drop comments."""
    soup = BeautifulSoup(text, _BACKEND)
    for anchor in soup.select("a[href]"):
        anchor["rel"] = "nofollow"
    for image in soup.find_all("img"):
        image["loading"] = "lazy"
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    _ = str(soup)


def edit(soup: BeautifulSoup) -> None:
    """Tag every link with rel=nofollow on a freshly parsed tree through BeautifulSoup's item assignment."""
    for anchor in soup.find_all("a"):
        anchor["rel"] = "nofollow"


def set_html(soup: BeautifulSoup) -> None:
    """Clear a freshly parsed body and append a reparsed fragment, BeautifulSoup's inner-HTML shape."""
    body = soup.find_all("body")[0]
    body.clear()
    for node in list(BeautifulSoup(_SET_HTML, _BACKEND).children):
        body.append(node)


def set_text(soup: BeautifulSoup) -> None:
    """Replace a freshly parsed body's children with one verbatim text node through BeautifulSoup's .string."""
    soup.find_all("body")[0].string = _SET_TEXT


def navigate(text: str) -> None:
    """Walk every descendant with BeautifulSoup's descendants iterator."""
    for _node in _parsed(text).descendants:
        pass


def match(text: str) -> None:
    """Test every anchor against a selector with BeautifulSoup's soupsieve-backed css.match."""
    for anchor in _parsed(text).find_all("a"):
        anchor.css.match("div a[href]")


def links_extract(text: str) -> None:
    """Collect every anchor's href with BeautifulSoup, the shape a scraper reads links in."""
    _ = [anchor.get("href") for anchor in _parsed(text).find_all("a")]


def links_rewrite(text: str) -> None:
    """Rewrite every link with an identity map, idempotent so the cached tree stays reusable."""
    for anchor in _parsed(text).find_all("a"):
        if (href := anchor.get("href")) is not None:
            anchor["href"] = href


def links_filter(text: str) -> None:
    """Collect the cleaned, absolutized, deduplicated page links, the work turbohtml's extract_links does."""
    seen: dict[str, None] = {}
    for anchor in BeautifulSoup(text, _BACKEND).find_all("a"):
        if href := anchor.get("href"):
            seen[urljoin(_LINKS_BASE, str(href))] = None
    _ = list(seen)


def socialcard(text: str) -> None:
    """Read every meta property and content off a freshly parsed tree, BeautifulSoup's take on card extraction."""
    for meta in _fresh(text).find_all("meta"):
        meta.get("property")
        meta.get("content")


def extract_url(case: tuple[str, str]) -> None:
    """Read a freshly parsed document's own URL hint with BeautifulSoup: the base href or the meta refresh."""
    kind, text = case
    soup = _fresh(text)
    if kind == "base":
        if (base := soup.find("base")) is not None:
            base.get("href")
    elif (refresh := soup.find("meta", attrs={"http-equiv": "refresh"})) is not None:
        refresh.get("content")


def links_absolutize(soup: BeautifulSoup) -> None:
    """Resolve every relative link on a freshly parsed tree against a base with BeautifulSoup's item assignment."""
    for anchor in soup.find_all("a"):
        if isinstance(href := anchor.get("href"), str):
            anchor["href"] = urljoin(_LINKS_BASE, href)


def _serialize_inner(text: str) -> str:
    return _body(text).decode_contents()


@functools.cache
def _body(text: str) -> Tag:
    return _parsed(text).find_all("body")[0]


def _encode_inner(text: str) -> bytes:
    return _body(text).encode_contents()


def _serialize_inner_indent(text: str) -> str:
    return _body(text).decode_contents(indent_level=0)


def _encode_inner_indent(text: str) -> bytes:
    return _body(text).encode_contents(indent_level=0)


def _collapse_whitespace(soup: BeautifulSoup) -> None:
    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment) or any(parent.name in PRESERVE_TAGS for parent in node.parents):
            continue
        collapsed = SPACE_RUN.sub(" ", str(node))
        previous = node.previous_sibling
        while isinstance(previous, NavigableString) and not isinstance(previous, Comment) and not previous:
            previous = previous.previous_sibling
        if isinstance(previous, NavigableString) and not isinstance(previous, Comment) and previous.endswith(" "):
            collapsed = collapsed.removeprefix(" ")
        if collapsed != node:
            node.replace_with(collapsed)


def _transform_tree(soup: BeautifulSoup) -> None:
    _strip_comments(soup)
    _collapse_whitespace(soup)


def _strip_comments(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()


OPERATIONS = {
    "collapse-whitespace": (Mutating(_fresh, _collapse_whitespace), "BeautifulSoup (lxml)"),
    "transform-tree": (Mutating(_fresh, _transform_tree), "BeautifulSoup (lxml)"),
    "serialize-inner": (_serialize_inner, "BeautifulSoup (lxml)"),
    "encode-inner": (_encode_inner, "BeautifulSoup (lxml)"),
    "serialize-inner-indent": (_serialize_inner_indent, "BeautifulSoup (lxml)"),
    "encode-inner-indent": (_encode_inner_indent, "BeautifulSoup (lxml)"),
    "strip-comments": (Mutating(_fresh, _strip_comments), "BeautifulSoup (lxml)"),
    "parse": (parse, "BeautifulSoup (lxml)"),
    "parse-formatting": (parse, "BeautifulSoup (lxml)"),
    "parse-foster": (parse, "BeautifulSoup (lxml)"),
    "parse-crlf": (parse, "BeautifulSoup (lxml)"),
    "parse-nul": (parse, "BeautifulSoup (lxml)"),
    "parse-afe": (parse, "BeautifulSoup (lxml)"),
    "parse-scope": (parse, "BeautifulSoup (lxml)"),
    "find": (find, "BeautifulSoup (lxml)"),
    "select": (select, "BeautifulSoup (lxml)"),
    "select-relative": (_select_relative, "BeautifulSoup (lxml)"),
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
    "edit": (Mutating(_fresh, edit), "BeautifulSoup (lxml)"),
    "set-html": (Mutating(_fresh, set_html), "BeautifulSoup (lxml)"),
    "set-text": (Mutating(_fresh, set_text), "BeautifulSoup (lxml)"),
    "navigate": (navigate, "BeautifulSoup (lxml)"),
    "match": (match, "BeautifulSoup (lxml)"),
    "links-extract": (links_extract, "BeautifulSoup (lxml)"),
    "links-rewrite": (links_rewrite, "BeautifulSoup (lxml)"),
    "links-filter": (links_filter, "BeautifulSoup (lxml)"),
    "socialcard": (socialcard, "BeautifulSoup (lxml)"),
    "extract-url": (extract_url, "BeautifulSoup (lxml)"),
    "links-absolutize": (Mutating(_fresh, links_absolutize), "BeautifulSoup (lxml)"),
    "serialize-named": (_serialize_named, "BeautifulSoup (lxml)"),
}
