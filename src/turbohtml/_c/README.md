# The C core

Every non-trivial algorithm lives here. The Python package is a thin shim that unpacks arguments, calls the `_html`
extension this directory compiles to, and wraps the result; removing the Python for a value would move the logic into C,
not change it.

## Layout

`meson.build` at the repo root lists every `.c` in this tree as one translation unit of the `_html` module. Includes are
root-relative to `_c/` (`include_directories('src/turbohtml/_c')`), so a header is included by its path from here
(`#include "serialize/buffer.h"`) regardless of which file includes it.

## Subsystem map

A subsystem keeps the same name across four places: the `_c/` directory that implements it, the public Python module
that exposes it, the `tests/` directory that exercises it, and the `docs/reference/` page that documents it. Use the row
to jump between them.

| `_c/`        | Python surface                                                 | `tests/`                  | `docs/reference/`               |
| ------------ | -------------------------------------------------------------- | ------------------------- | ------------------------------- |
| `tokenizer/` | `parse`, `parse_xml`, `saxparse`, `rewrite`                    | `tokenizer`               | `parsing`, `tokenizer`          |
| `dom/`       | `Node`, `treebuild`, `traverse`, `mutations`, `build`          | `dom`, `build`            | `nodes`, `build`                |
| `serialize/` | `Node.serialize`, `convert`/minify renderers                   | `serialize`               | `serialize`                     |
| `query/`     | `query`, `convert`, `transform`                                | `query`, `convert`        | `query`, `convert`, `transform` |
| `clean/`     | `clean` (sanitize, linkify)                                    | `clean`                   | `clean`                         |
| `extract/`   | `extract` (readability, structured data, dates, ...)           | `extract`                 | `extract`, `structured-data`    |
| `css/`       | `cssom`; `query`/`Node.select` selectors; `convert` CSS minify | `cssom`, `query`          | `cssom`                         |
| `encoding/`  | `detect`                                                       | `detect`, `encoding`      | `detect`                        |
| `url/`       | absolutization behind `clean` and `extract`                    | `url`                     | (under `clean`, `extract`)      |
| `validate/`  | `validate`, `conformance`                                      | `validate`, `conformance` | `validate`, `conformance`       |
| `core/`      | module init, atom interning, shared buffers                    | `core`                    | —                               |
| `data/`      | generated static tables                                        | —                         | —                               |
| `unicode/`   | normalization used by `url/` and `validate/`                   | (via those)               | —                               |

`css/` is the one umbrella that backs more than one Python surface, because the three CSS engines share a value model:
it holds `minify/` (the CSS minifier behind `convert`), `select/` (the selector matcher and the `css_to_xpath`
translation that `query`, `Node.select`, and `cssom` all match with), and `cssom/` (the cascade behind the `cssom`
module).

The public Python module names (`clean`, `cssom`, `detect`, `query`, ...) are the user-facing API and do not move; this
map is how the four names line up, not a promise that every subsystem is one file, one test dir, and one page.
