"""Validate turbohtml's sanitizer head-to-head against DOMPurify's own test corpus and a live DOMPurify Node build.

DOMPurify (https://github.com/cure53/DOMPurify) is the reference HTML sanitizer. It is vendored as a pinned shallow
submodule at ``tests/conformance/DOMPurify``; the corpus is its ``test/fixtures/expect.mjs``, ~219 ``{payload}`` XSS
vectors and config cases. The oracle is DOMPurify's *actual* output: the Node runner
``tools/bench/node/dompurify_conformance_runner.js`` runs each payload through ``isomorphic-dompurify`` under the config
that mirrors the Policy feature being validated, so the comparison is head-to-head, not against a frozen string.

Two questions are asked, at two rigor levels:

1. *Security is absolute.* For every payload, under every config, turbohtml's output must be inert -- no scriptable
   element in the HTML namespace, no ``on*`` handler, no ``javascript:``/``data:``/``vbscript:`` URL, no dangerous CSS.
   And turbohtml must never be *less* safe than DOMPurify: any executable construct it keeps DOMPurify must keep too.
   ``_live_danger`` reparses the sanitized string as a browser would on ``innerHTML`` assignment and lists what
   survived; the list must be empty (and a subset of DOMPurify's). A single non-empty result is a real bypass.

2. *Config features match, on curated inputs.* DOMPurify keeps nearly all of HTML by default while turbohtml's Policy is
   an explicit allowlist, so byte-parity over the whole corpus is dominated by allowlist scope, not by the feature under
   test -- that is a documented architectural difference, not a bug. So each feature (``SAFE_FOR_TEMPLATES`` ->
   ``strip_template_markers``, ``SANITIZE_NAMED_PROPS`` -> ``isolate_named_props``, ``CUSTOM_ELEMENT_HANDLING`` ->
   ``custom_element_check``/``custom_attribute_check``, ``USE_PROFILES`` -> the ``allow_html``/``allow_svg``/
   ``allow_mathml`` gates) is validated on a curated corpus whose tags both allowlists share, where the only variable is
   the config feature. There the canonical (namespace-, attribute-order-normalized) forms must be identical.

Two deviations are documented and xfailed rather than hidden -- both cases where turbohtml is *stricter*, never less
safe. The template stripper collapses only marker runs that can *open* a template evaluation (``{{``/``${``/``<%``),
leaving a bare unmatched close delimiter as literal text since it cannot start one, while DOMPurify's greedy regex also
eats the text before a bare close; and stripping a disallowed HTML wrapper drops a foreign (SVG/MathML) child subtree
instead of hoisting it, so a profile keeps less than DOMPurify when foreign content is nested inside disallowed HTML.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404  # drives the DOMPurify Node oracle: fixed argv, vendored test-data inputs
from dataclasses import replace
from functools import cache
from pathlib import Path
from types import MappingProxyType

import pytest

from turbohtml import Element, parse_fragment
from turbohtml.clean import DEFAULT_CSS_PROPERTIES, DEFAULT_SCHEMES, OnDisallowed, Policy, sanitize

_SUBMODULE = Path(__file__).parent / "DOMPurify"
_FIXTURE = _SUBMODULE / "test" / "fixtures" / "expect.mjs"
_NODE_DIR = Path(__file__).parents[2] / "tools" / "bench" / "node"
_RUNNER = _NODE_DIR / "dompurify_conformance_runner.js"
_NODE = shutil.which("node")

# The oracle is mixed, so the two halves guard differently. The DOMPurify corpus is a vendored data submodule: the
# dedicated "conformance" CI job checks it out, so its absence is a setup error, not a reason to skip (a silent skip
# would let the whole suite vanish). Node with isomorphic-dompurify is a runtime toolchain a bare dev checkout may lack,
# so its absence is a legitimate skip -- the conformance job installs it, other envs need not.
if not _FIXTURE.exists():  # pragma: no cover
    msg = (
        "submodule tests/conformance/DOMPurify not checked out; "
        "run: git submodule update --init tests/conformance/DOMPurify"
    )
    raise RuntimeError(msg)
if _NODE is None or not (_NODE_DIR / "node_modules" / "isomorphic-dompurify").exists():
    pytest.skip("node or isomorphic-dompurify absent (run `npm install` in tools/bench/node)", allow_module_level=True)

_NODE_BIN = _NODE or "node"  # the skip above guarantees a real path here; the fallback only narrows str | None for ty


def _load_payloads() -> list[str]:
    """Extract every ``payload`` string from the ESM fixture by decoding each object with the JSON reader."""
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


_PAYLOADS = _load_payloads()
_CASES = list(enumerate(_PAYLOADS))
_IDS = [f"{position:03d}" for position in range(len(_PAYLOADS))]


@cache
def _dompurify(mode: str) -> tuple[str, ...]:
    """DOMPurify's output for the whole corpus under ``mode``, one Node process per mode (cached for the session)."""
    request = json.dumps({"mode": mode, "inputs": _PAYLOADS})
    result = subprocess.run(  # noqa: S603  # fixed argv, node is the absolute path from shutil.which
        [_NODE_BIN, str(_RUNNER)],
        input=request,
        capture_output=True,
        text=True,
        cwd=_NODE_DIR,
        check=True,
    )
    return tuple(json.loads(result.stdout))


def _dompurify_one(mode: str, html: str) -> str:
    """DOMPurify's output for a single ad-hoc input (curated corpora that are not part of the fixture)."""
    request = json.dumps({"mode": mode, "inputs": [html]})
    result = subprocess.run(  # noqa: S603  # fixed argv, node is the absolute path from shutil.which
        [_NODE_BIN, str(_RUNNER)],
        input=request,
        capture_output=True,
        text=True,
        cwd=_NODE_DIR,
        check=True,
    )
    return json.loads(result.stdout)[0]


_PERMISSIVE_ATTRS = MappingProxyType({"*": frozenset({"*"})})
# DOMPurify removes these elements together with their contents; turbohtml matches with remove_with_content so the two
# agree on script/style bodies rather than turbohtml leaking the body as inert text under a STRIP policy.
_REMOVE_WITH_CONTENT = frozenset({
    "script", "style", "noscript", "template", "noembed", "noframes", "iframe", "object", "embed",
})  # fmt: skip
_STRUCTURAL_TAGS = frozenset(Policy.relaxed().tags | {
    "form", "input", "select", "button", "svg", "circle", "rect", "g", "path", "defs", "filter", "image", "text",
    "math", "mi", "mn", "mo", "mrow", "mtext", "ms", "style",
})  # fmt: skip


# A max-permissive base allowlist so only the config feature under test and the C safety baseline shape the output; each
# mode is that base with one DOMPurify-equivalent feature turned on. The profiles reuse the shared allowlist.
_BASE = Policy(
    tags=_STRUCTURAL_TAGS,
    attributes=_PERMISSIVE_ATTRS,
    url_schemes=DEFAULT_SCHEMES,
    css_properties=DEFAULT_CSS_PROPERTIES,
    on_disallowed_tag=OnDisallowed.STRIP,
    remove_with_content=_REMOVE_WITH_CONTENT,
)
_MODES: dict[str, Policy] = {
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
}

# --- inertness oracle: what executes if a sanitized string is assigned to innerHTML ---------------------------------
_DANGER_TAGS = frozenset({"script", "iframe", "object", "embed", "frame", "style", "noscript", "base"})
_DANGER_SCHEMES = ("javascript:", "data:", "vbscript:")
_URL_ATTRS = frozenset({"href", "src", "action", "xlink:href", "formaction", "poster", "background", "cite", "ping"})
_CSS_DANGER = ("javascript:", "vbscript:", "expression(", "@import", "behavior:", "-moz-binding")
_TEMPLATE_OPENERS = ("{{", "${", "<%")


def _attr_value(raw: str | list[str] | None) -> str:
    """The lowercased attribute value, joining a duplicate-attribute list and reading a boolean attr as empty."""
    if raw is None:
        return ""
    return (" ".join(raw) if isinstance(raw, list) else raw).lower()


def _bad_scheme(value: str) -> bool:
    """Whether a URL value resolves to a script-capable scheme once control/whitespace obfuscation is stripped."""
    return "".join(char for char in value if ord(char) > 0x20).startswith(_DANGER_SCHEMES)


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
            if node.tag in _DANGER_TAGS and node.namespace.value == "html":
                body = "".join(getattr(child, "data", "") for child in node.children).lower()
                if node.tag != "style":
                    survived.append(f"<{node.tag}>")
                elif any(token in body for token in _CSS_DANGER):
                    survived.append("style-body")
            survived.extend(
                hit
                for name, raw in node.attrs.items()
                if (hit := _dangerous_attribute(name, _attr_value(raw))) is not None
            )
            stack.extend(node.children)
    return survived


def _canonical(html: str) -> str:
    """A serialization-independent form: reparse, then emit namespace-tagged tags with attributes in sorted order."""

    def walk(node: Element | object) -> str:
        if isinstance(node, Element):
            attrs = sorted((k, _attr_value(v)) for k, v in node.attrs.items())
            rendered = "".join(f" {name}={value!r}" for name, value in attrs)
            body = "".join(walk(child) for child in node.children)
            return f"<{node.namespace.value}:{node.tag}{rendered}>{body}</{node.tag}>"
        return getattr(node, "data", "")

    return "".join(walk(child) for child in parse_fragment(html).children)


def _text_and_attrs(html: str) -> str:
    """Every text node and attribute value concatenated, for scanning surviving template markers."""
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


@pytest.mark.parametrize(("index", "payload"), _CASES, ids=_IDS)
def test_turbohtml_is_inert_and_never_less_safe_than_dompurify(index: int, payload: str) -> None:
    # The absolute gate, over every DOMPurify config: turbohtml's output is inert, and every executable construct it
    # keeps DOMPurify keeps too (so it is never a downgrade). Inertness makes the subset trivially hold; asserting both
    # documents the head-to-head claim and would flag a regression that made turbohtml keep something new.
    for mode, policy in _MODES.items():
        theirs = _live_danger(_dompurify(mode)[index])
        ours = _live_danger(sanitize(payload, policy))
        assert ours == [], f"sanitizer bypass under {mode}: {ours} survived {payload!r}"
        assert set(ours) <= set(theirs), f"less safe than DOMPurify under {mode}: {ours} vs {theirs}"


@pytest.mark.parametrize(("index", "payload"), _CASES, ids=_IDS)
def test_templates_strip_every_opening_marker(index: int, payload: str) -> None:
    # SAFE_FOR_TEMPLATES exists to stop re-injection when output is later rendered by a template engine; the vector is a
    # delimiter that *opens* an evaluation ({{ , ${ , <%). turbohtml must leave none, and so must DOMPurify (oracle
    # cross-check). A bare unmatched close delimiter is inert and is covered by the documented deviation below.
    ours = _text_and_attrs(sanitize(payload, _MODES["templates"]))
    theirs = _text_and_attrs(_dompurify("templates")[index])
    assert not any(marker in ours for marker in _TEMPLATE_OPENERS), f"opening marker survived: {ours!r}"
    assert not any(marker in theirs for marker in _TEMPLATE_OPENERS), f"DOMPurify left an opener: {theirs!r}"


_TEMPLATE_DEVIATIONS = [
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


_NAMED_PROPS_CORPUS = [
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


_CUSTOM_ELEMENT_CORPUS = [
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


_PROFILE_CORPUS = [
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


_PROFILE_DEVIATIONS = [
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
        pytest.param("<svg><script>alert(1)</script></svg>", [], id="svg-namespace-script-inert"),
        pytest.param('<img src=x onerror="alert(1)">', ["@onerror"], id="event-handler"),
        pytest.param('<a href="javascript:alert(1)">x</a>', ["href=javascript:alert(1)"], id="url-danger"),
        pytest.param('<a href="https://example.com">x</a>', [], id="url-benign"),
        pytest.param("<style>a{behavior:url(#x)}</style>", ["style-body"], id="style-body-danger"),
    ],
)
def test_live_danger_labels_every_executable_construct(html: str, survived: list[str]) -> None:
    assert _live_danger(html) == survived
