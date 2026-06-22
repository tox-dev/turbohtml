"""
A drop-in for ``bleach.clean`` for projects migrating off bleach.

This is a thin translator over :mod:`turbohtml.sanitizer`, kept apart from the main API so the drop-in surface stays
bounded. ``clean(text, tags=..., attributes=..., protocols=..., strip=...)`` keeps bleach's signature, including the
list, per-tag-dict, and callable forms of ``attributes``, so a bleach call works with only the import changed. The one
intentional difference is that event-handler attributes and ``javascript:`` URLs are dropped unconditionally here, even
when a permissive ``attributes`` callable would keep them, because the underlying policy's safety baseline is not
negotiable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from turbohtml.sanitizer import DEFAULT_ATTRIBUTES, DEFAULT_SCHEMES, DEFAULT_TAGS, OnDisallowed, Policy, sanitize

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

#: bleach's default allowed tags, attributes, and protocols, under their bleach names.
ALLOWED_TAGS = DEFAULT_TAGS
ALLOWED_ATTRIBUTES = DEFAULT_ATTRIBUTES
ALLOWED_PROTOCOLS = DEFAULT_SCHEMES

# bleach attributes come in three shapes: a flat list for every tag, a per-tag dict (whose values may themselves be a
# list or a predicate), or a single predicate over (tag, name, value). The first element of each returned pair is the
# static name allowlist (``"*"`` means any name), the second the predicate folded into a value-rewriting filter.
_BleachAttributes = (
    "Iterable[str] | Mapping[str, Iterable[str] | Callable[[str, str, str], bool]] | Callable[[str, str, str], bool]"
)


def clean(  # noqa: PLR0913, PLR0917  # this is bleach.clean's signature, kept verbatim for drop-in compatibility
    text: str,
    tags: Iterable[str] | None = None,
    attributes: object = None,
    protocols: Iterable[str] | None = None,
    strip: bool = False,  # noqa: FBT001, FBT002  # bleach keeps strip a positional flag
    strip_comments: bool = True,  # noqa: FBT001, FBT002  # bleach keeps strip_comments a positional flag
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
    names, attribute_filter = _convert_attributes(ALLOWED_ATTRIBUTES if attributes is None else attributes)
    policy = Policy(
        tags=ALLOWED_TAGS if tags is None else frozenset(tags),
        attributes=names,
        url_schemes=ALLOWED_PROTOCOLS if protocols is None else frozenset(protocols),
        on_disallowed_tag=OnDisallowed.STRIP if strip else OnDisallowed.ESCAPE,
        strip_comments=strip_comments,
        attribute_filter=attribute_filter,
    )
    return sanitize(text, policy)


def _convert_attributes(
    attributes: object,
) -> tuple[Mapping[str, frozenset[str]], Callable[[str, str, str], str | None] | None]:
    """Translate bleach's three ``attributes`` shapes into a name allowlist and an optional value filter."""
    if callable(attributes):
        predicate = cast("Callable[[str, str, str], bool]", attributes)
        return {"*": frozenset({"*"})}, lambda tag, name, value: value if predicate(tag, name, value) else None
    if isinstance(attributes, Mapping):
        mapping = cast("Mapping[str, Iterable[str] | Callable[[str, str, str], bool]]", attributes)
        names: dict[str, frozenset[str]] = {}
        predicates: dict[str, Callable[[str, str, str], bool]] = {}
        for tag, value in mapping.items():
            if callable(value):
                names[tag] = frozenset({"*"})
                predicates[tag] = cast("Callable[[str, str, str], bool]", value)
            else:
                names[tag] = frozenset(cast("Iterable[str]", value))
        return names, _per_tag_filter(predicates) if predicates else None
    return {"*": frozenset(cast("Iterable[str]", attributes))}, None


def _per_tag_filter(
    predicates: dict[str, Callable[[str, str, str], bool]],
) -> Callable[[str, str, str], str | None]:
    """Fold per-tag bleach predicates into one value-rewriting filter, with the ``"*"`` predicate as the fallback."""

    def attribute_filter(tag: str, name: str, value: str) -> str | None:
        # a tag-specific predicate wins; otherwise the "*" predicate applies, so a wildcard callable does not fail open
        predicate = predicates.get(tag) or predicates.get("*")
        if predicate is None:
            return value
        return value if predicate(tag, name, value) else None

    return attribute_filter


__all__ = [
    "ALLOWED_ATTRIBUTES",
    "ALLOWED_PROTOCOLS",
    "ALLOWED_TAGS",
    "clean",
]
