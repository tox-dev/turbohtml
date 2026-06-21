# Architecture

turbohtml is a native HTML engine written in C with a thin, fully typed Python facade. The C code does the parsing,
querying, and serialization; the Python layer adds ergonomics and the migration shims. This document maps the source
tree so you know where a change belongs.

## Two layers

- **Python facade**: `src/turbohtml/`. Public modules users import: the package root re-exports the core types from the
  extension, and `query`, `sanitizer`, and `linkify` add higher-level APIs. `migration/` holds drop-in replacements for
  other libraries (`bleach`, `markupsafe`, `stdlib`). These files are small and hold no parsing logic.
- **C extension**: `src/turbohtml/_c/`. Everything compiles into one extension module, `turbohtml._html`. The Python
  facade calls into it; users never import `_html` directly. `_html.pyi` is its type stub.

## C subsystems (`src/turbohtml/_c/`)

| Directory    | Responsibility                                                                                                                                     |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core/`      | Module entry point (`module.c`), shared declarations and SWAR helpers (`common.h`), `ascii.h`.                                                     |
| `tokenizer/` | The WHATWG tokenizer state machine and its `Token`/`Tokenizer` bindings; character references.                                                     |
| `dom/`       | The tree builder (`tree.c`) and the node object model split by PyType (`node`, `element`, `leaf`, `document`, `formatters`).                       |
| `serialize/` | Output modes over a built tree: html5lib `#document`, minify, markdown, layout text, readability; plus escape/unescape and the markupsafe surface. |
| `query/`     | The selection engines, one per subdirectory: `css/` (selector matching), `xpath/` (XPath 1.0 + EXSLT), `find/` (`find`/`find_all`).                |
| `encoding/`  | Charset prescan and content-based encoding detection.                                                                                              |
| `features/`  | Transforms over a finished tree: `sanitize`, `linkify`, `links`, `annotation`.                                                                     |
| `data/`      | Generated static tables (tag and attribute atoms, HTML entities, TLDs). Regenerate with `tools/generate_*.py`.                                     |

## Data flow

Input bytes pass through `encoding/` (when detection is requested), then `tokenizer/` turns them into tokens, and
`dom/tree.c` builds the node tree with the WHATWG insertion-mode algorithm. From a built tree you query it (`query/`),
serialize it (`serialize/`), or transform it (`features/`). The Python node types in `dom/` wrap the C tree and expose
all of this to users.

## Conventions

- **Includes are subsystem-qualified.** With `-I src/turbohtml/_c`, a file includes `"tokenizer/statemachine.h"`, not a
  bare basename. The path tells you which subsystem owns the header.
- **Hot paths stay inlined across the split.** Some helpers are shared by several translation units in the same
  subsystem (the node traversal helpers in `dom/nodes.h`, the serialize primitives in `serialize/internal.h`). They are
  `static inline` in a shared header so each unit inlines its own copy. The serialize and tree-builder modes also share
  buffer and tree internals this way.
- **Generated tables have one owner.** The `data/` headers come from `tools/generate_*.py`; edit the generator, not the
  output. They are excluded from formatting and clang-tidy.
- **Coverage is 100%.** Both the gcc (Linux) and llvm-cov (macOS, Windows) gates require full line and branch coverage.
  An exclusion needs a written reason that testing is impossible.
- **Free-threading is supported.** Mutations take a per-tree critical section on the shared handle; it is a no-op on the
  GIL build. The 3.x`t` matrix and ThreadSanitizer guard this.

## Tests

`tests/` mirrors `src/turbohtml/_c/`: a test for code in `query/xpath/` lives in `tests/query/xpath/`. `conftest.py` and
the `html5lib-tests/` submodule stay at the root.

## Build

Meson drives the build; `meson.build` lists every C source and the Python files to install. Use `tox` for the gated
environments. The test, type, docs, and coverage jobs each run as a separate tox environment.
