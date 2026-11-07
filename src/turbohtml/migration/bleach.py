"""
A compatible ``bleach.clean`` interface for projects migrating off bleach.

This is a thin translator over :mod:`turbohtml.clean`, kept apart from the main API so the compatibility surface stays
bounded. ``clean(text, tags=..., attributes=..., protocols=..., strip=...)`` keeps bleach's signature, including the
list, per-tag-dict, and callable forms of ``attributes``. Kept attributes and text may change entity spelling after
serialization. Event-handler attributes and ``javascript:`` URLs are dropped unconditionally, even when a permissive
``attributes`` callable would keep them, because the underlying policy's safety baseline is fixed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TypeAlias

from turbohtml._html import _bleach_allow_relative, _bleach_attributes
from turbohtml.clean import DEFAULT_ATTRIBUTES, DEFAULT_SCHEMES, DEFAULT_TAGS, OnDisallowed, Policy, sanitize

#: bleach's default allowed tags, attributes, and protocols, under their bleach names.
ALLOWED_TAGS = DEFAULT_TAGS
ALLOWED_ATTRIBUTES = DEFAULT_ATTRIBUTES
ALLOWED_PROTOCOLS = DEFAULT_SCHEMES

_AttributeRules: TypeAlias = (
    Iterable[str] | Mapping[str, Iterable[str] | Callable[[str, str, str], bool]] | Callable[[str, str, str], bool]
)


def clean(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]  # this is bleach.clean's signature, kept verbatim for compatibility
    text: str,
    tags: Iterable[str] | None = None,
    attributes: _AttributeRules | None = None,
    protocols: Iterable[str] | None = None,
    strip: bool = False,  # ruff:ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]  # bleach keeps strip a positional flag
    strip_comments: bool = True,  # ruff:ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]  # bleach keeps strip_comments a positional flag
    css_sanitizer: object = None,
) -> str:
    """
    Sanitize HTML against a bleach-style allowlist, the way ``bleach.clean`` did.

    :param text: the untrusted HTML.
    :param tags: the allowed tag names; None uses bleach's default set.
    :param attributes: the allowed attributes as a flat list, a per-tag dict, or a (tag, name, value) predicate;
        None uses bleach's default.
    :param protocols: the allowed URL schemes; None uses bleach's default.
    :param strip: drop a disallowed tag and keep its children, rather than escaping it to text.
    :param strip_comments: drop HTML comments from the output.
    :param css_sanitizer: accepted for signature compatibility; passing one raises ``NotImplementedError``.
    :returns: the sanitized, safe HTML.
    """
    if css_sanitizer is not None:  # CSS sanitizing is a separate sub-problem, not yet ported
        msg = "css_sanitizer is not implemented yet; drop the style attribute and <style> instead"
        raise NotImplementedError(msg)
    names, attribute_predicate = attribute_policy(ALLOWED_ATTRIBUTES if attributes is None else attributes)
    if protocols is None:
        policy = Policy(
            tags=ALLOWED_TAGS if tags is None else frozenset(tags),
            attributes=names,
            url_schemes=ALLOWED_PROTOCOLS,
            on_disallowed_tag=OnDisallowed.STRIP if strip else OnDisallowed.ESCAPE,
            strip_comments=strip_comments,
            attribute_predicate=attribute_predicate,
            _bleach_url_policy=True,
        )
    else:
        schemes = frozenset(protocols)
        policy = Policy(
            tags=ALLOWED_TAGS if tags is None else frozenset(tags),
            attributes=names,
            url_schemes=schemes,
            allow_relative_urls=_bleach_allow_relative(schemes),
            allow_fragment_urls=True,
            on_disallowed_tag=OnDisallowed.STRIP if strip else OnDisallowed.ESCAPE,
            strip_comments=strip_comments,
            attribute_predicate=attribute_predicate,
            _bleach_url_policy=True,
        )
    return sanitize(text, policy)


def attribute_policy(
    attributes: _AttributeRules,
) -> tuple[dict[str, frozenset[str]], Callable[[str, str, str], bool] | None]:
    """Keep bleach attribute rules when configuring a native ``Policy``."""
    return _bleach_attributes(attributes, Mapping)


__all__ = [
    "ALLOWED_ATTRIBUTES",
    "ALLOWED_PROTOCOLS",
    "ALLOWED_TAGS",
    "attribute_policy",
    "clean",
]
