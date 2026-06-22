"""
A safe-string for composing HTML, a drop-in for markupsafe's public surface.

A :class:`Markup` is text already safe to place in HTML. Combining it with untrusted values escapes those values, so a
string built up through ``+``, ``%``, :meth:`Markup.format`, :meth:`Markup.join`, and the inherited ``str`` operations
stays safe without a manual escape at each step. ``escape``, ``escape_silent``, and ``soft_str`` are the C entry points
from :mod:`turbohtml._html`, bound here unwrapped so template autoescaping stays a single C call.

The surface matches markupsafe's so the template stack (Jinja2, WTForms, Werkzeug, ...) can migrate by changing the
import: ``Markup`` overrides every ``str`` method that returns text to keep its result a ``Markup``, because under
autoescaping a plain ``str`` return would be escaped a second time. The implementation is turbohtml's own, though: the
escape runs in C, the overrides call ``str`` methods unbound to skip the ``super()`` proxy, and :meth:`Markup.striptags`
and :meth:`Markup.unescape` run on turbohtml's tokenizer and HTML5 reference resolution, which are faster and resolve
references markupsafe's regex stripping can miss.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING, SupportsIndex, cast

from turbohtml._html import _markup_escape as escape
from turbohtml._html import _markup_escape_silent as escape_silent
from turbohtml._html import _markup_soft_str as soft_str
from turbohtml._html import _register_markup, parse_fragment, unescape

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from typing import Protocol, SupportsFloat, SupportsInt

    from typing_extensions import Self

    class _HasHtml(Protocol):
        """An object that renders itself as already-safe HTML."""

        def __html__(self) -> str: ...

    class _HasHtmlFormat(Protocol):
        """An object that renders itself safely under a format spec."""

        def __html_format__(self, format_spec: str, /) -> str: ...  # noqa: PLW3201  # markupsafe render protocol

    class _SupportsGetItem(Protocol):
        """Any object supporting subscript, the shape a ``%`` mapping operand needs."""

        def __getitem__(self, key: object, /) -> object: ...

    class _SupportsStrGetItem(Protocol):
        """The mapping shape ``str.format_map`` accepts: a string-keyed ``__getitem__``."""

        def __getitem__(self, key: str, /) -> object: ...

    class _SupportsIntGetItem(Protocol):
        """The table shape ``str.translate`` accepts: an int-keyed ``__getitem__``."""

        def __getitem__(self, key: int, /) -> str | int | None: ...


# A str subclass is required, not error-prone here: escape builds the Markup in C through str's own constructor, and
# isinstance(value, str) must hold for the template stack, so FURB189 is suppressed. PLR0904 is suppressed because the
# class mirrors str's full method surface on purpose: any text-returning method that fell back to str would have its
# already-safe result escaped a second time under autoescaping.
class Markup(str):  # noqa: FURB189, PLR0904
    """
    Text that is already safe to embed in HTML and is not escaped again.

    Wrap a value to declare it trusted. Operations that combine it with other text escape that text first, so the
    result stays safe. Constructing ``Markup`` directly trusts its argument without escaping, so wrap only content you
    control or have already escaped; call :meth:`escape` to make untrusted text safe.

    :param base: the value to trust as safe HTML; its ``__html__`` method is used when present, and ``bytes`` is
        decoded with ``encoding``.
    :param encoding: the codec to decode ``base`` with when it is ``bytes``; ``None`` leaves a ``str`` as is.
    :param errors: the decoding error policy passed to :meth:`bytes.decode` when ``encoding`` is given.
    """

    __slots__ = ()

    def __new__(cls, base: object = "", encoding: str | None = None, errors: str = "strict") -> Self:
        """Trust ``base`` as safe HTML, calling its ``__html__`` if present and decoding bytes when asked."""
        # An exact str (every escape result and str-method wrap) has no __html__, so skip the attribute probe.
        if type(base) is not str and hasattr(base, "__html__"):
            base = cast("_HasHtml", base).__html__()
        if encoding is None:
            return str.__new__(cls, base)
        return str.__new__(cls, cast("bytes", base), encoding, errors)

    def __html__(self) -> Markup:
        """Return self, since a Markup already renders as safe HTML."""
        return self

    def __add__(self, value: str, /) -> Markup:
        """Concatenate, escaping ``value`` so the joined result stays safe."""
        if isinstance(value, str) or hasattr(value, "__html__"):
            return self.__class__(str.__add__(self, self.escape(value)))
        return NotImplemented

    def __radd__(self, value: str, /) -> Markup:
        """Concatenate with ``value`` on the left, escaping it so the result stays safe."""
        if isinstance(value, str) or hasattr(value, "__html__"):
            return self.escape(value) + self
        return NotImplemented

    def __mul__(self, count: SupportsIndex, /) -> Markup:
        """Repeat the safe string; the repetition stays safe too."""
        return self.__class__(str.__mul__(self, count))

    __rmul__ = __mul__

    def __mod__(self, arg: object, /) -> Markup:
        """Interpolate with ``%``, escaping each value as ``%`` substitutes it."""
        if isinstance(arg, tuple):
            arg = tuple(_MarkupEscapeHelper(value, self.escape) for value in arg)
        elif hasattr(type(arg), "__getitem__") and not isinstance(arg, str):
            arg = _MarkupEscapeHelper(arg, self.escape)
        else:
            arg = (_MarkupEscapeHelper(arg, self.escape),)
        return self.__class__(str.__mod__(self, arg))

    def __repr__(self) -> str:
        """Wrap the str repr so a safe string reads differently from a plain one in logs and tracebacks."""
        return f"{self.__class__.__name__}({str.__repr__(self)})"

    def join(self, iterable: Iterable[str], /) -> Markup:
        """
        Join the items, escaping each one so the result stays safe.

        :param iterable: the items to join.
        :returns: the joined text as a Markup.
        """
        return self.__class__(str.join(self, map(self.escape, iterable)))

    def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        """Split into parts that stay safe; the list type is str since list is invariant, but each part is a Markup."""
        return [self.__class__(part) for part in str.split(self, sep, maxsplit)]

    def rsplit(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        """Split from the right into parts that each stay a Markup."""
        return [self.__class__(part) for part in str.rsplit(self, sep, maxsplit)]

    def splitlines(self, keepends: bool = False) -> list[str]:  # noqa: FBT001, FBT002  # keepends is str's own flag
        """Split into lines that each stay a Markup."""
        return [self.__class__(line) for line in str.splitlines(self, keepends)]

    def unescape(self) -> str:
        """
        Resolve character references back to text.

        :returns: the resolved text as a plain (no longer safe) :class:`str`.
        """
        return unescape(str(self))

    def striptags(self) -> str:
        """
        Parse the markup, drop tags and comments, collapse whitespace, and return the plain text.

        This parses with turbohtml's tokenizer rather than scanning for ``<``, so references resolve and a comment
        that contains a ``<`` cannot end tag removal early.

        :returns: the plain text, no longer safe markup.
        """
        return " ".join(parse_fragment(str(self)).text.split())

    @classmethod
    def escape(cls, s: object, /) -> Markup:
        """
        Escape a value to this class, the entry point the composing operations use to make operands safe.

        :param s: the value to escape; an ``__html__`` method marks it already safe.
        :returns: a Markup holding the escaped text.
        """
        rv = escape(s)
        if rv.__class__ is not cls:
            return cls(rv)
        return rv

    def __getitem__(self, key: SupportsIndex | slice, /) -> Markup:
        """Index or slice into safe text; the piece stays a Markup."""
        return self.__class__(str.__getitem__(self, key))

    def capitalize(self) -> Markup:
        """Capitalize, keeping the result a Markup so autoescaping does not re-escape it."""
        return self.__class__(str.capitalize(self))

    def title(self) -> Markup:
        """Title-case, keeping the result a Markup."""
        return self.__class__(str.title(self))

    def lower(self) -> Markup:
        """Lowercase, keeping the result a Markup."""
        return self.__class__(str.lower(self))

    def upper(self) -> Markup:
        """Uppercase, keeping the result a Markup."""
        return self.__class__(str.upper(self))

    def swapcase(self) -> Markup:
        """Swap case, keeping the result a Markup."""
        return self.__class__(str.swapcase(self))

    def casefold(self) -> Markup:
        """Casefold, keeping the result a Markup."""
        return self.__class__(str.casefold(self))

    def replace(self, old: str, new: str, /, count: SupportsIndex = -1) -> Markup:
        """Replace occurrences, escaping ``new`` so substituted text stays safe."""
        return self.__class__(str.replace(self, old, self.escape(new), count))

    def ljust(self, width: SupportsIndex, fillchar: str = " ", /) -> Markup:
        """Left-justify, escaping the fill character so padding stays safe."""
        return self.__class__(str.ljust(self, width, self.escape(fillchar)))

    def rjust(self, width: SupportsIndex, fillchar: str = " ", /) -> Markup:
        """Right-justify, escaping the fill character so padding stays safe."""
        return self.__class__(str.rjust(self, width, self.escape(fillchar)))

    def center(self, width: SupportsIndex, fillchar: str = " ", /) -> Markup:
        """Center, escaping the fill character so padding stays safe."""
        return self.__class__(str.center(self, width, self.escape(fillchar)))

    def strip(self, chars: str | None = None, /) -> Markup:
        """Strip surrounding characters, keeping the result a Markup."""
        return self.__class__(str.strip(self, chars))

    def lstrip(self, chars: str | None = None, /) -> Markup:
        """Strip leading characters, keeping the result a Markup."""
        return self.__class__(str.lstrip(self, chars))

    def rstrip(self, chars: str | None = None, /) -> Markup:
        """Strip trailing characters, keeping the result a Markup."""
        return self.__class__(str.rstrip(self, chars))

    def removeprefix(self, prefix: str, /) -> Markup:
        """Remove a prefix, keeping the result a Markup."""
        return self.__class__(str.removeprefix(self, prefix))

    def removesuffix(self, suffix: str, /) -> Markup:
        """Remove a suffix, keeping the result a Markup."""
        return self.__class__(str.removesuffix(self, suffix))

    def expandtabs(self, tabsize: SupportsIndex = 8) -> Markup:
        """Expand tabs, keeping the result a Markup."""
        return self.__class__(str.expandtabs(self, tabsize))

    def zfill(self, width: SupportsIndex, /) -> Markup:
        """Zero-fill, keeping the result a Markup."""
        return self.__class__(str.zfill(self, width))

    def translate(self, table: _SupportsIntGetItem, /) -> Markup:
        """Translate through a table, keeping the result a Markup; the caller's table values are trusted."""
        return self.__class__(str.translate(self, table))

    def partition(self, sep: str, /) -> tuple[Markup, Markup, Markup]:
        """Partition into three parts that each stay a Markup."""
        left, middle, right = str.partition(self, sep)
        return self.__class__(left), self.__class__(middle), self.__class__(right)

    def rpartition(self, sep: str, /) -> tuple[Markup, Markup, Markup]:
        """Partition from the right into three parts that each stay a Markup."""
        left, middle, right = str.rpartition(self, sep)
        return self.__class__(left), self.__class__(middle), self.__class__(right)

    def format(self, *args: object, **kwargs: object) -> Markup:
        """
        Format, escaping each field unless it renders itself through ``__html_format__`` or ``__html__``.

        :param args: positional values for the format fields.
        :param kwargs: keyword values for the format fields.
        :returns: the formatted text as a Markup.
        """
        return self.__class__(EscapeFormatter(self.escape).vformat(self, args, kwargs))

    def format_map(self, mapping: _SupportsStrGetItem, /) -> Markup:
        """Format from a mapping, escaping each field the way :meth:`format` does."""
        return self.__class__(EscapeFormatter(self.escape).vformat(self, (), cast("Mapping[str, object]", mapping)))

    def __html_format__(self, format_spec: str, /) -> Markup:  # noqa: PLW3201  # markupsafe format protocol, not a dunder
        """Render under ``format()``: a Markup ignores an empty spec and rejects any other."""
        if format_spec:
            msg = "Unsupported format specification for Markup."
            raise ValueError(msg)
        return self


class EscapeFormatter(string.Formatter):
    """
    A :class:`string.Formatter` that escapes each field while rendering it.

    :meth:`Markup.format` builds on str formatting but must escape interpolated values. A field that already renders
    itself safely through ``__html_format__`` or ``__html__`` stays trusted instead of escaping again. The class is
    public and subclassable so a template sandbox can mix it with its own formatter, the way Jinja2 does, so its calls
    go through ``super()`` to cooperate with that multiple inheritance.

    :param escape: the function applied to each interpolated field value to make it safe.
    """

    __slots__ = ("escape",)

    def __init__(self, escape: Callable[[object], str]) -> None:
        """Store the escape callable each field passes through."""
        self.escape = escape
        super().__init__()

    def format_field(self, value: object, format_spec: str) -> str:
        """
        Render one field: trust its safe-rendering protocol if present, else escape the formatted value.

        :param value: the field value to render.
        :param format_spec: the field's format specification.
        :returns: the rendered, escaped field text.
        """
        if hasattr(value, "__html_format__"):
            rendered = cast("_HasHtmlFormat", value).__html_format__(format_spec)
        elif hasattr(value, "__html__"):
            if format_spec:
                msg = (
                    f"Format specifier {format_spec} given, but {type(value)} does not define __html_format__. "
                    "A class that defines __html__ must define __html_format__ to work with format specifiers."
                )
                raise ValueError(msg)
            rendered = cast("_HasHtml", value).__html__()  # noqa: PLC2801  # __html__ is a render protocol, not an operator
        else:
            rendered = super().format_field(value, str(format_spec))
        return str(self.escape(rendered))


class _MarkupEscapeHelper:
    """
    Wraps a ``%`` operand and escapes it only when interpolation coerces it.

    :meth:`Markup.__mod__` cannot escape eagerly: ``%d``/``%f`` need the raw number and ``%(k)s`` needs item access
    first. The proxy defers escaping until str formatting coerces the operand, and forwards indexing and coercion.
    """

    def __init__(self, obj: object, escape: Callable[[object], str]) -> None:
        """Store the operand and the escape callable coercion applies to it."""
        self.obj = obj
        self.escape = escape

    def __getitem__(self, key: object) -> _MarkupEscapeHelper:
        """Defer escaping through ``%(name)s`` item access, wrapping the looked-up value in turn."""
        return _MarkupEscapeHelper(cast("_SupportsGetItem", self.obj)[key], self.escape)

    def __str__(self) -> str:
        """Escape the operand at the point ``%s`` coerces it to text."""
        return str(self.escape(self.obj))

    def __repr__(self) -> str:
        """Escape the operand's repr at the point ``%r`` coerces it."""
        return str(self.escape(repr(self.obj)))

    def __int__(self) -> int:
        """Pass the operand through ``%d`` unescaped, since a number carries no markup."""
        return int(cast("SupportsInt", self.obj))

    def __float__(self) -> float:
        """Pass the operand through ``%f`` unescaped, since a number carries no markup."""
        return float(cast("SupportsFloat", self.obj))


_register_markup(Markup)

__all__ = ["EscapeFormatter", "Markup", "escape", "escape_silent", "soft_str"]
