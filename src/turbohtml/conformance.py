"""
HTML5 authoring-conformance checking with a severity model.

:func:`check` runs the WHATWG document-conformance requirements the parser does not raise
as a :class:`~turbohtml.ParseError` -- a missing ``img`` alt, obsolete presentational
markup, a duplicate ``id``, an invalid or redundant ARIA role, an empty heading, a
``section`` without a heading, a document with no title or language -- over a tree parsed
with :func:`turbohtml.parse`. It returns a :class:`ConformanceReport`: a ``valid`` verdict
(the document is valid exactly when nothing is an error) and every :class:`ConformanceMessage`
found. Each message carries a stable ``code``, a ``severity`` (``"error"``, ``"warning"``, or
``"info"``), a human-readable ``message``, and the source ``line``/``column``, the way the Nu
Html Checker (validator.nu) classifies its findings by type and subType.

The whole walk runs in the C core; the Python layer only shapes the input and wraps the C
findings into the report records. :func:`check_html` is the shorthand that parses a markup
string first.
"""

from __future__ import annotations

from itertools import starmap
from typing import TYPE_CHECKING, Literal, NamedTuple

from ._html import _conformance_check, parse

if TYPE_CHECKING:
    from ._html import Document, Node

__all__ = [
    "ConformanceMessage",
    "ConformanceReport",
    "Severity",
    "check",
    "check_html",
]

Severity = Literal["error", "warning", "info"]
"""The three severities a :class:`ConformanceMessage` carries, ordered most to least serious."""


class ConformanceMessage(NamedTuple):
    """
    One conformance finding.

    :param code: a stable machine-readable identifier for the rule, such as ``"img-missing-alt"``.
    :param severity: ``"error"`` for a violated requirement, ``"warning"`` for an authoring
        recommendation, ``"info"`` for an advisory note.
    :param message: a human-readable description of what was found.
    :param line: the 1-based source line of the offending node, or ``0`` when unknown.
    :param column: the 0-based source column of the offending node, or ``0`` when unknown.
    """

    code: str
    severity: Severity
    message: str
    line: int
    column: int


class ConformanceReport(NamedTuple):
    """
    The outcome of a conformance check: a ``valid`` verdict and every message found.

    The report is truthy exactly when the document is valid (no message is an error), so
    ``if check(doc):`` reads naturally. Warnings and info messages never affect the verdict.
    """

    valid: bool
    messages: tuple[ConformanceMessage, ...]

    def __bool__(self) -> bool:
        """Whether the document is valid, so the report reads as a truth value."""
        return self.valid

    @property
    def errors(self) -> tuple[ConformanceMessage, ...]:
        """Only the ``"error"`` messages -- the findings that make the document invalid."""
        return tuple(message for message in self.messages if message.severity == "error")

    @property
    def warnings(self) -> tuple[ConformanceMessage, ...]:
        """Only the ``"warning"`` messages -- the authoring recommendations."""
        return tuple(message for message in self.messages if message.severity == "warning")

    @property
    def infos(self) -> tuple[ConformanceMessage, ...]:
        """Only the ``"info"`` messages -- the advisory notes."""
        return tuple(message for message in self.messages if message.severity == "info")


def check(document: Document | Node) -> ConformanceReport:
    """
    Check a parsed document (or any subtree) against the HTML5 authoring rules.

    The document-level rules (a missing title or ``lang``) apply only when ``document`` is a
    whole :class:`~turbohtml.Document`; on a fragment or a subtree only the per-element rules run.

    :param document: a tree parsed with :func:`turbohtml.parse` (or an element within one).
    :returns: the :class:`ConformanceReport` with the verdict and every message.
    """
    messages = tuple(starmap(ConformanceMessage, _conformance_check(document)))
    valid = not any(message.severity == "error" for message in messages)
    return ConformanceReport(valid, messages)


def check_html(markup: str) -> ConformanceReport:
    """
    Parse a markup string with :func:`turbohtml.parse` and check the resulting document.

    :param markup: the HTML to parse and check.
    :returns: the :class:`ConformanceReport` for the parsed document.
    """
    return check(parse(markup))
