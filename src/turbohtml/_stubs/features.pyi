# Subsystem: features (_c/features) — sanitizing, linkify scanning, annotation, and type registration.
from collections.abc import Callable, Iterable, Mapping

from turbohtml._structured_data import JSONValue, MicrodataItem, OpenGraph, RdfaItem, StructuredData

from .dom import Element

def _register_markup(markup_type: type, /) -> None: ...
def _linkify_scan(
    text: str,
    parse_email: bool,
    bare_domains: bool,
    extra_tlds: tuple[str, ...] = ...,
    url_schemes: tuple[str, ...] = ...,
    /,
) -> list[tuple[int, int, int]]: ...
def _linkify_find(
    text: str,
    emails: bool,
    bare_domains: bool,
    extra_tlds: tuple[str, ...],
    schemes: tuple[str, ...],
    url_schemes: tuple[str, ...] = ...,
    /,
) -> list[tuple[int, int, int]]: ...
def _registrable_domain(host: str, /) -> str: ...
def _url_split(url: str, /) -> tuple[str, str, str, str, str, str, str, str, bool, int]: ...
def _register_links(link_type: type, /) -> None: ...
def _register_structured_data(
    parser: Callable[[list[str]], list[JSONValue]],
    microdata_item: type[MicrodataItem],
    rdfa_item: type[RdfaItem],
    structured_data: type[StructuredData],
    opengraph: type[OpenGraph],
    /,
) -> None: ...
def _register_article(article_type: type, /) -> None: ...
def _sanitize(
    element: Element,
    tags: frozenset[str],
    attributes: Mapping[str, frozenset[str]],
    url_schemes: frozenset[str],
    allow_relative: bool,
    on_disallowed: int,
    strip_comments: bool,
    add_link_rel: str | None,
    attribute_filter: Callable[[str, str, str], str | None] | None,
    set_attributes: Mapping[str, Mapping[str, str]],
    remove_with_content: frozenset[str],
    css_properties: frozenset[str],
    attribute_prefixes: frozenset[str],
    attribute_values: Mapping[str, Mapping[str, frozenset[str]]],
    media_hosts: frozenset[str],
    /,
) -> None: ...
def annotation_surface(text: str, spans: Iterable[tuple[int, int, str]], /) -> dict[str, list[str]]: ...
def annotation_tags(text: str, spans: Iterable[tuple[int, int, str]], /) -> str: ...
