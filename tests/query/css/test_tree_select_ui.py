"""CSS selectors: the Selectors-4 §6.6/§11/§12 :scope, UI/form, :lang() and :dir()
pseudo-classes, plus the live-state pseudo-classes that match nothing."""

from __future__ import annotations

import pytest

from turbohtml import Element, parse

# every element carries an id so a match set is unambiguous regardless of the
# html/head/body wrappers the parser inserts
_FORM = (
    "<form>"
    "<input id=i-checkbox type=checkbox checked>"
    "<input id=i-radio type=radio checked>"
    "<input id=i-text-checked checked>"  # checked attr but text type: not :checked
    "<input id=i-plain>"
    "<input id=i-disabled disabled>"
    "<input id=i-required required>"
    "<input id=i-readonly readonly>"
    "<input id=i-submit type=submit>"
    "<input id=i-tel type=tel>"
    "<input id=i-color type=color>"
    "<button id=b-plain>x</button>"
    "<button id=b-disabled disabled>y</button>"
    "<button id=b-submit type=submit>s</button>"
    "<select id=sel><option id=o-sel selected>a</option><option id=o-dis disabled>b</option></select>"
    "<textarea id=ta></textarea>"
    "<textarea id=ta-ro readonly></textarea>"
    "<fieldset id=fs disabled><legend><input id=i-legend></legend><input id=i-in-fs></fieldset>"
    "<optgroup id=og disabled><option id=o-in-og>c</option></optgroup>"
    "</form>"
    "<p id=p-edit contenteditable>e</p>"
    "<p id=p-plain>p</p>"
    "<div id=d-noedit contenteditable=false>x</div>"
)


def _ids(html: str, selector: str) -> list[str]:
    matched = [e.attrs.get("id") for e in parse(html).select(selector)]
    return sorted(value for value in matched if isinstance(value, str))


def _scope_ids(elements: list[Element]) -> list[str]:
    """The id of each element in document order (the values are always present strings here)."""
    return [value for e in elements if isinstance(value := e.attrs.get("id"), str)]


@pytest.mark.parametrize(
    ("selector", "ids"),
    [
        pytest.param(":checked", ["i-checkbox", "i-radio", "o-sel"], id="checked"),
        pytest.param(
            ":disabled",
            ["b-disabled", "fs", "i-disabled", "i-in-fs", "o-dis", "o-in-og", "og"],
            id="disabled",
        ),
        pytest.param(
            ":enabled",
            [
                "b-plain",
                "b-submit",
                "i-checkbox",
                "i-color",
                "i-legend",
                "i-plain",
                "i-radio",
                "i-readonly",
                "i-required",
                "i-submit",
                "i-tel",
                "i-text-checked",
                "o-sel",
                "sel",
                "ta",
                "ta-ro",
            ],
            id="enabled",
        ),
        pytest.param(":required", ["i-required"], id="required"),
        pytest.param(
            ":optional",
            [
                "i-checkbox",
                "i-color",
                "i-disabled",
                "i-in-fs",
                "i-legend",
                "i-plain",
                "i-radio",
                "i-readonly",
                "i-submit",
                "i-tel",
                "i-text-checked",
                "sel",
                "ta",
                "ta-ro",
            ],
            id="optional",
        ),
        pytest.param(
            ":read-write",
            ["i-legend", "i-plain", "i-required", "i-tel", "i-text-checked", "p-edit", "ta"],
            id="read-write",
        ),
        pytest.param(":default", ["i-checkbox", "i-radio", "i-submit", "o-sel"], id="default"),
    ],
)
def test_form_pseudo_ids(selector: str, ids: list[str]) -> None:
    assert _ids(_FORM, selector) == ids


def test_read_only_is_the_complement_of_read_write() -> None:
    read_write = set(_ids(_FORM, ":read-write"))
    every = set(_ids(_FORM, "*"))
    assert set(_ids(_FORM, ":read-only")) == every - read_write


@pytest.mark.parametrize(
    "input_type",
    [
        pytest.param(t, id=t)
        for t in ("text", "search", "url", "tel", "email", "password", "number", "date", "month", "week", "time")
    ],
)
def test_mutable_input_types_are_read_write(input_type: str) -> None:
    assert _ids(f"<input id=x type={input_type}>", ":read-write") == ["x"]


@pytest.mark.parametrize(
    "input_type",
    [pytest.param(t, id=t) for t in ("checkbox", "radio", "submit", "button", "range", "color", "file", "hidden")],
)
def test_non_mutable_input_types_are_read_only(input_type: str) -> None:
    assert _ids(f"<input id=x type={input_type}>", ":read-write") == []
    assert _ids(f"<input id=x type={input_type}>", ":read-only") == ["x"]


@pytest.mark.parametrize(
    ("html", "selector", "ids"),
    [
        # :default submit button picks the first submit control of the form
        pytest.param(
            "<form><input id=a><input id=b type=submit><button id=c>x</button></form>",
            ":default",
            ["b"],
            id="default-first-submit-input",
        ),
        pytest.param(
            "<form><button id=a>x</button><input id=b type=submit></form>",
            ":default",
            ["a"],
            id="default-first-submit-is-typeless-button",
        ),
        pytest.param("<input id=a type=submit>", ":default", [], id="default-submit-without-form"),
        # contenteditable is editable only with no/empty/true value, and is not inherited
        pytest.param("<div id=a contenteditable=true><span id=b>x</span></div>", ":read-write", ["a"], id="ce-true"),
        pytest.param("<div id=a contenteditable=false>x</div>", ":read-write", [], id="ce-false"),
        # an option inside a non-disabled optgroup stays enabled
        pytest.param("<optgroup><option id=a>x</option></optgroup>", ":enabled", ["a"], id="option-enabled"),
    ],
)
def test_ui_pseudo_cases(html: str, selector: str, ids: list[str]) -> None:
    assert _ids(html, selector) == ids


@pytest.mark.parametrize(
    ("html", "selector", "ids"),
    [
        pytest.param('<span id=a lang="en">x</span>', ":lang(en)", ["a"], id="exact"),
        pytest.param('<span id=a lang="en-US">x</span>', ":lang(en)", ["a"], id="prefix-on-hyphen"),
        pytest.param('<span id=a lang="EN-GB">x</span>', ":lang(en)", ["a"], id="case-insensitive"),
        pytest.param('<div lang="fr"><span id=a>x</span></div>', ":lang(fr)", ["a"], id="inherited"),
        pytest.param('<span id=a lang="de">x</span>', ":lang(en, de)", ["a"], id="comma-list"),
        pytest.param("<span id=a lang='en'>x</span>", ":lang('en')", ["a"], id="quoted-range"),
        pytest.param('<span id=a lang="en">x</span>', ":lang(en-US)", [], id="range-more-specific-than-tag"),
        pytest.param('<span id=a lang="english">x</span>', ":lang(en)", [], id="no-hyphen-boundary"),
        pytest.param("<span id=a>x</span>", ":lang(en)", [], id="no-lang-attribute"),
        pytest.param('<span id=a lang="">x</span>', ":lang(en)", [], id="empty-lang-attribute"),
        # RFC 4647 §3.3.2 extended filtering: a non-matching, non-singleton subtag is
        # skipped, so a range's subtags need only appear in the tag in order
        pytest.param('<span id=a lang="en-Latn-GB">x</span>', ":lang(en-GB)", ["a"], id="extended-skip-script"),
        pytest.param('<span id=a lang="en-a-bbb">x</span>', ":lang(en-bbb)", [], id="extended-singleton-blocks"),
        # a '*' wildcard subtag: leading '*' matches any determined language, and an
        # interior/quoted or escaped '*' passes through per Selectors-4 §14
        pytest.param('<span id=a lang="de-DE">x</span>', ":lang('*')", ["a"], id="wildcard-any-language"),
        pytest.param("<span id=a>x</span>", ":lang('*')", [], id="wildcard-needs-a-language"),
        pytest.param('<span id=a lang="en-US">x</span>', ":lang('*-US')", ["a"], id="wildcard-primary-region"),
        pytest.param('<span id=a lang="en-GB">x</span>', ":lang('*-US')", [], id="wildcard-primary-region-miss"),
        pytest.param('<span id=a lang="de-DE">x</span>', r":lang(\*-DE)", ["a"], id="wildcard-escaped-asterisk"),
        pytest.param('<span id=a lang="en-Latn-US">x</span>', ":lang('*-US')", ["a"], id="wildcard-skips-script"),
        # an interior '*' subtag passes through; a non-wildcard single-char subtag matches literally
        pytest.param('<span id=a lang="de-Latn-DE">x</span>', ":lang('de-*-DE')", ["a"], id="wildcard-interior"),
        pytest.param('<span id=a lang="en-a-bbb">x</span>', ":lang('en-a')", ["a"], id="single-char-subtag"),
    ],
)
def test_lang_pseudo(html: str, selector: str, ids: list[str]) -> None:
    assert _ids(html, selector) == ids


@pytest.mark.parametrize(
    ("html", "selector", "ids"),
    [
        pytest.param("<p id=a dir=rtl>x</p>", ":dir(rtl)", ["a"], id="explicit-rtl"),
        pytest.param("<p id=a dir=ltr>x</p>", ":dir(ltr)", ["a"], id="explicit-ltr"),
        pytest.param("<div dir=rtl><span id=a>x</span></div>", ":dir(rtl)", ["a"], id="inherited-rtl"),
        pytest.param("<p id=a dir=auto>אב</p>", ":dir(rtl)", ["a"], id="auto-resolves-rtl"),
        pytest.param("<p id=a dir=auto>abc</p>", ":dir(ltr)", ["a"], id="auto-resolves-ltr"),
        pytest.param("<p id=a dir=auto>123</p>", ":dir(ltr)", ["a"], id="auto-neutral-defaults-ltr"),
        pytest.param("<bdi id=a>اب</bdi>", ":dir(rtl)", ["a"], id="bdi-defaults-auto-rtl"),
        pytest.param("<bdi id=a>x</bdi>", ":dir(ltr)", ["a"], id="bdi-defaults-auto-ltr"),
        pytest.param("<div dir=rtl><p id=a dir=bogus>x</p></div>", ":dir(rtl)", ["a"], id="invalid-dir-inherits"),
        pytest.param("<p id=a>x</p>", ":dir(ltr)", ["a"], id="default-ltr"),
        pytest.param("<p id=a dir=ltr>x</p>", ":dir(rtl)", [], id="ltr-not-rtl"),
        pytest.param("<p id=a dir=ltr>x</p>", ":dir(sideways)", [], id="unknown-direction-matches-nothing"),
    ],
)
def test_dir_pseudo(html: str, selector: str, ids: list[str]) -> None:
    assert _ids(html, selector) == ids


def test_dir_auto_descends_for_strong_character() -> None:
    # the first strong character sits inside a nested element's text
    assert _ids("<p id=a dir=auto><span>123 <b>א</b></span></p>", ":dir(rtl)") == ["a"]


@pytest.mark.parametrize(
    ("first_char", "direction"),
    [
        pytest.param("א", "rtl", id="hebrew"),  # main RTL block
        pytest.param("ا", "rtl", id="arabic"),  # noqa: RUF001  # main RTL block
        pytest.param("יִ", "rtl", id="hebrew-presentation-form"),  # presentation-form range
        pytest.param("a", "ltr", id="ascii-letter"),
        pytest.param("À", "ltr", id="latin1-letter"),  # strong L above U+00C0
        pytest.param("中", "ltr", id="cjk"),  # >U+08FF, not an RTL range
        pytest.param("Ａ", "ltr", id="fullwidth-latin"),  # noqa: RUF001  # >U+FEFF, not an RTL range
        pytest.param("5", "ltr", id="digit-is-neutral-defaults-ltr"),
    ],
)
def test_dir_auto_first_strong_character(first_char: str, direction: str) -> None:
    assert _ids(f"<p id=a dir=auto>{first_char}</p>", f":dir({direction})") == ["a"]


def test_dir_auto_skips_neutral_then_resolves() -> None:
    # leading neutral characters are skipped until the first strong one decides
    assert _ids("<p id=a dir=auto>12 34 א</p>", ":dir(rtl)") == ["a"]


@pytest.mark.parametrize(
    ("html", "selector", "ids"),
    [
        # a foreign-namespace element is never an HTML form control or link
        pytest.param("<svg></svg>", ":disabled", [], id="svg-not-disabled"),
        pytest.param("<svg></svg>", ":checked", [], id="svg-not-checked"),
        pytest.param("<svg></svg>", ":default", [], id="svg-not-default"),
        # a disabled fieldset with no legend disables its controls directly
        pytest.param("<fieldset disabled><input id=x></fieldset>", ":disabled", ["x"], id="fieldset-no-legend"),
        # the first submit control may be reached past a non-element node or nested
        pytest.param("<form><!--c--><input id=x type=submit></form>", ":default", ["x"], id="default-past-comment"),
        pytest.param(
            "<form><div><button id=x type=submit>g</button></div></form>", ":default", ["x"], id="default-nested"
        ),
        # a non-HTML element inside a form is skipped while scanning for the submit
        pytest.param(
            "<form><svg></svg><button id=x type=submit>g</button></form>", ":default", ["x"], id="default-skips-foreign"
        ),
        # :lang() tolerates whitespace around the list and each range
        pytest.param('<span id=a lang="en">x</span>', ":lang(en )", ["a"], id="lang-trailing-space"),
        pytest.param('<span id=a lang="de">x</span>', ":lang(en , de)", ["a"], id="lang-range-trailing-space"),
    ],
)
def test_ui_pseudo_coverage_cases(html: str, selector: str, ids: list[str]) -> None:
    assert _ids(html, selector) == ids


@pytest.mark.parametrize(
    ("html", "selector", "ids"),
    [
        # a foreign-namespace control carries the same tag atom but is never an HTML
        # form control, so every UI pseudo-class rejects it
        pytest.param("<svg><input type=checkbox checked id=x></svg>", ":checked", [], id="foreign-checked"),
        pytest.param("<svg><input id=x></svg>", ":enabled", [], id="foreign-enabled"),
        pytest.param("<svg><input disabled id=x></svg>", ":disabled", [], id="foreign-disabled"),
        pytest.param("<svg><input required id=x></svg>", ":required", [], id="foreign-required"),
        pytest.param("<svg><input id=x></svg>", ":optional", [], id="foreign-optional"),
        pytest.param("<svg><input id=x></svg>", ":read-write", [], id="foreign-read-write-input"),
        pytest.param("<svg><textarea id=x></textarea></svg>", ":read-write", [], id="foreign-read-write-textarea"),
        pytest.param("<svg><input type=submit id=x></svg>", ":default", [], id="foreign-default"),
        # a valueless type attribute behaves like a missing one
        pytest.param("<input type checked id=x>", ":checked", [], id="valueless-type-not-checked"),
        # a checkbox/radio without the checked attribute is not :checked
        pytest.param("<input type=checkbox id=x>", ":checked", [], id="unchecked-checkbox"),
        pytest.param("<input type=radio id=x>", ":checked", [], id="unchecked-radio"),
        pytest.param("<input type id=x>", ":read-write", ["x"], id="valueless-type-is-mutable"),
        # submit-control variants: image input, valueless button type, non-submit button
        pytest.param("<form><input type=image id=x></form>", ":default", ["x"], id="default-image-input"),
        pytest.param(
            "<form><button type id=x>g</button></form>", ":default", ["x"], id="default-button-valueless-type"
        ),
        pytest.param(
            "<form><button type=button id=a>n</button><button type=submit id=x>s</button></form>",
            ":default",
            ["x"],
            id="default-skips-non-submit-button",
        ),
        # a disabled textarea is read-only
        pytest.param("<textarea disabled id=x></textarea>", ":read-write", [], id="textarea-disabled-read-only"),
        # an optgroup/fieldset is disabled only by its own attribute
        pytest.param(
            "<optgroup id=a></optgroup><fieldset id=b></fieldset>", ":disabled", [], id="optgroup-fieldset-enabled"
        ),
        # contenteditable with an explicit empty-string value is editable
        pytest.param('<div contenteditable="" id=x>e</div>', ":read-write", ["x"], id="contenteditable-empty-string"),
        # a disabled fieldset with text, foreign and non-legend children before the legend
        pytest.param(
            "<fieldset disabled>t<svg></svg><div></div><legend><input id=a></legend><input id=b></fieldset>",
            ":disabled",
            ["b"],
            id="fieldset-messy-children-legend-exempt",
        ),
        # :lang() argument quoting and edge ranges
        pytest.param("<span lang id=x>v</span>", ":lang(en)", [], id="lang-valueless-attr"),
        pytest.param('<span lang="en" id=x>v</span>', ':lang("en")', ["x"], id="lang-double-quoted"),
        pytest.param('<span lang="en" id=x>v</span>', ":lang('en\")", [], id="lang-mismatched-quotes"),
        pytest.param('<span lang="x" id=a>v</span>', ":lang(x)", ["a"], id="lang-single-char-range"),
        pytest.param('<span lang="de" id=a>v</span>', ":lang(en, , de)", ["a"], id="lang-empty-range-in-list"),
        # a valueless or foreign dir resolves to the default ltr
        pytest.param("<p dir id=x>v</p>", ":dir(ltr)", ["x"], id="dir-valueless-attr"),
        pytest.param("<svg><text id=x>v</text></svg>", ":dir(ltr)", ["x"], id="dir-foreign-default-ltr"),
        # a control inside a fieldset without the disabled attribute stays enabled
        pytest.param("<fieldset><input id=x></fieldset>", ":disabled", [], id="enabled-fieldset-control"),
        # an unchecked checkbox is not the default checked control
        pytest.param("<form><input type=checkbox id=x></form>", ":default", [], id="default-unchecked-checkbox"),
    ],
)
def test_pseudo_branch_coverage(html: str, selector: str, ids: list[str]) -> None:
    assert _ids(html, selector) == ids


def test_detached_element_resolves_lang_and_dir_to_no_ancestor() -> None:
    # an element with no parent walks to NULL: :lang() finds no language, :dir()
    # falls back to the document default
    bare = Element("span")
    assert bare.matches(":lang(en)") is False
    assert bare.matches(":dir(ltr)") is True
    assert bare.matches(":dir(rtl)") is False


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(s, id=s.lstrip(":"))
        for s in (
            ":hover",
            ":focus",
            ":focus-within",
            ":focus-visible",
            ":active",
            ":target",
            ":target-within",
            ":visited",
        )
    ],
)
def test_live_state_pseudo_matches_nothing(selector: str) -> None:
    # a static tree has no interaction or navigation state, so these never match
    assert parse("<a href='/x'>link</a><input>").select(selector) == []


def test_live_state_pseudo_composes_with_not() -> None:
    # :not() of an always-empty pseudo keeps every element, so the bad arm does not
    # turn the whole selector into an error
    assert [e.tag for e in parse("<a href='/x'>l</a>").select("a:not(:hover)")] == ["a"]


def test_scope_is_the_query_root() -> None:
    doc = parse("<section id=s><p id=p1>a<a id=link>x</a></p><p id=p2>b</p></section>")
    section = doc.select_one("section")
    p1 = doc.select_one("#p1")
    link = doc.select_one("#link")
    assert section is not None
    assert p1 is not None
    assert link is not None
    # select returns descendants, so :scope (the root itself) is never in the result,
    # but it anchors a relative selector
    assert section.select(":scope") == []
    assert _scope_ids(section.select(":scope > p")) == ["p1", "p2"]
    assert _scope_ids(section.select(":scope a")) == ["link"]
    # matches()/closest() scope :scope to the node they are called on, so every
    # element matches :scope on itself and closest(:scope) returns that element
    assert section.matches(":scope") is True
    assert p1.matches(":scope") is True
    closest = link.closest(":scope")
    assert closest is not None
    assert closest.attrs.get("id") == "link"
    # a descendant query rooted at p1 scopes :scope to p1, so it is not in the result
    assert p1.select(":scope") == []
    assert _scope_ids(p1.select(":scope > a")) == ["link"]


def test_document_scope_falls_back_to_the_document_element() -> None:
    # rooted at the document, :scope resolves to the document element like :root does
    # (issue #351): the html element and every compound built on it now match
    doc = parse("<!doctype html><html><body><div id=d><p id=p>x</p></div></body></html>")
    assert [e.tag for e in doc.select(":scope")] == ["html"]
    assert [e.tag for e in doc.select(":scope > body > div")] == ["div"]
    assert [e.attrs.get("id") for e in doc.select(":scope div > p")] == ["p"]


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(":checked(x)", id="checked-takes-no-args"),
        pytest.param(":disabled()", id="disabled-takes-no-args"),
        pytest.param(":lang", id="lang-without-args"),
        pytest.param(":lang.x", id="lang-name-then-non-paren"),
        pytest.param(":lang()", id="lang-empty"),
        pytest.param(":lang(  )", id="lang-whitespace-only"),
        pytest.param(":lang(en", id="lang-unterminated"),
        pytest.param(":dir", id="dir-without-args"),
        pytest.param(":dir()", id="dir-empty"),
        pytest.param(":dir(rtl", id="dir-unterminated"),
        pytest.param(":dir(ltr x)", id="dir-junk-after-arg"),
        pytest.param(":hover(x)", id="live-state-takes-no-args"),
    ],
)
def test_invalid_ui_selectors_raise(selector: str) -> None:
    with pytest.raises(ValueError, match="selector"):
        parse("<p>").select(selector)
