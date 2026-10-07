"""Keep generated source contexts separate from qualified export ownership."""

from __future__ import annotations

import codecs
from importlib import import_module
from typing import TYPE_CHECKING, Final

import html5lib

from turbohtml import Node, parse

from .atheris_registry import Target
from .markdown_structure_generators import MarkdownProfileError, markdown_html_check, markdown_source_check

if TYPE_CHECKING:
    from collections.abc import Callable
    from xml.etree.ElementTree import Element


# Standalone adapters cannot depend on registry import order for codec registration.
import_module("turbohtml.detect")


def generation_targets(
    encoding: str = "UTF-8", *, sniff: bool = False, render: Callable[[Node], str] = Node.to_markdown
) -> tuple[Target, ...]:
    """Fix execution contexts without assigning a second qualified export owner."""
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
        Target("markdown-source", source, (), (UnicodeDecodeError, MarkdownProfileError)),
        Target("markdown-html", markup, (), (UnicodeDecodeError, MarkdownProfileError)),
        Target("encoding-bytes", encoded, ()),
    )


def encoding_observation(
    data: bytes,
    encoding: str = "UTF-8",
    *,
    sniff: bool = False,
    decode: Callable[[bytes, str], str] = bytes.decode,
) -> str:
    """Compare decoded text with an independent HTML parser before admitting bytes."""
    document: Final = parse(data, detect_encoding=True) if sniff else parse(data, encoding=encoding)
    # BOMs override explicit codecs; late declarations can restart sniffed parsing.
    decoded: Final = decode(data, f"whatwg-{document.encoding}").removeprefix("\ufeff")
    expected: Final = _reference_text(html5lib.parse(decoded, namespaceHTMLElements=False))
    if (document.text, parse(decoded).text) != (expected, expected):
        msg = f"Encoded parser text differs: {document.text!r} != {expected!r}"
        raise AssertionError(msg)
    return document.text


def _reference_text(element: Element) -> str:
    return (element.text or "") + "".join(
        (_reference_text(child) if isinstance(child.tag, str) else "") + (child.tail or "") for child in element
    )


__all__ = ["encoding_observation", "generation_targets"]
