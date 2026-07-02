# JavaScript minification - design

Status: implemented · Tracking: #343 · Branch: `feat/js-minify`

## Why

turbohtml minifies HTML and folds the document tree, but inline `<script>` text is copied byte-for-byte
(`serialize/minify.c`, the `is_rawtext_element` branch). To shrink real pages we need to minify the JavaScript itself,
and to stand as a migration target for `terser`/`esbuild`/`oxc` we need a standalone `minify_js`.

This is turbohtml's own minifier - a native-C subsystem built on turbohtml's existing infrastructure and following its
design principles, not a port of any existing tool. We studied the ecosystem (esbuild, oxc, swc, tdewolff/minify,
Crockford's `jsmin`) to learn which problems are real and which approaches are dead ends; the implementation, the data
structures, and the code are ours. Where this document cites a competitor it is citing a *lesson* or a *validation
oracle*, never a thing to copy.

The first lesson rules out the cheap path: a tokenizer-only minifier cannot decide `/` (regex) versus `/` (division) -
that needs grammar position - so `jsmin`-class tools are *known-incorrect*. turbohtml's "spec is authoritative" rule
forbids a known-incorrect heuristic, so we build a real front end: lex → parse → optimize → print. turbohtml already
runs exactly this shape, correctly and fast, in its XPath engine; the JS minifier is the same architecture applied to a
different grammar.

## Architecture

A self-contained subsystem under `src/turbohtml/_c/serialize/js/`, structured like the XPath engine that already lives
in the tree (`query/xpath/` - lexer, parser, arena AST, an `internal.h` contract, a public `.h`). That shape is
turbohtml's established pattern for a compiled mini-language, proven and coverage-gated; we reuse it rather than invent
a new one. All units compile into the existing `_html` module; TU-local symbols carry the `jm_` prefix, the one public
entry point carries `th_` - the project's naming convention.

```
source UCS4
  ├─ lex      js/lexer.c     token stream, regex/template rescan API, newline flag
  ├─ parse    js/parser.c    recursive-descent + precedence-climbing expressions
  │           js/ast.c       flat arena of jm_node; scope tree; interned symbols
  ├─ optimize js/fold.c      constant fold / boolean algebra / DCE to a fixpoint
  │           js/mangle.c    scope liveness → slot coloring → short-name renaming
  └─ print    js/printer.c   AST → sbuf, minimal whitespace, semicolons, parens
public: js/minify.h (th_js_minify)   internal: js/internal.h (jm_* contract)
```

It is built from turbohtml's own primitives, not new machinery:

- **Arena allocation** - a bump-allocator block list (`jm_arena`) in the same style as the document tree's arena
  (`dom/tree_internal.h`): AST nodes, scope structs and symbol bytes are freed in one shot when minification ends, no
  per-node frees.
- **Symbol interning** - identifiers intern to integer ids the way tag/attribute atoms do, so every binding/reference
  comparison is an integer compare and renaming is a single table write, never an AST rewrite.
- **The shared `sbuf`** - the printer emits into the serializer's existing UCS4 buffer, so the HTML path appends
  minified script text into the buffer it is already filling, under the same per-tree critical-section lock as every
  other serialize mode (free-threading-safe by construction).
- **SWAR / `[256]` classification** - identifier and whitespace classification use the same byte-table / lane-probe
  idioms the HTML tokenizer and serializer use.

### Design decisions

The hard correctness rules below are properties of the ECMAScript grammar, not of any tool; every correct front end
obeys them. These are the choices we make in obeying them:

- **Regex vs division - decided by the parser, not the lexer.** The lexer never guesses: `/` always lexes to `JT_DIV` /
  `JT_DIV_ASSIGN`. The parser, which alone knows whether a value or an operator is expected, calls
  `jm_lex_rescan_regex()` to rewind and re-read the token as a regex literal. This is the same position-driven
  disambiguation turbohtml's XPath lexer already does for `*` and the operator names - our own established technique,
  reused.
- **Template `${…}` continuation - same rewind.** A `}` re-reads as a template continuation (`jm_lex_rescan_template`)
  only when the parser, recursing through a substitution expression, asks for it. The lexer holds no template stack;
  nesting is the parser's recursion. (Lesson confirmed against esbuild/tdewolff, which both drive the same rescan from
  their parsers.)
- **ASI - sidestepped by always emitting an explicit `;`.** Minified output is a single line with one explicit semicolon
  between every statement pair (the trailing one before `}` or EOF is simply never flushed). With no newlines and an
  explicit separator, the leading-token continuation hazard never arises on statement joins. The only residual is an
  expression statement that itself begins with `{`/`function`/`class`/`let[`; we parenthesize it, detected by comparing
  the output offset recorded at statement start against the current offset at the hazardous node - one integer, no
  lookahead.
- **Restricted productions kept intact.** `return`/`throw`/`break`/`continue`/`yield` keep their argument on the same
  logical line (we never emit a newline there); postfix `++`/`--` print adjacent to their operand. Contextual keywords
  (`get`/`set`/`async`/`static`/`*`) are structured AST flags, not text, so the identifier-adjacency spacer reinserts
  exactly the one space that preserves meaning.

## Phases (commits on one draft PR)

Each phase is one gated commit (turbohtml's phased-commits / single-PR convention). The PR head is what CI gates;
intermediate commits build, and a phase's user-visible value lands with its tests and its rung of the corpus gate.

1. **Lexer** *(landed)* - `JT_*` token kinds, the `jm_lexer` struct, identifier / whitespace classification (ASCII fast
   path; code points ≥ 0x80 are identifier characters except the Unicode space and line-terminator set), scanners for
   number/string/template/regex/punctuator, the line-terminator flag for ASI, and the parser-driven rescan entry points.
   Tested through a canonical token-dump hook.
1. **Parser + AST** - a flat arena of `jm_node` (tagged, `int32` child/sibling indices, like the XPath `xn`), the
   ECMAScript statement and expression grammar with precedence climbing, a scope tree (`jm_scope`), and interned symbols
   with use counts resolved as bindings link to references. An S-expression dump hook lets parser tests diff the tree,
   as the XPath parser tests do.
1. **Printer** - AST → `sbuf`: minimal whitespace, deferred semicolons, the statement-start parenthesisation above,
   precedence-driven paren minimization, identifier-adjacency spacing, number canonicalization (`0x0d`→`13`, drop `_`
   separators) and string quote/escape minimization. End of phase: a correct whitespace minifier (≈30-50% smaller).
1. **Mangle** - build scope liveness, color scopes so a short name is reused wherever two bindings are never
   simultaneously live, rank by use frequency, and allocate the shortest legal names (a bijective base-54-then-64
   numeral: 54 first characters because an identifier cannot start with a digit), skipping reserved words, referenced
   globals, exports, and anything reachable from `with` or direct `eval`. The largest incremental win (≈+10-20%).
1. **Fold / DCE** - peephole passes to a fixpoint: constant folding, boolean algebra (`!0`/`!1`, `void 0`/`undefined`),
   conditional and logical minimization, and unreachable-branch / unused-binding removal, each gated by a side-effect
   predicate. Long-tail single-digit %.
1. **Bindings + HTML hook** - `turbohtml.minify_js(source) -> str`; a `minify_js` flag on the `Minify` options object
   threaded through `dom/formatters.c`, `dom/node.c` and `th_minify_opts`; route `<script>` rawtext through
   `th_js_minify` in `serialize_minify`. Update the `.pyi` stubs and `_render.py`.
1. **Tests / docs / gates** - see below.

## Correctness gates

turbohtml's HTML minifier gates on *reparse idempotence*. JS minification is lossy w.r.t. source bytes, so our invariant
is **semantic equivalence**, and we validate it against **the competing projects' own test corpora** rather than an
oracle we invent - the same playbook that took the CSS minifier to 97% against tdewolff's suite. The corpora are
vendored as pinned shallow submodules under `tools/bench-data` (never runtime-downloaded), per the datasets convention:

- **tdewolff/minify JS golden table** (`js/js_test.go`) - its `{input, expected}` pairs become parametrized cases; each
  phase must match or document a deliberate divergence. The closest architectural sibling and the most direct gate.
- **test262** (the official ECMA-262 conformance suite) - its lexer/parser fixtures must tokenize and parse every
  grammar production (each fixture lexes to EOF with no spurious `ERROR`; post-parser, round-trips through our printer).
- **terser** (`test/compress/*.js`) and **esbuild snapshot tests** - behavior fixtures for the transforms; we compare
  semantics, not bytes.
- **Real library bundles** (jquery, lodash, react, vue, d3) for size and robustness.

On top of the borrowed corpora, our own gates:

- **Differential AST-equivalence** - `minify(parse(src))` re-parsed equals `parse(src)` modulo
  renaming/whitespace/documented folds, over every corpus.
- **Idempotence** - `minify(minify(src)) == minify(src)` over every corpus.
- **Differential execution vs `terser`/`esbuild`** (dev-only, not imported in the CI unit env per the optional-dep
  rule): run original and minified under a JS engine on a behavior corpus and assert identical observable output.
- **Per-transform string tests**, `pytest.param` ids, fully typed.

Phase 1 lands the first rung: the lexer tests diff a canonical token dump, and the test262 lexer fixtures attach here.
The execution/AST gates attach as the parser (Phase 2) and printer (Phase 3) land.

100% line+branch coverage on the new C (gcc + llvm-cov) and Python, the tree's standard bar. `GCOVR_EXCL` only with a
written justification.

## Spec conformance

ECMA-262 is the authority for correctness; the borrowed corpora only guard against regressions. Each transform below
names the clause that licenses it and the guard that clause requires.

- **Whitespace and comment removal (§12.2, §12.4).** Output is one line with an explicit `;` between statements, so ASI
  (§12.10) does not have to fire. The token-adjacency guard inserts one space wherever maximal munch (§12.8) would merge
  two tokens, as in `a in b`, `typeof x`, `return x`, `1 instanceof`.
- **Numeric canonicalisation (§12.9.3).** Each rewrite (`0x0d`→`13`, separator removal, `100000`→`1e5`, `0.5`→`.5`)
  changes digits without changing the mathematical value, so the `Number` matches. A number before `.` keeps a wrapping
  paren so the dot does not re-lex as a decimal point.
- **BigInt separator removal (§12.9.3.1).** A BigInt carries no dot or exponent, so dropping `_` (`1_000n`→`1000n`) is
  its only value-preserving shortening.
- **`true`/`false`→`!0`/`!1` (§13.5.7, §12.7.2).** The rewrites evaluate to the booleans, and `true`/`false` are
  reserved words that cannot be a binding, so they need no shadow check.
- **`undefined`→`void 0` (§13.4.2, §19.1.1).** `void` yields `undefined`. The fold runs only when no binding named
  `undefined` exists anywhere in the program, since a local `undefined` shadows the global.
- **Constant short-circuit and dead-code elimination (§13.13, §13.14, §14.6).** A branch drops only when its condition
  is a pure constant or sits on the untaken side of a short circuit, and a statement subtree drops only when it hoists
  no `var` or function (§10.2.11, Annex B.3.3), because a hoisted name outlives a dead branch.
- **Identifier mangling (§8, §9, §14.2).** A binding renames only when every reference resolves to it at parse time.
  `with` (§14.11) and direct `eval` (§19.2.1) leave the whole program unrenamed; indirect `eval` runs in global scope
  and stays safe. Globals, exports, and property names keep their names.
- **Named function and class expression names (§15.2.5, §15.7).** The name of `(function f(){…})` or `(class C{…})`
  binds only inside its own body, so it renames with its self-references. A declaration name stays: its scope observes
  it, and Annex-B block-function hoisting can move it.
- **Labels (§14.13).** A label is a separate namespace from variables, and `break`/`continue` only reaches a
  lexically-enclosing label, so each label renames with its jumps and may reuse a variable's short name. Past 52 nesting
  levels a label keeps its source name rather than take a multi-character name that could spell a reserved word.
- **Parenthesisation (§13).** The printer drops a paren once precedence and associativity make it redundant and keeps
  the rest. Four groupings stay load-bearing even though precedence alone would discard them: a `UnaryExpression` left
  of `**` (§13.6, `(-2)**2`), a `||`/`&&` operand next to `??` in either direction (§13.13, `(a||b)??c`), and an
  optional chain used as a template tag (§13.3.1, `` (a?.b)`t`  ``) or as a `new` callee (§13.3.5, `new (a?.b)()`).

Two consequences are deliberate. The fold pass does no arithmetic folding (`1+2`→`3`, string concatenation): an
equivalence proof across `+0`/`-0`, `NaN`, IEEE-754 rounding (§6.1.6), and `valueOf`/`toString` coercion (§7.1) is where
minifiers ship miscompiles, so the pass stays at boolean, `undefined`, and short-circuit rewrites. Mangling also changes
`Function.prototype.name` and `toString` output, as renaming any local already does; code that reflects on identifier
text should opt out of minification.

## API

```python
import turbohtml

turbohtml.minify_js("const x = 1 ;\n foo( x )")  # -> "const x=1;foo(x)"

html = turbohtml.parse(page)
html.serialize(layout=turbohtml.Minify(minify_js=True))  # minifies inline <script>
```

`minify_js=True` only touches `<script>` whose type is absent / `text/javascript` / `module`; `application/json`, import
maps and unknown types stay verbatim.

## Non-goals

Bundling, module resolution, source maps, cross-module tree-shaking, TypeScript/JSX stripping. Single-file minification
only.

## Positioning

Migration guides (`migration/terser.rst`, `migration/esbuild.rst`) per the coverage-gap strategy: the same single-file
minify call, native, fully typed, no Node runtime. Benchmarks in `performance.rst` against `terser`, `esbuild`,
`oxc-minify` and `tdewolff/minify` on the shared corpus.
