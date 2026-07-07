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
from typing import TYPE_CHECKING, ClassVar, Final, NamedTuple

from ._html import _schema_compile, _schema_validate

if TYPE_CHECKING:
    from ._html import Document, Node

__all__ = [
    "RelaxNG",
    "SchemaValidationError",
    "ValidationError",
    "ValidationResult",
    "XMLSchema",
]


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


class ValidationResult(NamedTuple):
    """
    The outcome of validating a document: a ``valid`` flag and the errors found.

    The result is truthy exactly when the document is valid, so ``if schema.validate(doc):``
    reads naturally.
    """

    valid: bool
    errors: tuple[ValidationError, ...]

    def __bool__(self) -> bool:
        """Whether the document is valid, so the result reads as a truth value."""
        return self.valid


class SchemaValidationError(Exception):
    """Raised by :meth:`XMLSchema.assert_valid` / :meth:`RelaxNG.assert_valid` when a document is invalid."""

    def __init__(self, errors: tuple[ValidationError, ...]) -> None:
        """Carry the ``errors`` the failed validation produced, the first of which becomes the message."""
        self.errors = errors
        super().__init__(errors[0].message if errors else "document is invalid")


class _Schema:
    """Shared machinery for the two schema languages; not instantiated directly."""

    _KIND: ClassVar[int]

    def __init__(self, source: str | Node) -> None:
        text = source if isinstance(source, str) else source.serialize()
        self._compiled: Final = _schema_compile(self._KIND, text)

    def validate(self, document: Document | Node) -> ValidationResult:
        """Validate a parsed document (or element), returning the full :class:`ValidationResult`."""
        valid, errors = _schema_validate(self._compiled, document)
        return ValidationResult(valid, tuple(starmap(ValidationError, errors)))

    def is_valid(self, document: Document | Node) -> bool:
        """Whether the document satisfies the schema."""
        return _schema_validate(self._compiled, document)[0]

    def assert_valid(self, document: Document | Node) -> None:
        """Validate the document, raising :class:`SchemaValidationError` with the errors when it is invalid."""
        result = self.validate(document)
        if not result.valid:
            raise SchemaValidationError(result.errors)


class XMLSchema(_Schema):
    """
    A compiled XSD 1.0 schema.

    :param source: the schema as XSD text, or a schema document parsed with :func:`turbohtml.parse_xml`.
    :raises ValueError: when the schema is malformed or its root is not ``xs:schema``.
    """

    _KIND = 0


class RelaxNG(_Schema):
    """
    A compiled RELAX NG schema (XML syntax).

    :param source: the schema as RELAX NG text, or a schema document parsed with :func:`turbohtml.parse_xml`.
    :raises ValueError: when the schema is malformed.
    """

    _KIND = 1
