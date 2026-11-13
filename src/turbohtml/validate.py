"""
XSD (XML Schema) and RELAX NG schema validation for parsed XML documents.

:class:`XMLSchema` and :class:`RelaxNG` mirror lxml's ``etree.XMLSchema`` and
``etree.RelaxNG``: construct one from schema text (or a parsed schema document), then
call :meth:`~XMLSchema.validate` on a document parsed with :func:`turbohtml.parse_xml`.
The schema compiles once in the C core; each validation walks the instance tree there
and returns a :class:`ValidationResult` -- a ``valid`` flag plus a tuple of
:class:`ValidationError` records locating every violation. The Python layer only shapes
the input and wraps the C result; every datatype, facet, content-model, and RELAX NG
derivative check runs in the extension.
"""

from __future__ import annotations

from itertools import starmap
from typing import TYPE_CHECKING, Final, NamedTuple

from ._html import schema_compile as _schema_compile
from ._html import schema_validate as _schema_validate

if TYPE_CHECKING:
    from pathlib import Path

    from ._html import Document, Node


class _Schema:
    # Shared machinery for the two schema languages; not instantiated directly.
    def __init__(self, compiled: object) -> None:
        self._compiled: Final = compiled

    def is_valid(self, document: Document | Node) -> bool:
        """
        Whether the document satisfies the schema.

        :param document: the parsed document or element to check.
        :returns: whether the document satisfies the schema.
        """
        return _schema_validate(self._compiled, document, collect_errors=False)[0]

    def assert_valid(self, document: Document | Node) -> None:
        """
        Validate the document, raising :class:`SchemaValidationError` with the errors when it is invalid.

        :param document: the parsed document or element to check.
        :raises SchemaValidationError: if the document is invalid.
        """
        result = self.validate(document)
        if not result.valid:
            raise SchemaValidationError(result.errors)

    def validate(self, document: Document | Node) -> ValidationResult:
        """
        Validate a parsed document (or element), returning the full :class:`ValidationResult`.

        :param document: the parsed document or element to check.
        :returns: the validation outcome, its verdict and the errors found.
        """
        valid, errors = _schema_validate(self._compiled, document)
        return ValidationResult(valid, tuple(starmap(ValidationError, errors)))


class SchemaValidationError(Exception):
    """Raised by :meth:`XMLSchema.assert_valid` / :meth:`RelaxNG.assert_valid` when a document is invalid."""

    def __init__(self, errors: tuple[ValidationError, ...]) -> None:
        """
        Carry the ``errors`` the failed validation produced, the first of which becomes the message.

        :param errors: the errors the failed validation produced.
        """
        self.errors = errors
        super().__init__(errors[0].message if errors else "document is invalid")


class ValidationResult(NamedTuple):
    """
    The outcome of validating a document: a ``valid`` flag and the errors found.

    The result is truthy exactly when the document is valid, so ``if schema.validate(doc):``
    reads naturally.

    :param valid: whether the document is valid.
    :param errors: the errors found, empty when the document is valid.
    """

    valid: bool
    errors: tuple[ValidationError, ...]

    def __bool__(self) -> bool:
        """
        Whether the document is valid, so the result reads as a truth value.

        :returns: whether the document is valid.
        """
        return self.valid


class ValidationError(NamedTuple):
    """
    One schema violation.

    :param message: a human-readable description of what failed.
    :param path: the ``/root/child`` document-order path of the offending node.
    :param line: the 1-based source line of the node, or ``0`` when unknown.
    :param type: the violation category -- ``"structure"``, ``"datatype"``, or ``"facet"``.
    """

    message: str
    path: str
    line: int
    type: str


class XMLSchema(_Schema):
    """
    A compiled XSD 1.0 schema.

    :raises ValueError: when the schema is malformed, its root is not ``xs:schema``, a ``ref``/``type``/``base``
        does not resolve, a group, attribute-group, or type-derivation reference forms a cycle or a chain over 100
        hops, or an ``xs:unique``/``xs:key``/``xs:keyref`` identity constraint is declared.
    """

    def __init__(self, source: str | Node) -> None:
        """
        Compile the schema once; every validation reuses the result.

        :param source: the schema as XSD text, or a schema document parsed with :func:`turbohtml.parse_xml`.
        """
        super().__init__(_schema_compile(0, _text(source)))


def _text(source: str | Node) -> str:
    return source if isinstance(source, str) else source.serialize()


class RelaxNG(_Schema):
    """
    A compiled RELAX NG schema (XML syntax).

    :raises ValueError: when the schema is malformed, references a file without a ``base_url``, or a reference
        escapes ``include_root``, carries a fragment, loops, loads the wrong kind of pattern, or overrides a component
        the included grammar lacks.
    :raises OSError: when a referenced file cannot be read.
    """

    def __init__(
        self, source: str | Node, *, base_url: str | None = None, include_root: str | Path | None = None
    ) -> None:
        """
        Compile the schema, reading the files it references, once; every validation reuses the result.

        :param source: the schema as RELAX NG text, or a schema document parsed with :func:`turbohtml.parse_xml`.
        :param base_url: the schema's path or file URL, against which each ``include`` and ``externalRef`` href resolves
            (after any ``xml:base``); required only when the schema references other files.
        :param include_root: when set, referenced files must resolve inside this directory, including through nested
            references.
        """
        super().__init__(_schema_compile(1, _text(source), base_url, include_root))


__all__ = [
    "RelaxNG",
    "SchemaValidationError",
    "ValidationError",
    "ValidationResult",
    "XMLSchema",
]
