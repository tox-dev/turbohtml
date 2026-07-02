# JS minifier test corpora

Fixtures used by the Python tests for the C JavaScript minifier. Each file is a JSON list of objects extracted verbatim
from a third-party project's test suite. Strings are decoded to their literal runtime values (Go interpreted-string
escapes resolved, Go raw backtick-strings kept byte-for-byte), so they can be fed directly to the minifier and compared
against `expected`.

## tdewolff/minify

- Source: https://github.com/tdewolff/minify
- Commit: `80940e9e0aa13843dadf041ea059971044c03a66`
- File: `js/js_test.go`
- License: MIT - Copyright (c) 2025 Taco de Wolff. The MIT permission notice must be preserved in any redistribution.
  These fixtures are derived data from that test file; attribution is retained here.

Extraction was done by parsing the Go source with `go/parser` and decoding every string literal with `strconv.Unquote`,
so both interpreted (`"..."`) and raw (`` `...` ``) strings are exact. Commented-out test cases in the source are
intentionally excluded.

| File                         | Source table        | Schema                                                    | Rows |
| ---------------------------- | ------------------- | --------------------------------------------------------- | ---- |
| `tdewolff_js.json`           | `TestJS`            | `{input, expected, line}`                                 | 732  |
| `tdewolff_js_varrename.json` | `TestJSVarRenaming` | `{input, expected, line}` (alphabet var renaming enabled) | 53   |
| `tdewolff_js_version.json`   | `TestJSVersion`     | `{version, input, before, after, line}`                   | 3    |

Field notes:

- `input` is the source field `js` (renamed for clarity).
- `line` is the 1-based line number of the case in `js_test.go` at the pinned commit.
- `tdewolff_js_version.json` is ECMAScript-version dependent: minifiers targeting `< version` must produce `before`,
  those targeting `>= version` must produce `after`.
