"""turbohtml: fast, typed HTML utilities powered by a C-accelerated core."""

from __future__ import annotations

from importlib.metadata import version

from ._html import (
    Axis,
    CData,
    Comment,
    Doctype,
    Document,
    Element,
    Formatter,
    HTMLParseError,
    IncrementalParser,
    Indent,
    Minify,
    Namespace,
    Node,
    ParseError,
    ProcessingInstruction,
    Text,
    Token,
    Tokenizer,
    TokenType,
    escape,
    parse,
    parse_fragment,
    tokenize,
    unescape,
)
from ._links import Link  # registers the Link record type with the C core on import
from ._xpath import XPathString  # registers the smart-string type with the C core on import

__version__ = version("turbohtml")
"""The installed package version."""

__all__ = [
    "Axis",
    "CData",
    "Comment",
    "Doctype",
    "Document",
    "Element",
    "Formatter",
    "HTMLParseError",
    "IncrementalParser",
    "Indent",
    "Link",
    "Minify",
    "Namespace",
    "Node",
    "ParseError",
    "ProcessingInstruction",
    "Text",
    "Token",
    "TokenType",
    "Tokenizer",
    "XPathString",
    "__version__",
    "escape",
    "parse",
    "parse_fragment",
    "tokenize",
    "unescape",
]
