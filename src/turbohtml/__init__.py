"""turbohtml: fast, typed HTML utilities powered by a C-accelerated core."""

from __future__ import annotations

from importlib.metadata import version

from . import (
    _cssmin,  # noqa: F401  # registers the CSSMinify record type with the C core on import
    _jsminify,  # noqa: F401  # registers the JSMinify record type with the C core on import
)
from ._article import Article  # registers the Article record type with the C core on import
from ._feed import Entry, Feed  # registers the Feed/Entry record types with the C core on import
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
from ._render import Html, Markdown, PlainText
from ._selectors import SelectorSyntaxError  # registers the selector error type with the C core on import
from ._structured_data import (  # registers the JSON-LD parser and record classes with the C core on import
    MicrodataItem,
    OpenGraph,
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
    "Entry",
    "Feed",
    "Formatter",
    "HTMLParseError",
    "Html",
    "IncrementalParser",
    "Indent",
    "Link",
    "Markdown",
    "MicrodataItem",
    "Minify",
    "Namespace",
    "Node",
    "OpenGraph",
    "ParseError",
    "PlainText",
    "ProcessingInstruction",
    "RdfaItem",
    "SelectorSyntaxError",
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
    "parse",
    "parse_fragment",
    "tokenize",
    "unescape",
]
