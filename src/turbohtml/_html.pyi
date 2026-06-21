# Aggregate stub for the compiled turbohtml._html extension; re-exports the _stubs subsystem packages.
from ._stubs.dom import (
    Axis as Axis,
    CData as CData,
    Comment as Comment,
    Doctype as Doctype,
    Document as Document,
    Element as Element,
    HTMLParseError as HTMLParseError,
    IncrementalParser as IncrementalParser,
    Namespace as Namespace,
    Node as Node,
    ParseError as ParseError,
    ProcessingInstruction as ProcessingInstruction,
    Text as Text,
    _Filter as _Filter,
    _parse_fragment as _parse_fragment,
    _parse_only as _parse_only,
    _parse_tree as _parse_tree,
    _reconstruct as _reconstruct,
    parse as parse,
    parse_fragment as parse_fragment,
)
from ._stubs.features import (
    _linkify_find as _linkify_find,
    _linkify_scan as _linkify_scan,
    _register_links as _register_links,
    _register_markup as _register_markup,
    _sanitize as _sanitize,
    annotation_surface as annotation_surface,
    annotation_tags as annotation_tags,
)
from ._stubs.query import (
    _register_xpath_string as _register_xpath_string,
    _xpath_parse as _xpath_parse,
)
from ._stubs.serialize import (
    Formatter as Formatter,
    Indent as Indent,
    Minify as Minify,
    _markup_escape as _markup_escape,
    _markup_escape_silent as _markup_escape_silent,
    _markup_soft_str as _markup_soft_str,
    escape as escape,
    unescape as unescape,
)
from ._stubs.tokenizer import (
    Token as Token,
    TokenType as TokenType,
    Tokenizer as Tokenizer,
    _tokenize_states as _tokenize_states,
    tokenize as tokenize,
)
