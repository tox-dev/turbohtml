"""Run DOMPurify's own XSS fixture corpus through :func:`turbohtml.clean.sanitize` as an adversarial oracle.

The corpus is ``test/fixtures/expect.mjs`` from DOMPurify (https://github.com/cure53/DOMPurify), vendored verbatim at
commit ``9f8187b0839f392cd65919d4f53567348714185b`` under ``data/dompurify_expect.mjs`` (dual-licensed
``MPL-2.0 OR Apache-2.0``). It is 219 ``{title, payload, expected}`` cases; ``expected`` is DOMPurify's default-config
output, a string or an array of acceptable outputs that absorbs cross-browser serialization variance.

DOMPurify allows nearly all of HTML by default, while turbohtml's default :class:`Policy` is bleach's 12-tag allowlist,
so byte-for-byte parity is not the security question -- the two libraries keep different element sets by design. What is
comparable is the property that *defines* every accepted output: it is inert. So the oracle here is
security-equivalence, not string equality. ``_live_danger`` reparses a sanitized string as a browser would on
``innerHTML`` assignment and lists any executable construct that survived (a kept scriptable element in the HTML
namespace, an ``on*`` handler, a ``javascript:``/``data:``/``vbscript:`` URL, or dangerous CSS on a kept ``<style>`` or
``style`` attribute). Every DOMPurify payload must sanitize to an inert tree -- under the shipped default and relaxed
policies, and under a deliberately max-permissive policy that keeps the SVG/MathML/style surface so only the C-enforced
baseline stands between the payload and the output. A single non-empty result is a real sanitizer bypass, the
highest-severity finding this file exists to catch.

Strict string idempotence is *not* asserted: a permissive policy that keeps foreign content lets the WHATWG parser
foster-parent an already-escaped, inert node across an SVG/MathML boundary on reparse, so ``sanitize(sanitize(x))`` can
reshuffle inert markup without ever producing an executable construct. Inertness of the consumer's single reparse -- the
property that actually gates mutation-XSS -- is what ``_live_danger`` checks and what every case satisfies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from turbohtml import Element, parse_fragment
from turbohtml.clean import DEFAULT_CSS_PROPERTIES, DEFAULT_SCHEMES, Policy, sanitize

_FIXTURE = Path(__file__).parent / "data" / "dompurify_expect.mjs"


@dataclass(frozen=True)
class _Case:
    payload: str
    accepted: frozenset[str]
    title: str


def _load_cases() -> list[_Case]:
    """Extract the ``{payload, expected}`` entries from the ESM fixture by decoding each object with the JSON reader."""
    body = _FIXTURE.read_text(encoding="utf-8").split("export default", 1)[1]
    decoder = json.JSONDecoder()
    index = body.index("[") + 1
    cases: list[_Case] = []
    while True:
        while body[index] in " \t\r\n,":  # whitespace and the array/element separators between entries
            index += 1
        if body[index] == "]":
            return cases
        entry, index = decoder.raw_decode(body, index)
        expected = entry["expected"]
        accepted = frozenset(expected if isinstance(expected, list) else [expected])
        cases.append(_Case(entry["payload"], accepted, entry.get("title", "")))


_CASES = _load_cases()
_IDS = [
    f"{position:03d}-{re.sub(r'[^a-z0-9]+', '-', case.title.lower()).strip('-')[:48] or 'untitled'}"
    for position, case in enumerate(_CASES)
]

# A max-permissive adversarial policy: keep the whole HTML/SVG/MathML structural surface and every attribute name so the
# only thing removing danger is the non-configurable C baseline (unsafe tags, on* handlers, non-allowlisted schemes,
# CSS scrubbing). This is the strongest bypass surface -- a restrictive policy would escape most payloads to inert text
# and never exercise a kept, attack-capable element.
_PERMISSIVE_TAGS = frozenset({
    "a", "abbr", "b", "blockquote", "br", "button", "caption", "cite", "code", "col", "colgroup", "dd", "del", "div",
    "dl", "dt", "em", "figcaption", "figure", "form", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "input",
    "ins", "label", "li", "mark", "ol", "option", "p", "pre", "q", "s", "select", "small", "span", "strong", "style",
    "sub", "sup", "table", "tbody", "td", "textarea", "tfoot", "th", "thead", "tr", "u", "ul",
    "svg", "g", "rect", "circle", "path", "defs", "filter", "fegaussianblur", "image", "title", "desc",
    "foreignobject", "text", "use", "animate", "set",
    "math", "mi", "mo", "mn", "ms", "mtext", "mrow", "mglyph", "annotation-xml",
})  # fmt: skip
_PERMISSIVE = Policy(
    tags=_PERMISSIVE_TAGS,
    attributes=MappingProxyType({"*": frozenset({"*"})}),
    url_schemes=DEFAULT_SCHEMES,
    css_properties=DEFAULT_CSS_PROPERTIES,
)
_POLICIES = [
    pytest.param(Policy(), id="default"),
    pytest.param(Policy.relaxed(), id="relaxed"),
    pytest.param(_PERMISSIVE, id="permissive"),
]

# Scriptable elements that execute or load code if they survive in the HTML namespace; scheme prefixes that run script.
_DANGER_TAGS = frozenset({"script", "iframe", "object", "embed", "frame", "style", "noscript", "base"})
_DANGER_SCHEMES = ("javascript:", "data:", "vbscript:")
_URL_ATTRS = frozenset({"href", "src", "action", "xlink:href", "formaction", "poster", "background", "cite", "ping"})
# CSS constructs that execute or fetch when a kept <style> body or style attribute survives the property scrub.
_CSS_DANGER = ("javascript:", "vbscript:", "expression(", "@import", "behavior:", "-moz-binding")


def _attr_value(raw: str | list[str] | None) -> str:
    """The lowercased attribute value, joining a duplicate-attribute list and reading a boolean attr as empty."""
    if raw is None:
        return ""
    joined = " ".join(raw) if isinstance(raw, list) else raw
    return joined.lower()


def _style_body(element: Element) -> str:
    """The lowercased text content of a ``<style>`` element (rawtext, so its children are text nodes)."""
    return "".join(getattr(child, "data", "") for child in element.children).lower()


def _bad_scheme(value: str) -> bool:
    """Whether a URL value resolves to a script-capable scheme once the control/whitespace obfuscation is stripped."""
    return "".join(char for char in value if ord(char) > 0x20).startswith(_DANGER_SCHEMES)


def _dangerous_element(node: Element) -> str | None:
    """A label for a scriptable element kept in the HTML namespace (a kept ``<style>`` only if its body survived)."""
    if node.tag not in _DANGER_TAGS or node.namespace.value != "html":
        return None
    if node.tag != "style":
        return f"<{node.tag}>"
    return "style-body" if any(token in _style_body(node) for token in _CSS_DANGER) else None


def _dangerous_attribute(name: str, value: str) -> str | None:
    """A label for an executable attribute: an ``on*`` handler, dangerous CSS on ``style``, or a scriptable URL."""
    if name.startswith("on"):
        return f"@{name}"
    if name == "style":
        return "@style" if any(token in value for token in _CSS_DANGER) else None
    return f"{name}={value[:32]}" if name in _URL_ATTRS and _bad_scheme(value) else None


def _live_danger(html: str) -> list[str]:
    """Reparse sanitized HTML and list every executable construct that survived; an empty list means inert."""
    survived: list[str] = []
    stack = list(parse_fragment(html).children)
    while stack:
        node = stack.pop()
        if isinstance(node, Element):
            if (element_hit := _dangerous_element(node)) is not None:
                survived.append(element_hit)
            survived.extend(
                hit
                for name, raw in node.attrs.items()
                if (hit := _dangerous_attribute(name, _attr_value(raw))) is not None
            )
            stack.extend(node.children)
    return survived


@pytest.mark.parametrize("policy", _POLICIES)
@pytest.mark.parametrize("case", _CASES, ids=_IDS)
def test_dompurify_payload_sanitizes_to_inert(case: _Case, policy: Policy) -> None:
    survived = _live_danger(sanitize(case.payload, policy))
    assert survived == [], (
        f"sanitizer bypass -- executable markup survived DOMPurify payload {case.title!r}: {survived}"
    )


@pytest.mark.parametrize("case", _CASES, ids=_IDS)
def test_turbohtml_is_never_less_safe_than_dompurify(case: _Case) -> None:
    # "output in the accepted set" is the wrong metric on its own: DOMPurify keeps data: image URIs and does not scrub
    # CSS, so its accepted outputs carry constructs turbohtml strips by design, and the two allowlists differ. The
    # security-equivalence claim is the one that holds every time -- turbohtml's output is byte-identical to an accepted
    # DOMPurify output, or, where the allowlists diverge, still provably inert. It is never a downgrade.
    out = sanitize(case.payload, _PERMISSIVE)
    assert out in case.accepted or _live_danger(out) == [], f"downgrade vs DOMPurify on {case.title!r}: {out!r}"


def test_attr_value_normalizes_every_shape() -> None:
    assert (_attr_value(None), _attr_value(["A", "B"]), _attr_value("HrEf")) == ("", "a b", "href")


# Guard the oracle: each row pins the exact label _live_danger yields (or [] for the inert counterpart), so a checker
# that stopped detecting a class would fail here rather than silently green-light a bypass in the corpus run above.
@pytest.mark.parametrize(
    ("html", "survived"),
    [
        pytest.param("<script>alert(1)</script>", ["<script>"], id="scriptable-element"),
        pytest.param("<svg><script>alert(1)</script></svg>", [], id="scriptable-element-in-svg-namespace-inert"),
        pytest.param("<style>a{background:url(javascript:alert(1))}</style>", ["style-body"], id="style-body-danger"),
        pytest.param("<style>a{color:red}</style>", [], id="style-body-benign"),
        pytest.param('<img src=x onerror="alert(1)">', ["@onerror"], id="event-handler"),
        pytest.param('<p style="behavior:url(#x)">x</p>', ["@style"], id="style-attr-danger"),
        pytest.param('<p style="color:red">x</p>', [], id="style-attr-benign"),
        pytest.param('<a href="javascript:alert(1)">x</a>', ["href=javascript:alert(1)"], id="url-danger"),
        pytest.param('<a href="https://example.com">x</a>', [], id="url-benign"),
    ],
)
def test_live_danger_labels_every_executable_construct(html: str, survived: list[str]) -> None:
    assert _live_danger(html) == survived
