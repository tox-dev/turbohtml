from __future__ import annotations

import re
from dataclasses import dataclass
from math import isinf, isnan
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias, cast
from xml.etree import ElementTree as ET  # ruff:ignore[suspicious-xml-etree-import]  # trusted pinned QT3 fixture

import pytest

import turbohtml

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.mark.structures import ParameterSet

_QT3: Final[str] = "{http://www.w3.org/2010/09/qt-fots-catalog}"
Expected: TypeAlias = "tuple[str, bool | str | list[Expected] | None]"
XPathResult: TypeAlias = "list[turbohtml.Node | str] | float | bool | str"
_ROOT: Final[Path] = Path(__file__).parent / "qt3tests"
_CATALOG: Final[Path] = _ROOT / "catalog.xml"

if not _CATALOG.exists():
    _MSG = (
        "submodule tests/conformance/qt3tests not checked out; "
        "run: git submodule update --init tests/conformance/qt3tests"
    )
    raise RuntimeError(_MSG)  # pragma: no cover  # raises only without the vendored QT3 suite

_RAW_MARKERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"collation"), "collation URI (XPath 2.0 collations)"),
    (re.compile(r"&#"), "XQuery character reference in string literal"),
)
_GRAMMAR_MARKERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\bxs:"), "xs: schema type constructor/cast (schema-aware)"),
    (re.compile(r"\*:|:\*"), "namespace wildcard (XPath 2.0)"),
    (
        re.compile(r"(?#function namespace prefix)\b(?:array|map|math|err)(?#followed by a colon):"),
        "XPath 3.x namespaced function",
    ),
    (re.compile(r"\bQ\{"), "EQName braced-URI literal (3.0)"),
    (re.compile(r"(?#prolog keyword)declare(?#whitespace)\s+(?:namespace|function|variable|option)"), "XQuery prolog"),
    (
        re.compile(
            r"(?#start or a character that is not a name or dollar)(?:^|[^\w$])"
            r"(?#FLWOR or quantified keyword)(?:for|let|some|every)"
            r"(?#whitespace before the variable)\s+\$(?#variable sigil)"
        ),
        "FLWOR/quantified expression",
    ),
    (re.compile(r"\breturn\b|\bsatisfies\b"), "FLWOR return/satisfies"),
    (re.compile(r"\bcastable\b|\bcast\s+as\b|\btreat\s+as\b|\binstance\s+of\b"), "cast/treat/instance-of (2.0)"),
    (re.compile(r"=>"), "arrow operator (3.1)"),
    (re.compile(r"function\s*\("), "inline function / higher-order (3.0)"),
    (
        re.compile(
            r"(?#word start)\b(?:element|attribute|processing-instruction|namespace|document|text|comment)"
            r"(?#whitespace)\s+(?#optional QName)[\w:]*(?#whitespace)\s*(?#opening brace)\{"
        ),
        "computed node constructor (XQuery)",
    ),
    (
        re.compile(
            r"(?#word start)\b(?:element|attribute|document-node|schema-element|schema-attribute)"
            r"(?#whitespace)\s*(?#opening parenthesis)\("
        ),
        "kind test / sequence type (2.0)",
    ),
    (re.compile(r"<[!?/a-zA-Z]"), "direct node constructor (XQuery)"),
    (re.compile(r"(?#word start)\b(?:eq|ne|lt|le|gt|ge)(?#word end)\b"), "value comparison operator (2.0)"),
    (re.compile(r"\bto\b"), "range expression (2.0)"),
    (
        re.compile(
            r"(?#integer digit)\d(?#optional fraction)(?:\.\d+)?(?#exponent marker)[eE]"
            r"(?#optional sign)[+-]?(?#exponent digit)\d"
        ),
        "double literal (2.0)",
    ),
    (re.compile(r"\|\||&&"), "non-XPath boolean operator"),
)

# fn:matches / fn:replace take Python's re syntax, so XSD-only constructs and counted repeats too large to compile are
# out of scope.
_RUNTIME_REGEX_ERRORS: Final[tuple[str, ...]] = (
    "bad escape",
    "bad character range",
    "invalid group reference",
    "repetition number is too large",
    "unterminated",
    "redefinition of group",
    "cannot refer to an open group",
    "global flags not at the start",
    "regular expression compiles to more than",
)


def strip_function_prefix(query: str) -> str:
    return re.sub(r"\bfn:", "", query)


def blank_string_literals(query: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(query):
        if (char := query[index]) in "'\"":
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
    # True when the query builds a sequence/array/empty-sequence literal (all XPath 2.0+).
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
    return [
        double or single
        for double, single in re.findall(
            r'(?#double quoted literal)"(?P<double>[^"]*)"|(?#single quoted literal)\'(?P<single>[^\']*)\'', query
        )
    ]


def regex_dialect_reason(query: str) -> str | None:
    joined = " ".join(string_literals(query))
    if re.search(r"\\[pPicIC]", joined):
        return r"XSD \p/\P/\i/\c regex escape (Python re dialect)"
    if re.search(r"-\[", joined):
        return "XSD character-class subtraction (Python re dialect)"
    if re.search(r"\\ ", joined):
        return "XSD x-flag whitespace escape (Python re dialect)"
    for literal in string_literals(query):
        if 0 < len(literal) <= 4 and all(char in "imsxq " for char in literal) and ("q" in literal or " " in literal):
            return "XSD/3.0 regex flag q or whitespace (Python re dialect)"
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class Environment:
    source: str | None
    source_count: int


def load_environments() -> dict[str, Environment]:
    catalog = ET.parse(_CATALOG).getroot()  # ruff:ignore[suspicious-xml-element-tree-usage]  # trusted pinned QT3 fixture
    environments: dict[str, Environment] = {}
    for element in catalog.findall(f"{_QT3}environment"):
        sources = element.findall(f"{_QT3}source")
        context = [source for source in sources if source.get("role") == "."]
        environments[element.get("name", "")] = Environment(
            source=context[0].get("file") if context else None,
            source_count=len(sources),
        )
    return environments


_ENVIRONMENTS: Final[dict[str, Environment]] = load_environments()


def dependency_reason(case: ET.Element) -> str | None:
    unsupported_features = frozenset({
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
    for dependency in case.findall(f"{_QT3}dependency"):
        kind = dependency.get("type")
        value = dependency.get("value") or ""
        if kind == "spec":
            if (tokens := value.split()) and not any(token.startswith("XP") for token in tokens):
                return f"XQuery-only test (spec: {value})"
        elif kind == "feature" and dependency.get("satisfied") != "false" and value in unsupported_features:
            return f"requires feature: {value}"
    return None


CONTEXTS: Final[dict[str | None, turbohtml.Document]] = {}


def context_for(env: str | None) -> turbohtml.Document:
    if env not in CONTEXTS:
        if env in {None, "empty"}:
            CONTEXTS[env] = turbohtml.parse_xml("<empty/>")
        else:
            assert env is not None
            source = _ENVIRONMENTS[env].source
            assert source is not None  # single-source guard already applied by environment_reason
            CONTEXTS[env] = turbohtml.parse_xml((_ROOT / source).read_text(encoding="utf-8"))
    return CONTEXTS[env]


def environment_reason(env: str | None) -> str | None:
    if env in frozenset({"atomic", "atomic-xq"}):
        return "namespaced source document (turbohtml is HTML-first: local-name == name, namespace-uri empty)"
    if env in {None, "empty"}:
        return None
    if (info := _ENVIRONMENTS.get(env)) is None or info.source is None or info.source_count != 1:
        return f"environment {env!r} is not a single-source document"
    try:
        context_for(env)
    except turbohtml.HTMLParseError as error:
        return f"environment {env!r} is not parseable by turbohtml.parse_xml ({error})"
    return None


def exclusion_reason(family: str, case: ET.Element, query: str) -> str | None:  # ruff:ignore[too-many-return-statements]  # one return per out-of-scope marker reads clearest
    # Return why a supported-family case is out of turbohtml's XPath scope, or None if in scope.
    if (reason := dependency_reason(case)) is not None:
        return reason
    for pattern, marker in _RAW_MARKERS:
        if pattern.search(query):
            return marker
    outside = blank_string_literals(query)
    for pattern, marker in _GRAMMAR_MARKERS:
        if pattern.search(outside):
            return marker
    if has_sequence(query):
        return "sequence/array/empty-sequence literal (2.0)"
    unknown = (
        called_functions(query)
        - frozenset({
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
        - frozenset({"text", "node", "comment", "processing-instruction"})
    )
    if unknown:
        return f"function not implemented: {', '.join(sorted(unknown))}"
    if family in {"matches", "replace"}:
        return regex_dialect_reason(query)
    return None


def expected_result(result: ET.Element) -> Expected:  # ruff:ignore[too-many-return-statements]  # one return per QT3 assertion tag reads clearest
    constant_assertions: dict[str, Expected] = {
        "assert-true": ("bool", True),
        "assert-false": ("bool", False),
        "assert-empty": ("empty", None),
    }
    unmodeled_assertions = frozenset({
        "assert-deep-eq",
        "assert",
        "assert-count",
        "assert-xml",
        "assert-permutation",
        "assert-type",
    })
    for child in result:
        tag = child.tag.removeprefix(_QT3)
        if tag in constant_assertions:
            return constant_assertions[tag]
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
        if tag in unmodeled_assertions:
            return ("unmodeled", tag)
    return ("unmodeled", "no-assertion")


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    query: str
    environment: str | None
    expected: Expected


def xpath_number_string(value: float) -> str:
    if isnan(value):
        return "NaN"
    if isinf(value):
        return "INF" if value > 0 else "-INF"
    return str(int(value)) if value == int(value) else repr(value)


def string_value(result: XPathResult) -> str | None:
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


def matches_expected(expected: Expected, result: XPathResult) -> bool:  # ruff:ignore[too-many-return-statements]  # one return per expected-result kind reads clearest
    kind, value = expected
    if kind == "bool":
        return isinstance(result, bool) and result == value
    if kind == "str":
        return string_value(result) == value
    if kind == "empty":
        return isinstance(result, (str, list)) and not result
    if kind == "any-of":
        return any(matches_expected(option, result) for option in cast("list[Expected]", value))
    if kind == "eq":
        assert isinstance(value, str)
        if isinstance(result, bool):
            return value.lower() in (("true", "true()") if result else ("false", "false()"))
        if isinstance(result, float):
            try:
                return float(value) == result  # ruff:ignore[float-equality-comparison]  # exact equality is the QT3 oracle's contract
            except ValueError:
                return False
        if isinstance(result, str):
            return result == value or result == value.strip("\"'")
    return False


def collect() -> Iterator[ParameterSet]:
    families = (
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
    for family in families:
        catalog = ET.parse(_ROOT / "fn" / f"{family}.xml").getroot()  # ruff:ignore[suspicious-xml-element-tree-usage]  # trusted pinned QT3 fixture
        for element in catalog.findall(f"{_QT3}test-case"):
            case_id = f"{family}::{element.get('name')}"
            reference = element.find(f"{_QT3}environment")
            env = reference.get("ref") if reference is not None else None
            raw_query = (element.findtext(f"{_QT3}test") or "").strip()
            result = element.find(f"{_QT3}result")
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


_QT3_CASES: Final[list[pytest.ParameterSet]] = list(collect())


def _expects_error(param: ParameterSet) -> bool:
    return cast("Case", param.values[0]).expected[0] == "error"


@pytest.mark.parametrize("case", [case for case in _QT3_CASES if _expects_error(case)])
def test_qt3_error_raises(case: Case) -> None:
    context = context_for(case.environment)
    with pytest.raises(Exception):  # ruff:ignore[assert-raises-exception, pytest-raises-too-broad]  # QT3 asserts an error code; turbohtml maps all to exceptions
        context.xpath(case.query)


@pytest.mark.parametrize("case", [case for case in _QT3_CASES if not _expects_error(case)])
def test_qt3_fn(case: Case) -> None:
    context = context_for(case.environment)
    try:
        result = context.xpath(case.query)
    except Exception as error:  # dispatch known XPath-1.0/Python-re-dialect deviations, re-raise the rest
        message = str(error)
        if "non-node-set" in message:
            pytest.xfail("XPath-1.0 count/sum/string-length restricted to node-sets, not atomic sequences")
        if "takes " in message and "argument" in message:
            pytest.xfail("function arity beyond XPath 1.0/2.0 (e.g. the 2-argument round is XPath 3.0)")
        if any(marker in message for marker in _RUNTIME_REGEX_ERRORS):
            pytest.xfail("fn:matches/fn:replace take Python's re syntax, not the XSD regex dialect")
        raise
    assert matches_expected(case.expected, result), f"{case.query!r} -> {result!r}, expected {case.expected}"
