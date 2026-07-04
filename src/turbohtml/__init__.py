"""turbohtml: fast, typed HTML utilities powered by a C-accelerated core."""

from __future__ import annotations

from importlib.metadata import version

from ._article import Article  # registers the Article record type with the C core on import
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
    XPath,
    annotation_surface,
    annotation_tags,
    escape,
    parse,
    parse_fragment,
    tokenize,
    unescape,
)
from ._links import Link  # registers the Link record type with the C core on import
from ._minify import JSMinify, minify_js
from ._render import Html, Markdown, PlainText
from ._structured_data import (  # registers the JSON-LD parser and record classes with the C core on import
    MicrodataItem,
    RdfaItem,
    StructuredData,
)
from ._xpath import XPathString  # registers the smart-string type with the C core on import
from .build import E, ElementMaker

__version__ = version("turbohtml")
"""The installed package version."""

__all__ = [
    "Article",
    "Axis",
    "CData",
    "Comment",
    "Doctype",
    "Document",
    "E",
    "Element",
    "ElementMaker",
    "Formatter",
    "HTMLParseError",
    "Html",
    "IncrementalParser",
    "Indent",
    "JSMinify",
    "Link",
    "Markdown",
    "MicrodataItem",
    "Minify",
    "Namespace",
    "Node",
    "ParseError",
    "PlainText",
    "ProcessingInstruction",
    "RdfaItem",
    "StructuredData",
    "Text",
    "Token",
    "TokenType",
    "Tokenizer",
    "XPath",
    "XPathString",
    "__version__",
    "annotation_surface",
    "annotation_tags",
    "escape",
    "minify_js",
    "parse",
    "parse_fragment",
    "tokenize",
    "unescape",
]
