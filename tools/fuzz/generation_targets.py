"""Keep generated source contexts separate from qualified export ownership."""

from __future__ import annotations

import codecs
import re
from importlib import import_module
from typing import TYPE_CHECKING, Final

import html5lib
from html5lib.constants import namespaces

from turbohtml import Node, parse

from .atheris_registry import Target
from .markdown_structure_generators import MarkdownProfileError, markdown_html_check, markdown_source_check

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from xml.etree.ElementTree import Element


# Standalone adapters cannot depend on registry import order for codec registration.
import_module("turbohtml.detect")

# html5lib scans its open-element stack on every insertion, so its time is quadratic in nesting depth: about 100 ms at
# 2500 nested divs. turbohtml caps its own tree depth and parses the same input in milliseconds.
_ORACLE_MAX_NESTING: Final[int] = 2500
_TAG: Final[re.Pattern[str]] = re.compile(r"<(?P<closing>/?)[a-zA-Z]")


def generation_targets(
    encoding: str = "UTF-8", *, sniff: bool = False, render: Callable[[Node], str] = Node.to_markdown
) -> tuple[Target, ...]:
    # Fix execution contexts without assigning a second qualified export owner.
    codecs.lookup(f"whatwg-{encoding}")

    def source(data: bytes) -> None:
        if error := markdown_source_check(data.decode("utf-8"), render):
            raise AssertionError(error)

    def markup(data: bytes) -> None:
        if error := markdown_html_check(data.decode("utf-8"), render):
            raise AssertionError(error)

    def encoded(data: bytes) -> None:
        encoding_observation(data, encoding, sniff=sniff)

    return (
        Target(
            name="markdown-source", callback=source, exports=(), exceptions=(UnicodeDecodeError, MarkdownProfileError)
        ),
        Target(
            name="markdown-html", callback=markup, exports=(), exceptions=(UnicodeDecodeError, MarkdownProfileError)
        ),
        Target(name="encoding-bytes", callback=encoded, exports=()),
    )


def encoding_observation(
    data: bytes,
    encoding: str = "UTF-8",
    *,
    sniff: bool = False,
    decode: Callable[[bytes, str], str] = bytes.decode,
) -> str:
    # Compare decoded text with an independent HTML parser before admitting bytes.
    document: Final = parse(data, detect_encoding=True) if sniff else parse(data, encoding=encoding)
    # BOMs override explicit codecs; late declarations can restart sniffed parsing.
    decoded: Final = decode(data, f"whatwg-{document.encoding}").removeprefix("\ufeff")
    nesting: Final = _nesting(decoded)
    if nesting <= _ORACLE_MAX_NESTING:
        unicode_text: Final = parse(decoded).text
        if document.text != unicode_text:
            msg = f"Encoded parser text differs: {document.text!r} != {unicode_text!r}"
            raise AssertionError(msg)
        # The C tree builder flattens past 512 open elements; html5lib does not.
        if nesting > 500:
            return document.text
        reference: Final = html5lib.parse(decoded, namespaceHTMLElements=False)
        expected: Final = _reference_text(reference)
        # html5lib's former in-select rules ignore style and title content that the current HTML parser keeps.
        if unicode_text != expected and not document.select("select style, select title"):
            # html5lib leaves an unmatched </p> inside foreign content, then replaces NULs that HTML drops.
            foreign: Final = tuple(f"{{{namespaces[name]}}}" for name in ("svg", "mathml"))
            misplaced_p: Final = any(
                isinstance(parent.tag, str)
                and parent.tag.startswith(foreign)
                and any(child.tag == "p" for child in parent)
                for parent in reference.iter()
            )
            without_p_nulls: Final = re.sub(
                r"</p>[^<]*", lambda match: match[0].replace("\0", ""), decoded, flags=re.IGNORECASE
            )
            if not (
                "\0" in decoded
                and "</p>" in decoded.lower()
                and misplaced_p
                and unicode_text == _reference_text(html5lib.parse(without_p_nulls, namespaceHTMLElements=False))
            ):
                msg = f"Encoded parser text differs: {document.text!r} != {expected!r}"
                raise AssertionError(msg)
    return document.text


def _nesting(markup: str) -> int:
    # Most start tags left open at once; implied end tags and void elements make it an upper bound.
    deepest = depth = 0
    for match in _TAG.finditer(markup):
        depth = max(depth - 1, 0) if match["closing"] else depth + 1
        deepest = max(deepest, depth)
    return deepest


def _reference_text(element: Element) -> str:
    parts: list[str] = [element.text or ""]
    stack: list[tuple[Element, Iterator[Element]]] = [(element, iter(element))]
    while stack:
        try:
            child = next(stack[-1][1])
        except StopIteration:
            finished, _ = stack.pop()
            if stack:
                parts.append(finished.tail or "")
            continue
        if isinstance(child.tag, str):
            parts.append(child.text or "")
            stack.append((child, iter(child)))
        else:
            parts.append(child.tail or "")
    return "".join(parts)


__all__ = [
    "encoding_observation",
    "generation_targets",
]
