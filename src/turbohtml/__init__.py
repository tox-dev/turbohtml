"""turbohtml: fast, typed HTML utilities powered by a C-accelerated core."""

from __future__ import annotations

# The C extension needs these record types registered before callers can parse a document.
from ._internal import Canonical, Html, Markdown, PlainText, SelectorSyntaxError, SourceLocation, SourceSpan
from .extract import (
    Article,
    Entry,
    Feed,
    Link,
    MicrodataItem,
    OpenGraph,
    RdfaItem,
    StructuredData,
)
from .mutations import MutationObserver, MutationRecord  # registers the MutationRecord type on import
from .query import XPathString

__lazy_modules__ = [
    f"{__spec__.parent}._html",  # ty: ignore[missing-union-attribute]  # imported modules have a spec
    f"{__spec__.parent}._version",  # ty: ignore[missing-union-attribute]  # imported modules have a spec
    f"{__spec__.parent}.build",  # ty: ignore[missing-union-attribute]  # imported modules have a spec
    f"{__spec__.parent}.traverse",  # ty: ignore[missing-union-attribute]  # imported modules have a spec
]

from ._html import (
    Axis,
    CData,
    Comment,
    Doctype,
    Document,
    DocumentFragment,
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
from ._version import __version__
from .build import E, ElementMaker
from .traverse import NodeFilter, NodeIterator, TreeWalker

__all__ = [
    "Article",
    "Axis",
    "CData",
    "Canonical",
    "Comment",
    "Doctype",
    "Document",
    "DocumentFragment",
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
