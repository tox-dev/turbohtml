# JS minifier corpus

These fixtures come from [tdewolff/minify](https://github.com/tdewolff/minify) `js/js_test.go` at commit
`80940e9e0aa13843dadf041ea059971044c03a66`. The source uses the MIT license; retain its copyright notice (Copyright (c)
2025 Taco de Wolff) when redistributing these fixtures.

The extraction used Go's `go/parser` and `strconv.Unquote` to preserve runtime string values. It excluded commented-out
cases.

| File | Source table | Schema | Rows | | ---------------------------- | ------------------- |
--------------------------------------------------------- | ---- | | `tdewolff_js.json` | `TestJS` |
`{input, expected, line}` | 732 | | `tdewolff_js_varrename.json` | `TestJSVarRenaming` | `{input, expected, line}` | 53
| | `tdewolff_js_version.json` | `TestJSVersion` | `{version, input, before, after, line}` | 3 |

`input` comes from the source's `js` field. `line` records the source line at the pinned commit. The version cases use
`before` below their stated ECMAScript version and `after` at or above it.
