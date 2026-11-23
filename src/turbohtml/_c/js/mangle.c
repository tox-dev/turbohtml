/* Identifier mangling: rename local bindings to short names.

   A post-parse pass over the arena AST. It builds the lexical scope tree, declares
   every binding, resolves every identifier reference to its binding (or to a free /
   global name), then assigns the shortest legal names to the locals.

   Correctness is by construction and conservative - under-renaming is safe, only a
   wrong rename corrupts:
   - A scope reached by `with` or a direct `eval(` is poisoned; nothing inside it (or
     any enclosing scope) is renamed, because those constructs resolve names
     dynamically.
   - Every free name, and every kept (top-level or pinned) binding's name, is forbidden
     as a new name, so a binding can never be renamed onto a name that some reference
     resolves to unrenamed - even a reference this pass failed to resolve keeps its
     (free) name and that name is reserved.
   - A new name also avoids every reserved word and every enclosing binding's name in
     scope, so it can neither be a keyword nor shadow an outer binding that is visible.
   Naming runs in two steps: each binding gets a slot index (inherited down the scope
   tree so a nested binding never shares a slot with a visible ancestor, while disjoint
   sibling scopes reuse slots), then the shortest names go to the slots whose bindings
   are referenced most -- the frequency ordering esbuild/terser/swc use. */

#include "core/work_steps.h"
#include "js/internal.h"

#ifndef JM_STANDALONE
#include "core/hash.h"
#endif

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
    int keyed; /* 1 once the slots moved to th_name_hash (htab_walked_far) */
} htab;

static uint32_t name_hash(const Py_UCS4 *name, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t index = 0; index < len; index++) {
        hash = (hash ^ (uint32_t)name[index]) * 16777619u;
    }
    return hash;
}

/* The source picks its identifiers, and FNV-1a lets it pick ones that share a home. Inside the extension a table whose
   walk passes TH_FAST_RUN_LIMIT slots moves to th_name_hash under the per-process key (core/hash.h); the standalone
   engine has no interpreter to draw a key from and never moves. */
#ifdef JM_STANDALONE
#define TH_FAST_RUN_LIMIT INT32_MAX
#define name_hash_keyed name_hash
#else
static uint32_t name_hash_keyed(const Py_UCS4 *name, Py_ssize_t len) {
    return th_name_hash(name, len * (Py_ssize_t)sizeof(Py_UCS4));
}
#endif

/* Move the slots into cap of them under the table's hash. The walk starts at an empty slot, so a run that wraps past
   the end of the array is re-seated in its own order: no name of a doubled table lands further from its home than it
   sat before, and the bound htab_slot keeps on how far a name sits holds on. */
static void htab_resize(htab *table, int32_t cap) {
    hslot *slots = jm_calloc((size_t)cap, sizeof(hslot));
    if (slots == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        table->failed = 1; /* GCOVR_EXCL_LINE */
        return;            /* GCOVR_EXCL_LINE */
    }
    uint32_t mask = (uint32_t)cap - 1;
    int32_t start = 0;
    while (start < table->cap && table->slots[start].name != NULL) {
        start++;
    }
    for (int32_t step = 0; step < table->cap; step++) {
        int32_t index = (start + step) & (table->cap - 1);
        if (table->slots[index].name != NULL) {
            const Py_UCS4 *name = table->slots[index].name;
            Py_ssize_t len = table->slots[index].len;
            uint32_t probe = (table->keyed ? name_hash_keyed(name, len) : name_hash(name, len)) & mask;
            while (slots[probe].name != NULL) {
                TH_WORK_STEP();
                probe = (probe + 1) & mask;
            }
            slots[probe] = table->slots[index];
        }
    }
    jm_free(table->slots);
    table->slots = slots;
    table->cap = cap;
}

/* htab_slot for a table on th_name_hash, where walks need no bound. Out of line, with the moves below, so the FNV-1a
   loop that callers inline stays as small as it was before tables could move. */
static TH_NOINLINE hslot *htab_slot_keyed(htab *table, const Py_UCS4 *name, Py_ssize_t len, int create) {
    uint32_t mask = (uint32_t)table->cap - 1;
    for (uint32_t probe = name_hash_keyed(name, len) & mask;; probe = (probe + 1) & mask) {
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
        TH_WORK_STEP();
    }
}

/* A walk on FNV-1a ended at the empty slot probe. No name is seated more than TH_FAST_RUN_LIMIT slots from its home, so
   a hit walks no further, but a miss walks to the end of the run its home falls in, and the source can line up homes
   into one long run without seating any name far. So when the TH_FAST_RUN_LIMIT + 1 slots before probe are all filled
   (testing the farthest first settles most calls with one load), move the table to th_name_hash for the rest of its
   life and return 1. The test stops at the limit, so it is no counted work step. */
static TH_NOINLINE int htab_walked_far(htab *table, uint32_t probe) {
    uint32_t mask = (uint32_t)table->cap - 1;
    for (uint32_t back = (uint32_t)TH_FAST_RUN_LIMIT + 1; back > 0; back--) {
        if (table->slots[(probe - back) & mask].name == NULL) {
            return 0;
        }
    }
    table->keyed = 1;
    htab_resize(table, table->cap);
    return 1;
}

/* Find the slot for name; with create, claim an empty slot (value -1) and grow the
   table when it passes a 3/4 load. Returns NULL only when create is 0 and absent, or
   on allocation failure. */
static inline hslot *htab_slot(htab *table, const Py_UCS4 *name, Py_ssize_t len, int create) {
    if (create && (table->cap == 0 || (table->count + 1) * 4 >= table->cap * 3)) {
        htab_resize(table, table->cap ? table->cap * 2 : 64);
        if (table->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
    }
    if (table->cap == 0) {
        return NULL; /* an empty read-only table */
    }
    if (table->keyed) {
        return htab_slot_keyed(table, name, len, create);
    }
    uint32_t mask = (uint32_t)table->cap - 1;
    uint32_t probe = name_hash(name, len) & mask;
    for (;;) {
        hslot *slot = &table->slots[probe];
        if (slot->name == NULL) {
            /* a table of no more names than the limit holds no run that long */
            if (table->count > TH_FAST_RUN_LIMIT && htab_walked_far(table, probe)) {
                if (table->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    return NULL;     /* GCOVR_EXCL_LINE */
                }
                return htab_slot_keyed(table, name, len, create);
            }
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
        TH_WORK_STEP();
        probe = (probe + 1) & mask;
    }
}

static int is_reserved(const Py_UCS4 *name, Py_ssize_t len) {
    if (len == 1) {
        return 0;
    }
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
    htab frees;   /* free names, plus kept binding names, reserved (value 1) */
    htab labels;  /* label name -> its symbol (a separate namespace; -1 when kept verbatim) */
    undo_rec *undo;
    int32_t undo_count;
    int32_t undo_cap;
    int32_t slot_count;  /* number of distinct rename slots assigned across the program */
    int32_t global;      /* the global scope, where label symbols are parked */
    int32_t label_depth; /* count of lexically-enclosing labels, indexing their short names */
    int32_t args_scope;  /* scope of the innermost enclosing non-arrow function, whose `arguments` a
                            free `arguments` read names (-1 at the top level) */
    int32_t catch_depth; /* enclosing catch bodies: only a var inside one can name a catch parameter */
    int32_t stmt;        /* the innermost enclosing expression, return or throw statement (-1 outside one) */
    int track_stmt;      /* a regex literal was parsed, so reads record their statement (see walk) */
    int32_t fn_scope;    /* the scope walk_function last opened */
    int32_t fn_now;      /* the function scope the walk stands in */
    int32_t depth;       /* walk calls on the stack: how deep the node being analyzed sits */
    int32_t deepest;     /* the largest depth reached under the function being analyzed */
    int poisoned;        /* a with / eval was seen: stop renaming entirely */
    int failed;
    int32_t *crossings; /* (binding, scope) pairs: a reference in scope to a binding declared in another */
    int32_t crossing_count;
    int32_t crossing_cap;
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
        if (!grew || cap > INT32_MAX) { /* GCOVR_EXCL_BR_LINE: needs over two billion undo records */
            mangler->failed = 1;        /* GCOVR_EXCL_LINE */
            return;                     /* GCOVR_EXCL_LINE */
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

/* Record that scope names sym, which another scope declares: the bindings of the scopes between may not take its
   name. */
static void note_crossing(M *mangler, int32_t sym, int32_t scope) {
    if (mangler->crossing_count + 2 > mangler->crossing_cap) { /* the cap need not be even */
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)mangler->crossing_cap + 2, (size_t)mangler->crossing_cap,
                               (size_t)mangler->prog->node_count / 4 + 2, sizeof(int32_t), &cap, &bytes);
        if (!grew || cap > INT32_MAX) { /* GCOVR_EXCL_BR_LINE: needs over a billion crossing references */
            mangler->failed = 1;        /* GCOVR_EXCL_LINE */
            return;                     /* GCOVR_EXCL_LINE */
        }
        int32_t *grown = jm_realloc(mangler->crossings, bytes);
        if (grown == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            mangler->failed = 1; /* GCOVR_EXCL_LINE */
            return;              /* GCOVR_EXCL_LINE */
        }
        mangler->crossings = grown;
        mangler->crossing_cap = (int32_t)cap;
    }
    mangler->crossings[mangler->crossing_count++] = sym;
    mangler->crossings[mangler->crossing_count++] = scope;
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
    int32_t outer = slot->val;
    int32_t sym = jm_sym_new(mangler->prog, name, len, scope, decl);
    if (sym < 0) {           /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1; /* GCOVR_EXCL_LINE */
        return;              /* GCOVR_EXCL_LINE */
    }
    /* past a block or catch binding, inherit the function-scope binding that one shadows */
    if (outer >= 0 && mangler->prog->scopes[mangler->prog->syms[outer].scope].kind != 1) {
        outer = mangler->prog->syms[outer].shadowed_var;
    }
    mangler->prog->syms[sym].shadowed_var = outer;
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

/* Hoist every `var` (and var-style for-loop binding) in the statement list at idx to fn_scope. A var
   declaration is a statement (or a for head), and an expression holds statements only inside a function or
   class body, which starts its own var scope, so the descent follows statement positions alone and skips
   every expression. */
static void hoist_vars(M *mangler, int32_t idx, int32_t fn_scope) {
    while (idx >= 0) {
        jm_node *node = &mangler->prog->nodes[idx];
        switch (node->kind) {
        case JN_VAR:
            if (node->decl == 0) {
                for (int32_t declarator = node->a; declarator >= 0;
                     declarator = mangler->prog->nodes[declarator].next) {
                    declare_pattern(mangler, mangler->prog->nodes[declarator].a, fn_scope, 0);
                }
            }
            break;
        case JN_BLOCK:
        case JN_DOWHILE:
        case JN_LABEL:
            hoist_vars(mangler, node->a, fn_scope);
            break;
        case JN_IF:
            hoist_vars(mangler, node->b, fn_scope);
            hoist_vars(mangler, node->c, fn_scope);
            break;
        case JN_FOR:
            hoist_vars(mangler, node->a, fn_scope);
            hoist_vars(mangler, node->d, fn_scope);
            break;
        case JN_FORIN:
        case JN_FOROF:
            hoist_vars(mangler, node->a, fn_scope);
            hoist_vars(mangler, node->c, fn_scope);
            break;
        case JN_WHILE:
        case JN_WITH:
        case JN_SWITCH:
        case JN_CASE:
            hoist_vars(mangler, node->b, fn_scope);
            break;
        case JN_TRY:
            hoist_vars(mangler, node->a, fn_scope);
            hoist_vars(mangler, node->c, fn_scope);
            hoist_vars(mangler, node->d, fn_scope);
            break;
        default:
            break;
        }
        idx = node->next;
    }
}

/* Pin each binding of name from scope out to the one its enclosing function's own code reaches: the var Annex B adds
   to that function shadows the outer ones, so a catch parameter between them must not hide one from the pin. */
static void pin_reachable(jm_program *prog, int32_t scope, const Py_UCS4 *name, Py_ssize_t len) {
    int past = 0;
    for (int32_t at = scope; at >= 0; at = prog->scopes[at].parent) {
        int found = 0;
        for (int32_t sym = prog->scopes[at].first_sym; sym >= 0; sym = prog->syms[sym].scope_next) {
            if (name_eq(prog->syms[sym].name, prog->syms[sym].name_len, name, len)) {
                prog->syms[sym].pinned = 1;
                found = 1;
            }
        }
        past |= prog->scopes[at].kind == 1;
        if (found && past) {
            return;
        }
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
            /* Annex B.3.3: in sloppy code, running the block that declares a function also assigns it to a
               same-named var of the enclosing function, which code after the block reads. The analysis
               sees neither that var nor the assignment, so the block binding and every binding the name
               reaches from outside the block stay exactly as written. */
            int annex_b = mangler->prog->scopes[scope].kind != 1;
            if (annex_b) {
                pin_reachable(mangler->prog, scope, node->str, node->str_len);
            }
            declare(mangler, scope, node->str, node->str_len, 4);
            node->sym = resolve(mangler, node->str, node->str_len); /* so the name renames with its references */
            if (node->sym >= 0) { /* GCOVR_EXCL_BR_LINE: unresolved only on an allocation failure */
                jm_sym *sym = &mangler->prog->syms[node->sym];
                if (sym->decl == 4) {
                    sym->decl_node = idx; /* let drop_unused find an unused function */
                } else {
                    /* a same-scope parameter or var keeps its binding and the function assigns it on
                       entry (§10.2.11 steps 29.2 and 37.3); the declaration is a write, not a declarator */
                    sym->writes++;
                }
                sym->pinned |= (uint8_t)annex_b;
            }
        } else if (node->kind == JN_CLASS) { /* likewise a class is always a named declaration */
            declare(mangler, scope, node->str, node->str_len, 6);
            node->sym = resolve(mangler, node->str, node->str_len);
            if (node->sym >= 0) { /* GCOVR_EXCL_BR_LINE: unresolved only on an allocation failure */
                /* one in a block keeps its name like a block function does; one directly in a function
                   body renames */
                mangler->prog->syms[node->sym].pinned = mangler->prog->scopes[scope].kind != 1;
            }
        }
    }
}

static void walk_node(M *mangler, int32_t idx, int32_t scope, int bind);
static void walk_stmt(M *mangler, int32_t idx, int32_t scope);

/* Analyze the subtree at idx, keeping count of how deep the walk stands: the single-use function inline reads the
   depth of a read and the height of a declaration to know how tall the tree would be with the one under the other. */
static TH_FORCEINLINE void walk(M *mangler, int32_t idx, int32_t scope, int bind) {
    if (idx < 0) {
        return;
    }
    if (++mangler->depth > mangler->deepest) {
        mangler->deepest = mangler->depth;
    }
    walk_node(mangler, idx, scope, bind);
    mangler->depth--;
}

/* A `var` in a catch block may name the catch parameter (Annex B.3.4): it binds the enclosing function's var while
   its initializer assigns the parameter, so the statement declares one binding and writes another. Pin both: they
   keep the one spelling the statement gives them, and no pass drops a statement the var needs. The var is the
   function-scope binding the parameter shadows, recorded when the parameter was declared: searching the function's
   bindings for it here would cost a pass over all of them for every declarator. */
static void pin_catch_redeclaration(jm_program *prog, int32_t param) {
    prog->syms[param].pinned = 1;
    int32_t var = prog->syms[param].shadowed_var;
    if (var >= 0) { /* GCOVR_EXCL_BR_LINE: hoisting declared the var first unless an allocation failed */
        prog->syms[var].pinned = 1;
    }
}

/* Apply pin_catch_redeclaration to every catch parameter a var declaration target resolves to. */
static void pin_catch_redeclarations(jm_program *prog, int32_t idx) {
    const jm_node *node = &prog->nodes[idx];
    switch (node->kind) {
    case JN_IDENT:
        if (node->sym >= 0) { /* GCOVR_EXCL_BR_LINE: a hoisted var resolves unless an allocation failed */
            if (prog->syms[node->sym].decl == 5) {
                pin_catch_redeclaration(prog, node->sym);
            }
        }
        break;
    case JN_ARRAY:
        for (int32_t element = node->a; element >= 0; element = prog->nodes[element].next) {
            pin_catch_redeclarations(prog, element);
        }
        break;
    case JN_OBJECT:
        for (int32_t prop = node->a; prop >= 0; prop = prog->nodes[prop].next) {
            const jm_node *entry = &prog->nodes[prop];
            pin_catch_redeclarations(prog, entry->b >= 0 ? entry->b : entry->a); /* a shorthand or rest binds a */
        }
        break;
    case JN_ASSIGN: /* a default `target = init`, or a rest `...target` */
    case JN_SPREAD:
        pin_catch_redeclarations(prog, node->a);
        break;
    default:
        break;
    }
}

/* A sloppy function with a simple parameter list maps `arguments[i]` onto its i-th parameter
   (§10.4.4), so reading `arguments` reads every parameter and writing through it writes them. Count
   both on each parameter, once per read, so no pass drops an assignment to one as a dead store. Finding the
   parameters is a pass over the function's bindings, so the callers batch the reads instead of making a pass for
   each one. */
static void alias_parameters(M *mangler, int32_t reads) {
    for (int32_t sym = mangler->prog->scopes[mangler->args_scope].first_sym; sym >= 0;
         sym = mangler->prog->syms[sym].scope_next) {
        if (mangler->prog->syms[sym].decl == 3) {
            mangler->prog->syms[sym].refs += reads;
            mangler->prog->syms[sym].writes += reads;
            mangler->prog->syms[sym].ref_scope = -1;
        }
    }
}

/* Walk a chain of siblings. */
static TH_FORCEINLINE void walk_chain(M *mangler, int32_t first, int32_t scope, int bind) {
    if (first < 0) {
        return;
    }
    if (++mangler->depth > mangler->deepest) { /* siblings share a depth: charge it once for the chain */
        mangler->deepest = mangler->depth;
    }
    for (int32_t idx = first; idx >= 0; idx = mangler->prog->nodes[idx].next) {
        walk_node(mangler, idx, scope, bind);
    }
    mangler->depth--;
}

/* Walk the statements from first in a new block scope, declaring its block-level bindings first. */
static void walk_block(M *mangler, int32_t first, int32_t scope) {
    int32_t inner = jm_scope_new(mangler->prog, scope, 0);
    if (inner < 0) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1; /* GCOVR_EXCL_LINE */
        return;              /* GCOVR_EXCL_LINE */
    }
    int32_t mark = mangler->undo_count;
    hoist_block(mangler, first, inner);
    walk_chain(mangler, first, inner, 0);
    undo_to(mangler, mark);
}

/* Annex B.3.3: a function declaration as a sloppy if clause scopes as the sole statement of a block. */
static void walk_if_clause(M *mangler, int32_t clause, int32_t scope) {
    if (clause >= 0 && mangler->prog->nodes[clause].kind == JN_FUNC) {
        walk_block(mangler, clause, scope);
    } else {
        walk(mangler, clause, scope, 0);
    }
}

/* Open a function/arrow scope: declare params + hoisted bindings, then walk the body. */
/* Whether a reference to sym may run before its let, const or class declaration initializes it: one textually
   before the declaration, or one in another function, which a hoisted declaration can call first. */
static int reached_early(const M *mangler, int32_t sym) {
    const jm_sym *binding = &mangler->prog->syms[sym];
    if (binding->decl != 1 && binding->decl != 2 && binding->decl != 6) {
        return 0;
    }
    if (binding->decl_node < 0) {
        return 1;
    }
    int32_t home = binding->scope;
    while (mangler->prog->scopes[home].kind != 1) {
        home = mangler->prog->scopes[home].parent;
    }
    return home != mangler->fn_now;
}

static void walk_function(M *mangler, int32_t idx, int32_t parent) {
    jm_node *node = &mangler->prog->nodes[idx];
    int32_t scope = jm_scope_new(mangler->prog, parent, 1);
    if (scope < 0) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1; /* GCOVR_EXCL_LINE */
        return;              /* GCOVR_EXCL_LINE */
    }
    mangler->prog->scopes[scope].node = idx;
    int32_t mark = mangler->undo_count;
    int32_t outer_args = mangler->args_scope;
    int32_t outer_fn = mangler->fn_now;
    mangler->fn_now = scope;
    int32_t own_scope = scope;
    if (node->kind == JN_FUNC) { /* an arrow has no arguments object of its own (§10.2.11) */
        mangler->args_scope = scope;
    }
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
        mangler->args_scope = outer_args;
        mangler->fn_now = outer_fn;
        mangler->fn_scope = own_scope;
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
    /* one pass counts every `arguments` read after the first; an arrow's belong to the enclosing function */
    if (mangler->args_scope == scope && mangler->prog->scopes[scope].args_reads > 1) {
        alias_parameters(mangler, mangler->prog->scopes[scope].args_reads - 1);
    }
    mangler->args_scope = outer_args;
    mangler->fn_now = outer_fn;
    mangler->fn_scope = own_scope;
    undo_to(mangler, mark);
}

/* Walk node idx in scope. bind selects how an identifier is counted: 0 a read reference, 1 a
   declaration target (already declared, only resolved here), 2 an assignment/update/for-in write
   target. The read/write split lets the inline and dead-binding passes tell a value's reads from its
   reassignments. */
static void walk_node(M *mangler, int32_t idx, int32_t scope, int bind) {
    TH_WORK_STEP();
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
            jm_sym *binding = &mangler->prog->syms[sym]; /* held once: a store through it may alias mangler */
            binding->uses++;
            if (bind != 1 && reached_early(mangler, sym)) {
                binding->early = 1;
            }
            if (binding->scope != scope) {
                note_crossing(mangler, sym, scope);
            }
            if (bind == 0) { /* a read reference */
                binding->refs++;
                binding->ref_node = idx;
                if (mangler->track_stmt) {
                    binding->ref_stmt = mangler->stmt;
                }
                binding->ref_depth = mangler->depth;
                if (binding->refs == 1) {
                    binding->ref_scope = scope;
                } else if (binding->ref_scope != scope) {
                    binding->ref_scope = -1;
                }
                if (idx < binding->min_ref) {
                    binding->min_ref = idx;
                }
            } else if (bind == 2) { /* an assignment / update / for-in target: a write */
                binding->writes++;
            } /* bind == 1: a declaration target, neither read nor write */
        } else {
            if (mangler->args_scope >= 0 && word_is(node->str, node->str_len, "arguments")) {
                /* count the first read at once, so that what follows sees the parameters as read, and leave
                   the others for walk_function to count in one pass */
                if (mangler->prog->scopes[mangler->args_scope].args_reads++ == 0) {
                    alias_parameters(mangler, 1);
                }
            }
            hslot *slot = htab_slot(&mangler->frees, node->str, node->str_len, 1); /* a global / free name */
            if (slot != NULL) { /* GCOVR_EXCL_BR_LINE: the false branch is an allocation failure */
                slot->val = 1;
            }
        }
        return;
    }
    case JN_FUNC:
    case JN_ARROW: {
        int32_t outer = mangler->deepest;
        mangler->deepest = mangler->depth;
        walk_function(mangler, idx, scope);
        node = &mangler->prog->nodes[idx];
        if (node->kind == JN_FUNC && !(node->flags & JN_F_EXPR) && node->sym >= 0 &&
            mangler->prog->syms[node->sym].decl_node == idx) {
            jm_sym *sym = &mangler->prog->syms[node->sym];
            sym->fn_depth = mangler->depth;
            sym->fn_height = mangler->deepest - mangler->depth;
            sym->fn_scope = mangler->fn_scope;
        }
        if (outer > mangler->deepest) {
            mangler->deepest = outer;
        }
        return;
    }
    case JN_EXPR_STMT:
    case JN_RETURN:
    case JN_THROW:
        if (mangler->track_stmt) { /* the kinds single_shot_stmt accepts, recorded as the ref_stmt of their reads */
            walk_stmt(mangler, idx, scope);
            return;
        }
        break;
    case JN_BLOCK:
        if (jm_block_single_stmt(mangler->prog, idx) == -1) {
            walk_block(mangler, node->a, scope);
        } else {
            /* the printer drops the braces around one statement that declares nothing in them, so the statement
               resolves in the enclosing scope, the way the printed output parses */
            walk_chain(mangler, node->a, scope, 0);
        }
        return;
    case JN_WHILE:
    case JN_DOWHILE: {
        /* a body runs once per iteration, braced or not: its own scope keeps a closure or a value the analysis
           would move into it from being built or evaluated once per iteration */
        int32_t inner = jm_scope_new(mangler->prog, scope, 0);
        if (inner < 0) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            mangler->failed = 1; /* GCOVR_EXCL_LINE */
            return;              /* GCOVR_EXCL_LINE */
        }
        walk(mangler, node->a, inner, 0);
        walk(mangler, node->b, inner, 0);
        return;
    }
    case JN_IF:
        walk(mangler, node->a, scope, 0);
        walk_if_clause(mangler, node->b, scope);
        walk_if_clause(mangler, node->c, scope);
        return;
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
            if (node->a >= 0 && mangler->prog->nodes[node->a].kind == JN_VAR) {
                mangler->prog->nodes[node->a].flags |= JN_F_LOOP_HEAD;
                if (mangler->prog->nodes[node->a].decl != 0) {
                    hoist_block(mangler, node->a, inner);
                }
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
            mangler->catch_depth++;
            if (node->b >= 0) {
                declare_pattern(mangler, node->b, cat, 5);
                walk(mangler, node->b, cat, 1);
            }
            /* the body shares the parameter's scope, so its block-level declarations land there too */
            hoist_block(mangler, mangler->prog->nodes[node->c].a, cat);
            walk_chain(mangler, mangler->prog->nodes[node->c].a, cat, 0);
            mangler->catch_depth--;
            undo_to(mangler, mark);
        }
        walk(mangler, node->d, scope, 0);
        return;
    case JN_VAR:
        for (int32_t declarator = node->a, previous = -1; declarator >= 0;
             previous = declarator, declarator = mangler->prog->nodes[declarator].next) {
            /* a destructuring declaration assigns each name it binds, as an assignment target does; without the write
               a propagated value would rewrite the pattern's `{ x }` key */
            int32_t bound = mangler->prog->nodes[declarator].a;
            walk(mangler, bound, scope, mangler->prog->nodes[bound].kind == JN_IDENT ? 1 : 2);
            walk(mangler, mangler->prog->nodes[declarator].b, scope, 0);
            /* Single-declaration transforms cannot account for another declaration target. */
            if (mangler->prog->nodes[mangler->prog->nodes[declarator].a].kind == JN_IDENT) {
                int32_t target = mangler->prog->nodes[mangler->prog->nodes[declarator].a].sym;
                if (target >= 0) { /* GCOVR_EXCL_BR_LINE: unresolved only on a hoist allocation failure */
                    if (mangler->prog->syms[target].decl_node >= 0) {
                        mangler->prog->syms[target].writes++;
                    }
                    mangler->prog->syms[target].decl_node = idx;
                    mangler->prog->syms[target].declr_node = declarator;
                    mangler->prog->syms[target].declr_prev = previous;
                    /* Preserve traversal order when compression moves expression nodes. */
                    mangler->prog->syms[target].read_before_init = mangler->prog->syms[target].refs != 0;
                }
            }
        }
        if (mangler->catch_depth > 0 && node->decl == 0) {
            for (int32_t declarator = node->a; declarator >= 0; declarator = mangler->prog->nodes[declarator].next) {
                pin_catch_redeclarations(mangler->prog, mangler->prog->nodes[declarator].a);
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
            /* only a read: expanding a `{ x }` that binds or assigns x would link the inlined value under it too */
            if (self >= 0 && bind == 0) {
                mangler->prog->syms[self].ref_prop = idx;
            }
            if (self >= 0 && node->b >= 0) { /* `{ x = 1 }` walked x under its default too, but names it once */
                mangler->prog->syms[self].uses--;
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
        /* a direct call to `eval` resolves names dynamically: poison renaming. The parser marks every
           other name callee JN_F_VALUE, and so a folded `(1&&eval)(s)`, an indirect eval that sees only
           the global scope. */
        const jm_node *callee = &mangler->prog->nodes[node->a];
        if (callee->kind == JN_IDENT && !(callee->flags & JN_F_VALUE)) {
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
        /* A case jump can skip an initializer in the shared switch scope. */
        for (int32_t sym = mangler->prog->scopes[inner].first_sym; sym >= 0;
             sym = mangler->prog->syms[sym].scope_next) {
            mangler->prog->syms[sym].decl_node = -1;
            mangler->prog->syms[sym].early = 1;
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

/* A read below records this statement as its ref_stmt, so whether a binding's one read sits in the statement after
   its declaration is a comparison rather than a search of that statement per binding. */
static void walk_stmt(M *mangler, int32_t idx, int32_t scope) {
    int32_t outer = mangler->stmt;
    mangler->stmt = idx;
    walk(mangler, mangler->prog->nodes[idx].a, scope, 0);
    mangler->stmt = outer;
}

/* Whether name is a reserved word or a name that must not be reused: a free name, or
   a kept binding's name (reserved globally so a mangled binding never shadows one).
   The free table holds both, so this is O(1). */
static int is_forbidden(M *mangler, const Py_UCS4 *name, Py_ssize_t len) {
    return is_reserved(name, len) || htab_get(&mangler->frees, name, len) > 0;
}

static int renamable(const jm_sym *binding) {
    return !binding->pinned && binding->decl_node != -2;
}

/* Order a scope's bindings most-used first, then by index, so its most-used names gather in the low slots. members
   holds them in index order and is rewritten in place, unless already in order; counts (sym_count + 1 entries) and
   sorted (count entries) are scratch. A short list takes an insertion sort; a long one a counting sort on its use
   counts, capped at sym_count so the counts fit, which only leaves bindings used more often than there are bindings in
   index order. */
static void order_by_uses(const jm_program *prog, int32_t *members, int32_t count, int32_t *counts, int32_t *sorted) {
    const jm_sym *syms = prog->syms;
    int32_t ordered = 1;
    while (ordered < count && syms[members[ordered - 1]].uses >= syms[members[ordered]].uses) {
        ordered++;
    }
    if (ordered >= count) {
        return;
    }
    if (count <= 16) {
        for (int32_t index = 1; index < count; index++) {
            int32_t sym = members[index];
            int32_t at = index;
            for (; at > 0 && syms[members[at - 1]].uses < syms[sym].uses; at--) {
                members[at] = members[at - 1];
            }
            members[at] = sym;
        }
        return;
    }
    uint32_t cap = (uint32_t)prog->sym_count;
    uint32_t top = 0;
    for (int32_t index = 0; index < count; index++) {
        uint32_t uses = syms[members[index]].uses < cap ? syms[members[index]].uses : cap;
        top = uses > top ? uses : top;
    }
    memset(counts, 0, ((size_t)top + 1) * sizeof(int32_t));
    for (int32_t index = 0; index < count; index++) {
        counts[top - (syms[members[index]].uses < cap ? syms[members[index]].uses : cap)]++;
    }
    for (uint32_t key = 0, at = 0; key <= top; key++) {
        int32_t run = counts[key];
        counts[key] = (int32_t)at;
        at += (uint32_t)run;
    }
    for (int32_t index = 0; index < count; index++) {
        sorted[counts[top - (syms[members[index]].uses < cap ? syms[members[index]].uses : cap)]++] = members[index];
    }
    memcpy(members, sorted, (size_t)count * sizeof(int32_t));
}

/* Give every renamable binding below the global scope a slot; bindings sharing a slot take the same short name.

   A binding takes the lowest slot no other binding of its scope holds and no binding read or written inside its scope
   but declared further out holds: that outer binding's name would be captured there. Any other outer binding is
   shadowed harmlessly, so a nested function reuses the names of the outer bindings it never names, as terser does,
   and a scope's most-used bindings come first so they gather in the low slots. The scopes between a reference and its
   binding are collected per binding, a scope once each, by walking up from every crossing reference until a scope
   already marked for it. A parent scope is created before its children, so index order visits it first. */
static void assign_slots(M *mangler, int32_t global) {
    jm_program *prog = mangler->prog;
    if (prog->scope_count < 2) {
        return; /* no scope below the global one, whose bindings keep their names */
    }
    int32_t scopes = prog->scope_count;
    int32_t pairs = mangler->crossing_count / 2;
    size_t syms = (size_t)prog->sym_count + 1;
    /* one zeroed block holds starts, taken, members and the sort's scratch, order, marked and heads */
    int32_t *block = jm_calloc(syms * 4 + (size_t)pairs + 1 + (size_t)scopes * 2, sizeof(int32_t));
    int32_t links_cap = pairs; /* every crossing marks one scope at least, and a deep one more */
    int32_t *links = pairs > 0 ? jm_malloc((size_t)links_cap * 2 * sizeof(int32_t)) : NULL; /* (binding, next) */
    if (block == NULL || (pairs > 0 && links == NULL)) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        mangler->failed = 1;                             /* GCOVR_EXCL_LINE */
        goto done;                                       /* GCOVR_EXCL_LINE */
    }
    int32_t *starts = block;
    int32_t *taken = starts + syms;
    int32_t *members = taken + syms; /* then the sort's scratch */
    int32_t *order = members + syms * 2;
    int32_t *marked = order + pairs + 1;
    int32_t *heads = marked + scopes;
    for (int32_t scope = 0; scope < scopes; scope++) {
        marked[scope] = -1;
        heads[scope] = -1;
    }
    int32_t entries = 0;
    for (int32_t at = 0; at < pairs * 2; at += 2) {
        starts[mangler->crossings[at] + 1]++;
    }
    for (int32_t sym = 0; pairs > 0 && sym < prog->sym_count; sym++) {
        starts[sym + 1] += starts[sym];
    }
    for (int32_t at = 0; at < pairs * 2; at += 2) {
        order[starts[mangler->crossings[at]]++] = mangler->crossings[at + 1];
    }
    for (int32_t sym = 0, first = 0; pairs > 0 && sym < prog->sym_count; sym++) {
        int32_t last = starts[sym]; /* the fill above advanced each start to the next binding's */
        int32_t home = prog->syms[sym].scope;
        for (int32_t index = first; index < last && renamable(&prog->syms[sym]) && home != global; index++) {
            for (int32_t scope = order[index]; scope != home && marked[scope] != sym;
                 scope = prog->scopes[scope].parent) {
                if (entries == links_cap) {
                    size_t cap;
                    size_t bytes;
                    int grew =
                        th_grow_cap((size_t)entries + 1, (size_t)links_cap, 256, 2 * sizeof(int32_t), &cap, &bytes);
                    int32_t *grown =
                        grew && cap <= INT32_MAX ? jm_realloc(links, bytes) : NULL; /* GCOVR_EXCL_BR_LINE */
                    if (grown == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation-failure path or a billion entries */
                        mangler->failed = 1; /* GCOVR_EXCL_LINE */
                        goto done;           /* GCOVR_EXCL_LINE */
                    }
                    links = grown;
                    links_cap = (int32_t)cap;
                }
                marked[scope] = sym;
                links[(size_t)entries * 2] = sym;
                links[(size_t)entries * 2 + 1] = heads[scope];
                heads[scope] = entries++;
            }
        }
        first = last;
    }
    for (int32_t scope = 0; scope < scopes; scope++) {
        int32_t stamp = scope + 1;
        int32_t count = 0;
        int32_t *listed = members + syms; /* the chain runs latest first, so the list fills down to index order */
        for (int32_t sym = prog->scopes[scope].first_sym; scope != global && sym >= 0;
             sym = prog->syms[sym].scope_next) {
            if (renamable(&prog->syms[sym])) {
                *--listed = sym;
                count++;
            }
        }
        if (count == 0) {
            continue;
        }
        for (int32_t entry = heads[scope]; entry >= 0; entry = links[(size_t)entry * 2 + 1]) {
            taken[prog->syms[links[(size_t)entry * 2]].slot] = stamp;
        }
        order_by_uses(prog, listed, count, starts, members + syms);
        int32_t slot = 0;
        for (int32_t index = 0; index < count; index++) {
            while (taken[slot] == stamp) {
                slot++;
            }
            prog->syms[listed[index]].slot = slot;
            taken[slot] = stamp;
        }
        if (slot + 1 > mangler->slot_count) {
            mangler->slot_count = slot + 1;
        }
    }
done:
    jm_free(block);
    jm_free(links);
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
   boolean/undefined. A regex is excluded: each evaluation creates a fresh object (13.2.7.3 in the
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
   folded boolean/undefined forms, a number's printed spelling (3600000 prints as 36e5, so a second
   minification would weigh the shorter form), and the lexeme otherwise. */
static Py_ssize_t literal_print_len(jm_program *prog, int32_t idx) {
    const jm_node *node = &prog->nodes[idx];
    if (node->kind == JN_UNARY) {
        Py_ssize_t operand = prog->nodes[node->a].str_len;
        return node->op == JT_NOT ? 1 + operand : 5 + operand;
    }
    if (node->kind == JN_NUM) {
        Py_UCS4 buf[JM_NUMBER_SPELL_MAX];
        Py_ssize_t len = jm_number_spell(node, buf);
        return len < 0 ? node->str_len : len;
    }
    return node->str_len;
}

static void collapse_walk(jm_program *prog, int32_t idx, int *changed);
static void collapse_stmt(jm_program *prog, int32_t node, int *changed);
static int is_droppable_init(jm_program *prog, int32_t init);

/* A value whose evaluation cannot be observed when its result is discarded: a side-effect-free
   initializer, or a resolved local read (reading a declared binding never throws). A member access,
   call or free-name read may throw or run a getter, so it is not pure. */
static int is_pure_value(jm_program *prog, int32_t idx) {
    if (prog->nodes[idx].kind == JN_IDENT && prog->nodes[idx].sym >= 0) {
        return !prog->syms[prog->nodes[idx].sym].early;
    }
    return is_droppable_init(prog, idx);
}

/* Whether the binding is immutable: a const, or the self-name of a function or class expression (15.2.5,
   15.7.14). Assigning one never stores and throws for a const or in strict code (9.1.1.1.5), so no pass
   may drop the assignment, and its value is the assigned one while the binding keeps its own. */
static int is_immutable(const jm_sym *sym) {
    return sym->decl == 2 || sym->decl == 7;
}

/* If node assigns `x = EXPR` to a mutable local that is never read, the store is dead -- ECMA-262 makes
   only EXPR's evaluation observable. A top-level binding is a global another script can read, so its stores stay.
   Decrement x's write count (a later pass drops the now-unwritten
   binding) and return EXPR, else -1. */
static int32_t dead_store_value(jm_program *prog, int32_t node) {
    if (prog->nodes[node].kind != JN_ASSIGN || prog->nodes[node].op != JT_ASSIGN ||
        prog->nodes[prog->nodes[node].a].kind != JN_IDENT) {
        return -1;
    }
    int32_t target = prog->nodes[prog->nodes[node].a].sym;
    if (target < 0 || prog->syms[target].refs != 0 || is_immutable(&prog->syms[target]) || prog->syms[target].early ||
        prog->scopes[prog->syms[target].scope].parent < 0) {
        return -1;
    }
    prog->syms[target].writes--;
    return prog->nodes[node].b;
}

/* The unlinked original drops its binding, so a binding whose cached read still points there reads
   as stale: inline_single_use would otherwise rewrite the original and drop the declaration the
   copy still reads (#1035). */
static void move_node(jm_program *prog, int32_t dst, int32_t src) {
    jm_node_replace(prog, dst, src);
    prog->nodes[src].sym = -1;
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
            move_node(prog, elem, init);
            *changed = 1;
            continue;
        }
        int32_t use = prog->nodes[elem].next;
        if (use < 0 || prog->nodes[elem].kind != JN_ASSIGN || prog->nodes[elem].op != JT_ASSIGN ||
            prog->nodes[prog->nodes[elem].a].kind != JN_IDENT) {
            continue;
        }
        int32_t target = prog->nodes[prog->nodes[elem].a].sym;
        if (target < 0 || prog->nodes[use].kind != JN_IDENT || prog->nodes[use].sym != target ||
            is_immutable(&prog->syms[target])) {
            continue; /* not `t = EXPR` immediately followed by a read of the same mutable local t */
        }
        int32_t after = prog->nodes[use].next;
        if (prog->syms[target].refs == 1 && prog->syms[target].writes == 1) {
            move_node(prog, elem, prog->nodes[elem].b); /* `(t=EXPR, t)` with t used once -> EXPR */
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
        move_node(prog, seq, only);
        *changed = 1;
    }
}

/* A declarator binding a function-local name that nothing reads or writes (another declaration of the name counts as a
   write): the declaration then only evaluates the initializer. */
static int unread_local(jm_program *prog, int32_t declr) {
    int32_t target = prog->nodes[declr].a;
    if (prog->nodes[target].kind != JN_IDENT) {
        return 0; /* a destructuring pattern runs the iterator or getters it names */
    }
    const jm_sym *sym = &prog->syms[prog->nodes[target].sym];
    return sym->refs == 0 && sym->writes == 0 && !sym->pinned && prog->scopes[sym->scope].parent >= 0;
}

static int reads_movable(jm_program *prog, int32_t idx);

/* Turn the trailing run of unread declarators of a declaration statement into an expression statement of their
   initializers, in order: `const a=g(0),b=g(1)` with neither read is `g(0),g(1)`. The run sits after every kept
   declarator, so the initializers still run after theirs, and a declaration whose every declarator goes becomes the
   expression statement itself. A pure initializer ends the run like a read declarator: drop_unused takes a literal or
   a function, and dropping a read of a binding would leave that binding fewer reads than the analysis counted. So
   does a sequence, whose elements the printer would splice into the run's, where collapse_sequence would drop them.
   Sets open when the rewrite may leave a pass more to do than the analysis shows. */
static int fuse_unread_tail(jm_program *prog, int32_t stmt, int *open) {
    int32_t kept = -1; /* the last declarator that stays */
    int32_t effects = 0;
    for (int32_t declr = prog->nodes[stmt].a; declr >= 0; declr = prog->nodes[declr].next) {
        int32_t init = prog->nodes[declr].b;
        if (!unread_local(prog, declr) || init < 0 || is_pure_value(prog, init) || prog->nodes[init].kind == JN_SEQ) {
            kept = declr;
            effects = 0;
        } else {
            effects++;
        }
    }
    if (effects == 0) {
        return 0;
    }
    int32_t expr = -1;
    if (effects > 1) {
        expr = jm_node_new(prog, JN_SEQ);
        if (expr < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the declarators */
            return 0;   /* GCOVR_EXCL_LINE */
        }
    }
    int32_t into = stmt;
    if (kept >= 0) {
        into = jm_node_new(prog, JN_EXPR_STMT);
        if (into < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the declarators */
            return 0;   /* GCOVR_EXCL_LINE */
        }
    }
    int32_t tail = -1;
    for (int32_t declr = kept >= 0 ? prog->nodes[kept].next : prog->nodes[stmt].a; declr >= 0;
         declr = prog->nodes[declr].next) {
        jm_sym *gone = &prog->syms[prog->nodes[prog->nodes[declr].a].sym];
        if (gone->decl == 3) {
            /* a var redeclaring a parameter leaves the parameter, to be named from what the tree now holds, which only
               a fresh analysis counts */
            *open = 1;
        } else {
            gone->decl_node = -2;
        }
        int32_t init = prog->nodes[declr].b;
        prog->nodes[init].next = -1;
        *open |= reads_movable(prog, init);
        if (expr < 0) {
            expr = init;
        } else if (tail < 0) {
            prog->nodes[expr].a = init;
        } else {
            prog->nodes[tail].next = init;
        }
        tail = init;
    }
    if (kept >= 0) {
        prog->nodes[kept].next = -1;
        prog->nodes[into].next = prog->nodes[stmt].next;
        prog->nodes[stmt].next = into;
    } else {
        /* a let or const leaving a block may leave it one statement the printer drops the braces of, and the
           analysis then resolves what that block reads in the scope around it */
        int32_t binding = prog->nodes[prog->nodes[prog->nodes[stmt].a].a].sym;
        *open |= prog->nodes[stmt].decl != 0 && prog->scopes[prog->syms[binding].scope].kind == 0;
        prog->nodes[stmt].decl = 0;
    }
    prog->nodes[into].kind = JN_EXPR_STMT;
    prog->nodes[into].a = expr;
    collapse_stmt(prog, into, open);
    return 1;
}

/* How deep the inlining scan reads into the statement after a declaration, and how tall an initializer it moves there:
   together they bound how much taller one move makes the tree, and a moved initializer sits in a return, throw or
   expression statement, never in a declaration a later move could carry it out of. */
#define INLINE_SCAN_DEPTH 4
#define INLINE_INIT_HEIGHT 16

/* Whether no path below idx runs deeper than budget levels. */
static int height_within(jm_program *prog, int32_t idx, int32_t budget) {
    if (budget == 0) {
        return 0;
    }
    const jm_node *node = &prog->nodes[idx];
    int32_t children[4] = {node->a, node->b, node->c, node->d};
    for (int child = 0; child < 4; child++) {
        for (int32_t item = children[child]; item >= 0; item = prog->nodes[item].next) {
            TH_WORK_STEP();
            if (!height_within(prog, item, budget - 1)) {
                return 0;
            }
        }
    }
    return 1;
}

/* A declarator whose one read can take its initializer: a function-local name read once and never written, with a
   value to move that is short enough and no sequence, whose elements would join a sequence it lands in. */
static int inline_candidate(jm_program *prog, int32_t declr) {
    if (prog->nodes[declr].b < 0) {
        return 0;
    }
    const jm_sym *sym = &prog->syms[prog->nodes[prog->nodes[declr].a].sym];
    return sym->refs == 1 && !sym->pinned && sym->writes == 0 && prog->scopes[sym->scope].parent >= 0 &&
           prog->nodes[prog->nodes[declr].b].kind != JN_SEQ &&
           height_within(prog, prog->nodes[declr].b, INLINE_INIT_HEIGHT);
}

/* Whether inline_single_use, given a fresh analysis, could move the binding of a read the declarator rewrites moved:
   one read, never written, and a value whose move that pass decides from where the read sits -- a function
   declaration by how deep, a regex by which statement holds the read, and a literal, a sequence or a value too tall
   for inline_into_next by whether the statement after the declaration jumps with it. Any other value moves only
   where inline_into_next would move it first. */
static int movable_binding(jm_program *prog, int32_t index) {
    const jm_sym *sym = &prog->syms[index];
    if (sym->refs != 1 || sym->writes != 0 || sym->decl_node < 0 || sym->pinned ||
        prog->scopes[sym->scope].parent < 0) {
        return 0;
    }
    if (sym->decl == 4) {
        return 1;
    }
    int32_t init = prog->nodes[sym->declr_node].b;
    return init >= 0 && (prog->nodes[init].kind == JN_REGEX || prog->nodes[init].kind == JN_SEQ ||
                         is_const_literal(prog, init) || !height_within(prog, init, INLINE_INIT_HEIGHT));
}

/* Whether the subtree at idx reads a binding movable_binding accepts. */
static int reads_movable(jm_program *prog, int32_t idx) {
    TH_WORK_STEP();
    const jm_node *node = &prog->nodes[idx];
    if (node->kind == JN_IDENT) {
        return node->sym >= 0 && movable_binding(prog, node->sym);
    }
    int32_t children[4] = {node->a, node->b, node->c, node->d};
    for (int child = 0; child < 4; child++) {
        for (int32_t item = children[child]; item >= 0; item = prog->nodes[item].next) {
            if (reads_movable(prog, item)) {
                return 1;
            }
        }
    }
    return 0;
}

typedef struct {
    int32_t stmt;
    int32_t first;    /* the first declarator to inline, once the scan met its read, else -1 */
    int32_t expected; /* the declarator whose read must come next, -1 once every one from first on was met */
} jm_inline_scan;

/* Walk an expression in evaluation order up to its first evaluation with an observable effect, matching the reads of
   the declaration's trailing declarators. Returns 1 when the walk may continue past idx. Only side-effect-free
   evaluations may precede a matched read: literals, closures and reads of never-written parameters. Everything else
   ends the walk, before it for a member access, a free name or a read of another binding, after its operands for a
   call or an operator, which may run user code (a getter, a valueOf) once they are evaluated. */
static int scan_inline_reads(jm_program *prog, int32_t idx, jm_inline_scan *scan, int depth) {
    TH_WORK_STEP();
    if (scan->first >= 0 && scan->expected < 0) {
        return 0; /* every initializer found its read: what follows runs after all of them either way */
    }
    if (depth > INLINE_SCAN_DEPTH) {
        return 0;
    }
    const jm_node *node = &prog->nodes[idx];
    switch (node->kind) {
    case JN_NUM:
    case JN_STRING:
    case JN_BIGINT:
    case JN_HOLE:
    case JN_FUNC:
    case JN_ARROW:
        return 1;
    case JN_IDENT: {
        if (node->sym < 0) {
            return 0;
        }
        const jm_sym *sym = &prog->syms[node->sym];
        if (sym->decl == 3 && sym->writes == 0) {
            return 1; /* a parameter is bound before the body runs */
        }
        if (sym->decl_node != scan->stmt || sym->ref_node != idx) {
            return 0;
        }
        if (scan->first < 0) {
            scan->first = sym->declr_node;
            scan->expected = sym->declr_node;
        }
        if (sym->declr_node != scan->expected) {
            scan->first = -1;
            return 0; /* read out of declaration order */
        }
        scan->expected = prog->nodes[scan->expected].next;
        return 1;
    }
    case JN_ARRAY:
    case JN_SEQ:
        for (int32_t item = node->a; item >= 0; item = prog->nodes[item].next) {
            if (prog->nodes[item].kind == JN_SPREAD || !scan_inline_reads(prog, item, scan, depth + 1)) {
                return 0; /* a spread runs the iterator protocol */
            }
        }
        return 1;
    case JN_MEMBER_EXPR:
        /* the object evaluates first, then the key; the access itself may run a getter */
        if (scan_inline_reads(prog, node->a, scan, depth + 1) && (node->flags & JN_F_COMPUTED)) {
            scan_inline_reads(prog, node->b, scan, depth + 1);
        }
        return 0;
    case JN_CALL:
    case JN_NEW: {
        int32_t callee = node->a;
        if (node->kind == JN_CALL && prog->nodes[callee].kind == JN_MEMBER_EXPR) {
            scan_inline_reads(prog, callee, scan, depth + 1); /* a method call reads its object first */
            return 0;
        }
        if (prog->nodes[callee].kind != JN_IDENT || prog->nodes[callee].sym < 0 ||
            prog->syms[prog->nodes[callee].sym].decl != 3 || prog->syms[prog->nodes[callee].sym].writes != 0 ||
            (node->flags & JN_F_OPTIONAL)) {
            return 0; /* only a parameter callee reads nothing observable; `?.` may skip the arguments */
        }
        for (int32_t arg = node->b; arg >= 0; arg = prog->nodes[arg].next) {
            if (prog->nodes[arg].kind == JN_SPREAD || !scan_inline_reads(prog, arg, scan, depth + 1)) {
                return 0;
            }
        }
        return 0;
    }
    case JN_BINARY:
        if (scan_inline_reads(prog, node->a, scan, depth + 1)) {
            scan_inline_reads(prog, node->b, scan, depth + 1);
        }
        return 0;
    default:
        return 0;
    }
}

/* Move the initializers of a declaration's trailing declarators into their one reads in the statement right after it,
   when those reads are that statement's first evaluations with an effect, in declaration order: `const a=g(0),
   b=g(1);return[a,b]` is `return[g(0),g(1)]`. Each initializer still runs in the same order, after the declarators
   that stay, and before anything observable in the statement, so only the names go. */
static int inline_into_next(jm_program *prog, int32_t stmt, int *open) {
    int32_t next = prog->nodes[stmt].next;
    if (next < 0 || prog->nodes[next].a < 0 ||
        (prog->nodes[next].kind != JN_RETURN && prog->nodes[next].kind != JN_THROW &&
         prog->nodes[next].kind != JN_EXPR_STMT)) {
        return 0;
    }
    jm_inline_scan scan = {stmt, -1, -1};
    scan_inline_reads(prog, prog->nodes[next].a, &scan, 0);
    if (scan.first < 0 || scan.expected >= 0) {
        return 0;
    }
    for (int32_t declr = scan.first; declr >= 0; declr = prog->nodes[declr].next) {
        if (!inline_candidate(prog, declr)) {
            return 0; /* a declarator after this one stays and would then run before it */
        }
    }
    int32_t list_scope = prog->syms[prog->nodes[prog->nodes[scan.first].a].sym].ref_scope;
    for (int32_t declr = scan.first; declr >= 0; declr = prog->nodes[declr].next) {
        jm_sym *sym = &prog->syms[prog->nodes[prog->nodes[declr].a].sym];
        int32_t init = prog->nodes[declr].b;
        int32_t moved = prog->nodes[init].kind == JN_IDENT ? prog->nodes[init].sym : -1;
        move_node(prog, sym->ref_node, init);
        if (moved >= 0 && prog->syms[moved].ref_node == init) {
            prog->syms[moved].ref_node = sym->ref_node; /* the read moved with it, where the next look must find it */
        }
        sym->decl_node = -2;
        sym->refs = 0;
        *open |= reads_movable(prog, sym->ref_node);
    }
    /* walked rather than read from declr_prev, which a declarator the propagation pass unlinked this round can leave
       pointing at itself */
    int32_t previous = -1;
    for (int32_t declr = prog->nodes[stmt].a; declr != scan.first; declr = prog->nodes[declr].next) {
        previous = declr;
    }
    if (previous < 0) {
        /* the statement takes the next one's place, leaving no empty behind; the reads it holds now sit in another
           statement */
        *open |= reads_movable(prog, next) || prog->scopes[list_scope].kind == 0; /* a block may lose its braces */
        prog->nodes[stmt] = prog->nodes[next];
    } else {
        prog->nodes[previous].next = -1;
    }
    collapse_stmt(prog, previous < 0 ? stmt : next, open);
    return 1;
}

/* Run fuse_unread_tail and inline_into_next over every declaration statement. Each declaration is found through the
   binding of its last declarator, and a rewrite that leaves a new last declarator goes around again, so the rewrites
   reach their own fixpoint here. They only move reads and drop the bindings they empty, so the analysis stays exact
   for the other passes unless a rewrite sets open; every function a rewrite touched is noted for jm_fold_touched. The
   analysis may be stale where the other passes changed the tree this round, but only toward more reads and writes
   than the tree holds, which make every check here more cautious. */
static int settle_declarations(jm_program *prog, int *open) {
    int any = 0;
    int changed = 1;
    while (changed) {
        changed = 0;
        for (int32_t index = 0; index < prog->sym_count; index++) {
            const jm_sym *sym = &prog->syms[index];
            int32_t stmt = sym->decl_node;
            if (stmt < 0 || prog->nodes[stmt].kind != JN_VAR || (prog->nodes[stmt].flags & JN_F_LOOP_HEAD) ||
                prog->nodes[sym->declr_node].next >= 0) {
                continue; /* a for head is no statement, and only the last declarator stands for its declaration */
            }
            int32_t scope = sym->scope;
            int rewrote = fuse_unread_tail(prog, stmt, open);
            if (prog->nodes[stmt].kind == JN_VAR) {
                rewrote |= inline_into_next(prog, stmt, open);
            }
            if (rewrote) {
                jm_fold_touch(prog, scope);
                changed = 1;
            }
        }
        any |= changed;
    }
    return any;
}

/* Collapse one statement or expression, dropping a statement-level dead store, collapsing sequences, and
   recursing. `x=EXPR;` where x is never read keeps only EXPR -- discarded when EXPR is pure, else left as an
   expression statement for its side effect. */
static void collapse_stmt(jm_program *prog, int32_t node, int *changed) {
    if (prog->nodes[node].kind == JN_EXPR_STMT) {
        int32_t init = dead_store_value(prog, prog->nodes[node].a);
        if (init >= 0) {
            if (is_pure_value(prog, init)) {
                jm_node_empty(prog, node);
            } else {
                prog->nodes[node].a = init;
            }
            *changed = 1;
        }
    }
    collapse_walk(prog, node, changed);
}

/* Collapse every statement or expression of the chain at first. */
static void collapse_chain(jm_program *prog, int32_t first, int *changed) {
    for (int32_t node = first; node >= 0; node = prog->nodes[node].next) {
        collapse_stmt(prog, node, changed);
    }
}

/* Descend into every nested statement list (block, function/arrow body, switch case) and every
   expression that may hold one, mirroring the fold pass's traversal. */
/* A function/class expression's self-name binds only inside its own body (13.2.4 / 15.7.4); with
   zero reads and writes it names nothing, so the expression prints anonymous and the name's slot is
   freed. A write keeps it: without the binding the assignment would land on an outer name. */
static void drop_unread_expr_name(jm_program *prog, jm_node *node, int *changed) {
    /* a named expression always resolved its self-name, so str != NULL implies a live sym */
    if ((node->flags & JN_F_EXPR) && node->str != NULL && prog->syms[node->sym].refs == 0 &&
        prog->syms[node->sym].writes == 0) {
        node->str = NULL;
        node->str_len = 0;
        node->sym = -1;
        *changed = 1;
    }
}

/* The bits collapse_walk adds to its change flag for a hoist_call rewrite, which leaves the analysis exact: HOISTED
   for one anywhere below, which every function around it notes for the fold, and HOISTED_HERE for one below and in
   the same function. A call in a test negates at another price than a conditional, so a hoist in a test leaves fold
   more to do. */
enum { HOISTED = 2, HOISTED_HERE = 4 };

/* Whether reading the binding sym can neither throw nor run code: a local var, parameter, function or catch binding,
   which no temporal dead zone guards. */
static int reads_quietly(const jm_program *prog, int32_t sym) {
    const jm_sym *binding = &prog->syms[sym];
    return prog->scopes[binding->scope].parent >= 0 &&
           ((1U << binding->decl) & (1U << 0 | 1U << 3 | 1U << 4 | 1U << 5)) != 0;
}

/* Whether two nodes are the same name, resolving to the same binding, or the same number or string literal. */
static int same_leaf(const jm_program *prog, int32_t left, int32_t right) {
    const jm_node *first = &prog->nodes[left];
    const jm_node *second = &prog->nodes[right];
    if (first->kind != second->kind || first->str_len != second->str_len || first->sym != second->sym ||
        (first->kind != JN_IDENT && first->kind != JN_NUM && first->kind != JN_STRING)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < first->str_len; index++) {
        if (first->str[index] != second->str[index]) {
            return 0;
        }
    }
    return 1;
}

/* Whether evaluating the expression at idx can neither throw nor run code: number and string literals and reads of a
   local var, parameter, function or catch binding, which no temporal dead zone guards, joined by `!`, strict
   (in)equality, which never converts an operand, and the logical operators. height levels bound the descent. */
static int quiet_test(const jm_program *prog, int32_t idx, int height) {
    const jm_node *node = &prog->nodes[idx];
    if (height == 0) {
        return 0;
    }
    switch (node->kind) {
    case JN_NUM:
    case JN_STRING:
        return 1;
    case JN_IDENT:
        return node->sym >= 0 && reads_quietly(prog, node->sym);
    case JN_UNARY:
        return node->op == JT_NOT && quiet_test(prog, node->a, height - 1);
    case JN_BINARY:
        if (node->op != JT_EQ_EQ_EQ && node->op != JT_NE_EQ) {
            return 0;
        }
        return quiet_test(prog, node->a, height - 1) && quiet_test(prog, node->b, height - 1);
    case JN_LOGICAL:
        return quiet_test(prog, node->a, height - 1) && quiet_test(prog, node->b, height - 1);
    default:
        return 0;
    }
}

/* Rewrite `c?g(a):g(b)` as `g(c?a:b)` when both arms call the same name with arguments that differ in exactly one
   place and are names or literals elsewhere: the call now reads the name, and the arguments before the differing
   one, ahead of the test, which a quiet test makes unobservable. Adds HOISTED to changed, and 1 too when the
   rewrite leaves another pass or fold something to do: the dropped arm read a binding, whose read count the analysis
   then overstates, or the new conditional is one fold_conditional rewrites. Returns the new conditional, else -1. */
static int32_t hoist_call(jm_program *prog, int32_t idx, int *changed) {
    int32_t cons = prog->nodes[idx].b;
    int32_t alt = prog->nodes[idx].c;
    if (prog->nodes[cons].kind != JN_CALL || prog->nodes[alt].kind != JN_CALL ||
        ((prog->nodes[cons].flags | prog->nodes[alt].flags) & JN_F_OPTIONAL) ||
        prog->nodes[prog->nodes[cons].a].kind != JN_IDENT ||
        !same_leaf(prog, prog->nodes[cons].a, prog->nodes[alt].a)) {
        return -1;
    }
    int32_t *link = &prog->nodes[cons].b;
    int32_t *differing = NULL;
    int32_t other = -1;
    int32_t left = prog->nodes[cons].b;
    int32_t right = prog->nodes[alt].b;
    for (; left >= 0 && right >= 0; left = prog->nodes[left].next, right = prog->nodes[right].next) {
        if (prog->nodes[left].kind == JN_SPREAD || prog->nodes[right].kind == JN_SPREAD) {
            return -1;
        }
        if (!same_leaf(prog, left, right)) {
            if (differing != NULL) {
                return -1;
            }
            differing = link;
            other = right;
        }
        link = &prog->nodes[left].next;
    }
    int32_t test = prog->nodes[idx].a;
    if (left >= 0 || right >= 0 || differing == NULL || !quiet_test(prog, test, 16)) {
        return -1;
    }
    int32_t arg = *differing;
    int open = prog->nodes[prog->nodes[alt].a].sym >= 0 || same_leaf(prog, test, arg) || same_leaf(prog, test, other) ||
               (prog->nodes[arg].kind == JN_ASSIGN && prog->nodes[other].kind == JN_ASSIGN);
    for (int32_t item = prog->nodes[alt].b; item >= 0; item = prog->nodes[item].next) {
        open |= item != other && prog->nodes[item].sym >= 0;
    }
    /* the conditional takes the node of the dropped arm, so the rewrite allocates nothing and the node pointers the
       walks above it hold stay valid */
    prog->nodes[alt] = prog->nodes[idx];
    prog->nodes[alt].b = arg;
    prog->nodes[alt].c = other;
    prog->nodes[alt].flags &= ~(uint32_t)JN_F_VALUE;
    prog->nodes[alt].next = prog->nodes[arg].next;
    prog->nodes[arg].next = -1;
    prog->nodes[other].next = -1;
    *differing = alt;
    jm_node_replace(prog, idx, cons);
    *changed |= HOISTED | HOISTED_HERE | open;
    return alt;
}

static void collapse_walk(jm_program *prog, int32_t idx, int *changed) {
    if (idx < 0) {
        return;
    }
    jm_node *node = &prog->nodes[idx];
    switch (node->kind) {
    case JN_IDENT:
    case JN_NUM:
    case JN_STRING:
        return; /* the commonest leaves skip the four empty child calls */
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
    case JN_COND: {
        int test = 0;
        collapse_walk(prog, node->a, &test);
        *changed |= test | (test & HOISTED_HERE ? 1 : 0);
        collapse_walk(prog, prog->nodes[idx].b, changed);
        collapse_walk(prog, prog->nodes[idx].c, changed);
        /* the new conditional's arms may call one name again: c?g(h(1)):g(h(2)) -> g(c?h(1):h(2)) -> g(h(c?1:2)) */
        for (int32_t cond = idx; cond >= 0;) {
            cond = hoist_call(prog, cond, changed);
        }
        return;
    }
    case JN_IF:
    case JN_WHILE:
    case JN_DOWHILE:
    case JN_FOR:
    case JN_LOGICAL:
    case JN_UNARY: {
        int32_t tested = node->kind == JN_DOWHILE || node->kind == JN_FOR ? node->b : node->a;
        int test = 0;
        collapse_walk(prog, tested, &test);
        *changed |= test | (test & HOISTED_HERE ? 1 : 0);
        if (node->kind == JN_LOGICAL) {
            test = 0;
            collapse_walk(prog, node->b, &test);
            *changed |= test | (test & HOISTED_HERE ? 1 : 0);
            return;
        }
        int32_t children[4] = {node->a, node->b, node->c, node->d};
        for (int child = 0; child < 4; child++) {
            if (children[child] != tested) {
                collapse_walk(prog, children[child], changed);
            }
        }
        return;
    }
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
    case JN_ARROW: {
        int inner = 0;
        if (node->kind == JN_FUNC) {
            drop_unread_expr_name(prog, node, &inner);
        }
        collapse_chain(prog, node->a, &inner); /* params (defaults may hold a function) */
        collapse_walk(prog, node->b, &inner);  /* body: a block, or an arrow expression */
        if (inner & HOISTED) {
            jm_fold_touch_function(prog, idx);
        }
        *changed |= inner & ~HOISTED_HERE;
        return;
    }
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

static void unlink_declarator(jm_program *prog, int32_t sym) {
    int32_t stmt = prog->syms[sym].decl_node;
    int32_t declr = prog->syms[sym].declr_node;
    int32_t previous = prog->syms[sym].declr_prev;
    int32_t next = prog->nodes[declr].next;
    if (previous < 0) {
        prog->nodes[stmt].a = next;
    } else {
        prog->nodes[previous].next = next;
    }
    if (next >= 0) {
        int32_t target = prog->nodes[next].a;
        if (prog->nodes[target].kind == JN_IDENT) {
            prog->syms[prog->nodes[target].sym].declr_prev = previous;
        }
    }
    if (prog->nodes[stmt].a < 0) {
        prog->nodes[stmt].kind = JN_EMPTY;
    }
    prog->syms[sym].decl_node = -2;
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
            prog->syms[sym].scope == global || prog->syms[sym].pinned) {
            continue; /* a written binding (even if never read) keeps its declaration; see dead stores */
        }
        int32_t stmt = prog->syms[sym].decl_node;
        if (prog->syms[sym].decl == 4) { /* an unused function declaration: drop the whole statement */
            jm_node_empty(prog, stmt);
            prog->syms[sym].decl_node = -2;
            changed = 1;
            continue;
        }
        if (is_droppable_init(prog, prog->nodes[prog->syms[sym].declr_node].b)) {
            unlink_declarator(prog, sym);
            changed = 1;
        }
    }
    return changed;
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
    prog->nodes[prop].flags &= ~(uint32_t)JN_F_SHORTHAND;
    return 1;
}

/* Whether evaluating the initializer at idx runs no code of the program: a literal, a function, a read of a local no
   temporal dead zone guards, `!`, `typeof` or `void` of one, a sign over a number, arithmetic on two numbers, and an
   array, an object with plain keys or a template without substitutions built of such values. height levels bound the
   descent. */
static int inert_init(const jm_program *prog, int32_t idx, int height) {
    if (idx < 0) {
        return 1;
    }
    const jm_node *node = &prog->nodes[idx];
    if (height == 0) {
        return 0;
    }
    switch (node->kind) {
    case JN_NUM:
    case JN_STRING:
    case JN_BIGINT:
    case JN_REGEX:
    case JN_FUNC:
    case JN_ARROW:
    case JN_HOLE:
    case JN_QUASI:
        return 1;
    case JN_IDENT:
        return node->sym >= 0 && reads_quietly(prog, node->sym);
    case JN_UNARY:
        if (node->op == JT_NOT || node->op == JT_IDENT) { /* !, typeof and void convert nothing */
            return inert_init(prog, node->a, height - 1);
        }
        return prog->nodes[node->a].kind == JN_NUM;
    case JN_BINARY:
        return prog->nodes[node->a].kind == JN_NUM && prog->nodes[node->b].kind == JN_NUM;
    case JN_ARRAY:
    case JN_TEMPLATE:
        for (int32_t item = node->a; item >= 0; item = prog->nodes[item].next) {
            if (prog->nodes[item].kind == JN_SPREAD ||
                (node->kind == JN_TEMPLATE && prog->nodes[item].kind != JN_QUASI) ||
                !inert_init(prog, item, height - 1)) {
                return 0;
            }
        }
        return 1;
    case JN_OBJECT:
        for (int32_t prop = node->a; prop >= 0; prop = prog->nodes[prop].next) {
            if (prog->nodes[prop].kind != JN_PROP || (prog->nodes[prop].flags & JN_F_COMPUTED) ||
                !inert_init(prog, prog->nodes[prop].b, height - 1)) {
                return 0;
            }
        }
        return 1;
    default:
        return 0;
    }
}

/* Whether nothing before the declarator declr runs code of the program, when its statement opens its function's
   body: only the initializers of the declarators left of it run, so no closure that reads it can be called first. */
static int opens_function(const jm_program *prog, int32_t sym, int32_t stmt, int32_t declr) {
    if (stmt != prog->scopes[prog->syms[sym].scope].first_stmt) {
        return 0;
    }
    for (int32_t before = prog->nodes[stmt].a; before != declr; before = prog->nodes[before].next) {
        if (!inert_init(prog, prog->nodes[before].b, 8)) {
            return 0;
        }
    }
    return 1;
}

/* Whether the binding's declaration dominates its one read with a value that survives the move:
   a value let/const must have no read before initialization. Cross-scope reads require the first
   initializer in the function, before a caller could run. A regex may ride only into the very next
   single-shot statement (a fresh object per evaluation, 13.2.7.3, so the read must run at most
   once per pass over the declaration and outside any nested function), and a value var must sit
   in its function body's first statement -- nothing executes before it except the initializers of
   the declarators left of its own, so a read there keeps the binding. */
static int read_sees_initialized(jm_program *prog, int32_t sym, int32_t stmt, int32_t declr, int32_t init) {
    TH_WORK_STEP();
    if (prog->syms[sym].decl >= 1 && is_value_literal(prog, init)) {
        return prog->syms[sym].min_ref >= declr &&
               (prog->syms[sym].ref_scope == prog->syms[sym].scope || opens_function(prog, sym, stmt, declr));
    }
    if (prog->nodes[init].kind == JN_REGEX && prog->syms[sym].ref_scope == prog->syms[sym].scope &&
        prog->nodes[stmt].next >= 0 && single_shot_stmt(prog, prog->nodes[stmt].next) &&
        prog->syms[sym].ref_stmt == prog->nodes[stmt].next) {
        return 1;
    }
    if (prog->syms[sym].decl != 0 || !is_value_literal(prog, init) ||
        stmt != prog->scopes[prog->syms[sym].scope].first_stmt ||
        (prog->syms[sym].ref_scope != prog->syms[sym].scope && !opens_function(prog, sym, stmt, declr))) {
        return 0;
    }
    return !prog->syms[sym].read_before_init;
}

/* Whether moving the function declaration of sym under its one read keeps the tree within prog->rewrite_limit, and
   if so, the bookkeeping for the move. The body lands as deep as the read sits plus its own height, so a chain of
   nested declarations, each read under a tall expression, would otherwise stack those expressions: the parser meters
   each on its own, the inlined tree is as tall as their sum, and every later walk recurses once per level. The
   depths come from this pass's analysis, so moves made earlier in the pass are added in: an enclosing function that
   was inlined took the read down with it (sunk), and one inlined inside the body made the body taller (grown).
   inline_single_use only passes a declaration whose one read sits in the declaration's own scope: the analysis
   walked the declaration, so its scope is recorded, and the read sits inside a statement of that list, below it. */
static int function_inline_fits(jm_program *prog, int32_t sym) {
    int32_t sink = prog->syms[sym].ref_depth - prog->syms[sym].fn_depth;
    int32_t own = prog->syms[sym].fn_scope;
    int32_t bottom = prog->syms[sym].ref_depth + prog->syms[sym].fn_height + prog->scopes[own].grown;
    for (int32_t scope = prog->syms[sym].scope; scope >= 0; scope = prog->scopes[scope].parent) {
        bottom += prog->scopes[scope].sunk;
    }
    if (bottom > prog->rewrite_limit) {
        return 0;
    }
    prog->scopes[own].sunk = sink;
    for (int32_t scope = prog->syms[sym].scope; scope >= 0; scope = prog->scopes[scope].parent) {
        prog->scopes[scope].grown += sink;
    }
    return 1;
}

/* Inline a single-declarator binding that is read exactly once into that one read and drop the
   declaration (ECMA-262 propagates the assigned value; the optimization just removes the name). The
   walk records exactly one read (`refs == 1`) and no writes (`writes == 0`), so the value the
   declaration assigns is the value that single read sees, and the read is a genuine read position --
   never an assignment or for-in target the inlined value would land on illegally. Two cases are sound:

     - a let/const initialized to a literal: textual order protects same-scope reads; cross-scope reads
       require initialization before the function can call a closure;

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
            prog->syms[sym].scope == global || prog->syms[sym].pinned) {
            continue; /* one read, never written: the read is where the declaration's value goes */
        }
        int32_t ref = prog->syms[sym].ref_node;
        if (prog->syms[sym].decl == 4) {
            /* a function used once becomes an expression at that use, dropping the declaration and the
               name (refs == 1 means no self-reference). Only into its own scope: a use in a loop body or
               a nested function runs repeatedly and would build a fresh closure each time (13.2.4). */
            if (prog->syms[sym].ref_scope != prog->syms[sym].scope || prog->nodes[ref].sym != sym) {
                continue; /* a read that moved this pass waits for the next pass to resolve its new node */
            }
            if (!function_inline_fits(prog, sym)) {
                continue; /* the declaration stays where the tree is still short enough to walk */
            }
            if (!expand_shorthand_ref(prog, sym)) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                continue;                           /* GCOVR_EXCL_LINE */
            }
            int32_t decl = prog->syms[sym].decl_node;
            jm_node_replace(prog, ref, decl);
            prog->nodes[ref].flags |= JN_F_EXPR;
            prog->nodes[ref].str = NULL;
            prog->nodes[ref].str_len = 0;
            jm_node_empty(prog, decl);
            prog->syms[sym].decl_node = -2;
            changed = 1;
            continue;
        }
        int32_t stmt = prog->syms[sym].decl_node;
        int32_t declr = prog->syms[sym].declr_node;
        int32_t init = prog->nodes[declr].b;
        if (init < 0) {
            continue; /* a bare `var x;` declares no value to propagate */
        }
        int lone = prog->nodes[stmt].a == declr && prog->nodes[declr].next < 0;
        if (!read_sees_initialized(prog, sym, stmt, declr, init)) {
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
        if (prog->nodes[ref].sym != sym) {
            continue; /* the read moved this pass; the next pass resolves its new node */
        }
        if (!expand_shorthand_ref(prog, sym)) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            continue;                           /* GCOVR_EXCL_LINE */
        }
        move_node(prog, ref, init);
        unlink_declarator(prog, sym);
        changed = 1;
    }
    return changed;
}

/* Replace every read of a qualifying symbol with a copy of its literal, counting the copies; a
   `{ x }` shorthand read first gains its explicit key. The declarator still stands while this
   runs, so if a key allocation fails mid-walk the untouched reads keep the binding -- the
   caller unlinks it only once every read was replaced. */
/* Indexed by symbol ID; target == -1 marks a binding without a propagation plan. */
typedef struct {
    int32_t init;
    int32_t target;
    int32_t replaced;
} jm_propagation;

/* Apply every planned propagation in one tree walk. A `{ x }` shorthand read first gains its
   explicit key; if that allocation fails mid-walk the declarators are all still linked, and a
   partially-rewritten binding stays consistent -- the remaining reads see the binding holding the
   same value the copies carry. */
static void replace_reads(jm_program *prog, int32_t idx, jm_propagation *plans) {
    for (; idx >= 0; idx = prog->nodes[idx].next) {
        if (prog->nodes[idx].kind == JN_PROP && (prog->nodes[idx].flags & JN_F_SHORTHAND)) {
            int32_t read = prog->nodes[idx].a;
            int32_t sym = prog->nodes[read].sym;
            if (sym >= 0 && plans[sym].target >= 0) {
                int32_t key = jm_node_new(prog, JN_IDENT);
                if (key < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the binding */
                    return;    /* GCOVR_EXCL_LINE */
                }
                prog->nodes[key].str = prog->syms[sym].name;
                prog->nodes[key].str_len = prog->syms[sym].name_len;
                prog->nodes[idx].a = key;
                prog->nodes[idx].b = read;
                prog->nodes[idx].flags &= ~(uint32_t)JN_F_SHORTHAND;
            }
        }
        if (prog->nodes[idx].kind == JN_IDENT && prog->nodes[idx].sym >= 0) {
            jm_propagation *plan = &plans[prog->nodes[idx].sym];
            if (plan->target >= 0 && idx != plan->target) {
                jm_node_replace(prog, idx, plan->init);
                plan->replaced++;
            }
            if (prog->nodes[idx].kind != JN_IDENT) {
                continue; /* just rewritten into the literal: nothing below to walk */
            }
        }
        replace_reads(prog, prog->nodes[idx].a, plans);
        replace_reads(prog, prog->nodes[idx].b, plans);
        replace_reads(prog, prog->nodes[idx].c, plans);
        replace_reads(prog, prog->nodes[idx].d, plans);
    }
}

/* Literal copies save bytes when N*(len-1) < 3+len: each removes a one-character name,
   while deleting the binding saves its name, equals sign, value and separator. */
static int propagate_value_literals(jm_program *prog, int32_t global) {
    jm_propagation *plans = NULL;
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        if (prog->syms[sym].refs < 2 || prog->syms[sym].writes != 0 || prog->syms[sym].decl_node < 0 ||
            prog->syms[sym].scope == global || prog->syms[sym].decl > 2 || prog->syms[sym].pinned) {
            continue; /* decl > 2 also skips functions; only var/let/const record a JN_VAR decl_node */
        }
        int32_t stmt = prog->syms[sym].decl_node;
        int32_t declr = prog->syms[sym].declr_node;
        int32_t init = prog->nodes[declr].b;
        if (init < 0) {
            continue;
        }
        if (!is_value_literal(prog, init)) {
            continue;
        }
        Py_ssize_t len = literal_print_len(prog, init);
        if (prog->syms[sym].refs * (len - 1) >= 3 + len) {
            continue; /* the copies would outweigh the binding */
        }
        if (prog->syms[sym].min_ref < declr ||
            (prog->syms[sym].decl == 0 && stmt != prog->scopes[prog->syms[sym].scope].first_stmt) ||
            (prog->syms[sym].ref_scope != prog->syms[sym].scope && !opens_function(prog, sym, stmt, declr))) {
            continue;
        }
        if (plans == NULL) {
            plans = jm_malloc((size_t)prog->sym_count * sizeof(jm_propagation));
            if (plans == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                prog->failed = 1; /* GCOVR_EXCL_LINE */
                return 0;         /* GCOVR_EXCL_LINE */
            }
            for (int32_t slot = 0; slot < prog->sym_count; slot++) {
                plans[slot].target = -1;
            }
        }
        plans[sym] = (jm_propagation){.init = init, .target = prog->nodes[declr].a, .replaced = 0};
    }
    if (plans == NULL) {
        return 0;
    }
    replace_reads(prog, prog->nodes[prog->root].a, plans);
    int changed = 0;
    for (int32_t sym = 0; sym < prog->sym_count; sym++) {
        if (plans[sym].target < 0 || prog->syms[sym].decl_node < 0) {
            continue;
        }
        if (plans[sym].replaced != prog->syms[sym].refs) { /* GCOVR_EXCL_BR_LINE: an allocation failed */
            continue;                                      /* GCOVR_EXCL_LINE */
        }
        int32_t stmt = prog->syms[sym].decl_node;
        int32_t declr_prev = -1;
        for (int32_t declr = prog->nodes[stmt].a; declr >= 0; declr = prog->nodes[declr].next) {
            int32_t target = prog->nodes[declr].a;
            if (prog->nodes[target].kind != JN_IDENT || plans[prog->nodes[target].sym].target != target) {
                declr_prev = declr;
                continue;
            }
            int32_t binding = prog->nodes[target].sym;
            if (plans[binding].replaced != prog->syms[binding].refs) { /* GCOVR_EXCL_BR_LINE: an allocation failed */
                declr_prev = declr;                                    /* GCOVR_EXCL_LINE */
                continue;                                              /* GCOVR_EXCL_LINE */
            }
            if (declr_prev < 0) {
                prog->nodes[stmt].a = prog->nodes[declr].next;
            } else {
                prog->nodes[declr_prev].next = prog->nodes[declr].next;
            }
            prog->syms[binding].decl_node = -2;
            changed = 1;
        }
        if (prog->nodes[stmt].a < 0) {
            prog->nodes[stmt].kind = JN_EMPTY;
        }
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
    mangler->fn_now = global;
    mangler->args_scope = -1;
    mangler->stmt = -1;
    mangler->track_stmt = prog->has_regex;
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
    jm_free(mangler->crossings);
}

struct jm_analysis {
    M mangler;
};

/* One compression pass over the whole program: resolve afresh, then run the tree-shrinking transforms.
   Returns 1 when a transform changed the tree (the driver runs another pass), 0 at a fixpoint, and -1
   when the analysis is unusable -- a with/eval poisoned name resolution, or an allocation failed -- so
   the driver stops. Names are never assigned here; that is the final jm_mangle pass. */
int jm_compress(jm_program *prog, jm_analysis **settled) {
    M mangler = {0};
    *settled = NULL;
    int32_t global = analyze(&mangler, prog);
    prog->resolved = 0;
    int result = -1;
    /* GCOVR_EXCL_BR_START: each operand is an allocation failure */
    prog->failed |= global < 0 || mangler.failed || mangler.visible.failed || mangler.frees.failed;
    /* GCOVR_EXCL_BR_STOP */
    /* top-level bindings are observable and never touched; a with/eval poisons the whole program */
    if (!mangler.poisoned) {
        if (!prog->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            prog->resolved = 1;
            int changed = 0;
            collapse_chain(prog, prog->nodes[prog->root].a, &changed);
            changed &= 1; /* alone, a hoist leaves nothing for another pass or fold to do */
            changed |= drop_unused(prog, global);
            changed |= inline_single_use(prog, global);
            changed |= propagate_value_literals(prog, global);
            int open = 0;
            if (settle_declarations(prog, &open) && !changed) {
                changed = open ? 1 : 2;
            }
            result = changed;
        }
    }
    if (result == 0 || result == 2) {
        *settled = jm_malloc(sizeof **settled);
        prog->failed |= *settled == NULL; /* GCOVR_EXCL_BR_LINE: allocation failure */
    }
    if (*settled != NULL) {
        (*settled)->mangler = mangler;
    } else {
        mangler_free(&mangler);
    }
    return result;
}

void jm_analysis_free(jm_analysis *analysis) {
    if (analysis != NULL) {
        mangler_free(&analysis->mangler);
        jm_free(analysis);
    }
}

/* Keep a binding's name out of the new names. */
static void reserve_name(M *mangler, const jm_sym *kept) {
    hslot *slot = htab_slot(&mangler->frees, kept->name, kept->name_len, 1);
    if (slot != NULL) { /* GCOVR_EXCL_BR_LINE: the false branch is an allocation failure */
        slot->val = 1;
    }
}

/* Assign the shortest legal names to the surviving local bindings. Run once after compression settles;
   it changes no tree shape, only names. Skipped entirely under with/eval, where resolution is unsafe. */
void jm_mangle(jm_program *prog, jm_analysis *settled) {
    M mangler = {0};
    int32_t global;
    if (settled != NULL) {
        mangler = settled->mangler;
        global = mangler.global;
        jm_free(settled);
    } else {
        global = analyze(&mangler, prog);
    }
    prog->resolved = 0;
    /* GCOVR_EXCL_BR_START: each operand is an allocation failure */
    prog->failed |= global < 0 || mangler.failed || mangler.visible.failed || mangler.frees.failed;
    /* GCOVR_EXCL_BR_STOP */
    if (!mangler.poisoned) {
        if (!prog->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            prog->resolved = 1;
            /* Reserve every kept (top-level or pinned) binding name. Slots keep only renamed bindings apart, so a
               new name equal to a kept one would capture its references inside the renamed scope, or be captured
               inside its scope. A top-level binding nothing reads or writes has no reference to capture, and every
               renamed binding sits inside it. Labels have their own namespace. */
            for (int32_t sym = 0; sym < prog->sym_count; sym++) {
                const jm_sym *kept = &prog->syms[sym];
                if (kept->decl != 8 &&
                    (kept->pinned || (kept->scope == global && (kept->refs != 0 || kept->writes != 0)))) {
                    reserve_name(&mangler, kept);
                }
            }
            /* An unread top-level var declared inside a block or catch body still hoists out through that scope, where
               a renamed let, const or class of its name would be a redeclaration (ECMA-262 14.2.1.1). Its declaration
               there is a crossing like any reference. */
            for (int32_t at = 0; at < mangler.crossing_count; at += 2) {
                if (prog->syms[mangler.crossings[at]].scope == global) {
                    reserve_name(&mangler, &prog->syms[mangler.crossings[at]]);
                }
            }
            assign_slots(&mangler, global);
            assign_names_by_frequency(&mangler);
            /* a kept name whose reservation failed could be handed out again, capturing its references */
            prog->failed |= mangler.failed || mangler.frees.failed; /* GCOVR_EXCL_BR_LINE: allocation failure */
        }
    }
    mangler_free(&mangler);
}
