"""The serialize(layout=Minify(minify_js=...)) inline-<script> JavaScript pass.

Minify never touches scripts on its own; passing a JSMinify turns the pass on. A script
is minified only when its type marks it as JavaScript, and a script the JS parser cannot
handle is emitted verbatim, so one bad <script> never breaks document serialization. The
JS minifier's own correctness is gated in tests/serialize/js/; here we pin the wiring:
which scripts the HTML serializer routes through it, and the Minify options surface.
"""

from __future__ import annotations

import pytest

from turbohtml import Html, JSMinify, Minify, parse_fragment

_SCRIPT = "function f(){ var longName = 1 + 2 ; return longName }"


def script(source: str, minify_js: JSMinify | None = None) -> str:
    """Serialize source as the single child of a <script> under Minify(minify_js=...),
    returning just the script element so the assertion is the minified (or verbatim) body."""
    out = parse_fragment(f"<script>{source}</script>", "div").serialize(Html(layout=Minify(minify_js=minify_js)))
    assert out.startswith("<div><script>")
    assert out.endswith("</script></div>")
    return out[len("<div>") : -len("</div>")]


def test_scripts_untouched_without_minify_js() -> None:
    # the default Minify leaves JavaScript exactly as written
    assert script(_SCRIPT) == f"<script>{_SCRIPT}</script>"


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(JSMinify(), "function f(){return 3}", id="full"),
        pytest.param(JSMinify(mangle=False), "function f(){var longName=3;return longName}", id="no-mangle"),
        pytest.param(JSMinify(fold=False), "function f(){return 1+2}", id="no-fold"),
        pytest.param(
            JSMinify(mangle=False, fold=False), "function f(){var longName=1+2;return longName}", id="ws-only"
        ),
    ],
)
def test_minify_js_passes_thread_through(options: JSMinify, expected: str) -> None:
    assert script(_SCRIPT, minify_js=options) == f"<script>{expected}</script>"


@pytest.mark.parametrize(
    "script_type",
    [
        pytest.param("", id="empty"),
        pytest.param("text/javascript", id="text-javascript"),
        pytest.param("application/javascript", id="application-javascript"),
        pytest.param("MODULE", id="module-uppercase"),
        pytest.param("application/x-javascript", id="x-javascript-mid-list"),
        pytest.param("text/javascript1.5", id="versioned"),
    ],
)
def test_javascript_types_are_minified(script_type: str) -> None:
    # top-level names are global, so they are kept; the JS pass still runs, visible as the
    # collapsed whitespace (a non-JS type would leave the spaces, see the test below). The
    # empty case pins type="" (an explicit empty type is still a classic script).
    source = f'<script type="{script_type}">var topLevel = 1 + 2</script>'
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert "var topLevel=3" in out


@pytest.mark.parametrize(
    "script_type",
    [
        pytest.param("application/json", id="json"),
        pytest.param("importmap", id="importmap"),
        pytest.param("text/html", id="template"),
        pytest.param("speculationrules", id="speculationrules"),
    ],
)
def test_non_javascript_types_pass_through(script_type: str) -> None:
    # a non-JS payload that happens to be valid JS (an array literal) must still be left
    # byte-for-byte: minifying JSON as JS could change quoting or numbers and break it
    body = "[1,    2,    3]"
    source = f'<script type="{script_type}">{body}</script>'
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert body in out


def test_unparseable_script_emitted_verbatim() -> None:
    # the JS parser cannot handle this; the script falls back to its original bytes rather
    # than breaking the surrounding document
    assert script("function( broken", minify_js=JSMinify()) == "<script>function( broken</script>"


def test_empty_script_is_unchanged() -> None:
    assert script("", minify_js=JSMinify()) == "<script></script>"


def test_other_rawtext_elements_are_not_touched() -> None:
    # style is raw text too, but never JavaScript: minify_js must not reach it
    source = "<style>a  {  color : red  }</style>"
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert "a  {  color : red  }" in out


def test_each_script_in_a_document_is_minified() -> None:
    source = "<script>var aaa = 1</script><script>var bbb = 2</script>"
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert out == "<div><script>var aaa=1</script><script>var bbb=2</script></div>"


def test_minified_script_is_idempotent() -> None:
    once = script(_SCRIPT, minify_js=JSMinify())
    assert script("function f(){var a=1+2;return a}", minify_js=JSMinify()) == once


def test_minify_js_defaults_off() -> None:
    assert Minify().minify_js is None


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(JSMinify(), id="mangle-fold"),
        pytest.param(JSMinify(mangle=False), id="no-mangle"),
        pytest.param(JSMinify(fold=False), id="no-fold"),
        pytest.param(JSMinify(mangle=False, fold=False), id="neither"),
    ],
)
def test_minify_js_getter_round_trips(config: JSMinify) -> None:
    # rebuild every mangle/fold combination so the getter's two toggle branches both run
    assert Minify(minify_js=config).minify_js == config


def test_minify_js_none_is_explicit_off() -> None:
    assert Minify(minify_js=None).minify_js is None


@pytest.mark.parametrize(
    ("options", "text"),
    [
        pytest.param(None, "minify_js=None", id="off"),
        pytest.param(JSMinify(), "minify_js=JSMinify(mangle=True, fold=True)", id="on"),
        pytest.param(JSMinify(fold=False), "minify_js=JSMinify(mangle=True, fold=False)", id="on-no-fold"),
        pytest.param(JSMinify(mangle=False), "minify_js=JSMinify(mangle=False, fold=True)", id="on-no-mangle"),
    ],
)
def test_minify_repr_includes_minify_js(options: JSMinify | None, text: str) -> None:
    assert repr(Minify(minify_js=options)).endswith(f", {text}, minify_css=False)")


def test_minify_equality_accounts_for_minify_js() -> None:
    assert Minify(minify_js=JSMinify()) == Minify(minify_js=JSMinify())
    assert Minify(minify_js=JSMinify()) != Minify()
    assert Minify(minify_js=JSMinify(fold=False)) != Minify(minify_js=JSMinify())


def test_minify_hash_distinguishes_minify_js() -> None:
    assert hash(Minify(minify_js=JSMinify())) != hash(Minify())
    assert hash(Minify(minify_js=JSMinify())) == hash(Minify(minify_js=JSMinify()))


def test_minify_js_rejects_non_jsminify() -> None:
    with pytest.raises(TypeError, match="minify_js must be a JSMinify or None"):
        Minify(minify_js=123)  # ty: ignore[invalid-argument-type]  # wrong type on purpose, to test the guard
