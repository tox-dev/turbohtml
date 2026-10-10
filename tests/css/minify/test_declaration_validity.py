"""Protect valid declarations when an invalid edge cannot join a shorthand."""

from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import minify_css, minify_css_inline

_OVERFLOW_EXPONENT: Final = "999999999999999999999"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a{border:}", "", id="empty-border"),
        pytest.param("a{border-color:,}", "", id="rendered-empty"),
        pytest.param("a{border-color:red;border-color:,}", "a{border-color:red}", id="rendered-empty-fallback"),
        pytest.param("p{background-color:#000;background:}", "p{background-color:#000}", id="empty-background"),
        pytest.param("a{color:red;color: !important}", "a{color:red}", id="empty-important"),
        pytest.param("a{color:red;color: /**/ }", "a{color:red}", id="comment-only"),
        pytest.param("a{border:/**/!/**/important}", "", id="comment-only-important"),
        pytest.param("a{border: ", "", id="empty-at-eof"),
        pytest.param("a{color:red;width:;height:1px}", "a{color:red;height:1px}", id="empty-between-valid"),
        pytest.param("a{--x:}", "a{--x:}", id="empty-custom"),
        pytest.param("a{--x: !important}", "a{--x: !important}", id="empty-custom-important"),
        pytest.param("a{--x: /**/}", "a{--x: }", id="comment-only-custom"),
        pytest.param("a{--x:1;--x:}", "a{--x:1;--x:}", id="custom-empty-fallback"),
        pytest.param("a{border:medium none currentcolor}", "a{border:none}", id="valid-border-defaults"),
        pytest.param("a{filter:blur(1px)/**/}", "a{filter:blur(1px)}", id="nonempty-filter-comment"),
    ],
)
def test_empty_declarations(source: str, expected: str) -> None:
    first: Final = minify_css(source)
    assert (first, minify_css(first)) == (expected, expected)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("border:;color:red", "color:red", id="inline-empty"),
        pytest.param("border:!important", "", id="inline-empty-important"),
        pytest.param("--x:;color:red", "--x:;color:red", id="inline-custom"),
    ],
)
def test_empty_inline_declarations(source: str, expected: str) -> None:
    first: Final = minify_css_inline(source)
    assert (first, minify_css_inline(first)) == (expected, expected)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "p { background-color: red; background: rg }", "p{background-color:red;background:rg}", id="color"
        ),
        pytest.param(
            "p { background-image: url(a); background: rg }",
            "p{background-image:url(a);background:rg}",
            id="image",
        ),
        pytest.param(
            "p { background-color: red!important; background: rg!important }",
            "p{background-color:red!important;background:rg!important}",
            id="important",
        ),
        pytest.param(
            "p { background-color: red; background: unknown-ident }",
            "p{background-color:red;background:unknown-ident}",
            id="hyphenated-ident",
        ),
        pytest.param("p{background-color:red;background:red}", "p{background:red}", id="named-color"),
        pytest.param("p{background-color:red;background:blue}", "p{background:blue}", id="equal-length-color"),
        pytest.param("p{background-color:red;background:green}", "p{background:green}", id="shorter-color-name"),
        pytest.param("p{background-color:red;background:black}", "p{background:#000}", id="shortened-color"),
        pytest.param("p{background-color:red;background:none}", "p{background:0 0}", id="none"),
        pytest.param("p{background-color:red;background:inherit}", "p{background:inherit}", id="css-wide"),
        pytest.param("p{background-color:red;background:url(a)}", "p{background:url(a)}", id="image-function"),
    ],
)
def test_background_dedup_respects_identifier_validity(source: str, expected: str) -> None:
    assert minify_css(source) == expected


def test_background_invalid_identifier_preserves_inline_longhand() -> None:
    assert minify_css_inline("background-color: red; background: rg") == "background-color:red;background:rg"


@pytest.mark.parametrize(
    ("property_name", "value", "expected_value"),
    [
        pytest.param("background-position", "left", "0", id="position"),
        pytest.param("background-size", "cover", "cover", id="size"),
        pytest.param("background-repeat", "repeat", "repeat", id="repeat"),
        pytest.param("background-origin", "border-box", "border-box", id="origin"),
        pytest.param("background-clip", "padding-box", "padding-box", id="clip"),
        pytest.param("background-attachment", "fixed", "fixed", id="attachment"),
    ],
)
def test_background_invalid_identifier_preserves_other_longhands(
    property_name: str, value: str, expected_value: str
) -> None:
    assert minify_css(f"p {{ {property_name}: {value}; background: rg }}") == (
        f"p{{{property_name}:{expected_value};background:rg}}"
    )


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        pytest.param(
            "background-color: red; background: rg",
            "background-color:red;background:rg",
            id="covered-longhand",
        ),
        pytest.param("background: rg; background: rg", "background:rg", id="identical-shorthand"),
    ],
)
def test_background_invalid_identifier_hashed_dedup(tail: str, expected: str) -> None:
    filler: Final = "".join(f"--v{index}:{index};" for index in range(40))
    assert minify_css(f"p{{ {filler}{tail} }}") == f"p{{{filler}{expected}}}"


@pytest.mark.parametrize(
    ("property_name", "edge"),
    [
        pytest.param("margin", "x", id="margin-unknown-keyword"),
        pytest.param("margin", ".", id="margin-dot"),
        pytest.param("margin", ".x", id="margin-dot-ident"),
        pytest.param("margin", "+", id="margin-plus-alone"),
        pytest.param("margin", "-", id="margin-minus-alone"),
        pytest.param("margin", "+x", id="margin-plus-ident"),
        pytest.param("margin", "-x", id="margin-minus-ident"),
        pytest.param("margin", "-.", id="margin-minus-dot"),
        pytest.param("margin", "-.x", id="margin-minus-dot-ident"),
        pytest.param("margin", "1e", id="margin-exponent-unit"),
        pytest.param("margin", "1e+", id="margin-incomplete-positive-exponent"),
        pytest.param("margin", "1e-", id="margin-incomplete-negative-exponent"),
        pytest.param("margin", "1e-x", id="margin-malformed-exponent"),
        pytest.param("margin", ".5", id="margin-fraction-unitless"),
        pytest.param("margin", ".51", id="margin-multiple-fraction-digits-unitless"),
        pytest.param("margin", "1.5", id="margin-decimal-unitless"),
        pytest.param("margin", f"1e{_OVERFLOW_EXPONENT}", id="margin-overflow-unitless"),
        pytest.param("margin", "auto auto", id="margin-extra-keyword"),
        pytest.param("margin", "1", id="margin-nonzero-unitless"),
        pytest.param("margin", "12", id="margin-multidigit-unitless"),
        pytest.param("margin", "1s", id="margin-time"),
        pytest.param("margin", "1furlong", id="margin-unknown-unit"),
        pytest.param("margin", "0furlong", id="margin-unknown-zero-unit"),
        pytest.param("margin", "min(1px,2%)", id="margin-function-fallback"),
        pytest.param("margin", "calc(1px + 2%)", id="margin-calc-fallback"),
        pytest.param("margin", "var(--edge)", id="margin-substitution"),
        pytest.param("padding", "auto", id="padding-auto"),
        pytest.param("padding", "-1px", id="padding-negative-length"),
        pytest.param("padding", "-.5%", id="padding-negative-percentage"),
        pytest.param("padding", "1", id="padding-nonzero-unitless"),
        pytest.param("padding", "x", id="padding-unknown-keyword"),
        pytest.param("padding", "1deg", id="padding-angle"),
        pytest.param("padding", "min(1px,2%)", id="padding-function-fallback"),
    ],
)
def test_box_merge_unsupported_edge(property_name: str, edge: str) -> None:
    source: Final = (
        f"a {{ {property_name}-top: 1px; {property_name}-right: 2px; "
        f"{property_name}-bottom: 3px; {property_name}-left: {edge} }}"
    )
    expected: Final = (
        f"a{{{property_name}-top:1px;{property_name}-right:2px;{property_name}-bottom:3px;{property_name}-left:{edge}}}"
    )
    first: Final = minify_css(source)
    assert (first, minify_css(first)) == (expected, expected)


@pytest.mark.parametrize("property_name", [pytest.param("margin", id="margin"), pytest.param("padding", id="padding")])
def test_box_merge_empty_edge(property_name: str) -> None:
    source: Final = (
        f"a{{{property_name}-top:1px;{property_name}-right:2px;{property_name}-bottom:3px;{property_name}-left:}}"
    )
    expected: Final = f"a{{{property_name}-top:1px;{property_name}-right:2px;{property_name}-bottom:3px}}"
    first: Final = minify_css(source)
    assert (first, minify_css(first)) == (expected, expected)


@pytest.mark.parametrize(
    ("property_name", "edges", "expected"),
    [
        pytest.param("margin", ("1px", "2px", "3px", "4px"), "1px 2px 3px 4px", id="margin-lengths"),
        pytest.param("margin", ("1px", "2px", "3px", ".5px"), "1px 2px 3px .5px", id="margin-leading-fraction"),
        pytest.param("margin", ("1px", "2px", "3px", "-.5px"), "1px 2px 3px -.5px", id="margin-negative-fraction"),
        pytest.param("margin", ("1px", "2px", "3px", "1.5px"), "1px 2px 3px 1.5px", id="margin-decimal"),
        pytest.param(
            "margin", ("1px", "2px", "3px", ".51px"), "1px 2px 3px .51px", id="margin-multiple-fraction-digits"
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"+1e{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px +1e{_OVERFLOW_EXPONENT}px",
            id="margin-positive-overflow-length",
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"-1e-{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px -1e-{_OVERFLOW_EXPONENT}px",
            id="margin-negative-underflow-length",
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"+.5e{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px +.5e{_OVERFLOW_EXPONENT}px",
            id="margin-positive-fraction-overflow",
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"-.5e{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px -.5e{_OVERFLOW_EXPONENT}px",
            id="margin-negative-fraction-overflow",
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"1.5E{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px 1.5E{_OVERFLOW_EXPONENT}px",
            id="margin-uppercase-exponent-overflow",
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"1e{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px 1e{_OVERFLOW_EXPONENT}px",
            id="margin-exponent-overflow",
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"1e-{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px 1e-{_OVERFLOW_EXPONENT}px",
            id="margin-exponent-underflow",
        ),
        pytest.param(
            "margin",
            ("1px", "2px", "3px", f"1e+{_OVERFLOW_EXPONENT}px"),
            f"1px 2px 3px 1e+{_OVERFLOW_EXPONENT}px",
            id="margin-positive-exponent-overflow",
        ),
        pytest.param("padding", ("1px", "2px", "3px", "4px"), "1px 2px 3px 4px", id="padding-lengths"),
        pytest.param("margin", ("1px", "2px", "3px", "auto"), "1px 2px 3px auto", id="margin-auto"),
        pytest.param("margin", ("-1px", "2px", "3px", "-4%"), "-1px 2px 3px -4%", id="margin-negatives"),
        pytest.param("padding", ("0px", "0em", "-0%", "0"), "0 0 0%", id="padding-negative-zero"),
        pytest.param("margin", ("1%", "2%", "3%", "4%"), "1% 2% 3% 4%", id="margin-percentages"),
        pytest.param("margin", ("1rem", "2em", "3vh", "4vw"), "1rem 2em 3vh 4vw", id="margin-known-units"),
        pytest.param("margin", ("initial", "initial", "initial", "initial"), "initial", id="margin-wide"),
        pytest.param("padding", ("revert-layer",) * 4, "revert-layer", id="padding-wide"),
        pytest.param("padding", ("1px!important",) * 4, "1px!important", id="padding-important"),
    ],
)
def test_box_merge_supported_edges(property_name: str, edges: tuple[str, ...], expected: str) -> None:
    source: Final = (
        "a{"
        + ";".join(
            f"{property_name}-{side}:{value}"
            for side, value in zip(("top", "right", "bottom", "left"), edges, strict=True)
        )
        + "}"
    )
    first: Final = minify_css(source)
    assert (first, minify_css(first)) == (f"a{{{property_name}:{expected}}}",) * 2


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "a{border:1px solid red;border:!important}",
            "a{border:1px solid red}",
            id="border-fallback",
        ),
        pytest.param(
            "a{margin-left:4px;margin-top:1px;margin-right:2px;margin-bottom:3px;margin-left:}",
            "a{margin:1px 2px 3px 4px}",
            id="empty-edge-keeps-valid-fallback",
        ),
        pytest.param(
            "a{margin-left:4px;margin-top:1px;margin-right:2px;margin-bottom:3px;margin-left:x}",
            "a{margin-left:4px;margin-top:1px;margin-right:2px;margin-bottom:3px;margin-left:x}",
            id="invalid-edge-keeps-valid-fallback",
        ),
    ],
)
def test_box_and_border_cascade_fallback(source: str, expected: str) -> None:
    first: Final = minify_css(source)
    assert (first, minify_css(first)) == (expected, expected)
