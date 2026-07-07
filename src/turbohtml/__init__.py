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
    Range,
    ShadowRoot,
    StaticRange,
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
    parse_xml,
    tokenize,
    unescape,
)
from ._links import Link  # registers the Link record type with the C core on import
from ._locations import SourceLocation, SourceSpan  # registers the source-location record types on import
from ._render import Canonical, Html, Markdown, PlainText
from ._selectors import SelectorSyntaxError  # registers the selector error type with the C core on import
from ._structured_data import (  # registers the JSON-LD parser and record classes with the C core on import
    MicrodataItem,
    OpenGraph,
    RdfaItem,
    StructuredData,
)
from ._xpath import XPathString  # registers the smart-string type with the C core on import
from .build import E, ElementMaker
from .mutations import MutationObserver, MutationRecord  # registers the MutationRecord type on import
from .traverse import NodeFilter, NodeIterator, TreeWalker

__version__ = version("turbohtml")
"""The installed package version."""

__all__ = [
    "Article",
    "Axis",
    "CData",
    "Canonical",
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
    "MutationObserver",
    "MutationRecord",
    "Namespace",
    "Node",
    "NodeFilter",
    "NodeIterator",
    "OpenGraph",
    "ParseError",
    "PlainText",
    "ProcessingInstruction",
    "Range",
    "RdfaItem",
    "SelectorSyntaxError",
    "ShadowRoot",
    "SourceLocation",
    "SourceSpan",
    "StaticRange",
    "StructuredData",
    "Text",
    "Token",
    "TokenType",
    "Tokenizer",
    "TreeWalker",
    "XPath",
    "XPathString",
    "__version__",
    "annotation_surface",
    "annotation_tags",
    "escape",
    "parse",
    "parse_fragment",
    "parse_xml",
    "tokenize",
    "unescape",
]
