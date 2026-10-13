"""Reference-shaped consumers must agree with their compiled public entry points."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, cast, get_args

import turbohtml
from turbohtml import Canonical, HTMLParseError, Markdown, ParseError, PlainText, annotation_surface, annotation_tags
from turbohtml.__main__ import main
from turbohtml.conformance import ConformanceMessage, ConformanceReport, Severity, check, check_html
from turbohtml.convert import (
    ExpressionError,
    GenericTranslator,
    HTMLTranslator,
    SelectorError,
    css_specificity,
    css_to_xpath,
)
from turbohtml.cssom import ComputedStyle, RuleList, StyleDeclaration, StyleRule, StyleSheet, computed_style
from turbohtml.transform import Transform, strparam, transform
from turbohtml.validate import RelaxNG, SchemaValidationError, ValidationError, ValidationResult, XMLSchema

from .atheris_invariants import assert_invariant
from .atheris_registry import Target
from .round_trip_oracles import selector_entry_check, style_check, xml_check

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "cli_observation",
    "computed_observation",
    "conformance_observation",
    "cssom_observation",
    "reference_targets",
    "render_observation",
    "schema_observation",
    "selector_observation",
    "transform_observation",
]


_SELECTOR_DOCUMENT: Final = '<div id="a"><p class="x">one</p><p>two</p></div>'


def reference_targets() -> tuple[Target, ...]:
    """Keep each grammar and its documented rejection set independent."""
    return (
        Target(
            "html-render",
            _render,
            (
                "turbohtml.Canonical",
                "turbohtml.Markdown",
                "turbohtml.PlainText",
                "turbohtml.annotation_surface",
                "turbohtml.annotation_tags",
                "turbohtml.escape",
                "turbohtml.unescape",
                "turbohtml.__version__",
            ),
            (UnicodeDecodeError,),
        ),
        Target(
            "html-conformance",
            _conformance,
            (
                "turbohtml.conformance.ConformanceMessage",
                "turbohtml.conformance.ConformanceReport",
                "turbohtml.conformance.Severity",
                "turbohtml.conformance.check",
                "turbohtml.conformance.check_html",
                "turbohtml.HTMLParseError",
                "turbohtml.ParseError",
            ),
            (UnicodeDecodeError,),
        ),
        Target(
            "css-translate",
            _selector,
            (
                "turbohtml.convert.ExpressionError",
                "turbohtml.convert.GenericTranslator",
                "turbohtml.convert.HTMLTranslator",
                "turbohtml.convert.SelectorError",
                "turbohtml.convert.SelectorSyntaxError",
                "turbohtml.convert.css_specificity",
                "turbohtml.convert.css_to_xpath",
                "turbohtml.SelectorSyntaxError",
            ),
            (UnicodeDecodeError, turbohtml.SelectorSyntaxError, ExpressionError),
        ),
        Target(
            "css-object-model",
            _cssom,
            (
                "turbohtml.cssom.ComputedStyle",
                "turbohtml.cssom.RuleList",
                "turbohtml.cssom.StyleDeclaration",
                "turbohtml.cssom.StyleRule",
                "turbohtml.cssom.StyleSheet",
                "turbohtml.cssom.computed_style",
            ),
            (UnicodeDecodeError,),
        ),
        Target(
            "xml-schema",
            _schema,
            (
                "turbohtml.parse_xml",
                "turbohtml.validate.RelaxNG",
                "turbohtml.validate.SchemaValidationError",
                "turbohtml.validate.ValidationError",
                "turbohtml.validate.ValidationResult",
                "turbohtml.validate.XMLSchema",
            ),
            (UnicodeDecodeError, HTMLParseError),
        ),
        Target(
            "xml-transform",
            _transform,
            (
                "turbohtml.transform.Transform",
                "turbohtml.transform.strparam",
                "turbohtml.transform.transform",
            ),
            (UnicodeDecodeError,),
        ),
        Target("html-cli", _cli, ("turbohtml.__main__.main",), (UnicodeDecodeError,)),
    )


def _render(data: bytes) -> None:
    render_observation(data)


def _conformance(data: bytes) -> None:
    conformance_observation(data)


def _selector(data: bytes) -> None:
    selector_observation(data)


def _cssom(data: bytes) -> None:
    cssom_observation(data)


def _schema(data: bytes) -> None:
    schema_observation(data)


def _transform(data: bytes) -> None:
    transform_observation(data)


def _cli(data: bytes) -> None:
    cli_observation(data)


def render_observation(data: bytes) -> tuple[str, str, str, bytes]:
    """Escaping text must preserve it through a parsed element."""
    source: Final = data.decode("utf-8")
    document: Final = turbohtml.parse(source)
    _require("Escape round trip differs", condition=turbohtml.unescape(turbohtml.escape(source)) == source)
    _require("Version result", condition=isinstance(turbohtml.__version__, str) and bool(turbohtml.__version__))
    plain: Final = document.to_text(PlainText())
    annotated, spans = document.to_annotated_text({"a": ("link",)}, PlainText())
    _require("Annotated text differs", condition=annotated == plain)
    surface: Final = annotation_surface(annotated, spans)
    _require(
        "Annotation values", condition=all(isinstance(value, str) for values in surface.values() for value in values)
    )
    return (
        document.to_markdown(Markdown()),
        plain,
        annotation_tags(annotated, spans),
        document.canonicalize(Canonical()),
    )


def conformance_observation(data: bytes) -> tuple[bool, tuple[str, ...], tuple[str, int, int] | None]:
    """Strict parse diagnostics and conformance reports use separate records."""
    source: Final = data.decode("utf-8")
    report: Final = check(turbohtml.parse(source))
    _require("Conformance entry points differ", condition=report == check_html(source))
    _require("Conformance result type", condition=isinstance(report, ConformanceReport))
    _require("Conformance verdict differs", condition=bool(report) == (not report.errors))
    _require(
        "Severity views differ",
        condition=sorted(report.errors + report.warnings + report.infos) == sorted(report.messages),
    )
    _require(
        "Conformance record fields",
        condition=all(
            isinstance(message, ConformanceMessage) and message.severity in get_args(Severity)
            for message in report.messages
        ),
    )
    try:
        turbohtml.parse(source, strict=True)
    except HTMLParseError as error:
        _require("Parse error record", condition=isinstance(error.error, ParseError))
        diagnostic = (error.error.code, error.error.line, error.error.col)
    else:
        diagnostic = None
    return report.valid, tuple(message.code for message in report.messages), diagnostic


def selector_observation(
    data: bytes, entries: Callable[[str], str | None] = selector_entry_check
) -> tuple[str, tuple[tuple[int, int, int], ...]]:
    """Both translator wrappers must retain selector specificity and node selection."""
    selector: Final = data.decode("utf-8")
    assert_invariant(entries, json.dumps({"html": _SELECTOR_DOCUMENT, "css": selector}))
    try:
        expression: Final = css_to_xpath(selector)
    except ExpressionError as error:
        _require("Expression error ancestry", condition=isinstance(error, SelectorError))
        raise
    _require("Generic translator differs", condition=GenericTranslator().css_to_xpath(selector) == expression)
    _require("HTML translator differs", condition=HTMLTranslator().css_to_xpath(selector) == expression)
    document: Final = turbohtml.parse(_SELECTOR_DOCUMENT)
    _require(
        "Selector translation differs",
        condition=tuple(node.serialize() for node in document.select(selector))
        == tuple(node.serialize() for node in cast("list[turbohtml.Node]", document.xpath(expression))),
    )
    return expression, tuple(css_specificity(selector))


def cssom_observation(
    data: bytes, style: Callable[[str], str | None] = style_check
) -> tuple[tuple[str, str, bool], ...]:
    """Preserve declaration records through rule attachment and a ``StyleDeclaration.text`` re-read."""
    source: Final = data.decode("utf-8")
    assert_invariant(style, source)
    declaration: Final = StyleDeclaration.parse(source)
    observed: Final = tuple((name, declaration[name], declaration.important(name)) for name in declaration)
    _require("Property order differs", condition=declaration.properties() == tuple(name for name, _, _ in observed))
    _require("Declaration length differs", condition=len(declaration) == len(observed))
    _require(
        "Declaration lookup differs",
        condition=all(name in declaration and declaration.get(name) == value for name, value, _ in observed),
    )
    rebuilt: Final = StyleDeclaration(observed)
    _require(
        "Declaration reconstruction differs",
        condition=tuple((name, rebuilt[name], rebuilt.important(name)) for name in rebuilt) == observed,
    )
    rule: Final = StyleRule("p", declaration)
    rules: Final = RuleList((rule,))
    _require("Rule wrapper differs", condition=tuple(rules) == (rules[0],) and rules[0].style is declaration)
    if (close := _rule_close(declaration.text)) is not None:
        sheet: Final = StyleSheet("p { " + declaration.text + close)
        _require(
            "Sheet declaration differs", condition=tuple(item.style.text for item in sheet.rules) == (declaration.text,)
        )
    computed_observation(data)
    return observed


def _rule_close(text: str) -> str | None:
    """
    Return what closes ``p { <text>`` so the rule body holds text as written, or None when no closer can.

    A rule body reads its declarations nested, so a top-level ``}`` ends the rule where a style attribute keeps it as a
    value (CSS Syntax 3 §5.5.7). A block, string, comment or escape still open at the end of text runs to the end of
    input (§5.5.8, §4.3.5, §4.3.2, §4.3.7) and would swallow a written ``}``, so the rule ends at end of input as well.
    """
    closers: list[str] = []
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char in "([{":
            closers.append(")]}"["([{".index(char)])
        elif closers and char == closers[-1]:
            closers.pop()
        elif not closers and char == "}":
            return None
        elif (pos := _opaque_end(text, pos)) < 0:
            return ""
        pos += 1
    return "" if closers else " }"


def _opaque_end(text: str, pos: int) -> int:
    """Return the last offset of the escape, comment or string at pos, pos for any other code point, or -1 if open."""
    if text[pos] == "\\":
        return pos + 1 if pos + 1 < len(text) else -1
    if text.startswith("/*", pos):
        return end + 1 if (end := text.find("*/", pos + 2)) >= 0 else -1
    if (quote := text[pos]) in "\"'":
        pos += 1
        while pos < len(text) and text[pos] not in {quote, "\n"}:
            pos += 2 if text[pos] == "\\" else 1
        return pos if pos < len(text) else -1
    return pos


def computed_observation(data: bytes) -> str:
    """Attach the declarations to the element whose cascade consumes them."""
    declaration: Final = StyleDeclaration.parse(data.decode("utf-8"))
    document: Final = turbohtml.parse("<style></style><p>x</p>")
    document.select("style")[0].set_text("p { color: black; " + declaration.text + " }")
    computed: Final = computed_style(document.select("p")[0])
    _require("Computed result type", condition=isinstance(computed, ComputedStyle))
    _require(
        "Computed lookup differs",
        condition=all(name in computed and computed.get(name) == computed[name] for name in computed),
    )
    _require(
        "Computed reconstruction differs",
        condition=ComputedStyle(tuple((name, computed[name]) for name in computed)).properties()
        == computed.properties(),
    )
    _require("Computed color missing", condition=computed.get("color") == computed["color"])
    return computed["color"]


def schema_observation(data: bytes, xml: Callable[[str], str | None] = xml_check) -> tuple[bool, bool]:
    """Compiled schema verdicts and assertion errors must describe the same instance, and XML output must re-read."""
    source: Final = data.decode("utf-8")
    assert_invariant(xml, source)
    document: Final = turbohtml.parse_xml(source)
    schemas: Final = (
        XMLSchema(
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            '<xs:element name="root" type="xs:string"/></xs:schema>'
        ),
        RelaxNG('<element xmlns="http://relaxng.org/ns/structure/1.0" name="root"><text/></element>'),
    )
    verdicts: list[bool] = []
    for schema in schemas:
        result: Final = schema.validate(document)
        _require("Validation result type", condition=isinstance(result, ValidationResult))
        _require("Validation verdict differs", condition=bool(result) == schema.is_valid(document))
        _require(
            "Validation error records",
            condition=all(isinstance(error, ValidationError) and bool(error.message) for error in result.errors),
        )
        try:
            schema.assert_valid(document)
        except SchemaValidationError as error:
            _require("Validation assertion differs", condition=not result.valid and error.errors == result.errors)
        else:
            _require("Validation assertion accepted invalid instance", condition=result.valid)
        verdicts.append(result.valid)
    return verdicts[0], verdicts[1]


def transform_observation(data: bytes) -> str:
    """Parameters must remain string values even when they contain XPath quotes."""
    source: Final = data.decode("utf-8")
    stylesheet: Final = turbohtml.parse_xml(
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">'
        '<xsl:output method="text"/><xsl:param name="value"/><xsl:template match="/">'
        '<xsl:value-of select="$value"/></xsl:template></xsl:stylesheet>'
    )
    document: Final = turbohtml.parse_xml("<root/>")
    quoted: Final = strparam(source)
    observed: Final = Transform(stylesheet, allow_imports=False)(document, value=quoted)
    _require("Transform parameter differs", condition=observed == source)
    _require(
        "Transform entry points differ",
        condition=transform(stylesheet, document, allow_imports=False, value=quoted) == observed,
    )
    return observed


def cli_observation(data: bytes) -> str:
    """File-based CLI output must match the library conversion."""
    source: Final = data.decode("utf-8")
    with TemporaryDirectory(prefix="turbohtml-atheris-") as directory:
        input_file: Final = Path(directory) / "input.html"
        output_file: Final = Path(directory) / "output.txt"
        input_file.write_text(source, encoding="utf-8")
        _require("CLI exit status", condition=main(("to-text", str(input_file), "-o", str(output_file))) == 0)
        observed: Final = output_file.read_text(encoding="utf-8")
    _require("CLI result differs", condition=observed == turbohtml.parse(source).to_text())
    return observed


def _require(message: str, *, condition: bool) -> None:
    if not condition:
        raise AssertionError(message)
