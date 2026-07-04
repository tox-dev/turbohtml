# House style

turbohtml is a C extension (`src/turbohtml/_c`) behind a thin Python shim (`src/turbohtml/*.py`). All non-trivial logic
lives in C; Python only validates config, unpacks arguments, calls into `_html`, and wraps the result. This document
records the conventions the two trees already follow so new code matches without a review round-trip. It records what
the code does. Where a rule and the tree disagree, the tree is the bug.

## C tree

### Comments

Every `.c` and `.h` file opens with a block comment saying what the unit is and why it exists as a separate unit, not
how it works. Example, from `serialize/escape.c`:

```c
/* HTML escaping. */
```

and, where the split needs justifying, from `core/common.h`:

```c
/* Internal header shared by the turbohtml._html translation units.

   The module is split per feature for readability (escape.c, unescape.c) but
   compiled into a single _html extension. ... */
```

No section-divider or grouping-header banners: no `/* ---- parsing ---- */` between top-level definitions, and none
inside a function body. Function names and the file header carry the structure. A comment earns its place only by
explaining a *why* that the code cannot: a spec citation, an issue number, a non-obvious invariant. It never restates
the next line.

No `TODO`, no commented-out code, no dead branches left "for later".

### Naming

Descriptive names everywhere, including loop counters: `index`, `pos`, `offset`, `inner_index`, never `i`, `j`, `c`,
`n`. C has no comprehensions, so there is no single-letter exception.

Two prefixes mark the boundary:

- `th_` for internal C symbols shared across translation units (`th_tree`, `th_tok_next`, `th_url_resolve`).
- `turbohtml_` for a Python entry point, matching a `METH_*` signature and wired into a method table
  (`turbohtml_escape`, `turbohtml_detect_encoding`).

A file-local static helper needs no prefix.

### Iteration and errors

One child-iteration idiom: walk `node->first_child` and follow `->next_sibling`. Do not index children or cache a count
that a mutation can invalidate.

Growable buffers go through `core/vec.h`; ASCII classification and compact-buffer reads go through `core/ascii.h`. Both
are `static inline` in a header so every translation unit still inlines them. Do not re-open a capacity-doubling
`realloc` loop or an `ch >= 'a' && ch <= 'z'` test inline.

Allocation is the only failure a builder function guards. On `NULL` from the allocator it sets the owning tree's
`failed` flag (or returns `NULL`/`-1` per the function's contract) and unwinds. No bare `if (!p)` that swallows the
error, and no re-check of a state the types rule out.

## Python shim

Each module opens with a one-line docstring naming the module and its job:

```python
"""turbohtml.detect: standalone character-encoding detection over bytes."""
```

`__all__` lists every name other modules import, placed after the imports. Module-scope constants are `UPPER_CASE`,
prefixed with `_` when no other module imports them, and typed `Final`:

```python
_EARLIEST: Final = date(1995, 1, 1)
_MODIFIED_MARKERS: Final = ("updated", "modified", "lastmod", "revised")
```

The shim stays a shim: if deleting a line of Python would change a returned value, that logic belongs in C.
