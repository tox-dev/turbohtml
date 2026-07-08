# Subsystem: features (_c/features) — sanitizing, linkify scanning, annotation, and type registration.
import re
from collections.abc import Callable, Iterable, Mapping

from turbohtml.extract._feed import Entry, Feed
from turbohtml.extract._structured_data import JSONValue, MicrodataItem, OpenGraph, RdfaItem, StructuredData

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
def _date_scan(text: str, current_year: int, /) -> tuple[int, int, int] | None: ...
def _date_scan_all(text: str, current_year: int, /) -> list[tuple[int, int, int]]: ...
def _date_url(url: str, /) -> tuple[int, int, int] | None: ...
def _url_split(url: str, /) -> tuple[str, str, str, str, str, str, str, str, bool, int]: ...
def _url_percent_encode(text: str, url_set: int, /) -> str: ...
def _url_percent_decode(text: str, /) -> str: ...
def _url_join(base: str, target: str, /) -> str: ...
def _url_to_ascii(host: str, /) -> str: ...
def _register_links(link_type: type, /) -> None: ...
def _register_structured_data(
    parser: Callable[[list[str]], list[JSONValue]],
    microdata_item: type[MicrodataItem],
    rdfa_item: type[RdfaItem],
    structured_data: type[StructuredData],
    opengraph: type[OpenGraph],
    /,
) -> None: ...
def _register_feed(feed: type[Feed], entry: type[Entry], /) -> None: ...
def _register_article(article_type: type, /) -> None: ...
def _register_locations(location_type: type, span_type: type, /) -> None: ...
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
    strip_templates: bool,
    removed: list[tuple[str, str | None]] | None,
    allowed_styles: Mapping[str, Mapping[str, tuple[re.Pattern[str], ...]]],
    transform_tags: Mapping[str, tuple[str, Mapping[str, str]]],
    isolate_named_props: bool,
    custom_element_check: Callable[[str], bool] | None,
    custom_attribute_check: Callable[[str, str], bool] | None,
    allow_customized_builtins: bool,
    allow_html: bool,
    allow_svg: bool,
    allow_mathml: bool,
    /,
) -> None: ...
def annotation_surface(text: str, spans: Iterable[tuple[int, int, str]], /) -> dict[str, list[str]]: ...
def annotation_tags(text: str, spans: Iterable[tuple[int, int, str]], /) -> str: ...
