# XPath engine design (issue #179)

Native-C XPath 1.0 over the turbohtml DOM, so `lxml` / `parsel` / `pyquery` / `html5-parser` users keep their XPath when
they migrate. This document fixes the architecture before implementation; it is the synthesis of a cross-language study
of libxml2 (`xpath.c`), pugixml, Rust `sxd-xpath`/`xee`, and Go `antchfx/xpath`+`htmlquery`.

## Principles

1. **Python is the top layer only.** Every byte of parsing, compilation, and evaluation is C. The Python surface is a
   thin set of method wrappers; results are marshaled to native Python objects (`Element`, `str`, `float`, `bool`)
   inside the extension. No hot path crosses into Python.
1. **Compile once, evaluate many.** A query string compiles to an immutable program held in a process-global LRU.
   Scrapers run the same XPath over thousands of pages — that is the workload the cache is built for.
1. **Spec is authority, lxml/parsel are the behavioral oracle where the spec is silent.** XPath 1.0 semantics; HTML
   quirks (lowercased names, no namespaces) matched to lxml.html.
1. **Free-threading safe by construction.** The compiled program is immutable and shareable; all mutable state lives in
   a per-evaluation arena context; tree reads run under the existing per-tree critical section.
1. **Only a measured win counts**, and **100% line+branch coverage under gcc-15 (`gcov-15`, the CI gate)** — this
   constrains the design (no dead branches, no XML/XPointer/namespace machinery that HTML can never reach).

## Pythonic API (the only Python that exists)

Evaluation is relative to the node it is called on: `/` is the document root, `.` is the context node, `.//x` is
descendants of the context, and a bare `//x` rescans the whole document (the Scrapy footgun, matched deliberately for
parity).

```python
from turbohtml import parse, XPath

doc = parse(html)

doc.xpath("//a")  # list[Element]      — node-set of elements
doc.xpath("//a/@href")  # list[str]          — attribute string-values
doc.xpath("//h1/text()")  # list[str]          — text-node string-values
doc.xpath("count(//a)")  # float              — number expression
doc.xpath("boolean(//a)")  # bool               — boolean expression
doc.xpath("string(//h1)")  # str                — string expression

doc.xpath_one("//a")  # Element | None     — first hit, short-circuits the walk
doc.xpath_one("//a/@href")  # str | None

article = doc.xpath_one("//article")
article.xpath(".//p")  # context-relative: paragraphs under <article>
```

Return contract (lxml-compatible, so migration is mechanical): a node-set expression yields a `list` (elements as
`Element`, attributes/text as `str`, in document order); the three scalar-typed top-level expressions yield `bool` /
`float` / `str` directly. `xpath_one` returns the first result or `None` and stops the walk at the first hit.

Precompiled form for hot loops — a C-backed, immutable, thread-shareable object:

```python
q = XPath("//a[@href]")  # parse + compile once
for doc in docs:
    for a in q(doc):  # __call__ evaluates against any tree
        ...
q.evaluate(doc)  # explicit alias of __call__
```

`node.xpath(str)` already hits the C compiled-program cache, so the precompiled object is an ergonomic convenience that
additionally skips the cache hash, not a separate fast path. One name per concept: `xpath` / `xpath_one` / `XPath`. No
`xpath_all`, no `find*` aliases.

Typing (`_html.pyi`): `def xpath(self, expr: str) -> list[Element | str] | str | float | bool` and
`def xpath_one(self, expr: str) -> Element | str | None`. `XPath` is `Callable[[Node], ...]`.

## Architecture

### Compiled program — flat immutable arena

The query compiles to a **single contiguous arena block**: a flat array of fixed-size, tagged op structs (a linearized
AST), child links as `int32` indices not pointers, plus side pools for literals and resolved name atoms. This is the
storage discipline `xee` proves out (contiguous, prefetch-friendly, trivially shareable read-only) without its IR /
stack-VM / closure machinery, which XPath 1.0 — no user functions, no closures, no lazy sequences — does not need. It is
libxml2's flat `steps[]` op-array (`xpath.c:885`) with pugixml's fixed-size single-node-type discipline, minus the
kitchen sink.

A location step op carries `(axis, node-test, node-type, name-atom, predicate-chain-index)`. **Element name tests store
a resolved `uint16` tag atom, not a string**, so the per-node test is one integer compare and HTML's ASCII
case-insensitivity falls out of atom interning — the single biggest win turbohtml's architecture hands us, which none of
the studied engines have (all four strcmp QNames). The name-test literal is resolved *as written* (no folding): `//DIV`
resolves to no atom and returns nothing, exactly like lxml.html. Names outside the static tag table (custom elements)
keep their bytes and resolve lazily against the tree's atom table at eval, memoized in the eval context — keeping the
program tree-independent and globally shareable.

Functions resolve to a **small integer id at compile time** against a static `const` table
(`xp_value (*)(eval_ctx*, xp_value*, int)`); we never copy libxml2's per-eval `op->cache` write-back into the compiled
expression (`xpath.c:10999`) — that is a data race under free-threading and the reason its "immutable" program is not
actually immutable.

### Value model — four types, scalars inline

```c
typedef enum { XP_BOOL, XP_NUMBER, XP_STRING, XP_NODESET } xp_kind;
typedef struct {
    xp_kind kind;
    union { bool b; double n; xp_str s; xp_nodeset ns; } u;
} xp_value;
```

The complete XPath 1.0 data model: `Number` is always IEEE-754 double, node-set the only compound. Coercions
(`xp_to_boolean/number/string`) are pure functions implementing the spec promotions, including the quirks the
conformance tests pin (`number("")`→NaN, `Infinity`/ `-Infinity`, `-0`→`"0"`, integer doubles print without `.0`).
Evaluation is **typed** — four evaluators `eval_node_set/string/number/boolean` (pugixml's structure), never a boxed
value for a scalar subtree — so `//a[@href]` and `[position()<3]` allocate nothing for their scalar parts.

### Node binding — value-type cursor, no vtable

turbohtml has exactly one tree type, so the `antchfx` `NodeNavigator` seam becomes a **monomorphic value-type cursor**,
not an interface:

```c
typedef struct { th_node *root; th_node *curr; int32_t attr; } th_xpath_cursor;
```

`attr == -1` means "on the element"; `attr >= 0` indexes the element's `(name-atom, value)` array — **attributes are
pseudo-children**, so no attribute tree nodes are invented. The movers (`parent/child/next/prev/next_attr`) are
`static inline` over the intrusive links, and `Copy()` — the dominant allocation in the Go engine — becomes struct
assignment (free). This is the highest-leverage idea from the study and it maps onto turbohtml's links exactly.

### Node-sets and document order — integer ordinals

Every studied engine pays for document order: libxml2 punning a sort key into `node->content` (tree mutation, not
thread-safe), pugixml's in-situ buffer-pointer trick (breaks under the node-moving that HTML parsing requires — foster
parenting, adoption agency). turbohtml sheds all of it: **stamp each node with a `uint32 doc_order` preorder index when
tree construction completes**. Then:

- document-order compare is `a->doc_order < b->doc_order` (one integer compare),
- a node-set is a `uint32`/pointer vector with an `{unsorted, sorted, reverse}` tag,
- sort is an integer sort guarded by pugixml's "already monotone?" linear pre-check (axes emit in order, so the common
  case never sorts),
- dedup of a sorted set is adjacent-`unique`; child/attribute/self axes skip dedup entirely (they provably never revisit
  a node);
- union, `[n]`, `position()`, reverse axes all reduce to integer ops.

The index is assigned once on a frozen tree (the free-threaded read-path target) and never mutates during evaluation.

### Evaluation — hybrid streaming, materialize only where forced

Default to the `antchfx` **pull/streaming model** for forward axes (`child`, `descendant`, `self`, `attribute`,
`following-sibling`): emit one node at a time, document order falls out of the DFS walk, and `xpath_one` / existential
predicates short-circuit at the first hit with O(depth) memory. The Go closures become explicit **per-axis state
structs** (cursor + phase enum + level counter) driven by a `next()` function — same state machine, zero allocation,
every branch reachable (closures are hostile to gcov). Materialize into a per-eval arena vector only where XPath
semantics demand the whole set: `last()`, `count()`, `union`, `and`/ `or` over node-sets, reverse-axis positional
predicates.

Compile-time rewrites carried from all three engines (pure AST transforms, tree-independent):

- `//x` (`descendant-or-self::node()/child::x`) → a single `descendant::x` walk — the biggest win for HTML, done in
  every engine (libxml2 `xpath.c:11737`, pugixml `optimize_self`, antchfx `descendantOverDescendant`).
- positional `[n]` / `[last()]` fused into the axis loop (stop at position n, never collect- then-filter); `[1]` routes
  through the first-hit evaluator.
- predicate classification (constant / position-invariant) to skip the sort when a predicate cannot reorder.
- `@a` and `@a='v'` specialized steps; `and`/`or` short-circuit.

### Resource guards

Carry libxml2's op-count and recursion-depth caps (`xpath.c:10757`) so untrusted query strings cannot blow the stack or
spin — cheap, and these are public-facing string inputs.

## Thread-safety

The universal pattern, made genuinely immutable (unlike libxml2's write-backs and unlike `xee`'s `!Send` non-atomic
refcounts):

- **Compiled program** — immutable, tree-independent, **process-global**, refcounted, stored in an LRU keyed by the
  query string alone (no attr-generation: the program memoizes no tree-bound state, so it never goes stale). Shared
  across all trees and all threads with no lock on the read path. The cache itself takes a short lock (read-mostly /
  sharded) on insert/evict only; programs it hands out are lock-free thereafter.
- **Per-evaluation context** — a short-lived arena holding the cursor stack, position/size counters, node-set working
  buffers, and string scratch. Two threads evaluating the same program get separate contexts; nothing in the program is
  written.
- **Tree reads** run inside the tree's `Py_BEGIN_CRITICAL_SECTION` (no-op on the GIL build), atomic against concurrent
  mutators.
- **Lock order**: program-cache lock → release → tree critical section. Never nested, no deadlock. Cache lookup/compile
  happens before the tree section, so cache contention never serializes tree access.

The compiled program is process-global, **not** per-tree: a program has no tree affinity, and per-tree storage would
recompile identical strings across every document a scraper touches.

## MVP scope (first landable PR after this design)

Tier 0 plus the high-frequency Tier 1 slice — ~90% of real scraping XPath, sized to hold 100% line+branch coverage under
gcc-15:

- **Axes**: `child` (default), `descendant-or-self` (`//`), `attribute` (`@`), `self` (`.`), `parent` (`..`),
  `following-sibling`, `preceding-sibling`.
- **Node tests**: name (atom, case-sensitive vs lowercased atoms), `*`, `text()`, `node()`.
- **Paths**: absolute (`/`, `//`) and relative (`.`, `..`, `.//`); union `|`; `(path)[n]` grouping (so `//a[1]` vs
  `(//a)[1]` is correct).
- **Predicates**: `[n]`, `[last()]`/`[last()-k]`, `[@a]`, `[@a='v']`, `[@a!='v']`, `[contains(@a,'v')]`,
  `[starts-with(@a,'v')]`, `[text()='v']`, `[normalize-space(.)='v']`, nested step predicates `[.//x]`,
  `and`/`or`/relational chains.
- **Functions**:
  `text() position() last() count() contains() starts-with() normalize-space() string() not() concat() substring() string-length() local-name()`.

Deferred (fast-follow): `ancestor`/`following`/`preceding`/`namespace` axes, `id()`/`lang()`, numeric functions
(`sum`/`floor`/`ceiling`/`round`), variables `$x`, general FilterExpr chaining, EXSLT (`re:test`).

## Correctness checklist (test matrix must cover every item)

1. Document order forward / reverse-document order for reverse axes; union dedups by identity in document order.
1. Context position/size inside predicates; `[1]` on a reverse axis is the node nearest the context, not document-first.
1. `[n]` ≡ `[position()=n]`; non-numeric predicate is boolean.
1. `//a[1]` (first `<a>` of each parent) vs `(//a)[1]` (single document-first `<a>`).
1. Node-set `=`/`!=` are existential; `not(ns='x') ≠ ns!='x'` — test 0/1/≥2-member sets.
1. String-value of a node = concatenation of descendant text; string functions on a node-set use the **first** node
   only.
1. `last()`/`position()` track the current predicate's context as chained predicates filter.
1. Boolean/number/string coercion quirks (`number("")`→NaN, empty node-set false, `-0`→`"0"`).

## Coverage strategy

Coverage is measured with gcc-15 (`gcov-15`) — the CI gate; clang is not used for the gate. The four-type value model
and the HTML-only node kinds (element/text/comment/attribute/document) keep every branch reachable — the opposite of
libxml2's monolithic `xmlXPathCompOpEval` switch, whose XPointer / location-set / XSLT / DTD arms are dead for HTML and
would be uncoverable. Unreachable `switch` defaults compile to `__builtin_unreachable()` (not live branches gcov flags).
Per-axis code paths are separate functions, not a runtime `if(axis==…)` ladder, so each is independently exercised.

## What we take, and refuse, from each engine

- **libxml2**: take the flat op-array, stateless `next(ctx,cur)` axis iterators, the `//` collapse, positional
  early-stop, op/depth guards. Refuse the `op->cache` write-back, the `node->content` sort-value pun, global NaN/inf
  state, and the namespace/XPointer/location-set machinery (dead for HTML, uncoverable).
- **pugixml**: take the two-arena capture/revert scratch, typed evaluators, order-tracking node-sets with the
  monotone-key compare and lazy-sort pre-check, the `@attr`/`@attr='v'` and constant-index predicate specializations.
  Refuse the in-situ string-pointer document order (breaks under HTML node moves) — our `doc_order` ordinal is strictly
  better.
- **sxd-xpath**: take the precedence-by-layered-productions recursive-descent parser, the deabbreviation pass, the four
  coercion rules. Refuse trait objects / `HashSet` node-sets / per-node boxing — all become tagged unions + `switch` +
  integer-ordinal vectors.
- **xee**: take the contiguous immutable compiled artifact and integer-id function dispatch. Refuse the ANF IR, stack
  VM, closures, and `Rc`/`RefCell` (we make the program atomically shareable by holding no refcounted mutable state at
  all).
- **antchfx**: take the `NodeNavigator` seam (as a value-type monomorphic cursor), attribute-as-pseudo-child, the
  streaming pull model with short-circuit, and the `//` fusion. Refuse per-node `Copy()` heap allocation,
  closure-captured iterators, string-hash dedup, and the coarse global cache mutex.
- **Zig**: no mature engine exists; nothing to take beyond arena/comptime idioms turbohtml's C already embodies.

## Implementation phases

1. **Lexer + parser** → flat AST in an arena; deabbreviation; precedence ladder; op/depth guards. Unit-tested on parse
   trees.
1. **Value model + coercions** with the spec-quirk tests.
1. **Node cursor + `doc_order` stamping** + node-set vector (sort/dedup/union).
1. **Evaluator**: Tier-0 axes/tests/predicates streaming; materialized paths; functions.
1. **Compiled-program LRU + per-eval arena + critical-section integration.**
1. **Python surface**: `xpath` / `xpath_one` / `XPath`, result marshaling, `.pyi`.
1. **Compile-time rewrites** (`//` collapse, positional fusion, predicate classification).
1. **Tier-1 axes/functions**; benchmark vs lxml/parsel; Diátaxis docs + changelog fragment.

Each phase lands behind the full gate (gcc-15 line+branch coverage, type, free-threading/TSan, pyperf no-regression)
before the next.
