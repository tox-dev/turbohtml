from __future__ import annotations

import json
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # drives the DOMPurify Node oracle: fixed argv, vendored test-data inputs
from dataclasses import replace
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest

from turbohtml import Element, Node, parse_fragment
from turbohtml.clean import DEFAULT_CSS_PROPERTIES, DEFAULT_SCHEMES, OnDisallowed, Policy, sanitize

_SUBMODULE: Final[Path] = Path(__file__).parent / "DOMPurify"
_FIXTURE: Final[Path] = _SUBMODULE / "test" / "fixtures" / "expect.mjs"
_NODE_DIR: Final[Path] = Path(__file__).parents[2] / "tools" / "bench" / "node"
_RUNNER: Final[Path] = _NODE_DIR / "dompurify_conformance_runner.js"
_NODE: Final[str | None] = shutil.which("node")

# The oracle is mixed, so the two halves guard differently. The DOMPurify corpus is a vendored data submodule: the
# dedicated "conformance" CI job checks it out, so its absence is a setup error, not a reason to skip (a silent skip
# would let the whole suite vanish). Node with isomorphic-dompurify is a runtime toolchain a bare dev checkout may lack,
# so its absence is a legitimate skip -- the conformance job installs it, other envs need not.
if not _FIXTURE.exists():  # pragma: no cover
    _MSG = (
        "submodule tests/conformance/DOMPurify not checked out; "
        "run: git submodule update --init tests/conformance/DOMPurify"
    )
    raise RuntimeError(_MSG)
if _NODE is None or not (_NODE_DIR / "node_modules" / "isomorphic-dompurify").exists():
    pytest.skip("node or isomorphic-dompurify absent (run `npm install` in tools/bench/node)", allow_module_level=True)

_NODE_BIN: Final[str] = (
    _NODE or "node"
)  # the skip above guarantees a real path here; the fallback only narrows str | None for ty


def _load_payloads() -> list[str]:
    # Extract every ``payload`` string from the ESM fixture by decoding each object with the JSON reader.
    body = _FIXTURE.read_text(encoding="utf-8").split("export default", 1)[1]
    decoder = json.JSONDecoder()
    index = body.index("[") + 1
    payloads: list[str] = []
    while True:
        while body[index] in " \t\r\n,":  # whitespace and the separators between array entries
            index += 1
        if body[index] == "]":
            return payloads
        entry, index = decoder.raw_decode(body, index)
        payloads.append(entry["payload"])


_PAYLOADS: Final[list[str]] = _load_payloads()
_CASES: Final[list[tuple[int, str]]] = list(enumerate(_PAYLOADS))
_IDS: Final[list[str]] = [f"{position:03d}" for position in range(len(_PAYLOADS))]


_PERMISSIVE_ATTRS: Final[MappingProxyType[str, frozenset[str]]] = MappingProxyType({"*": frozenset({"*"})})
# DOMPurify removes these elements together with their contents; turbohtml matches with remove_with_content so the two
# agree on script/style bodies rather than turbohtml leaking the body as inert text under a STRIP policy.
_REMOVE_WITH_CONTENT: Final[frozenset[str]] = frozenset({
    "script", "style", "noscript", "template", "noembed", "noframes", "iframe", "object", "embed",
})  # fmt: skip
_STRUCTURAL_TAGS: Final[frozenset[str]] = frozenset(Policy.relaxed().tags | {
    "form", "input", "select", "button", "script", "svg", "circle", "rect", "g", "path", "defs", "filter", "image",
    "text",
    "animate", "animateColor", "animateMotion", "animateTransform", "set",
    "math", "mi", "mn", "mo", "mrow", "mtext", "ms", "style",
})  # fmt: skip


# A max-permissive base allowlist so only the config feature under test and the C safety baseline shape the output; each
# mode is that base with one DOMPurify-equivalent feature turned on. The profiles reuse the shared allowlist.
_BASE: Final[Policy] = Policy(
    tags=_STRUCTURAL_TAGS,
    attributes=_PERMISSIVE_ATTRS,
    url_schemes=DEFAULT_SCHEMES,
    css_properties=DEFAULT_CSS_PROPERTIES,
    on_disallowed_tag=OnDisallowed.STRIP,
    remove_with_content=_REMOVE_WITH_CONTENT,
)
_MODES: Final[MappingProxyType[str, Policy]] = MappingProxyType({
    "templates": replace(_BASE, strip_template_markers=True),
    "named-props": replace(_BASE, isolate_named_props=True),
    "custom-elements": replace(
        _BASE,
        custom_element_check=lambda tag: tag.startswith("x-"),
        custom_attribute_check=lambda _tag, attr: attr.startswith("data-"),
    ),
    "profile-html": replace(_BASE, allow_svg=False, allow_mathml=False),
    "profile-svg": replace(_BASE, allow_html=False, allow_mathml=False),
    "profile-mathml": replace(_BASE, allow_html=False, allow_svg=False),
})

_URL_ATTRS: Final[frozenset[str]] = frozenset({
    "href",
    "src",
    "action",
    "xlink:href",
    "formaction",
    "poster",
    "background",
    "cite",
    "ping",
})
_CSS_DANGER: Final[tuple[str, ...]] = (
    "javascript:",
    "vbscript:",
    "expression(",
    "@import",
    "behavior:",
    "-moz-binding",
)


@pytest.mark.parametrize("mode", list(_MODES), ids=list(_MODES))
@pytest.mark.parametrize(("index", "payload"), _CASES, ids=_IDS)
def test_turbohtml_is_inert_and_never_less_safe_than_dompurify(mode: str, index: int, payload: str) -> None:
    # The absolute gate, over every DOMPurify config: turbohtml's output is inert, and every executable construct it
    # keeps DOMPurify keeps too (so it is never a downgrade). Inertness makes the subset trivially hold; asserting both
    # documents the head-to-head claim and would flag a regression that made turbohtml keep something new.
    theirs = _live_danger(_dompurify(mode)[index])
    ours = _live_danger(sanitize(payload, _MODES[mode]))
    assert ours == [], f"sanitizer bypass under {mode}: {ours} survived {payload!r}"
    assert set(ours) <= set(theirs), f"less safe than DOMPurify under {mode}: {ours} vs {theirs}"


def _live_danger(html: str) -> list[str]:
    # Reparse sanitized HTML and list every executable construct that survived; an empty list means inert.
    survived: list[str] = []
    stack = list(parse_fragment(html).children)
    while stack:
        node = stack.pop()
        if isinstance(node, Element):
            survived.extend(_element_danger(node))
            stack.extend(node.children)
    return survived


def _element_danger(node: Element) -> list[str]:
    danger_tags = frozenset({"script", "iframe", "object", "embed", "frame", "style", "noscript", "base"})
    svg_animation_tags = frozenset({"animate", "animateColor", "animateMotion", "animateTransform", "set"})
    namespace = node.namespace.value
    survived: list[str] = []
    if namespace == "svg" and node.tag in svg_animation_tags:
        attrs = {name.lower(): _attr_value(value) for name, value in node.attrs.items()}
        target = attrs.get("attributename", "")
        if target.startswith("on") or (
            target in _URL_ATTRS
            and any(_bad_scheme(value) for name in ("from", "to", "values") for value in attrs.get(name, "").split(";"))
        ):
            survived.append(f"<{node.tag}>->{target}")
    if node.tag == "script" and namespace in {"html", "svg"}:
        survived.append("<script>")
    elif node.tag == "style" and namespace in {"html", "svg"}:
        body = "".join(getattr(child, "data", "") for child in node.children).lower()
        if any(token in body for token in _CSS_DANGER):
            survived.append("style-body")
    elif node.tag in danger_tags and namespace == "html":
        survived.append(f"<{node.tag}>")
    survived.extend(
        hit for name, raw in node.attrs.items() if (hit := _dangerous_attribute(name, _attr_value(raw))) is not None
    )
    return survived


def _attr_value(raw: str | list[str] | None) -> str:
    # The lowercased attribute value, joining a duplicate-attribute list and reading a boolean attr as empty.
    if raw is None:
        return ""
    return (" ".join(raw) if isinstance(raw, list) else raw).lower()


def _bad_scheme(value: str) -> bool:
    # Whether a URL value resolves to a script-capable scheme once control/whitespace obfuscation is stripped.
    return "".join(char for char in value if ord(char) > 0x20).startswith(("javascript:", "data:", "vbscript:"))


def _dangerous_attribute(name: str, value: str) -> str | None:
    # A label for an executable attribute: an ``on*`` handler, dangerous CSS on ``style``, or a scriptable URL.
    if name.startswith("on"):
        return f"@{name}"
    if name == "style":
        return "@style" if any(token in value for token in _CSS_DANGER) else None
    return f"{name}={value[:32]}" if name in _URL_ATTRS and _bad_scheme(value) else None


@cache
def _dompurify(mode: str) -> tuple[str, ...]:
    # DOMPurify's output for the whole corpus under ``mode``, one Node process per mode (cached for the session).
    request = json.dumps({"mode": mode, "inputs": _PAYLOADS})
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, node is the absolute path from shutil.which
        [_NODE_BIN, str(_RUNNER)],
        input=request,
        capture_output=True,
        text=True,
        cwd=_NODE_DIR,
        check=True,
    )
    return tuple(json.loads(result.stdout))


@pytest.mark.parametrize(("index", "payload"), _CASES, ids=_IDS)
def test_templates_strip_every_opening_marker(index: int, payload: str) -> None:
    # SAFE_FOR_TEMPLATES exists to stop re-injection when output is later rendered by a template engine; the vector is a
    # delimiter that *opens* an evaluation ({{ , ${ , <%). turbohtml must leave none, and so must DOMPurify (oracle
    # cross-check). A bare unmatched close delimiter is inert and is covered by the documented deviation below.
    ours = _text_and_attrs(sanitize(payload, _MODES["templates"]))
    theirs = _text_and_attrs(_dompurify("templates")[index])
    template_openers = ("{{", "${", "<%")
    assert not any(marker in ours for marker in template_openers), f"opening marker survived: {ours!r}"
    assert not any(marker in theirs for marker in template_openers), f"DOMPurify left an opener: {theirs!r}"


def _text_and_attrs(html: str) -> str:
    # Every text node and attribute value concatenated, for scanning surviving template markers.
    parts: list[str] = []
    stack = list(parse_fragment(html).children)
    while stack:
        node = stack.pop()
        if isinstance(node, Element):
            parts.extend(_attr_value(raw) for raw in node.attrs.values())
            stack.extend(node.children)
        else:
            parts.append(getattr(node, "data", ""))
    return "".join(parts)


_TEMPLATE_DEVIATIONS: Final[list[pytest.ParameterSet]] = [
    pytest.param("<p>a{{x}}b</p>", id="mustache-keeps-leading-text"),
    pytest.param("<p>x{{a}b}}y</p>", id="mustache-keeps-trailing-text"),
    pytest.param("<p>lead %> tail</p>", id="bare-erb-close-kept-as-text"),
]


@pytest.mark.xfail(
    strict=True,
    reason="turbohtml collapses only opener-anchored marker runs; DOMPurify greedily eats "
    "surrounding text. Both remove every injection-capable opener, so turbohtml is never less safe.",
)
@pytest.mark.parametrize("payload", _TEMPLATE_DEVIATIONS)
def test_templates_greedy_text_eating_deviation(payload: str) -> None:
    assert _canonical(sanitize(payload, _MODES["templates"])) == _canonical(_dompurify_one("templates", payload))


def _canonical(html: str) -> str:
    # A serialization-independent form: reparse, then emit namespace-tagged tags with attributes in sorted order.
    def walk(node: Node) -> str:
        if isinstance(node, Element):
            attrs = sorted((k, _attr_value(v)) for k, v in node.attrs.items())
            rendered = "".join(f" {name}={value!r}" for name, value in attrs)
            body = "".join(walk(child) for child in node.children)
            return f"<{node.namespace.value}:{node.tag}{rendered}>{body}</{node.tag}>"
        return getattr(node, "data", "")

    return "".join(walk(child) for child in parse_fragment(html).children)


def _dompurify_one(mode: str, html: str) -> str:
    # DOMPurify's output for a single ad-hoc input (curated corpora that are not part of the fixture).
    request = json.dumps({"mode": mode, "inputs": [html]})
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, node is the absolute path from shutil.which
        [_NODE_BIN, str(_RUNNER)],
        input=request,
        capture_output=True,
        text=True,
        cwd=_NODE_DIR,
        check=True,
    )
    return json.loads(result.stdout)[0]


_NAMED_PROPS_CORPUS: Final[list[pytest.ParameterSet]] = [
    pytest.param('<a id="location" href="http://x/">x</a>', id="id-collision"),
    pytest.param("<input name=attributes>", id="name-collision"),
    pytest.param('<img name=body id=cfg src="http://x/i">', id="id-and-name"),
    pytest.param("<form name=x><input name=y></form>", id="nested-form-controls"),
    pytest.param('<p id="user-content-already">t</p>', id="already-prefixed-is-fixpoint"),
    pytest.param('<a name="body">t</a>', id="name-shadowing-document-body"),
    pytest.param("<button id=submit name=item>go</button>", id="form-submit-clobber"),
    pytest.param("<div id=constructor>c</div>", id="prototype-clobber"),
    pytest.param("<input name=nodeName><input name=nodeType>", id="node-props"),
]


@pytest.mark.parametrize("payload", _NAMED_PROPS_CORPUS)
def test_named_props_parity(payload: str) -> None:
    assert _canonical(sanitize(payload, _MODES["named-props"])) == _canonical(_dompurify_one("named-props", payload))


_CUSTOM_ELEMENT_CORPUS: Final[list[pytest.ParameterSet]] = [
    pytest.param("<x-a data-x=1>hi</x-a><y-b>no</y-b>", id="keep-matching-drop-other"),
    pytest.param("<x-foo onclick=alert(1) data-ok=1>t</x-foo>", id="baseline-scrubs-handler-on-kept"),
    pytest.param("<my-el>drop</my-el>", id="non-matching-stripped"),
    pytest.param("<x-card><b>t</b></x-card>", id="known-child-of-custom"),
    pytest.param("<x-a data-a=1 title=t>k</x-a>", id="data-and-known-attr"),
    pytest.param("<x-y><x-z>deep</x-z></x-y>", id="nested-custom"),
    pytest.param("<x-btn data-role=save>S</x-btn><other-btn>C</other-btn>", id="mixed-custom"),
]


@pytest.mark.parametrize("payload", _CUSTOM_ELEMENT_CORPUS)
def test_custom_element_parity(payload: str) -> None:
    ours = sanitize(payload, _MODES["custom-elements"])
    assert _canonical(ours) == _canonical(_dompurify_one("custom-elements", payload))


_PROFILE_CORPUS: Final[list[pytest.ParameterSet]] = [
    pytest.param("<p>text</p><svg><circle/></svg><math><mi>x</mi></math>", id="all-three-namespaces"),
    pytest.param("<b>bold</b><i>it</i>", id="plain-html"),
    pytest.param("<svg><rect/></svg>", id="top-level-svg"),
    pytest.param("<math><mrow><mn>1</mn></mrow></math>", id="top-level-mathml"),
    pytest.param("<svg><g><path/></g></svg><math><mo>+</mo></math>", id="foreign-siblings"),
]


@pytest.mark.parametrize("payload", _PROFILE_CORPUS)
@pytest.mark.parametrize("mode", ["profile-html", "profile-svg", "profile-mathml"])
def test_profile_parity(mode: str, payload: str) -> None:
    assert _canonical(sanitize(payload, _MODES[mode])) == _canonical(_dompurify_one(mode, payload))


_PROFILE_DEVIATIONS: Final[list[pytest.ParameterSet]] = [
    pytest.param("profile-svg", "<div><svg><g><path/></g></svg></div>", id="svg-child-of-disallowed-html"),
    pytest.param("profile-mathml", "<p><math><mo>+</mo></math></p>", id="mathml-child-of-disallowed-html"),
]


@pytest.mark.xfail(
    strict=True,
    reason="turbohtml drops a foreign child subtree when its disallowed HTML wrapper is "
    "stripped instead of hoisting it, so a profile keeps less than DOMPurify. Stricter, never less safe.",
)
@pytest.mark.parametrize(("mode", "payload"), _PROFILE_DEVIATIONS)
def test_profile_nested_foreign_deviation(mode: str, payload: str) -> None:
    assert _canonical(sanitize(payload, _MODES[mode])) == _canonical(_dompurify_one(mode, payload))


def test_corpus_exercises_the_oracle() -> None:
    # guard against a silent corpus: the fixture must be large and DOMPurify must actually transform some payloads
    assert len(_PAYLOADS) >= 200
    assert sum(1 for i, p in enumerate(_PAYLOADS) if _dompurify("named-props")[i] != p) >= 20


@pytest.mark.parametrize(
    ("html", "survived"),
    [
        pytest.param("<script>alert(1)</script>", ["<script>"], id="scriptable-element"),
        pytest.param("<svg><script>alert(1)</script></svg>", ["<script>"], id="svg-script"),
        pytest.param("<svg><style>a{behavior:url(#x)}</style></svg>", ["style-body"], id="svg-style-danger"),
        pytest.param("<svg><style>a{fill:red}</style></svg>", [], id="svg-style-benign"),
        pytest.param('<img src=x onerror="alert(1)">', ["@onerror"], id="event-handler"),
        pytest.param('<a href="javascript:alert(1)">x</a>', ["href=javascript:alert(1)"], id="url-danger"),
        pytest.param('<a href="https://example.com">x</a>', [], id="url-benign"),
        pytest.param("<style>a{behavior:url(#x)}</style>", ["style-body"], id="style-body-danger"),
        pytest.param(
            '<svg><animate attributeName="xlink:href" from="javascript:x"></animate></svg>',
            ["<animate>->xlink:href"],
            id="svg-animation-url",
        ),
        pytest.param(
            '<svg><set attributeName="onload" to="alert(1)"></set></svg>',
            ["<set>->onload"],
            id="svg-animation-handler",
        ),
    ],
)
def test_live_danger_labels_every_executable_construct(html: str, survived: list[str]) -> None:
    assert _live_danger(html) == survived
