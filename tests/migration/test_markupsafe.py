from __future__ import annotations

import string
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # the migration test runs jinja2 in a clean interpreter; the command is fixed, not input
import sys
from textwrap import dedent
from typing import TYPE_CHECKING, cast

import pytest

from turbohtml.migration.markupsafe import EscapeFormatter, Markup, escape, escape_silent, soft_str

if TYPE_CHECKING:
    from collections.abc import Callable


class Renderable:
    """Renders itself as already-safe HTML through the __html__ protocol."""

    def __html__(self) -> str:
        return "<raw & unescaped>"


class HtmlBoom:
    """__html__ raises, to exercise escape's error-propagation path."""

    def __html__(self) -> str:
        raise ValueError


class Unstringable:
    """Conversion to text raises, to exercise escape's stringification error path."""

    def __str__(self) -> str:
        raise ValueError


class HtmlReturnsUnstringable:
    """__html__ returns a value whose own str() raises, exercising the nested error path."""

    def __html__(self) -> object:
        return Unstringable()


@pytest.fixture
def renderable() -> Renderable:
    return Renderable()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("abcdefgh", "abcdefgh", id="clean-one-block"),
        pytest.param("abcdefghijklmnop", "abcdefghijklmnop", id="clean-two-blocks"),
        pytest.param("&", "&amp;", id="amp"),
        pytest.param("<", "&lt;", id="lt"),
        pytest.param(">", "&gt;", id="gt"),
        pytest.param('"', "&#34;", id="double-quote-numeric"),
        pytest.param("'", "&#39;", id="single-quote-numeric"),
        pytest.param("a&b<c>d", "a&amp;b&lt;c&gt;d", id="mixed-short"),
        pytest.param("<tag>&amp;</tag>!!!", "&lt;tag&gt;&amp;amp;&lt;/tag&gt;!!!", id="special-in-block-with-tail"),
        pytest.param("&bcdefghi", "&amp;bcdefghi", id="special-at-block-start"),
        pytest.param("abcdefg&", "abcdefg&amp;", id="special-at-block-end"),
        pytest.param("café & <b>", "café &amp; &lt;b&gt;", id="ucs2-latin1-supplement"),
        pytest.param("☃ & <b>", "☃ &amp; &lt;b&gt;", id="ucs2-bmp"),
        pytest.param("😀 & <b>", "😀 &amp; &lt;b&gt;", id="ucs4-astral"),
    ],
)
def test_escape_output_matches_markupsafe(text: str, expected: str) -> None:
    result = escape(text)
    assert isinstance(result, Markup)
    assert str(result) == expected


def test_escape_of_none_stringifies() -> None:
    assert str(escape(None)) == "None"


def test_escape_is_idempotent_on_markup() -> None:
    once = escape("<b>")
    assert str(escape(once)) == str(once)


def test_escape_trusts_html_protocol(renderable: Renderable) -> None:
    assert str(escape(renderable)) == "<raw & unescaped>"


def test_escape_stringifies_other_objects() -> None:
    assert str(escape(42)) == "42"
    assert str(escape(["<a>", "&"])) == "[&#39;&lt;a&gt;&#39;, &#39;&amp;&#39;]"


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(HtmlBoom, id="html-raises"),
        pytest.param(HtmlReturnsUnstringable, id="html-result-unstringable"),
        pytest.param(Unstringable, id="str-raises"),
    ],
)
def test_escape_propagates_errors(factory: Callable[[], object]) -> None:
    with pytest.raises(ValueError):  # ruff:ignore[pytest-raises-too-broad]  # the path raises a bare ValueError with no message to match
        escape(factory())


def test_escape_silent_none_is_empty_markup() -> None:
    result = escape_silent(None)
    assert isinstance(result, Markup)
    assert result == Markup()


def test_escape_silent_delegates_for_values() -> None:
    assert str(escape_silent("<b>")) == "&lt;b&gt;"


def test_escape_classmethod_returns_the_calling_subclass() -> None:
    class Sub(Markup):
        """A Markup subclass, to check Markup.escape returns the called class."""

    result = Sub.escape("<b>")
    assert type(result) is Sub
    assert str(result) == "&lt;b&gt;"


@pytest.mark.parametrize(
    ("value", "expected_type", "expected"),
    [
        pytest.param(Markup("<safe>"), Markup, "<safe>", id="markup-passes-through"),
        pytest.param("plain", str, "plain", id="str-passes-through"),
        pytest.param(7, str, "7", id="int-coerced"),
    ],
)
def test_soft_str(value: object, expected_type: type, expected: str) -> None:
    result = soft_str(value)
    assert type(result) is expected_type
    assert result == expected


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        pytest.param("<b>", "<b>", id="trusts-str-without-escaping"),
        pytest.param(Renderable(), "<raw & unescaped>", id="uses-html-protocol"),
        pytest.param(42, "42", id="stringifies-non-str-without-html"),
        pytest.param(b"caf\xc3\xa9", "café", id="decodes-bytes"),
    ],
)
def test_markup_constructor(base: object, expected: str) -> None:
    markup = Markup(base, "utf-8") if isinstance(base, bytes) else Markup(base)
    assert str(markup) == expected


def test_markup_default_is_empty() -> None:
    assert Markup() == Markup("")


def test_add_escapes_right_operand() -> None:
    assert str(Markup("<b>") + "<i>") == "<b>&lt;i&gt;"


def test_radd_escapes_left_operand() -> None:
    assert str("<i>" + Markup("<b>")) == "&lt;i&gt;<b>"


def test_add_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        Markup("<b>") + cast("str", 5)


def test_radd_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        cast("str", 5) + Markup("<b>")


def test_mul_keeps_markup() -> None:
    assert str(Markup("<hr>") * 2) == "<hr><hr>"
    assert str(2 * Markup("<hr>")) == "<hr><hr>"


@pytest.mark.parametrize(
    ("template", "operand", "expected"),
    [
        pytest.param("<a>%s</a>", "<x>", "<a>&lt;x&gt;</a>", id="single"),
        pytest.param("%s & %s", ("<a>", "<b>"), "&lt;a&gt; & &lt;b&gt;", id="tuple"),
        pytest.param("%(k)s", {"k": "<v>"}, "&lt;v&gt;", id="mapping"),
        pytest.param("%d/%f", (3, 2.5), "3/2.500000", id="numeric-passes-raw"),
        pytest.param("%r", "<x>", "&#39;&lt;x&gt;&#39;", id="repr"),
    ],
)
def test_mod_escapes_interpolated_values(template: str, operand: object, expected: str) -> None:
    assert str(Markup(template) % operand) == expected


def test_repr_wraps_the_string() -> None:
    assert repr(Markup("<b>")) == "Markup('<b>')"


def test_join_escapes_parts() -> None:
    assert str(Markup(", ").join(["<a>", "<b>"])) == "&lt;a&gt;, &lt;b&gt;"


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        pytest.param(lambda m: m.capitalize(), "Ab cd", id="capitalize"),
        pytest.param(lambda m: m.title(), "Ab Cd", id="title"),
        pytest.param(lambda m: m.lower(), "ab cd", id="lower"),
        pytest.param(lambda m: m.upper(), "AB CD", id="upper"),
        pytest.param(lambda m: m.swapcase(), "AB cD", id="swapcase"),
        pytest.param(lambda m: m.casefold(), "ab cd", id="casefold"),
    ],
)
def test_case_methods_keep_markup(op: Callable[[Markup], Markup], expected: str) -> None:
    result = op(Markup("ab Cd"))
    assert isinstance(result, Markup)
    assert str(result) == expected


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        pytest.param(lambda m: m.strip(), "ab Cd", id="strip"),
        pytest.param(lambda m: m.lstrip(), "ab Cd  ", id="lstrip"),
        pytest.param(lambda m: m.rstrip(), "  ab Cd", id="rstrip"),
        pytest.param(lambda m: m.expandtabs(2), "  ab Cd  ", id="expandtabs"),
    ],
)
def test_trim_methods_keep_markup(op: Callable[[Markup], Markup], expected: str) -> None:
    result = op(Markup("  ab Cd  "))
    assert isinstance(result, Markup)
    assert str(result) == expected


def test_getitem_keeps_markup() -> None:
    result = Markup("  ab Cd  ")[2:4]
    assert isinstance(result, Markup)
    assert str(result) == "ab"


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        pytest.param(lambda m: m.removeprefix("ab"), "cd", id="removeprefix"),
        pytest.param(lambda m: m.removesuffix("cd"), "ab", id="removesuffix"),
        pytest.param(lambda m: m.zfill(6), "00abcd", id="zfill"),
        pytest.param(lambda m: m.translate({ord("a"): "x"}), "xbcd", id="translate"),
    ],
)
def test_more_wrap_only_methods_keep_markup(op: Callable[[Markup], Markup], expected: str) -> None:
    result = op(Markup("abcd"))
    assert isinstance(result, Markup)
    assert str(result) == expected


def test_split_keeps_markup() -> None:
    parts = Markup("a b c").split()
    assert all(isinstance(part, Markup) for part in parts)
    assert [str(part) for part in parts] == ["a", "b", "c"]


def test_rsplit_keeps_markup() -> None:
    parts = Markup("a b c").rsplit(None, 1)
    assert [str(part) for part in parts] == ["a b", "c"]
    assert all(isinstance(part, Markup) for part in parts)


def test_splitlines_keeps_markup() -> None:
    lines = Markup("a\nb").splitlines()
    assert [str(line) for line in lines] == ["a", "b"]
    assert all(isinstance(line, Markup) for line in lines)


def test_partition_keeps_markup() -> None:
    left, sep, right = Markup("a=b").partition("=")
    assert (str(left), str(sep), str(right)) == ("a", "=", "b")
    assert all(isinstance(part, Markup) for part in (left, sep, right))


def test_rpartition_keeps_markup() -> None:
    left, sep, right = Markup("a=b=c").rpartition("=")
    assert (str(left), str(sep), str(right)) == ("a=b", "=", "c")
    assert all(isinstance(part, Markup) for part in (left, sep, right))


def test_replace_escapes_replacement() -> None:
    result = Markup("a-b").replace("-", "<")
    assert isinstance(result, Markup)
    assert str(result) == "a&lt;b"


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        pytest.param(lambda m: m.ljust(5, "."), "ab...", id="ljust"),
        pytest.param(lambda m: m.rjust(5, "."), "...ab", id="rjust"),
        pytest.param(lambda m: m.center(4, "."), ".ab.", id="center"),
    ],
)
def test_justify_keeps_markup(op: Callable[[Markup], Markup], expected: str) -> None:
    result = op(Markup("ab"))
    assert isinstance(result, Markup)
    assert str(result) == expected


@pytest.mark.parametrize(
    "op",
    [
        pytest.param(lambda m: m.ljust(5, "<"), id="ljust"),
        pytest.param(lambda m: m.rjust(5, "<"), id="rjust"),
        pytest.param(lambda m: m.center(4, "<"), id="center"),
    ],
)
def test_justify_escapes_fill_then_str_rejects_it(op: Callable[[Markup], Markup]) -> None:
    # The fill character is escaped for safety, so a special expands past one character and str rejects it; this
    # matches markupsafe's behavior. CPython and PyPy word the same TypeError differently.
    with pytest.raises(TypeError, match=r"fill character|single character"):
        op(Markup("ab"))


@pytest.mark.parametrize(
    ("template", "field", "expected"),
    [
        pytest.param("<a>{}</a>", "<x>", "<a>&lt;x&gt;</a>", id="plain-field-escaped"),
        pytest.param("{}", Markup("<b>"), "<b>", id="markup-field-trusted"),
        pytest.param("{}", Renderable(), "&lt;raw &amp; unescaped&gt;", id="html-field-rendered-then-escaped"),
        pytest.param("{:>4}", "ab", "  ab", id="plain-field-with-spec"),
    ],
)
def test_format_escapes_fields(template: str, field: object, expected: str) -> None:
    assert str(Markup(template).format(field)) == expected


def test_format_map_escapes_field() -> None:
    assert str(Markup("{k}").format_map({"k": "<x>"})) == "&lt;x&gt;"


def test_format_rejects_spec_on_html_only_object(renderable: Renderable) -> None:
    with pytest.raises(ValueError, match="does not define __html_format__"):
        Markup("{:>5}").format(renderable)


def test_format_rejects_spec_on_markup_field() -> None:
    with pytest.raises(ValueError, match="Unsupported format specification"):
        Markup("{:>5}").format(Markup("<b>"))


def test_unescape_resolves_references() -> None:
    assert Markup("Main &raquo; <em>x</em>").unescape() == "Main » <em>x</em>"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("Main &raquo;\t<em>About</em>", "Main » About", id="drops-tags-and-collapses-whitespace"),
        pytest.param("a <!-- <b> --> c", "a c", id="ignores-tag-inside-comment"),
    ],
)
def test_striptags(markup: str, expected: str) -> None:
    assert Markup(markup).striptags() == expected


def test_escape_formatter_is_public_and_subclassable() -> None:
    seen: list[object] = []

    class Recording(EscapeFormatter):
        """Record each field, to show EscapeFormatter cooperates with subclassing the way Jinja2 needs."""

        def format_field(self, value: object, format_spec: str) -> str:
            seen.append(value)
            return super().format_field(value, format_spec)

    assert issubclass(EscapeFormatter, string.Formatter)
    rendered = Recording(Markup.escape).vformat("<a>{}</a>", ("<x>",), {})
    assert rendered == "<a>&lt;x&gt;</a>"
    assert seen == ["<x>"]


_MIGRATION_SCRIPT = dedent(
    """
    import sys, types
    import turbohtml.migration.markupsafe as tm

    fake = types.ModuleType("markupsafe")
    fake.Markup = tm.Markup
    fake.escape = tm.escape
    fake.escape_silent = tm.escape_silent
    fake.soft_str = tm.soft_str
    fake.EscapeFormatter = tm.EscapeFormatter
    sys.modules["markupsafe"] = fake

    import jinja2

    env = jinja2.Environment(autoescape=True)
    # an untrusted value is escaped once
    assert env.from_string("{{ x }}").render(x="<i>") == "&lt;i&gt;", "plain value not escaped"
    # a Markup value through a str-method filter stays safe and is not escaped a second time
    assert env.from_string("{{ x|upper }}").render(x=tm.Markup("<b>hi</b>")) == "<B>HI</B>", "double-escaped"
    # escaping filters still escape untrusted operands
    assert env.from_string("{{ x|replace('o', '0') }}").render(x="f<o>o") == "f&lt;0&gt;0", "replace lost escaping"
    print("MIGRATION_OK")
    """
)


def test_jinja2_migrates_to_turbohtml_markup() -> None:
    # Run in a clean interpreter so markupsafe can be swapped for turbohtml.migration.markupsafe before jinja2
    # imports it; this proves a jinja2-based project migrates by changing only the import.
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv (this interpreter + a literal script), no external input
        [sys.executable, "-c", _MIGRATION_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "MIGRATION_OK" in result.stdout
