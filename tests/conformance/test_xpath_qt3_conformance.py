"""XPath functions validated against the W3C QT3 (XQuery/XPath 3.1) conformance suite.

``tests/conformance/qt3tests`` (pinned submodule) is the W3C's own oracle for the fn:* functions:
a catalog of ``.xml`` test sets, each case pairing a query with an asserted result or error code.
This module runs every case turbohtml's XPath engine can express -- the XPath 1.0/2.0 string,
numeric, and boolean functions behind feature #558 -- against the case's expected result, and records
the families turbohtml deliberately omits (``skip`` for unsupported grammar, ``xfail`` for the typed
error-code taxonomy) with a per-case, spec-justified reason so the signal stays on what turbohtml
claims to support.

Excluded families (each carries its reason in the parametrize id): XQuery-only tests, ``xs:`` schema
constructors and casts, XPath 2.0 sequences/ranges/value-comparisons, XPath 3.x maps/arrays/arrows/
higher-order functions, XSD-regex dialect features (turbohtml's ``fn:matches``/``fn:replace`` run on
Python's ``re``), the typed error-code taxonomy (turbohtml raises Python exceptions), and XML
namespace decomposition (turbohtml is HTML-first: ``local-name`` == ``name``, ``namespace-uri`` empty
outside SVG/MathML).

The suite is a pure data submodule with no runtime oracle, so its absence is an error, not a skip:
the module raises at import when ``qt3tests`` is not checked out. It runs in the dedicated ``🔬
conformance`` CI job (which inits the submodule) and is deselected from the normal matrix via ``tox
--ignore``; it is omitted from the coverage gate (see ``[tool.coverage]``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import isinf, isnan
from pathlib import Path
from typing import TYPE_CHECKING, cast
from xml.etree import ElementTree as ET  # noqa: S405  # trusted pinned QT3 fixture

import pytest

import turbohtml

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.mark.structures import ParameterSet

QT3 = "{http://www.w3.org/2010/09/qt-fots-catalog}"
ROOT = Path(__file__).parent / "qt3tests"
CATALOG = ROOT / "catalog.xml"

if not CATALOG.exists():
    msg = (
        "submodule tests/conformance/qt3tests not checked out; "
        "run: git submodule update --init tests/conformance/qt3tests"
    )
    raise RuntimeError(msg)  # pragma: no cover

SUPPORTED_FUNCTIONS = frozenset({
    "last",
    "position",
    "count",
    "id",
    "local-name",
    "namespace-uri",
    "name",
    "string",
    "concat",
    "starts-with",
    "contains",
    "substring-before",
    "substring-after",
    "substring",
    "string-length",
    "normalize-space",
    "translate",
    "ends-with",
    "string-join",
    "lower-case",
    "upper-case",
    "matches",
    "replace",
    "boolean",
    "not",
    "true",
    "false",
    "lang",
    "number",
    "sum",
    "floor",
    "ceiling",
    "round",
})
NODE_TESTS = frozenset({"text", "node", "comment", "processing-instruction"})

FAMILIES = (
    "ends-with",
    "starts-with",
    "contains",
    "substring",
    "substring-before",
    "substring-after",
    "string-join",
    "lower-case",
    "upper-case",
    "matches",
    "replace",
    "normalize-space",
    "translate",
    "concat",
    "string-length",
    "string",
    "boolean",
    "not",
    "true",
    "false",
    "number",
    "floor",
    "ceiling",
    "round",
    "count",
    "sum",
    "name",
    "local-name",
    "namespace-uri",
    "lang",
    "position",
    "last",
)

NAMESPACED_ENVIRONMENTS = frozenset({"atomic", "atomic-xq"})
UNSUPPORTED_FEATURES = frozenset({
    "schemaValidation",
    "schemaImport",
    "staticTyping",
    "higherOrderFunctions",
    "moduleImport",
    "typedData",
    "advanced-uca-fallback",
    "non_unicode_codepoint_collation",
    "namespace-axis",
})

RAW_MARKERS = (
    (re.compile(r"collation"), "collation URI (XPath 2.0 collations)"),
    (re.compile(r"&#"), "XQuery character reference in string literal"),
)
GRAMMAR_MARKERS = (
    (re.compile(r"\bxs:"), "xs: schema type constructor/cast (schema-aware)"),
    (re.compile(r"\*:|:\*"), "namespace wildcard (XPath 2.0)"),
    (re.compile(r"\b(array|map|math|err):"), "XPath 3.x namespaced function"),
    (re.compile(r"\bQ\{"), "EQName braced-URI literal (3.0)"),
    (re.compile(r"declare\s+(namespace|function|variable|option)"), "XQuery prolog"),
    (re.compile(r"(^|[^\w$])(for|let|some|every)\s+\$"), "FLWOR/quantified expression"),
    (re.compile(r"\breturn\b|\bsatisfies\b"), "FLWOR return/satisfies"),
    (re.compile(r"\bcastable\b|\bcast\s+as\b|\btreat\s+as\b|\binstance\s+of\b"), "cast/treat/instance-of (2.0)"),
    (re.compile(r"=>"), "arrow operator (3.1)"),
    (re.compile(r"function\s*\("), "inline function / higher-order (3.0)"),
    (
        re.compile(r"\b(element|attribute|processing-instruction|namespace|document|text|comment)\s+[\w:]*\s*\{"),
        "computed node constructor (XQuery)",
    ),
    (
        re.compile(r"\b(element|attribute|document-node|schema-element|schema-attribute)\s*\("),
        "kind test / sequence type (2.0)",
    ),
    (re.compile(r"<[!?/a-zA-Z]"), "direct node constructor (XQuery)"),
    (re.compile(r"\b(eq|ne|lt|le|gt|ge)\b"), "value comparison operator (2.0)"),
    (re.compile(r"\bto\b"), "range expression (2.0)"),
    (re.compile(r"\d(\.\d+)?[eE][+-]?\d"), "double literal (2.0)"),
    (re.compile(r"\|\||&&"), "non-XPath boolean operator"),
)

# fn:matches / fn:replace run on Python's re, so these XSD-regex-only constructs are out of scope.
RUNTIME_REGEX_ERRORS = (
    "bad escape",
    "bad character range",
    "invalid group reference",
    "repetition number is too large",
    "unterminated",
    "redefinition of group",
    "cannot refer to an open group",
    "global flags not at the start",
)


def strip_function_prefix(query: str) -> str:
    return re.sub(r"\bfn:", "", query)


def blank_string_literals(query: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(query):
        char = query[index]
        if char in "'\"":
            index += 1
            while index < len(query) and query[index] != char:
                index += 1
            index += 1
            out.append(" ")
        else:
            out.append(char)
            index += 1
    return "".join(out)


def has_sequence(query: str) -> bool:
    """True when the query builds a sequence/array/empty-sequence literal (all XPath 2.0+)."""
    outside = blank_string_literals(query)
    is_call: list[bool] = []
    previous = ""
    for char in outside:
        if char == "(":
            is_call.append(bool(previous) and (previous.isalnum() or previous in {")", "]", "_", "-"}))
        elif char == ")":
            if is_call:
                is_call.pop()
        elif char == "," and is_call and not is_call[-1]:
            return True
        if not char.isspace():
            previous = char
    return bool(re.search(r"\(\s*\)", outside)) or bool(re.search(r"\[[^\]]*,[^\]]*\]", outside))


def called_functions(query: str) -> set[str]:
    return set(re.findall(r"([a-zA-Z][\w-]*)\s*\(", blank_string_literals(query)))


def string_literals(query: str) -> list[str]:
    return [double or single for double, single in re.findall(r'"([^"]*)"|\'([^\']*)\'', query)]


def regex_dialect_reason(query: str) -> str | None:
    joined = " ".join(string_literals(query))
    if re.search(r"\\[pPicIC]", joined):
        return r"XSD \p/\P/\i/\c regex escape (Python-re backend)"
    if re.search(r"-\[", joined):
        return "XSD character-class subtraction (Python-re backend)"
    if re.search(r"\\ ", joined):
        return "XSD x-flag whitespace escape (Python-re backend)"
    for literal in string_literals(query):
        if 0 < len(literal) <= 4 and all(char in "imsxq " for char in literal) and ("q" in literal or " " in literal):
            return "XSD/3.0 regex flag q or whitespace (Python-re backend)"
    return None


@dataclass(frozen=True)
class Environment:
    source: str | None
    source_count: int


def load_environments() -> dict[str, Environment]:
    catalog = ET.parse(CATALOG).getroot()  # noqa: S314  # trusted pinned QT3 fixture
    environments: dict[str, Environment] = {}
    for element in catalog.findall(f"{QT3}environment"):
        sources = element.findall(f"{QT3}source")
        context = [source for source in sources if source.get("role") == "."]
        environments[element.get("name", "")] = Environment(
            source=context[0].get("file") if context else None,
            source_count=len(sources),
        )
    return environments


ENVIRONMENTS = load_environments()


def dependency_reason(case: ET.Element) -> str | None:
    for dependency in case.findall(f"{QT3}dependency"):
        kind = dependency.get("type")
        value = dependency.get("value") or ""
        if kind == "spec":
            tokens = value.split()
            if tokens and not any(token.startswith("XP") for token in tokens):
                return f"XQuery-only test (spec: {value})"
        elif kind == "feature" and dependency.get("satisfied") != "false" and value in UNSUPPORTED_FEATURES:
            return f"requires feature: {value}"
    return None


CONTEXTS: dict[str | None, turbohtml.Document] = {}


def context_for(env: str | None) -> turbohtml.Document:
    if env not in CONTEXTS:
        if env in {None, "empty"}:
            CONTEXTS[env] = turbohtml.parse_xml("<empty/>")
        else:
            assert env is not None
            source = ENVIRONMENTS[env].source
            assert source is not None  # single-source guard already applied by environment_reason
            CONTEXTS[env] = turbohtml.parse_xml((ROOT / source).read_text(encoding="utf-8"))
    return CONTEXTS[env]


def environment_reason(env: str | None) -> str | None:
    if env in NAMESPACED_ENVIRONMENTS:
        return "namespaced source document (turbohtml is HTML-first: local-name == name, namespace-uri empty)"
    if env in {None, "empty"}:
        return None
    info = ENVIRONMENTS.get(env)
    if info is None or info.source is None or info.source_count != 1:
        return f"environment {env!r} is not a single-source document"
    try:
        context_for(env)
    except turbohtml.HTMLParseError as error:
        return f"environment {env!r} is not parseable by turbohtml.parse_xml ({error})"
    return None


def exclusion_reason(family: str, case: ET.Element, query: str) -> str | None:  # noqa: PLR0911  # one return per out-of-scope marker reads clearest
    """Return why a supported-family case is out of turbohtml's XPath scope, or None if in scope."""
    reason = dependency_reason(case)
    if reason is not None:
        return reason
    for pattern, marker in RAW_MARKERS:
        if pattern.search(query):
            return marker
    outside = blank_string_literals(query)
    for pattern, marker in GRAMMAR_MARKERS:
        if pattern.search(outside):
            return marker
    if has_sequence(query):
        return "sequence/array/empty-sequence literal (2.0)"
    unknown = called_functions(query) - SUPPORTED_FUNCTIONS - NODE_TESTS
    if unknown:
        return "function not implemented: " + ", ".join(sorted(unknown))
    if family in {"matches", "replace"}:
        return regex_dialect_reason(query)
    return None


CONSTANT_ASSERTIONS: dict[str, tuple[str, object]] = {
    "assert-true": ("bool", True),
    "assert-false": ("bool", False),
    "assert-empty": ("empty", None),
}
UNMODELED_ASSERTIONS = frozenset({
    "assert-deep-eq",
    "assert",
    "assert-count",
    "assert-xml",
    "assert-permutation",
    "assert-type",
})


def expected_result(result: ET.Element) -> tuple[str, object]:  # noqa: PLR0911  # one return per QT3 assertion tag reads clearest
    for child in result:
        tag = child.tag.removeprefix(QT3)
        if tag in CONSTANT_ASSERTIONS:
            return CONSTANT_ASSERTIONS[tag]
        if tag == "assert-eq":
            return ("eq", (child.text or "").strip())
        if tag == "assert-string-value":
            return ("str", child.text or "")
        if tag == "error":
            return ("error", child.get("code"))
        if tag == "all-of":
            return expected_result(child)
        if tag == "any-of":
            return ("any-of", [expected_result(option) for option in child])
        if tag in UNMODELED_ASSERTIONS:
            return ("unmodeled", tag)
    return ("unmodeled", "no-assertion")


@dataclass(frozen=True)
class Case:
    case_id: str
    query: str
    environment: str | None
    expected: tuple[str, object]


def xpath_number_string(value: float) -> str:
    if isnan(value):
        return "NaN"
    if isinf(value):
        return "INF" if value > 0 else "-INF"
    return str(int(value)) if value == int(value) else repr(value)


def string_value(result: object) -> str | None:
    if isinstance(result, bool):
        return "true" if result else "false"
    if isinstance(result, float):
        return xpath_number_string(result)
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        if not result:
            return ""
        if len(result) == 1 and isinstance(result[0], str):
            return result[0]
    return None


def matches_expected(expected: tuple[str, object], result: object) -> bool:  # noqa: PLR0911  # one return per expected-result kind reads clearest
    kind, value = expected
    if kind == "bool":
        return isinstance(result, bool) and result == value
    if kind == "str":
        return string_value(result) == value
    if kind == "empty":
        return isinstance(result, (str, list)) and not result
    if kind == "any-of":
        return any(matches_expected(option, result) for option in cast("list[tuple[str, object]]", value))
    if kind == "eq":
        assert isinstance(value, str)
        if isinstance(result, bool):
            return value.lower() in (("true", "true()") if result else ("false", "false()"))
        if isinstance(result, float):
            try:
                return float(value) == result  # noqa: RUF069  # exact equality is the QT3 oracle's contract
            except ValueError:
                return False
        if isinstance(result, str):
            return result == value or result == value.strip("\"'")
    return False


def collect() -> Iterator[ParameterSet]:
    for family in FAMILIES:
        catalog = ET.parse(ROOT / "fn" / f"{family}.xml").getroot()  # noqa: S314  # trusted pinned QT3 fixture
        for element in catalog.findall(f"{QT3}test-case"):
            case_id = f"{family}::{element.get('name')}"
            reference = element.find(f"{QT3}environment")
            env = reference.get("ref") if reference is not None else None
            raw_query = (element.findtext(f"{QT3}test") or "").strip()
            result = element.find(f"{QT3}result")
            assert result is not None  # every QT3 test case carries a result element
            expected = expected_result(result)
            case = Case(case_id, strip_function_prefix(raw_query), env, expected)

            reason = environment_reason(env) or exclusion_reason(family, element, strip_function_prefix(raw_query))
            if reason is not None:
                # skip, not xfail(run=False): both never run the body, but xfail(run=False) is a pytest
                # performance pathology (~40 ms/item) that would add minutes over the thousands of excluded cases.
                yield pytest.param(case, id=case_id, marks=pytest.mark.skip(reason=reason))
            elif expected[0] == "unmodeled":
                yield pytest.param(
                    case, id=case_id, marks=pytest.mark.skip(reason=f"assertion form not modeled: {expected[1]}")
                )
            elif expected[0] == "error":
                yield pytest.param(
                    case,
                    id=case_id,
                    marks=pytest.mark.xfail(
                        reason="turbohtml raises Python exceptions, not the typed XPath error-code taxonomy "
                        "(xpass = it still rejects the input)",
                        strict=False,
                    ),
                )
            else:
                yield pytest.param(case, id=case_id)


@pytest.mark.parametrize("case", list(collect()))
def test_qt3_fn(case: Case) -> None:
    context = context_for(case.environment)
    if case.expected[0] == "error":
        with pytest.raises(Exception):  # noqa: B017, PT011  # QT3 asserts an error code; turbohtml maps all to exceptions
            context.xpath(case.query)
        return
    try:
        result = context.xpath(case.query)
    except Exception as error:  # dispatch known XPath-1.0/Python-re deviations, re-raise the rest
        message = str(error)
        if "non-node-set" in message:
            pytest.xfail("XPath-1.0 count/sum/string-length restricted to node-sets, not atomic sequences")
        if "takes " in message and "argument" in message:
            pytest.xfail("function arity beyond XPath 1.0/2.0 (e.g. the 2-argument round is XPath 3.0)")
        if any(marker in message for marker in RUNTIME_REGEX_ERRORS):
            pytest.xfail("fn:matches/fn:replace run on Python re, not the XSD regex dialect")
        raise
    assert matches_expected(case.expected, result), f"{case.query!r} -> {result!r}, expected {case.expected}"
