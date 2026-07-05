/* Identifier mangling: rename local bindings to short names.

   A post-parse pass over the arena AST. It builds the lexical scope tree, declares
   every binding, resolves every identifier reference to its binding (or to a free /
   global name), then assigns the shortest legal names to the locals.

   Correctness is by construction and conservative - under-renaming is safe, only a
   wrong rename corrupts:
   - A scope reached by `with` or a direct `eval(` is poisoned; nothing inside it (or
     any enclosing scope) is renamed, because those constructs resolve names
     dynamically.
   - Every free / global name used anywhere in the program is forbidden as a new
     name, so a binding can never be renamed onto a name that some reference resolves
     to globally - even a reference this pass failed to resolve keeps its (free) name
     and that name is reserved.
   - A new name also avoids every reserved word and every enclosing binding's name in
     scope, so it can neither be a keyword nor shadow an outer binding that is visible.
   Naming runs in two steps: each binding gets a slot index (inherited down the scope
   tree so a nested binding never shares a slot with a visible ancestor, while disjoint
   sibling scopes reuse slots), then the shortest names go to the slots whose bindings
   are referenced most -- the frequency ordering esbuild/terser/swc use. */

#include "serialize/js/internal.h"

#include <stdlib.h>
#include <string.h>

static int word_is(const Py_UCS4 *name, Py_ssize_t len, const char *word) {
    if (len != (Py_ssize_t)strlen(word)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] != (Py_UCS4)(unsigned char)word[index]) {
            return 0;
        }
    }
    return 1;
}

static int name_eq(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    if (left_len != right_len) {
        return 0;
    }
    return memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4)) == 0;
}

/* Name hash table.

   An open-addressing (linear-probe) map from an identifier name to an int32 value.
   Used three ways: the visible-binding map (value = symbol index, or -1 when the
   binding it held has gone out of scope), the free-name set, and the enclosing-binding
   set during renaming (value = 1 for present). A slot is never deleted; going out of
   scope just resets the value, so probe chains stay intact and scope exit is an O(1)
   restore from an undo log. */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t len;
    int32_t val;
} hslot;

typedef struct {
    hslot *slots;
    int32_t cap; /* a power of two */
    int32_t count;
    int failed;
} htab;

static uint32_t name_hash(const Py_UCS4 *name, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t index = 0; index < len; index++) {
        hash = (hash ^ (uint32_t)name[index]) * 16777619u;
    }
    return hash;
}

static void htab_resize(htab *table, int32_t cap) {
    hslot *slots = jm_calloc((size_t)cap, sizeof(hslot));
    if (slots == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        table->failed = 1; /* GCOVR_EXCL_LINE */
        return;            /* GCOVR_EXCL_LINE */
    }
    uint32_t mask = (uint32_t)cap - 1;
    for (int32_t index = 0; index < table->cap; index++) {
        if (table->slots[index].name != NULL) {
            uint32_t probe = name_hash(table->slots[index].name, table->slots[index].len) & mask;
            while (slots[probe].name != NULL) {
                probe = (probe + 1) & mask;
            }
            slots[probe] = table->slots[index];
        }
    }
    jm_free(table->slots);
    table->slots = slots;
    table->cap = cap;
}

/* Find the slot for name; with create, claim an empty slot (value -1) and grow the
   table when it passes a 3/4 load. Returns NULL only when create is 0 and absent, or
   on allocation failure. */
static hslot *htab_slot(htab *table, const Py_UCS4 *name, Py_ssize_t len, int create) {
    if (create && (table->cap == 0 || (table->count + 1) * 4 >= table->cap * 3)) {
        htab_resize(table, table->cap ? table->cap * 2 : 64);
        if (table->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
    }
    if (table->cap == 0) {
        return NULL; /* an empty read-only table */
    }
    uint32_t mask = (uint32_t)table->cap - 1;
    uint32_t probe = name_hash(name, len) & mask;
    for (;;) {
        hslot *slot = &table->slots[probe];
        if (slot->name == NULL) {
            if (!create) {
                return NULL;
            }
            slot->name = name;
            slot->len = len;
            slot->val = -1;
            table->count++;
            return slot;
        }
        if (name_eq(slot->name, slot->len, name, len)) {
            return slot;
        }
        probe = (probe + 1) & mask;
    }
}

static int is_reserved(const Py_UCS4 *name, Py_ssize_t len) {
    static const char *const words[] = {
        "break",   "case",   "catch",  "class",      "const",   "continue",   "debugger", "default",   "delete",
        "do",      "else",   "enum",   "export",     "extends", "false",      "finally",  "for",       "function",
        "if",      "import", "in",     "instanceof", "new",     "null",       "return",   "super",     "switch",
        "this",    "throw",  "true",   "try",        "typeof",  "var",        "void",     "while",     "with",
        "yield",   "let",    "static", "await",      "async",   "implements", "package",  "protected", "interface",
        "private", "public", "do",     NULL};
    for (int index = 0; words[index] != NULL; index++) {
        Py_ssize_t wlen = (Py_ssize_t)strlen(words[index]);
        if (wlen != len) {
            continue;
        }
        int same = 1;
        for (Py_ssize_t pos = 0; pos < len; pos++) {
            if (name[pos] != (Py_UCS4)(unsigned char)words[index][pos]) {
                same = 0;
                break;
            }
        }
        if (same) {
            return 1;
        }
    }
    return 0;
}

/* The i-th shortest identifier name: a bijective base-54-then-64 numeral. The first
   character comes from 54 options (no digit, identifiers cannot start with one); the
   rest from 64. PyMem-owned. */
static Py_UCS4 *base54(Py_ssize_t index, Py_ssize_t *out_len) {
    static const char head[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$";
    static const char tail[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$0123456789";
    Py_UCS4 buf[16];
    Py_ssize_t len = 0;
    Py_ssize_t value = index;
    buf[len++] = (Py_UCS4)(unsigned char)head[value % 54];
    value /= 54;
    while (value > 0) {
        value--;
        buf[len++] = (Py_UCS4)(unsigned char)tail[value % 64];
        value /= 64;
    }
    Py_UCS4 *name = jm_malloc((size_t)len * sizeof(Py_UCS4));
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    memcpy(name, buf, (size_t)len * sizeof(Py_UCS4));
    *out_len = len;
    return name;
}

/* Analysis state.

   Resolution uses one visible-binding table (name -> innermost visible symbol) rather
   than a per-reference walk up the scope chain. Entering a scope pushes its bindings,
   shadowing whatever they hide; leaving it restores them from the undo log. Each entry
   records the table, the name and the value to put back, so a single linear scan of the
   log unwinds a scope in O(bindings), and resolve is a single O(1) hash lookup. */
typedef struct {
    htab *table;
    const Py_UCS4 *name;
    Py_ssize_t len;
    int32_t old;
} undo_rec;

typedef struct {
    jm_program *prog;
    htab visible; /* name -> currently-visible symbol index (-1 when its binding is gone) */
    htab frees;   /* free / global names, plus kept declaration names, reserved (value 1) */
    htab labels;  /* label name -> its symbol (a separate namespace; -1 when kept verbatim) */
    undo_rec *undo;
    int32_t undo_count;
    int32_t undo_cap;
    int32_t slot_count;  /* number of distinct rename slots assigned across the program */
    int32_t global;      /* the global scope, where label symbols are parked */
    int32_t label_depth; /* count of lexically-enclosing labels, indexing their short names */
    int poisoned;        /* a with / eval was seen: stop renaming entirely */
    int failed;
} M;

static int32_t htab_get(htab *table, const Py_UCS4 *name, Py_ssize_t len) {
    hslot *slot = htab_slot(table, name, len, 0);
    return slot != NULL ? slot->val : -1;
}

/* Record, then apply, a shadowing assignment so a later undo_to can restore it. */
static void push(M *mangler, htab *table, const Py_UCS4 *name, Py_ssize_t len, int32_t val) {
    hslot *slot = htab_slot(table, name, len, 1);
    if (slot == NULL || table->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1;             /* GCOVR_EXCL_LINE */
        return;                          /* GCOVR_EXCL_LINE */
    }
    if (mangler->undo_count == mangler->undo_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)mangler->undo_cap + 1, (size_t)mangler->undo_cap, 256, sizeof(undo_rec), &cap, &bytes);
        if (!grew) {             /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            mangler->failed = 1; /* GCOVR_EXCL_LINE */
            return;              /* GCOVR_EXCL_LINE */
        }
        undo_rec *grown = jm_realloc(mangler->undo, bytes);
        if (grown == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            mangler->failed = 1; /* GCOVR_EXCL_LINE */
            return;              /* GCOVR_EXCL_LINE */
        }
        mangler->undo = grown;
        mangler->undo_cap = (int32_t)cap;
    }
    mangler->undo[mangler->undo_count].table = table;
    mangler->undo[mangler->undo_count].name = name;
    mangler->undo[mangler->undo_count].len = len;
    mangler->undo[mangler->undo_count].old = slot->val;
    mangler->undo_count++;
    slot->val = val;
}

/* Unwind the undo log back to mark, restoring each shadowed binding. The name re-lookup
   is O(1) and stays correct even if the table was resized after the entry was pushed. */
static void undo_to(M *mangler, int32_t mark) {
    while (mangler->undo_count > mark) {
        mangler->undo_count--;
        undo_rec *rec = &mangler->undo[mangler->undo_count];
        hslot *slot = htab_slot(rec->table, rec->name, rec->len, 0);
        slot->val = rec->old; /* the slot always exists: push created it */
    }
}

/* Resolve a name to the innermost visible binding, or -1 (free / global). */
static int32_t resolve(M *mangler, const Py_UCS4 *name, Py_ssize_t len) {
    return htab_get(&mangler->visible, name, len);
}

static void declare(M *mangler, int32_t scope, const Py_UCS4 *name, Py_ssize_t len, uint8_t decl) {
    hslot *slot = htab_slot(&mangler->visible, name, len, 1);
    if (slot == NULL || mangler->visible.failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1;                       /* GCOVR_EXCL_LINE */
        return;                                    /* GCOVR_EXCL_LINE */
    }
    if (slot->val >= 0 && mangler->prog->syms[slot->val].scope == scope) {
        return; /* a re-declaration (var x; var x) reuses the same-scope symbol */
    }
    int32_t sym = jm_sym_new(mangler->prog, name, len, scope, decl);
    if (sym < 0) {           /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1; /* GCOVR_EXCL_LINE */
        return;              /* GCOVR_EXCL_LINE */
    }
    push(mangler, &mangler->visible, name, len, sym);
}

static void declare_pattern(M *mangler, int32_t idx, int32_t scope, uint8_t decl);
static void hoist_vars(M *mangler, int32_t idx, int32_t fn_scope);

/* Declare the binding identifiers in a destructuring (or simple) target. */
static void declare_pattern(M *mangler, int32_t idx, int32_t scope, uint8_t decl) {
    jm_node *node = &mangler->prog->nodes[idx];
    switch (node->kind) {
    case JN_IDENT:
        declare(mangler, scope, node->str, node->str_len, decl);
        break;
    case JN_ARRAY:
        for (int32_t element = node->a; element >= 0; element = mangler->prog->nodes[element].next) {
            declare_pattern(mangler, element, scope, decl);
        }
        break;
    case JN_OBJECT:
        for (int32_t prop = node->a; prop >= 0; prop = mangler->prog->nodes[prop].next) {
            const jm_node *entry = &mangler->prog->nodes[prop];
            declare_pattern(mangler,
                            entry->kind == JN_SPREAD ? entry->a
                            : entry->b >= 0          ? entry->b
                                                     : entry->a,
                            scope, decl);
        }
        break;
    case JN_ASSIGN: /* a default `target = init`, or a rest `...target` */
    case JN_SPREAD:
        declare_pattern(mangler, node->a, scope, decl);
        break;
    default:
        break;
    }
}

/* Hoist every `var` (and var-style for-loop binding) in the subtree to fn_scope,
   without descending into nested function bodies (which start their own var scope). */
static void hoist_vars(M *mangler, int32_t idx, int32_t fn_scope) {
    while (idx >= 0) {
        jm_node *node = &mangler->prog->nodes[idx];
        switch (node->kind) {
        case JN_FUNC:
        case JN_ARROW:
            break; /* a nested function has its own var scope */
        case JN_VAR:
            if (node->decl == 0) {
                for (int32_t declarator = node->a; declarator >= 0;
                     declarator = mangler->prog->nodes[declarator].next) {
                    declare_pattern(mangler, mangler->prog->nodes[declarator].a, fn_scope, 0);
                }
            }
            break;
        case JN_FORIN:
        case JN_FOROF:
            if (mangler->prog->nodes[node->a].kind == JN_VAR && mangler->prog->nodes[node->a].decl == 0) {
                hoist_vars(mangler, node->a, fn_scope);
            }
            hoist_vars(mangler, node->c, fn_scope);
            break;
        default:
            /* descend into every child to find vars through blocks, if, for, ... */
            hoist_vars(mangler, node->a, fn_scope);
            hoist_vars(mangler, node->b, fn_scope);
            hoist_vars(mangler, node->c, fn_scope);
            hoist_vars(mangler, node->d, fn_scope);
            break;
        }
        idx = node->next;
    }
}

/* Declare the block-level bindings (let/const, function and class declarations) that
   appear directly among stmts, in scope. */
static void hoist_block(M *mangler, int32_t first, int32_t scope) {
    for (int32_t idx = first; idx >= 0; idx = mangler->prog->nodes[idx].next) {
        jm_node *node = &mangler->prog->nodes[idx];
        if (node->kind == JN_VAR && node->decl != 0) {
            for (int32_t declarator = node->a; declarator >= 0; declarator = mangler->prog->nodes[declarator].next) {
                declare_pattern(mangler, mangler->prog->nodes[declarator].a, scope, node->decl);
            }
        } else if (node->kind == JN_FUNC) { /* in a statement list a function is always a named declaration */
            declare(mangler, scope, node->str, node->str_len, 4);
            node->sym = resolve(mangler, node->str, node->str_len); /* so the name renames with its references */
            if (node->sym >= 0) { /* GCOVR_EXCL_BR_LINE: unresolved only on an allocation failure */
                mangler->prog->syms[node->sym].decl_node = idx; /* let drop_unused find an unused function */
            }
        } else if (node->kind == JN_CLASS) { /* likewise a class is always a named declaration */
            declare(mangler, scope, node->str, node->str_len, 6);
            node->sym = resolve(mangler, node->str, node->str_len);
        }
    }
}

static void walk(M *mangler, int32_t idx, int32_t scope, int bind);

/* Walk a chain of siblings. */
static void walk_chain(M *mangler, int32_t first, int32_t scope, int bind) {
    for (int32_t idx = first; idx >= 0; idx = mangler->prog->nodes[idx].next) {
        walk(mangler, idx, scope, bind);
    }
}

/* Open a function/arrow scope: declare params + hoisted bindings, then walk the body. */
static void walk_function(M *mangler, int32_t idx, int32_t parent) {
    jm_node *node = &mangler->prog->nodes[idx];
    int32_t scope = jm_scope_new(mangler->prog, parent, 1);
    if (scope < 0) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1; /* GCOVR_EXCL_LINE */
        return;              /* GCOVR_EXCL_LINE */
    }
    int32_t mark = mangler->undo_count;
    /* a named function expression binds its own name inside its scope (and nowhere else), so the
       name is renamable (decl 7) like any local; tag the node so the printer emits the new name */
    if (node->kind == JN_FUNC && (node->flags & JN_F_EXPR) && node->str != NULL) {
        declare(mangler, scope, node->str, node->str_len, 7);
        node->sym = resolve(mangler, node->str, node->str_len);
    }
    for (int32_t param = node->a; param >= 0; param = mangler->prog->nodes[param].next) {
        declare_pattern(mangler, param, scope, 3);
    }
    int32_t body = node->b;
    if (node->kind == JN_ARROW && (node->flags & JN_F_EXPRBODY)) {
        /* default param values reference the param scope, then the expression body */
        walk_chain(mangler, node->a, scope, 1);
        walk(mangler, body, scope, 0);
        undo_to(mangler, mark);
        return;
    }
    /* params may carry default-value expressions and patterns */
    walk_chain(mangler, node->a, scope, 1);
    int32_t body_stmts = mangler->prog->nodes[body].a;
    mangler->prog->scopes[scope].first_stmt = body_stmts;
    hoist_vars(mangler, body_stmts, scope);
    hoist_block(mangler, body_stmts, scope);
    walk_chain(mangler, body_stmts, scope, 0);
    undo_to(mangler, mark);
}

/* Walk node idx in scope. bind selects how an identifier is counted: 0 a read reference, 1 a
   declaration target (already declared, only resolved here), 2 an assignment/update/for-in write
   target. The read/write split lets the inline and dead-binding passes tell a value's reads from its
   reassignments. */
static void walk(M *mangler, int32_t idx, int32_t scope, int bind) {
    if (idx < 0) {
        return;
    }
    jm_node *node = &mangler->prog->nodes[idx];
    switch (node->kind) {
    case JN_NUM:
    case JN_STRING:
    case JN_REGEX:
    case JN_BIGINT:
    case JN_EMPTY:
    case JN_DEBUGGER:
        return; /* leaves: nothing resolves below, so skip the four child calls */
    case JN_IDENT: {
        int32_t sym = resolve(mangler, node->str, node->str_len);
        node->sym = sym;
        if (sym >= 0) {
            mangler->prog->syms[sym].uses++;
            if (bind == 0) { /* a read reference */
                mangler->prog->syms[sym].refs++;
                mangler->prog->syms[sym].ref_node = idx;
                mangler->prog->syms[sym].ref_scope = scope;
                if (idx < mangler->prog->syms[sym].min_ref) {
                    mangler->prog->syms[sym].min_ref = idx;
                }
            } else if (bind == 2) { /* an assignment / update / for-in target: a write */
                mangler->prog->syms[sym].writes++;
            } /* bind == 1: a declaration target, neither read nor write */
        } else {
            hslot *slot = htab_slot(&mangler->frees, node->str, node->str_len, 1); /* a global / free name */
            if (slot != NULL) { /* GCOVR_EXCL_BR_LINE: the false branch is an allocation failure */
                slot->val = 1;
            }
        }
        return;
    }
    case JN_FUNC:
    case JN_ARROW:
        walk_function(mangler, idx, scope);
        return;
    case JN_BLOCK: {
        int32_t inner = jm_scope_new(mangler->prog, scope, 0);
        if (inner < 0) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            mangler->failed = 1; /* GCOVR_EXCL_LINE */
            return;              /* GCOVR_EXCL_LINE */
        }
        int32_t mark = mangler->undo_count;
        hoist_block(mangler, node->a, inner);
        walk_chain(mangler, node->a, inner, 0);
        undo_to(mangler, mark);
        return;
    }
    case JN_FOR:
    case JN_FORIN:
    case JN_FOROF: {
        /* a let/const loop binding scopes to the loop; give the whole loop a scope */
        int32_t inner = jm_scope_new(mangler->prog, scope, 0);
        if (inner < 0) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            mangler->failed = 1; /* GCOVR_EXCL_LINE */
            return;              /* GCOVR_EXCL_LINE */
        }
        int32_t mark = mangler->undo_count;
        if (node->kind == JN_FOR) {
            if (node->a >= 0 && mangler->prog->nodes[node->a].kind == JN_VAR &&
                mangler->prog->nodes[node->a].decl != 0) {
                hoist_block(mangler, node->a, inner);
            }
            walk(mangler, node->a, inner, 0);
            walk(mangler, node->b, inner, 0);
            walk(mangler, node->c, inner, 0);
            walk(mangler, node->d, inner, 0);
        } else {
            if (mangler->prog->nodes[node->a].kind == JN_VAR && mangler->prog->nodes[node->a].decl != 0) {
                hoist_block(mangler, node->a, inner);
            }
            /* `for(var x in o)` declares x; `for(x in o)` assigns it -- a write target */
            walk(mangler, node->a, inner, mangler->prog->nodes[node->a].kind == JN_VAR ? 0 : 2);
            walk(mangler, node->b, inner, 0);
            walk(mangler, node->c, inner, 0);
            /* a for-in/for-of loop binding is required syntax (`for( in o)` is invalid), so it is never
               a drop or inline candidate: forget the single-ident declaration the JN_VAR walk recorded
               (a destructuring target was never recorded, so it needs no clearing) */
            if (mangler->prog->nodes[node->a].kind == JN_VAR) {
                int32_t bound = mangler->prog->nodes[mangler->prog->nodes[node->a].a].a;
                if (mangler->prog->nodes[bound].kind == JN_IDENT) {
                    int32_t bsym = mangler->prog->nodes[bound].sym;
                    if (bsym >= 0) { /* GCOVR_EXCL_BR_LINE: unresolved only on an allocation failure */
                        mangler->prog->syms[bsym].decl_node = -1;
                    }
                }
            }
        }
        undo_to(mangler, mark);
        return;
    }
    case JN_TRY:
        walk(mangler, node->a, scope, 0);
        if (node->c >= 0) {
            int32_t cat = jm_scope_new(mangler->prog, scope, 2);
            if (cat < 0) {           /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                mangler->failed = 1; /* GCOVR_EXCL_LINE */
                return;              /* GCOVR_EXCL_LINE */
            }
            int32_t mark = mangler->undo_count;
            if (node->b >= 0) {
                declare_pattern(mangler, node->b, cat, 5);
                walk(mangler, node->b, cat, 1);
            }
            walk_chain(mangler, mangler->prog->nodes[node->c].a, cat, 0);
            undo_to(mangler, mark);
        }
        walk(mangler, node->d, scope, 0);
        return;
    case JN_VAR:
        for (int32_t declarator = node->a; declarator >= 0; declarator = mangler->prog->nodes[declarator].next) {
            walk(mangler, mangler->prog->nodes[declarator].a, scope, 1); /* resolve the (declared) target */
            walk(mangler, mangler->prog->nodes[declarator].b, scope, 0); /* the initializer is a reference context */
            /* record the declaring statement on each ident-target binding so the inline and unused passes
               can find it. A redeclaration that initializes again is a write: the declarator search
               lands on the first, so its value must not propagate over the later one. */
            if (mangler->prog->nodes[mangler->prog->nodes[declarator].a].kind == JN_IDENT) {
                int32_t target = mangler->prog->nodes[mangler->prog->nodes[declarator].a].sym;
                if (target >= 0) { /* GCOVR_EXCL_BR_LINE: unresolved only on a hoist allocation failure */
                    if (mangler->prog->syms[target].decl_node >= 0 && mangler->prog->nodes[declarator].b >= 0) {
                        mangler->prog->syms[target].writes++;
                    }
                    mangler->prog->syms[target].decl_node = idx;
                }
            }
        }
        return;
    case JN_MEMBER_EXPR:
        walk(mangler, node->a, scope, 0);
        if (node->flags & JN_F_COMPUTED) {
            walk(mangler, node->b, scope, 0); /* a[b]: b is a reference */
        }
        /* a.b: b is a property name, not a variable - skip */
        return;
    case JN_PROP:
        if (node->flags & JN_F_COMPUTED) {
            walk(mangler, node->a, scope, 0);
        }
        walk(mangler, node->b, scope, bind);
        if (node->flags & JN_F_SHORTHAND) {
            walk(mangler, node->a, scope, bind); /* { x } references (or binds) x */
            int32_t self = mangler->prog->nodes[node->a].sym;
            if (self >= 0) {
                mangler->prog->syms[self].ref_prop = idx;
            }
        }
        return;
    case JN_MEMBER: /* class member: key may be computed; value is a function */
        if (node->flags & JN_F_COMPUTED) {
            walk(mangler, node->a, scope, 0);
        }
        walk(mangler, node->b, scope, 0);
        return;
    case JN_ARRAY:
    case JN_OBJECT:
    case JN_SEQ:
    case JN_TEMPLATE:
        walk_chain(mangler, node->a, scope, bind); /* elements / properties / exprs / parts */
        return;
    case JN_CALL: {
        /* a direct call to `eval` resolves names dynamically: poison renaming */
        const jm_node *callee = &mangler->prog->nodes[node->a];
        if (callee->kind == JN_IDENT && callee->str_len == 4 && callee->str[0] == 'e' && callee->str[1] == 'v' &&
            callee->str[2] == 'a' && callee->str[3] == 'l') {
            mangler->poisoned = 1;
        }
        walk(mangler, node->a, scope, 0);
        walk_chain(mangler, node->b, scope, 0);
        return;
    }
    case JN_NEW: /* a = callee, b = arguments */
        walk(mangler, node->a, scope, 0);
        walk_chain(mangler, node->b, scope, 0);
        return;
    case JN_CLASS: { /* a = superclass, b = members */
        /* a named class expression binds its own name inside the class body (heritage included)
           and nowhere else, so the name is renamable; a class declaration keeps its (hoisted)
           name. Give the expression its own scope holding the renamable self-name. */
        if ((node->flags & JN_F_EXPR) && node->str != NULL) {
            int32_t inner = jm_scope_new(mangler->prog, scope, 0);
            if (inner < 0) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                mangler->failed = 1; /* GCOVR_EXCL_LINE */
                return;              /* GCOVR_EXCL_LINE */
            }
            int32_t mark = mangler->undo_count;
            declare(mangler, inner, node->str, node->str_len, 7);
            node->sym = resolve(mangler, node->str, node->str_len);
            walk(mangler, node->a, inner, 0);
            walk_chain(mangler, node->b, inner, 0);
            undo_to(mangler, mark);
            return;
        }
        walk(mangler, node->a, scope, 0);
        walk_chain(mangler, node->b, scope, 0);
        return;
    }
    case JN_SWITCH: {
        int32_t inner = jm_scope_new(mangler->prog, scope, 0); /* case bodies may hold let/const */
        if (inner < 0) {                                       /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            mangler->failed = 1;                               /* GCOVR_EXCL_LINE */
            return;                                            /* GCOVR_EXCL_LINE */
        }
        int32_t mark = mangler->undo_count;
        walk(mangler, node->a, scope, 0); /* discriminant is in the outer scope */
        for (int32_t clause = node->b; clause >= 0; clause = mangler->prog->nodes[clause].next) {
            hoist_block(mangler, mangler->prog->nodes[clause].b, inner);
        }
        for (int32_t clause = node->b; clause >= 0; clause = mangler->prog->nodes[clause].next) {
            walk(mangler, mangler->prog->nodes[clause].a, inner, 0); /* case test */
            walk_chain(mangler, mangler->prog->nodes[clause].b, inner, 0);
        }
        undo_to(mangler, mark);
        return;
    }
    case JN_WITH:
        mangler->poisoned = 1; /* with(obj) resolves names against obj at runtime */
        walk(mangler, node->a, scope, 0);
        walk(mangler, node->b, scope, 0);
        return;
    case JN_LABEL: {
        /* labels are their own namespace (a label and a variable may share a name) and are always
           statically resolved, so they rename safely even under poison. The n-th enclosing label
           takes the n-th single-letter name (none of which is a reserved word); past 52 the label
           is kept verbatim. Shadow any enclosing same-named label either way so an inner
           break/continue still resolves to this one. */
        int32_t mark = mangler->undo_count;
        int32_t sym = -1;
        if (mangler->label_depth < 52) {
            Py_ssize_t len = 0;
            Py_UCS4 *name = base54(mangler->label_depth, &len);
            if (name == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                mangler->failed = 1; /* GCOVR_EXCL_LINE */
                return;              /* GCOVR_EXCL_LINE */
            }
            sym = jm_sym_new(mangler->prog, node->str, node->str_len, mangler->global, 8);
            if (sym < 0) {           /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                jm_free(name);       /* GCOVR_EXCL_LINE */
                mangler->failed = 1; /* GCOVR_EXCL_LINE */
                return;              /* GCOVR_EXCL_LINE */
            }
            mangler->prog->syms[sym].mangled = name;
            mangler->prog->syms[sym].mangled_len = len;
            node->sym = sym;
        }
        push(mangler, &mangler->labels, node->str, node->str_len, sym);
        mangler->label_depth++;
        walk(mangler, node->a, scope, bind);
        mangler->label_depth--;
        undo_to(mangler, mark);
        return;
    }
    case JN_BREAK:
    case JN_CONTINUE:
        if (node->str != NULL) { /* a labeled jump: point it at the (possibly renamed) label */
            node->sym = htab_get(&mangler->labels, node->str, node->str_len);
        }
        return;
    case JN_ASSIGN: {
        /* a simple `x = v` writes x; a compound `x op= v` reads x and writes it. A member or
           destructuring target is walked as an ordinary expression, where its bindings read. */
        int simple = node->op == JT_ASSIGN;
        walk(mangler, node->a, scope, simple ? 2 : 0);
        if (!simple && mangler->prog->nodes[node->a].kind == JN_IDENT) {
            int32_t target = mangler->prog->nodes[node->a].sym;
            if (target >= 0) {
                mangler->prog->syms[target].writes++;
            }
        }
        walk(mangler, node->b, scope, 0);
        return;
    }
    case JN_UNARY:
        walk(mangler, node->a, scope, 0);
        if (node->op == JT_IDENT && word_is(node->str, node->str_len, "delete") &&
            mangler->prog->nodes[node->a].kind == JN_IDENT) {
            /* `delete x` on a resolved binding yields false where a copied-in value yields true
               (13.5.1.2), so the operand counts as a write and no inline ever lands there */
            int32_t target = mangler->prog->nodes[node->a].sym;
            if (target >= 0) {
                mangler->prog->syms[target].writes++;
            }
        }
        return;
    case JN_UPDATE: /* ++x / x-- reads the operand and writes it */
        walk(mangler, node->a, scope, 0);
        if (mangler->prog->nodes[node->a].kind == JN_IDENT) {
            int32_t target = mangler->prog->nodes[node->a].sym;
            if (target >= 0) {
                mangler->prog->syms[target].writes++;
            }
        }
        return;
    default:
        break;
    }
    walk(mangler, node->a, scope, bind);
    walk(mangler, node->b, scope, bind);
    walk(mangler, node->c, scope, bind);
    walk(mangler, node->d, scope, bind);
}

/* Whether name is a reserved word or a name that must not be reused: a free/global
   name, or a kept function/class declaration name (reserved globally so a mangled
   binding never shadows one). The free table holds both, so this is O(1). */
static int is_forbidden(M *mangler, const Py_UCS4 *name, Py_ssize_t len) {
    return is_reserved(name, len) || htab_get(&mangler->frees, name, len) > 0;
}

/* Give every renamable binding of scope (and, recursively, its children) a slot index.

   The slot counter is inherited from the enclosing scope: a nested scope starts where
   its parent stopped, so a binding here can never share a slot with a visible ancestor
   -- the parent's count already passed every ancestor binding. Sibling scopes do not
   nest, so each restarts from the same inherited base and shares the other's slots.
   Names are assigned to slots afterwards, by frequency, so the slot is just a position;
   bindings that share a slot (across disjoint scopes) end up with the same short name. */
static void assign_slots(M *mangler, int32_t scope, int32_t base) {
    int32_t counter = base;
    for (int32_t sym = mangler->prog->scopes[scope].first_sym; sym >= 0; sym = mangler->prog->syms[sym].scope_next) {
        if ((mangler->prog->syms[sym].decl == 4 || mangler->prog->syms[sym].decl == 6) &&
            mangler->prog->scopes[scope].kind != 1) {
            continue; /* a function/class declaration in a block keeps its name: renaming it would
                         miss the Annex-B B.3.3 hoisted copy in the enclosing function scope. One
                         directly in a function body (scope kind 1) has no such copy and renames. */
        }
        mangler->prog->syms[sym].slot = counter++;
    }
    if (counter > mangler->slot_count) {
        mangler->slot_count = counter;
    }
    for (int32_t child = mangler->prog->scopes[scope].first_child; child >= 0;
         child = mangler->prog->scopes[child].next_sibling) {
        assign_slots(mangler, child, counter);
    }
}

/* A slot paired with the total number of references the bindings sharing it make, so
   slots can be ordered most-used first. */
typedef struct {
    uint64_t uses;
    int32_t slot;
} slot_rank;

/* Order by descending use count, then ascending slot index for a deterministic tie
   break. */
static int cmp_slot_rank(const void *pa, const void *pb) {
    const slot_rank *left = pa;
    const slot_rank *right = pb;
    if (left->uses != right->uses) {
        return left->uses < right->uses ? 1 : -1;
    }
    return (left->slot > right->slot) - (left->slot < right->slot); /* branchless slot tie-break */
}

/* Hand the shortest names to the slots whose bindings are referenced most, the way
   esbuild/terser/swc do: total each slot's references, sort slots by that count, then
   walk the base-54 sequence (skipping reserved and free names) assigning names in rank
   order. Every binding sharing a slot then copies that slot's name. */
static void assign_names_by_frequency(M *mangler) {
    if (mangler->slot_count == 0) {
        return; /* nothing renamable */
    }
    jm_program *prog = mangler->prog;
    slot_rank *ranks = jm_calloc((size_t)mangler->slot_count, sizeof(slot_rank));
    Py_UCS4 **slot_name = jm_calloc((size_t)mangler->slot_count, sizeof(Py_UCS4 *));
    Py_ssize_t *slot_len = jm_calloc((size_t)mangler->slot_count, sizeof(Py_ssize_t));
    if (ranks == NULL || slot_name == NULL || slot_len == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1;                                      /* GCOVR_EXCL_LINE */
        goto done;                                                /* GCOVR_EXCL_LINE */
    }
    for (int32_t index = 0; index < mangler->slot_count; index++) {
        ranks[index].slot = index;
    }
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        if (prog->syms[sym].slot >= 0) {
            ranks[prog->syms[sym].slot].uses += prog->syms[sym].uses;
        }
    }
    qsort(ranks, (size_t)mangler->slot_count, sizeof(slot_rank), cmp_slot_rank);
    Py_ssize_t counter = 0;
    for (int32_t rank = 0; rank < mangler->slot_count; rank++) {
        Py_ssize_t len = 0;
        Py_UCS4 *name = NULL;
        for (;;) {
            jm_free(name);
            name = base54(counter++, &len);
            if (name == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                mangler->failed = 1; /* GCOVR_EXCL_LINE */
                goto done;           /* GCOVR_EXCL_LINE */
            }
            if (is_forbidden(mangler, name, len)) {
                continue;
            }
            break;
        }
        slot_name[ranks[rank].slot] = name;
        slot_len[ranks[rank].slot] = len;
    }
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        int32_t slot = prog->syms[sym].slot;
        if (slot < 0) {
            continue;
        }
        Py_UCS4 *copy = jm_malloc((size_t)slot_len[slot] * sizeof(Py_UCS4));
        if (copy == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure */
            mangler->failed = 1; /* GCOVR_EXCL_LINE */
            goto done;           /* GCOVR_EXCL_LINE */
        }
        memcpy(copy, slot_name[slot], (size_t)slot_len[slot] * sizeof(Py_UCS4));
        prog->syms[sym].mangled = copy;
        prog->syms[sym].mangled_len = slot_len[slot];
    }
done:
    if (slot_name != NULL) { /* GCOVR_EXCL_BR_LINE: a NULL table is an allocation failure */
        for (int32_t index = 0; index < mangler->slot_count; index++) {
            jm_free(slot_name[index]);
        }
    }
    jm_free(slot_name);
    jm_free(slot_len);
    jm_free(ranks);
}

/* A const initializer safe to inline at the use site: a value with no side effects that cannot
   change between the declaration and the use -- a number, string or regex literal, or a folded
   true/false/undefined (`!0`/`!1`/`void 0`). An identifier or member access would not qualify. */
static int is_const_literal(jm_program *prog, int32_t idx) {
    switch (prog->nodes[idx].kind) {
    case JN_NUM:
    case JN_STRING:
    case JN_REGEX:
        return 1;
    case JN_UNARY:
        return prog->nodes[prog->nodes[idx].a].kind == JN_NUM;
    default:
        return 0;
    }
}

/* A statement whose expression evaluates each subexpression at most once: an expression cannot
   loop, so a read inside runs at most once per pass over the preceding declaration. */
static int single_shot_stmt(jm_program *prog, int32_t stmt) {
    switch (prog->nodes[stmt].kind) {
    case JN_EXPR_STMT:
    case JN_RETURN:
    case JN_THROW:
        return 1;
    default:
        return 0;
    }
}

/* A literal whose copy is the same immutable value at every read: a number, a string, or a folded
   boolean/undefined. A regex is excluded: each evaluation creates a fresh object (13.2.7.5 in the
   evaluation of a RegularExpressionLiteral), so a read inside a loop or closure would mint new
   state (lastIndex, identity) where the binding shared one object. */
static int is_value_literal(jm_program *prog, int32_t idx) {
    switch (prog->nodes[idx].kind) {
    case JN_NUM:
    case JN_STRING:
        return 1;
    case JN_UNARY:
        return prog->nodes[prog->nodes[idx].a].kind == JN_NUM;
    default:
        return 0;
    }
}

/* The characters the literal prints as, for the propagation cost model: `!N` / `void N` for the
   folded boolean/undefined forms, the lexeme otherwise. */
static Py_ssize_t literal_print_len(jm_program *prog, int32_t idx) {
    const jm_node *node = &prog->nodes[idx];
    if (node->kind == JN_UNARY) {
        Py_ssize_t operand = prog->nodes[node->a].str_len;
        return node->op == JT_NOT ? 1 + operand : 5 + operand;
    }
    return node->str_len;
}

static void collapse_walk(jm_program *prog, int32_t idx, int *changed);
static int is_droppable_init(jm_program *prog, int32_t init);

/* A value whose evaluation cannot be observed when its result is discarded: a side-effect-free
   initializer, or a resolved local read (reading a declared binding never throws). A member access,
   call or free-name read may throw or run a getter, so it is not pure. */
static int is_pure_value(jm_program *prog, int32_t idx) {
    if (prog->nodes[idx].kind == JN_IDENT && prog->nodes[idx].sym >= 0) {
        return 1;
    }
    return is_droppable_init(prog, idx);
}

/* If node assigns `x = EXPR` to a local that is never read, the store is dead -- ECMA-262 makes only
   EXPR's evaluation observable. Decrement x's write count (a later pass drops the now-unwritten binding)
   and return EXPR, else -1. */
static int32_t dead_store_value(jm_program *prog, int32_t node) {
    if (prog->nodes[node].kind != JN_ASSIGN || prog->nodes[node].op != JT_ASSIGN ||
        prog->nodes[prog->nodes[node].a].kind != JN_IDENT) {
        return -1;
    }
    int32_t target = prog->nodes[prog->nodes[node].a].sym;
    if (target < 0 || prog->syms[target].refs != 0) {
        return -1;
    }
    prog->syms[target].writes--;
    return prog->nodes[node].b;
}

/* Simplify a comma sequence: replace a dead store `x=EXPR` (x never read) with EXPR, fold `(x=EXPR, x)`
   -- the shape the fold pass leaves after merging `x=EXPR; return x` -- down to EXPR when x is a local
   written once (this assignment) and read once (the trailing element), and drop any non-final element
   whose value is discarded and whose evaluation is pure (13.16 runs each operand for effect; only the
   last is the sequence value). The (x=EXPR, x) fold is sound by adjacency, not flow analysis -- the
   shape a `var r; ...; r=f(); return r` temporary leaves behind. */
static void collapse_sequence(jm_program *prog, int32_t seq, int *changed) {
    for (int32_t elem = prog->nodes[seq].a; elem >= 0; elem = prog->nodes[elem].next) {
        int32_t init = dead_store_value(prog, elem);
        if (init >= 0) { /* `x=EXPR` with x never read is just EXPR (value and effects preserved) */
            int32_t after = prog->nodes[elem].next;
            prog->nodes[elem] = prog->nodes[init];
            prog->nodes[elem].next = after;
            *changed = 1;
            continue;
        }
        int32_t use = prog->nodes[elem].next;
        if (use < 0 || prog->nodes[elem].kind != JN_ASSIGN || prog->nodes[elem].op != JT_ASSIGN ||
            prog->nodes[prog->nodes[elem].a].kind != JN_IDENT) {
            continue;
        }
        int32_t target = prog->nodes[prog->nodes[elem].a].sym;
        if (target < 0 || prog->nodes[use].kind != JN_IDENT || prog->nodes[use].sym != target) {
            continue; /* not `t = EXPR` immediately followed by a read of the same local t */
        }
        int32_t after = prog->nodes[use].next;
        if (prog->syms[target].refs == 1 && prog->syms[target].writes == 1) {
            prog->nodes[elem] = prog->nodes[prog->nodes[elem].b]; /* `(t=EXPR, t)` with t used once -> EXPR */
            prog->nodes[elem].next = after;
            prog->syms[target].refs = 0;
            prog->syms[target].writes = 0;
        } else {
            /* `(t=EXPR, t)` with t used elsewhere too -> `t=EXPR`: the assignment already yields t's new
               value, so the trailing read adds nothing (nothing runs between to change t) */
            prog->nodes[elem].next = after;
            prog->syms[target].refs--;
        }
        *changed = 1;
    }
    int32_t prev = -1;
    for (int32_t elem = prog->nodes[seq].a; elem >= 0;) {
        int32_t after = prog->nodes[elem].next;
        if (after >= 0 && is_pure_value(prog, elem)) {
            if (prev < 0) {
                prog->nodes[seq].a = after;
            } else {
                prog->nodes[prev].next = after;
            }
            elem = after;
            *changed = 1;
        } else {
            prev = elem;
            elem = after;
        }
    }
    int32_t only = prog->nodes[seq].a; /* a sequence always keeps its last element; if that is the only
                                          one left, the sequence is just that element */
    if (prog->nodes[only].next < 0) {
        int32_t seq_next = prog->nodes[seq].next;
        prog->nodes[seq] = prog->nodes[only];
        prog->nodes[seq].next = seq_next;
        *changed = 1;
    }
}

/* Descend a chain of statements or expressions, dropping a statement-level dead store, collapsing
   sequences, and recursing. `x=EXPR;` where x is never read keeps only EXPR -- discarded when EXPR is
   pure, else left as an expression statement for its side effect. */
static void collapse_chain(jm_program *prog, int32_t first, int *changed) {
    for (int32_t node = first; node >= 0; node = prog->nodes[node].next) {
        if (prog->nodes[node].kind == JN_EXPR_STMT) {
            int32_t init = dead_store_value(prog, prog->nodes[node].a);
            if (init >= 0) {
                prog->nodes[node].kind = is_pure_value(prog, init) ? JN_EMPTY : prog->nodes[node].kind;
                if (prog->nodes[node].kind == JN_EXPR_STMT) {
                    prog->nodes[node].a = init;
                }
                *changed = 1;
            }
        }
        collapse_walk(prog, node, changed);
    }
}

/* Descend into every nested statement list (block, function/arrow body, switch case) and every
   expression that may hold one, mirroring the fold pass's traversal. */
/* A function/class expression's self-name binds only inside its own body (13.2.4 / 15.7.4); with
   zero reads it names nothing, so the expression prints anonymous and the name's slot is freed. */
static void drop_unread_expr_name(jm_program *prog, jm_node *node, int *changed) {
    /* a named expression always resolved its self-name, so str != NULL implies a live sym */
    if ((node->flags & JN_F_EXPR) && node->str != NULL && prog->syms[node->sym].refs == 0) {
        node->str = NULL;
        node->str_len = 0;
        node->sym = -1;
        *changed = 1;
    }
}

static void collapse_walk(jm_program *prog, int32_t idx, int *changed) {
    if (idx < 0) {
        return;
    }
    jm_node *node = &prog->nodes[idx];
    switch (node->kind) {
    case JN_BLOCK:
        collapse_chain(prog, node->a, changed);
        return;
    case JN_MEMBER_EXPR:
        collapse_walk(prog, node->a, changed);
        if (node->flags & JN_F_COMPUTED) {
            collapse_walk(prog, node->b, changed);
        }
        return;
    case JN_PROP:
    case JN_MEMBER:
        if (node->flags & JN_F_COMPUTED) {
            collapse_walk(prog, node->a, changed);
        }
        collapse_walk(prog, node->b, changed);
        return;
    case JN_SEQ:
        collapse_chain(prog, node->a, changed); /* recurse into elements before simplifying the sequence */
        collapse_sequence(prog, idx, changed);  /* may unwrap the sequence node itself */
        return;
    case JN_ARRAY:
    case JN_OBJECT:
    case JN_TEMPLATE:
        collapse_chain(prog, node->a, changed);
        return;
    case JN_CALL:
    case JN_NEW:
        collapse_walk(prog, node->a, changed);  /* callee */
        collapse_chain(prog, node->b, changed); /* arguments */
        return;
    case JN_CLASS:
        drop_unread_expr_name(prog, node, changed);
        collapse_walk(prog, node->a, changed);  /* superclass */
        collapse_chain(prog, node->b, changed); /* members */
        return;
    case JN_FUNC:
        drop_unread_expr_name(prog, node, changed);
        collapse_chain(prog, node->a, changed); /* params (defaults may hold a function) */
        collapse_walk(prog, node->b, changed);  /* body */
        return;
    case JN_ARROW:
        collapse_chain(prog, node->a, changed); /* params (defaults may hold a function) */
        collapse_walk(prog, node->b, changed);  /* body: a block, or an arrow expression */
        return;
    case JN_VAR:
        for (int32_t declr = node->a; declr >= 0; declr = prog->nodes[declr].next) {
            collapse_walk(prog, prog->nodes[declr].b, changed);
        }
        return;
    case JN_SWITCH:
        collapse_walk(prog, node->a, changed);
        for (int32_t clause = node->b; clause >= 0; clause = prog->nodes[clause].next) {
            collapse_walk(prog, prog->nodes[clause].a, changed);
            collapse_chain(prog, prog->nodes[clause].b, changed);
        }
        return;
    default:
        collapse_walk(prog, node->a, changed);
        collapse_walk(prog, node->b, changed);
        collapse_walk(prog, node->c, changed);
        collapse_walk(prog, node->d, changed);
        return;
    }
}

/* Whether an unused binding's initializer can be discarded along with it: it has no observable side
   effect, so dropping `var/let/const name = init` (when name is never read) changes nothing. A literal,
   a function/arrow expression and a unary over a pure operand (`void 0`, `!0`, `-1`) are pure. A call,
   member access, `new`, identifier read (a free name may throw ReferenceError) or anything else is
   conservatively kept -- the binding then stays rather than risk losing a side effect (ECMA-262 13.3,
   14.3.2 spell out that only the initializer evaluation is observable). */
static int is_droppable_init(jm_program *prog, int32_t init) {
    if (init < 0) {
        return 1; /* a bare `var x;` declares nothing observable */
    }
    switch (prog->nodes[init].kind) {
    case JN_NUM:
    case JN_STRING:
    case JN_REGEX:
    case JN_BIGINT:
    case JN_FUNC:
    case JN_ARROW:
        return 1;
    case JN_UNARY:
        return is_droppable_init(prog, prog->nodes[init].a);
    default:
        return 0;
    }
}

/* Drop a local binding that is never referenced (dead-code elimination, ECMA-262 has no observable
   effect for an unread binding). A function declaration is dropped whole; a var/let/const declarator is
   dropped when its initializer is side-effect-free -- one declarator is unlinked from its statement, and
   the statement is emptied only when its last declarator goes (so `var a=g(),x=1` loses just `x=1`).
   Confined to non-global scopes so a top-level (observable) name is never removed, and the whole pass is
   skipped under with/eval (the caller guards on poisoned), where a name may resolve dynamically.
   References are counted across nested scopes by the walk, so a binding captured by a closure has
   refs > 0 and is kept. */
static int drop_unused(jm_program *prog, int32_t global) {
    int changed = 0;
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        if (prog->syms[sym].refs != 0 || prog->syms[sym].writes != 0 || prog->syms[sym].decl_node < 0 ||
            prog->syms[sym].scope == global) {
            continue; /* a written binding (even if never read) keeps its declaration; see dead stores */
        }
        int32_t stmt = prog->syms[sym].decl_node;
        if (prog->syms[sym].decl == 4) { /* an unused function declaration: drop the whole statement */
            prog->nodes[stmt].kind = JN_EMPTY;
            prog->syms[sym].decl_node = -2;
            changed = 1;
            continue;
        }
        int32_t prev = -1; /* find this binding's declarator among the statement's declarators */
        /* GCOVR_EXCL_BR_START: decl_node records the statement holding this binding, so the declarator is
           always present and the loop always breaks at it -- the declr < 0 exhaustion never runs */
        for (int32_t declr = prog->nodes[stmt].a; declr >= 0; prev = declr, declr = prog->nodes[declr].next) {
            /* GCOVR_EXCL_BR_STOP */
            int32_t target = prog->nodes[declr].a;
            if (prog->nodes[target].kind != JN_IDENT || prog->nodes[target].sym != sym) {
                continue;
            }
            if (!is_droppable_init(prog, prog->nodes[declr].b)) {
                break; /* a side-effecting initializer keeps the whole declarator */
            }
            if (prev < 0) {
                prog->nodes[stmt].a = prog->nodes[declr].next;
            } else {
                prog->nodes[prev].next = prog->nodes[declr].next;
            }
            if (prog->nodes[stmt].a < 0) { /* removed the last declarator: the statement is now empty */
                prog->nodes[stmt].kind = JN_EMPTY;
            }
            prog->syms[sym].decl_node = -2;
            changed = 1;
            break;
        }
    }
    return changed;
}

static int chain_contains(jm_program *prog, int32_t first, int32_t target);

/* Whether target sits anywhere inside the subtree rooted at idx (its own siblings excluded);
   callers pass real nodes, chain_contains skips the absent links. */
static int subtree_contains(jm_program *prog, int32_t idx, int32_t target) {
    if (idx == target) {
        return 1;
    }
    const jm_node *node = &prog->nodes[idx];
    return chain_contains(prog, node->a, target) || chain_contains(prog, node->b, target) ||
           chain_contains(prog, node->c, target) || chain_contains(prog, node->d, target);
}

static int chain_contains(jm_program *prog, int32_t first, int32_t target) {
    for (int32_t idx = first; idx >= 0; idx = prog->nodes[idx].next) {
        if (subtree_contains(prog, idx, target)) {
            return 1;
        }
    }
    return 0;
}

/* The last element of a comma sequence -- the operand whose value the sequence yields (13.16). */
static int32_t seq_value(jm_program *prog, int32_t seq) {
    int32_t elem = prog->nodes[seq].a;
    while (prog->nodes[elem].next >= 0) {
        elem = prog->nodes[elem].next;
    }
    return elem;
}

/* Give a `{ x }` shorthand an explicit key so its value slot can take an inlined expression:
   the fresh key ident carries the original name and no symbol (a property name, never renamed).
   1 on success (or nothing to expand), 0 when the key allocation failed. */
static int expand_shorthand_ref(jm_program *prog, int32_t sym) {
    int32_t prop = prog->syms[sym].ref_prop;
    if (prop < 0) {
        return 1;
    }
    int32_t key = jm_node_new(prog, JN_IDENT);
    if (key < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the shorthand */
        return 0;  /* GCOVR_EXCL_LINE */
    }
    prog->nodes[key].str = prog->syms[sym].name;
    prog->nodes[key].str_len = prog->syms[sym].name_len;
    prog->nodes[prop].a = key;
    prog->nodes[prop].b = prog->syms[sym].ref_node;
    prog->nodes[prop].flags &= (uint16_t)~JN_F_SHORTHAND;
    return 1;
}

/* Whether the binding's declaration dominates its one read with a value that survives the move:
   a value let/const dominates anywhere (TDZ), a regex may ride only into the very next
   single-shot statement (a fresh object per evaluation, 13.2.7.5, so the read must run at most
   once per pass over the declaration and outside any nested function), and a value var must sit
   in its function body's first statement -- nothing executes before it except the initializers of
   the declarators left of its own, so a read there keeps the binding. */
static int read_sees_initialized(jm_program *prog, int32_t sym, int32_t stmt, int32_t declr, int32_t init,
                                 int32_t ref) {
    if (prog->syms[sym].decl >= 1 && is_value_literal(prog, init)) {
        return 1;
    }
    if (prog->nodes[init].kind == JN_REGEX && prog->syms[sym].ref_scope == prog->syms[sym].scope &&
        prog->nodes[stmt].next >= 0 && single_shot_stmt(prog, prog->nodes[stmt].next) &&
        subtree_contains(prog, prog->nodes[stmt].next, ref)) {
        return 1;
    }
    if (prog->syms[sym].decl != 0 || !is_value_literal(prog, init) ||
        stmt != prog->scopes[prog->syms[sym].scope].first_stmt) {
        return 0;
    }
    for (int32_t earlier = prog->nodes[stmt].a; earlier != declr; earlier = prog->nodes[earlier].next) {
        if (subtree_contains(prog, earlier, ref)) {
            return 0;
        }
    }
    return 1;
}

/* Inline a single-declarator binding that is read exactly once into that one read and drop the
   declaration (ECMA-262 propagates the assigned value; the optimization just removes the name). The
   walk records exactly one read (`refs == 1`) and no writes (`writes == 0`), so the value the
   declaration assigns is the value that single read sees, and the read is a genuine read position --
   never an assignment or for-in target the inlined value would land on illegally. Two cases are sound:

     - a let/const initialized to a literal: the value is constant and the declaration dominates its use
       (block scope / TDZ), so the read takes that value wherever it sits, a nested closure included;

     - any binding whose declaration is immediately followed by `return x` / `throw x` (x being the one
       read): nothing executes between the declaration and the read and the read is not inside a
       closure, so moving the initializer onto the jump preserves both value and evaluation order even
       when the initializer has side effects. A `var` is inlined only this way, never anywhere-at-once:
       a `var` declaration may be conditional or captured, so only adjacency proves it dominates.
       A literal initializer also rides to the tail of the adjacent jump's comma sequence
       (`var x=1;return g(),x` -> `return g(),1`): the sequence's earlier operands run after the
       declaration and cannot change a never-written literal (13.16 evaluates left to right).

   The emptied declaration is removed from its statement chain by the next fold pass. */
static int inline_single_use(jm_program *prog, int32_t global) {
    int changed = 0;
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        if (prog->syms[sym].refs != 1 || prog->syms[sym].writes != 0 || prog->syms[sym].decl_node < 0 ||
            prog->syms[sym].scope == global) {
            continue; /* one read, never written: the read is where the declaration's value goes */
        }
        int32_t ref = prog->syms[sym].ref_node;
        if (prog->syms[sym].decl == 4) {
            /* a function used once becomes an expression at that use, dropping the declaration and the
               name (refs == 1 means no self-reference). Only into its own scope: a use in a loop body or
               a nested function runs repeatedly and would build a fresh closure each time (13.2.4). */
            if (prog->syms[sym].ref_scope != prog->syms[sym].scope) {
                continue;
            }
            if (!expand_shorthand_ref(prog, sym)) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                continue;                           /* GCOVR_EXCL_LINE */
            }
            int32_t next = prog->nodes[ref].next;
            int32_t decl = prog->syms[sym].decl_node;
            prog->nodes[ref] = prog->nodes[decl];
            prog->nodes[ref].flags |= JN_F_EXPR;
            prog->nodes[ref].str = NULL;
            prog->nodes[ref].str_len = 0;
            prog->nodes[ref].next = next;
            prog->nodes[decl].kind = JN_EMPTY;
            prog->syms[sym].decl_node = -2;
            changed = 1;
            continue;
        }
        int32_t stmt = prog->syms[sym].decl_node;
        /* decl_node is recorded only on a var/let/const statement whose ident-target declarator
           binds this symbol (functions were handled above), so the search always lands; it walks
           past destructuring siblings, whose bindings never record a decl_node */
        int32_t declr = prog->nodes[stmt].a;
        int32_t declr_prev = -1;
        while (!(prog->nodes[prog->nodes[declr].a].kind == JN_IDENT && prog->nodes[prog->nodes[declr].a].sym == sym)) {
            declr_prev = declr;
            declr = prog->nodes[declr].next;
        }
        int32_t init = prog->nodes[declr].b;
        if (init < 0) {
            continue; /* a bare `var x;` declares no value to propagate */
        }
        int lone = declr_prev < 0 && prog->nodes[declr].next < 0;
        if (!read_sees_initialized(prog, sym, stmt, declr, init, ref)) {
            if (!lone) {
                continue; /* moving an impure initializer past its sibling declarators would reorder them */
            }
            int32_t jump = prog->nodes[stmt].next; /* the statement right after the decl */
            if (jump < 0 || (prog->nodes[jump].kind != JN_RETURN && prog->nodes[jump].kind != JN_THROW)) {
                continue; /* not a literal let/const, and not an adjacent `return` / `throw` */
            }
            int32_t target = prog->nodes[jump].a;
            if (target != ref && !(is_const_literal(prog, init) && target >= 0 && prog->nodes[target].kind == JN_SEQ &&
                                   seq_value(prog, target) == ref)) {
                continue; /* the one read is not where the adjacent jump's value comes from */
            }
        }
        if (!expand_shorthand_ref(prog, sym)) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            continue;                           /* GCOVR_EXCL_LINE */
        }
        int32_t next = prog->nodes[ref].next;
        prog->nodes[ref] = prog->nodes[init];
        prog->nodes[ref].next = next;
        if (lone) {
            prog->nodes[stmt].kind = JN_EMPTY;
        } else if (declr_prev < 0) { /* unlink just this declarator; its siblings keep the statement */
            prog->nodes[stmt].a = prog->nodes[declr].next;
        } else {
            prog->nodes[declr_prev].next = prog->nodes[declr].next;
        }
        prog->syms[sym].decl_node = -2; /* mark inlined so assign_slots spends no name on it */
        changed = 1;
    }
    return changed;
}

/* Replace every read of a qualifying symbol with a copy of its literal, counting the copies; a
   `{ x }` shorthand read first gains its explicit key. The declarator still stands while this
   runs, so if a key allocation fails mid-walk the untouched reads keep the binding -- the
   caller unlinks it only once every read was replaced. */
/* One planned propagation: every read of `sym` (its declarator target excluded) becomes a copy of
   the literal at `init`; `replaced` counts the copies so the caller unlinks the declarator only
   once every read was rewritten. */
typedef struct {
    int32_t sym;
    int32_t init;
    int32_t target;
    int32_t replaced;
} jm_propagation;

/* Apply every planned propagation in one tree walk. A `{ x }` shorthand read first gains its
   explicit key; if that allocation fails mid-walk the declarators are all still linked, and a
   partially-rewritten binding stays consistent -- the remaining reads see the binding holding the
   same value the copies carry. */
static void replace_reads(jm_program *prog, int32_t idx, jm_propagation *plans, int32_t count) {
    for (; idx >= 0; idx = prog->nodes[idx].next) {
        if (prog->nodes[idx].kind == JN_PROP && (prog->nodes[idx].flags & JN_F_SHORTHAND)) {
            int32_t read = prog->nodes[idx].a;
            for (int32_t plan = 0; plan < count; plan++) {
                if (prog->nodes[read].sym != plans[plan].sym) {
                    continue;
                }
                int32_t key = jm_node_new(prog, JN_IDENT);
                if (key < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the binding */
                    return;    /* GCOVR_EXCL_LINE */
                }
                prog->nodes[key].str = prog->syms[plans[plan].sym].name;
                prog->nodes[key].str_len = prog->syms[plans[plan].sym].name_len;
                prog->nodes[idx].a = key;
                prog->nodes[idx].b = read;
                prog->nodes[idx].flags &= (uint16_t)~JN_F_SHORTHAND;
                break;
            }
        }
        if (prog->nodes[idx].kind == JN_IDENT && prog->nodes[idx].sym >= 0) {
            for (int32_t plan = 0; plan < count; plan++) {
                if (prog->nodes[idx].sym != plans[plan].sym || idx == plans[plan].target) {
                    continue;
                }
                int32_t next = prog->nodes[idx].next;
                prog->nodes[idx] = prog->nodes[plans[plan].init];
                prog->nodes[idx].next = next;
                plans[plan].replaced++;
                break;
            }
            if (prog->nodes[idx].kind != JN_IDENT) {
                continue; /* just rewritten into the literal: nothing below to walk */
            }
        }
        replace_reads(prog, prog->nodes[idx].a, plans, count);
        replace_reads(prog, prog->nodes[idx].b, plans, count);
        replace_reads(prog, prog->nodes[idx].c, plans, count);
        replace_reads(prog, prog->nodes[idx].d, plans, count);
    }
}

/* Inline every read of a never-written binding holding a short value literal, when the copies cost
   less than the binding: N reads of a one-character mangled name plus the declarator (name, `=`,
   value, separator) against N copies of the value -- N*(len-1) < 3+len, so a one-character literal
   always wins and longer ones need fewer reads. The domination rules match inline_single_use: a
   let/const dominates by TDZ, a var must sit in its function body's first statement with no read
   left of its own declarator (min_ref, in parse = textual order). The qualifying bindings are
   collected first and rewritten in a single tree walk. */
static int propagate_value_literals(jm_program *prog, int32_t global) {
    jm_propagation *plans = NULL;
    int32_t count = 0;
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        if (prog->syms[sym].refs < 2 || prog->syms[sym].writes != 0 || prog->syms[sym].decl_node < 0 ||
            prog->syms[sym].scope == global || prog->syms[sym].decl > 2) {
            continue; /* decl > 2 also skips functions; only var/let/const record a JN_VAR decl_node */
        }
        int32_t stmt = prog->syms[sym].decl_node;
        int32_t declr = prog->nodes[stmt].a;
        while (!(prog->nodes[prog->nodes[declr].a].kind == JN_IDENT && prog->nodes[prog->nodes[declr].a].sym == sym)) {
            declr = prog->nodes[declr].next;
        }
        int32_t init = prog->nodes[declr].b;
        if (init < 0 || !is_value_literal(prog, init)) {
            continue;
        }
        Py_ssize_t len = literal_print_len(prog, init);
        if (prog->syms[sym].refs * (len - 1) >= 3 + len) {
            continue; /* the copies would outweigh the binding */
        }
        if (prog->syms[sym].decl == 0 &&
            (stmt != prog->scopes[prog->syms[sym].scope].first_stmt || prog->syms[sym].min_ref < declr)) {
            continue; /* only the function body's first statement runs before every possible read */
        }
        if (plans == NULL) {
            plans = jm_malloc((size_t)prog->sym_count * sizeof(jm_propagation));
            if (plans == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                return 0;        /* GCOVR_EXCL_LINE */
            }
        }
        plans[count++] = (jm_propagation){.sym = sym, .init = init, .target = prog->nodes[declr].a, .replaced = 0};
    }
    if (plans == NULL) {
        return 0;
    }
    replace_reads(prog, prog->nodes[prog->root].a, plans, count);
    int changed = 0;
    for (int32_t plan = 0; plan < count; plan++) {
        int32_t sym = plans[plan].sym;
        if (plans[plan].replaced != prog->syms[sym].refs) { /* GCOVR_EXCL_BR_LINE: an allocation failed */
            continue;                                       /* GCOVR_EXCL_LINE */
        }
        int32_t stmt = prog->syms[sym].decl_node;
        int32_t declr = prog->nodes[stmt].a;
        int32_t declr_prev = -1;
        while (prog->nodes[declr].a != plans[plan].target) {
            declr_prev = declr;
            declr = prog->nodes[declr].next;
        }
        if (declr_prev < 0 && prog->nodes[declr].next < 0) {
            prog->nodes[stmt].kind = JN_EMPTY;
        } else if (declr_prev < 0) {
            prog->nodes[stmt].a = prog->nodes[declr].next;
        } else {
            prog->nodes[declr_prev].next = prog->nodes[declr].next;
        }
        prog->syms[sym].decl_node = -2; /* no name is spent on the gone binding */
        changed = 1;
    }
    jm_free(plans);
    return changed;
}

/* Clear the resolved-symbol and scope tables so a fresh analysis can run over the (now transformed)
   tree. Every compress pass re-derives read/write counts from scratch, which is what lets the
   transforms cascade -- one drop exposing the next -- and keeps the result stable under
   re-minification. A label name the previous walk assigned is freed here (only the final naming pass's
   names outlive the analysis). */
static void reset_analysis(jm_program *prog) {
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        jm_free(prog->syms[sym].mangled);
        prog->syms[sym].mangled = NULL;
    }
    prog->sym_count = 0;
    prog->scope_count = 0;
}

/* Build the scope tree and resolve every reference into a fresh mangler, populating each binding's
   read/write counts, its one read node, and its declaring statement. Returns the global scope, or -1 on
   allocation failure. */
static int32_t analyze(M *mangler, jm_program *prog) {
    reset_analysis(prog);
    mangler->prog = prog;
    int32_t global = jm_scope_new(prog, -1, 1);
    if (global < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;    /* GCOVR_EXCL_LINE */
    }
    mangler->global = global;
    int32_t stmts = prog->nodes[prog->root].a;
    hoist_vars(mangler, stmts, global);
    hoist_block(mangler, stmts, global);
    walk_chain(mangler, stmts, global, 0);
    return global;
}

static void mangler_free(M *mangler) {
    jm_free(mangler->visible.slots);
    jm_free(mangler->frees.slots);
    jm_free(mangler->labels.slots);
    jm_free(mangler->undo);
}

/* One compression pass over the whole program: resolve afresh, then run the tree-shrinking transforms.
   Returns 1 when a transform changed the tree (the driver runs another pass), 0 at a fixpoint, and -1
   when the analysis is unusable -- a with/eval poisoned name resolution, or an allocation failed -- so
   the driver stops. Names are never assigned here; that is the final jm_mangle pass. */
int jm_compress(jm_program *prog) {
    M mangler = {0};
    int32_t global = analyze(&mangler, prog);
    int result = -1;
    /* top-level bindings are observable and never touched; a with/eval poisons the whole program */
    if (!mangler.poisoned) {
        /* GCOVR_EXCL_BR_START: the remaining guards are allocation-failure paths */
        if (global >= 0 && !mangler.failed && !mangler.visible.failed && !mangler.frees.failed) {
            /* GCOVR_EXCL_BR_STOP */
            int changed = 0;
            collapse_chain(prog, prog->nodes[prog->root].a, &changed);
            changed |= drop_unused(prog, global);
            changed |= inline_single_use(prog, global);
            changed |= propagate_value_literals(prog, global);
            result = changed;
        }
    }
    mangler_free(&mangler);
    return result;
}

/* Assign the shortest legal names to the surviving local bindings. Run once after compression settles;
   it changes no tree shape, only names. Skipped entirely under with/eval, where resolution is unsafe. */
void jm_mangle(jm_program *prog) {
    M mangler = {0};
    int32_t global = analyze(&mangler, prog);
    if (!mangler.poisoned) {
        /* GCOVR_EXCL_BR_START: the remaining guards are allocation-failure paths */
        if (global >= 0 && !mangler.failed && !mangler.visible.failed && !mangler.frees.failed) {
            /* GCOVR_EXCL_BR_STOP */
            /* reserve every *kept* function/class declaration name globally so a mangled binding in any
               scope can never be assigned a name that shadows one. A declaration in a non-global
               function scope is renamed, not kept (see assign_slots), so it is not reserved. */
            for (int32_t sym = 0; sym < prog->sym_count; sym++) {
                if ((prog->syms[sym].decl == 4 || prog->syms[sym].decl == 6) &&
                    (prog->syms[sym].scope == global || prog->scopes[prog->syms[sym].scope].kind != 1)) {
                    hslot *slot = htab_slot(&mangler.frees, prog->syms[sym].name, prog->syms[sym].name_len, 1);
                    if (slot != NULL) { /* GCOVR_EXCL_BR_LINE: the false branch is an allocation failure */
                        slot->val = 1;
                    }
                }
            }
            for (int32_t child = prog->scopes[global].first_child; child >= 0;
                 child = prog->scopes[child].next_sibling) {
                assign_slots(&mangler, child, 0);
            }
            assign_names_by_frequency(&mangler);
        }
    }
    mangler_free(&mangler);
}
